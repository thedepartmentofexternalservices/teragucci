# Phase 3: Display + Multi-Monitor + Clipboard — Context

**Gathered:** 2026-04-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver the display + clipboard layer a Flame artist expects from a local workstation, in all three topologies they actually use — single-monitor solo work, mirror-all for client review, pick-one for laptop-on-the-go. Plus pixel-accurate cursor math on mixed-DPI Mac clients (Retina internal + external 4K is the canonical landmine), crash-free monitor hot-plug on both server platforms, and the bidirectional text + image clipboard with per-direction privacy toggles that PCoIP users take for granted.

Phase 3 ships when: (1) the user picks single / mirror-all / pick-one at connect time and the chosen mode behaves correctly across all server display topologies (1, 2, or 3 monitors); (2) hot-plugging a monitor on either side mid-session does not crash server or client and the session continues with a remap UI; (3) cursor coordinates land on the correct pixel on a Retina-MacBook + external-4K client driving a 2×2560×1600 Rocky NVIDIA Xorg server — verified by the pre-phase hardware spike; (4) bidirectional text clipboard handles >1MB pastes + CRLF/LF/CR without corruption AND bidirectional PNG image clipboard moves screenshots either direction; (5) CustomEDID for Xvfb sessions advertises a Flame-approved monitor model so Flame's monitor-config dialog stops complaining.

**In scope:** DISP-01..07 (7 reqs — per-session mode selector, hot-plug survival on Linux + macOS, cursor coord model, CustomEDID, mixed-DPI rendering, per-monitor fullscreen) + CLIP-01..03 (3 reqs — text clipboard hardening, image clipboard, per-direction toggles). 10 requirements total.

