---
phase: 01-stability-ci-test-baseline
plan: 01
subsystem: infra
tags: [asyncio, queue, video-pipeline, hevc, idr, keyframe, regression-test, pytest, pytest-asyncio, stab-04]

# Dependency graph
requires:
  - phase: phase-0-init
    provides: prototype server/main.py with ClientSession.send_queue bug
provides:
  - ClientSession.send_queue bounded at maxsize=4 (was 30) with IDR-on-drop recovery
  - FakeEncoder test fixture that counts request_keyframe() calls
  - Canned length-prefixed encoded-frame binary fixture (D-02 mocked-encoder boundary)
  - Root conftest.py with FakeWebSocket, FakeEncoder, canned_hevc_keyframes fixtures
  - tests/ package tree scaffold (unit + integration + smoke layers)
  - Regression test suite for STAB-04 (4 tests, all pass locally)
affects: [01-02-ssl-cert-none, 01-03-pytest-baseline, 01-04-ci, 01-05-refactor, phase-02-input-color, phase-04-audio, phase-05-network]

# Tech tracking
tech-stack:
  added: [pytest-asyncio (local runtime verification; formal dependency add lands in Plan 03)]
  patterns:
    - Bounded asyncio.Queue + drop-oldest + IDR-on-drop recovery (STAB-04 template for STAB-07 audit of other pipeline queues)
    - FakeEncoder stand-in at the encoder boundary (D-02 mocked-subprocess test pattern)
    - conftest.py-driven fixture composition (FakeEncoder consumes canned_hevc_keyframes)

key-files:
  created:
    - tests/__init__.py
    - tests/server/__init__.py
    - tests/smoke/__init__.py
    - tests/conftest.py
    - tests/smoke/fixtures/canned_encoded_frames.bin
    - tests/server/test_pipelines.py
  modified:
    - server/main.py (ClientSession.__init__, ClientSession.enqueue, SessionRuntime._enqueue_frame, SessionRuntime._on_encoded_frame)

key-decisions:
  - "Keep ClientSession in server/main.py for this plan; extraction to server/client_session.py is Plan 12 under the test safety net (D-10 / D-14 order-of-operations)"
  - "Drop OLDEST on overflow (freshness over order) — matches VIDEO-06 responsiveness bias; P-frame references are invalidated anyway by the imminent IDR"
  - "Clear queue on keyframe enqueue — the new IDR supersedes any pending P-frames that would reference a frame the decoder will skip"
  - "IDR request fires on FIRST drop of a streak only — repeated IDR requests during a drop burst would thrash the encoder with no recovery benefit"
  - "Use stdlib logger (logger.warning) not structlog — structlog migration is Plan 06"

patterns-established:
  - "Bounded-queue drop recovery: on QueueFull, drop OLDEST + enqueue new; increment _drops_since_keyframe; on first drop of streak call encoder.request_keyframe(); reset counter on keyframe enqueue"
  - "FakeEncoder fixture shape: start(on_encoded_frame), feed_frame(raw), request_keyframe() (counted), stop() — mirrors server.video_encoder.VideoEncoder API without FFmpeg subprocess"
  - "Canned-frame binary layout: repeating [uint32 big-endian size][uint8 is_keyframe][size bytes payload]"

requirements-completed: [STAB-04]

# Metrics
duration: 4min
completed: 2026-04-18
---

# Phase 1 Plan 01: STAB-04 send_queue Fix Summary

**Bounded the per-client send_queue at maxsize=4 and added first-drop-of-streak IDR recovery via `self.runtime.encoder.request_keyframe()` — caps worst-case stall from ~2s GOP boundary to ~100ms IDR latency, committed with a 4-test FakeEncoder-driven regression suite (all green locally).**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-04-18T23:46:34Z
- **Completed:** 2026-04-18T23:50:49Z
- **Tasks:** 2 (both atomic, both green)
- **Files created:** 6 (5 test-scaffold + 1 canned-frames fixture binary)
- **Files modified:** 1 (`server/main.py`)

## Accomplishments

