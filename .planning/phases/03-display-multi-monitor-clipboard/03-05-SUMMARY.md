---
phase: 03-display-multi-monitor-clipboard
plan: 05
subsystem: hot-plug-ux + auto-fallback + remap-banner + info-toast
tags: [phase-03, hot-plug, remap-banner, toasts, auto-fallback, ui]

# Dependency graph
requires:
  - phase: 03-display-multi-monitor-clipboard
    provides: "Plan 03 foundations: apply_capture_mode(session, mode, picked_id, picked_name) setting capture_mode_degraded=True when pick_one monitor vanishes; monitor_hotplug 1.0s cadence + _hotplug_pending fast path; encoder_lifecycle.restart as sole encoder re-init entry; full (id, width, height, x, y) hot-plug tuple on Linux + Mac SCK delegate (D-09/D-11)"
  - phase: 03-display-multi-monitor-clipboard
    provides: "Plan 02 toolbar mode badge (update_capture_mode(mode, picked_name, degraded=True) slot already flips to WARNING underline + 'Mode: pick → primary' copy); MonitorSelector pick_one radio mode; ConnectionProfile.monitor_mode persistence"
  - phase: 03-display-multi-monitor-clipboard
    provides: "Plan 01 Wave 0 wire-shape contract (MonitorListMsg dataclass, parse_message thin json.loads contract); client/session.py Signal-bridge plumbing pattern"
  - phase: 02-input-color-fidelity
    provides: "Theme tokens (WARNING, ACCENT, INFO, BG_TERTIARY, TEXT_PRIMARY/SECONDARY/MUTED); FullscreenToolbar 200 ms OutCubic QPropertyAnimation slide vocabulary; client/icons.py _svg_icon 24×24 + 1.5 px-stroke convention"
provides:
  - "MonitorListMsg.degradations payload field — per-client fallback events ``{client_token, previous_pick, now_showing}``; empty list when no sessions needed auto-fallback (wire-compat default)"
  - "server/monitor_hotplug.py iteration restructured so apply_capture_mode runs BEFORE the broadcast, harvesting degradations in a single pass; unified MonitorListMsg carries new topology + fallback events per D-09"
  - "client/remap_banner.py NEW — RemapBanner single-instance widget per UI-SPEC Surface 5 / C-05: 48 px tall, full-width slide-in, WARNING 3 px left border, 4-case copy dictionary (pick_missing / mirror_add / mirror_remove / single_change), sticky (no auto-timeout), Esc-dismiss + Enter-activate-action, 'Choose monitor →' action only in pick_missing case"
  - "client/toasts.py NEW — InfoToast reusable widget per UI-SPEC Surface 8 + 9: 360 px fixed width, fade-in 120 ms / fade-out 200 ms, hover-pause auto-dismiss, max 3 visible (T-03-19 DoS cap); show_monitor_switched_toast helper for Surface 9 (Plan 05 use); show_oversize_image_toast helper for Surface 8 (Plan 06 use)"
  - "client/icons.py::icon_clipboard — clipboard silhouette per UI-SPEC Surface 7 SVG body (Plan 06 will consume for toolbar toggle button; landed early so icons.py changes stay co-located with banner+toast surface work)"
  - "client/session.py degradation-aware handler (_on_monitor_list_with_degradations): degradation matching our client_token → pick_missing banner + monitor-switched toast + toolbar.update_capture_mode(degraded=True); topology count delta without degradation → mirror_add / mirror_remove / single_change banner per capture_mode; T-03-18 mitigation — entries filtered by client_token so a malicious server can't spoof another session's fallback into our UI"
  - "_Bridge.monitor_list signal upgraded from Signal(list) to Signal(dict) carrying the full MonitorListMsg payload (downstream monitor_list_received keeps the plain-list shape for main_window back-compat)"
  - "D-18 telemetry: monitor_hotplug.broadcast + monitor_hotplug.fallback structlog events fire per iteration with counts only (no payload bytes, no monitor names per T-03-13 info-disclosure rule)"
