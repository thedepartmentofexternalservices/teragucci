---
phase: 01-stability-ci-test-baseline
plan: 10
subsystem: refactor
tags: [server, decomposition, d-11, asyncio, encoder, streaming, monitor-hotplug, health-loop]

# Dependency graph
requires:
  - phase: 01-stability-ci-test-baseline
    provides: "Plan 01-01 bounded send_queue + IDR-on-drop (runtime.encoder handle is load-bearing); Plan 01-08 ServerFSM state stamping on HealthPong; Plan 01-09 characterization tests acting as the regression gate for this decomposition"
provides:
  - "server/stream_loop.py — StreamLoop class owning capture → encode → dispatch loops (h264 + jpeg)"
  - "server/health_loop.py — HealthLoop class owning 2s-tick HealthPong + HealthStats broadcast with server_state stamping"
  - "server/encoder_lifecycle.py — EncoderLifecycle class owning VideoEncoder spawn/restart/stop with NVENC fallback preservation"
  - "server/monitor_hotplug.py — MonitorHotplug class owning 5s-tick hot-plug detection + encoder-restart"
  - "server/main.py::SessionRuntime is now a wiring + shared-state owner rather than a monolithic loop-runner"
affects: [01-11, 01-14, 01-17]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "D-11 SessionRuntime decomposition pattern — sub-loop classes take the runtime as constructor-injected back-reference and reach shared state (capture, encoder, clients, health) through self._runtime, keeping asyncio + threading.Lock usage exactly as-is"
    - "EncoderLifecycle back-compat handle — runtime.encoder stays a live attribute (refreshed on every spawn/restart/stop) so Plan 01-01's IDR-on-drop call site and tests/server/test_pipelines.py assertions continue to work unchanged"

key-files:
  created:
    - "server/stream_loop.py"
    - "server/health_loop.py"
    - "server/encoder_lifecycle.py"
    - "server/monitor_hotplug.py"
  modified:
    - "server/main.py"

key-decisions:
  - "Kept runtime.encoder as a back-compat attribute rather than forcing all call sites to reach encoder_lifecycle.encoder — preserves Plan 01-01's STAB-04 patch and avoids touching test_pipelines.py assertions"
  - "Kept _hotplug_task field deletion inside __init__ rather than keeping a stub — MonitorHotplug sub-object now owns its own task handle; SessionRuntime.stop() delegates teardown to _hotplug.stop()"
  - "Passed sw_only + jpeg_quality into EncoderLifecycle.spawn() as keyword args rather than stashing them on the runtime — spawn() is the only site that needs them, and they're one-time __init__ decisions"
  - "Left unused imports (HealthPong, encode_jpeg_header) in server/main.py untouched — pure extraction plan, import-cleanup is out of scope"

patterns-established:
  - "Sub-loop lifecycle: start() flips _running=True and asyncio.ensure_future(self.run(...)); stop() flips _running=False and cancels the task handle if not done — mirrors the pre-extraction _stream_task / _health_task / _hotplug_task pattern exactly"
  - "Module logger naming: logging.getLogger('teraguchi.server.<module>') — matches CONVENTIONS.md + the existing server/main.py 'teraguchi.server' logger, routed through common.logging.configure()'s stdlib bridge"

requirements-completed: [STAB-05]

# Metrics
duration: ~30min
completed: 2026-04-19
---

# Phase 1 Plan 10: Server Decomposition Pt. 1 (StreamLoop + HealthLoop + EncoderLifecycle + MonitorHotplug) Summary

**Extracted 4 of D-11's 6 server modules (StreamLoop, HealthLoop, EncoderLifecycle, MonitorHotplug) out of server/main.py::SessionRuntime with zero behavior change — all 172 pre-existing tests still pass + 1 xfail unchanged.**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-04-19T01:00:00Z (approx)
- **Completed:** 2026-04-19T01:36:00Z
- **Tasks:** 2
- **Files created:** 4
- **Files modified:** 1

## Accomplishments

