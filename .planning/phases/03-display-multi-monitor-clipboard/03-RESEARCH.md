# Phase 3: Display + Multi-Monitor + Clipboard — Research

**Researched:** 2026-04-19
**Domain:** Multi-monitor capture/display, mixed-DPI cursor math, monitor hot-plug, bidirectional text+image clipboard with per-direction toggles
**Confidence:** HIGH for codebase facts (verified against tree); HIGH for Qt 6.10 / SCK / xrandr behavior (CITED to docs); MEDIUM for exact wire-shape choices (left to planner per CONTEXT discretion)

---

## Summary

Phase 3 ships the topology half of the "indistinguishable from local" promise. After Phase 2 locked color (10-bit P010 / HEVC Main10) and input (modifier discipline + IOHIDUserDevice pen), Phase 3 has to make a Flame artist's three deployment topologies — single-monitor solo, mirror-all client review, pick-one laptop-on-the-go — all behave correctly on a Mac client driving a Rocky NVIDIA Xorg server with mixed-DPI screens, while surviving monitor hot-plug on either side mid-session and adding bidirectional PNG image clipboard with per-direction privacy toggles.

The 18 locked decisions in CONTEXT.md (D-01 through D-18) constrain almost every implementation choice. The unconstrained surface is small and well-bounded: exact `MonitorListMsg` extension shape, exact new-message names (`ClipboardChunkMsg`, `SessionConfigureMsg`), CustomEDID profile name choice, banner/toast/F12-overlay visual design, and `SCStreamDelegate` placement. The planner should treat the locked decisions as inviolable and resist the temptation to revisit them under pressure during plan generation.

Three crosscutting risks dominate: (1) cursor coord math correctness on mixed-DPI client straddling Retina + external 4K — D-08's 4-corner spike is the gate; (2) NVIDIA Xorg xrandr-segfault — solved by D-10's read-only hot-plug + frozen RESIZE_REQUEST stub, do not relitigate; (3) latency regression from server-side GPU crop (D-02) and clipboard-chunk interleave (D-17) — Phase 1's p99<25ms gate plus the P2 DXS hardware measurement re-run is the safety net.

**Primary recommendation:** Plan Phase 3 in 4 wave-sized arcs that map cleanly to the 4 gray areas in CONTEXT: (Wave 0) common/messages.py wire extensions + Mac SCK push-callback foundation; (Wave 1) Mode selector UX + server-side GPU crop + bookmark migration; (Wave 2) cursor-coord physical-pixel rewrite + per-screen DPR + F12 dev overlay + 4-corner spike; (Wave 3) hot-plug push-signal upgrade + remap banner + auto-fallback toast; (Wave 4) clipboard image PNG + chunked transport + 4-toggle per-bookmark UX. Each wave has explicit unit + in-process-loopback integration tests; the pre-phase 4-corner DXS spike (D-08) is a separate ritual that gates Wave 2.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Mode Selector UX + Lock-on-Connect (DISP-01 / DISP-07)**

- **D-01:** Mode picker lives in the connect dialog, full-stop. Single / mirror-all / pick-one is chosen when the user clicks Connect on a bookmark and is locked for the life of that session. Bookmark stores the last-used mode as the default. Toolbar surfaces the current mode read-only (badge in health overlay area). No hidden "advanced" disclosure — the mode is too consequential to bury.
- **D-02:** Server-side crops per mode before encode. Server capture stays always-full-virtual-desktop (no per-session capture variance → simpler capture codepath, no regressions to the Phase 2 10-bit fixture). For `single` and `pick-one`, the server applies a GPU crop between capture and encode so only the chosen pixels hit the codec (bandwidth savings on WAN). `mirror-all` ships the virtual desktop unchanged. The crop rectangle comes from the selected monitor's geometry in `MonitorListMsg`; negotiated via a `CAPTURE_MODE` field on an extended `ClientHelloMsg` or a new SESSION_CONFIGURE message (planner decides shape).
- **D-03:** No-mid-session-switch is enforced by grayed UI + explicit tooltip — the mode widget is visibly disabled during an active session with tooltip text "Disconnect and reconnect to change monitor mode." No silent failures, no half-supported code paths.
- **D-04:** Pick-one reuses the existing `client/monitor_selector.py` widget constrained to single-check (radio) behavior. Bookmark stores the last pick by both `id` AND `name` (belt + suspenders — `id` survives reorder, `name` survives hotplug). On connect, if the bookmarked monitor still exists by id-or-name, use it; else fall back to primary monitor + emit a toast "Monitor X not found, now viewing primary." Zero new UI code — the widget already supports this shape.

**Cursor-Coord Math + Mixed-DPI (DISP-03 / DISP-05)**

- **D-05:** Wire carries server physical-pixel integer coords. End the normalize 0.0-1.0 era for cursor/pen events — it costs precision on 10,000px-wide virtual desktops. `MouseEventMsg` / `KeyEvent` / `TabletEvent` payloads gain explicit `server_x: int, server_y: int` fields in server physical pixels. Client-side `client/viewer.py::_widget_to_remote` rewrites to produce physical pixels using the current `MonitorListMsg` geometry. Normalized floats may remain on the wire during transition, but server consumers prefer the integer fields when present.
- **D-06:** Per-screen DPR via `QScreen::devicePixelRatio()` re-evaluated on Qt `screenChanged`. On every pointer event, look up which `QScreen` the viewer window is currently on (`QWidget::screen()`) and use THAT screen's DPR. Re-evaluate + recompute scaling cache on the `QWindow::screenChanged` signal. Handles the Cintiq Pro 24 + Retina case correctly per Qt 6 HighDPI docs. Known edge: a viewer window straddling two screens gets Qt's pick of one DPR — documented limitation.
- **D-07:** Dev-only F12 debug overlay. `RemoteViewer.keyPressEvent` F12 toggles a transparent overlay showing widget coords, computed server coords, current `QScreen` DPR, active monitor region from `MonitorListMsg`, and (in pick-one) the crop rect. Gated on `TERAGUCHI_DEBUG=1` env var so the overlay is invisible in release builds. Production users get no UI change.
- **D-08:** Pre-phase spike pass bar is the 4-corner click test on real mixed-DPI hardware. On a Retina MBP + external 4K client driving a 2×2560×1600 Rocky NVIDIA Xorg server: click each of the 4 corner pixels of each server monitor, from both the Retina screen and the external 4K screen, in pick-one / mirror-all / single modes. Expected server click coord = corner coord within 1 px. Any fail blocks DISP-03 / DISP-05 ship. Captured in `docs/release.md` alongside P2 D-16 Wacom matrix.

**Hot-Plug Behavior (DISP-02 / DISP-06)**

- **D-09:** Degrade-in-place + non-modal remap banner. When a server-side monitor appears/disappears mid-session: `MonitorListMsg` broadcasts the new topology; `EncoderLifecycle.restart()` reinitializes with the new geometry; client shows a non-modal banner ("Monitor removed — click to remap") with a link that opens pick-one picker when applicable. If the currently-picked monitor vanishes, server auto-falls-back to primary monitor + emits a toast "Monitor X gone, now viewing primary." Stream never stalls; session state preserved.
- **D-10:** NVIDIA xrandr-segfault guard = keep `RESIZE_REQUEST` stubbed, read-only hot-plug only. No Phase 3 code calls `xrandr --mode` / `xrandr --addmode` / any set-operation on a running Xorg. Server advertises fixed geometry from CustomEDID/session_manager.py at bootstrap and the hot-plug handler is pure read (mss / xrandr-query / SCK enumeration). The `# TODO: investigate safe resize path for GPU displays` comment at `server/main.py:548-552` STAYS. Documented as a known v1 limitation in `docs/release.md`.
- **D-11:** macOS SCK hot-plug signature = full `(displayID, width, height, x, y)` tuple + push-based `SCStreamDelegate` callback. Today's `server/mac_screen_capture.py::detect_hotplug` only checks count + WxH — misses display reorder, same-size swaps, and repositioning. Upgrade to the full tuple signature. Also subscribe to ScreenCaptureKit's `SCStreamDelegate` display-change callback so the handler fires on-change instead of only on 5s poll (poll stays as safety net).
- **D-12:** Hot-plug CI coverage = mocked + in-process loopback. Unit tests inject fake monitor-list changes into `ScreenCapture.detect_hotplug` + `MacScreenCapture.detect_hotplug` and assert `MonitorListMsg` broadcast fires and `EncoderLifecycle.restart` is called. Integration test via P1 D-03 in-process loopback: simulate monitor-gone mid-session, assert client receives `MonitorListMsg`, viewer re-renders new topology, no exception bubbles, session FSM stays in `streaming`. Real hardware is the pre-phase spike (D-08) + every-release manual ritual. No self-hosted CI runners.

**Clipboard Image + Per-Direction Toggle (CLIP-01 / CLIP-02 / CLIP-03)**

