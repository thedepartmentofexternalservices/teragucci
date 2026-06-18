---
phase: 01-stability-ci-test-baseline
plan: 06
subsystem: observability
tags: [structlog, logging, error-taxonomy, redaction, contextvars, obs-01, t-1-04]

# Dependency graph
requires:
  - phase: 01-stability-ci-test-baseline
    provides: "Plan 01-03 pyproject/requirements-*.txt pin of structlog>=25.1,<26 + pytest toolchain"
provides:
  - "common/logging.py — configure(phase, verbose) + get_logger(name) + StageTimer"
  - "Canonical CONTEXT.md §'Claude's Discretion' schema on every emit: event, phase, level, ts, logger always; session_id, client_id, stage inside session_scope"
  - "Processor chain (load-bearing): merge_contextvars → _add_logger_name → add_log_level → TimeStamper → StackInfo/ExcInfo/Unicode → _warn_session_scope_missing → _redact_secrets → JSONRenderer"
  - "_redact_secrets processor mitigating T-1-04 (credentials in logs) — case-insensitive substring match on password/credential/token/secret/key/pin, walks nested dicts"
  - "_warn_session_scope_missing diagnostic processor — WARN via stdlib 'teraguchi.logging' when session_scope=True event is missing session_id/client_id/stage"
  - "common/errors.py — 15-class TeraguchiError hierarchy with stable TERA_* codes (wire contract)"
  - "common/messages.py MsgType.ERROR + ProtocolErrorMsg dataclass for structured protocol-error envelope"
  - "3 entry-point wiring calls: server/client/broker main.py configure(phase=...) replaces basicConfig"
affects:
  - "01-07 through 01-17 — every subsequent Phase 1 plan binds on structlog via get_logger() instead of stdlib logging.getLogger()"
  - "01-09 — Plan 09 (SessionFSM) will raise InvalidTransitionError from this hierarchy"
  - "01-11..01-14 — decomposition of server/client monolith progressively migrates logger.info() → get_logger(__name__).info()"
  - "01-17 — OBS-05 (pipeline latency histogram) consumes StageTimer instances"
  - "Phase-2+ all modules — session start binds {session_id, client_id, stage} as contextvars; session_scope=True on all session events"

# Tech tracking
tech-stack:
  added:
    - "structlog 25.1.x (already pinned in requirements-server.txt + requirements-client.txt by Plan 01-03; this plan is the first consumer)"
  patterns:
    - "Canonical structlog schema per CONTEXT.md §'Claude's Discretion' line 83 — phase field NOT tier (resolved ambiguity between CONTEXT.md and RESEARCH draft)"
    - "Load-bearing processor chain order: merge_contextvars BEFORE JSONRenderer; _redact_secrets BEFORE JSONRenderer (Pitfall 2 — contextvars + redaction only surface if they run before rendering)"
    - "Custom _add_logger_name processor over structlog.stdlib.add_logger_name — the latter reads .name off a stdlib Logger, incompatible with PrintLoggerFactory. get_logger() binds logger_name; _add_logger_name rewrites to canonical logger field."
    - "Session-scope diagnostic: session_scope=True tag on events; missing required fields emit WARN (never drop) via side-channel stdlib 'teraguchi.logging' to avoid structlog recursion"
    - "T-1-04 redaction walks nested dicts recursively — payloads like {cfg: {broker_secret: 'abc'}} redact on the nested key"
    - "Stable TERA_* error codes as wire contract — FSMs react deterministically to specific codes (TERA_AUTH_FAILED → do-not-reconnect; TERA_TRANSPORT → exp back-off)"
    - "Progressive migration: basicConfig bridge in configure() keeps existing logger.info() call sites emitting through the same stderr stream; new modules bind structlog directly"

