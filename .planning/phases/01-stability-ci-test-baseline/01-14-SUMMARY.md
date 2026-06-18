---
phase: 01-stability-ci-test-baseline
plan: 14
subsystem: observability

tags: [obs-02, obs-03, healthmonitor, healthstats, stagetimer, structlog, clock-offset, keyframe-telemetry, state-disagreement]

# Dependency graph
requires:
  - phase: 01-stability-ci-test-baseline
    provides: "StageTimer (Plan 01-06), HealthLoop + StreamLoop extracted (Plan 01-10), ClientSession extracted (Plan 01-11), ServerFSM + ALLOWED_PAIRS (Plan 01-08)"
provides:
  - "HealthMonitor per-stage latency deques (transmit/decode/display) + keyframe counters"
  - "HealthStats wire fields for per-stage latency + keyframe telemetry"
  - "ClockOffsetEstimator (~40-LOC EWMA helper, re-estimate every 60 s)"
  - "StageTimer wiring around capture + encode in StreamLoop.run_h264"
  - "Structured ERROR 'fsm.state_disagreement' structlog event (upgrade from Plan 01-08 stdlib warning)"
  - "Keyframe request/emit counters backed by STAB-04 IDR-on-drop path + encoder callback"
affects: [phase-02-input-color, phase-04-network, plan-01-15-connection-supervisor, plan-01-17-smoke-harness]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "StageTimer context manager wrap around measurable pipeline stages (common/logging.StageTimer)"
    - "Per-stage deque + get_stats() aggregation (HealthMonitor convention extended to 6 stages)"
    - "Structlog ERROR emit for state-disagreement (overlay/dashboard filter on level=error)"
    - "EWMA clock-offset estimator, one sample per ping/pong exchange, re-estimate on 60 s interval"

key-files:
  created:
    - "common/clock_offset.py — ClockOffsetEstimator EWMA helper (~40 LOC)"
    - "tests/server/test_health_monitor.py — OBS-02/OBS-03 unit tests (15 tests)"
    - "tests/integration/test_latency_breakdown.py — StageTimer + state-disagreement integration tests (7 tests)"
  modified:
    - "server/health.py — +3 stage deques, +2 keyframe counters, +5 recorder methods, extended get_stats()"
    - "common/messages.py — HealthStats +5 new fields (backward-compatible 0 defaults)"
    - "server/stream_loop.py — StageTimer imported + wrapped around capture + encode in run_h264"
    - "server/health_loop.py — check_state_pair() function (structlog ERROR emit)"
    - "server/session_runtime.py — check_state_pair call + record_keyframe_emitted wiring"
    - "server/client_session.py — record_keyframe_requested in STAB-04 IDR-on-drop path"

key-decisions:
  - "StageTimer coexists with HealthMonitor.record_capture_time — event log and rolling-average deques serve different consumers (dashboard / overlay)"
  - "ClockOffsetEstimator is server-side only in Phase 1 — client smoke harness (Phase 4) will own the client-side exchange"
  - "Structlog logger fetched per-call in check_state_pair (not cached at module scope) so test stderr-swap + re-configure picks up fresh factory"
  - "fsm.state_disagreement event name reused from Plan 01-08 warning rather than new health.state_disagreement — keeps wire/grep consistent"

patterns-established:
  - "StageTimer('stage-name') context manager — canonical measurement for OBS-02 per-stage events"
  - "Separation of event-log emit (structlog) from aggregate metric (HealthMonitor deque) — same measurement, two consumers"

requirements-completed: [OBS-02, OBS-03]

# Metrics
duration: 47min
completed: 2026-04-19
---

# Phase 01 Plan 14: Latency Breakdown + Keyframe Telemetry + Clock-Offset Helper Summary

**Per-stage latency events via StageTimer in StreamLoop, HealthMonitor extended with transmit/decode/display deques + keyframe request/emit counters, structured-ERROR fsm.state_disagreement event, and a ~40-LOC EWMA ClockOffsetEstimator ready for Phase 4 cross-tier latency attribution.**

## Performance

- **Duration:** ~47 min
- **Started:** 2026-04-19T01:49Z (worktree reset)
- **Completed:** 2026-04-19T02:36Z
- **Tasks:** 2 (both TDD with RED + GREEN phases)
- **Files created:** 3 (clock_offset.py, 2 test modules)
- **Files modified:** 6