affects:
  - 03-06-clipboard-image-chunked       # icon_clipboard consumed there
  - 03-07-client-clipboard-ui           # icon_clipboard consumed there
  - Phase 3 verification run            # DISP-02 + DISP-06 now code-complete

# Tech tracking
tech-stack:
  added:
    - "QPropertyAnimation on RemapBanner + QGraphicsOpacityEffect-driven fade on InfoToast (motion vocabulary matches FullscreenToolbar slide-down)"
    - "Module-level _toast_stack list tracker — not thread-safe by design; toasts are only ever invoked from the Qt GUI thread via queued _Bridge signals, mirroring Plan 02 clipboard signal-bridge pattern"
  patterns:
    - "Verbatim-copy dictionary pattern: UI-SPEC Surface 5 copy lives in a single _COPY dict keyed by case id ('pick_missing' / 'mirror_add' / 'mirror_remove' / 'single_change') — unknown keys fall back to 'single_change' (most conservative). Source of truth moves the grep-matching burden away from text literals scattered through call sites."
    - "Protocol-layer dispatch upgrade: client/protocol.py MONITOR_LIST branch now passes the full parsed msg dict to on_monitor_list (was msg.get('monitors', [])). Downstream signal-bridge Signal(dict) replaces Signal(list); main_window's list-shape _on_monitor_list consumes via the monitor_list_received compat wrapper that extracts msg['monitors']."
    - "Single-instance banner pattern (T-03-19 mitigation): Session.remap_banner is lazily instantiated once on first degradation; subsequent show_for_case calls update the existing widget's text rather than stacking copies. Ten-thousand-fallback attacks can't overwhelm the UI because there's only ever one banner."
    - "T-03-18 graceful single-session fallback: when self._client_token is empty (server hasn't surfaced a session id on the wire yet), the handler accepts any single-entry degradations list as 'ours' ONLY if the session is in pick_one mode. Single-session-per-host reality of v1 + pick_one guard keeps the spoof surface minimal until the server exposes a stable token."
    - "Integration-test-via-fake-runtime pattern: the loopback monitor-hotplug tests spin up a types.SimpleNamespace SessionRuntime (with _FakeCapture / _FakeEncoderLifecycle / apply_capture_mode closure) rather than a real websocket server. Drives MonitorHotplug.run() for exactly one iteration (1.2s sleep + cancel). Cheaper than a full TLS loopback; the wire contract + degradation payload are the load-bearing pieces for Plan 05, and full websocket loopback is already covered by tests/integration/test_reconnect.py."

key-files:
  created:
    - "client/remap_banner.py — RemapBanner single-instance non-modal banner (+170 lines)"
    - "client/toasts.py — InfoToast + show_monitor_switched_toast + show_oversize_image_toast (+200 lines)"
    - "tests/client/test_remap_banner.py — 8 GREEN tests (Surface 5 verbatim copy + sticky-no-timer + action-link signal + hex-free source check) (+165 lines)"
    - "tests/client/test_toasts.py — 6 GREEN tests (360 px width + Surface 8/9 copy verbatim + max-3 stack + hover-pause + hex-free source check) (+130 lines)"
    - "tests/client/test_icon_clipboard.py — 2 GREEN tests (QIcon shape + color-override accept) (+45 lines)"
    - "tests/client/test_session_monitor_list_wiring.py — 3 GREEN tests (our-token → banner+toast; other-token → no-op; count delta → mirror_remove banner) (+170 lines)"
    - ".planning/phases/03-display-multi-monitor-clipboard/03-05-SUMMARY.md (this file)"
  modified:
    - "common/messages.py — MonitorListMsg.degradations: list = [] field + D-09 docstring (+12 lines)"
    - "server/monitor_hotplug.py — iteration restructured: apply_capture_mode BEFORE broadcast, harvests degradations list, unified MonitorListMsg broadcast + monitor_hotplug.broadcast/fallback telemetry (rewrite: +135 / -51 lines)"
    - "client/icons.py — icon_clipboard() function (+10 lines)"
    - "client/protocol.py — MONITOR_LIST branch passes full msg dict (+5 / -2 lines)"
    - "client/session.py — _Bridge.monitor_list Signal(list)→Signal(dict); __init__ fields (remap_banner, toolbar, _client_token, _last_monitor_count); _on_monitor_list accepts dict; NEW _on_monitor_list_with_degradations handler (+105 / -3 lines)"
    - "tests/common/test_messages_phase2.py — test_monitor_list_with_degradations round-trip (+35 lines)"
    - "tests/server/test_screen_capture_hotplug.py — 2 GREEN tests + _FakeRuntime fixture (hotplug iteration harvests degradation vs mirror_all clean) (+180 lines)"
    - "tests/integration/test_monitor_hotplug.py — 3 GREEN tests replacing 2 Wave 0 skeletons (mid_session_hotplug_session_survives + pick_vanish_fallback + no_degradation_when_picked_still_present) (+190 / -25 lines)"