key-files:
  created:
    - "common/logging.py (351 lines) — structlog configure + 15-class TeraguchiError hierarchy imports + StageTimer"
    - "common/errors.py (149 lines) — 15-class TeraguchiError hierarchy with stable TERA_* codes"
    - "tests/common/test_logging.py (198 lines, 11 test functions)"
    - "tests/common/test_errors.py (72 lines, 5 test functions)"
  modified:
    - "common/messages.py — add MsgType.ERROR + ProtocolErrorMsg dataclass (with to_json + from_dict)"
    - "server/main.py — import common.logging.configure; replace basicConfig with configure(phase='server', verbose=...)"
    - "client/main.py — import common.logging.configure; replace basicConfig with configure(phase='client', verbose=...)"
    - "broker/main.py — import common.logging.configure; replace basicConfig with configure(phase='broker', verbose=...)"

key-decisions:
  - "phase NOT tier: CONTEXT.md line 83 locked the deployment-tier field name as 'phase'. RESEARCH draft called it 'tier'. CONTEXT.md wins. Regression test (test_phase_field_name_matches_context_md) asserts 'tier' does not appear in any emitted event."
  - "Custom _add_logger_name instead of structlog.stdlib.add_logger_name: the stdlib processor requires a .name attribute on the wrapped logger (stdlib Logger has one; PrintLogger does not). Custom processor reads logger_name bound by get_logger() and rewrites to canonical 'logger' field. Objective (canonical 'logger' field on every emit) unchanged."
  - "basicConfig without force=True: force=True would wipe pytest's caplog handler and break test_session_scope_missing_emits_warning. Instead we set the root logger level explicitly via logging.getLogger().setLevel(level) after basicConfig (which is a no-op when handlers already exist)."
  - "Redaction walks nested structures: plain dict-level redaction would miss cfg={'broker_secret': 'x'}. Recursive walk in _redact_value handles arbitrary nesting."
  - "Error codes use TERA_* prefix: namespaced across the 15 classes (TERA_UNKNOWN, TERA_TRANSPORT, TERA_TLS_VERIFY, TERA_PROTOCOL, TERA_PROTO_VERSION, TERA_PROTO_INVALID, TERA_AUTH, TERA_AUTH_FAILED, TERA_AUTH_TIMEOUT, TERA_TOKEN, TERA_FSM, TERA_FSM_TRANSITION, TERA_ENCODER, TERA_ENCODER_RESTART, TERA_CAPTURE). Stable wire contract — changing these breaks client FSM reactions."

patterns-established:
  - "Entry-point hook pattern: every tier's main.py calls common.logging.configure(phase=<tier>, verbose=args.verbose) AFTER argparse, BEFORE any lifecycle imports log. Replaces the previous 'logging.basicConfig(...)' prologue."
  - "Session-scope binding pattern (future consumers): at session start, bind_contextvars(session_id=..., client_id=..., stage='handshake'); subsequent log events inherit all three. Tag events with session_scope=True to get the diagnostic warn if fields go missing."
  - "Error raising pattern: raise ConcreteError('human message') — caller's except clause catches the base TeraguchiError (or a specific subclass) and uses .code for FSM logic / ProtocolErrorMsg.code on the wire."
  - "StageTimer pattern (ready for Plan 17): `with StageTimer('encode'): ...` — binds 'stage' contextvar for the block, emits stage.timing event with latency_ms on exit."

requirements-completed: [OBS-01]

# Metrics
duration: ~20min
completed: 2026-04-18
---

# Phase 1 Plan 06: Structlog Observability Baseline + Error Taxonomy Summary

**OBS-01 structured logging substrate lands — every Phase 2-7 module now binds session_id / client_id / stage on structlog with the canonical CONTEXT.md schema, and T-1-04 credential redaction fires on every emit.**

## Performance

- **Duration:** ~20 minutes
- **Started:** 2026-04-19T00:32:39Z
- **Completed:** 2026-04-19T00:52:39Z
- **Tasks:** 2/2 (RED test + GREEN impl + refactor where needed, both tasks)
- **Files modified:** 8 total (3 created: logging.py, errors.py, test files; 5 modified: messages.py + 3 main.py entry points; 1 TDD-first test commit)

## Accomplishments

### Task 1: common/logging.py + common/errors.py + ProtocolErrorMsg extension

