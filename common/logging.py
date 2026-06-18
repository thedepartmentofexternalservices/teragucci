"""Shared structlog configuration (OBS-01).

Imported by all three entry points (server, client, broker) and by the
smoke harness. Idempotent — safe to call :func:`configure` multiple times.

Canonical schema per CONTEXT.md §"Claude's Discretion" line 83
----------------------------------------------------------------

Required on every event (bound by the processor chain on every emit):

  * ``event``   dotted short name, first positional arg to ``log.info(...)``
  * ``phase``   ``"server" | "client" | "broker"`` — deployment tier bound
                via ``structlog.contextvars.bind_contextvars`` inside
                :func:`configure`. NOT the GSD workflow phase; if a future
                need arises a separate ``gsd_phase`` field will be used.
  * ``level``   ``debug/info/warning/...`` — added by
                :func:`structlog.processors.add_log_level`
  * ``ts``      ISO 8601 UTC — added by
                :class:`structlog.processors.TimeStamper` (key=``ts``)
  * ``logger``  dotted logger name — added by
                :func:`structlog.stdlib.add_logger_name`

Required on session-scoped events (caller sets ``session_scope=True``):

  * ``session_id``  16-hex, bound per SessionRuntime on the server,
                    per-tab on the client
  * ``client_id``   bound per ClientSession handler at handshake
  * ``stage``       ``capture | encode | transport | decode | paint`` —
                    bound by :class:`StageTimer` context manager

Optional (bind per-context): ``username, state, prev_state, transport,
latency_ms, frame_size, keyframe, err_class, err_code``.

Session-scope enforcement
-------------------------

When a caller tags an event with ``session_scope=True``, a missing
``session_id``/``client_id``/``stage`` triggers a warn-level log
(``log.session_scope_missing_fields``) on the same emit. The original
event is still delivered (never dropped); the warning is a diagnostic
nudge for incomplete wiring. See
:func:`_warn_session_scope_missing` below.

Redaction (T-1-04)
------------------

Any event-dict key whose lowered name contains one of
:data:`REDACT_KEYS` (substring match) has its VALUE replaced with the
literal string ``"[REDACTED]"`` BEFORE :class:`structlog.processors.JSONRenderer`.
Redaction walks nested dicts recursively. Regression tests:
``test_redaction_strips_password`` + ``test_redaction_strips_case_insensitively``
+ ``test_redaction_does_not_touch_innocent_fields``.

Processor chain (order is load-bearing — Pitfall 2 in RESEARCH)
----------------------------------------------------------------

    merge_contextvars
        ↓
    add_logger_name
        ↓
    TimeStamper(fmt="iso", utc=True, key="ts")
        ↓
    add_log_level
        ↓
    StackInfoRenderer / format_exc_info / UnicodeDecoder
        ↓
    _warn_session_scope_missing   (diagnostic — must run AFTER merge_contextvars)
        ↓
    _redact_secrets               (T-1-04 — must run BEFORE renderer)
        ↓
    JSONRenderer(sort_keys=True)

``merge_contextvars`` MUST come before ``JSONRenderer`` or contextvars
never surface in the emitted JSON. ``_redact_secrets`` MUST come before
``JSONRenderer`` or redaction is a no-op on the rendered string.
"""
from __future__ import annotations

import contextvars
import logging
import sys
import time
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger


# ---------------------------------------------------------------------------
# Contextvar storage
# ---------------------------------------------------------------------------

# session_id contextvar (bound per-SessionRuntime on server; per-tab on client).
# Exposed here for modules that want to read the current session_id directly
# without going through structlog.contextvars.get_contextvars().
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_id", default="")


# Case-insensitive substring match — catches "password", "api_key", "TOKEN",
# "user_credentials", "broker_secret", "private_key", "pin_code", etc.
REDACT_KEYS: tuple[str, ...] = (
    "password", "credential", "token", "secret", "key", "pin",
)

