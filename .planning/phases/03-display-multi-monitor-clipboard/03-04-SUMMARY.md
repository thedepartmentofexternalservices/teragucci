---
phase: 03-display-multi-monitor-clipboard
plan: 04
subsystem: client cursor math + per-screen DPR + F12 dev overlay
tags: [phase-03, cursor-math, dpr, f12-overlay, dxs-spike, mixed-dpi, code-complete, hardware-pending]

# Dependency graph
requires:
  - phase: 02-input-color-fidelity
    provides: "Pen-proximity bookkeeping (_pen_was_in_proximity + _last_pen_type), focusIn re-synth pattern (D-19 — Plan 04 mirrors for screenChanged / Pitfall 2), centralized _emit_pen_proximity helper for idempotent wire shape"
  - phase: 03-display-multi-monitor-clipboard
    plan: 01
    provides: "Plan 01 promoted MouseMoveMsg / MouseButtonMsg / MouseScrollMsg / KeyEventMsg / PenEventMsg to dataclasses with server_x/server_y: int = -1 sentinel fields; Plan 04 consumes via new protocol.send_* helpers"
  - phase: 03-display-multi-monitor-clipboard
    plan: 02
    provides: "ClientHelloMsg.capture_mode negotiation pushes mode at connect time; set_capture_mode setter in protocol.py established the Phase 3 wire setter pattern that Plan 04 mirrors for cursor-math state"
  - phase: 03-display-multi-monitor-clipboard
    plan: 03
    provides: "Server-side crop rectangle in session_runtime.py means client-computed server_x / server_y lands in the pre-crop coord space server consumers already handle"
provides:
  - "client/viewer.py::_widget_to_remote returns 4-tuple (rx_norm, ry_norm, server_x_px, server_y_px) per D-05"
  - "client/viewer.py::_current_screen_dpr() returns the CURRENT QScreen's DPR via windowHandle().screen() (D-06 / Pitfall 1 fix)"
  - "client/viewer.py::_on_screen_changed slot wired on showEvent; recomputes scaling + re-emits pen_proximity on Pitfall 2 mixed-DPI screen migration"
  - "client/viewer.py mouse/pen/scroll signals carry server_x / server_y on every emission; pen_data dict gains server_x / server_y keys"
  - "client/protocol.py send_mouse_move / send_mouse_button / send_mouse_scroll / send_pen_event helpers build MouseMoveMsg / MouseButtonMsg / MouseScrollMsg / PenEventMsg with D-05 integer fields"
  - "client/coord_debug_overlay.py::CoordDebugOverlay widget (NEW) — UI-SPEC Surface 6 F12 dev overlay gated on TERAGUCHI_DEBUG=1 at handler-install time per Pitfall 8"
  - "docs/release.md D-08 4-corner DXS hardware spike recording template (pre-populated, awaits Randy)"
affects: [03-05-edid-remap-banner, 03-06-clipboard-image-chunked, 03-07-client-clipboard-ui]

# Tech tracking
tech-stack:
  added:
    - "client/coord_debug_overlay.py::CoordDebugOverlay — new dev-only Qt widget (189 lines)"
  patterns:
    - "Handler-install-time gating for dev surfaces: TERAGUCHI_DEBUG=1 check inside RemoteViewer.__init__ produces None when unset, and every caller uses `if self._coord_overlay is not None` — Pitfall 8 mitigation (release builds have zero F12 handler installed)"
    - "Per-screen DPR lookup via self.window().windowHandle().screen().devicePixelRatio() — NEVER QApplication.primaryScreen() (Pitfall 1 anti-regression)"
    - "showEvent is the earliest spot QWindow.screenChanged can be connected (windowHandle is None in __init__); pattern mirrors RESEARCH Example 1"
    - "Signal arity extension pattern: Qt signals can be declared with new-count kwargs but Python slot receivers take defaults so pre-Plan-04 code paths (if any existed) degrade gracefully"
    - "4-tuple unpack at every _widget_to_remote call site — `nx, ny, sx, sy = self._widget_to_remote(...)` ensures every emitter carries both normalized floats AND integer server px"
    - "theme.* token exclusivity on F12 overlay — zero inline hex, all colors via `from client import theme` + QColor(theme.TOKEN) construction per UI-SPEC Surface 6"

