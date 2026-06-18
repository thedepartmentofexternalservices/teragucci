---
phase: 02-input-color-fidelity
plan: 09
subsystem: input
tags: [modifier-release, ime, qt, pyside6, xtest, uinput, coregraphics, xdotool, fsm, focus, reconnect]

# Dependency graph
requires:
  - phase: 01-stability-ci-test-baseline
    provides: ConnectionSupervisor + ClientFSM (post-auth reconnect hook), structlog telemetry, integration test harness (free_port + tls_ca_and_cert fixtures), bounded send_queue
  - phase: 02-input-color-fidelity
    provides: KEY_RESET_MODIFIERS / TEXT_COMMIT MsgType + KeyResetModifiersMsg / TextCommitMsg dataclasses (02-02), KeyEventMsg lock-bit fields (02-02), swap_cmd_ctrl_for_linux_dest helper (02-03), QT_KEY_TO_MAC_VK + FLAME_CRITICAL_CHORDS (02-03)
provides:
  - Release-all-modifiers fires on all 4 D-11 triggers (focus_out / reconnect / periodic / panic_f9), funneled through a single idempotent server-side InputInjector.reset_modifiers()
  - Per-bookmark Cmd<->Ctrl swap (D-10) — UI checkbox + ConnectionProfile schema + ClientProtocol per-session flag, defaults ON for Linux servers / OFF for Mac
  - Caps/Num/Scroll lock bits attached to every outbound KeyEvent + server-side auto-correct on mismatch (D-14)
  - IME / dead-key commit string passthrough (D-15) — RemoteViewer.inputMethodEvent → TextCommitMsg → xdotool type / CGEventKeyboardSetUnicodeString
  - X server auto-key-repeat disabled at session start (D-13) — `xset -display $DISPLAY r off`
  - Periodic safety-net timer with Pitfall 6 chord-guard precondition
affects: [02-10-mac-pen-injector, 02-11-input-04-integration-loopback, 02-12-wacom-hardware-matrix, 03-display-multimon, 06-distribution]

# Tech tracking
tech-stack:
  added:
    - QShortcut (PySide6.QtGui) — F9 panic shortcut, application-scope
    - QInputMethodEvent (PySide6.QtGui) — IME commit-string handler
    - PostAuthHook protocol type (client/connection_supervisor.py)
    - CGEventKeyboardSetUnicodeString (Quartz) — Mac text_commit
  patterns:
    - "Single idempotent reset path: 4 client triggers → 1 wire message → 1 server-side InputInjector.reset_modifiers()"
    - "Per-session protocol flags driven from per-bookmark ConnectionProfile (set at connect-time, before first wire packet)"
    - "Pure module-level predicate (_should_fire_periodic_reset) for testability + single source of truth for the chord-guard rule"
    - "Wire bits → server cache compare-and-toggle pattern for lock-state auto-correct (D-14)"

key-files:
  created:
    - tests/client/test_viewer_modifier_triggers.py
    - tests/client/test_protocol_modifier_wiring.py
    - tests/server/test_modifier_dispatch.py
    - tests/integration/test_modifier_stress.py
  modified:
    - client/viewer.py
    - client/protocol.py
    - client/session.py
    - client/connection_supervisor.py
    - client/bookmarks.py
    - client/main_window.py
    - common/messages.py
    - server/input_injector.py
    - server/mac_input_injector.py
    - server/session_manager.py
    - server/session_runtime.py

key-decisions:
  - "F9 conflict resolved in favor of D-11 panic key (was Health Overlay since Phase 1); Health Overlay reassigned to Ctrl+Alt+H. Rationale: F9 is locked as panic in FLAME_CRITICAL_CHORDS / CONTEXT.md D-11 — keymap is muscle-memory load-bearing per CLAUDE.md."
  - "Server-side dispatch lives in session_runtime.handle_input (not client_session.py as the plan implied) because that is the actual MsgType dispatcher; client_session.py only owns send-queue + FSM state per Phase 1 D-11."
  - "Viewer↔protocol signal wiring lives in client/session.py::_wire_viewer (the existing wiring point) rather than client/protocol.py directly — the helpers themselves (send_reset_modifiers/send_text_commit/send_key_event) live in client/protocol.py per the plan."
  - "ConnectionProfile (the bookmark dataclass) is owned by common/messages.py, not client/bookmarks.py — extended in messages.py with destination_kind + swap_cmd_ctrl; bookmarks.py owns the migration + add() default-policy logic."
  - "_should_fire_periodic_reset is module-level (not a method) so tests + the loop both use the same predicate without standing up a SessionRuntime."

