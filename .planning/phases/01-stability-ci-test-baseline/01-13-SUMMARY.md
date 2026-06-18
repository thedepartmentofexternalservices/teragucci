---
phase: 01-stability-ci-test-baseline
plan: 13
subsystem: infra
tags: [asyncio, queues, backpressure, stab-07, obs-03-hooks, pipelines]

# Dependency graph
requires:
  - phase: 01-stability-ci-test-baseline
    provides: "Plan 01-01 STAB-04 send_queue maxsize=4 + IDR-on-drop (ClientSession); Plan 01-10 StreamLoop extraction; Plan 01-11 SessionRuntime extraction"
provides:
  - "server/pipelines package: CaptureQueue (maxsize=2, drop-oldest) + EncoderQueue (maxsize=3, drop-oldest)"
  - "on_drop telemetry hook wired to HealthMonitor.record_frame_dropped — ready for Plan 01-14 OBS-03 per-queue drop counters"
  - "SessionRuntime instantiates both pipeline queues so future capture-rate decoupling has a wiring anchor"
  - "tests/server/test_pipelines.py coverage for all 4 pipeline queue shapes: capture + encoder + send (STAB-04) + input-block (asyncio.Queue default)"
affects: [phase-01-plan-14-obs-telemetry, phase-05-network, phase-04-audio]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Bounded-queue wrapper with on_drop telemetry hook (class pair — CaptureQueue/EncoderQueue)"
    - "Drop-OLDEST via try-put_nowait → QueueFull → get_nowait + put_nowait"
    - "on_drop callback exception-swallowed at DEBUG — put() is the hot path, telemetry must not fail it"

key-files:
  created:
    - server/pipelines/__init__.py
    - server/pipelines/capture_queue.py
    - server/pipelines/encoder_queue.py
  modified:
    - server/session_runtime.py
    - tests/server/test_pipelines.py

key-decisions:
  - "Per-client send_queue stays in ClientSession (not hoisted into pipelines/) because its drop handling reads session-local _drops_since_keyframe state for STAB-04 IDR-on-drop. Wrapping it would fork that behavior."
  - "Input queue uses plain asyncio.Queue(maxsize=64) with no wrapper — blocking on full is the stdlib default; a CaptureQueue-style wrapper would have to deliberately *remove* that behavior."
  - "Pipeline queues wired into SessionRuntime as instrumentation-visible attributes today; the StreamLoop sync capture → encoder.feed_frame handoff is NOT refactored because that's a bigger change than STAB-07 scopes, and the queues stay empty under the current sync path (no behavior change)."
  - "on_drop callback exceptions are swallowed at DEBUG so faulty telemetry cannot break the pipeline put() contract."

patterns-established:
  - "Bounded pipeline queue pattern: maxsize + dropped counter + on_drop callback, all three exposed publicly for Plan 14 telemetry extension"
  - "Source-file text inspection (not inspect.getsource) for wiring-contract tests that would otherwise require full module imports — keeps unit tests runnable on CI nodes without capture stack"

requirements-completed: [STAB-07]

# Metrics
duration: ~15min
completed: 2026-04-19
---

# Phase 1 Plan 13: Pipeline Queue Audit (STAB-07) Summary

**CaptureQueue (maxsize=2) + EncoderQueue (maxsize=3) drop-oldest wrappers with OBS-03-ready on_drop hooks, wired into SessionRuntime; input queue blocking + send queue IDR-on-drop preserved unchanged**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-04-19T02:11:00Z
- **Completed:** 2026-04-19T02:26:19Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files created:** 4 (3 source + 1 package __init__)
- **Files modified:** 2 (session_runtime.py + test_pipelines.py)

## Accomplishments

- 4-queue pipeline bounded + policy-correct across the capture → encode → transport path:
  | Queue | Where | Maxsize | Policy |
  |-------|-------|---------|--------|
  | Capture → Encoder | `server/pipelines/capture_queue.py` | 2 | Drop-OLDEST + on_drop hook |
  | Encoder → Broadcaster | `server/pipelines/encoder_queue.py` | 3 | Drop-OLDEST + on_drop hook |
  | Broadcaster → per-client | `server/client_session.py::ClientSession.send_queue` (untouched — Plan 01-01 STAB-04) | 4 | Drop-OLDEST + IDR-on-drop |
  | Input (client→server) | plain `asyncio.Queue(maxsize=64)` default | 64 | BLOCK producer (stdlib default) |