key-files:
  created:
    - "client/coord_debug_overlay.py — 189 lines, NEW; UI-SPEC Surface 6 verbatim (D-07)"
    - ".planning/phases/03-display-multi-monitor-clipboard/03-04-SUMMARY.md (this file)"
  modified:
    - "client/viewer.py — +150 / -25 lines (4-tuple rewrite + DPR helpers + screenChanged + F12 branch + coord overlay wiring)"
    - "client/protocol.py — +61 lines (send_mouse_move / send_mouse_button / send_mouse_scroll / send_pen_event + server_x/-1 on send_key_event)"
    - "client/session.py — +16 / -5 lines (signal-slot signatures updated for 4-arg mouse + server_x/y kwargs)"
    - "tests/common/test_cursor_math.py — +108 lines, Wave 0 skips → 3 GREEN assertions"
    - "tests/client/test_viewer_dpr.py — +91 lines, Wave 0 skips → 5 GREEN assertions"
    - "tests/client/test_viewer_screen_changed.py — +80 lines, Wave 0 skips → 4 GREEN assertions"
    - "docs/release.md — +59 lines (D-08 4-corner spike recording template; ROADMAP flip pending hardware session)"
    - ".planning/STATE.md (code-complete + hardware-pending status)"
    - ".planning/ROADMAP.md (Plan 04 checkbox NOT flipped; note added)"

key-decisions:
  - "ROADMAP plan 04 checkbox stays un-flipped `[ ]` even though all code-change tasks are committed — per plan autonomous:false framing, the checkbox flips only after Randy completes the D-08 manual hardware spike at DXS and signs off in docs/release.md. Attempting to fabricate the hardware measurements would ship a half-verified DISP-03/DISP-05 (the exact anti-pattern CLAUDE.md 'Zero tolerance for pressure/hotkey glitches' forbids)."
  - "DISP-03 + DISP-05 are NOT marked complete in REQUIREMENTS.md by this plan. Code ships the surface; the requirements graduate to complete only on D-08 sign-off. Matches the Phase 2 INPUT-12 pattern (DXS Wacom matrix gates the requirement, not the plan merge)."
  - "send_key_event's inline dict got server_x=-1 / server_y=-1 sentinels rather than a KeyEventMsg dataclass construction — Plan 01 promoted KeyEventMsg but the current session/protocol path keeps the KEY_EVENT message as a dict. Wire-shape identical, avoids Plan 04 scope-creep into the Phase 2 D-14 caller graph."
  - "F12 overlay repositioning on resizeEvent refreshes monitor/crop metadata rows via cursor-center heuristic (cx=width/2, cy=height/2) — widget/server-px rows keep last-known values until next mouseMoveEvent. Alternative would be to track last-emitted widget coords explicitly; heuristic is simpler and matches the plan's suggested pattern."
  - "4-tuple return type annotation kept as bare `tuple` rather than `tuple[float, float, int, int]` — matches Phase 2 convention (no tuple-generic type hints on viewer methods) and keeps mypy quiet on the Python 3.10+ floor."

patterns-established:
  - "Dev-surface gating: handler-install-time env-var check + None-guards on every call site (Pitfall 8 pattern extensible to future dev overlays — e.g. network-telemetry, frame-timing)"
  - "Screen-change re-synth pattern: showEvent wires screenChanged → slot that both refreshes scaling cache AND re-emits pen_proximity when bookkeeping flag is True. Symmetric to Phase 2 D-19 focusIn re-synth; future triggers (Wayland display-change, macOS System Preferences color-space change) follow the same shape"
  - "4-arg mouse signal arity (x_norm, y_norm, server_x, server_y) as the new baseline — future input paths (VR headset, touch panel) carry both normalized + physical-pixel from the outset"