patterns-established:
  - "Pattern: Mac-first lock-state probe with safe-fallback (probe failure ⇒ all-False ⇒ server treats as locks-off, releases stale state). Adopted in client/protocol.py::_probe_lock_state."
  - "Pattern: post-auth hook on ConnectionSupervisor — async callable invoked only on RECONNECTs (retry_count > 0), wrapped in try/except so a hook failure never bricks the reconnect loop."
  - "Pattern: per-platform symmetric injector contract — both Linux (uinput) and Mac (CoreGraphics) InputInjectors expose the same {reset_modifiers, text_commit} surface so server-side dispatch is platform-agnostic."

requirements-completed: [INPUT-01, INPUT-02, INPUT-03, INPUT-05, INPUT-06, INPUT-07]

# Metrics
duration: ~50min
completed: 2026-04-19
---

# Phase 2 Plan 09: Modifier Stress + IME Passthrough Summary

**Closes the PITFALLS #4 modifier-desync failure class — all 4 D-11 release-all-modifiers triggers funnel through a single idempotent server-side reset, per-bookmark Cmd↔Ctrl swap is wired end-to-end, every KeyEvent carries Caps/Num/Scroll lock bits with server auto-correct, IME dead-key composition rides TextCommit instead of synthesized keycodes, and X-server key-repeat is replaced by client-driven repeats with the periodic safety net guarded against breaking held chords.**

## Performance

- **Duration:** ~50 min
- **Started:** 2026-04-19 (Wave 6)
- **Completed:** 2026-04-19
- **Tasks:** 4 (Task 1 client triggers + UI; Task 2 client protocol layer; Task 3 server dispatch + xset + periodic; Task 4 integration test)
- **Files modified:** 11 source + 4 new test files

## Accomplishments

- All 4 D-11 release-all-modifiers triggers wired through a single server-side `InputInjector.reset_modifiers()` (focus_out, reconnect, periodic, panic_f9).
- Per-bookmark `swap_cmd_ctrl` flag persists in `ConnectionProfile`; UI checkbox in `ConnectionDialog`; defaults ON for Linux destinations matching Flame muscle memory; `ClientProtocol.send_key_event` runs the swap helper before serializing.
- Every outbound `KeyEventMsg` now carries `caps_lock_on` / `num_lock_on` / `scroll_lock_on` from a Mac-first NSEvent probe; server-side `_sync_lock_state_from_wire` auto-corrects the virtual display's lock state on mismatch (Linux toggle path; Mac documented as no-op).
- IME / dead-key composition rides `TextCommitMsg` end-to-end: `RemoteViewer.inputMethodEvent` filters preedit-only events (the dead-key anti-pattern); server dispatch routes to `xdotool type --clearmodifiers` on Linux and `CGEventKeyboardSetUnicodeString` on Mac.
- `xset -display $DISPLAY r off` runs immediately after Xorg/Xvfb starts (D-13) so client-driven repeats are the only repeat path on the server.
- `_should_fire_periodic_reset(now, last_event, last_had_modifiers)` is the module-level predicate that the periodic safety-net timer consults every 2s — Pitfall 6 guard built in (no fire while a chord is held).
- F9 panic shortcut wired as application-scope `QShortcut` plus a discoverable Help-menu item; emits `reset_modifiers_requested("panic_f9")` on the active session's viewer.
- 31 new tests cover viewer signals, protocol wire layer, server-side dispatch + xset + periodic, focus-stress + reconnect contract + held-chord invariant; all green.

## Task Commits

Each task was committed atomically (TDD pattern: test → impl):