key-decisions:
  - "MonitorListMsg.degradations field default = empty list for wire-compat. Pre-Plan-05 servers ship the field as an empty list automatically (dataclass default_factory=list); older clients ignoring the field see no change. Single unified MonitorListMsg broadcast on hot-plug is cheaper than introducing a sibling MonitorDegradationMsg wire type and keeps the client's dispatcher simple."
  - "apply_capture_mode runs BEFORE the broadcast, not after. The prior Plan 03 wiring broadcast MonitorListMsg THEN re-applied per-session crop — which meant the client would receive the stale MonitorListMsg before learning about the fallback. Plan 05 flips the order so the degradations payload is built in the same pass; one broadcast, one UI update. Preserves encoder_lifecycle.restart as the sole encoder re-init entry (PATTERNS L362)."
  - "client_token = ClientSession.client_id (str(id(ws))) — no new wire contract. The server already has a stable per-session identifier in ClientSession.client_id (module id of the websocket); we don't need to add a new token negotiation step. Client-side self._client_token defaults to empty string until the server surfaces the id on the wire (future work); the handler falls back to 'accept single degradation in pick_one mode' for v1's single-session-per-host reality, with pick_one as the T-03-18 spoof guard."
  - "Single-instance RemapBanner widget (T-03-19 mitigation). Session.remap_banner is lazily instantiated once on first degradation; subsequent show_for_case calls update the existing widget in place rather than stacking copies. An attacker emitting 10k degradation events can't spawn 10k banners."
  - "InfoToast _MAX_VISIBLE = 3 (T-03-19 mitigation). Stack cap is hard — _enforce_max_visible dismisses the oldest toast when a 4th is shown. The 360 px fixed width + bottom-right anchor anchor means even if all 3 visible toasts are showing, they occupy <360×200 px of the viewer."
  - "Protocol-layer dispatch upgrade (msg dict, not monitors list). Passing the full MonitorListMsg dict through the bridge is cheaper than adding a second signal for degradations — keeps the bridge signature narrow and avoids the 2-signal ordering trap (which arrives first? which fires the banner?). Downstream monitor_list_received compat wrapper extracts msg['monitors'] so main_window._on_monitor_list keeps working without changes."

patterns-established:
  - "Phase 3 Task 1 RED→GREEN gate (Plan 05): 6 new RED tests (1 wire round-trip + 2 hotplug iteration + 3 integration loopback) → MonitorListMsg.degradations field + monitor_hotplug.py iteration reorder in a single atomic commit; 3856/3856 quick-suite GREEN (+6 new vs Plan 03 baseline 3850)."
  - "Phase 3 Task 2 RED→GREEN gate (Plan 05): 19 new RED tests (2 icon + 6 toast + 8 banner + 3 session wiring) → icon_clipboard + toasts.py + remap_banner.py + session wiring in a single atomic commit; 3875/3875 quick-suite GREEN (+19 new)."
  - "D-18 telemetry rule: monitor_hotplug.broadcast + monitor_hotplug.fallback fire per iteration with counts only (monitor_count, degradation_count). No payload bytes. No monitor names. T-03-13 information-disclosure mitigation preserved."