## Accomplishments

- HealthMonitor now tracks 6 pipeline stages (capture, encode, input, transmit, decode, display) via uniform deque pattern
- HealthStats wire contract extended with 5 new fields (transmit/decode/display timing + keyframe request/emit) — 0-valued defaults preserve backward compat with existing round-trip tests
- common/clock_offset.py delivers the RESEARCH Open Q #5 locked decision: ~40-LOC EWMA helper, re-estimate every 60 s
- StreamLoop.run_h264 emits `stage.timing` structlog events for capture + encode stages on every iteration (canonical measurement substrate per PATTERNS)
- `fsm.state_disagreement` upgraded from stdlib warning (Plan 01-08 data path) to structured structlog ERROR event with client_state/server_state/client_id fields — dashboard-filterable on level=error
- STAB-04 IDR-on-drop path now tracks a request/emit ratio: ClientSession.enqueue bumps keyframe_requested; SessionRuntime._on_encoded_frame bumps keyframe_emitted
- 22 new tests green (15 unit + 7 integration); 50 regression gate tests still green (server bootstrap + pipelines + session FSM)

## Task Commits

Each task was TDD (RED + GREEN):

1. **Task 1 RED: HealthMonitor + HealthStats + ClockOffsetEstimator tests** — `da18b4e` (test)
2. **Task 1 GREEN: HealthMonitor + HealthStats + ClockOffsetEstimator impl** — `65cb437` (feat)
3. **Task 2 RED: integration tests for stage.timing + state-disagreement** — `d9d8703` (test)
4. **Task 2 GREEN: StageTimer wiring + check_state_pair + keyframe counter wiring** — `5925a2a` (feat)

**Supplementary:** `5f9480c` (docs) — deferred-items.md logging pre-existing pam_auth failures (out of scope).

## Files Created/Modified

### Created
- `common/clock_offset.py` — ClockOffsetEstimator with `submit_exchange(T1, T2, T3)`, `current_offset_ms`, `should_reestimate()` using symmetric one-way math and EWMA damping
- `tests/server/test_health_monitor.py` — 15 unit tests covering the 6 deques, 2 counters, wire contract, and EWMA convergence + re-estimate interval
- `tests/integration/test_latency_breakdown.py` — 7 integration tests covering StageTimer emission, HealthStats wire fields, state-pair ERROR emit, and StreamLoop.run_h264 stage.timing surfacing

### Modified
- `server/health.py` — 3 new deques (`_transmit_times`, `_decode_times`, `_display_times`), 2 new counters (`keyframe_requested`, `keyframe_emitted`), 5 new recorder methods, extended `get_stats()` to populate all new fields
- `common/messages.py` — `HealthStats` dataclass +5 new fields (`transmit_time_ms`, `decode_time_ms`, `display_time_ms`, `keyframe_requested`, `keyframe_emitted`) with 0-valued defaults
- `server/stream_loop.py` — `from common.logging import StageTimer`; `run_h264` wraps capture + encode calls in `with StageTimer(...)` blocks (existing `record_capture_time` call preserved)
- `server/health_loop.py` — new `check_state_pair(client_state, server_state, client_id)` function emits structlog ERROR 'fsm.state_disagreement' via per-call `get_logger()`
- `server/session_runtime.py` — `handle_input` HEALTH_PING branch delegates to `check_state_pair`; `_on_encoded_frame` bumps `keyframe_emitted` on `is_keyframe=True`
- `server/client_session.py` — STAB-04 IDR-on-drop block bumps `runtime.health.record_keyframe_requested()` alongside `runtime.encoder.request_keyframe()`

## Decisions Made

1. **StageTimer + HealthMonitor deques coexist** — the structlog `stage.timing` event serves the dashboard/event-log consumer while `HealthStats.capture_time_ms` serves the client overlay's rolling-average display. Same measurement, two consumers.
2. **Per-call structlog fetch in check_state_pair** — a module-level cached logger breaks tests that swap `sys.stderr` + re-configure structlog because `PrintLoggerFactory` binds the stream at factory time. Per-call `get_logger()` is a negligible overhead and honest about the reconfigure contract.
3. **Event name kept as fsm.state_disagreement** — Plan 01-08 already established this event name; Plan 01-14's change is level (warning → error) and structure (kwargs not fmt-string), not name.
4. **Clock offset is server-side plumbing only in Phase 1** — the protocol surface doesn't need new fields yet; `HealthPing.timestamp_ms` + `HealthPong.ping_timestamp_ms` + `server_timestamp_ms` already carry enough data. Phase 4's smoke harness will consume `current_offset_ms` when the client-side exchange lands.

