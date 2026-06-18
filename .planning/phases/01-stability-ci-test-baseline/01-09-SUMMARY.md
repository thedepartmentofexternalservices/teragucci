---
phase: 01-stability-ci-test-baseline
plan: 09
subsystem: testing

tags: [pytest, pytest-asyncio, websockets, tls, cryptography, python-statemachine, mock, characterization, integration]

# Dependency graph
requires:
  - phase: 01-stability-ci-test-baseline
    provides: "Plan 02 TLS CA fixture (tls_ca_and_cert, free_port), Plan 01 FakeEncoder/FakeWebSocket fixtures, Plan 08 HealthPing/Pong direction inversion + ClientProtocol.fsm + ClientSession.fsm wiring"
provides:
  - "tests/integration/test_server_bootstrap.py — D-10 server-side characterization safety net for Plans 10-11"
  - "tests/integration/test_auth_flow.py — STAB-01 multi-module FSM-driven auth flow integration"
  - "tests/integration/test_client_bootstrap.py — D-10 client-side characterization safety net for Plan 12"
  - "tests/server/test_video_encoder_mock.py — D-02 ffmpeg-subprocess mock boundary regression gate"
  - "14 new tests, ~2 s total runtime, zero new production code"
affects: [01-10, 01-11, 01-12, 01-13, 01-14, 01-15, 01-16, 01-17]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "D-02 mock boundary: patch subprocess.Popen at module-level (both subprocess and server.video_encoder.subprocess) so any accidental encoder spawn is caught"
    - "D-03 in-process loopback: real TLS via Plan 02's tls_ca_and_cert, client + server in same Python process"
    - "FSM-event-driven characterization: drive proto.fsm.send(event) in lockstep with observed protocol messages rather than depending on Qt or thread lifecycle"
    - "FakeSessionRuntime: minimal stand-in for server.main.SessionRuntime covering just the attributes handle_client reads before ServerHello emission"

key-files:
  created:
    - "tests/integration/test_server_bootstrap.py"
    - "tests/integration/test_auth_flow.py"
    - "tests/integration/test_client_bootstrap.py"
    - "tests/server/test_video_encoder_mock.py"
  modified: []

key-decisions:
  - "Task 1 uses a FakeSessionRuntime (not a real SessionRuntime) because instantiating SessionRuntime requires Xvfb + a screen-capture backend that CI runners cannot provide. The fake covers every attribute handle_client reads on the happy path."
  - "Task 4 drives ClientFSM events directly rather than calling ClientProtocol.connect(). Reason: connect() spawns a Qt-adjacent thread and runs its own asyncio loop; driving that from a headless test is flaky. Instantiating a real ClientProtocol proves the import + fsm wiring is intact, and the FSM-event driver proves the transition contract Plan 12 must preserve."
  - "Task 3's third test uses a real server.auth.Authenticator (mode='local') over a real TLS loopback to prove the wire-level credential-rejection contract end-to-end. Units of Authenticator are still covered in Plan 04's test_auth.py; this is the integration-layer complement."
  - "Task 2's mock_popen fixture patches BOTH subprocess.Popen and server.video_encoder.subprocess.Popen. The two reference the same attribute, so the second patch is redundant on paper, but it makes the intent explicit and survives a future refactor that rebinds subprocess inside the module."
  - "Task 1's second test (test_server_fsm_reaches_streaming_on_hello) uses an OR-assertion: EITHER runtime.handle_input received CLIENT_HELLO OR the ServerFSM advanced past capability_exchange. Current monolith routes CLIENT_HELLO through handle_input; post-Plan-11 the FSM may advance directly. Both are acceptable characterizations — Plan 11 can tighten."