- **D-13:** Image clipboard format = PNG lossless, base64 inside the existing `ClipboardMsg`. Extend `ClipboardMsg.content_type` to accept `"image/png"` with `data = base64(png_bytes)`. PNG is the universal clipboard format on both NSPasteboard (`NSPasteboardTypePNG`) and X11 (`xclip -t image/png`). Lossless preserves paint references and frame grabs — JPEG lossy-compression is unacceptable for VFX reference. One format, one codepath, no format negotiation.
- **D-14:** Size cap = 64 MB (decoded PNG bytes), silent-fail-closed with local toast. Copies exceeding 64 MB on the originating side are dropped locally with a user-visible toast ("Clipboard image too large — copy it as a file instead"). Never sent on wire. Receiving side independently enforces the same 64 MB cap as defense-in-depth. Matches the PITFALLS "size cap; validate magic bytes" guidance.
- **D-15:** Per-direction toggles live per-bookmark with in-session toolbar override. Bookmark stores four bool flags: `clipboard_text_c2s`, `clipboard_text_s2c`, `clipboard_image_c2s`, `clipboard_image_s2c` — all default ON. A clipboard icon in the existing toolbar (near `MonitorSelector`) opens a 4-checkbox menu for in-session flip; changes take effect immediately on the next clipboard event. Mirrors the P2 D-10 per-bookmark + in-session pattern (Cmd↔Ctrl swap).
- **D-16:** Security defaults = all directions ON, magic-byte + size validated, CRLF preserved. Out-of-box, text and image both flow both directions. Inbound image payloads validate the first 8 bytes = PNG signature `\x89PNG\r\n\x1a\n` before accepting; malformed headers dropped with a structlog warning + nothing injected into the system clipboard. Size cap (D-14) enforced on receive too. Text side preserves the sender's native newline encoding on arrival (CRLF stays CRLF, LF stays LF, CR stays CR) — never silently transform.
- **D-17:** Large-image transport stays on the control channel, chunked into 1 MB JSON messages with sequence ID. A 64 MB PNG base64's to ~85 MB of JSON. Rather than stalling the control channel behind one giant message, chunk into 1 MB `ClipboardChunkMsg` frames with `{sequence_id, chunk_index, total_chunks, content_type, data}`. Mouse / pen / key events interleave between chunks so input latency doesn't spike during a paste. Clipboard chunks get their own bounded queue slot distinct from the streaming queue (preserves P1 STAB-07 "bounded pipeline queues" discipline).

**Phase 3 Latency Gate (crosscutting)**

- **D-18:** Phase 3 must not regress the P1/P2 latency gate. The Phase 1 synthetic p99 < 25 ms CI gate (P1 D-08) and the P2 D-21 DXS real-hardware input-to-photon measurement both stay green. Specific risk zones for this phase:
  - Server-side GPU crop path (D-02) — adds a shader pass; verify encoder-stage latency in `structlog` telemetry stays in budget
  - Wire coord model change (D-05) — adds 8 bytes per input event but removes a normalize/denormalize op; likely neutral-to-positive
  - Clipboard chunking (D-17) — cannot stall input-to-photon since input messages get their own queue slot
  Phase 3 verification step re-runs the P1 1-hour synthetic smoke harness + the D-08 corner-click test; any regression blocks ship.

### Claude's Discretion

The planner has freedom on these — they fall out naturally from the locked decisions:

- **CustomEDID Flame-approved monitor profile (DISP-04).** Existing `server/session_manager.py::find_edid_file` already has PCoIP fallback + minimal 1920×1200 TGC generator. Pick a Flame-approved profile name (Eizo CG279X-like or similar industry-standard grading monitor) so Flame's monitor-config dialog accepts it without complaint. Not user-configurable in v1. The generator stays as fallback for users without the shipped binary.
- **Exact `MonitorListMsg` extension shape** for physical-pixel geometry + per-monitor `scale` flow into D-05 client math.
- **Banner UX copy + color for D-09 remap** — any Qt toast/banner pattern that fits with the existing `health_display.py` aesthetic. Already locked in 03-UI-SPEC.md; planner consumes that.
- **4-checkbox menu visual design** for D-15 toolbar toggle. Already locked in 03-UI-SPEC.md.
- **New protocol message names** (`ClipboardChunkMsg`, `SessionConfigureMsg`, any image-clipboard receive-side helpers) — follow existing `common/messages.py` naming convention.
- **F12 overlay visual design** for D-07 — locked in 03-UI-SPEC.md.
- **Whether `SCStreamDelegate` subscription lives in `mac_screen_capture.py` or a new thin wrapper** (D-11).
- **Test fixture layout** for hotplug mocks (follow P1 `tests/server/` + `tests/integration/` conventions).

### Deferred Ideas (OUT OF SCOPE)

- Dynamic server geometry / RESIZE_REQUEST path resurrection → v1.1+. Blocked on NVIDIA xrandr-segfault driver bug.
- Rich-text + file-reference clipboard (`text/html`, `text/uri-list`) → v1.1 if demanded. Phase 3 only covers text + PNG image per CLIP-01..03.
- JPEG as a second clipboard image format → v1.1 if 64 MB PNG cap proves limiting.
- Side-channel clipboard transport via file_transfer.py infra → v1.1 if D-17 control-channel chunking shows head-of-line latency issues.
- Window-straddling-two-screens DPR correctness → documented v1 limitation per D-06. Qt picks one DPR.
- CGEventTap "aggressive capture" for Mac-client trap keys → preserved from Phase 2 deferred.
- Self-hosted DXS CI runners for real-hardware hot-plug tests → preserved from Phase 1/2 deferred.
- Chaos-test random monitor-toggle extension to the 1-hour smoke harness → Phase 4/5 chaos extension.
- `server/main.py DISPLAY` env-var thread-safety cleanup → planner's call. Default: push to post-v1 cleanup unless D-10 session-manager work makes it cheap to fold in.
- Cursor-shape resampling across mixed-DPI boundaries → existing `cursor_tracker.py` ships server-cursor-shape today; mixed-DPI resampling is not in DISP-01..07.
- Reconnect-clipboard convergence "last-write-wins with user confirmation on collision" → existing `_last_content` + `_last_change_count` caches already do silent last-write-wins; defer dialog unless reported.
- Apple DriverKit-signed HIDDriverKit, full ICC / HDR tone-mapping pipeline, Wayland / Rocky 10 server target — all preserved out-of-scope from prior phases.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DISP-01 | Per-session multi-monitor mode selector at connect time: single-monitor / mirror-all-server-monitors / pick-one | Connect-dialog mode picker (UI-SPEC C-01); D-01 lock-at-connect; bookmark stores last-used mode; D-02 server-side crop pre-encode for single/pick-one |
| DISP-02 | Monitor hot-plug during session gracefully handled (no crash, offer remap UI) | D-09 degrade-in-place + non-modal remap banner; existing `server/monitor_hotplug.py` 5s poll + EncoderLifecycle.restart() wiring; pick-one auto-fallback to primary; D-12 mocked + in-process loopback CI |
| DISP-03 | Cursor coordinates always computed in server physical pixels regardless of client DPR / retina / zoom | D-05 wire model change to integer server physical pixels; D-06 per-screen DPR; D-08 pre-phase 4-corner spike on real Cintiq Pro 24 + Retina + 2×2560×1600 NVIDIA Xorg |
| DISP-04 | CustomEDID for Xvfb sessions matching Flame's monitor-config-dialog expectations | Existing `server/session_manager.py::find_edid_file` (PCoIP fallback + TGC generator); Claude's discretion to pick Flame-approved profile name |
| DISP-05 | Mixed-DPI client rendering (retina + external non-retina) without distortion | D-05 + D-06 + D-08 same as DISP-03; QScreen::devicePixelRatio per-screen lookup re-evaluated on screenChanged |
| DISP-06 | ScreenCaptureKit display-change handler for macOS server monitor hot-plug | D-11 SCStreamDelegate push callback + full (displayID, width, height, x, y) tuple signature; 5s poll stays as safety net; D-12 mocked SCK in CI |
| DISP-07 | Per-monitor fullscreen mode (client chooses which server monitor the client window corresponds to) | D-01 connect-dialog mode picker (single / mirror-all / pick-one) is the UX surface; D-04 pick-one reuses monitor_selector in radio mode; bookmark stores picked monitor by id+name |
| CLIP-01 | Bidirectional text clipboard — existing, harden against edge cases (large pastes, newline encoding) | Existing `server/clipboard.py` (xclip 500ms poll) + `server/mac_clipboard.py` (NSPasteboard 250ms poll); D-16 preserve native CRLF/LF/CR; D-17 chunked 1MB transport for >1MB pastes |
| CLIP-02 | Bidirectional image clipboard — PNG/JPEG transport, screenshots and paint reference | D-13 PNG lossless, base64 in extended `ClipboardMsg.content_type="image/png"`; D-14 64MB cap with silent-fail-closed toast; D-16 PNG magic-byte validation on receive |
| CLIP-03 | Per-direction clipboard toggles (user can disable client → server paste for privacy) | D-15 four bookmark bool flags + toolbar 4-checkbox menu (UI-SPEC C-07/C-08); default-ON policy; in-session immediate effect |
</phase_requirements>

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Connect-dialog mode picker | Client UI | Bookmark store | D-01 lock-at-connect; bookmark stores last-used mode as default |
| Mode badge (read-only display) | Client UI | — | D-01 toolbar surface; passive readback of session state |
| Mode lock during session | Client UI / Session FSM | — | D-03 grayed widget tied to session state |
| Server-side GPU crop per mode | Server (capture/encode pipeline) | Server (negotiation) | D-02 server crops between always-full-virtual-desktop capture and encoder; preserves Phase 2 capture path |
| Monitor list enumeration | Server (platform backends) | Wire protocol | `ScreenCapture.list_monitors()` + `MacScreenCapture.list_monitors()` already populate `MonitorInfo`; Phase 3 extends consumption |
| Cursor coord math | Client (RemoteViewer) | Wire protocol | D-05 client emits server physical pixels; server consumes integer fields when present |
| Per-screen DPR re-evaluation | Client (Qt window/screen plumbing) | — | D-06 hooks `QWindow::screenChanged` signal |
| F12 dev overlay | Client UI (debug-gated) | — | D-07 invisible in release; pure QPainter on top of Metal blit |
| Server-side hot-plug detection | Server (platform backends) | Wire protocol | D-09 / D-11 existing `monitor_hotplug.py` poll + new SCK push callback; broadcast `MonitorListMsg` |
| Encoder restart on hot-plug | Server (EncoderLifecycle) | — | Existing wire-up from Phase 1 STAB-05; reuse, don't reinvent |
| Auto-fallback to primary on picked-monitor-vanish | Server (session_runtime) | Client UI (toast) | D-09; server-driven so picked-monitor accidents don't kill session |
| Remap banner on topology change | Client UI | — | D-09 non-modal banner per UI-SPEC C-05 |
| Clipboard text round-trip | Server (clipboard.py / mac_clipboard.py) | Wire protocol | Existing path; harden per D-16 newline-preservation |
| Clipboard image round-trip (PNG) | Server (clipboard.py / mac_clipboard.py) | Wire protocol | D-13 extend `ClipboardMsg.content_type`; xclip `-t image/png` on Linux, `NSPasteboardTypePNG` on Mac |
| Clipboard chunking | Wire protocol (control channel) | Server + Client (assembler) | D-17 1MB `ClipboardChunkMsg` frames; assemble on receive |
| Per-direction toggle gating | Client (session) + Server (session_runtime) | Bookmark store | D-15 short-circuit dispatch on disabled direction; UI lives in toolbar |
| Magic-byte + size validation | Client + Server (defense-in-depth) | — | D-16 both sides validate independently |