# Fields required on every event tagged session_scope=True. Missing any of
# these triggers the diagnostic warn in _warn_session_scope_missing.
SESSION_SCOPE_REQUIRED: tuple[str, ...] = ("session_id", "client_id", "stage")


# ---------------------------------------------------------------------------
# Custom processors
# ---------------------------------------------------------------------------


def _redact_value(value: Any) -> Any:
    """Walk nested dicts/lists, redacting any key whose lowered name matches REDACT_KEYS.

    Returns a new structure — never mutates the input.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            lowered = str(k).lower()
            if any(needle in lowered for needle in REDACT_KEYS):
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact_value(v)
        return out
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _redact_secrets(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Processor — strips any key containing a REDACT_KEYS substring.

    Runs BEFORE JSONRenderer per RESEARCH Pitfall 2. Replaces values with
    the literal string ``[REDACTED]`` (never drops the key — visibility that
    redaction happened is itself useful signal).

    Walks nested dicts/lists recursively so a payload like
    ``{"cfg": {"broker_secret": "abc"}}`` is also redacted.
    """
    out: dict[str, Any] = {}
    for k, v in event_dict.items():
        lowered = str(k).lower()
        if any(needle in lowered for needle in REDACT_KEYS):
            out[k] = "[REDACTED]"
        else:
            out[k] = _redact_value(v)
    return out