- `on_drop` telemetry hook threaded through to `HealthMonitor.record_frame_dropped` so Plan 01-14 (OBS-03) can read per-queue drop counters without further wiring
- STAB-04 regression gate intact: all 4 pre-existing `test_send_queue_*` tests pass unchanged
- 9 new tests added (13 total in `test_pipelines.py`):
  - `test_capture_queue_default_maxsize_is_two`
  - `test_encoder_queue_default_maxsize_is_three`
  - `test_capture_queue_drops_oldest`
  - `test_encoder_queue_drops_oldest`
  - `test_capture_queue_on_drop_hook`
  - `test_encoder_queue_on_drop_hook`
  - `test_capture_queue_on_drop_hook_exception_swallowed`
  - `test_input_queue_blocks_on_full`
  - `test_session_runtime_exposes_pipeline_queues`

## Task Commits

1. **RED: STAB-07 failing regression tests** — `c075658` (test)
2. **GREEN: CaptureQueue + EncoderQueue + SessionRuntime wiring** — `58a5ca2` (feat)

_No refactor commit — the implementation landed clean and the test that required a workaround (source-file read instead of module import) was adjusted inside the GREEN commit so the wiring contract stays asserted without pulling PIL/CoreGraphics into the unit-test import graph._

## Files Created/Modified

### Created

- `server/pipelines/__init__.py` — Package marker + re-exports of `CaptureQueue` and `EncoderQueue`; module docstring states the 4-queue shape of the pipeline so future readers don't have to chase it across files.
- `server/pipelines/capture_queue.py` — `CaptureQueue(asyncio)` wrapper: maxsize=2 default, drop-OLDEST on full, zero-arg `on_drop` callback, `dropped` counter. Callback exceptions swallowed at DEBUG so telemetry cannot break the put path.
- `server/pipelines/encoder_queue.py` — Same shape as CaptureQueue, maxsize=3 default. Kept as a separate class (not a shared base) so Plan 01-14 can specialise drop-reason labelling per queue when telemetry lands.

### Modified

- `server/session_runtime.py` — Adds `from server.pipelines import CaptureQueue, EncoderQueue` and instantiates both queues in `__init__` with `on_drop=self.health.record_frame_dropped`. The comment documents why the wiring is instrumentation-visible today (sync capture path) and what Plan 01-14 will do with the hook. ~18 lines added, zero lines removed from existing behavior.
- `tests/server/test_pipelines.py` — Appends 9 new tests under a `# ── STAB-07 …` banner; all 4 pre-existing STAB-04 tests remain untouched. Final test uses direct file-read inspection (not `inspect.getsource(module)`) so the wiring contract is asserted without importing `server.session_runtime` (which drags in PIL/CoreGraphics bindings not present in unit test env).

## Decisions Made

1. **Per-client `send_queue` stays in `ClientSession`** — deliberately NOT hoisted into `server/pipelines/`. The `ClientSession.enqueue` method owns the `_drops_since_keyframe` streak counter and the `runtime.encoder.request_keyframe()` call; wrapping that into the generic pipeline base would either fork the behavior or couple the pipelines package to the encoder API. Leaving STAB-04 exactly as Plan 01-01 landed it is the lower-risk choice.
2. **Input queue uses plain `asyncio.Queue(maxsize=64)`** — no wrapper class. The whole point of the input queue is to BLOCK the producer on full (we never drop input). `asyncio.Queue.put` already blocks on full by default. A wrapper would have to deliberately NOT alter that behavior, i.e. do nothing useful. Tested via `test_input_queue_blocks_on_full` using a plain `asyncio.Queue(maxsize=2)` and `asyncio.wait_for` to confirm the producer blocks.
3. **Wiring depth: instrumentation-visible, not in the sync capture path** — the plan's `<action>` block explicitly notes: "wire the queues visibly (instantiate, expose on the runtime), but don't try to refactor StreamLoop's synchronous capture call right now." Honored. `StreamLoop.run_h264` still calls `encoder.feed_frame(raw)` synchronously; the pipeline queues are reachable via `runtime._capture_queue` / `runtime._encoder_queue` for Plan 01-14 telemetry and any future Phase 5 capture-rate decoupling. Behavior is unchanged on the hot path.
4. **`on_drop` exception swallow at DEBUG** — the `put()` method MUST NOT fail because telemetry misbehaves. Exceptions from the callback are caught and logged at `logger.debug(...)` only. A dedicated test (`test_capture_queue_on_drop_hook_exception_swallowed`) pins this behavior.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Adjusted `test_session_runtime_exposes_pipeline_queues` to read source file instead of importing module**
- **Found during:** GREEN step of Task 1 — test written per plan draft failed because `import server.session_runtime` triggers the full module import chain, which requires `PIL` / `CoreGraphics` / `Xlib` bindings that unit-test environments don't install.
- **Issue:** Using `inspect.getsource(module)` forces a real import. The wiring contract is purely textual (does SessionRuntime reference the queue classes?) so a real import isn't needed.
- **Fix:** Test now reads `server/session_runtime.py` via `pathlib.Path(...).read_text()` and greps for the import line + class instantiations. Contract is asserted identically; CI portability improves.
- **Files modified:** `tests/server/test_pipelines.py` (single test method body)
- **Verification:** `python3 -m pytest tests/server/test_pipelines.py -v` → 13/13 pass
- **Committed in:** `58a5ca2` (same GREEN commit — adjustment made before commit)