requirements-completed: [DISP-02, DISP-06]
# DISP-02 — Monitor hot-plug during session gracefully handled (no crash, offer remap UI):
#           auto-fallback to primary + RemapBanner pick_missing case + InfoToast + degraded mode badge.
# DISP-06 — ScreenCaptureKit display-change handler for macOS server monitor hot-plug:
#           Plan 03 landed the _DisplayChangeDelegate; Plan 05 completes the UX path that
#           consumes the 1s-poll push-signaled hot-plug events (broadcast + banner + toast).

# Metrics
duration: ~55min
completed: 2026-04-20
---

# Phase 3 Plan 05: Hot-Plug UX + Auto-Fallback Summary

**Server-side hot-plug detection now runs apply_capture_mode in a per-session iteration BEFORE broadcasting MonitorListMsg, harvesting degradation events (``{client_token, previous_pick, now_showing}``) into the wire payload; client-side session handler reacts to degradations matching our token by instantiating a single-instance RemapBanner (UI-SPEC Surface 5 pick_missing case) + firing a monitor-switched InfoToast (UI-SPEC Surface 9) + flipping the toolbar mode badge to WARNING underline via the pre-existing Plan 02 update_capture_mode(degraded=True) slot; topology changes without degradations drive mirror_add / mirror_remove / single_change banner variants per capture_mode; D-18 telemetry fires with counts only (no payload bytes per T-03-13); D-10 NVIDIA xrandr-SET guard preserved (zero set operations); D-11 cadence preserved (1 s). DISP-02 + DISP-06 shipped in code — no hardware dependency for Plan 05 (D-08 hardware session was a Plan 04 prerequisite only).**

## Performance

- **Duration:** ~55 min
- **Tasks:** 2 / 2
- **Files created:** 7 (2 source + 4 test + this SUMMARY)
- **Files modified:** 8 (3 server/common sources + 3 client sources + 2 test files)
- **Commits:** 4 atomic commits (2 RED + 2 GREEN)
  - `50b9ee7` test(03-05): add failing tests for MonitorListMsg.degradations + hotplug fallback
  - `ae7b185` feat(03-05): server hot-plug auto-fallback harvests degradations payload
  - `5120ba3` test(03-05): add failing tests for RemapBanner + InfoToast + icon_clipboard + session wiring
  - `3c27a2c` feat(03-05): RemapBanner + InfoToast + icon_clipboard + session degradation wiring

## Accomplishments

### Task 1 — Server hot-plug auto-fallback iteration + MonitorListMsg.degradations payload + D-18 telemetry