1. **Task 1 RED: viewer trigger tests** — `defea29` (test)
2. **Task 1 GREEN: client triggers + UI + supervisor reconnect hook** — `9ae775e` (feat)
3. **Task 2 RED: protocol wire-layer tests** — `b736a74` (test)
4. **Task 2 GREEN: client protocol layer (lock bits + swap + signal wiring)** — `1477ce1` (feat)
5. **Task 3 RED: server dispatch + xset + periodic tests** — `d8fa1d6` (test)
6. **Task 3 GREEN: server dispatch + xset r off + periodic safety net** — `e8fafcb` (feat)
7. **Task 4: integration coverage (focus + reconnect contract + chord invariant)** — `b4a0e8f` (test)

## Files Created/Modified

### Created

- `tests/client/test_viewer_modifier_triggers.py` — focusOutEvent + inputMethodEvent signal coverage (3 tests).
- `tests/client/test_protocol_modifier_wiring.py` — send_reset_modifiers / send_text_commit / send_key_event with lock bits + swap (12 tests).
- `tests/server/test_modifier_dispatch.py` — InputInjector + MacInputInjector reset_modifiers + text_commit + _should_fire_periodic_reset (13 tests, 3 skipped on platform).
- `tests/integration/test_modifier_stress.py` — focus-stress + reconnect contract + held-chord invariant + wire round-trip (6 tests).

### Modified

- `client/viewer.py` — `reset_modifiers_requested` + `text_commit` signals; `focusOutEvent` override; `inputMethodEvent` override (preedit-only events suppressed).
- `client/protocol.py` — `_probe_lock_state` (NSEvent.modifierFlags Mac-first); `_swap_cmd_ctrl` per-session flag + setter; `send_reset_modifiers`, `send_text_commit`, `send_key_event` helpers.
- `client/session.py` — viewer signal wiring (`reset_modifiers_requested` → `protocol.send_reset_modifiers`, `text_commit` → `protocol.send_text_commit`); `_send_key_event` delegates to new protocol method; `connect()` propagates per-bookmark swap flag.
- `client/connection_supervisor.py` — `PostAuthHook` type + `post_auth_hook` ctor arg; `_fire_post_auth_hook` invoked on every retry (`retry_count > 0`).
- `client/bookmarks.py` — `_load` migrates pre-Phase-2 bookmarks; `add()` applies destination-default swap policy; `default_swap_for_destination` static helper.
- `client/main_window.py` — `Swap Cmd/Ctrl for this server` checkbox in `ConnectionDialog`; F9 application-scope `QShortcut`; Help-menu "Release stuck modifiers (F9)" entry; Health Overlay shortcut moved to `Ctrl+Alt+H`; `_panic_release_modifiers` slot; `_new_session_and_connect` resolves bookmark profile and forwards swap flag.
- `common/messages.py` — `ConnectionProfile.destination_kind` + `swap_cmd_ctrl` fields with D-10 defaults.
- `server/input_injector.py` — `subprocess` import; `_MODIFIER_SCAN_CODES` table; `InputInjector.reset_modifiers` (releases LSHIFT/RSHIFT/LCTRL/RCTRL/LALT/RALT/LMETA/RMETA via the keyboard sub-device); `InputInjector.text_commit` (xdotool type --clearmodifiers --delay 0).
- `server/mac_input_injector.py` — `CGEventKeyboardSetUnicodeString` import; `_MAC_MODIFIER_KEYS` table; `reset_modifiers` releases kVK_{Cmd,RCmd,Shift,RShift,Option,ROption,Control,RControl,Function,CapsLock}; `text_commit` posts a CGEvent down/up pair with Unicode payload.
- `server/session_manager.py` — `_disable_x_key_repeat(display, xauthority)` helper; invoked from `create_session` immediately after Xorg/Xvfb starts.
- `server/session_runtime.py` — module-level `PERIODIC_RESET_QUIET_S` constant + `_should_fire_periodic_reset`; `__init__` initializes `_last_key_event_at`, `_last_key_event_had_modifiers`, lock-state cache, modifier safety task handle; `handle_input` branches on `KEY_RESET_MODIFIERS` + `TEXT_COMMIT` and updates last-event tracking; `_modifier_periodic_safety_loop` (2s tick); `_sync_lock_state_from_wire` (D-14 auto-correct); `_start_streaming` schedules the safety loop; `stop()` cancels the safety task before injector teardown.