---

## Architecture Patterns

### System Architecture Diagram

```
                      ┌──────────────────────────────────────┐
                      │         CLIENT (PySide6)             │
                      │                                      │
   Connect Dialog ──▶ │ ModeSelector (single / mirror /      │
                      │   pick-one) [C-01, D-01]             │
                      │           │                          │
                      │           ▼                          │
                      │ Bookmark.monitor_mode +              │
                      │   picked_monitor_id/name [D-04]      │
                      │           │                          │
                      │           ▼                          │
                      │ ClientHello / SessionConfigure ──┐   │
                      │   capture_mode + pick_id  [D-02] │   │
                      └──────────────────────────────────┼───┘
                                                         │
                          control-channel WebSocket      │
                                                         │
                      ┌──────────────────────────────────┼───┐
                      │         SERVER                   │   │
                      │                                  ▼   │
                      │  SessionRuntime [Phase 1 STAB-05]    │
                      │           │                          │
                      │           ▼                          │
                      │  ScreenCapture (full virtual desk)   │
                      │           │                          │
                      │           ▼                          │
                      │  GPU Crop Stage [D-02]               │
                      │   • mirror-all: pass-through         │
                      │   • single/pick-one: crop to monitor │
                      │           │                          │
                      │           ▼                          │
                      │  VideoEncoder [Phase 2 P010 path]    │
                      │           │                          │
                      │           ▼                          │
                      │  send_queue(maxsize=4) [STAB-04]     │
                      └───────────┬──────────────────────────┘
                                  │  encoded video frames
                                  ▼
                      ┌──────────────────────────────────────┐
                      │         CLIENT                       │
                      │  DecoderManager → VideoBlitWidget    │
                      │           │                          │
                      │           ▼                          │
                      │  RemoteViewer (composite paint)      │
                      │   • _widget_to_remote → server px    │
                      │     [D-05] using current QScreen DPR │
                      │     [D-06] + MonitorListMsg geometry │
                      │           │                          │
                      │           ▼                          │
                      │  PenEvent / KeyEvent / MouseEvent    │
                      │   with explicit server_x, server_y   │
                      │           │                          │
                      └───────────┼──────────────────────────┘
                                  │
                                  ▼ (input round-trip)

  Hot-plug paths (parallel):
    Linux:   xrandr-query (read-only) → MonitorHotplug 5s poll + change
              → MonitorListMsg broadcast → client banner [D-09]
    macOS:   SCStreamDelegate push callback [D-11] → MonitorHotplug poll
              → MonitorListMsg broadcast + EncoderLifecycle.restart()

  Clipboard paths (control channel, separate queue slot):
    Text c2s/s2c:  existing path; D-16 newline preservation
    Image c2s/s2c: PNG + base64 + magic-byte validate; D-17 1MB chunks
                    interleaved with input events
```

### Recommended Project Structure (Phase 3 additions / extensions)

```
client/
├── connect_dialog.py          # EXISTING — add ModeSelector sub-widget [C-01]
├── monitor_selector.py        # EXTEND — add `mode="radio"` for pick-one [C-04]
├── viewer.py                  # REFACTOR — _widget_to_remote → physical px [D-05]
│                              # screenChanged hook [D-06]
├── coord_debug_overlay.py     # NEW — F12 dev overlay [C-06, D-07]
├── remap_banner.py            # NEW — non-modal topology change banner [C-05]
├── toasts.py                  # NEW — InfoToast + helpers [C-09, C-10]
├── clipboard_toggle_menu.py   # NEW — 4-checkbox toolbar QMenu [C-07]
├── icons.py                   # EXTEND — icon_clipboard() [C-08]
├── bookmarks.py               # EXTEND — monitor_mode, picked_monitor_id/name,
│                              # 4 clipboard toggle bools [D-04, D-15]
├── session.py                 # EXTEND — capture_mode + 4 toggles wiring [D-01/02/15]
├── protocol.py                # EXTEND — image/png ClipboardMsg + chunk receive
│                              # [D-13, D-17]; per-direction gating [D-15]
└── fullscreen_toolbar.py      # EXTEND — clipboard toolbar button [C-07] +
                               # mode badge [C-02]

server/
├── screen_capture.py          # EXTEND — GPU crop stage for single/pick-one [D-02]
│                              # full hot-plug signature
├── mac_screen_capture.py      # EXTEND — full (id, w, h, x, y) signature [D-11]
│                              # SCStreamDelegate display-change hook
├── monitor_hotplug.py         # EXTEND — auto-fallback to primary on
│                              # picked-monitor-vanish [D-09]
├── session_manager.py         # EXTEND — Flame-approved CustomEDID profile [DISP-04]
├── session_runtime.py         # EXTEND — per-direction toggle policy [D-15];
│                              # capture_mode → crop config [D-02]
├── clipboard.py               # EXTEND — image/png path via xclip -t image/png
│                              # [D-13, D-14, D-16]
└── mac_clipboard.py           # EXTEND — NSPasteboardTypePNG path [D-13/14/16]

common/
└── messages.py                # EXTEND — server_x/server_y on input msgs [D-05]
                               # ClipboardChunkMsg or extended ClipboardMsg [D-17]
                               # capture_mode field on ClientHelloMsg or new
                               # SessionConfigureMsg [D-02]

tests/
├── server/
│   ├── test_screen_capture_hotplug.py      # NEW — mocked mss + xrandr [D-12]
│   ├── test_mac_screen_capture_hotplug.py  # NEW — mocked SCK [D-12]
│   ├── test_clipboard_image.py             # NEW — PNG magic-byte + size cap
│   ├── test_mac_clipboard_image.py         # NEW — same on Mac side
│   └── test_capture_crop.py                # NEW — D-02 crop-rect math
├── integration/
│   └── test_monitor_hotplug.py             # NEW — in-process loopback [D-12]
└── common/
    ├── test_cursor_math.py                 # NEW — widget→server px [D-05]
    └── test_clipboard_chunking.py          # NEW — assembler tests [D-17]

docs/
└── release.md                 # EXTEND — D-08 4-corner spike pass bar template
                               # D-10 RESIZE_REQUEST known-limitation note
```

### Pattern 1: Server-side capture-then-crop (D-02)

**What:** Capture stays always-full-virtual-desktop. A new GPU-side crop stage lives between capture and encoder feed. For `single`/`pick-one`, crop the BGRA / P010 surface to the chosen monitor's geometry. For `mirror-all`, pass through.

**When to use:** Every Phase 3 capture path. This is the load-bearing simplification.

**Why this shape:** The Phase 2 9-checkpoint 10-bit fixture was captured against the always-full-virtual-desktop path. Per-session capture variance (e.g., capturing only one monitor when single-mode is active) would require re-running the fixture per topology and double the capture-side surface area. Server-side crop preserves the existing capture invariant.

**Example shape (Linux, BGRA — sketch only, planner picks the exact API):**
```python
# server/screen_capture.py — extended
def capture_raw_bgra_with_crop(self, crop: Optional[Tuple[int, int, int, int]]) -> bytes:
    raw = self.capture_raw_bgra()  # full virtual desktop, unchanged
    if crop is None:
        return raw  # mirror-all path
    x, y, w, h = crop
    # Reshape, slice, contiguous-copy. NumPy view is fine on hot path
    # because the encoder needs a tight buffer anyway.
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(self.height, self.width, 4)
    return arr[y:y+h, x:x+w].tobytes()
```

**For 10-bit P010 path (Mac VTCompressionSession + Linux NvFBC YUV420P10LE):** crop must operate on Y plane and UV plane separately because they're different resolutions (UV is half horizontal AND vertical). Test fixture: extend Phase 2 ten_bit_pipeline.py to verify a crop region preserves 10-bit fidelity (no chroma stride bugs).

### Pattern 2: Wire-format physical-pixel coords with backwards-compat (D-05)

**What:** Add `server_x: int, server_y: int` fields to input message dataclasses. Keep existing normalized fields as compat shim during the transition.

**Example:**
```python
# common/messages.py — extended
@dataclass
class PenEventMsg:
    type: str = MsgType.PEN_EVENT
    # Phase 1/2 normalized fields — kept for back-compat with older servers
    x: float = 0.0
    y: float = 0.0
    # Phase 3 D-05 — server physical pixel coords; preferred when present
    server_x: int = -1
    server_y: int = -1
    pressure: float = 0.0
    # ... existing fields unchanged ...
```

Server prefers integer fields when both are present and `server_x >= 0`. Sentinel `-1` keeps the existing-zero default unambiguous.

### Pattern 3: SCStreamDelegate push hot-plug + poll safety net (D-11)

**What:** Subscribe to `SCStreamDelegate` callback for display configuration changes. Keep the existing 5s poll as a safety net.

**Why dual-path:** SCK push callback can be missed if the delegate object is GC'd or if the runloop is suspended (e.g., during long synchronous capture init). Poll catches the case the push misses.