---

**Total deviations:** 1 auto-fixed (Rule 3 — unblocking the new test on dep-light environments)
**Impact on plan:** Zero functional change. Contract asserted identically. Plan's `<action>` block sketched the test shape but didn't specify inspection mechanism; I picked the lower-dep option so CI doesn't need PIL installed just to assert that two class names appear in a `.py` file.

## Issues Encountered

- **Missing unit-test deps in the worktree Python environment** — `websockets`, `pytest-asyncio`, `python-statemachine`, `freezegun`, `psutil`, `pillow`, `numpy`, `cryptography`, `aiohttp`. Not caused by this plan; the worktree inherits its Python from Homebrew's `python@3.14` rather than a project-managed venv. Installed via `pip install --break-system-packages` for local verification. CI nodes install from `requirements-dev.txt` so this is local-only friction.
- **Pre-existing PAM test failures** (`tests/server/test_pam_auth.py` — 1 failure + 3 errors) — confirmed pre-existing via `git stash`. Out of scope for Plan 01-13 per deviation rule scope boundary. Not logged to `deferred-items.md` since phase tracking already has visibility (pre-existing, known, not regressed by my work).

## User Setup Required

None — no external service configuration.

## Next Phase Readiness

### What Plan 01-14 (OBS-03) Inherits

- `CaptureQueue.dropped` and `EncoderQueue.dropped` counters exposed as public int attributes — Plan 14 can poll them from `HealthMonitor.get_stats()` or emit them on structlog `pipeline.queue_drop` events with `queue_name` + `depth` + `policy` fields.
- `on_drop` callbacks are already firing (routed to `HealthMonitor.record_frame_dropped`) so `HealthStats.frames_dropped` already increments when the pipeline queues drop. Plan 14 can split this into per-queue sub-counters by swapping the callback for queue-labeled variants.

### Known follow-ups (out of scope for 01-13)

- **Sync capture path refactor** — `StreamLoop.run_h264` still calls `encoder.feed_frame(raw)` synchronously. The pipeline queues are wired but currently empty on the hot path. Decoupling capture rate from encoder rate is a Phase 5 / Phase 3 concern (multi-monitor) and isn't required for STAB-07.
- **Pre-existing PAM test failures** — `tests/server/test_pam_auth.py` mock issues unrelated to this plan. Flagged in previous waves; not my regression.

## Self-Check: PASSED

### Created files exist

```
FOUND: server/pipelines/__init__.py
FOUND: server/pipelines/capture_queue.py
FOUND: server/pipelines/encoder_queue.py
```

### Commits exist

```
FOUND: c075658 (test RED)
FOUND: 58a5ca2 (feat GREEN)
```

### Verification gate (from PLAN)

```
test -f server/pipelines/__init__.py                                 → OK
test -f server/pipelines/capture_queue.py                            → OK
test -f server/pipelines/encoder_queue.py                            → OK
grep -q "class CaptureQueue" server/pipelines/capture_queue.py       → OK
grep -q "class EncoderQueue" server/pipelines/encoder_queue.py       → OK
grep -q "maxsize: int = 2" server/pipelines/capture_queue.py         → OK
grep -q "maxsize: int = 3" server/pipelines/encoder_queue.py         → OK
grep -q "CaptureQueue" server/session_runtime.py                     → OK
grep -q "def test_capture_queue_drops_oldest" tests/server/test_pipelines.py → OK
grep -q "def test_input_queue_blocks_on_full" tests/server/test_pipelines.py → OK
grep -rn "asyncio.Queue(" server/ | grep -v "maxsize=" | grep -v QueueFull | grep -v QueueEmpty → empty
python3 -m pytest tests/server/test_pipelines.py -x --timeout=10     → 13/13 pass
python3 -m pytest tests/integration/test_server_bootstrap.py         → 2/2 pass
```

### Full suite (excluding smoke)

190 passed, 1 xfailed, 1 pre-existing PAM failure + 3 pre-existing PAM errors (confirmed pre-existing via `git stash`; out of scope per deviation rule scope boundary).

---
*Phase: 01-stability-ci-test-baseline*
*Plan: 13*
*Completed: 2026-04-19*