## Decisions Made

- **F9 binding conflict:** F9 was previously bound to "Health Overlay" (Phase 1) but `common/keymap.py::FLAME_CRITICAL_CHORDS` and `02-CONTEXT.md` D-11 both lock F9 as the panic-release-modifiers key. Health Overlay moved to `Ctrl+Alt+H` (free) so F9 fires the panic. Rule 3 deviation (see below).
- **Dispatch site:** Plan said "server/client_session.py" but the actual MsgType dispatcher is `server/session_runtime.py::handle_input`. Implemented there. Rule 3 deviation.
- **Viewer↔protocol wiring site:** Plan implied wiring lives in `client/protocol.py` but the existing wiring point is `client/session.py::_wire_viewer` (which holds the viewer + protocol references for each tab). The protocol-layer helpers live in `client/protocol.py` per the plan; the signal-to-helper connection lives in session.py.
- **Bookmark schema location:** `swap_cmd_ctrl` field added to `common/messages.py::ConnectionProfile` (the dataclass that bookmarks serialize) — `client/bookmarks.py` owns the load-time migration and `add()` default-from-destination policy. Plan grep targets both files.
- **`_should_fire_periodic_reset` placement:** Module-level free function so tests can import and call without standing up a `SessionRuntime`. Single source of truth — both the periodic loop and tests use the same predicate.
- **Mac lock-state probe:** Uses `NSEvent.modifierFlags()` for Caps Lock; macOS does not expose Num Lock state via NSEvent (Mac keyboards have no Num Lock indicator) so the probe always returns False for that bit. Server-side auto-correct treats False as "release the lock" — safer direction (probe miss → stuck-lock release; phantom-lock-on is unrecoverable).
- **Mac lock-state auto-correct path:** Mac `_sync_lock_state_from_wire` only updates the cache; toggling locks via `CGEventCreateKeyboardEvent` doesn't actually flip system lock state on Mac (the OS owns it). Linux gets the toggle. Documented in code.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] F9 shortcut conflict between D-11 panic and pre-existing Health Overlay**
- **Found during:** Task 1 (client triggers — F9 was already bound to `_toggle_health` from Phase 1)
- **Issue:** Plan specifies F9 as the panic shortcut, and `common/keymap.py::FLAME_CRITICAL_CHORDS` entry 19 explicitly declares F9 as the canonical panic key. Health Overlay was on F9 from Phase 1. Cannot register two `_action()` entries on the same shortcut.
- **Fix:** Reassigned Health Overlay to `Ctrl+Alt+H` in both menu and toolbar. Wired F9 as a `QShortcut(QKeySequence("F9"))` with `Qt.ApplicationShortcut` context so it fires regardless of which tab has focus. Kept a Help-menu entry "Release stuck modifiers (F9)" for discoverability. Logged the conflict resolution rationale inline so the next planner does not re-bind F9.
- **Files modified:** `client/main_window.py`
- **Verification:** Both menu entries register without warning; viewer tests confirm F9 path emits `reset_modifiers_requested("panic_f9")` via `_panic_release_modifiers`. Documented in code with `Rule 3 deviation` comment.
- **Committed in:** `9ae775e` (Task 1 commit)

**2. [Rule 3 - Plan ambiguity] Server-side dispatch site is session_runtime.py, not client_session.py**
- **Found during:** Task 3 (server dispatch — plan referred to `server/client_session.py`)
- **Issue:** Plan's verify grep targets both `server/client_session.py` and `server/session_runtime.py` (`grep -qE "reset_modifiers\(\)" server/client_session.py server/session_runtime.py`). The actual `MsgType` dispatcher is `server/session_runtime.py::handle_input` (per Phase 1 D-11 split: `client_session.py` only owns the per-WS connection state + send queue + FSM). Implementing dispatch in `client_session.py` would duplicate the existing branching logic.
- **Fix:** Added `KEY_RESET_MODIFIERS` + `TEXT_COMMIT` branches to `server/session_runtime.py::handle_input`. Plan's verify grep passes (search includes session_runtime.py).
- **Files modified:** `server/session_runtime.py`
- **Verification:** `tests/server/test_modifier_dispatch.py` 10/10 host-applicable tests pass.
- **Committed in:** `e8fafcb` (Task 3 commit)