def _add_logger_name(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Processor — add the bound logger's name to the event dict as ``logger``.

    This is the canonical-schema ``logger`` field. We roll our own rather than
    using :func:`structlog.stdlib.add_logger_name` because that processor
    requires a stdlib :class:`logging.Logger` wrapped logger (reads ``.name``),
    and our factory is :class:`structlog.PrintLoggerFactory` for direct-to-
    stderr JSON output.

    :func:`get_logger` binds ``logger_name`` into the bound context at creation
    time; this processor moves it from ``logger_name`` → ``logger`` to match
    the canonical CONTEXT.md schema field name.
    """
    name = event_dict.pop("logger_name", None)
    if name is None:
        # Fallback for callers that bypassed get_logger(): read .name if the
        # underlying wrapped logger exposes one (stdlib.Logger does).
        name = getattr(logger, "name", None) or "teraguchi"
    event_dict["logger"] = name
    return event_dict


def _warn_session_scope_missing(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """If session_scope=True is set, warn about any missing required fields.

    Does NOT drop the original event; emits a separate warn log with the
    list of missing fields via the stdlib logger ``teraguchi.logging``. This
    avoids recursing into structlog's own processor chain. Keeps wiring
    honest without breaking runtime.
    """
    if event_dict.get("session_scope") is True:
        # merge_contextvars has already run upstream, so contextvars are in
        # event_dict. Check for each required field.
        missing = [
            field for field in SESSION_SCOPE_REQUIRED
            if not event_dict.get(field)
        ]
        if missing:
            try:
                # Side-channel WARN — no structlog recursion.
                logging.getLogger("teraguchi.logging").warning(
                    "log.session_scope_missing_fields missing=%s event=%s",
                    ",".join(missing),
                    event_dict.get("event", "<unknown>"),
                )
            except Exception:
                # Never let a diagnostic emit break the real log path.
                pass
    return event_dict


# ---------------------------------------------------------------------------
# configure / get_logger
# ---------------------------------------------------------------------------


# Idempotent gate. Tests may flip this to False to force a reconfigure after
# swapping sys.stderr (see tests/common/test_logging.py::_capture_one_event).
_configured = False


def configure(
    phase: str,
    *,
    verbose: bool = False,
) -> None:
    """Configure structlog once with the canonical CONTEXT.md schema.

    Args:
        phase: ``'server'``, ``'client'``, or ``'broker'``. Bound into
            contextvars so every event carries it. Entry points call this
            in ``main()`` AFTER argparse but BEFORE any lifecycle imports
            that log. Name ``phase`` is per CONTEXT.md §"Claude's Discretion"
            line 83 (the canonical user-locked field name — this is the
            deployment tier, NOT the GSD workflow phase). RESEARCH drafted
            the same concept as ``tier``; CONTEXT.md supersedes.
        verbose: DEBUG level if True, else INFO. Threaded through from the
            ``--verbose`` CLI flag on server/client/broker.

    Idempotent: a second call is a no-op unless a caller (typically a test)
    explicitly flips the module-level ``_configured`` flag back to False.
    """
    global _configured
    if _configured:
        return
    _configured = True

    level = logging.DEBUG if verbose else logging.INFO
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,        # MUST be before JSONRenderer (Pitfall 2)
        _add_logger_name,                               # canonical-schema `logger` field
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        _warn_session_scope_missing,                    # diagnostic — must run after merge_contextvars
        _redact_secrets,                                # T-1-04 — before renderer
        structlog.processors.JSONRenderer(sort_keys=True),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    # Bridge stdlib logging.getLogger(...) → stderr so existing logger.info()
    # call sites (documented in CONVENTIONS.md §"Logging") still emit
    # alongside structlog JSON output during progressive migration.
    #
    # NOTE: we DO NOT pass force=True. Under pytest, caplog installs its own
    # handlers on the root logger; force=True would wipe them and break
    # ``caplog.records``-based assertions. basicConfig is a no-op when handlers
    # already exist, which is the behaviour we want — existing handlers (pytest,
    # PySide6) keep working, and if none exist (production main.py entry point)
    # we install one pointing at stderr.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
    )
    # Ensure the level is set even when basicConfig was a no-op (handlers
    # already existed). Root-logger level drives propagation to the
    # `teraguchi.logging` diagnostic channel used by _warn_session_scope_missing.
    logging.getLogger().setLevel(level)
    # Tag every emit with the deployment phase (server/client/broker).
    # Keep existing bindings intact — clear_contextvars is the caller's job.
    structlog.contextvars.bind_contextvars(phase=phase)


def get_logger(name: str = "") -> Any:
    """Return a structlog BoundLogger tagged with a canonical ``logger`` name.

    Use ``name="server.broadcaster"`` so logs get
    ``logger=teraguchi.server.broadcaster``. Call sites that still use stdlib
    logging get the same output via ``basicConfig`` bridge.

    Binds ``logger_name`` into the bound context; the :func:`_add_logger_name`
    processor rewrites it to the canonical ``logger`` field on emit.
    """
    logger_name = f"teraguchi.{name}" if name else "teraguchi"
    return structlog.get_logger(logger_name).bind(logger_name=logger_name)


# ---------------------------------------------------------------------------
# StageTimer helper
# ---------------------------------------------------------------------------


class StageTimer:
    """Context manager that binds ``stage`` + emits ``latency_ms`` for the block.

    Usage::

        with StageTimer("capture"):
            raw = capture.capture_raw_bgra()

    On exit, logs a ``stage.timing`` event with the elapsed ms (rounded to
    3 decimals). Binds ``stage`` as a contextvar for the duration of the
    block so all nested log events inherit it; unbinds on exit.

    Plan 17 (OBS-05) wires this around capture / encode / transport /
    decode / paint to populate the pipeline-latency histogram.
    """

    def __init__(self, stage: str):
        self.stage = stage
        self._log = get_logger("stage")
        self._t0: float = 0.0

    def __enter__(self) -> "StageTimer":
        self._t0 = time.perf_counter()
        structlog.contextvars.bind_contextvars(stage=self.stage)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        elapsed_ms = (time.perf_counter() - self._t0) * 1000.0
        self._log.info(
            "stage.timing",
            stage=self.stage,
            latency_ms=round(elapsed_ms, 3),
        )
        structlog.contextvars.unbind_contextvars("stage")
        return False   # don't suppress exceptions