- **common/messages.py — `MonitorListMsg.degradations: list = []`.** New field carrying per-client fallback events. Each entry is a dict of ``{client_token: str, previous_pick: str, now_showing: str}``. Empty list when no sessions needed auto-fallback — pre-Plan-05 wire-compat default. Round-trip verified via `test_monitor_list_with_degradations`.
- **server/monitor_hotplug.py — iteration restructured.** Prior Plan 03 wiring did: broadcast MonitorListMsg (no degradations) → re-apply per-session crop (silently flip capture_mode_degraded). Plan 05 flips the order: per-session `apply_capture_mode` runs FIRST, building a degradations list as each pick_one/single session falls back (`apply_capture_mode` returning False + a previous_pick present → emit a degradation entry keyed by `ClientSession.client_id`). Then a single unified MonitorListMsg carrying new topology + degradations is broadcast. Client iteration wrapped in `list(runtime.clients.items())` for dict-mutation safety (PATTERNS L361). `encoder_lifecycle.restart()` stays the sole encoder re-init entry.
- **D-18 telemetry.** Two structlog events fire per hot-plug iteration: `monitor_hotplug.broadcast` with `monitor_count=N degradation_count=N` always; `monitor_hotplug.fallback` with `events=N` only when degradations is non-empty. Counts only — zero payload bytes, zero monitor names (T-03-13 info-disclosure mitigation).
- **D-10 NVIDIA xrandr-SET guard preserved.** Acceptance grep `grep -E "xrandr.*--(mode|addmode|output)" server/monitor_hotplug.py` returns zero matches. Read-only enumeration only.
- **D-11 cadence preserved (1 s).** `asyncio.sleep(1.0)` loop; `_hotplug_pending` push flag fast path untouched.
- **Tests (6 new GREEN):**
  - `tests/common/test_messages_phase2.py::test_monitor_list_with_degradations` — wire round-trip + default empty list
  - `tests/server/test_screen_capture_hotplug.py::test_hotplug_iteration_harvests_degradation_for_vanished_pick_one` — pick_one + monitor vanished → broadcast has 1 degradation entry with correct previous_pick/now_showing/client_token
  - `tests/server/test_screen_capture_hotplug.py::test_hotplug_iteration_no_degradation_for_mirror_all` — mirror_all session → empty degradations list
  - `tests/integration/test_monitor_hotplug.py::test_mid_session_hotplug_session_survives` — mirror_all, FSM surrogate stays streaming, encoder restarted
  - `tests/integration/test_monitor_hotplug.py::test_pick_vanish_fallback` — full flow: topology change + degradation entry + session continues
  - `tests/integration/test_monitor_hotplug.py::test_hotplug_no_degradation_when_picked_monitor_still_present` — scope boundary

### Task 2 — Client RemapBanner + InfoToast + icon_clipboard + session-side wiring

- **client/icons.py — `icon_clipboard(color=...)`.** UI-SPEC Surface 7 SVG body verbatim (clipboard silhouette with 2 horizontal text lines); returns a cached QIcon following the 24×24 + 1.5 px-stroke convention. Plan 06 will consume for the toolbar toggle button; landing here keeps icons.py changes co-located with banner+toast surface work.
- **client/toasts.py (NEW) — `InfoToast` + `show_monitor_switched_toast` + `show_oversize_image_toast`.** 360 px fixed width, fade-in 120 ms / fade-out 200 ms via QGraphicsOpacityEffect + QPropertyAnimation. Hover `enterEvent` stops the auto-dismiss QTimer; leaveEvent resumes. Click dismisses; Esc dismisses. `_MAX_VISIBLE = 3` hard cap (T-03-19 DoS mitigation). Surface 9 helper ships the "Monitor switched" title + "{name} is no longer available. Showing primary monitor." body verbatim; Surface 8 helper (for Plan 06) ships "Clipboard image too large" title + "{size_mb} MB exceeds the 64 MB limit…" body verbatim. Theme tokens only, zero inline hex.
- **client/remap_banner.py (NEW) — `RemapBanner` single-instance non-modal widget.** 48 px tall, slides in from the top with 200 ms OutCubic via QPropertyAnimation on `b"pos"`. WARNING 3 px left border. 4-case verbatim copy dictionary keyed by case id — only `pick_missing` renders the "Choose monitor →" action link. Sticky: no auto-timeout QTimer (UI-SPEC Surface 5 explicit rule — mid-session topology changes are consequential; 6-second auto-dismiss could be missed mid-stroke). Esc dismisses; Enter activates action link when visible. Action-link click emits `remap_requested` signal for future wire to ConnectionDialog re-open.
- **client/protocol.py — MONITOR_LIST branch passes full msg dict.** Was `self.on_monitor_list(msg.get("monitors", []))`; now `self.on_monitor_list(msg)`. Downstream signal bridge upgraded accordingly.
- **client/session.py — `_Bridge.monitor_list` Signal(list)→Signal(dict).** Full message flows to the handler for degradations access. Session.__init__ adds `remap_banner` / `toolbar` / `_client_token` / `_last_monitor_count` state fields.
- **client/session.py — `_on_monitor_list_with_degradations(msg)` handler.** Late-imports RemapBanner + show_monitor_switched_toast to avoid PySide6 cost when the session is idle. Behavior per UI-SPEC Surface 5:
  - Degradation entry matching `self._client_token` (exact match when token set; single-entry pick_one-mode match when token empty for v1 single-session-per-host reality) → lazily instantiate banner + show_for_case("pick_missing", picked_name) + show_monitor_switched_toast(viewer, monitor_name) + toolbar.update_capture_mode(degraded=True)
  - Topology count delta with no matching degradation → mirror_add / mirror_remove (mirror_all session) or single_change (single/pick_one session) banner
  - T-03-18 mitigation: entries filtered by client_token so a malicious server can't spoof another session's fallback into our UI