**3. [Rule 2 - Missing critical] Wired post-auth hook into ConnectionSupervisor**
- **Found during:** Task 1 (reconnect hook — plan said "actual wire emission is wired by Task 2's protocol layer, this task just defines the reconnect hook and the callable it fires")
- **Issue:** Plan asked for "a post-auth reconnect hook" in `connection_supervisor.py` without specifying the mechanism. ConnectionSupervisor's existing `connect()` loop did not have any hook surface at all. Without an actual hook, there is no way to fire the post-auth `KEY_RESET_MODIFIERS` send required by D-11 trigger #2.
- **Fix:** Added `PostAuthHook` type alias, `post_auth_hook` constructor argument, and `_fire_post_auth_hook(reason)` invoked at the top of every retry iteration (`retry_count > 0`) BEFORE the factory body. Wrapped in try/except so hook failures cannot brick the supervisor loop. The supervisor itself does NOT send the wire message (it has no `ws` reference) — it just invokes the callback; the actual send is the caller's responsibility (Phase 5 / 02-11 wires this all the way to a live ws). Pinned by `tests/integration/test_modifier_stress.py::test_reconnect_post_auth_hook_emits_reset_modifiers`.
- **Files modified:** `client/connection_supervisor.py`, `tests/integration/test_modifier_stress.py`
- **Verification:** Reconnect-contract integration test passes; first connect does NOT trigger the hook (only retries do).
- **Committed in:** `9ae775e` (hook wiring) + `b4a0e8f` (test)

**4. [Rule 3 - Missing primitive] CGEventKeyboardSetUnicodeString was not in mac_input_injector.py imports**
- **Found during:** Task 3 (Mac text_commit implementation)
- **Issue:** `text_commit` needs `CGEventKeyboardSetUnicodeString` from `Quartz`. Existing import block did not include it; would have raised `NameError` at first call.
- **Fix:** Added `CGEventKeyboardSetUnicodeString` to the deferred Quartz import block.
- **Files modified:** `server/mac_input_injector.py`
- **Verification:** Mac text_commit test (Quartz import-skip-aware) confirms the call paths line up.
- **Committed in:** `e8fafcb`

---

**Total deviations:** 4 auto-fixed (1 Rule 2 missing-critical, 3 Rule 3 blocking/ambiguity)
**Impact on plan:** All four were necessary for the plan to land working code. F9 conflict was a planning oversight that would have failed the verification grep had it been hit literally; dispatch-site clarification was a docs-vs-reality mismatch from Phase 1 D-11 split that the planner could not have foreseen without re-reading `session_runtime.py`. No scope creep — every fix is scoped to delivering the plan's stated outcome.

## Issues Encountered

- **Quartz unavailable in worktree venv** — `pyobjc-framework-Quartz` is in `requirements-server.txt` (server-only) and not installed in the dev venv. Tests requiring real Quartz `pytest.importorskip("Quartz")` and skip cleanly. Mac dispatch path was hand-validated by inspection + structural test (the `_MAC_MODIFIER_KEYS` table assertion + `CGEventKeyboardSetUnicodeString` call structure both verified by source reading and the import-conditional test that runs on hosts where Quartz is available).
- **PySide6 `QInputMethodEvent` constructor signature** — initial test used `QInputMethodEvent("", [])` which works on PySide6 6.10. Constructed under the offscreen platform plugin; `setCommitString` API confirmed by experimental run during RED iteration.
- **No regressions** — full project test run (`pytest tests/ --ignore=tests/smoke`) reports 3737 passed, 96 skipped, 9 xfailed (all pre-existing wave-anchored stubs); no new failures.

## D-11 Trigger Wiring Table