- D-11 Wave 6 step 1 complete: 4 of the 6 planned server modules extracted. Plan 01-11 picks up SessionRuntime + ClientSession + thin `__main__`.
- `server/main.py` shrank 1299 → 1209 lines (-90). Each new module lands well under the 250-line D-11 target (stream_loop 103, health_loop 69, encoder_lifecycle 119, monitor_hotplug 61).
- Regression gate (Plan 01-09's characterization tests + Plan 01-01's STAB-04 pipeline tests) held green before and after both tasks. Full suite 172 passed + 1 xfailed, matching the Wave-5 baseline exactly.
- `runtime.encoder` back-compat attribute preserved via deliberate EncoderLifecycle.spawn/restart/stop keeping it in sync — no test or external call site needed changes.
- All four new modules use the canonical `logging.getLogger("teraguchi.server.<module>")` naming, routed through the stdlib bridge inside `common.logging.configure()`.

## Task Commits

Each task was committed atomically:

1. **Task 1: Extract StreamLoop + HealthLoop** — `7996ad3` (refactor)
2. **Task 2: Extract EncoderLifecycle + MonitorHotplug** — `3caf211` (refactor)

_No metadata commit made at the executor stage — orchestrator handles state/roadmap updates after merge._

## Files Created/Modified

### Created
- `server/stream_loop.py` (103 lines) — StreamLoop class with `run_h264()`, `run_jpeg()`, `start(fps)`, `stop()`. Preserves per-frame try/except + FPS-pacing exactly from the pre-extraction monolith.
- `server/health_loop.py` (69 lines) — HealthLoop class with `run()`, `start()`, `stop()`. Emits HealthPong (server_state stamped) + HealthStats on the 2s tick to every authenticated client.
- `server/encoder_lifecycle.py` (119 lines) — EncoderLifecycle class with `spawn(sw_only, jpeg_quality)`, `restart()`, `stop()`. `spawn()` absorbs the `codec in (h264,h265,av1) and ffmpeg_caps.get(codec)` check from SessionRuntime.__init__; `restart()` preserves NVENC fallback state via `_available` reuse exactly as the old `_restart_encoder` did.
- `server/monitor_hotplug.py` (61 lines) — MonitorHotplug class with `run()`, `start()`, `stop()`. Polls `capture.detect_hotplug()` every 5s; broadcasts refreshed monitor list + restarts encoder through `encoder_lifecycle.restart()`.

### Modified
- `server/main.py` (1299 → 1209 lines, -90):
  - Added imports for the 4 new classes.
  - Replaced inline encoder-init block (L148-163) with `self.encoder_lifecycle = EncoderLifecycle(self); self.encoder_lifecycle.spawn(sw_only=sw_only, jpeg_quality=jpeg_quality)`.
  - Replaced `_stream_task` / `_health_task` / `_hotplug_task` fields with `self._stream_loop`, `self._health_loop`, `self._hotplug` sub-objects.
  - `_start_streaming()` now calls `_stream_loop.start(fps)`, `_health_loop.start()`, `_hotplug.start()` instead of `asyncio.ensure_future(...)`.
  - Deleted method bodies: `_stream_h264`, `_stream_jpeg`, `_health_ping_loop`, `_monitor_hotplug_loop`, `_restart_encoder`.
  - Rewrote 3 internal call sites (`apply_quality`, `_handle_resize` x2, `handle_input`) from `self._restart_encoder()` → `self.encoder_lifecycle.restart()`.
  - `stop()` delegates to `_stream_loop.stop()`, `_health_loop.stop()`, `_hotplug.stop()`, `encoder_lifecycle.stop()` in that order.

## Decisions Made

- **runtime.encoder as back-compat attribute:** Plan 01-01's STAB-04 fix put `self.runtime.encoder.request_keyframe()` inside `ClientSession.enqueue()`, and `tests/server/test_pipelines.py` asserts on `cs.runtime.encoder.keyframe_requests`. Rather than refactor those call sites (out of scope for this plan's zero-behavior-change contract), `EncoderLifecycle.spawn/restart/stop` explicitly assigns `runtime.encoder = self.encoder` on every transition. The attribute is now a read-only view onto `encoder_lifecycle.encoder` from external callers' perspective.
- **sw_only + jpeg_quality as spawn() args rather than runtime attributes:** These are one-time SessionRuntime.__init__ inputs that only matter to the initial encoder decision. Stashing them on the runtime would widen the API surface for no benefit. spawn() takes them as keyword args; restart() doesn't need them (reuses encoder's `_available`).
- **No cleanup of now-unused imports in main.py:** `HealthPong` and `encode_jpeg_header` are no longer used at runtime in server/main.py after the extraction. Left untouched — this is a pure extraction plan and import-cleanup is not in scope (would add risk of touching the wrong symbol). Plan 01-11 or a later OSS-polish pass can prune.