- Fixed STAB-04: `server/main.py:639` queue bound now `maxsize=4` (was 30). `maxsize=30` string no longer present anywhere in `server/main.py`.
- Added IDR-on-drop recovery: first overflow in a drop streak calls `self.runtime.encoder.request_keyframe()`. Counter guards against thrash; a new keyframe enqueue clears the queue and resets the counter.
- Threaded `is_keyframe` bool through `_on_encoded_frame` → `_enqueue_frame` → `enqueue` so the clear-on-IDR branch receives the signal from the encoder callback.
- Landed `tests/` directory scaffold with root `conftest.py` (FakeEncoder, FakeWebSocket, canned_hevc_keyframes fixtures) — establishes test-fixture composition pattern for all subsequent Phase 1 plans.
- Committed `tests/smoke/fixtures/canned_encoded_frames.bin` (530 bytes, 10 frames, 4 keyframes at indices 0/3/6/9) — D-02 mocked-encoder-subprocess boundary baseline.
- All 4 regression tests pass locally (pytest 9.0.3, pytest-asyncio 1.3.0 in ephemeral `.venv-test-local/` — not committed).

## Task Commits

Each task was committed atomically on the worktree branch `worktree-agent-aff0d34a`:

1. **Task 1: Test scaffold + FakeEncoder fixture + canned frames binary** — `2178601` (test)
2. **Task 2: STAB-04 send_queue fix + regression tests** — `78b88e6` (fix, tests land with code per D-04)

_Plan metadata commit (SUMMARY.md) follows this file via the orchestrator's worktree-mode git_commit_metadata step._

## Files Created/Modified

Created:
- `tests/__init__.py` — empty package marker
- `tests/server/__init__.py` — empty package marker
- `tests/smoke/__init__.py` — empty package marker
- `tests/conftest.py` — root fixtures: `FakeWebSocket`, `fake_ws`, `FakeEncoder`, `fake_encoder`, `canned_hevc_keyframes`
- `tests/smoke/fixtures/canned_encoded_frames.bin` — 530 bytes, 10 length-prefixed frames (4 keyframes)
- `tests/server/test_pipelines.py` — 4 regression tests guarding STAB-04 behavior

Modified:
- `server/main.py`:
  - Lines 638-643: `ClientSession.__init__` — `send_queue(maxsize=4)` + `self._drops_since_keyframe = 0` + STAB-04 comment
  - Lines 660-702: `ClientSession.enqueue` — new signature `enqueue(self, data, is_keyframe: bool = False)`, drop-oldest + request-IDR-on-first-drop + clear-on-keyframe policy
  - Lines 380-382: `SessionRuntime._enqueue_frame` — new `is_keyframe: bool = False` parameter threaded to `cs.enqueue`
  - Lines 373-376: `SessionRuntime._on_encoded_frame` — passes `is_keyframe` to `_enqueue_frame` call

## Decisions Made

- **Stdlib `logger.warning`, not structlog.** The STAB-04 patch emits `logger.warning("broadcaster.idr_requested ...")` when an IDR is requested on drop. Structlog migration is Plan 06's scope; pre-empting it here would violate D-14's order-of-operations.
- **No ClientSession extraction.** D-11 assigns ClientSession extraction to Plan 12 (under the test safety net this plan begins). Inlining the fix honors "fix first, decompose under the safety net."
- **No pyproject.toml `[tool.pytest.ini_options]` edits.** That section lands in Plan 03. Tests pass without it because pytest auto-discovers `conftest.py` and `test_*.py` via defaults; `@pytest.mark.asyncio` on each test ensures compatibility regardless of asyncio mode.
- **Drop-OLDEST (FIFO eviction), not drop-NEWEST.** When the decoder is already behind, the freshest frame carries the most current pixels. With an IDR arriving within ~100ms anyway, retaining the oldest frame would just display stale-then-skip.
- **Queue clear on keyframe enqueue.** Any P-frame still queued when the IDR hits would reference a frame the decoder will skip, causing visible artifacting. Clearing preserves decoder coherence.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Removed `maxsize=30` literal from docstring**
- **Found during:** Task 2 acceptance-criteria run
- **Issue:** The plan's acceptance criterion `! grep -q "maxsize=30" server/main.py` requires zero occurrences of the literal `maxsize=30` anywhere in `server/main.py`. My initial enqueue docstring said "...not the ~2s GOP stall the old maxsize=30 + return-False policy produced" which referenced the old value.
- **Fix:** Reworded the docstring to "the old large-queue + return-False policy produced" — semantically identical, no `maxsize=30` string.
- **Files modified:** `server/main.py` (enqueue docstring only)
- **Verification:** `! grep -q "maxsize=30" server/main.py` now exits 0. All plan verify commands pass as a single composite.
- **Committed in:** `78b88e6` (folded into Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking).
**Impact on plan:** Cosmetic docstring wording; did not change behavior or structure. No scope creep.