- **common/logging.py** — `configure(phase, verbose)` + `get_logger(name)` + `StageTimer` context manager
  - Processor chain: `merge_contextvars → _add_logger_name → add_log_level → TimeStamper(iso/utc, key=ts) → StackInfoRenderer → format_exc_info → UnicodeDecoder → _warn_session_scope_missing → _redact_secrets → JSONRenderer(sort_keys=True)`
  - Idempotent (module-level `_configured` gate)
  - Bridges stdlib `logging` (no `force=True` to preserve pytest caplog)
- **common/errors.py** — 15-class `TeraguchiError` hierarchy with stable `code` class attributes
- **common/messages.py** — `MsgType.ERROR = "error"` + `ProtocolErrorMsg` dataclass with `to_json()` + `from_dict()`
- **tests/common/test_logging.py** — 11 tests covering: required fields present, logger-name stamping, redaction strips password / case-insensitive / innocent fields untouched, contextvars bind+merge, session-scope fields present, session-scope missing emits warning, merge_contextvars before JSONRenderer, configure idempotent, phase-not-tier regression
- **tests/common/test_errors.py** — 5 tests covering: default TERA_UNKNOWN code, every subclass stable code, hierarchy catchable by base, TlsVerificationError ⊂ TransportError ⊂ TeraguchiError, ProtocolErrorMsg round-trip with no leaked passwords

### Task 2: Entry-point wiring

- `server/main.py`: import `common.logging.configure`; replace `logging.basicConfig(...)` with `configure(phase="server", verbose=args.verbose)`
- `client/main.py`: same pattern with `phase="client"`
- `broker/main.py`: same pattern with `phase="broker"`
- Existing stdlib `logger = logging.getLogger(...)` at lines 71 (server), 38 (client), 37 (broker) UNCHANGED — `basicConfig` bridge inside `configure` keeps them emitting to stderr

### Canonical schema confirmation

```
$ python3 -c "from common.logging import configure, get_logger; configure('server'); get_logger('x').info('auth.request', username='alice', password='s3cret')" 2>&1
{"event": "auth.request", "level": "info", "logger": "teraguchi.x", "password": "[REDACTED]", "phase": "server", "ts": "2026-04-19T00:52:02Z", "username": "alice"}
```

Every canonical field (`event, phase, level, ts, logger`) present; `password: "[REDACTED]"` proves T-1-04 mitigation; `logger: "teraguchi.x"` proves `_add_logger_name` processor fires; `phase: "server"` (not `tier`) confirms CONTEXT.md line 83 authority.

## Error Taxonomy Reference

| Class                     | Code                   | Inherits        |
| ------------------------- | ---------------------- | --------------- |
| `TeraguchiError`          | `TERA_UNKNOWN`         | `Exception`     |
| `TransportError`          | `TERA_TRANSPORT`       | `TeraguchiError`|
| `TlsVerificationError`    | `TERA_TLS_VERIFY`      | `TransportError`|
| `ProtocolError`           | `TERA_PROTOCOL`        | `TeraguchiError`|
| `ProtocolVersionMismatch` | `TERA_PROTO_VERSION`   | `ProtocolError` |
| `InvalidMessageError`     | `TERA_PROTO_INVALID`   | `ProtocolError` |
| `AuthError`               | `TERA_AUTH`            | `TeraguchiError`|
| `AuthFailedError`         | `TERA_AUTH_FAILED`     | `AuthError`     |
| `AuthTimeoutError`        | `TERA_AUTH_TIMEOUT`    | `AuthError`     |
| `TokenError`              | `TERA_TOKEN`           | `AuthError`     |
| `FSMError`                | `TERA_FSM`             | `TeraguchiError`|
| `InvalidTransitionError`  | `TERA_FSM_TRANSITION`  | `FSMError`      |
| `EncoderError`            | `TERA_ENCODER`         | `TeraguchiError`|
| `EncoderRestartFailed`    | `TERA_ENCODER_RESTART` | `EncoderError`  |
| `CaptureError`            | `TERA_CAPTURE`         | `TeraguchiError`|