**Out of scope:**
- RESIZE_REQUEST path / dynamic server geometry (`server/main.py:548-552` stays stubbed — NVIDIA driver segfault per CONCERNS.md, PITFALLS #5; revisit post-v1 if NVIDIA fixes the bug)
- Full ICC / display-calibration pipeline (excluded from v1 per PROJECT.md)
- HDR tone-mapping on client (same bucket)
- Rocky 10 / Wayland server (blocked on Autodesk Flame Wayland support)
- Window-straddling-two-screens DPR edge case (Qt picks one — accepted limitation; artists who straddle get the "wrong" DPR on the un-picked portion, document)
- Audio / QUIC / file transfer / USB / signing / docs (Phases 4–7)

</domain>

<decisions>
## Implementation Decisions

### Mode Selector UX + Lock-on-Connect (DISP-01 / DISP-07)

- **D-01: Mode picker lives in the connect dialog, full-stop.** Single / mirror-all / pick-one is chosen when the user clicks Connect on a bookmark and is locked for the life of that session. Bookmark stores the last-used mode as the default. Toolbar surfaces the current mode read-only (badge in health overlay area) so artists can see state at a glance. No hidden "advanced" disclosure — the mode is too consequential to bury. Matches the PITFALLS #5 recommendation "Fullscreen mode: explicit screen picker. No guessing."
- **D-02: Server-side crops per mode before encode.** Server capture stays always-full-virtual-desktop (no per-session capture variance → simpler capture codepath, no regressions to the Phase 2 10-bit fixture). For `single` and `pick-one`, the server applies a GPU crop between capture and encode so only the chosen pixels hit the codec (bandwidth savings on WAN). `mirror-all` ships the virtual desktop unchanged. The crop rectangle comes from the selected monitor's geometry in MonitorListMsg; negotiated via a `CAPTURE_MODE` field on an extended `ClientHelloMsg` or a new SESSION_CONFIGURE message (planner decides shape).
- **D-03: No-mid-session-switch is enforced by grayed UI + explicit tooltip** — the mode widget is visibly disabled during an active session with tooltip text "Disconnect and reconnect to change monitor mode." No silent failures, no half-supported code paths. If we ever want mid-session change it becomes a forced-reconnect feature in v1.1.
- **D-04: Pick-one reuses the existing `client/monitor_selector.py` widget constrained to single-check (radio) behavior.** Bookmark stores the last pick by both `id` and `name` (belt + suspenders — `id` survives reorder, `name` survives hotplug). On connect, if the bookmarked monitor still exists by id-or-name, use it; else fall back to primary monitor + emit a toast "Monitor X not found, now viewing primary." Zero new UI code — the widget already supports this shape.

### Cursor-Coord Math + Mixed-DPI (DISP-03 / DISP-05)

- **D-05: Wire carries server physical-pixel integer coords.** End the normalize 0.0-1.0 era for cursor/pen events — it costs precision on 10,000px-wide virtual desktops and is the root of the PITFALLS #5 "cursor lands a bit left of where I click" class. `MouseEventMsg` / `KeyEvent` / `TabletEvent` payloads gain explicit `server_x: int, server_y: int` fields in server physical pixels. Client-side `client/viewer.py::_widget_to_remote` rewrites to produce physical pixels using the current `MonitorListMsg` geometry (not the old `_remote_width`/`_remote_height` floats). Normalized floats may remain for backwards-compat on the wire during the transition, but server consumers prefer the integer fields when present.
- **D-06: Per-screen DPR via `QScreen::devicePixelRatio()` re-evaluated on Qt `screenChanged`.** On every pointer event, look up which `QScreen` the viewer window is currently on (`QWidget::screen()`) and use THAT screen's DPR. Re-evaluate + recompute scaling cache on the `QWindow::screenChanged` signal. Handles the mixed-DPI case (Retina MBP + external non-Retina monitor) correctly per Qt 6 HighDPI docs. Known edge: a viewer window straddling two screens gets Qt's pick of one DPR — documented limitation, not a code-fix target in v1.
- **D-07: Dev-only F12 debug overlay.** `RemoteViewer.keyPressEvent` F12 toggles a transparent overlay showing widget coords, computed server coords, current `QScreen` DPR, active monitor region from MonitorListMsg, and (in pick-one) the crop rect. Gated on a dev env-var (`TERAGUCHI_DEBUG=1`) so the overlay is invisible in release builds. Production users get no UI change. This is our "artist reports cursor off by N pixels" field-debug tool, borrowing the pattern from `client/key_diagnostic.py`.
- **D-08: Pre-phase spike pass bar is the 4-corner click test on real mixed-DPI hardware.** On a Retina MBP + external 4K client driving a 2×2560×1600 Rocky NVIDIA Xorg server: click each of the 4 corner pixels of each server monitor, from both the Retina screen and the external 4K screen, in pick-one / mirror-all / single modes. Expected server click coord = corner coord within 1 px. Any fail blocks DISP-03 / DISP-05 ship. Captured in `docs/release.md` alongside P2 D-16 Wacom matrix. If the 4-corner test reliably passes, the rest of Phase 3 math is defensible.

### Hot-Plug Behavior (DISP-02 / DISP-06)

- **D-09: Degrade-in-place + non-modal remap banner.** When a server-side monitor appears/disappears mid-session: `MonitorListMsg` broadcasts the new topology; `EncoderLifecycle.restart()` reinitializes with the new geometry (path already exists from `server/monitor_hotplug.py`); client shows a non-modal banner ("Monitor removed — click to remap") with a link that opens pick-one picker when applicable. If the currently-picked monitor vanishes, server auto-falls-back to primary monitor + emits a toast "Monitor X gone, now viewing primary." Stream never stalls; session state preserved; Flame never loses focus.
- **D-10: NVIDIA xrandr-segfault guard = keep `RESIZE_REQUEST` stubbed, read-only hot-plug only.** No Phase 3 code calls `xrandr --mode` / `xrandr --addmode` / any set-operation on a running Xorg. Server advertises fixed geometry from CustomEDID/session_manager.py at bootstrap and the hot-plug handler is pure read (mss / xrandr-query / SCK enumeration). The `# TODO: investigate safe resize path for GPU displays` comment at `server/main.py:548-552` stays. Documented as a known v1 limitation in `docs/release.md` and the user-facing "limitations" section in docs. If a studio demands dynamic resolution change, it becomes a v1.1 scoped item with the driver-bug research that entails.
- **D-11: macOS SCK hot-plug signature = full `(displayID, width, height, x, y)` tuple + push-based `SCStreamDelegate` callback.** Today's `server/mac_screen_capture.py::detect_hotplug` only checks count + WxH — misses display reorder, same-size swaps, and repositioning (all of which the D-08 mixed-DPI spike will hit). Upgrade to the full tuple signature. Also subscribe to ScreenCaptureKit's `SCStreamDelegate` display-change callback so the handler fires on-change instead of only on 5s poll (poll stays as safety net). Closes CONCERNS.md §"Platform Parity Gaps" #7.
- **D-12: Hot-plug CI coverage = mocked + in-process loopback.** Unit tests inject fake monitor-list changes into `ScreenCapture.detect_hotplug` + `MacScreenCapture.detect_hotplug` and assert `MonitorListMsg` broadcast fires and `EncoderLifecycle.restart` is called. Integration test via P1 D-03 in-process loopback: simulate monitor-gone mid-session, assert client receives `MonitorListMsg`, viewer re-renders new topology, no exception bubbles, session FSM stays in `streaming`. Real hardware is the pre-phase spike (D-08) + every-release manual ritual (per P2 D-16 pattern). No self-hosted CI runners (preserves P1 D-05 discipline).

### Clipboard Image + Per-Direction Toggle (CLIP-01 / CLIP-02 / CLIP-03)

- **D-13: Image clipboard format = PNG lossless, base64 inside the existing `ClipboardMsg`.** Extend `ClipboardMsg.content_type` to accept `"image/png"` with `data = base64(png_bytes)`. PNG is the universal clipboard format on both NSPasteboard (`NSPasteboardTypePNG`) and X11 (`xclip -t image/png`). Lossless preserves paint references and frame grabs — JPEG lossy-compression is unacceptable for VFX reference. One format, one codepath, no format negotiation.
- **D-14: Size cap = 64 MB (decoded PNG bytes), silent-fail-closed with local toast.** Copies exceeding 64 MB on the originating side are dropped locally with a user-visible toast ("Clipboard image too large — copy it as a file instead"). Never sent on wire. Receiving side independently enforces the same 64 MB cap as defense-in-depth. 64 MB chosen (over initial 16 MB rec) to cover high-res paint references + multi-layer Photoshop screenshots; revisit if it proves bandwidth-heavy on WAN. Matches the PITFALLS "size cap; validate magic bytes" guidance.
- **D-15: Per-direction toggles live per-bookmark with in-session toolbar override.** Bookmark stores four bool flags: `clipboard_text_c2s`, `clipboard_text_s2c`, `clipboard_image_c2s`, `clipboard_image_s2c` — all default ON. A clipboard icon in the existing toolbar (near MonitorSelector) opens a 4-checkbox menu for in-session flip; changes take effect immediately on the next clipboard event. Mirrors the P2 D-10 per-bookmark + in-session pattern (Cmd↔Ctrl swap). Covers the "oh wait, I'm screen-sharing with client — kill c2s paste" mid-session privacy reveal.
- **D-16: Security defaults = all directions ON, magic-byte + size validated, CRLF preserved.** Out-of-box, text and image both flow both directions. Inbound image payloads validate the first 8 bytes = PNG signature `\x89PNG\r\n\x1a\n` before accepting; malformed headers dropped with a structlog warning + nothing injected into the system clipboard. Size cap (D-14) enforced on receive too. Text side preserves the sender's native newline encoding on arrival (CRLF stays CRLF, LF stays LF, CR stays CR) — never silently transform; let each OS's clipboard consumer normalize as it sees fit. Small-studio trust model assumes both ends are trusted; users wanting lockdown flip per-bookmark (D-15).
- **D-17: Large-image transport stays on the control channel, chunked into 1 MB JSON messages with sequence ID.** A 64 MB PNG base64's to ~85 MB of JSON. Rather than stalling the control channel behind one giant message, chunk into 1 MB `ClipboardChunkMsg` frames with `{sequence_id, chunk_index, total_chunks, content_type, data}`. Mouse / pen / key events interleave between chunks so input latency doesn't spike during a paste. Clipboard chunks get their own bounded queue slot distinct from the streaming queue (preserves P1 STAB-07 "bounded pipeline queues" discipline). If measurement later shows head-of-line issues, file_transfer.py side-channel fallback is the next-step, not a v1 requirement.

### Phase 3 Latency Gate (crosscutting)

- **D-18: Phase 3 must not regress the P1/P2 latency gate.** The Phase 1 synthetic p99 < 25 ms CI gate (P1 D-08) and the P2 D-21 DXS real-hardware input-to-photon measurement both stay green. Specific risk zones for this phase:
  - Server-side GPU crop path (D-02) — adds a shader pass; verify encoder-stage latency in `structlog` telemetry stays in budget
  - Wire coord model change (D-05) — adds 8 bytes per input event but removes a normalize/denormalize op; likely neutral-to-positive
  - Clipboard chunking (D-17) — cannot stall input-to-photon since input messages get their own queue slot
  Phase 3 verification step re-runs the P1 1-hour synthetic smoke harness + the D-08 corner-click test; any regression blocks ship.

### Claude's Discretion

The planner has freedom on these — they fall out naturally from the decisions above:

- **CustomEDID Flame-approved monitor profile (DISP-04).** Existing `server/session_manager.py::find_edid_file` already has PCoIP fallback + minimal 1920×1200 TGC generator. Pick a Flame-approved profile name (Eizo CG279X-like or similar industry-standard grading monitor) so Flame's monitor-config dialog accepts it without complaint. Not user-configurable in v1. The generator stays as fallback for users without the shipped binary.
- **Exact `MonitorListMsg` extension shape** for physical-pixel geometry + per-monitor `scale` flow into D-05 client math.
- **Banner UX copy + color for D-09 remap** — any Qt toast/banner pattern that fits with the existing `health_display.py` aesthetic.
- **4-checkbox menu visual design** for D-15 toolbar toggle — any Qt menu/toolbutton pattern that matches the existing monitor_selector.py look.
- **New protocol message names** (`ClipboardChunkMsg`, `SessionConfigureMsg`, any image-clipboard receive-side helpers) — follow existing `common/messages.py` naming convention.
- **F12 overlay visual design** for D-07 — anything terse + monospace; no art direction required.
- **Whether `SCStreamDelegate` subscription lives in `mac_screen_capture.py` or a new thin wrapper** (D-11).
- **Test fixture layout** for hotplug mocks (follow P1 `tests/server/` + `tests/integration/` conventions).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents (researcher, planner, executor) MUST read these before planning or implementing.**

### Project / Phase context

- `.planning/PROJECT.md` — Core Value, 10-bit non-negotiable, Out-of-Scope (full ICC pipeline, HDR tone-mapping, Wayland)
- `.planning/REQUIREMENTS.md` — Phase 3 owns DISP-01..07 (7) and CLIP-01..03 (3)
- `.planning/ROADMAP.md` §"Phase 3: Display + Multi-Monitor + Clipboard" — goal, 5 success criteria, pre-phase spike definition, dependency on Phase 2
- `.planning/STATE.md` — Performance Metrics targets (sub-20ms LAN, 60fps LAN, 30fps WAN fallback)
- `.planning/phases/01-stability-ci-test-baseline/01-CONTEXT.md` — Phase 1 locked decisions Phase 3 builds on: FSM library (`python-statemachine`), mock-at-subprocess-boundary test strategy (D-02), in-process loopback integration tests (D-03), GHA runner topology (D-05/06), 4 CI hard gates including synthetic latency p99<25ms (D-08), bounded pipeline queues (STAB-07)
- `.planning/phases/02-input-color-fidelity/02-CONTEXT.md` — Phase 2 decisions carried forward: per-bookmark + in-session override pattern (D-10 Cmd↔Ctrl swap), structlog per-stage telemetry convention, mock-at-VT-boundary + mock-at-IOKit-boundary test boundaries, `MonitorListMsg` already wired. **Deferred to Phase 3 explicitly:** `server/main.py DISPLAY` env-var thread-safety cleanup if session-manager work lands here (currently in scope only if touched).
- `CLAUDE.md` — project guide; lists `xrandr resize crashes NVIDIA` as Phase-3 known trap; flags the 4 `ssl.CERT_NONE` sites already removed in Phase 1 (do not regress); flags DISP-era as UI phase requiring regression tests

### Research outputs (consult before planning)

- `.planning/research/PITFALLS.md` §"Pitfall 5: Multi-monitor / display topology is the #1 artist complaint" — drives D-05 through D-12; explicit 4-corner test pattern (D-08), per-window DPR warning (D-06), xrandr-segfault NVIDIA advisory (D-10), NSScreen alternative for Mac hot-plug (D-11)
- `.planning/research/PITFALLS.md` §"Pitfall 6: Reconnect doesn't converge state" — "last-write-wins with user confirmation on collision" for clipboard; informs reconnect-clipboard convergence (covered by existing `_last_content` in `ClipboardSync` + `_last_change_count` in `MacClipboardSync`)
- `.planning/research/PITFALLS.md` §"UX Pitfalls" — "cursor lands a bit left of where I click" → DPR math error (D-05/D-06); "cursor jumps to wrong monitor on server" → topology math (D-02/D-04)
- `.planning/research/STACK.md` §"Screen Capture" — ScreenCaptureKit display-change delegate (D-11)
- `.planning/research/STACK.md` §"Input Injection" — cross-platform cursor warping semantics (QCursor::setPos DPR differences)
- `.planning/research/SUMMARY.md` §"Critical Path" — Phase 3 has no new critical-path blockers; builds on Phase 1 + 2 foundation

### Existing-code analysis (codebase post-Phase-2)

- `.planning/codebase/STRUCTURE.md` — directory layout + "Where to Add New Code" recipes
- `.planning/codebase/CONCERNS.md` §"Known Bugs" line 39-41 — `xrandr resize crashes NVIDIA` (drives D-10)
- `.planning/codebase/CONCERNS.md` §"Clipboard: text only" line 240-243 — drives D-13 image support
- `.planning/codebase/CONCERNS.md` §"Mac clipboard polls every 250 ms" line 165-168 — informs D-17 chunk-interleave strategy and reconnect convergence
- `.planning/codebase/CONCERNS.md` §"Platform parity gaps" line 253-255 — `detect_hotplug` divergence (drives D-11)
- `.planning/codebase/CONCERNS.md` §"Test coverage gaps" line 360-364 — `mac_screen_capture.py` / `mac_clipboard.py` currently have zero tests; Phase 3 adds them
- `.planning/codebase/TESTING.md` — Phase 1 test layout (`tests/common/`, `tests/server/`, `tests/integration/`, `tests/smoke/`) — Phase 3 extends

### Existing code Phase 3 will touch (by file)

- `client/monitor_selector.py` — **EXTEND** — single-check mode for pick-one (D-04); stays multi-check for mirror-all sub-selection
- `client/viewer.py` — **REFACTOR** — `_widget_to_remote` → produce server physical pixels (D-05); `screenChanged` hook for per-screen DPR (D-06); F12 dev overlay (D-07); remap banner UX (D-09)
- `client/protocol.py` — **EXTEND** — `send_clipboard` supports `content_type="image/png"` (D-13); chunked clipboard receive path (D-17); per-direction toggle gating (D-15)
- `client/bookmarks.py` — **EXTEND** — four clipboard-toggle fields + `monitor_mode` + `picked_monitor_id`/`picked_monitor_name` (D-01, D-04, D-15)
- `client/main_window.py` / `client/fullscreen_toolbar.py` — **EXTEND** — clipboard-toggle toolbar menu (D-15); mode badge in health overlay area (D-01)
- `client/session.py` — **EXTEND** — pass `capture_mode` + `pick_monitor_id` to server on connect (D-01/D-02); grayed mode-selector policy (D-03)
- `server/screen_capture.py` — **EXTEND** — GPU crop pre-encode for single/pick-one (D-02); full `detect_hotplug` signature (mirror of D-11 approach)
- `server/mac_screen_capture.py` — **EXTEND** — full `(displayID, width, height, x, y)` signature + SCStreamDelegate display-change callback (D-11)
- `server/monitor_hotplug.py` — **EXTEND** — fall-back to primary when picked monitor vanishes (D-09); drive encoder restart via EncoderLifecycle (already wired)
- `server/session_manager.py` — **EXTEND** — Flame-approved CustomEDID profile selection (Claude's discretion, DISP-04)
- `server/clipboard.py` — **EXTEND** — image PNG support via xclip `-t image/png` + size cap + magic-byte validation (D-13/D-14/D-16)
- `server/mac_clipboard.py` — **EXTEND** — image PNG support via NSPasteboardTypePNG + size cap + magic-byte validation (D-13/D-14/D-16)
- `server/session_runtime.py` — **EXTEND** — wire the per-direction toggle policy through clipboard dispatch (D-15)
- `common/messages.py` — **EXTEND** — `server_x/server_y` physical-pixel fields on input messages (D-05); `ClipboardChunkMsg` or extended `ClipboardMsg` for chunking (D-17); capture-mode field on ClientHelloMsg or new SessionConfigureMsg (D-02)
- `tests/server/test_screen_capture_hotplug.py` — **NEW** — mocked mss + xrandr hot-plug scenarios (D-12)
- `tests/server/test_mac_screen_capture_hotplug.py` — **NEW** — mocked SCK display-change (D-12)
- `tests/integration/test_monitor_hotplug.py` — **NEW** — in-process loopback mid-session hotplug (D-12)
- `tests/server/test_clipboard_image.py` + `tests/server/test_mac_clipboard_image.py` — **NEW** — PNG magic-byte validation, size cap enforcement, CRLF preservation
- `tests/common/test_cursor_math.py` — **NEW** — widget→physical-pixel math with fixture mixed-DPI topologies (D-05)
- `docs/release.md` — **EXTEND** — pre-phase spike pass bar recording template (D-08), hot-plug + RESIZE_REQUEST known-limitation notes (D-10)

### External docs (informs but does not dictate)

- Qt 6 HighDPI documentation — per-screen DPR semantics and `QScreen::devicePixelRatio()` behavior (D-06)
- Mozilla Bugzilla bug #794038 — mixed-DPI Mac picking one DPR on window-straddle — drives D-06 edge case acceptance
- NVIDIA forum xorg SIGSEGV on HDMI hotplug disconnect (RTX 5090, 580.x drivers) — drives D-10
- Apple ScreenCaptureKit `SCStreamDelegate` API documentation — drives D-11 push-callback
- Apple `NSPasteboardTypePNG` / `NSPasteboardTypeString` reference — drives D-13/D-16 clipboard paths
- PCoIP EDID bundled files reference (`/usr/share/pcoip-agent/1024x768.bin`) — existing fallback path stays

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- `client/monitor_selector.py` (126 lines) — checkable multi-select menu; D-04 constrains to single-check for pick-one, stays multi-check elsewhere. Quick-action "Select All" / "Select None" already present.
- `server/monitor_hotplug.py` (62 lines) — 5s poll loop, `MonitorListMsg` broadcast, `EncoderLifecycle.restart()` wire-up. D-09 extends (fall-back logic) and D-11 extends the push-signal side.
- `server/screen_capture.py::list_monitors` (lines 383-417) + `detect_hotplug` (lines 419-444) — Linux side. Returns `MonitorInfo` dataclass with id/name/width/height/x/y/primary/scale. Full signature already; just needs to be consumed consistently.
- `server/mac_screen_capture.py::detect_hotplug` (lines 568-580) — shallow signature, upgrade target for D-11.
- `server/session_manager.py::find_edid_file` (lines 121-140) + `_generate_edid` (lines 143-262) — CustomEDID infra already in place; D-04 picks a Flame-approved profile to ship or generate.
- `server/clipboard.py` (125 lines) — xclip/xsel text path with 500ms poll + `_last_content` echo suppression; extend with `-t image/png` path (D-13).
- `server/mac_clipboard.py` (142 lines) — NSPasteboard text path with 250ms poll + `_last_change_count` echo suppression; extend with `NSPasteboardTypePNG` path (D-13).
- `common/messages.py::ClipboardMsg` (lines 443-450) — already has `content_type` and `data` fields; base64-encoded text. Extend content_type values (D-13); add chunk envelope (D-17).
- `common/messages.py::MonitorListMsg` — already broadcast path; just needs the geometry to be consumed by client viewer math (D-05).
- `client/viewer.py::_widget_to_remote` (lines 655-694) — existing composite-mode math. D-05 refactors to emit integer server physical pixels; existing monitor-regions logic (lines 673-688) maps cleanly if rewritten over MonitorListMsg geometry.
- `client/viewer.py::tabletEvent` (line 765+) — already forwards pressure / tilt. D-05 adds server_x/y emission parallel to existing normalized forward.
- `client/key_diagnostic.py` — pattern reference for D-07 F12 overlay.
- `tests/common/` + `tests/server/` + `tests/integration/` scaffolding from Phase 1 D-02/D-03 — Phase 3 extends without inventing new test infrastructure.

### Established Patterns

- **Platform split:** `mac_<feature>.py` vs plain-named Linux file — `server/mac_clipboard.py` vs `server/clipboard.py`, `server/mac_screen_capture.py` vs `server/screen_capture.py`. Phase 3 additions follow.
- **Polling + echo-suppression cache** for external state (clipboard both platforms, monitor-hotplug both platforms). Phase 3 keeps the poll as safety net, adds push signal (D-11) where available.
- **Bookmark = per-session config** with in-session toolbar override (P2 D-10 Cmd↔Ctrl pattern). D-01 mode + D-15 clipboard toggles replicate this shape.
- **MonitorListMsg-driven client math** — the message is already being consumed for monitor-selector population; D-05 extends to drive cursor math too.
- **Mock-at-OS-boundary test pattern** (P1 D-02, P2 mock-at-VT / mock-at-IOKit) — Phase 3 mocks at mss / xrandr / SCK / NSPasteboard / xclip boundaries for D-12 + clipboard tests.
- **structlog per-stage telemetry** (P1 OBS-01) — Phase 3 adds `event=monitor_hotplug`, `event=clipboard_image`, `event=clipboard_chunk` with size / duration fields to keep D-18 measurable.
- **`_HAS_APPKIT` / `_HAS_<framework>` import-guard at module top** (pattern from `mac_clipboard.py`) — new Mac code follows.

### Integration Points

- **Capture-mode negotiation** (D-02): `client/session.py` connect → `ClientHelloMsg` (or new SessionConfigureMsg) carries `capture_mode: "single"|"mirror_all"|"pick_one"` + `picked_monitor_id: Optional[int]`. Server `session_runtime.py` configures capture crop rectangle; `EncoderLifecycle` sees it on encoder (re)start.
- **Physical-pixel coord path** (D-05): `client/viewer.py::_widget_to_remote` emits `(x_px, y_px)` ints using `QScreen::devicePixelRatio()` of current screen × widget-relative geometry × MonitorListMsg server geometry; `client/protocol.py` send_mouse/send_tablet adds `server_x/server_y` fields; `server/input_injector.py` + `server/mac_input_injector.py` consume the explicit fields when present.
- **Hot-plug signal upgrade** (D-11): `server/mac_screen_capture.py` adds `SCStreamDelegate` callback that sets a `_hotplug_pending` flag; `server/monitor_hotplug.py` poll loop checks the flag in addition to the 5s cadence so the handler fires faster.
- **Clipboard chunk pipeline** (D-17): `common/messages.py` adds `ClipboardChunkMsg`; client side (`client/protocol.py`) buffers incoming chunks by `sequence_id` into a per-session dict, emits one `ClipboardMsg`-equivalent when all chunks arrive; server side mirrors. Own queue slot distinct from streaming queue per P1 STAB-07.
- **Per-direction toggle gating** (D-15): `client/session.py` and `server/session_runtime.py` each hold the 4 active toggle bools; clipboard dispatchers short-circuit on direction-disabled without wire traffic.
- **F12 dev overlay** (D-07): `client/viewer.py::keyPressEvent` gates on `os.environ.get("TERAGUCHI_DEBUG")`; overlay is pure `QPainter` on top of the Metal video blit (same layering as existing cursor/overlays path).

</code_context>

<specifics>
## Specific Ideas

- **"Indistinguishable from local" → Phase 3 owns the topology half of that promise.** After Phase 2 locked color and input, a Flame artist who drags their viewer from Retina to external 4K mid-session and clicks in a Flame tab expects the click to land on the correct pixel. D-05 + D-06 + D-08 exist because "feels close enough" is not the target.
- **Rocky server stays the Flame production path.** The 4-corner spike deliberately runs on Rocky NVIDIA Xorg, not Mac server — that's where Flame lives per P2 D-07. Mac-server mixed-DPI correctness is a nice-to-have, not load-bearing.
- **PCoIP feature-parity is the DISP / CLIP user mental model.** Artists migrating from PCoIP expect: single / mirror / pick-one (got it, D-01). Hot-plug survives (got it, D-09). Clipboard images just work (got it, D-13). Per-direction privacy (got it, D-15). We do not need to invent new paradigms here — matching PCoIP + closing its 8-bit / Windows-only gap is the whole point.
- **The 4-corner test (D-08) is a Phase 3 pre-gate, not a Phase 3 deliverable per se.** The spike lands in the Phase 3 timeline but conceptually it decides whether D-05's physical-pixel math ships as-designed. If the test fails (off-by-N at corners), the planner re-scopes D-05 before the rest of Phase 3 writes math over the same broken foundation.
- **Do not regress Phase 1 or Phase 2.** All Phase 3 work must pass: P1's 4 CI gates (pytest, ruff+mypy, build artifacts, latency p99<25ms), the P2 9-checkpoint 10-bit ramp fixture, the P2 exhaustive keymap table, the P2 modifier-reset FSM. The Phase 3 verification step re-runs the 1-hour synthetic smoke harness + adds the D-08 corner-click test + re-verifies the P2 pipeline fixture did not regress.
- **Bulletproof definition for Phase 3:** session does not die on server or client monitor hotplug (D-09/D-11), cursor does not drift on mixed-DPI (D-05/D-06/D-08), clipboard does not corrupt text or lose images (D-13/D-14/D-16/D-17), and no silent failure modes that skip UI feedback (D-03 grayed + tooltip, D-07 dev overlay, D-14 toast on oversize, D-09 remap banner, D-15 toolbar menu state).
- **Phase-2-deferred `server/main.py DISPLAY` env-var thread-safety:** the P2 context-file punted this to Phase 3 "if session-manager work lands there." Hot-plug handler touches `session_manager.py` indirectly via `EncoderLifecycle`, but does not write DISPLAY. Planner decides whether to fold the cleanup into D-10 session-manager work or push it forward to a post-v1 cleanup pass. Not a Phase 3 commitment.

</specifics>

<deferred>
## Deferred Ideas

- **Dynamic server geometry / RESIZE_REQUEST path resurrection** → v1.1+. Blocked on NVIDIA xrandr-segfault driver bug (PITFALLS #5, CONCERNS.md). Revisit when NVIDIA ships a fix or when a studio demands dynamic resolution change.
- **Rich-text + file-reference clipboard (`text/html`, `text/uri-list`)** → v1.1 if demanded. Phase 3 only covers text + PNG image per CLIP-01..03. File references better handled through the Phase 5 file_transfer.py drag-drop path.
- **JPEG as a second clipboard image format** → v1.1 if 64 MB PNG cap proves limiting. Currently PNG-only (D-13) keeps one codepath.
- **Side-channel clipboard transport via file_transfer.py infra** → v1.1 if D-17 control-channel chunking shows head-of-line latency issues. Measurement-driven; not speculative.
- **Window-straddling-two-screens DPR correctness** → documented v1 limitation per D-06. Qt picks one DPR, artist accepts. v1.1 if demand surfaces.
- **CGEventTap "aggressive capture" for Mac-client trap keys** → preserved from Phase 2 deferred (still). Phase 3 doesn't add it.
- **Self-hosted DXS CI runners for real-hardware hot-plug tests** → preserved from Phase 1/2 deferred. Synthetic CI (D-12) + release-ritual spike cover it in v1.
- **Chaos-test random monitor-toggle extension to the 1-hour smoke harness** → Phase 4/5 chaos extension (already a roadmap item for audio 60-min continuity + network roaming). Not Phase 3.
- **`server/main.py DISPLAY` env-var thread-safety cleanup** → planner's call (per Specifics above). Default: push to post-v1 cleanup unless D-10 session-manager work makes it cheap to fold in.
- **Cursor-shape sync across mixed-DPI boundaries** → existing cursor_tracker.py ships server-cursor-shape today; mixed-DPI resampling of the shape is not called out in DISP-01..07. Leave as-is; revisit if artists complain.
- **Reconnect-clipboard convergence "last-write-wins with user confirmation on collision"** → PITFALLS #6 idea. Existing `_last_content` + `_last_change_count` caches already do last-write-wins silently. Adding a confirmation dialog would interrupt Flame artists; defer unless it becomes a real user-reported issue.
- **Apple DriverKit-signed HIDDriverKit** → v1.1 (unchanged from Phase 2 deferred).
- **Full ICC / HDR tone-mapping pipeline** → out of scope for v1 (unchanged from PROJECT.md).
- **Wayland / Rocky 10 server target** → blocked on Autodesk Flame Wayland support (unchanged).

### Reviewed Todos (not folded)

None — `node ~/.claude/get-shit-done/bin/gsd-tools.cjs list-todos` returns `count: 0` at phase discussion time.

</deferred>

---

*Phase: 03-display-multi-monitor-clipboard*
*Context gathered: 2026-04-19*