**Implementation choice (Claude's discretion per D-11):** Either extend `_StreamOutputHandler` to also conform to `SCStreamDelegate` selectors, or write a thin separate wrapper class. Recommend the separate wrapper for testability — `_StreamOutputHandler` is already tightly coupled to the frame-capture path; adding hot-plug responsibility makes the unit tests harder to focus.

**Selectors of interest:** ScreenCaptureKit's `SCStream` delegate protocol exposes `stream:didStopWithError:` (already used for error handling). For display reconfig, the relevant signal is `SCShareableContent` re-enumeration on display change — the canonical pattern is to call `SCShareableContent.getShareableContentWithCompletionHandler_` from inside an `NSWorkspace` `didChangeScreenParametersNotification` observer. [CITED: Apple ScreenCaptureKit programming guide]

### Pattern 4: Per-bookmark + in-session toolbar override (D-15, mirrors P2 D-10)

**What:** Bookmark stores defaults; toolbar surface lets the user flip in-session. Phase 2 established this pattern for Cmd↔Ctrl swap (`destination_kind` + `swap_cmd_ctrl`); Phase 3 replicates the shape for the 4 clipboard toggles.

**Example:**
```python
# common/messages.py ConnectionProfile — extended
@dataclass
class ConnectionProfile:
    # ... existing fields ...
    # Phase 3 D-15 — per-direction clipboard toggles
    clipboard_text_c2s: bool = True
    clipboard_text_s2c: bool = True
    clipboard_image_c2s: bool = True
    clipboard_image_s2c: bool = True
    # Phase 3 D-01 / D-04 — monitor mode + picked monitor by id+name
    monitor_mode: str = "single"  # "single" | "mirror" | "pick"
    picked_monitor_id: int = -1
    picked_monitor_name: str = ""
```

Migration: `BookmarkManager._load` extends the Phase 2 D-10 `_migrate_swap_default` pattern to fold in defaults for any of these 6 fields when missing from saved JSON.

### Anti-Patterns to Avoid

- **Per-session capture variance.** Do NOT change `ScreenCapture.__init__` based on capture_mode. Always-full-virtual-desktop + crop preserves Phase 2 fixture. Locked by D-02.
- **Calling `xrandr --mode` / `--addmode` / any set-operation** on a running NVIDIA Xorg. Causes server segfault — confirmed by NVIDIA forum bug + CONCERNS.md. Read-only hot-plug only. Locked by D-10.
- **Putting clipboard image bytes through the existing video send_queue.** Clipboard chunks need their own bounded queue slot per STAB-07. Locked by D-17.
- **JPEG as a second clipboard image format.** One format, one codepath. Locked by D-13. Defer to v1.1.
- **Synthetic keycode injection for pasted text** (anti-pattern from Phase 2 PenFSM lessons). Use the existing `text_commit` path if pasting bypasses the clipboard system call.
- **Mid-session mode switch via UI.** Locked by D-03. Disconnect-and-reconnect is the only path.
- **Allowing the F12 overlay to consume mouse events.** Use `WA_TransparentForMouseEvents`. UI-SPEC explicit.
- **Silent fallback when picked monitor vanishes.** D-09 requires explicit toast + banner. Server auto-falls-back, but the user MUST see the change.
- **Polling rate increase to "fix" hot-plug detection latency.** Add the SCK push callback (D-11) instead of dropping the 5s poll cadence. The poll is the safety net; the push is the fast path.

---

## Standard Stack

### Core (already locked from Phase 1/2 — no new top-level deps)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PySide6 | >=6.10 | Qt6 GUI; QScreen.devicePixelRatio per-screen API; QRhiWidget Metal 10-bit blit (Phase 2 D-02 baseline) | Locked Phase 1; only toolkit with full QTabletEvent + QRhiWidget |
| PyObjC ScreenCaptureKit | (system PyObjC) | macOS SCK capture + SCStreamDelegate push callback for hot-plug | Phase 2 baseline; SCK is Apple's modern capture path |
| python-xlib | >=0.33 | xrandr-query read for monitor enumeration; existing path | Already used for XDamage (Phase 1) |
| mss | >=9.0 | Linux fallback monitor enumeration when xrandr-query unavailable | Already in stack; per-monitor list via `_sct.monitors` |
| websockets | >=15.0,<16 | Control channel for clipboard chunk transport (D-17) | Phase 1 baseline |
| structlog | >=25.1,<26 | `event=monitor_hotplug` / `event=clipboard_image` / `event=clipboard_chunk` JSON telemetry | Phase 1 OBS-01 |

**Verified versions:** All from `requirements-server.txt` and `requirements-client.txt` head of tree. No version bumps needed for Phase 3. [VERIFIED: requirements-server.txt, requirements-client.txt, pyproject.toml]

### Supporting (cross-platform clipboard adjuncts)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Pillow | >=10.0 | PNG magic-byte validation, optional decode/re-encode for size cap enforcement | Already in `requirements-server.txt`; reuse for `Image.open(io.BytesIO(...)).verify()` |
| (system) xclip / xsel | (system) | `-t image/png` flag for X11 clipboard image set/get | Already detected by `ClipboardSync._find_clipboard_tool` |

**xclip image/png support:** xclip 0.13+ supports `-t image/png` natively. xsel does NOT support binary clipboard targets — confirm xclip availability and surface explicit error when only xsel is present. [CITED: xclip(1) man page; xsel(1) man page]

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Server-side GPU crop (D-02) | Client-side crop on full frame | Client-crop wastes WAN bandwidth on dropped pixels; bandwidth savings dominate latency on Tailscale paths. **Locked: server-side per D-02.** |
| Single image clipboard format (PNG) | PNG + JPEG negotiation | Two codepaths, two test surfaces, two compression artifact profiles — JPEG lossy is unacceptable for VFX reference per D-13. **Locked: PNG only per D-13.** |
| 1MB chunk size for clipboard (D-17) | 64KB or 256KB chunks | Smaller chunks = more interleave granularity but more JSON overhead per chunk. 1MB strikes the balance per D-17; revisit only if measurement shows head-of-line. **Locked per D-17.** |
| Clipboard chunked control channel | Side-channel via existing `file_transfer.py` infra | More implementation surface; v1.1 fallback if D-17 measurement fails. **Locked: control channel per D-17.** |
| Push-only SCK hot-plug | Push + 5s poll safety net | Push-only loses changes during runloop suspension. **Locked: dual-path per D-11.** |

**Installation:** No new deps. Validate `xclip` is installed:
```bash
which xclip || echo "WARNING: xclip required for image clipboard on Linux server"
```

**Version verification:** Run `npm view`-equivalent for Python:
```bash
pip show PySide6 | grep Version  # expect >=6.10
pip show pyobjc-framework-ScreenCaptureKit | grep Version  # macOS server
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| PNG magic-byte validation | Custom byte-comparison code | `PIL.Image.open(io.BytesIO(payload)).verify()` AND first-8-byte check | PIL handles malformed-but-not-truncated PNGs that pass the magic-byte check; defense-in-depth |
| Multi-monitor enumeration on macOS | CGGetActiveDisplayList wrappers | `SCShareableContent.getShareableContentWithCompletionHandler_().displays()` | SCK is the modern path; CGGetActiveDisplayList still works but loses HDR / colorspace info |
| Multi-monitor enumeration on Linux | xrandr binary parsing | Existing `detect_monitors_xrandr()` in `screen_capture.py` lines 75-136 | Already in tree; integrates with existing `MonitorInfo` dataclass |
| Hot-plug detection on macOS | Polling SCShareableContent every frame | `SCStreamDelegate` push callback + 5s safety poll (D-11) | Push is dramatically lower latency; poll catches the case push misses |
| Per-screen DPR computation | Manual DPR-from-physical-and-logical-size math | `QScreen::devicePixelRatio()` looked up via `QWidget::screen()` | Qt 6 handles per-screen DPR correctly when probed via the widget's current screen |
| screenChanged event detection | Polling `QApplication.screens()` | `QWindow::screenChanged` Qt signal | Qt fires this on the GUI thread when the window's screen affiliation changes; canonical |
| PNG decode for size validation | Reading entire payload to compute size | `len(base64.b64decode(payload))` after first chunk; compare against 64MB cap before continuing chunked receive | Cheap; lets us reject oversize before allocating the assembly buffer |
| Bounded queue with overflow policy | Custom asyncio.Queue subclass | `asyncio.Queue(maxsize=...)` + `qsize()` check + drop-policy in caller | Phase 1 STAB-07 already established the pattern in `server/pipelines.py` |
| Clipboard newline preservation | Custom CRLF→LF normalizer | Send/receive raw bytes; let each OS clipboard consumer normalize | D-16 explicit; X11 and NSPasteboard each have their own conventions |
| EDID generation | Custom binary builder | Existing `server/session_manager.py::_generate_edid` | Already produces valid EDID 1.3; for DISP-04 just pick a Flame-approved profile name |
| Banner/toast widget | Custom QFrame from scratch | UI-SPEC C-05 / C-09 spec exact dimensions + states; planner produces minimal QFrame impl | UI-SPEC is the design contract; planner consumes it |

**Key insight:** Phase 3 is mostly extension of existing patterns from Phase 1 (FSM, bounded queues, structlog telemetry, mock-at-OS-boundary tests) and Phase 2 (per-bookmark + in-session toolbar pattern). Resist the urge to build new abstractions; the existing ones are the load-bearing decisions.

---

## Runtime State Inventory

> Phase 3 is a brownfield extension phase, not a rename/refactor phase. The full inventory does not apply, but two categories of stored state need explicit handling.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data — bookmarks | `~/Library/Application Support/Teraguchi/bookmarks.json` (Mac), `~/.config/teraguchi/bookmarks.json` (Linux). Will gain 6 new fields: `monitor_mode`, `picked_monitor_id`, `picked_monitor_name`, `clipboard_text_c2s`, `clipboard_text_s2c`, `clipboard_image_c2s`, `clipboard_image_s2c` | **Migration code** in `BookmarkManager._load` extends the Phase 2 D-10 pattern: detect missing fields, apply policy defaults, save. Existing tests in `tests/client/test_bookmarks.py` need to assert migration succeeds. |
| Stored data — server-side EDID | `server/edid/*.bin` (generated by `_generate_edid`), `/usr/share/pcoip-agent/1024x768.bin` if PCoIP installed | DISP-04 picks Flame-approved profile name; existing generator stays. No data migration. |
| Live service config | None — Teraguchi server is stateless across restarts; sessions are in-memory `SessionRuntime` instances | None |
| OS-registered state | LaunchAgent `com.dxs.teraguchi.server` (Mac), systemd `teraguchi-server.service` (Linux). Phase 3 makes no change to either. | None |
| Secrets/env vars | `TERAGUCHI_DEBUG=1` is a NEW env var introduced by D-07 for the F12 overlay. Documented at intro time; no existing code depends on it. | Document in `docs/release.md` debug-mode section |
| Build artifacts / installed packages | `dist/Teraguchi.app/`, `dist/teraguchi-server-*.rpm` (Phase 6 artifacts) — not Phase 3 scope. PyInstaller bundle includes `client/coord_debug_overlay.py` + `client/remap_banner.py` + `client/toasts.py` + `client/clipboard_toggle_menu.py` automatically because `build_client.py` pulls all of `client/`. | None — verify Phase 3 new files appear in `dist/Teraguchi/` after `python build_client.py` |

---

## Common Pitfalls

### Pitfall 1: Mixed-DPI cursor offset on Cintiq Pro 24 + Retina internal client

**What goes wrong:** Cursor lands N pixels left of where the artist clicked when the pen is on the external 4K Cintiq but the viewer window's DPR is computed from the Retina internal screen. Off-by-1 on Cintiq corner clicks during the D-08 spike.

**Why it happens:** `QScreen::devicePixelRatio()` is per-screen on macOS, but if the math reads `QApplication.primaryScreen().devicePixelRatio()` it always uses the primary's DPR even when the viewer window has migrated to the external 4K. Mozilla bug #794038 documented this exact failure mode for mixed-DPI Mac.

**How to avoid:** D-06 — always look up `QWidget::screen().devicePixelRatio()` for the widget's CURRENT screen at event time. Re-evaluate cached scaling on `QWindow::screenChanged` signal. The PITFALLS doc explicitly calls this out.

**Warning signs:** D-08 4-corner spike fails on the Cintiq corners but passes on the Retina corners. F12 overlay shows DPR=2.0 when the viewer is on the 1.0-DPR external monitor.

[CITED: Mozilla bz #794038, Qt 6 HighDPI documentation]

### Pitfall 2: Pen pressure lost across screenChanged on mixed-DPI

**What goes wrong:** Wacom pen pressure round-trips fine on the Retina screen but on the external Cintiq the recorded pressure is quantized to 8 levels instead of 1024.

**Why it happens:** Not a Phase 3 bug per se, but Phase 3 is the first time the pen path is exercised across screen-affiliation changes. The PenFSM proximity re-synth on focusIn/showEvent (Phase 2 D-19) may interact with `screenChanged` in ways not yet tested.

**How to avoid:** Add a `screenChanged` re-synth path symmetric to D-19's focusIn re-synth: when the viewer migrates between screens with the pen in proximity, re-emit a `PenProximityMsg` so the server's PenFSM doesn't think the pen device just changed. Test fixture: synthesize a `screenChanged` event in `tests/client/test_viewer_proximity.py` and assert the proximity msg is re-emitted.

### Pitfall 3: NVIDIA Xorg segfaults on monitor disconnect

**What goes wrong:** Server process dies entirely when a monitor is unplugged from the NVIDIA-driven Rocky machine. Sessions for ALL users are killed, not just the one that was viewing the disappearing monitor.

**Why it happens:** Confirmed by NVIDIA forum bug filed under driver versions 580.x; reproduces on RTX 5090 and other Blackwell GPUs. The xrandr disconnect event triggers a SIGSEGV inside the NVIDIA driver's monitor-removal path.

**How to avoid:** D-10. Phase 3 hot-plug handler is **read-only**: queries `xrandr --query` for the new state, NEVER calls `xrandr --mode` / `--addmode` / `--output --off` / any set operation. The existing `RESIZE_REQUEST` stub at `server/main.py:548-552` STAYS stubbed. Documented in `docs/release.md` as a known v1 limitation.

**Warning signs:** Server process disappears from the systemd journal mid-session with `signal SIGSEGV` and the only stack frame is inside `libnvidia-glcore.so` or `libnvidia-eglcore.so`.

[CITED: NVIDIA developer forum xorg-sigsegv-nvidia-drm-warning-on-hdmi-hotplug-disconnect]

### Pitfall 4: SCK silently delivers stale frame-size after display reconfig

**What goes wrong:** macOS server's `MacScreenCapture._latest_size` says the display is 3840×2160 but SCK is now actually delivering 5120×2880 frames after the user changed display resolution from System Settings. Encoder feeds a wrong-sized buffer to NVENC and produces corrupt H.265.

**Why it happens:** Today's `MacScreenCapture.detect_hotplug` only checks count + WxH; same WxH at a different resolution scaling factor (Retina rebuild) doesn't trigger a reinit.

**How to avoid:** D-11. Full signature `(displayID, width, height, x, y)` per display + `SCStreamDelegate` push callback for change events. The push callback fires before the next frame arrives, giving the reinit a window to complete.

**Warning signs:** Mid-session display setting change in System Settings → Color shifts or block artifacts in client video. SCK frame size != configured stream size.

### Pitfall 5: Clipboard image roundtrip strips alpha channel

**What goes wrong:** Artist copies a Photoshop screenshot with a transparent background; pastes on the remote and the transparent area is now solid black.

**Why it happens:** xclip's `-t image/png` path can negotiate a different MIME type (e.g., `image/x-portable-pixmap`) if the receiving X11 client requests it; some legacy receive paths drop alpha. NSPasteboardTypePNG preserves alpha but the wire format must.

**How to avoid:** Always send PNG bytes verbatim — base64 the original PNG, never decode-and-re-encode unless you must enforce the size cap. Add an alpha-preservation test fixture: a 4-channel PNG with known transparency, copy → wire → paste, assert byte-equal payload.

**Warning signs:** Visual inspection: artist reports lost transparency. Test: round-trip a known PNG and `hashlib.sha256` compare in/out.

### Pitfall 6: Clipboard chunk reorder corrupts assembly

**What goes wrong:** Receiver gets `chunk_index=2` before `chunk_index=1` (rare on a single websocket but possible with future QUIC multiplexing) and assembles garbage.

**Why it happens:** D-17 says chunks have `sequence_id, chunk_index, total_chunks` — the assembler MUST index by `chunk_index` into a sized slot, not append in arrival order.

**How to avoid:** Build a chunk-assembler abstraction: `ClipboardChunkAssembler(sequence_id, total_chunks)` with `add(chunk_index, data) -> Optional[full_payload]`. Returns `None` until all chunks present, then returns the assembled bytes. Add unit tests for in-order, out-of-order, duplicate-chunk, and missing-chunk-timeout cases.

**Warning signs:** Unit tests skip out-of-order; first user with packet reorder loses all clipboard images.

### Pitfall 7: Per-direction toggle race with in-flight clipboard message

**What goes wrong:** Artist toggles c2s OFF in the toolbar menu while a clipboard set is mid-chunked. Receiver enforces toggle on each chunk and drops chunks 5..N, leaving an incomplete assembly half-resident in memory.

**Why it happens:** D-15 says toggles take effect immediately on the next clipboard event. "Next event" is ambiguous when chunked transport is mid-stream.

**How to avoid:** Toggle gating MUST be at message-START boundary, not per-chunk. Once a chunk-stream has begun (chunk_index=0 received), the assembler completes. Any subsequent toggle change applies to the next sequence_id. Document this in the per-direction-toggle policy.

**Warning signs:** Memory leak in the chunk assembler; user reports clipboard "stuck" after a fast toolbar toggle during a paste.

### Pitfall 8: F12 overlay leaks into release builds

**What goes wrong:** Production user accidentally hits F12, sees a developer overlay full of internal coord math, files a bug report.

**Why it happens:** D-07 gates on `TERAGUCHI_DEBUG=1` env var, but if the keyPressEvent handler is wired before the env-var check, F12 still consumes the event in release builds (no UI but also no F12 passthrough to system shortcuts).

**How to avoid:** Gate handler installation, not just visibility. In `RemoteViewer.__init__`, only register the F12 keyPressEvent override if `os.environ.get("TERAGUCHI_DEBUG") == "1"`. Add a unit test that asserts F12 is a no-op in non-debug mode.

### Pitfall 9: Bookmark migration data-loss on pre-v3 bookmarks

**What goes wrong:** A user upgrades from Phase 2-shipped Teraguchi to Phase 3-shipped Teraguchi. Their existing bookmarks (which never had monitor_mode / clipboard toggles) get loaded but the migration is incomplete — fields exist on the dataclass but defaults aren't sensible.

**Why it happens:** Phase 2 D-10 had a similar trap with `swap_cmd_ctrl`; the fix was the explicit `_migrate_swap_default` path in `BookmarkManager._load`. Phase 3 must replicate this pattern.

**How to avoid:** Extend the Phase 2 migration block to detect missing `monitor_mode` (default `"single"` per most-conservative-bandwidth) and missing clipboard toggles (default ON per D-15/16). Always re-save after migration. Add unit test that loads a Phase-2-shape bookmark JSON and asserts the migrated profile has all 6 new fields with policy-correct defaults.

---

## Code Examples

Verified patterns from official sources + existing codebase.

### Example 1: QScreen DPR per-window lookup (D-06)

```python
# client/viewer.py — within tabletEvent / mouseMoveEvent handlers
from PySide6.QtGui import QScreen

def _current_screen_dpr(self) -> float:
    """Return the DPR of the QScreen the viewer widget is currently on.

    D-06: always look up the CURRENT screen's DPR — never cache the
    primary's. Re-evaluated on QWindow.screenChanged.
    """
    win = self.window().windowHandle()
    if win is None:
        return float(self.devicePixelRatioF())
    screen = win.screen()
    if screen is None:
        return float(self.devicePixelRatioF())
    return float(screen.devicePixelRatio())

# Connect screenChanged in __init__:
def _connect_screen_changed(self):
    win = self.window().windowHandle()
    if win is not None:
        win.screenChanged.connect(self._on_screen_changed)

def _on_screen_changed(self, screen):
    """Recompute scaling cache when the viewer migrates between screens."""
    self._update_scaling()
    # D-06 + Phase 2 D-19 — re-emit pen proximity if the pen was in range
    # so the server PenFSM doesn't lose state across the screen change
    if getattr(self, "_pen_was_in_proximity", False):
        self.pen_proximity.emit({
            "in_proximity": True,
            "pen_type": self._last_pen_type,
        })
```

[CITED: Qt 6 HighDPI docs, QScreen / QWindow API reference]

### Example 2: PNG magic-byte validation (D-16)

```python
# server/clipboard.py / server/mac_clipboard.py — receive path
import io
from PIL import Image

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_MAX_BYTES = 64 * 1024 * 1024  # D-14 — 64 MB cap

def validate_png_payload(payload: bytes) -> bool:
    """Defense-in-depth PNG payload validation per D-16.

    Returns True iff payload is a well-formed PNG within the size cap.
    Logs a structlog warning and returns False on any failure.
    """
    if len(payload) > PNG_MAX_BYTES:
        logger.warning("clipboard.image_oversize",
                       size=len(payload), cap=PNG_MAX_BYTES)
        return False
    if len(payload) < 8 or payload[:8] != PNG_MAGIC:
        logger.warning("clipboard.image_bad_magic",
                       prefix=payload[:8].hex())
        return False
    try:
        Image.open(io.BytesIO(payload)).verify()
    except Exception as e:
        logger.warning("clipboard.image_pil_verify_failed", error=str(e))
        return False
    return True
```

### Example 3: Clipboard chunk assembler (D-17)

```python
# common/clipboard_chunks.py — NEW module
from dataclasses import dataclass, field
from typing import Optional, Dict

CHUNK_TIMEOUT_S = 30.0  # drop incomplete sequences after 30s

@dataclass
class ClipboardChunkAssembler:
    """D-17 chunk-reassembly state.

    Indexed by chunk_index (NOT arrival order) so out-of-order delivery
    over future QUIC multiplexed streams works. Returns the full payload
    once all chunks present; None until then.
    """
    sequence_id: int
    total_chunks: int
    content_type: str
    _slots: Dict[int, bytes] = field(default_factory=dict)
    _started_at: float = 0.0

    def add(self, chunk_index: int, data: bytes) -> Optional[bytes]:
        if chunk_index in self._slots:
            return None  # duplicate; idempotent ignore
        if not (0 <= chunk_index < self.total_chunks):
            return None  # out-of-range; drop
        self._slots[chunk_index] = data
        if len(self._slots) == self.total_chunks:
            return b"".join(self._slots[i] for i in range(self.total_chunks))
        return None

    def is_stale(self, now: float) -> bool:
        return (now - self._started_at) > CHUNK_TIMEOUT_S
```

### Example 4: SCStreamDelegate display-change subscription (D-11)

```python
# server/mac_screen_capture.py — sketch (planner picks final shape)
# Subscribe to NSWorkspace's didChangeScreenParametersNotification because
# SCStreamDelegate itself doesn't expose a "screen list changed" callback —
# the canonical pattern is workspace observer → call SCShareableContent.
from AppKit import NSWorkspace, NSWorkspaceDidChangeScreenParametersNotification

class _DisplayChangeObserver(NSObject):
    def initWithCapture_(self, capture):
        self = objc.super(_DisplayChangeObserver, self).init()
        if self is None:
            return None
        self._capture = capture
        return self

    def screenParametersChanged_(self, notification):
        # Set a flag for MonitorHotplug poll to pick up immediately,
        # rather than waiting for the next 5s tick.
        self._capture._hotplug_pending = True

# In MacScreenCapture.__init__:
self._hotplug_pending = False
nc = NSWorkspace.sharedWorkspace().notificationCenter()
self._display_observer = _DisplayChangeObserver.alloc().initWithCapture_(self)
nc.addObserver_selector_name_object_(
    self._display_observer,
    "screenParametersChanged:",
    NSWorkspaceDidChangeScreenParametersNotification,
    None,
)

# In monitor_hotplug.py — poll loop checks the flag in addition to the
# 5s cadence:
async def run(self) -> None:
    while self._running:
        await asyncio.sleep(1.0)  # tighter cadence; was 5.0
        runtime = self._runtime
        pending = getattr(runtime.capture, "_hotplug_pending", False)
        if pending or runtime.capture.detect_hotplug():
            runtime.capture._hotplug_pending = False
            # ... existing broadcast + restart logic ...
```

[CITED: Apple NSWorkspace API reference; ScreenCaptureKit programming guide]

### Example 5: Server-side BGRA crop (D-02)

```python
# server/screen_capture.py — extension
from typing import Optional, Tuple
import numpy as np

def capture_raw_bgra_with_crop(
    self, crop: Optional[Tuple[int, int, int, int]] = None
) -> bytes:
    """D-02 — full-virtual-desktop capture + optional GPU-side crop.

    crop is (x, y, w, h) in server physical pixels, or None to pass through.
    Validates the crop fits inside the captured frame; clamps if not.
    """
    raw = self.capture_raw_bgra()  # unchanged Phase 2 path
    if crop is None:
        return raw
    x, y, w, h = crop
    # Clamp to capture bounds (defense against stale crop after hot-plug)
    x = max(0, min(x, self.width))
    y = max(0, min(y, self.height))
    w = max(0, min(w, self.width - x))
    h = max(0, min(h, self.height - y))
    if w == 0 or h == 0:
        # Degenerate; return a single black pixel instead of crashing
        return b"\x00\x00\x00\xff"
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(self.height, self.width, 4)
    return arr[y:y+h, x:x+w].tobytes()
```

For the 10-bit P010 path (NvFBC YUV420P10LE on Linux, VTCompressionSession on Mac), the crop must be applied separately to Y and UV planes — UV is half-resolution in both dimensions for 4:2:0 P010. The Y plane crop is straightforward; UV crop must round x/y down to even pixels and w/h to even pixels to preserve 4:2:0 alignment.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Normalized 0.0-1.0 cursor coords | Server physical pixel integers (D-05) | Phase 3 lock | Eliminates float-precision artifacts on 10K-wide virtual desktops |
| Mac SCK shallow hot-plug check (count + WxH) | Full (id, w, h, x, y) tuple + push callback (D-11) | Phase 3 lock | Catches reorder + same-size swap + reposition that today silently miss |
| Polled-only hot-plug (5s) | Push + poll dual-path (D-11) | Phase 3 lock | Sub-second hot-plug response on Mac; poll stays as safety net |
| Text-only clipboard | Text + PNG image (D-13) | Phase 3 lock | Closes a PCoIP feature-parity gap; image transfer for paint reference |
| Single global clipboard policy | Per-bookmark + in-session toolbar (D-15) | Phase 3 lock | Enables mid-session privacy reveal during client screen-sharing |
| Single coord space normalize/denormalize | Hybrid wire (compat normalized + new server_x/y) | Phase 3 transition | Backwards-compat with older servers; new path preferred when present |

**Deprecated/outdated:**

- **Synthesized keycode for clipboard text paste** — never the path; use system clipboard set-and-notify
- **JPEG as a second clipboard image format** — explicitly out of scope per D-13
- **Mid-session capture-mode switch** — explicitly out of scope per D-03
- **xrandr set-operations on a running NVIDIA Xorg** — explicitly forbidden per D-10
- **Window-straddling-two-screens DPR correctness** — documented v1 limitation per D-06; Qt picks one DPR

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | xclip 0.13+ supports `-t image/png` natively on Rocky 9 | Standard Stack — Supporting | Phase 3 image clipboard on Linux side breaks; need alternate tool (xsel does NOT support it) |
| A2 | NSWorkspaceDidChangeScreenParametersNotification fires reliably on macOS Sonoma + Sequoia for display add/remove/reorder/resize | Code Example 4 | Hot-plug push callback misses some change types; falls back to 5s poll (acceptable degradation) |
| A3 | SCStreamDelegate `stream:didStopWithError:` does NOT need to re-fire on display change (we use the workspace observer instead) | Code Example 4 | If SCK auto-stops the stream on display change, we need additional reinit handling |
| A4 | NumPy slice + `.tobytes()` on a BGRA frame is fast enough (sub-millisecond at 4K) for the D-02 crop hot path | Pattern 1 | Crop adds visible latency; need C extension or GPU shader path for crop |
| A5 | The `MonitorListMsg` already-broadcast path's existing `MonitorInfo` dataclass (id/name/w/h/x/y/primary/scale) is sufficient field-wise for D-05 cursor math | Code Example 1 | Need to extend `MonitorInfo` with `physical_x/y/w/h` if the existing fields are logical-pixel rather than physical-pixel on macOS |
| A6 | Phase 1's send_queue is the input/output queue for video; clipboard chunks need a NEW separate bounded queue | Pattern 4 / Pitfall 7 | If clipboard shares the video queue, head-of-line blocking returns even for 1MB chunks |
| A7 | Eizo CG279X is a Flame-approved monitor profile name suitable for CustomEDID | DISP-04 (Claude's Discretion) | Flame's monitor-config dialog rejects the chosen profile; planner picks an alternative from Autodesk's Flame system requirements list |
| A8 | The 4-corner spike (D-08) can be performed in a single 1-day DXS lab session with Cintiq Pro 24 + Retina MBP + Rocky NVIDIA workstation | Wave 2 timing | Spike runs over multiple sessions; gate slips |
| A9 | macOS NSPasteboard `setData:forType:` with NSPasteboardTypePNG round-trips PNG bytes verbatim (no re-encode) | Pitfall 5 | Alpha channel or chunked-IDAT layout differs after round-trip; need to test with a known fixture |

**Action for planner:** Each `[ASSUMED]` claim above needs verification during implementation, ideally via test fixture. A1 and A7 are highest-risk; verify A1 in Wave 0 (5-minute check on Rocky 9 vm), verify A7 by attempting the EDID profile against an actual Flame install if possible.

---

## Open Questions (RESOLVED)

1. **Should `SessionConfigureMsg` be a new message or a `ClientHelloMsg` extension?**
   - What we know: D-02 says "extended `ClientHelloMsg` or a new SESSION_CONFIGURE message (planner decides shape)".
   - What's unclear: ClientHello is fired once at connect; if we ever want to support reconnect-with-different-mode (v1.1), `SessionConfigureMsg` is the cleaner shape. But adding a new message to v1 raises the protocol surface.
   - RESOLVED: Extend `ClientHelloMsg` for v1 (simpler, smaller surface). Convert to a separate `SessionConfigureMsg` in v1.1 only if reconnect-with-mode-change is requested.

2. **CRLF preservation guarantee on macOS clipboard.**
   - What we know: D-16 says "preserve native CRLF/LF/CR; never silently transform".
   - What's unclear: NSPasteboard's `setString_forType_` may itself normalize line endings. PIL Pillow likely does too on read.
   - RESOLVED: Use raw byte set/get on the pasteboard side (`setData_forType_` with NSData + NSPasteboardTypeString), not the high-level `setString_forType_`. Add a fixture that round-trips a known CRLF/LF/CR mix.

3. **Should the F12 overlay show a "Wave 2 spike pass" indicator?**
   - What we know: D-08 captures the corner-click result in `docs/release.md`.
   - What's unclear: It might be useful for the dev overlay to show a per-corner pass/fail state during the spike.
   - RESOLVED: Out of scope for v1 — the spike is a one-time gate, not an ongoing measurement. F12 overlay shows live coord state only.

4. **macOS chunked clipboard receive: how do we surface a stalled assembly to the user?**
   - What we know: D-17 says clipboard chunks get their own queue slot; CHUNK_TIMEOUT_S = 30s drops incomplete sequences.
   - What's unclear: Should the user see a "clipboard image transfer failed" toast?
   - RESOLVED: structlog warning + silent drop for v1. Add toast surfacing if user reports surface-able.

5. **Does the GPU crop path (D-02) need to be CPU NumPy slice or actual GPU shader?**
   - What we know: D-02 calls it "GPU crop" but the codebase is Python NumPy + FFmpeg subprocess.
   - What's unclear: At 4K @ 60fps, NumPy slice + .tobytes() is ~2-4 ms. Acceptable budget?
   - RESOLVED: Start with NumPy slice (Pattern 1). If D-18 latency gate fails, consider passing crop to FFmpeg via `-vf crop=W:H:X:Y` filter inside the existing subprocess (avoids a Python-side copy entirely).

6. **What should happen if pick-one's bookmarked monitor exists by ID but a different monitor now has that name?**
   - What we know: D-04 says "fall back to primary if neither id nor name matches".
   - What's unclear: ID matches but name doesn't (rare; name-by-position changed but ID is stable). Trust ID or trust name?
   - RESOLVED: ID first (more stable); log a warning if names diverge so we can detect environment drift.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| xclip | Linux server image clipboard (D-13) | Likely (existing requirement) | 0.13+ for image/png | xsel does NOT support image; surface explicit error |
| xrandr | Linux monitor enumeration (existing) | ✓ | system | mss fallback (existing) |
| Python xlib | XDamage (existing Phase 1) | ✓ | >=0.33 | Already required |
| pyobjc-framework-ScreenCaptureKit | macOS server SCK push hot-plug (D-11) | ✓ | system | None — Phase 2 baseline |
| pyobjc-framework-AppKit | NSWorkspace observer for display change (Code Example 4) | ✓ | system | None — Phase 2 baseline |
| Pillow (PIL) | PNG magic-byte validation (D-16) | ✓ | >=10.0 | Already required (server) |
| PySide6 6.10+ QScreen.devicePixelRatio per-screen | Per-screen DPR (D-06) | ✓ | >=6.10 | Locked Phase 1 |
| NSPasteboardTypePNG (AppKit) | Mac image clipboard (D-13) | ✓ | system | None |

**Missing dependencies with no fallback:** None at design time. **Verify in Wave 0:**
1. `xclip --version` on Rocky 9 reference system; confirm `>= 0.13`.
2. `python3 -c "from AppKit import NSWorkspaceDidChangeScreenParametersNotification; print('ok')"` on macOS server (Sonoma + Sequoia).

**Missing dependencies with fallback:** None — Phase 3 builds on already-installed Phase 1/2 baselines.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 7.0+ (already in `requirements-dev.txt`); pytest-asyncio auto mode (Phase 1 STAB-01) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]`; existing markers: `latency_bench`, `smoke_1h`, `flame_critical`, `wacom_hw`, `ten_bit_smoke`, `gpu` |
| Quick run command | `pytest -m "not wacom_hw and not gpu and not smoke_1h and not latency_bench" -x` |
| Full suite command | `pytest` (default `-m 'not wacom_hw'`) |

**New markers needed for Phase 3:** None. Existing markers cover the deselect needs (gpu for the D-02 crop test if it requires real NVENC output, ten_bit_smoke for the crop-preserves-10-bit fixture extension).

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DISP-01 | ModeSelector emits correct (mode, picked_id, name) tuple for each radio choice | unit | `pytest tests/client/test_connect_dialog_mode.py -x` | ❌ Wave 0 |
| DISP-01 | Bookmark stores last-used mode and reloads | unit | `pytest tests/client/test_bookmarks.py::test_monitor_mode_persistence -x` | ⚠ extend |
| DISP-01 | Mode badge renders correct text per active session | unit | `pytest tests/client/test_fullscreen_toolbar_mode_badge.py -x` | ❌ Wave 0 |
| DISP-02 | Mocked mss hot-plug fires `MonitorListMsg` broadcast | unit | `pytest tests/server/test_screen_capture_hotplug.py -x` | ❌ Wave 0 |
| DISP-02 | In-process loopback: monitor-gone mid-session → client receives MonitorListMsg, FSM stays streaming | integration | `pytest tests/integration/test_monitor_hotplug.py -x` | ❌ Wave 0 |
| DISP-03 | `_widget_to_remote` produces correct server physical pixels for fixture mixed-DPI topology | unit | `pytest tests/common/test_cursor_math.py -x` | ❌ Wave 0 |
| DISP-03 | 4-corner click test on real Cintiq Pro 24 + Retina + 2×2560×1600 NVIDIA Xorg | manual-only | DXS lab spike captured in `docs/release.md` | manual |
| DISP-04 | CustomEDID profile name advertises Flame-approved monitor model | unit | `pytest tests/server/test_session_manager_edid.py::test_flame_profile -x` | ❌ Wave 0 |
| DISP-05 | `screenChanged` signal triggers cache recompute + scaling update | unit | `pytest tests/client/test_viewer_screen_changed.py -x` | ❌ Wave 0 |
| DISP-05 | Per-screen DPR lookup uses widget's current screen, not primary | unit | `pytest tests/client/test_viewer_dpr.py -x` | ❌ Wave 0 |
| DISP-06 | Mocked SCK display change triggers detect_hotplug + push callback path | unit | `pytest tests/server/test_mac_screen_capture_hotplug.py -x` | ❌ Wave 0 |
| DISP-07 | Pick-one mode + bookmarked monitor exists → server crops to that monitor | integration | `pytest tests/integration/test_capture_mode.py -x` | ❌ Wave 0 |
| DISP-07 | Pick-one mode + bookmarked monitor missing → server falls back to primary + emits toast | integration | `pytest tests/integration/test_capture_mode.py::test_pick_fallback -x` | ❌ Wave 0 |
| CLIP-01 | Bidirectional text >1MB round-trip preserves CRLF | integration | `pytest tests/integration/test_clipboard_text_large.py -x` | ❌ Wave 0 |
| CLIP-01 | LF / CRLF / CR encodings preserved verbatim each direction | unit | `pytest tests/server/test_clipboard.py::test_newline_preservation -x` | ⚠ extend |
| CLIP-02 | Bidirectional PNG image round-trip preserves bytes (sha256 equality) | integration | `pytest tests/integration/test_clipboard_image.py -x` | ❌ Wave 0 |
| CLIP-02 | PNG magic-byte validation rejects malformed payloads | unit | `pytest tests/server/test_clipboard_image.py::test_magic_byte -x` | ❌ Wave 0 |
| CLIP-02 | 64MB cap enforcement on send + receive sides | unit | `pytest tests/server/test_clipboard_image.py::test_size_cap -x` | ❌ Wave 0 |
| CLIP-02 | Chunked transport: in-order, out-of-order, duplicate, missing assembly | unit | `pytest tests/common/test_clipboard_chunking.py -x` | ❌ Wave 0 |
| CLIP-03 | Per-direction toggle gates dispatch (4 directions × on/off matrix) | unit | `pytest tests/server/test_clipboard_toggles.py -x` | ❌ Wave 0 |
| CLIP-03 | Toolbar 4-checkbox menu emits correct toggle state changes | unit | `pytest tests/client/test_clipboard_toggle_menu.py -x` | ❌ Wave 0 |
| **D-18 latency** | P1 1-hour synthetic smoke with crop + chunk paths active stays p99 < 25 ms | smoke (nightly) | `pytest -m smoke_1h tests/smoke/test_synthetic_1h.py` | ⚠ verify with crop active |
| **D-18 latency** | DXS pre/post real-hardware input-to-photon measurement re-run | manual | `docs/release.md` D-08 + Phase 3 timing checkpoint | manual |
| **D-12 hot-plug** | Hot-plug mid-stream + auto-fallback → client UX path verified | integration | `pytest tests/integration/test_monitor_hotplug.py::test_pick_vanish_fallback -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/{server,client,common}/test_<file>.py -x` (the file changed)
- **Per wave merge:** `pytest -m "not wacom_hw and not gpu and not smoke_1h and not latency_bench"` (full unit + integration)
- **Phase gate:** Full suite green + nightly smoke_1h green + P2 ten_bit_smoke green + D-08 4-corner spike captured

### Wave 0 Gaps

- [ ] `tests/client/test_connect_dialog_mode.py` — covers DISP-01 mode picker
- [ ] `tests/client/test_fullscreen_toolbar_mode_badge.py` — covers DISP-01 badge
- [ ] `tests/server/test_screen_capture_hotplug.py` — covers DISP-02 Linux side
- [ ] `tests/server/test_mac_screen_capture_hotplug.py` — covers DISP-06 Mac side
- [ ] `tests/server/test_session_manager_edid.py` — covers DISP-04
- [ ] `tests/server/test_clipboard_image.py` — covers CLIP-02 Linux side
- [ ] `tests/server/test_mac_clipboard_image.py` — covers CLIP-02 Mac side
- [ ] `tests/server/test_clipboard_toggles.py` — covers CLIP-03 server-side
- [ ] `tests/server/test_capture_crop.py` — covers D-02 crop math + 10-bit preservation
- [ ] `tests/client/test_viewer_screen_changed.py` — covers DISP-05 screenChanged path
- [ ] `tests/client/test_viewer_dpr.py` — covers D-06 per-screen DPR
- [ ] `tests/client/test_clipboard_toggle_menu.py` — covers CLIP-03 client-side
- [ ] `tests/integration/test_monitor_hotplug.py` — covers DISP-02 + D-12 in-process loopback
- [ ] `tests/integration/test_capture_mode.py` — covers DISP-07 server crop end-to-end
- [ ] `tests/integration/test_clipboard_text_large.py` — covers CLIP-01 large/CRLF round-trip
- [ ] `tests/integration/test_clipboard_image.py` — covers CLIP-02 round-trip + sha256
- [ ] `tests/common/test_cursor_math.py` — covers D-05 widget→physical-px math
- [ ] `tests/common/test_clipboard_chunking.py` — covers D-17 assembler
- [ ] Extend `tests/client/test_bookmarks.py` — covers monitor_mode + 4 toggle migration

**Test framework already installed.** Wave 0 is purely about scaffolding the new test files + extending the bookmark migration test.

---

## Security Domain

> Required per `security_enforcement` default-on. Phase 3 introduces clipboard image and chunked transport — both new attack surfaces.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no (no new auth surface in Phase 3) | — |
| V3 Session Management | partial (clipboard chunks scoped per-session, sequence_id namespaced per session) | Existing per-session ClientSession isolation; add per-session chunk-assembler dict |
| V4 Access Control | yes (per-direction toggles ARE the access control) | D-15 toggle gating short-circuits before wire transmission AND on receive (defense-in-depth) |
| V5 Input Validation | yes (PNG magic-byte + size cap; CRLF preservation; chunk index bounds) | D-16 magic-byte; D-14 size cap; chunk-index range check; assembler timeout |
| V6 Cryptography | no (no new crypto in Phase 3) | — |
| V7 Error Handling & Logging | yes (structlog warnings on validation failures; never include payload bytes) | structlog `event=clipboard.image_oversize` etc. with size/cap fields only |
| V11 Business Logic | yes (toggle gating must happen at message boundary, not per-chunk — see Pitfall 7) | Toggle gate at sequence-id-start; don't drop mid-stream |
| V13 API & Web Service | partial (control channel JSON message validation; no new HTTP endpoints) | Existing `parse_message` + dataclass field validation extends |

### Known Threat Patterns for Phase 3

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed PNG payload (DoS via PIL parser bug) | Tampering / DoS | First-8-byte magic check BEFORE invoking PIL; PIL `verify()` after; size cap before either |
| Oversized clipboard payload (memory DoS) | DoS | D-14 64MB cap on send + receive; reject before chunk reassembly buffer allocation |
| Chunk-stream memory exhaustion (start chunk_index=0 + never send rest) | DoS | CHUNK_TIMEOUT_S=30s drops incomplete sequences; assembler tracks `_started_at` |
| Per-direction toggle bypass via mid-stream chunk | Authorization bypass | Toggle gate at sequence-id-start, not per-chunk; document in D-15 implementation (Pitfall 7) |
| Clipboard exfiltration via background paste | Information disclosure | Per-direction toggles let user disable c2s; default-ON is the locked policy per D-15 (small-studio trust model) |
| Cross-session clipboard leak (tenant A copies, tenant B's session sees it) | Information disclosure | Existing PAM per-user X session isolation on Linux (each user has their own Xvfb/Xorg) — reuse; do not introduce shared clipboard cache |
| Malicious clipboard auto-paste injecting commands | Injection | Clipboard SET only — never auto-PASTE on receive (existing pattern); user pastes deliberately |
| Bookmark JSON injection of malicious monitor_mode value | Tampering | Whitelist enum: `"single"|"mirror"|"pick"` only; reject unknown values at load time + revert to default |

**Threat-model addition for planner:** Phase 3 expands the trust boundary slightly — bidirectional image clipboard means a compromised remote server could push images that exploit a client-side image parser bug. PIL is mature and well-fuzzed but the threat exists. Mitigation: rely on Apple/X11 system clipboard handlers (which are heavily fuzzed) for the actual decode-and-display; Teraguchi only validates magic bytes and round-trips bytes, never decodes for display.

---

## Sources

### Primary (HIGH confidence — verified against codebase)

- `.planning/phases/03-display-multi-monitor-clipboard/03-CONTEXT.md` — 18 locked decisions (D-01..D-18) drive every prescription in this research [VERIFIED]
- `.planning/phases/03-display-multi-monitor-clipboard/03-UI-SPEC.md` — 9 UI surfaces, 10 components inventoried, copy contract locked [VERIFIED]
- `.planning/phases/03-display-multi-monitor-clipboard/03-DISCUSSION-LOG.md` — alternatives considered (audit trail) [VERIFIED]
- `.planning/REQUIREMENTS.md` — DISP-01..07 + CLIP-01..03 verbatim [VERIFIED]
- `.planning/PROJECT.md` — hard constraints (Mac client + Rocky/Mac server, 10-bit, sub-20ms LAN, Apache 2.0) [VERIFIED]
- `.planning/ROADMAP.md` Phase 3 section — pre-phase spike, dependencies, success criteria [VERIFIED]
- `.planning/research/PITFALLS.md` §"Pitfall 5: Multi-monitor / display topology" — drives D-05..D-12 [VERIFIED]
- `.planning/research/PITFALLS.md` §"UX Pitfalls" — cursor offset, monitor switch [VERIFIED]
- `.planning/codebase/STACK.md` — Python 3.12 floor, PySide6 6.10, websockets 15, pyobjc baseline [VERIFIED]
- `.planning/codebase/ARCHITECTURE.md` — three-tier client/broker/server, hybrid transport, platform dispatch via `platform_backends.py` [VERIFIED]
- `.planning/codebase/CONCERNS.md` — known bugs (xrandr NVIDIA segfault, mac_input_injector pen drop), platform parity gaps [VERIFIED]
- `.planning/codebase/TESTING.md` (Phase 1 baseline) + actual `tests/` tree (Phase 1 + Phase 2 conventions present) [VERIFIED]
- Existing source: `server/screen_capture.py`, `server/mac_screen_capture.py`, `server/clipboard.py`, `server/mac_clipboard.py`, `server/monitor_hotplug.py`, `client/viewer.py`, `client/monitor_selector.py`, `client/protocol.py`, `client/bookmarks.py`, `common/messages.py` [VERIFIED]

### Secondary (MEDIUM confidence — official docs cited but not all version-checked against the latest API)

- Qt 6 HighDPI documentation — `QScreen::devicePixelRatio()` per-screen behavior [CITED: doc.qt.io/qt-6/highdpi.html]
- Apple ScreenCaptureKit programming guide + `SCStreamDelegate` API [CITED: developer.apple.com/documentation/screencapturekit]
- Apple `NSPasteboardTypePNG` / `NSPasteboardTypeString` API reference [CITED: developer.apple.com/documentation/appkit/nspasteboardtypepng]
- NSWorkspaceDidChangeScreenParametersNotification [CITED: developer.apple.com/documentation/appkit/nsworkspacedidchangescreenparametersnotification]
- xclip(1) man page — `-t image/png` target support [CITED: xclip 0.13+ man page]

### Tertiary (LOW confidence — assumptions to verify in Wave 0)

- Mozilla Bugzilla #794038 — mixed-DPI Mac picking one DPR on window-straddle (cited in CONTEXT but not directly verified by this research session) [CITED via CONTEXT.md]
- NVIDIA forum xorg-sigsegv-nvidia-drm-warning-on-hdmi-hotplug-disconnect (cited in CONTEXT but not directly verified by this research session) [CITED via CONTEXT.md]
- Eizo CG279X as the recommended Flame-approved monitor profile name for CustomEDID — needs verification against Autodesk Flame system requirements [ASSUMED — see A7]

---

## Metadata

**Confidence breakdown:**

- **Standard stack:** HIGH — every dependency is already in `requirements-server.txt` / `requirements-client.txt` and verified at `>=` of the needed version
- **Architecture:** HIGH — every component referenced exists in the codebase as confirmed by `.planning/codebase/STRUCTURE.md` and direct file reads; all extension points are real, not speculative
- **Pitfalls:** HIGH for codebase pitfalls (xrandr NVIDIA segfault, pen pressure drop), MEDIUM for upstream library pitfalls (PIL on malformed PNG, NSPasteboard on chunked-IDAT) — verify with fixture tests
- **Validation architecture:** HIGH — extends Phase 1's `tests/` tree using established patterns (mock-at-OS-boundary, in-process loopback, structlog telemetry)
- **Security:** MEDIUM — defense-in-depth design follows V4/V5 ASVS but needs implementation review for the chunk-assembler memory-bound enforcement

**Research date:** 2026-04-19
**Valid until:** 2026-05-19 (30 days; Phase 3 dependencies are stable — Qt 6.10, PySide6, ScreenCaptureKit, xclip all unchanged for >12 months at time of research)

---

## RESEARCH COMPLETE
