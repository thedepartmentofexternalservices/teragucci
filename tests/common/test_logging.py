"""OBS-01 + T-1-04 — structlog canonical schema, ordering, redaction, session-scope.

Tests the contract that every Phase 2+ module binds against:

* Canonical schema per CONTEXT.md §"Claude's Discretion" line 83 =
  {event, phase, session_id, client_id, stage, level, ts, logger}
* Required on every event: event, phase, level, ts, logger.
* Required on session-scoped events (session_scope=True): session_id, client_id, stage —
  missing fields emit a `log.session_scope_missing_fields` warn (not drop).
* Processor chain order: merge_contextvars → add_logger_name → TimeStamper → add_log_level
  → StackInfo/format_exc_info/UnicodeDecoder → _warn_session_scope_missing → _redact_secrets
  → JSONRenderer. Contextvars appearing in the final JSON proves merge_contextvars ran
  before JSONRenderer (Pitfall 2 regression).
* Redaction (T-1-04): any case-insensitive substring match on REDACT_KEYS has its VALUE
  replaced with "[REDACTED]" BEFORE JSONRenderer.
* Field name `phase` (not `tier`) per CONTEXT.md line 83 — explicit regression test.
"""
import io
import json
import logging as _stdlib_logging

import pytest
import structlog


@pytest.fixture(autouse=True)
def _reset_configure(monkeypatch):
    """Each test gets a fresh configure() — tests assert schema output."""
    import common.logging as ml
    monkeypatch.setattr(ml, "_configured", False)
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    yield
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def _capture_one_event(log_call, phase="client"):
    """Run log_call() and return the parsed JSON of the emitted line.

    Replaces stderr while the structlog emit fires; asserts at least one
    JSON line was produced and returns the parsed dict of the LAST line.
    """
    from common.logging import configure, get_logger
    buf = io.StringIO()
    import sys
    configure(phase=phase, verbose=True)
    # Replace stderr for capture. structlog PrintLoggerFactory was bound to the
    # real sys.stderr during configure(); tell it about the new one by re-binding
    # via structlog's bind — but simpler: the PrintLogger uses the stream passed
    # at factory time. So we must route through the factory again by reconfiguring
    # after the stream swap (configure is idempotent per the _configured gate, so
    # flip the gate and re-configure).
    import common.logging as ml
    ml._configured = False
    saved = sys.stderr
    sys.stderr = buf
    try:
        configure(phase=phase, verbose=True)
        log = get_logger("test")
        log_call(log)
    finally:
        sys.stderr = saved
    lines = [l for l in buf.getvalue().splitlines() if l.strip().startswith("{")]
    assert lines, f"no JSON line captured; got: {buf.getvalue()!r}"
    return json.loads(lines[-1])


def test_required_fields_present():
    """Canonical required fields (CONTEXT.md line 83): event, phase, level, ts, logger."""
    evt = _capture_one_event(lambda log: log.info("fsm.transition", state="streaming"))
    assert evt["event"] == "fsm.transition"
    assert "ts" in evt
    assert "level" in evt
    assert evt["level"] in ("info", "warning", "error", "debug", "critical")
    assert evt["phase"] == "client"
    assert "logger" in evt  # structlog.stdlib.add_logger_name
    assert evt["logger"].startswith("teraguchi.")
    assert evt["state"] == "streaming"


def test_logger_name_is_stamped():
    """`logger` field comes from structlog.stdlib.add_logger_name (canonical schema)."""
    evt = _capture_one_event(
        lambda log: log.info("x"),
        phase="server",
    )
    assert evt["logger"] == "teraguchi.test"


def test_redaction_strips_password():
    evt = _capture_one_event(lambda log: log.info("auth.request",
                                                   username="alice",
                                                   password="hunter2"))
    assert evt["username"] == "alice"
    assert evt["password"] == "[REDACTED]"


def test_redaction_strips_case_insensitively():
    evt = _capture_one_event(lambda log: log.info("x",
                                                   API_KEY="abc",
                                                   Secret_VALUE="xyz",
                                                   broker_token="ztoken"))
    assert evt["API_KEY"] == "[REDACTED]"
    assert evt["Secret_VALUE"] == "[REDACTED]"
    assert evt["broker_token"] == "[REDACTED]"


def test_redaction_does_not_touch_innocent_fields():
    evt = _capture_one_event(lambda log: log.info("x",
                                                   username="alice",
                                                   session_id="s1",
                                                   stage="encode"))
    assert evt["username"] == "alice"
    assert evt["session_id"] == "s1"
    assert evt["stage"] == "encode"


def test_contextvars_bind_and_merge():
    """merge_contextvars must run BEFORE JSONRenderer (Pitfall 2)."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(session_id="sess-42")
    evt = _capture_one_event(lambda log: log.info("streaming.frame"))
    assert evt["session_id"] == "sess-42"


def test_session_scope_fields_present_in_session_events():
    """Harness binds session_id/client_id/stage at session start; subsequent
    events inside the session carry all three."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        session_id="sess-1", client_id="c1", stage="handshake",
    )
    evt = _capture_one_event(lambda log: log.info("session.started",
                                                   session_scope=True))
    # All three required fields present in the event
    assert evt["session_id"] == "sess-1"
    assert evt["client_id"] == "c1"
    assert evt["stage"] == "handshake"

    # Subsequent event inside the same session scope inherits all three
    evt2 = _capture_one_event(lambda log: log.info("fsm.transition",
                                                    session_scope=True))
    assert evt2["session_id"] == "sess-1"
    assert evt2["client_id"] == "c1"
    assert evt2["stage"] == "handshake"


def test_session_scope_missing_emits_warning(caplog):
    """Missing session_id/client_id/stage on a session-scoped event → warn."""
    structlog.contextvars.clear_contextvars()
    # Bind only session_id — client_id and stage are missing
    structlog.contextvars.bind_contextvars(session_id="sess-partial")
    with caplog.at_level(_stdlib_logging.WARNING, logger="teraguchi.logging"):
        _ = _capture_one_event(lambda log: log.info("bad.event",
                                                     session_scope=True))
    # The diagnostic warn must fire with a list of the missing fields
    matching = [r for r in caplog.records
                if "log.session_scope_missing_fields" in r.getMessage()]
    assert matching, "Expected a session_scope_missing_fields warn to fire"
    msg = matching[0].getMessage()
    assert "client_id" in msg
    assert "stage" in msg


def test_merge_contextvars_is_before_json_renderer():
    """Structural assertion: contextvars appear in emitted JSON
    (impossible unless merge_contextvars runs before JSONRenderer)."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(session_id="introspect-ok")
    evt = _capture_one_event(lambda log: log.info("ordering.smoke"))
    assert evt["session_id"] == "introspect-ok"


def test_configure_is_idempotent():
    from common.logging import configure
    configure(phase="server")
    configure(phase="server")


def test_phase_field_name_matches_context_md():
    """CONTEXT.md line 83 locks the deployment-tier field name as `phase`
    (not `tier`). Structural regression test."""
    evt = _capture_one_event(lambda log: log.info("x"), phase="broker")
    assert "phase" in evt
    assert evt["phase"] == "broker"
    # `tier` was the RESEARCH draft name, not the canonical one
    assert "tier" not in evt