patterns-established:
  - "D-02 subprocess-boundary mock fixture pattern (mock_popen) — reusable across future Plan 10's EncoderLifecycle tests"
  - "FakeSessionRuntime minimal-surface pattern — can be extended for Plan 11's ClientSession tests"
  - "FSM event × protocol message lockstep driver — used in Tasks 3 and 4, can be used by Plan 12's post-extraction tests to confirm the extracted ConnectionSupervisor preserves the transition sequence"

requirements-completed: [STAB-05, STAB-01]

# Metrics
duration: 52min
completed: 2026-04-19
---

# Phase 01 Plan 09: Monolith Characterization Tests (Server + Client) Summary

**D-10 test-gated decomposition safety net: 14 tests across 4 files pin server+client+encoder+auth behavior before Plans 10-12 extract modules from the monoliths; runs in ~2s, mocks ffmpeg at the subprocess boundary, drives real TLS over loopback.**

## Performance

- **Duration:** 52 min
- **Started:** 2026-04-19T00:31Z
- **Completed:** 2026-04-19T01:23Z
- **Tasks:** 4
- **Files modified:** 4 (all new test files)

## Accomplishments
- **D-10 server-side safety net in place** (Task 1). Plans 10-11's extraction of SessionRuntime / ClientSession / health loop from server/main.py now has a regression gate — if the auth+ServerHello path breaks after extraction, `tests/integration/test_server_bootstrap.py` trips.
- **D-10 client-side safety net in place** (Task 4). Plan 12's extraction of client/app.py + client/main_window.py + client/tab_manager.py + client/session_view.py + client/connection_supervisor.py from client/main.py has a regression gate — if ClientProtocol.fsm drops the transition table or set_insecure_skip_verify disappears, `tests/integration/test_client_bootstrap.py` trips.
- **STAB-01 multi-module integration coverage** (Task 3). VALIDATION.md Wave 0's previously-orphaned `test_auth_flow.py` (checker BLOCKER #3) now has an owning plan. Exercises client/protocol.py × server/auth.py × common/session_fsm.py together over real TLS.
- **D-02 mock boundary formalized** (Task 2). The ffmpeg-subprocess mock pattern used by Plan 01's FakeEncoder fixture is now proven to work against the REAL VideoEncoder class via a dedicated test file — Plan 10's EncoderLifecycle extraction has a direct regression gate.
- **Zero new production code.** All 4 files are tests-only; no changes to server/, client/, broker/, or common/.
- **No test-suite regressions.** Pre-plan baseline: 158 passing + 1 xfailed. Post-plan: 172 passing + 1 xfailed (14 new).
- **Fast.** All 4 new test files combined finish in ~2.0 s — well under the plan's 30 s budget.

## Task Commits

Each task was committed atomically:

1. **Task 2: D-02 VideoEncoder subprocess-mock tests** — `3462fd8` (test)
2. **Task 3: STAB-01 multi-module auth flow integration** — `8a8f16a` (test)
3. **Task 4: D-10 client-side characterization for Plan 12** — `3716306` (test)
4. **Task 1: loopback characterization of server handle_client** — `f37e343` (test)

Execution order deviated from plan-file order (Task 2, 3, 4, 1 instead of 1, 2, 3, 4) to build the smallest-scope tests first and only attempt the monolith-harness test after the simpler patterns were proven.

## Files Created/Modified

- `tests/server/test_video_encoder_mock.py` — D-02 boundary: 5 tests that patch subprocess.Popen and exercise VideoEncoder.start / feed_frame / request_keyframe / stop without invoking real ffmpeg.
- `tests/integration/test_auth_flow.py` — STAB-01 integration: 3 tests covering the full FSM-driven auth handshake (happy path + auth-failure via protocol + auth-failure via real Authenticator.verify).
- `tests/integration/test_client_bootstrap.py` — D-10 client-side: 4 tests driving ClientProtocol + ClientFSM through the handshake events with and without a loopback TLS server.
- `tests/integration/test_server_bootstrap.py` — D-10 server-side: 2 tests that wire a FakeSessionRuntime into server.main, run handle_client through real TLS loopback, and assert ServerHello + MonitorList + per-session ServerFSM advances past bootstrapping.

