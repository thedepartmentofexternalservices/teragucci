---
phase: 01-stability-ci-test-baseline
plan: 11
subsystem: server/decomposition
tags: [stability, refactor, d-11, stab-05, server]
requires:
  - 01-10 (Wave 6 server decomposition part 1 — StreamLoop/HealthLoop/
    MonitorHotplug/EncoderLifecycle extraction)
  - 01-09 (STAB-05 characterization test — the regression gate)
  - 01-08 (STAB-06 per-session ServerFSM — wired inside ClientSession)
  - 01-01 (STAB-04 send_queue maxsize=4 + IDR-on-drop — preserved verbatim)
provides:
  - server/session_runtime.py::SessionRuntime — per-user session lifecycle
  - server/client_session.py::ClientSession — per-WebSocket connection state
    (carries the STAB-04 fix intact)
  - server/__main__.py — thin entrypoint enabling `python -m server`
  - server/bootstrap.py::check_system_dependencies, ::create_tls_context
  - server/status_endpoint.py::make_status_handler — broker /status HTTP
  - server/main.py shrunk to 540 lines (54% reduction from 1,117 pre-plan;
    55% from 1,209 pre-Wave-6)
affects:
  - server/stream_loop.py, server/health_loop.py, server/encoder_lifecycle.py,
    server/monitor_hotplug.py — TYPE_CHECKING imports updated to point at
    the canonical server.session_runtime home
  - tests/server/test_pipelines.py — import path updated to
    `from server.client_session import ClientSession`
  - tests/integration/test_server_bootstrap.py — unchanged (relies on the
    main.py re-export, which still works)
tech-stack:
  added: []
  patterns:
    - "Module re-export for backward-compat imports: server/main.py
      re-exports SessionRuntime + ClientSession so legacy import paths
      keep working after a pure mechanical move."
    - "Handler-factory pattern for the HTTP status endpoint: state is
      passed via lambdas over module globals rather than an import cycle."
key-files:
  created:
    - server/session_runtime.py
    - server/client_session.py
    - server/__main__.py
    - server/bootstrap.py
    - server/status_endpoint.py
    - .planning/phases/01-stability-ci-test-baseline/01-11-SUMMARY.md
  modified:
    - server/main.py (1,209 pre-Wave-6 → 1,117 post-Wave-6 → 540 now)
    - server/stream_loop.py (TYPE_CHECKING import updated)
    - server/health_loop.py (TYPE_CHECKING import updated)
    - server/encoder_lifecycle.py (TYPE_CHECKING import updated)
    - server/monitor_hotplug.py (TYPE_CHECKING import updated)
    - tests/server/test_pipelines.py (import path updated)