requirements-completed: []
# DISP-03 + DISP-05 are NOT marked complete yet. Code ships; hardware
# sign-off (D-08 spike at DXS) gates the requirement. Code-complete
# doesn't mean requirement-complete in Phase 3 — see key-decisions.

# D-08 status
d-08:
  status: pending
  reason: "Manual hardware spike — Randy at DXS office with mixed-DPI client (Retina MBP + external non-Retina monitor) + 2× NVIDIA Xorg server rig. Pen row satisfied with Intuos Pro. Template pre-populated in docs/release.md; orchestrator handed checkpoint back."
  blocks: [DISP-03, DISP-05, "ROADMAP 03-04 checkbox flip"]

# Metrics
duration: ~60m
completed_code: 2026-04-20
completed_full: pending hardware session
---

# Phase 3 Plan 04: Cursor Math + Per-Screen DPR + F12 Overlay Summary

Cursor-pixel math rewritten to emit server physical-pixel integers, per-screen DPR lookup via QWindow (Pitfall 1 fix), screenChanged hook with pen-proximity re-synth (Pitfall 2), F12 dev overlay gated at handler-install time (Pitfall 8), plus the D-08 4-corner DXS hardware spike template pre-populated in docs/release.md.

## One-liner

Physical-pixel cursor math + per-screen DPR awareness with screenChanged re-synth and gated F12 dev overlay — wire model ships; real-hardware gate pending.

## What Was Built

### Cursor math (D-05)

`client/viewer.py::_widget_to_remote` rewritten from returning `(rx_norm, ry_norm)` to returning `(rx_norm, ry_norm, server_x_px, server_y_px)` — a 4-tuple. Legacy normalized floats stay clamped to [0, 1] for Phase 1/2 wire compat; new integer fields live in server physical-pixel space (from MonitorListMsg geometry) and are NOT clamped (the composite past-last-monitor branch can emit outside bounds; server-side `input_injector` enforces the final clamp per T-03-15 defense-in-depth).

All 5 call sites in `client/viewer.py` unpack the 4-tuple:
- `tabletEvent` (line 968): `nx, ny, sx, sy = self._widget_to_remote(...)`
- `mouseMoveEvent` (line 1040), `mousePressEvent` (line 1060), `mouseReleaseEvent` (line 1081), `wheelEvent` (line 1096)

Qt signal arities extended — `mouse_moved` 2→4 args, `mouse_button_changed` 4→6 args, `mouse_scrolled` 4→6 args. Pen dict gains `server_x` / `server_y` keys alongside the legacy `x` / `y` floats.

### Per-screen DPR (D-06, Pitfall 1)