## Decisions Made

- **Task 1: FakeSessionRuntime, not a real SessionRuntime.** Real SessionRuntime imports ScreenCapture, which requires Xvfb + a display or a macOS screen-capture session. CI macos-14 runners cannot provide that. FakeSessionRuntime covers the 6 attributes handle_client reads on the happy path (`capture.list_monitors()`, `capture.width`, `capture.height`, `encoder.active_backend`, `audio.available`, `add_client`) — enough for a ServerHello to go out the wire. Plan 11's ClientSession extraction will make a narrower test possible.
- **Task 1: monkeypatch ClientSession.__init__ to capture sessions.** The test needs to inspect the per-session ServerFSM after handle_client exits. Rather than expose an internal registry, we wrap ClientSession's __init__ and append self to a captured_sessions list. Deterministic under pytest's test isolation (monkeypatch scope).
- **Task 4: drive FSM events directly, not via ClientProtocol.connect().** connect() starts a Qt-adjacent thread and runs an asyncio loop inside it; driving that from pytest-asyncio is flaky (loops fighting over the policy, teardown races). The test instead instantiates real ClientProtocol (proving imports + fsm wiring are intact) and drives proto.fsm.send(event) in lockstep with observed messages. Zero Qt dependency, deterministic, headless.
- **Task 2: patch BOTH subprocess.Popen paths.** monkeypatch.setattr("subprocess.Popen", ...) and monkeypatch.setattr("server.video_encoder.subprocess.Popen", ...). On paper they reference the same attribute, but the explicit double-patch makes the D-02 boundary intent obvious and survives a future refactor that rebinds subprocess inside the module.

## Deviations from Plan

None critical. Minor plan-shape adjustments during execution:

**1. [Task-order rearrangement — not a deviation rule, just pragmatic]**
- **Found during:** Session start
- **Change:** Executed Tasks 2, 3, 4, 1 instead of 1, 2, 3, 4.
- **Reason:** Task 1 (server bootstrap) required the most infrastructure (FakeSessionRuntime + ClientSession capture). Building Tasks 2-4 first gave a proven pattern for the mock_popen fixture and FSM-event-driven harness before attempting the monolith test.
- **Impact:** None — all 4 tasks completed, all acceptance criteria met.

**2. [Task 1 test 2 assertion loosened — documented in the plan itself]**
- **Found during:** Task 1
- **Issue:** The plan text said "degrades to a smoke test (ClientHello accepted without error), which is still valuable as a characterization." The current server/main.py monolith routes CLIENT_HELLO through `runtime.handle_input(session, msg)` rather than firing a direct `client_hello` transition on the session FSM.
- **Fix:** test_server_fsm_reaches_streaming_on_hello uses an OR-assertion: EITHER `runtime.handle_input` received CLIENT_HELLO (current behavior) OR the ServerFSM advanced past capability_exchange (post-Plan-11 behavior). Both are acceptable characterizations.
- **Files modified:** tests/integration/test_server_bootstrap.py
- **Verification:** test passes on current monolith; after Plan 11 extracts the FSM-driving path, the assertion can be tightened.
- **Committed in:** f37e343

**Total deviations:** 0 auto-fixes (no Rule 1/2/3 triggered — pure test additions).

## Issues Encountered