## Deviations from Plan

None — plan executed exactly as written. Both tasks' action blocks, verification commands, and acceptance criteria matched the plan precisely. One tactical divergence worth recording (the plan's PATTERNS sketch shows the `@property def encoder` fallback; the final implementation went with explicit-assignment instead of a property because the handle is written in exactly 3 places — spawn/restart/stop — and the explicit-assignment form is easier to audit during later plans):

- **Plan mentioned `@property def encoder` fallback for back-compat.** The plan's Task 2 `<action>` block offered a property-based fallback if Plan 01's code relied on `self.runtime.encoder` directly. I used the **explicit-assignment** variant instead (plan's primary suggestion, mentioned first): `runtime.encoder = self.encoder` inside `spawn()` / `restart()`; `runtime.encoder = None` inside `stop()`. The property form wasn't needed — all 3 call sites that mutate the encoder live in EncoderLifecycle methods, so an explicit assignment from those methods is simpler to audit and cheaper at runtime (no descriptor lookup per `self.encoder` read).

## Issues Encountered

**Tool absolute-path confusion between main repo and worktree.** The prompt's `<files_to_read>` block referenced absolute paths under `/Users/randymcentee/workspace/GitHub/teraguchi/...` which resolve to the **main repo checkout on the `dev` branch**, not the worktree at `.claude/worktrees/agent-accb50ce/`. My first round of Task 1 edits landed in the main repo by mistake. Caught via `git branch --show-current` verification (the pre-commit safety check explicitly called out in the executor prompt): branch reported `dev`, not `worktree-agent-*`. Recovered by copying the new module files to `/tmp/plan-01-10-salvage/`, reverting the main repo via `git checkout -- server/main.py && rm server/stream_loop.py server/health_loop.py`, then re-applying the same edits against the worktree-relative paths. All subsequent Task 2 edits used worktree paths exclusively.

No code damage — tests green in the worktree, main repo's `dev` branch unchanged after revert.

## User Setup Required

None — no external service configuration required. Pure in-tree refactor.

## Next Phase Readiness

- **Plan 01-11 unblocked:** Has clean seams to extract SessionRuntime + ClientSession into their own modules + introduce the thin `__main__`. The 4 sub-loops are now independently movable; what's left inside `server/main.py` is glue + global module state (auth, runtimes dict, CLI main(), handle_http, run_server).
- **Plan 01-14 (OBS-02 per-stage instrumentation) has cleaner hook points:** StreamLoop.run_h264 / HealthLoop.run / EncoderLifecycle.spawn|restart can each host StageTimer wraps without fighting the monolith.
- **Plan 01-17 (OBS-03 / health pair-check):** HealthLoop is now the single place to add the state-disagreement counter bump.

## Self-Check

- [x] `server/stream_loop.py` exists, contains `class StreamLoop`
- [x] `server/health_loop.py` exists, contains `class HealthLoop`
- [x] `server/encoder_lifecycle.py` exists, contains `class EncoderLifecycle`
- [x] `server/monitor_hotplug.py` exists, contains `class MonitorHotplug`
- [x] `server/main.py` imports all 4 new classes (`grep -qE "from server\.(stream_loop|health_loop|encoder_lifecycle|monitor_hotplug)" server/main.py` returns 0)
- [x] `server/main.py` no longer has `async def _stream_h264`, `async def _stream_jpeg`, `async def _health_ping_loop`, `async def _monitor_hotplug_loop`, or `def _restart_encoder`
- [x] Commit `7996ad3` exists in `git log --all` (Task 1)
- [x] Commit `3caf211` exists in `git log --all` (Task 2)
- [x] `tests/integration/test_server_bootstrap.py` passes (regression gate)
- [x] `tests/server/test_pipelines.py` passes (STAB-04 back-compat check)
- [x] Full suite: 172 passed + 1 xfailed (matches Wave-5 baseline)
- [x] No new `logging.getLogger` calls bypass the canonical `teraguchi.server.<module>` naming
- [x] No `ssl.CERT_NONE` regressions outside the pre-existing Plan 01-02 double-gated dev escape hatch in `common/quic_transport.py:429`
- [x] STATE.md + ROADMAP.md untouched (per parallel-executor contract)
- [x] `git branch --show-current` returns `worktree-agent-accb50ce` for every commit

## Self-Check: PASSED

---
*Phase: 01-stability-ci-test-baseline*
*Completed: 2026-04-19*