decisions:
  - "Kept a re-export in server/main.py for both ClientSession and
    SessionRuntime to avoid breaking the import path that
    tests/integration/test_server_bootstrap.py (Plan 01-09) and any
    external tooling depend on. Cheap forward-compat; no runtime cost."
  - "Extracted check_system_dependencies + create_tls_context into
    server/bootstrap.py and the HTTP /status handler into
    server/status_endpoint.py to hit the plan's <550-line target for
    server/main.py. These are pure utility extractions (Rule 3 — the
    plan's verify-clause asserts `wc -l < 550` on main.py), not
    architectural changes."
  - "status_endpoint.py exposes a factory (make_status_handler) taking
    getter lambdas over the module globals in server/main.py rather
    than importing them directly — keeps main.py as the single source
    of truth for runtimes/default_runtime without creating an import
    cycle."
metrics:
  duration: 41 minutes
  completed: 2026-04-18
  tasks_completed: 2
  files_created: 5 (plus this SUMMARY)
  files_modified: 6
  commits: 2 (per-task) + 1 (summary)
---

# Phase 01 Plan 11: Server Decomposition Part 2 (D-11 complete) Summary

Completes the D-11 server-side decomposition started in Plan 01-10. The
monolithic `server/main.py` (1,209 lines pre-Wave-6, the original
"~1,191 lines" the plan was sized against) has been reduced to a 540-line
thin orchestrator; the per-user session lifecycle (`SessionRuntime`) and
the per-WebSocket state (`ClientSession`) now live in their own focused
modules, and `python -m server` works via a new thin `__main__.py`
entrypoint. All phase-1 regression gates stay green — STAB-04 pipeline
tests (4/4), STAB-05 bootstrap characterization (2/2), STAB-06 FSM state
sync (all) — plus the full 172-test suite passes unchanged from baseline.

## Objective

Execute the final D-11 extraction: move `SessionRuntime` (the largest
class in the monolith, ~480 lines) and `ClientSession` (the STAB-04-patched
per-connection class) into their own modules. Create a thin
`server/__main__.py` entrypoint. Shrink `server/main.py` to house only
`handle_client`, `main()`, `create_tls_context`, and module globals
(`auth`, `runtimes`, etc.) with a target of <550 lines.

Per D-10 / Plan 09, the regression contract was three gates:
characterization test from Plan 01-09, STAB-04 pipeline test from Plan
01-01, and FSM integration test from Plan 01-08. All three stay green.

## What Changed

### Task 1 — ClientSession extraction (commit `5dfbaa6`)

- **Created `server/client_session.py`** (129 lines): houses `ClientSession`
  wholesale — same `__init__` surface, same `enqueue` signature
  (`(data, is_keyframe: bool = False)`), same `_drops_since_keyframe`
  counter, same `self.runtime.encoder.request_keyframe()` call on the
  first drop of a streak, same `ServerFSM()` instantiation from Plan
  01-08, same `last_reported_client_state` field for Plan 01-17.
- **Patched `server/main.py`:** deleted the in-line class block, added
  a re-export (`from server.client_session import ClientSession`) so
  `from server.main import ClientSession` still resolves.
- **Patched `tests/server/test_pipelines.py`:** updated the single import
  from `server.main` → `server.client_session`. All 4 STAB-04 tests pass.

### Task 2 — SessionRuntime extraction + `__main__.py` (commit `0be5cdf`)

- **Created `server/session_runtime.py`** (558 lines): houses `SessionRuntime`
  wholesale. Every preservation invariant from `PATTERNS.md §"server/
  session_runtime.py"` is intact:
  - IS_MACOS platform branch with try/finally DISPLAY env swap
  - encoder spawn / restart / stop routed through `EncoderLifecycle`
    (from Plan 01-10) with the `self.encoder` back-compat handle
  - `threading.Lock` guarding the `clients` dict across asyncio + encoder
    threads
  - cross-thread `asyncio.run_coroutine_threadsafe` for every encoder
    callback and cursor/clipboard/audio broadcast
  - task-start pattern delegating to `self._stream_loop.start(fps)` /
    `self._health_loop.start()` / `self._hotplug.start()`
- **Created `server/__main__.py`** (21 lines): thin entrypoint calling
  `server.main:main`. Now `python -m server` works in addition to the
  existing `python -m server.main`. `pyproject.toml` console-script
  (`teraguchi-server = "server.main:main"`) unchanged.
- **Created `server/bootstrap.py`** (47 lines): houses
  `check_system_dependencies` + `create_tls_context`. Pure utility
  functions with no module-global dependencies.
- **Created `server/status_endpoint.py`** (92 lines): houses the HTTP
  `/status` handler factory for broker health probes. Takes getter
  lambdas over main.py's `runtimes` / `default_runtime` module globals
  so no runtime import cycle is introduced.
- **Patched `server/main.py`:** deleted `SessionRuntime`, `handle_http`,
  `check_system_dependencies`, `create_tls_context` bodies (all now in
  their new homes); added re-export for `SessionRuntime`; wired
  `handle_http` via `make_status_handler` factory; cleaned up imports
  that were only used by the extracted classes (many `common.messages`
  constants, `threading`, `subprocess`, `time`, `pathlib`, all the
  transport imports that were unused). Final size: **540 lines** (target
  `<550`, hit).
- **Patched Wave-6 sub-loop modules** (`stream_loop`, `health_loop`,
  `encoder_lifecycle`, `monitor_hotplug`): TYPE_CHECKING imports updated
  to point at `server.session_runtime` rather than `server.main` so
  static type checkers resolve to the canonical home.

## Preservation Proof Points

- **STAB-04 (Plan 01-01):** `tests/server/test_pipelines.py` — all 4
  tests pass. `maxsize=4` ✓, drop-OLDEST-on-full ✓, IDR-on-first-drop
  ✓, keyframe-clears-queue-and-resets-counter ✓.
- **STAB-05 characterization (Plan 01-09):**
  `tests/integration/test_server_bootstrap.py` — both tests pass.
  `handle_client` accepts a client over real TLS, emits `ServerHello`,
  FSM advances past `bootstrapping`, `add_client` fires on the fake
  runtime. Second test (ClientHello → FSM progression) still works via
  the existing fallback assertion.
- **STAB-06 (Plan 01-08):** FSM state-sync integration tests unchanged
  (they go through `server.main.handle_client` which still drives the
  FSM through `tls_ok` → `auth_ok` → `client_hello` via
  `runtime.handle_input`).
- **Full suite:** 172 passed, 1 xfailed, identical to pre-plan baseline.

## Import Compatibility

The following import paths all resolve identically pre- and post-plan
(verified by import-smoke + module-class-identity checks):

```python
from server.main import main, handle_client          # unchanged
from server.main import ClientSession                # re-export
from server.main import SessionRuntime               # re-export
from server.client_session import ClientSession      # new canonical
from server.session_runtime import SessionRuntime    # new canonical
```

`ClientSession.__module__` now reports `"server.client_session"` and
`SessionRuntime.__module__` reports `"server.session_runtime"` (verified
at a Python REPL).

## Entrypoint Compatibility

- `python -m server.main --help` — works (unchanged)
- `python -m server --help` — works (new, via `__main__.py`)
- `teraguchi-server` console-script — still points at `server.main:main`
  via `pyproject.toml`; no pyproject changes needed.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Hit the <550 line target]** Extracted `handle_http`,