## Deviations from Plan

None material. Plan executed as written. Three small clarifications worth logging:

1. **test_stagetimer_emits_stage_timing_event reshape** — the PLAN.md example sketch used `capfd`; the actual test uses an inline stderr-swap (matching `tests/common/test_logging.py::_capture_one_event` pattern) because `capfd` doesn't intercept `PrintLoggerFactory(file=sys.stderr)` output reliably across Python versions. Functionally equivalent — the test still verifies the `stage.timing` event emits with `stage` + `latency_ms` fields.
2. **check_state_pair location** — the PLAN.md considered placing the check at the message-handler site inline; I exposed it as a top-level function in `server/health_loop.py` so the integration test can exercise it directly without a full SessionRuntime. The handle_input path delegates to the same function. Cleaner separation.
3. **deferred-items.md created** — SCOPE BOUNDARY / Rule 3 doesn't touch pre-existing `tests/server/test_pam_auth.py` failures (unrelated to OBS-02/OBS-03). Logged per executor protocol.

---

**Total deviations:** 0 auto-fixed (clean execution)
**Impact on plan:** None — plan acceptance criteria all met.

## Issues Encountered

- **Clock-offset timestamp semantics check** — confirmed the math against PLAN.md: given T1 (ping_sent), T2 (server_received), T3 (pong_received), `offset = T2 - T1 - (T3-T1)/2`. Test `test_clock_offset_server_ahead_by_50ms` locks this in with a known-answer case.
- **structlog factory cache** — initial `check_state_pair` used a module-level `_state_log`. Test captured stdout (console-renderer output) instead of JSON because the cached factory bound to the original stderr. Fix: fetch logger per-call. Now all tests green.

## Known Stubs

None. All introduced code is wired end-to-end. `ClockOffsetEstimator` is deliberately queued for Phase 4 consumer (smoke harness); the CONTEXT documents this as intentional scope bounding, not a stub.

## Threat Flags

None. Plan 01-14 adds observability only; no new attack surface (per the plan's `<threat_model>`).

## Self-Check: PASSED

Verified:
- `common/clock_offset.py` exists — FOUND
- `tests/server/test_health_monitor.py` exists (15 tests pass) — FOUND
- `tests/integration/test_latency_breakdown.py` exists (7 tests pass) — FOUND
- `grep -q "_transmit_times" server/health.py` — FOUND
- `grep -q "record_keyframe_requested" server/client_session.py` — FOUND
- `grep -q "record_keyframe_emitted" server/session_runtime.py` — FOUND
- `grep -q 'StageTimer("capture"' server/stream_loop.py` — FOUND
- `grep -q "transmit_time_ms" common/messages.py` — FOUND
- `grep -q "keyframe_emitted" common/messages.py` — FOUND
- Commit `da18b4e` — FOUND in git log
- Commit `65cb437` — FOUND in git log
- Commit `d9d8703` — FOUND in git log
- Commit `5925a2a` — FOUND in git log
- Commit `5f9480c` — FOUND in git log
- Regression gate: `tests/integration/test_server_bootstrap.py` + `tests/server/test_pipelines.py` + `tests/common/test_session_fsm.py` — 50 passed, 0 failed

## Next Phase Readiness

- **Plan 01-15 (ConnectionSupervisor smoke)** can now exercise the `fsm.state_disagreement` ERROR path — the event surface is stable and the handler delegates through `check_state_pair`.
- **Plan 01-17 (OBS-05 observability ratchet)** has the measurement substrate in place: `stage.timing` events emit on every capture/encode iteration and the HealthMonitor keyframe counters are populated from real IDR paths.
- **Phase 4 (network / smoke harness)** can wire `ClockOffsetEstimator.submit_exchange` into the client's HealthPing/HealthPong loop and subtract `current_offset_ms` from cross-tier latency metrics without further server-side surgery.
- Pre-existing `tests/server/test_pam_auth.py` failures logged to `deferred-items.md` for a future plan.

---
*Phase: 01-stability-ci-test-baseline*
*Plan: 14*
*Completed: 2026-04-19*