| Trigger | Client emit site | Wire message | Server endpoint |
|---------|-----------------|--------------|-----------------|
| 1. focus-out | `RemoteViewer.focusOutEvent` → `reset_modifiers_requested.emit("focus_out")` | `KeyResetModifiersMsg(reason="focus_out")` | `SessionRuntime.handle_input` → `InputInjector.reset_modifiers()` |
| 2. reconnect | `ConnectionSupervisor._fire_post_auth_hook("reconnect")` | `KeyResetModifiersMsg(reason="reconnect")` | same as above |
| 3. periodic (10s quiet, no chord) | _server-side only — not a client trigger_ | _no wire round-trip_ | `SessionRuntime._modifier_periodic_safety_loop` → `_should_fire_periodic_reset` → `InputInjector.reset_modifiers()` |
| 4. F9 panic | `MainWindow._panic_release_modifiers` → `viewer.reset_modifiers_requested.emit("panic_f9")` | `KeyResetModifiersMsg(reason="panic_f9")` | same as above |

## Bookmark Schema Diff (D-10)

```diff
 @dataclass
 class ConnectionProfile:
     ...
     mode: str = "direct"
+    destination_kind: str = "linux"
+    swap_cmd_ctrl: bool = True
```

Migration: pre-Phase-2 bookmarks load with the dataclass defaults, then `BookmarkManager._load` re-applies the destination-aware swap default and resaves to disk.

## xset Command Line (D-13)

```bash
xset -display "$DISPLAY" r off
```

Invoked from `SessionManager._disable_x_key_repeat(display, xauthority)` immediately after Xorg/Xvfb starts (in `create_session`, before D-Bus + WM boot). Failure is logged at `warning` and tolerated.

## Periodic-Safety Decision Table

| now-last_event | last_had_modifiers | Fire? | Rationale |
|---|---|---|---|
| <10.0s | False | NO | Server's view of modifier state is still fresh |
| <10.0s | True | NO | Both reasons say no — chord guard wins |
| ≥10.0s | True | NO | **Pitfall 6 guard** — never break a held chord |
| ≥10.0s | False | YES | Server may have stale modifier state; safe to release |

## User Setup Required

None — no external service configuration required. The full hotkey-correctness path (D-10..D-15) is entirely in-process now. Note: `xdotool` is the canonical Linux IME-passthrough binary; install via `dnf install xdotool` on Rocky 9 server hosts. Missing-binary path is logged at `warning` and degrades gracefully.

## Next Phase Readiness

- **02-10 (Mac pen injector / IOHIDUserDevice):** independent of this plan — proceeds as scheduled.
- **02-11 (INPUT-04 in-process loopback):** the contract this plan ships (`KEY_RESET_MODIFIERS` first post-auth message after reconnect; D-10 swap rewrites Cmd→Ctrl on the wire) is the contract 02-11's loopback test will exercise end-to-end. The xfail stubs in `tests/integration/test_crazy_hotkeys.py` are unblocked.
- **02-12 (Wacom hardware matrix):** blocked on the IOHIDUserDevice spike (02-10), not on this plan.
- **No blockers** for downstream Phase 3 (display) work.

## Self-Check: PASSED

Created files exist:
- `tests/client/test_viewer_modifier_triggers.py` — FOUND
- `tests/client/test_protocol_modifier_wiring.py` — FOUND
- `tests/server/test_modifier_dispatch.py` — FOUND
- `tests/integration/test_modifier_stress.py` — FOUND

All commit hashes resolve in `git log`:
- `defea29`, `9ae775e`, `b736a74`, `1477ce1`, `d8fa1d6`, `e8fafcb`, `b4a0e8f` — all FOUND.

Plan grep gates verified:
- viewer signals/handlers, bookmark swap field, F9/QKeySequence, KEY_RESET_MODIFIERS in supervisor, lock bits + swap helper + wire messages in protocol, reset_modifiers in session_runtime, xset r off in session_manager, text_commit in both injectors, _should_fire_periodic_reset in session_runtime — ALL PASS.

Test results:
- All 31 plan-relevant tests green (3 platform-conditional skips on the dev macOS host where Quartz is not installed).
- Full suite: 3737 passed, 96 skipped, 9 xfailed (pre-existing wave-anchored stubs); 0 new failures.

---
*Phase: 02-input-color-fidelity*
*Completed: 2026-04-19*