`check_system_dependencies`, and `create_tls_context` into dedicated
modules (`server/status_endpoint.py` + `server/bootstrap.py`).

- **Found during:** Task 2 post-verify line count check
- **Issue:** After the SessionRuntime extraction `server/main.py` was
  619 lines — still over the plan's `<550` assertion
  (`test "$(wc -l < server/main.py)" -lt 550`) in the verify clause.
  `handle_client` (219 lines) and `main()` (178 lines) are legitimately
  load-bearing and the plan explicitly says `handle_client` must stay
  at top level.
- **Fix:** Three small sibling utility modules carry the remaining
  non-core code out of main.py: `status_endpoint.py` for the broker
  HTTP probe, `bootstrap.py` for dependency-check + TLS-context
  helpers. No behavior change — `status_endpoint.make_status_handler`
  is called with lambdas that read main.py's module globals so the
  data flow is identical.
- **Files created:** `server/status_endpoint.py`, `server/bootstrap.py`
- **Commit:** `0be5cdf` (part of Task 2)

### Import Cleanup

- Alongside the Rule-3 deviation, trimmed a large set of imports in
  `server/main.py` that were only used by `SessionRuntime` (most of
  `common.messages`, all of `common.udp_transport`,
  `common.hybrid_transport`, `common.quic_transport`, `server.cursor_tracker`,
  `server.audio_capture`, `server.health`, `server.file_transfer`,
  `server.usb_passthrough`, `server.stream_loop`, `server.health_loop`,
  `server.encoder_lifecycle`, `server.monitor_hotplug`, `threading`,
  `subprocess`, `time`, `pathlib`). This is part of the extraction,
  not a separate refactor.

## Self-Check: PASSED

- Created files all present:
  - `server/session_runtime.py` ✓
  - `server/client_session.py` ✓
  - `server/__main__.py` ✓
  - `server/bootstrap.py` ✓
  - `server/status_endpoint.py` ✓
- Commits exist on worktree branch:
  - `5dfbaa6` (Task 1) ✓
  - `0be5cdf` (Task 2) ✓
- `server/main.py` line count: 540 (< 550 target) ✓
- Re-exports in `server/main.py`:
  - `from server.client_session import ClientSession` ✓
  - `from server.session_runtime import SessionRuntime` ✓
- `python -m server --help` works ✓
- `python -m server.main --help` works ✓
- Import smoke: `from server.main import main, handle_client;
  from server.session_runtime import SessionRuntime;
  from server.client_session import ClientSession` resolves ✓
- Full pytest suite: 172 passed, 1 xfailed (matches baseline) ✓
- STAB-04 gate: 4/4 pass ✓
- STAB-05 gate: 2/2 pass ✓
- STAB-06 FSM gate: all pass ✓
- No `class ClientSession` or `class SessionRuntime` in `server/main.py` ✓
- Not on `dev` branch (on `worktree-agent-aa5a02bb`) ✓
- No `.planning/STATE.md` or `.planning/ROADMAP.md` modifications ✓