## Issues Encountered

- **Base python had no test deps.** The system Python 3.14 on this worktree lacked `pytest`, `pytest-asyncio`, `websockets`, `numpy`, etc. Per the plan's additional-context note, I created an ephemeral `.venv-test-local/` to prove the tests pass locally. That venv is gitignored (matches the existing `.venv/` rule via an auto-generated `.gitignore` inside the venv dir) — not committed. Plan 03 formally adds these to `requirements-dev.txt`.
- **No other issues.**

## User Setup Required

None — no external service configuration introduced. All work is in-repo code + tests + fixtures.

## Next Phase Readiness

- **Immediate unblocks:** Every Phase 1 plan that measures latency or claims smooth playback (OBS-02, OBS-03, STAB-09 smoke harness, VIDEO-* work in Phase 2) now measures a corrected pipeline. The IDR-on-drop recovery bounds the worst-case stall to ~100ms instead of the ~2s GOP boundary.
- **Ready for Plan 02 (SEC-01 `ssl.CERT_NONE` removal):** Independent — different files, no contention.
- **Ready for Plan 03 (pytest baseline / pyproject.toml config):** This plan left the test scaffold ready for Plan 03 to add `[tool.pytest.ini_options]` with `asyncio_mode = "auto"` + `testpaths = ["tests"]` + `timeout = 30`.
- **Ready for Plan 12 (ClientSession extraction):** The regression tests in `tests/server/test_pipelines.py` will characterize ClientSession behavior before Plan 12 moves it to `server/client_session.py`.
- **Pattern template for STAB-07 pipeline-queue audit:** The drop-oldest + IDR-on-first-drop pattern codified here is the template for auditing the other 3 pipeline queues per STAB-07.
- **No blockers.**

## Verification Evidence

- `grep -n "maxsize" server/main.py` → 2 matches, both `maxsize=4` (1 comment + 1 literal at line 641).
- `grep -n "request_keyframe" server/main.py` → 5 matches: pre-existing call sites at :233 and :543, plus 3 new references at :664 (docstring), :696 (IDR-on-drop call), :701 (log message).
- `! grep -q "maxsize=30" server/main.py` → exit 0 (literal removed).
- Local pytest run: `.venv-test-local/bin/python -m pytest tests/server/test_pipelines.py -v --timeout=10` → `4 passed, 2 warnings in 0.14s`. The two warnings are unrelated `websockets` deprecation notices, not STAB-04 regressions.
- `python3 -c "import ast; ast.parse(open('server/main.py').read())"` → OK (no syntax regression).

## Self-Check: PASSED

- `tests/__init__.py` — FOUND
- `tests/server/__init__.py` — FOUND
- `tests/smoke/__init__.py` — FOUND
- `tests/conftest.py` — FOUND
- `tests/smoke/fixtures/canned_encoded_frames.bin` — FOUND (530 bytes)
- `tests/server/test_pipelines.py` — FOUND (4 test functions)
- `server/main.py` — MODIFIED (maxsize=4 present; maxsize=30 absent; _drops_since_keyframe present; self.runtime.encoder.request_keyframe call present)
- Commit `2178601` — FOUND in git log
- Commit `78b88e6` — FOUND in git log

---
*Phase: 01-stability-ci-test-baseline*
*Plan: 01 (STAB-04)*
*Completed: 2026-04-18*