`_current_screen_dpr()` uses `self.window().windowHandle().screen().devicePixelRatio()` — never `QApplication.primaryScreen().devicePixelRatio()` (the Mozilla bz #794038 root-cause failure mode). Falls back to `devicePixelRatioF()` when `windowHandle()` is None (pre-show edge case; offscreen-platform tests).

`_current_dpr` cache refreshed in:
1. `showEvent` — initial show after connect
2. `_on_screen_changed` slot — every QWindow.screenChanged signal emission

### screenChanged hook (D-06, Pitfall 2)

`_connect_screen_changed` wired inside `showEvent` (not `__init__` — windowHandle is None pre-show). Idempotent: disconnects any prior connection before reconnecting so double-show doesn't duplicate-fire.

`_on_screen_changed(screen)` slot:
1. Recomputes `_current_dpr` via `_current_screen_dpr()`
2. Calls `_update_scaling()` to refresh widget↔server-px cache
3. Logs DPR transitions at INFO level
4. **Re-emits `pen_proximity`** via `_emit_pen_proximity(True, pen_type)` when `_pen_was_in_proximity` is True — this is the Pitfall 2 mitigation, symmetric to Phase 2 D-19 focusIn re-synth. The server PenFSM is idempotent on enter_proximity-from-in_proximity so duplicate emissions during rapid Retina↔External 4K drags are safe.
5. Refreshes F12 overlay DPR+screen_name row when visible.

### Wire payload (D-05)

`client/protocol.py` gets 4 new helpers:
```python
send_mouse_move(x, y, server_x=-1, server_y=-1)
send_mouse_button(button, pressed, x, y, server_x=-1, server_y=-1)
send_mouse_scroll(dx, dy, x, y, server_x=-1, server_y=-1)
send_pen_event(data: dict)  # pulls server_x/server_y from data
```
Each builds a proper MouseMoveMsg / MouseButtonMsg / MouseScrollMsg / PenEventMsg dataclass (promoted in Plan 01) and sends via `send_input(json.loads(msg.to_json()))`. `send_key_event` inline dict gains `server_x=-1 / server_y=-1` sentinels so the KeyEvent wire format also carries the fields.

`client/session.py::_send_mouse_*` slots updated to accept and forward `server_x` / `server_y` kwargs (default -1 = backward-compat sentinel).

### F12 dev overlay (D-07, Pitfall 8)

NEW file: `client/coord_debug_overlay.py` (189 lines). UI-SPEC Surface 6 verbatim:

| Row | Content |
|-----|---------|
| Header | `F12 · coord debug · {TERAGUCHI_DEBUG=1}` — ACCENT color |
| 1 | `widget px : x, y` |
| 2 | `server px : x, y` |
| 3 | `DPR       : dpr (screen_name)` |
| 4 | `monitor   : name WxH+x+y` |
| 5 (cond) | `crop rect : WxH+x+y` — only when `_crop` is set |
| 6 | `delta px  : dx, dy` — WARNING color when non-zero |
| Footer | `F12 to hide` — TEXT_MUTED, 10 pt mono |

Styling: 11 pt SF Mono/Monospace; BG_PRIMARY at 85% alpha; BORDER 1 px; 6 px rounded corners; WA_TransparentForMouseEvents (never blocks clicks); bottom-left anchored with 12 px health_display.py inherited margin; zero inline hex (all colors via `theme.*` tokens).

**Pitfall 8 gate:** `RemoteViewer.__init__` checks `os.environ.get("TERAGUCHI_DEBUG") == "1"` — if not set, `_coord_overlay = None` and the F12 branch in `keyPressEvent` (None-guarded) falls through. Release builds have zero F12 handler installed, exactly as the pitfall's "gate handler, not visibility" recommendation requires.

### D-08 spike template

`docs/release.md` pre-populated with an 8-row matrix (3 modes × client screen count × server monitor) × 4 corners = 32 measurement cells. Sign-off boxes for DISP-03 / DISP-05 / re-scope-needed. Awaits Randy.

## Files Changed

**Created:**
- `client/coord_debug_overlay.py` (189 lines)
- `.planning/phases/03-display-multi-monitor-clipboard/03-04-SUMMARY.md` (this file)

**Modified:**
- `client/viewer.py` (+150 / -25 lines)
- `client/protocol.py` (+61 lines)
- `client/session.py` (+16 / -5 lines)
- `tests/common/test_cursor_math.py` (+108 lines — 3 GREEN tests)
- `tests/client/test_viewer_dpr.py` (+91 lines — 5 GREEN tests)
- `tests/client/test_viewer_screen_changed.py` (+80 lines — 4 GREEN tests)
- `docs/release.md` (+59 lines — D-08 template)
- `.planning/STATE.md` (code-complete + hardware-pending note)
- `.planning/ROADMAP.md` (plan 04 note, checkbox NOT flipped)

## Commits

| # | Hash | Type | Message |
|---|------|------|---------|
| 1 | `8e3f18c` | test | add failing tests for cursor math + per-screen DPR + screenChanged |
| 2 | `a377090` | feat | cursor math + per-screen DPR + screenChanged + server_x/y wire |
| 3 | `64610c4` | feat | add F12 coord-debug overlay widget (D-07, UI-SPEC Surface 6) |
| 4 | `78f6be2` | docs | pre-populate D-08 4-corner DXS hardware spike template |

Final metadata commit (this SUMMARY.md + STATE.md + ROADMAP.md) lands after checkpoint.

## Deviations from Plan

**None.** Plan 04 executed exactly as written for the code-change tasks. No Rule 1/2/3 auto-fixes were needed; all acceptance grep patterns matched on first pass; full quick suite stayed green; zero regressions against the Plan 03-03 baseline.

## Authentication Gates

None — no external service calls, no API keys, no secrets.

## Known Stubs

None. Every wire-format field is populated (server_x / server_y carry real client-computed values when the viewer has a monitor_regions list; -1 sentinel only when backward-compat is required). F12 overlay is functional when enabled.

## Test Results

- **Plan 04 target suite:** 12/12 GREEN
  - `tests/common/test_cursor_math.py`: 3 tests (4-tuple shape + simple-scale + DXS 4-corner synthetic D-08 CI analog)
  - `tests/client/test_viewer_dpr.py`: 5 tests (Retina DPR 2.0 + external 4K DPR 1.0 + pre-show fallback + Pitfall 8 no-F12-without-debug + F12-installed-with-debug)
  - `tests/client/test_viewer_screen_changed.py`: 4 tests (slot exists + recomputes scaling + re-emits proximity + no-spurious-emit guard)

- **Full client+common suite:** 3639 passed / 101 skipped / 0 failed
- **Full quick suite:** 3850 passed / 132 skipped / 0 failed / 4 deselected / 2 xfailed (+12 vs Plan 03-03 baseline 3838)

## D-08 Status: PENDING

The DXS 4-corner hardware spike is the gate on DISP-03 + DISP-05. Code complete; manual session awaits.

**What's needed:** Randy at DXS office with:
- Retina MacBook Pro + external non-Retina monitor (mixed-DPI client)
- Intuos Pro (for pen interaction row — no display tablet required)
- 2× NVIDIA Xorg server rig (dxs-flame-XX)

**What happens next:**

1. Randy launches client with `TERAGUCHI_DEBUG=1` so F12 toggles the coord overlay
2. Connects in each of 3 modes (`single`, `mirror_all`, `pick_one`) to the Rocky server
3. Moves viewer between Retina internal (DPR 2.0) and external 4K (DPR 1.0) for each mode
4. Clicks each of the 4 corner pixels of each visible server monitor
5. Reads the `delta px` row from F12 overlay — must be `0, 0` (or within 1 px) for every test click
6. Tests pen interaction across screenChanged with Wacom (Pitfall 2)
7. Populates the matrix in `docs/release.md` under "Phase 3 D-08 4-corner DXS hardware spike"
8. Checks DISP-03 / DISP-05 sign-off boxes

**Failure branch:** If any corner fails, file a regression issue with (a) which corner, (b) which screen, (c) F12 overlay state at failure, (d) measured delta px. Per CONTEXT.md D-08, the planner re-scopes D-05 before later plans build further math on top of the broken foundation.

**Checkbox flip:** ROADMAP.md Plan 04 stays `[ ]` until sign-off. Orchestrator spawns a continuation agent after Randy's "approved" signal; continuation agent commits docs/release.md + flips checkbox + marks DISP-03 + DISP-05 complete in REQUIREMENTS.md.

## Self-Check: PASSED

Verified all claimed files and commits exist on disk / in git history:

- `client/coord_debug_overlay.py` — FOUND
- `.planning/phases/03-display-multi-monitor-clipboard/03-04-SUMMARY.md` — FOUND (this file)
- Commit `8e3f18c` (test RED) — FOUND
- Commit `a377090` (feat viewer + protocol + session) — FOUND
- Commit `64610c4` (feat F12 overlay widget) — FOUND
- Commit `78f6be2` (docs D-08 template) — FOUND