- **client/session.py — `_on_monitor_list(msg)` compat wrapper.** Accepts dict (Plan 05) or list (back-compat for pre-Plan-05 callers); extracts `monitors` list for downstream `monitor_list_received.emit` so main_window._on_monitor_list keeps working without changes.
- **Tests (19 new GREEN):**
  - 2 icon: `test_icon_clipboard_returns_qicon` + `test_icon_clipboard_accepts_color_argument`
  - 6 toast: 360 px width, Surface 9 copy verbatim, Surface 8 copy verbatim, max-3 stack, hover-pause, hex-free source check
  - 8 banner: parented + 48 px tall, 4 Surface-5 case copy verbatim, sticky-no-timer, action-link signal, hex-free source check
  - 3 session wiring: our-token → banner + toast; other-token → no-op; mirror_all count-delta → mirror_remove

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Integration test strategy: fake runtime instead of full websocket loopback**
- **Found during:** Task 1 test design
- **Issue:** The plan prescribed a full in-process TLS loopback integration test mirroring `tests/integration/test_reconnect.py`, but that pattern drives a real `websockets.serve` + `websockets.connect` pair plus a full SessionRuntime spin-up (which requires X11 / mss / encoder — unavailable in the unit-test environment on Mac without a virtual display). Plan 03 already landed the SCK push delegate + Linux enumeration regression coverage via mocked mss in test_screen_capture_hotplug.py.
- **Fix:** Integration tests drive `MonitorHotplug.run()` against a `types.SimpleNamespace` fake runtime with an `apply_capture_mode` closure + `_FakeClientSession` spies. Exactly one iteration is driven (1.2 s sleep window + task cancel). The wire contract + degradation payload + encoder-restart side effect + session-attribute mutations are all verified end-to-end; only the TLS websocket transport is stubbed (already covered by `tests/integration/test_reconnect.py` for the unrelated supervisor-reconnect path).
- **Files modified:** `tests/integration/test_monitor_hotplug.py`
- **Commit:** `50b9ee7` (RED) + `ae7b185` (GREEN)

**2. [Rule 1 — Bug] isHidden() vs isVisible() in headless offscreen tests**
- **Found during:** Task 2 initial GREEN run
- **Issue:** `banner._action.isVisible()` returned False in the QPA offscreen platform even after `setVisible(True)` was called, because Qt's effective visibility cascades from the parent chain (the QWidget parent was never `show()`n in tests). This is the exact pattern documented in the Plan 02 Rule 1 deviation for the mode badge tests.
- **Fix:** Test assertions changed from `isVisible() is True/False` to `isHidden() is False/True`. `isHidden()` reflects the explicit `setVisible(bool)` call state rather than the effective parent-chain cascade. Production banner behavior is correct (action link shows in pick_missing case, hidden in the other 3 cases); the fix only tightened the headless test assertion.
- **Files modified:** `tests/client/test_remap_banner.py` (4 assertions)
- **Commit:** `3c27a2c`