- **VideoEncoder default codec is h264**, which means `_start_ffmpeg` spawns a reader thread that calls `self._process.stdout.read()`. The mock's `.read.return_value = b""` triggers the loop's `if not chunk: break` branch, exiting cleanly. Verified via `test_encoder_start_invokes_subprocess` — the reader thread does NOT hang.
- **VideoEncoder.__init__ calls detect_encoders() when available_encoders is None.** detect_encoders spawns `subprocess.run("ffmpeg -encoders")`. To keep the D-02 boundary tight and avoid any real subprocess invocation, every VideoEncoder construction in test_video_encoder_mock.py passes an explicit `available_encoders={"h264": [], "h265": [], "av1": []}`, which makes VideoEncoder fall through to its absolute libx264 software fallback path without ever spawning detect_encoders.
- **Server/main.py's `handle_client` returns early if `default_runtime is None` in legacy (non-PAM) mode.** Task 1's FakeSessionRuntime install via `monkeypatch.setattr(srv_main, "default_runtime", fake_runtime)` resolves this — handle_client then emits ServerHello + MonitorList and enters the input-processing loop as designed.

## Threat Flags

None. All 4 tasks add test-only code; no new attack surface introduced.

## Next Phase Readiness

- **Plans 10-11 can proceed safely.** The server-side extraction of SessionRuntime / ClientSession / health-loop from server/main.py is now gated by `tests/integration/test_server_bootstrap.py`. Any extraction that breaks auth+ServerHello will trip the test.
- **Plan 12 can proceed safely.** The client-side extraction of 5 modules from client/main.py is gated by `tests/integration/test_client_bootstrap.py`. Any extraction that removes ClientProtocol.fsm, renames set_insecure_skip_verify, or breaks the happy-path transition table will trip the test.
- **Plan 12 depends_on update note:** per the plan's acceptance criteria, Plan 12's frontmatter should list `depends_on: [07, 08, 09]` so Plan 12 cannot start until Plan 09's characterization tests are green. This SUMMARY records that contract for the planning-phase orchestrator; updating Plan 12's frontmatter itself is out of scope for this worktree (Plan 12 does not exist as a file yet in this worktree; it will be authored later).
- **VALIDATION.md Wave 0 reconciliation:**
  - `tests/integration/test_auth_flow.py` → OWNED BY Plan 09 Task 3 (resolves checker BLOCKER #3 dangling test)
  - `tests/integration/test_client_bootstrap.py` → OWNED BY Plan 09 Task 4 (resolves checker BLOCKER #4 missing D-10 client-side gate)
  - Both files now exist, tests pass, ownership is permanent.

## TDD Gate Compliance

All 4 tasks had `tdd="true"` in the plan frontmatter, but the tasks are pure tests-only (no production code to implement). The TDD cycle collapses to:
- **RED phase (per-task):** write the test file.
- **GREEN phase:** the test immediately passes against the existing monolith (that's the characterization contract — the monolith IS the implementation being pinned).
- **REFACTOR:** N/A — no production refactor is part of Plan 09.

Task commits use `test(...)` type, consistent with RED-gate semantics. There is no accompanying `feat(...)` commit because Plan 09 explicitly forbids production changes — the plan's scope is to pin behavior BEFORE Plans 10-12 mutate it. Plans 10-11-12's GREEN commits will land in their own plan files.

## Self-Check: PASSED

- [x] tests/server/test_video_encoder_mock.py exists (5 tests, commit 3462fd8)
- [x] tests/integration/test_auth_flow.py exists (3 tests, commit 8a8f16a)
- [x] tests/integration/test_client_bootstrap.py exists (4 tests, commit 3716306)
- [x] tests/integration/test_server_bootstrap.py exists (2 tests, commit f37e343)
- [x] Commit 3462fd8 in git log (verified)
- [x] Commit 8a8f16a in git log (verified)
- [x] Commit 3716306 in git log (verified)
- [x] Commit f37e343 in git log (verified)
- [x] `python -m pytest tests/ --timeout=30` exits 0 (172 passed + 1 xfailed, zero regressions)
- [x] 4 new test files combined run in ~2.0 s (well under 30 s budget)
- [x] No modifications to server/, client/, broker/, common/ source files (only tests/ additions)

---
*Phase: 01-stability-ci-test-baseline*
*Plan: 09 — Monolith Characterization Tests (Server + Client)*
*Completed: 2026-04-19*