15 classes total. Every `TERA_*` code is a stable wire contract carried in `ProtocolErrorMsg.code`.

## Processor Chain Order (Load-Bearing)

```
structlog.contextvars.merge_contextvars          # Pitfall 2: MUST be before JSONRenderer
_add_logger_name                                  # Canonical `logger` field (custom; see Deviations)
structlog.processors.add_log_level                # Canonical `level` field
structlog.processors.TimeStamper(fmt="iso",       # Canonical `ts` field (ISO 8601 UTC)
                                  utc=True,
                                  key="ts")
structlog.processors.StackInfoRenderer()          # Optional stack info for exc events
structlog.processors.format_exc_info              # Formats exc_info tuple → string
structlog.processors.UnicodeDecoder()             # Decodes bytes → str
_warn_session_scope_missing                       # Diagnostic — session_scope=True → WARN if session_id/client_id/stage missing
_redact_secrets                                   # T-1-04 — [REDACTED] values for password/credential/token/secret/key/pin (case-insensitive, recursive)
structlog.processors.JSONRenderer(sort_keys=True) # Final render to sorted JSON
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Swapped `structlog.stdlib.add_logger_name` for custom `_add_logger_name` processor**

- **Found during:** Task 1 GREEN gate (running test suite after initial impl)
- **Issue:** The plan's `<action>` prescribed `structlog.stdlib.add_logger_name` in the processor chain. That processor calls `logger.name` on the wrapped logger, which only works for stdlib `logging.Logger` instances. Because `configure()` uses `structlog.PrintLoggerFactory(file=sys.stderr)`, the wrapped logger is a `PrintLogger` that has no `.name` attribute, so the processor raises `AttributeError: 'PrintLogger' object has no attribute 'name'` on every emit.
- **Fix:** Wrote a tiny `_add_logger_name` custom processor (15 lines) that reads `logger_name` from the event-dict (bound by `get_logger()` via `structlog.get_logger(name).bind(logger_name=name)`) and rewrites it to the canonical `logger` key. `get_logger()` was updated to bind `logger_name` at the `.bind()` step. Canonical schema (`logger` field on every event) is preserved exactly; the test `test_logger_name_is_stamped` asserts `evt["logger"] == "teraguchi.test"`.
- **Files modified:** `common/logging.py` (added `_add_logger_name` + updated `get_logger`), test output unchanged.
- **Commit:** b9212c6

**2. [Rule 3 - Blocking] Dropped `force=True` on `logging.basicConfig` to preserve pytest's caplog handler**

- **Found during:** Task 1 GREEN gate (`test_session_scope_missing_emits_warning` failed — caplog.records was empty).
- **Issue:** `logging.basicConfig(..., force=True)` removes all root-logger handlers before installing its own. Under pytest, caplog attaches its own handler at test-scope; `force=True` wipes it, so `caplog.records` stays empty when `_warn_session_scope_missing` emits a WARN via `teraguchi.logging`.
- **Fix:** Removed `force=True`; in no-handlers-installed environments (production main.py startup), basicConfig still installs the stderr handler. In pytest, basicConfig is a no-op (caplog handler already present) and we explicitly set the root level via `logging.getLogger().setLevel(level)` afterwards so the diagnostic WARN still propagates.
- **Files modified:** `common/logging.py::configure` (3-line change).
- **Commit:** b9212c6

### Acknowledged Divergences (intentional, not auto-fixes)

- The plan's `<verify><automated>` grep `grep -q "structlog.stdlib.add_logger_name" common/logging.py` would fail because of Deviation #1. The spirit of the check (canonical `logger` field stamped by a chain processor) is still satisfied, and `test_logger_name_is_stamped` + `test_required_fields_present` enforce it structurally via runtime assertion. The SUMMARY documents the swap so future plan-check / verifier can confirm the objective rather than the literal grep.

## Test Results

- `python3 -m pytest tests/common/test_logging.py tests/common/test_errors.py -v --timeout=10`: **16/16 passed** (11 logging + 5 errors)
- `python3 -m pytest tests/common/ --timeout=10`: **57/57 passed** (41 pre-existing common tests + 16 new)
- Full `tests/` collection has pre-existing `ModuleNotFoundError` failures for `tests/server/test_pipelines.py` (needs `websockets`) and `tests/integration/` (needs `cryptography`) — these are environment-only dependency gaps, not regressions from this plan. Parse-check of all three `main.py` entry points passes.

## Smoke Test Output (captures T-1-04 mitigation live)

```
$ python3 -c "from common.logging import configure, get_logger; configure(phase='server'); get_logger('smoke').info('startup.ok', version='3.0.0', password='leaked?')" 2>&1 | python3 -c "import json, sys; d = json.loads(sys.stdin.read()); print(json.dumps(d, indent=2, sort_keys=True))"
{
  "event": "startup.ok",
  "level": "info",
  "logger": "teraguchi.smoke",
  "password": "[REDACTED]",
  "phase": "server",
  "ts": "2026-04-19T00:52:02.860537Z",
  "version": "3.0.0"
}
```

jq confirmation:
- `.phase` → `"server"` ✓ canonical schema
- `.logger` → `"teraguchi.smoke"` ✓ canonical schema
- `.password` → `"[REDACTED]"` ✓ T-1-04 mitigation
- `.ts` → ISO 8601 UTC ✓
- `.level` → `"info"` ✓
- `.event` → `"startup.ok"` ✓

## Threats Addressed

- **T-1-04** (Information Disclosure — credentials in logs): MITIGATED. `_redact_secrets` case-insensitive substring match on `password / credential / token / secret / key / pin` replaces values with literal `"[REDACTED]"` before `JSONRenderer`. Regression tests: `test_redaction_strips_password` + `test_redaction_strips_case_insensitively` + `test_redaction_does_not_touch_innocent_fields`.
- **T-1-05** (Information Disclosure — ProtocolErrorMsg.message): ACCEPTED with rule. `message` is free-form human text; callers MUST NOT put raw secrets into `str(exception)`. `test_protocol_error_msg_serializes` asserts no `password` key appears in the serialized form. Plan 15 (OBS-05 diagnostic bundle) extends redaction to bundle ZIPs.

## Known Stubs

None. All functionality wired; `StageTimer` has no consumers yet but is ready for Plan 17 (OBS-05 pipeline-latency histogram) — this is the plan's declared `affects:` scope, not a stub.

## TDD Gate Compliance

- **RED:** f0479e7 `test(01-06): add failing tests for structlog schema + error taxonomy` — 16 tests written first; all failed via `ModuleNotFoundError: No module named 'common.errors'` etc.
- **GREEN:** b9212c6 `feat(01-06): add structlog logging baseline + error taxonomy` — `common/logging.py`, `common/errors.py`, extended `common/messages.py`. All 16 tests pass.
- **REFACTOR:** None needed in a separate commit. Minor adjustments during GREEN (custom `_add_logger_name`, dropped `force=True`) happened in the single GREEN commit because they were bug fixes discovered by the test suite, not cleanup.
- **Wire-up commit:** 23cc7d8 `feat(01-06): wire common.logging.configure() into server/client/broker entry points` — three surgical 2-line swaps plus import.

## Self-Check: PASSED

Verified:
- `common/logging.py` exists (351 lines, ≥ 100 required)
- `common/errors.py` exists (149 lines, 15 classes)
- `common/messages.py` contains `class ProtocolErrorMsg` + `ERROR = "error"`
- `tests/common/test_logging.py` has 11 test functions (≥ 9 required)
- `tests/common/test_errors.py` has 5 test functions (≥ 4 required)
- All three `main.py` entry points grep-pass for `from common.logging import configure` + `phase="<tier>"`
- `! grep -q 'tier="server"' server/main.py` (and client/broker) all pass
- Commits f0479e7, b9212c6, 23cc7d8 exist in `git log --oneline`

All acceptance criteria from plan's `<acceptance_criteria>` blocks satisfied (modulo the documented `structlog.stdlib.add_logger_name` → `_add_logger_name` swap, which preserves the objective).