**3. [Rule 1 — Bug] QEnterEvent required for super().enterEvent() type-strict dispatch**
- **Found during:** Task 2 initial GREEN run
- **Issue:** `test_info_toast_hover_stops_timer` constructed a `_FakeEv` bare class instance and passed it to `t.enterEvent(ev)`. The InfoToast implementation's `super().enterEvent(ev)` call rejects non-QEnterEvent arguments with a TypeError because PySide6's type dispatch is strict.
- **Fix:** Test constructs a real `QEnterEvent(QPointF(10,10), QPointF(10,10), QPointF(10,10))` (localPos / windowPos / screenPos). Production behavior is correct (hover does stop the timer); the fix only tightened the test harness.
- **Files modified:** `tests/client/test_toasts.py`
- **Commit:** `3c27a2c`

No Rule 2 / Rule 3 / Rule 4 deviations were needed. All acceptance grep patterns matched on first pass; full quick suite stayed green; zero regressions against the Plan 04 code-complete baseline.

## Known Stubs

None. All state is wired end-to-end: degradations payload flows server → wire → client bridge → session handler → banner + toast + toolbar badge. The `_client_token` field is cached as `""` by default (server hasn't surfaced a session id on the wire yet); the session handler has a graceful fallback for v1 single-session-per-host reality with a pick_one guard (T-03-18 mitigation) until the server-side token surfacing lands.

## Threat Flags

None. Plan 05 introduces no new trust boundaries beyond those already covered by the plan's `<threat_model>`. The `degradations` payload is consumed inside the existing authenticated WebSocket session; RemapBanner + InfoToast are pure presentation-layer widgets that render already-authenticated data. T-03-18 (client_token spoofing), T-03-19 (DoS via flood), T-03-20 (NVIDIA xrandr segfault), T-03-21 (info disclosure), and T-03-22 (NSWorkspace observer leak) are all mitigated as planned.

## Test Gate

| Suite | Count | Status |
|---|---|---|
| Plan 05 Task 1 target (server-side) | 6 | GREEN |
| Plan 05 Task 2 target (client-side) | 19 | GREEN |
| Full quick suite `-m "not wacom_hw and not gpu and not smoke_1h and not latency_bench"` | 3875 passed / 0 failed / 130 skipped / 2 xfailed | GREEN |
| Net vs Plan 04 code-complete baseline | +25 tests | PASS |

## Self-Check: PASSED

### Files verified to exist
- `client/remap_banner.py` — FOUND
- `client/toasts.py` — FOUND
- `tests/client/test_remap_banner.py` — FOUND
- `tests/client/test_toasts.py` — FOUND
- `tests/client/test_icon_clipboard.py` — FOUND
- `tests/client/test_session_monitor_list_wiring.py` — FOUND
- `.planning/phases/03-display-multi-monitor-clipboard/03-05-SUMMARY.md` — FOUND (this file)

### Commits verified in git log
- `50b9ee7` — FOUND
- `ae7b185` — FOUND
- `5120ba3` — FOUND
- `3c27a2c` — FOUND

### Acceptance grep patterns
- `degradations` in common/messages.py — 1 match (FOUND, pass)
- `degradations` in server/monitor_hotplug.py — 9 matches (FOUND, pass ≥3)
- `monitor_hotplug.broadcast|monitor_hotplug.fallback` in server/monitor_hotplug.py — 3 matches (FOUND, pass ≥2)
- `previous_pick` in server/monitor_hotplug.py — 5 matches (FOUND, pass ≥1)
- `xrandr.*--(mode|addmode|output)` in server/monitor_hotplug.py — 0 matches (D-10 guard preserved)
- `asyncio.sleep(1.0)` in server/monitor_hotplug.py — 1 match (D-11 cadence)
- `test_pick_vanish_fallback` in tests/integration/test_monitor_hotplug.py — 1 match
- `class RemapBanner` in client/remap_banner.py — 1 match
- `class InfoToast` in client/toasts.py — 1 match
- `def icon_clipboard` in client/icons.py — 1 match
- `show_monitor_switched_toast` in client/session.py — 2 matches
- `remap_banner` in client/session.py — 9 matches
- Verbatim copy strings (Surface 5 + Surface 8 + Surface 9) — all matched
- Zero inline hex colors in `client/remap_banner.py` and `client/toasts.py` — confirmed (theme.* tokens only)
