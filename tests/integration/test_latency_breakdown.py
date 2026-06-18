"""OBS-02 integration — stage.timing events emit via StageTimer + StreamLoop.

Plan 01-14 Task 2 wires :class:`common.logging.StageTimer` around the
capture + encode stages in :class:`server.stream_loop.StreamLoop` and
hooks the state-disagreement ERROR log emit + keyframe counters. This
module verifies the observable end of that wiring:

1. StageTimer itself emits a ``stage.timing`` JSON event on exit carrying
   ``stage=<name>`` + ``latency_ms`` (pure unit proof — no server needed).
2. HealthStats JSON includes the OBS-02 / OBS-03 fields when serialized
   over the wire (a regression gate for the HealthPong broadcast path).
3. The ``fsm.state_disagreement`` event fires at ERROR level when
   :func:`server.health_loop.check_state_pair` sees a disagreeing pair.
4. StreamLoop.run_h264 emits both ``stage.timing stage=capture`` and
   ``stage.timing stage=encode`` when a capture + encode iteration runs.

These tests run without network or TLS — they exercise the wiring and
the log stream, which is what Phase 1 OBS-02 actually delivers. The
full loopback proof (server bootstrap + client connect + frame flow)
is covered by :mod:`tests.integration.test_server_bootstrap`.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import sys

import pytest
import structlog

from common.messages import HealthStats


@pytest.fixture
def _reset_logging_configured():
    """Reset structlog _configured flag so each test can reconfigure stderr capture."""
    import common.logging as ml
    prior = ml._configured
    ml._configured = False
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    yield
    ml._configured = prior
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def _capture_stage_events(block) -> list[dict]:
    """Run ``block()`` with stderr rerouted, return list of parsed JSON events."""
    from common.logging import configure
    import common.logging as ml
    ml._configured = False
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    buf = io.StringIO()
    saved = sys.stderr
    sys.stderr = buf
    try:
        configure(phase="server", verbose=True)
        block()
    finally:
        sys.stderr = saved
    events = []
    for line in buf.getvalue().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def test_stagetimer_emits_stage_timing_event(_reset_logging_configured):
    """StageTimer binds stage + emits stage.timing with latency_ms on exit."""
    from common.logging import StageTimer

    def run():
        with StageTimer("capture"):
            # Tiny deterministic work — just enough that latency_ms > 0.
            for _ in range(1000):
                pass

    events = _capture_stage_events(run)
    stage_events = [e for e in events if e.get("event") == "stage.timing"]
    assert any(e.get("stage") == "capture" for e in stage_events), (
        f"expected stage.timing stage=capture in {events!r}"
    )
    # latency_ms field is present and numeric
    capture = next(e for e in stage_events if e.get("stage") == "capture")
    assert "latency_ms" in capture
    assert isinstance(capture["latency_ms"], (int, float))
    assert capture["latency_ms"] >= 0.0


def test_stagetimer_emits_multiple_stages(_reset_logging_configured):
    """capture + encode stages both surface distinct stage.timing events."""
    from common.logging import StageTimer

    def run():
        with StageTimer("capture"):
            pass
        with StageTimer("encode"):
            pass

    events = _capture_stage_events(run)
    stages = {e["stage"] for e in events
              if e.get("event") == "stage.timing" and "stage" in e}
    assert "capture" in stages
    assert "encode" in stages


def test_healthstats_wire_includes_obs_fields():
    """HealthStats JSON round-trip still carries the OBS-02 + OBS-03 fields.

    Plan 01-14 Task 1 added the fields; this gate ensures the HealthPong
    broadcast path in server/health_loop.py can serialize them without
    TypeError when they're populated from HealthMonitor.get_stats().
    """
    hs = HealthStats(
        transmit_time_ms=1.0,
        decode_time_ms=2.0,
        display_time_ms=3.0,
        keyframe_requested=5,
        keyframe_emitted=4,
    )
    parsed = json.loads(hs.to_json())
    for f in ("transmit_time_ms", "decode_time_ms", "display_time_ms",
              "keyframe_requested", "keyframe_emitted"):
        assert f in parsed, f"HealthStats wire missing {f}"


def test_state_disagreement_emits_error_event(_reset_logging_configured):
    """Plan 01-14 upgrades fsm.state_disagreement to a structured ERROR event.

    Plan 01-08 landed the disagreement-detection data path as a stdlib
    warning log. Plan 01-14 promotes it to a structlog ERROR event with
    ``client_state`` + ``server_state`` + ``client_id`` fields so the
    overlay / dashboard can filter on it.
    """
    from server.health_loop import check_state_pair

    def run():
        # "streaming" + "bootstrapping" is a disagreeing pair per
        # common/session_fsm.py ALLOWED_PAIRS.
        check_state_pair("streaming", "bootstrapping", client_id="test-c1")

    events = _capture_stage_events(run)
    disagreements = [e for e in events
                     if e.get("event") == "fsm.state_disagreement"
                     or e.get("event") == "health.state_disagreement"]
    assert disagreements, (
        f"no fsm/health.state_disagreement event emitted; got {events!r}"
    )
    e = disagreements[0]
    assert e.get("level") == "error", f"expected level=error, got {e!r}"
    assert e.get("client_state") == "streaming"
    assert e.get("server_state") == "bootstrapping"
    assert e.get("client_id") == "test-c1"


def test_state_pair_no_disagreement_emits_nothing(_reset_logging_configured):
    """A valid pair must not emit fsm.state_disagreement."""
    from server.health_loop import check_state_pair

    def run():
        # "streaming" client + "streaming" server is a legal pair.
        check_state_pair("streaming", "streaming", client_id="test-c2")

    events = _capture_stage_events(run)
    disagreements = [e for e in events
                     if e.get("event") in (
                         "fsm.state_disagreement", "health.state_disagreement"
                     )]
    assert not disagreements, (
        f"valid pair unexpectedly emitted disagreement: {disagreements!r}"
    )


def test_state_pair_empty_state_skipped(_reset_logging_configured):
    """Empty client_state or server_state is the 'not reported' sentinel — skip."""
    from server.health_loop import check_state_pair

    def run():
        check_state_pair("", "streaming", client_id="test-c3")
        check_state_pair("streaming", "", client_id="test-c4")

    events = _capture_stage_events(run)
    disagreements = [e for e in events
                     if e.get("event") in (
                         "fsm.state_disagreement", "health.state_disagreement"
                     )]
    assert not disagreements


@pytest.mark.asyncio
async def test_streamloop_h264_emits_stage_timing_events(
    _reset_logging_configured, monkeypatch,
):
    """One iteration of StreamLoop.run_h264 surfaces capture + encode timing.

    Uses a tiny stub runtime so no real screen capture or encoder is
    needed. The point of the test is wiring: StageTimer fires in the
    production path.
    """
    from server.stream_loop import StreamLoop

    class _StubCapture:
        def capture_raw_bgra(self):
            return b""

    class _StubEncoder:
        def feed_frame(self, raw):
            return None

    class _StubHealth:
        def record_capture_time(self, ms): pass
        def record_frame_sent(self, n): pass
        def record_frame_dropped(self): pass

    class _StubRuntime:
        def __init__(self):
            self.capture = _StubCapture()
            self.encoder = _StubEncoder()
            self.health = _StubHealth()
            self.clients = {"stub": object()}
            self.username = "tester"

    runtime = _StubRuntime()
    loop = StreamLoop(runtime)

    # Prime structlog via the fixture-like capture wrapper used by the
    # other tests. We can't use _capture_stage_events directly (it's not
    # async) — reimplement the capture inline.
    from common.logging import configure
    import common.logging as ml
    ml._configured = False
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    buf = io.StringIO()
    saved = sys.stderr
    sys.stderr = buf
    try:
        configure(phase="server", verbose=True)
        # Run exactly one iteration of the h264 loop.
        loop._running = True

        async def _drive_one():
            # Kick the loop, then stop it after a tiny pause so the first
            # iteration has fired.
            task = asyncio.ensure_future(loop.run_h264(fps=120))
            await asyncio.sleep(0.05)
            loop._running = False
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()

        await _drive_one()
    finally:
        sys.stderr = saved

    events = []
    for line in buf.getvalue().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    stages = {e["stage"] for e in events
              if e.get("event") == "stage.timing" and "stage" in e}
    assert "capture" in stages, (
        f"no capture stage.timing event emitted; got stages={stages} events={events!r}"
    )
    assert "encode" in stages, (
        f"no encode stage.timing event emitted; got stages={stages} events={events!r}"
    )
