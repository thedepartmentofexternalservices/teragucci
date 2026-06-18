# Pitfalls Research

**Domain:** High-performance open-source remote workstation for VFX/Flame, macOS→Rocky + macOS→macOS, 10-bit color, Wacom pressure, sub-20ms LAN input-to-photon
**Researched:** 2026-04-18
**Confidence:** HIGH for latency/color/input/network pitfalls (verified against Moonlight/Sunshine/Parsec/NICE-DCV docs, Qt docs, Apple WWDC, NVENC SDK, FFmpeg/PyAV issues, and the existing `.planning/codebase/CONCERNS.md`); MEDIUM for macOS-specific Wacom driver regressions (Qt bug tracker searches returned historical info only — specific Sonoma/Sequoia regressions require verification on real hardware).

**Tone and priority.** This is a production-hardening milestone, not a greenfield project. Every pitfall below is framed against: *"Does this cost a Flame artist their 8-hour client session?"* If yes → **Critical**. If it degrades perceived quality but doesn't kill the session → **High**. If it's polish → **Medium/Low**. The roadmap phase tags (P1..P7) map to a plausible production-hardening phase split; the orchestrator's roadmap generator should override these with whatever phase numbering the roadmap itself adopts.

---

## Critical Pitfalls

### Pitfall 1: Latency Death by a Thousand Cuts

**What goes wrong:**
Input-to-photon latency balloons from a target 15–20 ms (LAN) to 120–180 ms without any single smoking gun. The artist says "it feels sluggish" and you can't reproduce in microbenchmarks because each stage is "only" 8–20 ms.

**Why it happens:**
Each stage in the pipeline has a plausible "we'll add a small buffer for safety" decision. They compound:

| Stage | Typical silent-default cost | How it sneaks in |
|-------|------|----------------------------|
| Capture frame pacing | 16.67 ms | Waiting for next vblank/compose frame. Sunshine hits this with 59.94 Hz vs 60 Hz [Sunshine #2286](https://github.com/LizardByte/Sunshine/issues/2286) |
| NVENC lookahead | 33–50 ms | Default `rc-lookahead=8` on some presets; must explicitly disable for low-latency |
| B-frame reordering | 16–33 ms | H.264/HEVC default GOP enables B-frames — encoder reorders, decoder waits. WebRTC bans B-frames precisely because of this [mediamtx #2818](https://github.com/bluenviron/mediamtx/discussions/2818) |
| TCP HoL blocking on WebSocket | 100 ms+ per packet loss | Entire stream freezes waiting for retransmit [WebRTC/WebSocket analysis](https://getstream.io/blog/webrtc-websocket-av-sync/) |
| Encoder queue | 2–5 frames | FFmpeg pipe buffering + asyncio.Queue(maxsize=30) in `server/main.py` |
| Client jitter buffer | 20–80 ms | Defensive buffering for network smoothing |
| Decoder latency | 5–15 ms | Especially software decode fallback; VideoToolbox 10-bit P010 sometimes silently falls back to software [iina #5796](https://github.com/iina/iina/issues/5796) |
| Qt widget `update()` → paint | 16.67 ms | One compositor frame |
| macOS CoreAnimation double-buffering | 16.67 ms | Another compositor frame |
| Monitor scan-out | 8.33 ms | Half a frame average |

Stack them and 15 ms becomes 150 ms. PCoIP got this right by being paranoid on every stage.

**How to avoid:**
1. **Measure each stage continuously.** Instrument timestamps at capture → encode-in → encode-out → send → recv → decode-in → decode-out → paint. Expose the per-stage breakdown in the health overlay so regressions are visible at a glance, not hidden in total latency.
2. **Kill B-frames explicitly.** `-bf 0` for every encoder. Flame's workflow is real-time interactive — there are no future frames to reference.
3. **Disable rc-lookahead** for all NVENC/VAAPI/VideoToolbox presets in `server/video_encoder.py`. `rc-lookahead=0`, `no-scenecut=1`, `tune=zerolatency` (or equivalent) on every codec path.
4. **Push the media path off WebSocket.** UDP (already implemented via `HybridServerTransport`) or QUIC datagrams for video. Control (input, clipboard) stays reliable. TCP HoL blocking is a session-killer over any lossy link. See [MediaMTX HoL analysis](https://github.com/bluenviron/mediamtx/issues/2937).
5. **Single-frame queue depth.** Client-side: drop frames in favor of the newest, never buffer more than 1 display frame. Server-side: the current `maxsize=30` should be **maxsize=2** for video — backlog means the artist is watching stale frames.
6. **Request keyframe on drop, not on timer.** `CONCERNS.md` flags that dropped P-frames cause up to 2 s of broken video (GOP = 60 frames). Fix: on any `record_frame_dropped()` call, force `encoder.request_keyframe()` immediately.
7. **CBR, not VBR, for interactive.** VBR bursts during scene transitions can blow through the jitter buffer [PulseGeek Sunshine guide](https://pulsegeek.com/articles/optimize-sunshine-encoder-for-the-moonlight-client/).
8. **Avoid compositor on server.** Flame bypasses GNOME/mutter. A compositor adds one full frame of latency for free.

**Warning signs:**
- Health overlay shows total latency >30 ms on LAN but no single stage >15 ms (means everything is just slightly slow).
- "It feels fine most of the time but hiccups during fast pans" → B-frames or VBR.
- Input latency spikes from 20 ms → 300 ms on a single packet drop → TCP HoL, move to UDP.
- Latency walks upward over the session → queue backlog; check `send_queue` depth trend.

**Phase to address:** P1 — Stability/latency baseline. Must be instrumented before any other hardening work because every other pitfall shows up as "it feels laggy."

**Severity:** Critical.

---

### Pitfall 2: 10-Bit Pipeline Silently Degrades to 8-Bit

**What goes wrong:**
The user selects 10-bit (Main10/Main10 HEVC/AV1), streaming works, and nobody notices that somewhere in the pipeline the data got converted to 8-bit Y'CbCr 4:2:0 and back. Banding shows up on smooth gradients during a grading session — by then the project has already been delivered with bad color.

**Why it happens:**
Every link in the chain has an 8-bit default or an implicit down-conversion:

| Link | 8-bit default / silent downgrade |
|------|----------------------------------|
| Capture | NvFBC can deliver BGRA (8-bit). Need explicit 10-bit surface. ScreenCaptureKit defaults to BGRA8888 [TN3121](https://developer.apple.com/documentation/technotes/tn3121-selecting-a-pixel-format-for-an-avcapturevideodataoutput) |
| Encoder input format | FFmpeg `rawvideo -pix_fmt bgra` → encoder does RGB→YUV420P (8-bit). For 10-bit need `p010le` or `yuv420p10le`, and `-profile:v main10` |
| Encoder profile fallback | NVENC quietly accepts Main10 request but falls back to Main (8-bit) on older silicon [OBS NVENC issue](https://obsproject.com/forum/threads/nvenc-error-cannot-perform-10-bit-encode-on-this-encoder.165772/) |
| Chroma subsampling | 4:2:0 is the default; color detail (especially reds) loses 3/4 of its resolution. 4:4:4 is required for grading but most hardware encoders only do 4:2:0 in Main10 |
| Decoder HW path | VideoToolbox Main10 can silently fall back to software decode for P010 [iina #5796](https://github.com/iina/iina/issues/5796) — not wrong, but slow and may drop frames |
| Decoder output format | PyAV returns `AVFrame.format` — need to assert it's `p010le` / `yuv420p10le`, not `yuv420p` |
| Qt `QImage` format | `QImage::Format_RGB32` / `ARGB32` are 8-bit-per-channel. No built-in 10-bit QImage format until Qt 6.5+; even then, `Format_RGBA64` is 16-bit, and there's no 10-10-10-2 native format |
| Display blit | If `RemoteViewer` paints via `QPainter::drawImage(QImage)`, 10-bit is lost at the QImage conversion |
| macOS EDR tone mapping | macOS quietly tone-maps HDR content within current EDR headroom, and clips above it [Apple WWDC21](https://developer.apple.com/videos/play/wwdc2021/10161/). "Reference Mode" disables this but only on Pro Display XDR / MBP XDR |
| macOS Night Shift / True Tone | Alter displayed color — users don't realize these are on |

**How to avoid:**
1. **End-to-end 10-bit test fixture.** Generate a known 10-bit test pattern (SMPTE RP 2111 or a ramp with bit 9 toggled). Pump it through the pipeline, compare decoded output byte-for-byte. If any bit below position 8 is dropped, the pipeline lost depth.
2. **Explicit pixel-format assertions at every stage.** Capture returns assert `surface.format == RGBA10` or `BGRA10`. Encoder input validates `p010le`. Decoder output validates `frame.format in {AV_PIX_FMT_P010LE, AV_PIX_FMT_YUV420P10LE, ...}`. Never fall back silently.
3. **Hardware capability probe at startup.** On Linux, `nvidia-smi --query-gpu=gpu_name` + known table of Main10 support. On macOS, `VTIsHardwareDecodeSupported(kCMVideoCodecType_HEVC)` plus `kVTCompressionPropertyKey_ProfileLevel = HEVC_Main10_AutoLevel`. Refuse to advertise 10-bit if the hardware can't encode it.
4. **Use Metal/OpenGL direct texture upload on client.** Bypass QImage for the display blit — upload P010/YUV10 directly as a Metal texture, let Metal do the YUV→RGB → display conversion. The Qt `QRhi` / `QRhiTexture` path or a custom `QOpenGLWidget` / `QMetalWidget` is the right tool.
5. **Bake 10-bit status into the health overlay.** "10-bit: negotiated / confirmed / **DEGRADED** / not supported." If the server had to fall back, the client overlay must be explicit, not hidden in a verbose log.
6. **Document Reference Mode requirement for grading.** User-facing docs: if you're doing color-critical work on Mac, enable Reference Mode (MBP XDR) or use a calibrated external monitor. Teraguchi ships bits, not color management — be explicit.
7. **Skip Qt HDR entirely for v1.** The `Out of Scope` list already says "no ICC pipeline" — extend that to "no HDR tone mapping on client." Pass-through only. Anyone doing HDR grading needs to verify on a reference monitor, not on their MacBook's EDR-simulated HDR.
8. **Sanity-check chroma subsampling.** If the user's codec advertises 4:4:4 support, prefer it for grading sessions. `CONCERNS.md` already lists this as a `supports_yuv444` capability — wire it to a quality-tier "grading mode" in the UI.

**Warning signs:**
- Histogram shows values exactly on multiples of 4 (means bottom 2 bits got zeroed — classic 10→8→10 conversion).
- Banding on gray ramps / skies that wasn't there in the source.
- `ffprobe` on a saved sample shows `pix_fmt=yuv420p` when Main10 was requested.
- Client CPU spikes under HEVC 10-bit playback (software decode fallback).
- macOS EDR headroom changes under scene brightness change — colors shift unexpectedly.

**Phase to address:** P2 — Color fidelity / 10-bit certification. Must come before any "signed release" phase because shipping an 8-bit pipeline as "10-bit" is a reputation killer in the grading community.

**Severity:** Critical.

---

### Pitfall 3: Wacom Pressure Dropping, Tilt Inverted, Eraser Ignored

**What goes wrong:**
Artist's stroke starts at normal pressure, pressure drops to 0 mid-stroke, stroke ends as a hairline. Or eraser end of the pen behaves as the tip. Or tilt is reported but the rotation math is wrong — brush angle flips across the diagonal. Artists stop trusting the remote path and revert to local.

**Why it happens:**
- **`QTabletEvent` on macOS depends on the Wacom driver posting NSEvents the Qt bridge can translate.** If TCC/Input Monitoring permissions aren't fully granted, pressure still posts but some proximity events vanish.
- **Wacom driver sleep/wake bugs.** Documented by multiple users that after macOS sleep/wake, pen freezes mid-stroke until app restart or driver kick [Apple discussion #251419723](https://discussions.apple.com/thread/251419723). [Wacom Cintiq Pro 24 PT KB](https://support.wacom.com/hc/en-us/community/posts/30167202319639-Cintiq-Pro-24-PT-Disconnects-after-System-Wakes-Up-from-Hibernation)
- **Screen lock eats proximity event.** When the client locks and unlocks, the proximity-enter event is delivered to the lockscreen, not the app. The app thinks the pen is never down.
- **Cintiq vs Intuos ID handling.** Cintiq displays have different tablet-to-screen mapping. If the code assumes `tabletData().tabletScreen` matches the Qt screen it doesn't on Cintiq Pro 24 or 32.
- **Server-side injection dropped on macOS.** `CONCERNS.md` confirms: `mac_input_injector.pen_event` downgrades pen to mouse click, drops pressure. Known bug.
- **Qt's internal Wacom code path was almost removed in Qt5.** `xcb_wacom` plugin was deprecated; modern Qt uses xinput2 on Linux and Cocoa NSEvent on Mac. Historical context: [Qt removing Wacom support discussion](https://development.qt-project.narkive.com/c3sMUpQW/removing-wacom-support-in-qt5)

**How to avoid:**
1. **Ship a Wacom diagnostic in the client.** Extend the existing `client/key_diagnostic.py` pattern to pen: a canvas that dumps `QTabletEvent.pressure`, `xTilt`, `yTilt`, `rotation`, `pointerType`, `uniqueId`, and the raw `device()`. First thing a user runs on a new machine. If proximity events are missing you see it immediately.
2. **Re-raise proximity on focus-gained.** On `focusInEvent` and `showEvent`, synthesize a `TabletProximity` event so the server doesn't think the pen is absent after client minimization.
3. **Detect and handle modern Wacom driver TCC.** Verify "Input Monitoring" is granted to the *client* app. Wacom driver itself needs "Accessibility" — can't fix, but can detect (read `tccutil` state via `sqlite3` on `~/Library/Application Support/com.apple.TCC/TCC.db` — read-only, no bypass).
4. **Timer-based pressure watchdog.** If `QTabletEvent` pressure drops to 0 for >50 ms while buttons are still reported pressed, log + emit a diagnostic event. This is the "driver stuck" signature.
5. **Cintiq topology math unit tests.** Given a known `QScreen.geometry` + `QTabletEvent.posF`, assert the server-side coords land where expected. Test on Intuos Pro and Cintiq Pro 24 separately.
6. **Build IOHIDUserDevice-based pen injector for Mac server.** `CONCERNS.md` lists this as a known gap. Requires Objective-C (or PyObjC + CoreFoundation) IOHIDUserDevice emulating a Wacom HID report descriptor. This is non-trivial (~500 lines) but required for macOS server to be Flame-usable. `hidtest` + `hidutil` are the debug tools.
7. **Document supported tablets.** Ship a CI smoke matrix: Intuos Pro M/L, Cintiq Pro 16/24, Intuos Small. Anything else → "likely works, unsupported."
8. **Ignore Mac touch-pointer and Apple Pencil.** `QTabletEvent` can fire from a trackpad with Force Touch. Filter on `pointerType() == QTabletEvent::Pen`.

**Warning signs:**
- User reports "strokes feathering out at the end" → pressure watchdog
- User reports "eraser not working" → check `pointerType()` and tablet device class
- Different behavior after lunch break than before → sleep/wake driver reset
- `QTabletEvent` stops firing entirely but mouse events work → proximity desync
- Unit tests pass on Intuos but fail on Cintiq → topology math bug

**Phase to address:** P2 — Input fidelity hardening. Can't ship v1 without this.

**Severity:** Critical. Flame artists' muscle memory is pen pressure. Wacom bugs end artist trust in 5 minutes.

---

### Pitfall 4: Modifier Key Desync (Ctrl/Shift/Alt Stuck or Dropped)

**What goes wrong:**
Artist does `Ctrl+Shift+Alt+S` and the remote host only sees `Shift+S` — or worse, sees nothing because the modifier state got stuck on `Ctrl` pressed and then nothing is a normal letter anymore. Or `Cmd` on the Mac client gets mapped to `Ctrl` on the Linux server sometimes but not others. Key repeats run away during a brief network pause.

**Why it happens:**
- **Focus loss doesn't release modifiers.** Qt does not emit release events for modifiers if the window loses focus while a modifier is held (Cmd+Tab). The server believes `Ctrl` is still held. See [Qt focus handling docs](https://doc.qt.io/qt-6/focus.html) and [autokey/autokey#169](https://github.com/autokey/autokey/issues/169).
- **macOS traps keystrokes.** `Cmd+Tab`, `Cmd+Space`, Mission Control, Exposé — the OS swallows these before Qt sees them. So the server never sees the `Cmd-up` that followed a `Cmd-down`.
- **Cmd↔Ctrl remapping inconsistency.** A natural Mac-client-to-Linux-server remap is "Cmd→Super" (not Ctrl) for window-manager hotkeys, but "Ctrl→Ctrl" for app shortcuts. Flame uses Ctrl heavily; if the client ever maps Cmd→Ctrl, bang, Flame gets double-Ctrl presses.
- **Key repeat on network pause.** If the client sends `key-down` and the network stalls before `key-up`, the server's X11 keyboard keeps repeating. Artist returns from a connection blip and has 50 of the same letter.
- **Non-ASCII input.** Dead keys (French é), AltGr on international layouts, IME composition — all require `QInputMethodEvent` handling, not `QKeyEvent`. Missing → garbled input.
- **Caps Lock sync drift.** Client-side Caps can be on while server-side is off (or vice versa). Artist types an email address and it's ALL CAPS.
- **PySide6 known keys-stuck issue.** Documented report that keys can get stuck in internal `keysDown` set, fix requires explicit cleanup on focus events.

**How to avoid:**
1. **Reset modifiers on every focus loss.** In `RemoteViewer.focusOutEvent`, send an explicit "release all modifiers" message. Server's `InputInjector.reset_modifiers()` already exists (`server/platform_backends.py`) — wire it to a protocol message `KEY_RESET_MODIFIERS`.
2. **Reset modifiers on reconnect.** First thing after reconnect: synthesize releases for every modifier. Protects against key-repeat runaway.
3. **Server-side key-repeat is bounded to client acknowledgment.** Linux `xset r off` on the virtual display → rely on client to send N repeats. If the stream stalls, the key just stops repeating rather than machine-gunning.
4. **Explicit Cmd→Ctrl map table, exposed to user.** UI checkbox: "Swap Cmd and Ctrl when talking to Linux server" (on by default for Flame). A single authoritative table in `common/keymap.py` — never inferred at call site.
5. **Trap-key documentation and UX.** Some macOS keys physically cannot reach Qt (`Cmd+Tab`, Spotlight, Mission Control). Document this. Offer an in-app "aggressive capture" toggle that uses `kCGEventTapOptionDefault` with the accessibility API — but warn users TCC permission is required.
6. **IME passthrough.** `RemoteViewer` must accept `QInputMethodEvent` and forward commit strings as unicode text, not synthesized keycodes. Test with French / Japanese / Korean layouts.
7. **Caps Lock sync on every key event.** Include the Caps Lock state (a bit in modifier flags) on every keypress, server auto-corrects.
8. **Extend the existing `keydiag.py` tool.** Already covers Qt→X11 translation. Add: all-modifier-stress-test, key-repeat stress, focus-in/out stress, reconnect stress. This tool is the single best asset the codebase already has — double down on it.
9. **Unit-test `qt_key_to_linux_scancode`.** Currently no tests per `TESTING.md`. Table-driven tests on every Qt key → Linux scancode mapping. Include all modifier combinations.

**Warning signs:**
- Artist reports "it types lowercase instead of uppercase sometimes" → Caps Lock sync
- "The M key just started typing by itself" → stuck key-repeat after network blip
- "Shift+S pastes instead of saving" → modifier state desync  
- Non-English-keyboard user can't type accented characters → IME passthrough missing
- Flame hotkeys work initially but stop after Cmd+Tab to another app → focus-lost modifier leak

**Phase to address:** P2 — Input fidelity hardening. Ship `keydiag.py`-extended as CI smoke.

**Severity:** Critical. Flame uses modifier chords constantly. One hotkey getting mangled mid-session is enough to break trust.

---

### Pitfall 5: Multi-Monitor Topology / DPI Bugs

**What goes wrong:**
Client has a Retina MacBook internal (2x scale) and an external 4K (1x scale). Connects to a Rocky server with 2×2560×1600 Xorg screens. Result: cursor lands 100 px off target when crossing monitor boundary. Full-screen on one monitor covers both. Or the server switches monitors mid-session and the client widget resizes, but the cursor coordinate math doesn't update.

**Why it happens:**
- **`devicePixelRatio` is per-window on macOS, not per-screen.** When a Qt window straddles two monitors with different scale factors, Qt picks one [Qt HighDPI docs](https://doc.qt.io/qt-6/highdpi.html). Mozilla bug tracker documented this for mixed-DPI Mac setups [bz #794038](https://bugzilla.mozilla.org/show_bug.cgi?id=794038).
- **Qt `QCursor::setPos` on HiDPI.** Coordinates are in device-independent pixels on macOS but physical pixels on Windows. Cross-platform cursor warping is a minefield.
- **Existing codebase has `RESIZE_REQUEST` disabled.** `CONCERNS.md`: the xrandr resize path crashes NVIDIA X server. So the user *can't* adapt server geometry to client window. They're stuck with the startup `--width`/`--height`.
- **Xorg `xrandr` segfaults on NVIDIA on monitor disconnect.** [NVIDIA forum](https://forums.developer.nvidia.com/t/xorg-sigsegv-nvidia-drm-warning-on-hdmi-hotplug-disconnect-rtx-5090-max-q-gb203m-reproducible-across-580-119-02-580-126-18-580-142-595-58/366327) — reliably reproducible.
- **Xvfb virtual screen sizes.** Flame expects specific EDID-provided resolutions. Generic Xvfb `-screen 0 1920x1200x24` will work but not advertise a proper EDID, which some Flame monitor-config dialogs complain about.
- **Fullscreen on one monitor.** `QWidget::showFullScreen()` picks a screen — which one depends on parent widget geometry. Non-obvious from the UI.
- **Monitor hot-plug on Mac.** ScreenCaptureKit has a `SCShareableContent` list that updates async; capture needs to react to displays appearing/disappearing without dropping the session.

**How to avoid:**
1. **Per-session connect-time topology negotiation.** `PROJECT.md` already lists "Per-session flexible multi-monitor mode selection on connect" — hold the line on this. Three explicit modes: single monitor / mirror all / pick one. User picks once per connect, no dynamic mid-session resize.
2. **Stop trying to resize NVIDIA Xorg.** Confirmed-broken. Document as "set your `--width`/`--height` at session start, disconnect and reconnect to change." No magic mid-session resizing.
3. **Ship CustomEDID for virtual displays.** For Xvfb (when used), bake EDID binary matching Flame-approved monitor models. For Xorg+NVIDIA, use `Option "CustomEDID"` in the X config. Document the path.
4. **Cursor coord math: always work in server physical pixels.** Client-side normalize to `[0, server_width)` × `[0, server_height)` using `QPointF / QWidget.size()` ratios. Never send device-independent pixels to the server.
5. **Coalesce mouse moves.** 1000 Hz mouse on a 60 Hz stream → drop 940 events per second. Server doesn't need them.
6. **Test matrix.** CI smoke combinations: Retina + external 4K (mixed DPI), Retina alone, external 5K alone, 2×1080p. Document what works, what doesn't.
7. **Mac server follows Mac display changes.** ScreenCaptureKit stream reinit on display disconnect — `SCShareableContent.current` → pick by `displayID`. Gracefully handle the case where the selected display vanishes (notify clients, let them pick another).
8. **Fullscreen mode: explicit screen picker.** "Full-screen on: [primary | external | span]" dropdown in connect dialog. No guessing.

**Warning signs:**
- User reports "clicks land a bit left of where I click" on HiDPI → DPR math error
- Server log shows xrandr errors when user drags browser window → monitor hotplug race
- Session dies on Mac after unplugging external monitor → SCK display-gone unhandled
- Cursor jumps to wrong monitor on server → topology math or primary-monitor confusion

**Phase to address:** P3 — Multi-monitor / display hardening.

**Severity:** High. Not every user has multi-mon, but most VFX artists do. Cursor offset bugs waste hours.

---

### Pitfall 6: Audio Falls Over 30 Minutes In

**What goes wrong:**
Session starts fine with audio. 20-30 minutes in, audio cuts out silently. Video keeps going. Artist doesn't notice until the producer asks "can you hear me?" on the call. Or: buffer underruns during network jitter cause pops every 2 seconds. Or: mic-feedback when artist has near-field monitors.

**Why it happens:**
- **Sample-rate mismatch accumulates drift.** Source at 48 kHz, client decodes and plays at 44.1 kHz — the code resamples but the resampler's buffer slowly drifts. After 30 minutes → underrun or 30-min-aged audio.
- **PulseAudio `monitor` source dies on session changes.** If any process changes the default sink while a session is running (e.g., AirPods connect/disconnect on client echoed through USB passthrough), the monitor source gets re-enumerated and the capture loop silently stops.
- **PipeWire echo-cancel module config.** Sample rate of the echo-canceller must match the source [PipeWire docs](https://docs.pipewire.org/page_module_echo_cancel.html). Rocky 9 defaults to PulseAudio but PipeWire is preferred — mismatched configs between distros → echo-cancel disabled without warning.
- **Client has near-field monitors + mic.** Classic feedback loop: remote audio plays through speakers, picked up by local mic, sent back to server, re-played to client. Needs a real echo-canceller (`webrtc-audio-processing` or PipeWire module).
- **No `mac_audio_capture.py` exists.** `CONCERNS.md` confirms: macOS server advertises no audio support. Anyone running a Mac-server session has no audio at all. Hard fail for client review calls.
- **`asyncio.Queue` for audio fills up during a network stall.** Audio queued faster than it drains. Eventually queue overflows silently, frames drop, nobody notices because "audio stuttered for a moment."
- **Device change mid-session.** User plugs in AirPods. Default output switches. Capture was reading from the old default. Now reads silence.

**How to avoid:**
1. **One canonical sample rate end-to-end: 48 kHz, Opus 20ms frames.** Opus resamples internally at both ends. Don't mix 44.1 and 48 anywhere in the pipeline.
2. **Watchdog on audio stream.** If no audio frame for >500 ms while session is otherwise healthy, log it and re-init. Better a 1-s blip than 30 min of silence.
3. **PipeWire-first, PulseAudio-compat.** Rocky 9.3/9.4 both ship PipeWire via `pipewire-pulse` compat shim. Use `pw-cli` / `pw-record` rather than `parec` where possible for more deterministic behavior.
4. **Build `mac_audio_capture.py` via AVAudioEngine tap on default output device.** Listed as known gap in `CONCERNS.md`. Includes handling device-change notification (`kAudioHardwarePropertyDefaultOutputDevice`) to reattach the tap automatically.
5. **Echo-cancel is a client-side setting.** Client sends mic audio — apply echo-cancel on the client side using `webrtc-audio-processing` (works cross-platform) or Apple's built-in Voice Processing I/O audio unit (macOS). Server never needs to know.
6. **Target <40 ms mouth-to-ear** on LAN. A/V review with the client is the core audio workflow. Above ~80 ms they notice mouth-sync drift.
7. **Queue depth for audio = 1 or 2 packets max.** Same as video — the newest audio is always better than the oldest.
8. **UI health-overlay row for audio.** "Audio: 48k / Opus / mouth-to-ear 35 ms / echo-cancel on" — if any of these degrades, it's visible.
9. **Test device-swap explicitly.** CI/smoke: connect AirPods mid-session, disconnect them, assert audio is restored.

**Warning signs:**
- Silent log line `"No audio frames for N seconds"` without session end → watchdog catches what users don't
- Users say "audio sounds like it's on a 1 s delay" → sample rate drift or oversized buffer
- "Audio is gone after I plugged in my headphones" → device-change not handled
- "Echo in the meeting" → echo cancellation off or broken
- Mac server session has no audio at all → missing `mac_audio_capture.py`

**Phase to address:** P4 — Audio hardening. Required before v1 client-review workflow is trustworthy.

**Severity:** Critical for client sessions (A/V review is the core use case). High otherwise.

---

### Pitfall 7: Network Roaming / MTU / Captive Portal Death

**What goes wrong:**
Artist travels: session works fine at the studio, barely works at home, breaks completely at a hotel. Silent packet loss or silent UDP drop; Teraguchi shows "connected" but video is frozen.

**Why it happens:**
- **WireGuard/Tailscale MTU.** Default Tailscale MTU is 1280 bytes. Over a network with PPPoE (MTU 1492) + VPN overhead → packets get fragmented → silently dropped if ICMP "Fragmentation Needed" is blocked (most cafe/hotel networks drop it) [WireGuard MTU deep dive](https://keremerkan.dev/posts/wireguard-mtu-fixes/).
- **Tailscale peer-MTU-discovery is still an open FR.** [Tailscale #311](https://github.com/tailscale/tailscale/issues/311) — peer-MTU-discovery not fully shipped. Relies on node-side heuristics.
- **Hotel captive portal breaks Tailscale.** DNS interception on port 53 plus HTTP redirect means the client's tailnet hostname resolves to a portal, or never resolves. [Tailscale #1634](https://github.com/tailscale/tailscale/issues/1634).
- **Captive portal re-auth every N hours.** User's been connected for 4 hours, the portal re-authorizes, UDP suddenly stops working but TCP still does. Teraguchi stays on UDP → video freezes but TCP control still "works" → misleading "connected" state.
- **IPv6-only / NAT64.** Tailscale works, but direct `wss://host:443` WebSocket fallback might not resolve cleanly on IPv6-only networks.
- **Asymmetric paths dual-stack.** Client has IPv4+IPv6 but one direction goes v4 and the other v6 → weird delays.
- **Tailscale DERP relay.** When direct connection fails, traffic goes through a relay. Users don't notice until bitrate tanks. Health overlay should surface "relay: yes/no."

**How to avoid:**
1. **Don't pick the biggest MTU, pick a safe one.** Use 1280 bytes as the UDP datagram size ceiling. It's the IPv6 minimum MTU and survives WireGuard + GRE + PPPoE stacking. Fragment at application layer (already present in the codebase — `common/udp_transport.py` fragments to 1400; lower to 1280).
2. **UDP path health check.** Every 5 seconds, the client sends a probe over UDP and waits for ACK. If 3 consecutive fail while the WebSocket is still up → automatic fallback to TCP-only. Don't fall into the trap of "connected but broken."
3. **Auto-detect captive portals.** Before "Connected" banner, probe `http://captive.apple.com/hotspot-detect.html` or equivalent. If the response isn't the expected 200 "Success", tell the user "captive portal detected, sign in first."
4. **Expose path in the health overlay.** "Connection: direct UDP / direct TCP / relay / fallback-mode". Don't make the user guess.
5. **Document Tailscale MagicDNS in the README.** Explicitly state: "connect to `your-host.your-tailnet.ts.net`, not the IP." Caveat: MagicDNS breaks on some corporate networks that rewrite DNS.
6. **Test on a crappy hotel-wifi emulator.** `tc qdisc add dev ... netem delay 80ms loss 2% corrupt 0.5%` + MTU 1400. If it doesn't work there, it doesn't work.
7. **Reconnect with modifier reset.** On reconnect, client first resets all modifiers (Pitfall 4) then reloads server state.
8. **Never claim "connected" just because WebSocket is up.** The session is healthy only when media is flowing.

**Warning signs:**
- "Connected" in UI but video frozen for >3 seconds → dead UDP, needs path health check
- Works at studio, doesn't work on public Wi-Fi → MTU issue
- Works at hotel but breaks every 3 hours → captive re-auth
- Bitrate stuck at 5 Mbps when user is on gigabit → relay path, not direct
- Reconnection after laptop sleep hangs for 30s → Tailscale re-establishing

**Phase to address:** P5 — Network/transport hardening. Tailscale already listed as primary WAN substrate in `PROJECT.md`; v1 ship criterion should include a documented hotel-Wi-Fi test run.

**Severity:** High. Artists travel. A session that breaks outside the studio is a session the artist doesn't depend on.

---

### Pitfall 8: "Works on Randy's Machine" — Environment Assumptions

**What goes wrong:**
Install script fails for the second user. PyAV links against one FFmpeg, system has another → dylib hell. NvFBC helper compiles on Randy's dxs-flame-01 but fails on dxs-flame-02 because the CUDA toolkit version differs. macOS user doesn't get a TCC prompt → recording permission not granted → server starts but produces black frames.

**Why it happens:**
- **Root-required features gate the install.** `/dev/uinput` needs group membership or udev rule. First-run user sees permission denied. [kernel.org uinput docs](https://www.kernel.org/doc/html/v4.12/input/uinput.html), [evremap #21](https://github.com/wez/evremap/issues/21).
- **PyAV ABI coupling.** `CONCERNS.md`: "PyAV pins against specific FFmpeg versions." Brew `ffmpeg` vs pip `av` drift → either import fails or hardware decode silently falls back.
- **NVIDIA SDK not installed on user machine.** `nvfbc_capture.c` has a linker path that only works with the proprietary NVIDIA Video SDK installed. Without it → capture falls back to `mss`, still works but no hardware acceleration.
- **Python version skew.** `requirements-dev.txt` is built against Python 3.10+. Installer finds system Python 3.9 on older Rocky, or 3.14 in a venv → PySide6 wheel availability differs.
- **macOS TCC "quiet-fail" on TCC denial.** Client screen recording denied → user sees a black window, not an error message.
- **Hard-coded paths.** `Teraguchi.app/Contents/MacOS/Teraguchi` has `/Users/randymcentee/workspace/GitHub/teraguchi` baked in (`CONCERNS.md`).
- **Running as root vs user.** PAM auth requires server-as-root. Macbook testing is usually as user. Mode differences mask bugs on one platform but not the other.

**How to avoid:**
1. **Installer is the contract.** Both `install-server.sh` and `install-client.sh` (+ `install-server-macos.sh`) must succeed on a freshly provisioned machine. Test via ephemeral VMs / GitHub Actions runners.
2. **CI on at least one Linux + one macOS runner.** GitHub Actions macOS runner + Rocky 9 container. Runs installer end-to-end, runs test suite, runs protocol smoke test. `CONCERNS.md` flags this is missing entirely.
3. **Bundle FFmpeg with PyInstaller.** Don't rely on Homebrew / dnf. `CONCERNS.md` already lists this as the mitigation for PyAV drift.
4. **Bundle Python with PyInstaller.** No system Python dependency.
5. **Explicit TCC prompts with fallback text.** Before starting SCK stream, check screen recording permission (`CGPreflightScreenCaptureAccess`). If denied → show a dialog "Grant permission in System Settings → Privacy → Screen Recording, then quit and reopen Teraguchi."
6. **Udev rule shipped in the RPM.** `70-uinput.rules` granting `KERNEL=="uinput", GROUP="input", MODE="0660"` + user instructions "add yourself to the input group." No permission denied.
7. **Delete the hard-coded dev launcher.** `Teraguchi.app/Contents/MacOS/Teraguchi` committed with an absolute path — either make it relative (`$(dirname $0)/..`) or don't commit it at all. The PyInstaller-generated bundle is the distributable.
8. **Explicit prerequisite check.** Server startup logs a concise "system readiness" table: ffmpeg present? NVENC available? uinput usable? TCC granted? If any red, user sees it at launch.
9. **Document the support matrix.** Rocky 9.3, 9.4, 9.5. macOS 14 (Sonoma), 15 (Sequoia), 26 (latest). Python 3.11+. Older versions: unsupported, not tested. Be explicit.

**Warning signs:**
- Collaborator DMs "how do I get this running" twice in a week → install UX broken
- PyAV import error that only happens on Brew-installed ffmpeg → ABI drift
- Mac client shows black screen, no error → TCC silently denied
- Install works first time but not on reinstall → state pollution
- Test runs on your dev machine but never runs on CI → no CI

**Phase to address:** P1 — Baseline CI + installer; P6 — Distribution hardening.

**Severity:** High. Every friction in install is a user lost. For a 1–10 person studio, one broken install is "this doesn't work."

---

### Pitfall 9: Signing / Notarization Rabbit Hole

**What goes wrong:**
Release-day: Apple notary service rejects the bundle. Developer burns 2 days fighting entitlements. Gatekeeper blocks the installer anyway. RPM signed with wrong key, dnf refuses to install. Artist right-clicks → Open → still blocked.

**Why it happens:**
- **PyInstaller + hardened runtime.** PyInstaller-built apps crash under hardened runtime unless specific entitlements are granted [pyinstaller #4629](https://github.com/pyinstaller/pyinstaller/issues/4629), [pyinstaller #7937](https://github.com/pyinstaller/pyinstaller/issues/7937).
- **Required entitlements for Python apps:** `com.apple.security.cs.allow-unsigned-executable-memory`, `com.apple.security.cs.allow-jit`, `com.apple.security.cs.disable-library-validation`. Not all of these — sometimes the minimal set is just `allow-unsigned-executable-memory`.
- **Comments in entitlements.plist break notarization** on recent macOS.
- **`codesign --deep` is evil.** Apple docs recommend against it. Signing each binary individually from the inside out is mandatory for Python bundles — PyAV ships native `.so` files inside `dist/.../av/`, each one needs a signature.
- **Hardened Runtime must be on.** Notary rejects anything without it.
- **Mac TCC attribution pointing to Python, not Teraguchi.** If the bundle isn't signed with a stable identifier, every rebuild prompts again. Critical for Wacom TCC because Wacom's driver remembers apps by bundle ID.
- **Stapling the notarization ticket** is a separate step. Users offline get "damaged" warnings without it.
- **RPM signing: wrong key.** GPG keypair must be trusted in the user's rpm keyring or dnf refuses [Rocky GPG key info](https://rockylinux.org/resources/gpg-key-info), [oneuptime dnf GPG fix](https://oneuptime.com/blog/post/2026-03-04-fix-gpg-check-failed-installing-rpm-packages-rhel/view).
- **SELinux denials after install.** systemd unit might work but SELinux blocks socket bind or /dev access — service starts, immediately denied, user sees "active (exited)" or failed.
- **Notary service flaky.** Sometimes submissions sit in queue for hours. Occasionally fail without a useful reason.

**How to avoid:**
1. **Code-sign + notarize before v1.** `PROJECT.md` lists this as v1 scope — hold the line. Target: one-command `make release` that produces signed RPM + notarized DMG + GitHub release artifacts.
2. **Reproducible PyInstaller build.** Pin PyInstaller version, Python version, PyAV version in `build_client.py`. Any drift changes the bundle hash.
3. **Minimal entitlements set.** Start with just `allow-unsigned-executable-memory`; add more only if notary complains. Each added entitlement weakens the security story.
4. **Sign bottom-up, not `--deep`.** Iterate every `.dylib`, `.so`, nested `Frameworks/`, executable; sign each; then sign the app last. Proven PyInstaller workflow — see Haim Dev's write-up [haim.dev PyInstaller mac signing](https://haim.dev/posts/2020-08-08-python-macos-app).
5. **Staple + verify.** `xcrun stapler staple` and `spctl --assess --verbose Teraguchi.app`. Must output "accepted."
6. **Ship a stable bundle identifier.** `com.dxs.teraguchi.client` or similar. Don't change across versions — TCC remembers.
7. **Publish the GPG public key on GitHub Pages.** Users `rpm --import https://teraguchi.dev/RPM-GPG-KEY`. Or bundle in `teraguchi-release` meta-package.
8. **SELinux policy file shipped with RPM.** If systemd service needs specific ports / devices, write an SELinux module (`.te` file compiled to `.pp`). `semodule -i teraguchi.pp` at post-install.
9. **Gate release builds in CI, not on laptop.** GitHub Actions macOS runner has its own keychain-based signing workflow. Avoid "it worked when Randy signed it locally."
10. **Runbook for notary failures.** Document in `release-process.md`: common failure messages, their fixes, how to resubmit. First time through is painful; documented runbook saves the second.

**Warning signs:**
- Artist: "Mac says the app is damaged" → unstapled ticket or Gatekeeper quarantine
- Artist: "dnf install failed, Can't verify GPG key" → key not trusted
- `systemctl status teraguchi-server` shows active but `journalctl` shows AVC denials → SELinux
- Every release needs "right-click → Open" instructions → not signed/not notarized
- Wacom asks for Accessibility permission on every launch → bundle ID changing between builds

**Phase to address:** P6 — Distribution hardening. Timebox: signing rabbit hole can eat a week. Do it early enough that learnings feed back.

**Severity:** High. A signed download is the difference between "try this" and "call me when it's real."

---

### Pitfall 10: Solo Maintainer Burnout

**What goes wrong:**
Author ships v1.0 to enthusiasm. Issues pile up. Author fixes them on nights/weekends. Six months later, response times slow, burnout hits, project goes quiet, users lose trust. Classic single-maintainer death spiral.

**Why it happens:**
- **61% of unpaid OSS maintainers are solo** [Socket.dev solo maintainer report](https://socket.dev/blog/the-unpaid-backbone-of-open-source). 58% of maintainers of widely-used projects experience burnout.
- **Ambition creep.** v1 was scoped. v1.0.1 adds "just a small feature." v1.1 needs a Linux client. v1.2 needs Windows. Each addition seems small — together they're a full-time job.
- **User entitlement.** OSS users treat maintainer time as free. Every "how do I configure X?" ticket is friction.
- **"Looks like a startup" trap.** Launch with a slick GitHub page → users expect SaaS-level support.
- **No CI, no tests → every change is manual QA.** Author burns out on retesting.
- **Homebrew famously almost died from single-maintainer burnout** [Open Source Pledge on burnout](https://opensourcepledge.com/blog/burnout-in-open-source-a-structural-problem-we-can-fix-together/).

**How to avoid:**
1. **Explicit scope boundaries in `CONTRIBUTING.md`.** "Teraguchi supports: Mac client, Rocky/Mac server, Tailscale-first. We do NOT support: Windows, Linux client, browser client, iPad." Users can ask, but the answer is "out of scope, open a fork."
2. **Response-time SLA in README.** "Best-effort response on issues. Volunteer project. If it's urgent, fix it and send a PR."
3. **CI + tests before v1 ships.** `TESTING.md` shows zero tests today. Ship with a realistic test suite (Pitfall 16 below). Every future PR tests itself. `CONCERNS.md` flags no `.github/workflows`.
4. **Issue/PR templates that redirect.** `bug_report.yml` asks for version, OS, reproduction. Incomplete reports auto-labeled "needs more info." Lower cost per triage.
5. **`SECURITY.md` with explicit scope.** "Report security issues to X. We don't handle reports for: dependencies (file upstream), features not in scope."
6. **Publish a stale-bot config.** Issues with no activity for 90 days auto-close. Brutal but works.
7. **Invite a second committer early.** Even one collaborator prevents the "bus factor 1" panic. Target: by v1.1, have at least two people with merge rights.
8. **Monthly "no work" weekends.** Actually literally don't touch the repo. First time a maintainer does this, users notice; second time they stop noticing — trained them.
9. **Public roadmap prevents "when is feature X?"** A visible roadmap saves 10 individual replies.
10. **Monetization path doesn't need to be activated, but should exist.** GitHub Sponsors button, "Enterprise support available" note — signals value, opens future options.

**Warning signs:**
- Issue count climbs faster than closure → triage bottleneck
- Feature requests average >1/week → scope creep incoming
- Maintainer commits 3 months in, then 2 weeks silent → early burnout symptom
- Forum/Discord asking "is this project alive?" → signal has been given
- First feature that was *not* in original scope added → slippery slope starts

**Phase to address:** P7 — OSS polish / governance. But this is cross-cutting — every phase should respect the scope.

**Severity:** Critical for project survival. Teraguchi has one committer (Randy) per `PROJECT.md`. This is THE risk.

---

## High Pitfalls

### Pitfall 11: VFX / Flame Industry Assumptions

**What goes wrong:**
Flame runs but specific monitor-config dialogs refuse to cooperate. Color-managed apps (Nuke, Baselight) break when the display-level color management changes. DaVinci Resolve dongles are required server-side and users can't attach. Wacom driver version that Flame 2026 expects is older than what the artist has installed — behaviors diverge.

**Why it happens:**
- **Flame expects "Real" monitors (with EDID).** `install-server.sh` notes a warning dialog about refresh rate, and Flame's own docs warn against the NVIDIA app managing displays [Autodesk Flame help on Linux configuration](https://help.autodesk.com/cloudhelp/2017/ENU/Flame-Installation/files/GUID-6FA721D6-9A14-4ABC-896B-164CA79BAB8D.htm).
- **60 Hz requirement in Flame 2026.** Refresh above 60 Hz triggers a warning [Autodesk Flame 2026 sysreqs](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/flame-2026-sysreqs.html).
- **Wacom tablet + "Use as Touchpad".** Flame docs explicitly say disable this. Users don't know.
- **Color management crosstalk.** macOS display color profile changes when night-mode kicks in. Critical-color app on server streams an accurate image but client-side display shifts.
- **Dongles (Sentinel HASP, iLok).** `PROJECT.md` defers these to v2. But some VFX shops run Resolve on the server with a server-side dongle, and if the user tries to attach their client-side dongle thinking it'll work → confused.
- **X11 window-manager expectations.** Flame was historically optimized for specific WMs (FVWM, KDE specifically). Xfce/GNOME3 animations break Flame's "grab the whole screen" expectations.

**How to avoid:**
1. **Ship CustomEDID for virtual displays.** Bake in EDID for NEC PA311D or similar Flame-approved reference monitors.
2. **Document the Flame-approved server topology.** `Rocky 9.4 + Xorg (not Wayland) + KDE or minimal WM + NVIDIA driver N.N + NvFBC capture + 60 Hz`. Explicit supported-config matrix.
3. **Color-management guidance in README.** "Teraguchi is a color pipe. Configure your server-side color profile (X11 `xprop _ICC_PROFILE`) and your client-side display (Mac Reference Mode / calibrated external) independently. Teraguchi does not touch either."
4. **Disable macOS auto display adjustments on client side.** Recommend users disable Night Shift, True Tone, Auto-Brightness during color-critical work.
5. **Shape user expectations on dongles.** Be explicit in README: "Server-side dongles work (they're on the server). Client-side license dongles are deferred to v2." `PROJECT.md` already says this — surface it in user-facing docs.
6. **Smoke-test Flame explicitly.** Dedicated CI task or manual smoke that launches Flame in the Teraguchi session and verifies: can enter project, tablet pressure works in paint mode, specific hotkeys like `F4` switch modules, Action module plays back at 24 fps.
7. **Don't fight Flame on WM.** Document the minimal WM that Flame wants. Don't ship a fancy desktop environment.

**Warning signs:**
- User: "Flame won't start, says unsupported monitor" → EDID problem
- Paint strokes land in wrong place in Flame but right place elsewhere → tablet topology
- Reviewers see different color than artist → macOS display adjustment
- User: "my Flame license didn't load" → client-side dongle attempt

**Phase to address:** P3 (display / topology) + P7 (Flame-specific documentation / smoke). Could merge into a "VFX workflow validation" phase.

**Severity:** High for Flame-specific behavior, because Flame is the core use case.

---

### Pitfall 12: Session Persistence / Reconnect Edge Cases

**What goes wrong:**
PCoIP's core feature is session persistence — disconnect the client, reconnect later, desktop intact. Teraguchi claims this. But there are edge cases: modifiers stuck (see Pitfall 4), server-side Xvfb crashes after 24 hours, USB devices don't reattach cleanly, cursor position confused, audio sink gone, clipboard state drifted.

**Why it happens:**
- **Per-user X session isolation in `server/session_manager.py` is 1317 lines of subprocess orchestration** (`CONCERNS.md`). Race conditions exist.
- **Xvfb/Xorg don't survive forever.** 24-48 hours is common. A Flame project that spans 3 days and the user expects to not lose state — but Xvfb has a memory leak.
- **Cursor re-attach.** `CursorTracker` polls XFixes at 30 Hz. On reconnect, client doesn't know current cursor shape until next poll → one frame of wrong cursor.
- **USB passthrough: `usbip attach` state doesn't persist across client disconnect.** Tangent panel forwarded, client disconnects, server still has the attach — but now there's no client to forward to.
- **Audio monitor source reattach.** PulseAudio monitor source lifetime tied to the session; if PulseAudio restarted → monitor source is new → capture reads from old one.
- **Clipboard change counter.** Client and server may have diverged while disconnected.
- **FreeIPA ticket expiration.** Long-running session, user's Kerberos ticket expires, `id -Gn` starts failing, access denied on reconnect.

**How to avoid:**
1. **Reconnect as a first-class protocol message.** `SESSION_RESUME` sent after auth — server responds with cursor shape, monitor list, current clipboard, modifier state. Single round-trip catch-up, not "wait and discover."
2. **Modifier reset on reconnect** (see Pitfall 4).
3. **Xvfb/Xorg long-session tests.** Run a simulated 72-hour session in CI (sped-up timers, chaos-test capture/encode/inject in a loop). `session_manager.py` has zero tests per `CONCERNS.md` — this is the highest-priority integration test.
4. **USB re-attach on reconnect.** Client re-announces attached devices. Server unbinds orphaned and rebinds to new client.
5. **Audio stream restarts on reconnect.** Cleanest: capture pipeline doesn't outlive the session entirely, but reattaches on first client.
6. **Clipboard sync is always "last write wins," with user confirmation on collision.** If both sides changed while disconnected, show a small dialog.
7. **Kerberos ticket refresh.** If broker is in use with FreeIPA, cache group membership (already done via `_group_cache` in `broker/freeipa.py`), but also warn when cache is stale and user may have lost permissions.
8. **Graceful "session expired" message** rather than silent disconnect when server-side X dies.

**Warning signs:**
- User reports "my keys don't work after I reconnect" → modifier or state desync
- User returns from lunch and has to reclick a cursor that moved → cursor reattach
- USB-attached panel not working after reconnect → usbip state
- Long-running session (>24 h) randomly dies → Xvfb leak

**Phase to address:** P1 stability + P5 networking reconnect.

**Severity:** High. PCoIP users expect this and will measure Teraguchi against that expectation.

---

### Pitfall 13: Security Posture (TLS / Auth / Token)

**What goes wrong:**
User's session is encrypted but trivially MITM'd — client accepts any cert. Attacker on the same Tailnet can spoof a server and steal PAM credentials. Bookmarks file leaks passwords on a shared machine. Brute-force attempts on server auth go uncounted.

**Why it happens:**
Every item here is already documented in `.planning/codebase/CONCERNS.md` as a pre-existing issue. They are not new pitfalls — they are known-and-documented, but they are pitfalls for v1 if not addressed:

- TLS verification disabled everywhere (`check_hostname=False`, `verify_mode=CERT_NONE`).
- Bookmark "encryption" is XOR.
- SHA-256 + salt password hashing with no bcrypt/argon2, no rate limiting.
- Auth timeout is 30 s fixed, no per-IP backoff.
- Broker admin UI auth-cache is SHA-256(username:password) with no pepper.
- Broker secret auto-generated ephemeral on missing file.

**How to avoid:**
1. **Pin TLS certificates.** Ship a self-signed CA in the client installer, verify server certs against it. Or document one-time trust-on-first-use flow (print cert fingerprint on server, user confirms in client).
2. **argon2id password hashing.** `argon2-cffi` drop-in. Migrate legacy SHA-256 hashes on next login.
3. **Per-IP backoff on auth failures.** Track `{ip: (fail_count, last_fail_time)}` dict. Reject >5 fails in 60 s.
4. **Replace bookmark XOR with keyring.** macOS Keychain via `keyring` Python package.
5. **Broker secret bootstrapped by installer, not auto-generated.** If missing, fail hard with explicit instructions.
6. **Document security model explicitly.** `SECURITY.md` stating threat model: "Teraguchi assumes the Tailnet is trusted. Outside Tailnet, users must manage cert trust manually."

**Warning signs:**
Any of the concerns in `CONCERNS.md` "Security Considerations" section remaining unresolved at v1 release.

**Phase to address:** P1 (auth rate limiting), P6 (TLS pinning + distribution).

**Severity:** High. Small-studio threat model is low, but public readme will be scrutinized.

---

### Pitfall 14: Test Coverage Gaps

**What goes wrong:**
Any refactor breaks something silently. Wire-format drift between client and server goes undetected. Auth bypass on broker token regression, unnoticed. Maintainer burns time manually testing each PR.

**Why it happens:**
`TESTING.md` states it plainly: "This project has no automated test suite." `requirements-dev.txt` declares pytest, but zero test files exist. No `tests/`, no `conftest.py`, no CI. Every commit reaches `dev` unvalidated.

**How to avoid:**
Follow the natural-seams recommendation already in `TESTING.md`:

1. **Start with pure-function unit tests** (low-hanging fruit, high-value):
   - `common/messages.py` serialization / deserialization.
   - `common/keymap.py` qt→scancode table.
   - `QualitySettings.effective_*` methods.
   - `broker/tokens.py` HMAC generation and verification.
   - `server/auth.py` local-mode challenge/response.

2. **Platform-parity contract tests** — assert `ScreenCapture` / `InputInjector` / `ClipboardSync` have matching public interfaces on Linux and macOS via signature comparison.

3. **Integration tests for session lifecycle** (containerized Xvfb) — spawn PAM mock + Xvfb + session, assert capture/inject work, assert cleanup.

4. **GitHub Actions CI on Mac + Rocky runners.** `PROJECT.md` already includes this as v1 scope. Hold the line.

5. **Regression tests for every bug fix.** When a user reports a bug, first write the failing test. Kills "same bug keeps coming back."

**Warning signs:**
PRs merged without CI running. Same bug reported twice. Refactor breaks something "mysterious."

**Phase to address:** P1 — CI + test baseline. First-class work, not a nice-to-have.

**Severity:** High. Every pitfall above becomes worse without tests to catch regressions.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|---|---|---|---|
| Ship JPEG fallback path alongside H.264 | Works without FFmpeg, easier first-run | `CONCERNS.md` flags this as dead-weight; doubled maintenance burden; dirty-rect Python code on both Linux + macOS | Never — FFmpeg is a hard dependency of the installer. Delete. |
| `--auth-mode none` on macOS as the default | Gets the Mac server working quickly | Silent insecurity, easy to forget it's insecure, no per-user sessions | Only for the single-user-on-own-hardware case. Document loudly. |
| `verify_cert=False` on all TLS | Avoids cert-management UX in v0 | MITM exposure; users assume "TLS = secure" and it isn't | Never at v1. Ship a self-signed CA or TOFU. |
| `DISPLAY` env-var manipulation | Gets Xvfb/Xorg selection working | Thread-unsafe per `CONCERNS.md` — concurrent sessions race. | Only for single-session test code. Never in production path. |
| NumPy dirty-rect diffing on every frame | Makes JPEG fallback work | 33 MB numpy op per 4K frame, heavy CPU | Delete the whole path. See JPEG fallback above. |
| `send_queue: asyncio.Queue(maxsize=30)` | "Plenty of room" for slow clients | 1 s of backlog = stale video + artist sees laggy stream | Never at that size. Use `maxsize=2` and request keyframe on drop. |
| Local JSON user DB (`--auth-mode local`) | PAM-free testing path | `server/auth.py:106` marks it legacy; SHA-256 not argon2; no rate limit | Dev/test only. Remove the installer option. |
| Hard-coded Randy-workspace path in `Teraguchi.app/MacOS/Teraguchi` | Worked on dev machine | Fresh clone has broken app | Never — either relative path or uncommitted. |
| Anonymous LDAP bind fallback | FreeIPA integration "just works" | Confusing logs, unclear failure modes | Never — require GSSAPI or a service account. |
| `keydiag.py` and in-app diagnostics instead of unit tests | Fast feedback on live hardware | Doesn't catch regressions between releases | Keep alongside tests, never instead of tests. |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|---|---|---|
| FFmpeg via PyAV | Assume system FFmpeg will match PyAV's ABI | Bundle FFmpeg in PyInstaller; pin PyAV to a specific wheel |
| NVIDIA NVENC | Assume Main10 is always supported | Probe at runtime with test encode; advertise capability to client |
| NVIDIA NvFBC | Assume the library is present on all hosts | Fallback chain: NvFBC → NVENC+DRM-PRIME → XShm → `mss` |
| PipeWire vs PulseAudio | Assume one interface works on all Rocky versions | Use PipeWire's `pipewire-pulse` compat layer; test both explicitly |
| FreeIPA | Assume GSSAPI bind always works | Explicit service-account auth; never rely on anonymous bind |
| Tailscale MagicDNS | Assume `host.tailnet.ts.net` resolves everywhere | Document user-side MagicDNS enablement; document corporate-DNS override |
| ScreenCaptureKit | Assume BGRA is fine | Request P010 / `kCVPixelFormatType_420YpCbCr10BiPlanarFullRange` explicitly for 10-bit [Apple TN3121](https://developer.apple.com/documentation/technotes/tn3121-selecting-a-pixel-format-for-an-avcapturevideodataoutput) |
| VideoToolbox HEVC | Assume hardware Main10 decode on all Macs | Check via `VTIsHardwareDecodeSupported`; fall back gracefully with warning |
| macOS TCC (Screen Recording) | Assume the prompt fires reliably | Pre-check with `CGPreflightScreenCaptureAccess`; show explicit dialog if denied |
| Apple Notary | Assume each submission completes | Implement retry; cache submission UUIDs; alert on >1h stall |
| RPM signing | Sign the RPM only | Sign the RPM *and* publish the public key; test `dnf install` from a fresh Rocky 9 VM |
| Qt QTabletEvent on Mac | Assume Wacom driver posts every event | Run the in-app diagnostic; watchdog for pressure-stuck |
| USB/IP | Assume `bus_id` is always safe | Validate `bus_id` regex (already flagged in `CONCERNS.md`) |
| `subprocess.run` to ffmpeg | Assume stderr can be ignored | Capture and log stderr; timeout on stall |
| `asyncio.run_coroutine_threadsafe` | Call `.result()` from the event loop thread | Call only from worker threads; never block the loop |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|---|---|---|---|
| Per-client frame serialization | CPU climbs with each added client | Shared broadcast queue; serialize encoded frame once | >2 clients per session |
| Dirty-rect NumPy diff on every frame | 25–50% CPU on one core, 4K @ 30 fps | Disable when H.264 path is active; port to C if must keep | 4K resolution |
| Mac SCK padding-strip per frame | Capture thread CPU-bound | Stride-aware FFmpeg input; or ctypes memmove | 4K HDR |
| Broker health probe: new aiohttp.ClientSession per probe | TLS handshakes every 15 s × N machines | Long-lived session | Any production deployment |
| Unbounded send queue on slow clients | Memory grows; frames pile up | `maxsize=2`, drop-oldest + request-keyframe | First client on congested network |
| ffmpeg stdin pipe buffering | Encoder latency 50 ms+ | Small input buffer; feed per-frame, not batched | Always |
| Python GIL on capture + encode + send | Multi-core CPU idle, one core at 100% | FFmpeg subprocess offloads encode; capture in dedicated thread; be careful about `asyncio` + blocking calls | Always |
| Qt paint on main thread with large QImage | UI frame drops | OpenGL/Metal widget for video; QImage only for overlays | HiDPI + 4K |
| Polling (`mac_clipboard.py` 250 ms) | Copy→paste misses | Don't poll from the "fast" path; accept small delay on clipboard | Not a real problem |
| String parsing `/proc/bus/input/devices` | Silent failure on kernel format change | Use `libevdev` directly via ctypes | Rare but painful |

## Security Mistakes

| Mistake | Risk | Prevention |
|---|---|---|
| TLS CERT_NONE everywhere | MITM on tailnet or public network | Ship CA in installer; pin server cert in bookmarks |
| Bookmark XOR encryption | Credential theft from shared machine | Use OS keychain via `keyring` package |
| SHA-256 password hashing, no rate limit | Offline cracking + online brute-force | argon2id + per-IP backoff |
| Broker admin auth cache with plain SHA-256 | Password recovery from process memory | Per-process pepper; cache by expiry only |
| `--auth-mode none` silent default on macOS | Session wide open | Require explicit confirmation; UI warning |
| Ephemeral broker HMAC secret | All tokens invalidated on restart; silent auth bypass window | Installer-generated persistent secret |
| No cert pinning on broker → server health probe | Broker trusts spoofed "server" | Pin by SHA-256 fingerprint in broker config |
| USB bus_id injection surface | Subprocess arg injection | Regex-validate `^\d+-\d+(\.\d+)*$` |
| Clipboard image passthrough without validation | Malicious image triggers decoder bug | Validate magic bytes; size cap |
| File transfer without path validation | Server writes outside user home | Reject `..` in filenames; resolve to `~/Desktop` only |
| LaunchAgent on macOS with no hardening | Service has full user context | Sandbox with entitlements; document least-privilege model |
| FreeIPA anonymous bind fallback | Misleading log "auth succeeded" | Remove fallback; require explicit service account |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---|---|---|
| Black screen on TCC denial | Looks broken; no guidance | Dialog: "Screen recording permission required — open System Settings" |
| "Connected" shown while video frozen | User blames the app, tries reconnecting uselessly | "Connected" requires recent frame; otherwise "reconnecting" |
| Latency number shown as "50 ms" | User doesn't know if that's good or bad | Color-code: green <30, yellow 30-80, red >80. Include per-stage breakdown |
| No visible encoder fallback indicator | User picks 10-bit, gets 8-bit, doesn't know | Health overlay shows "10-bit: confirmed" or "DEGRADED (8-bit fallback)" |
| Silent USB detach on reconnect | Loupedeck stops working, unclear why | Show list of forwarded USB devices with status; explicit reattach button |
| Right-click-Open instructions in README | Signals "amateur project" | Signed + notarized; proper `open` works |
| Config file in YAML users have to edit | Typo = broken install | UI dialog for common settings; YAML for advanced |
| Bookmark password "encryption" claim | User trusts it more than they should | Change name in UI to "obscured" or just "stored"; recommend keychain |
| Per-connect monitor picker wall of text | Cognitive load every connect | Remember last choice per-bookmark; "Change" button reveals full picker |
| Test pattern is invisible on 10-bit assertion | User can't verify 10-bit themselves | Ship an "About this session" diagnostic that shows color pipeline details |
| Help docs live in GitHub issues | Search doesn't work; knowledge siloed | Dedicated docs site; versioned with releases |
| macOS TCC nag every launch | Eventually users grant every permission | Stable bundle ID; single ask at onboarding |

## "Looks Done But Isn't" Checklist

Things that demo well but miss a critical piece. Verify each during v1 readiness review.

- [ ] **10-bit pipeline:** Demo shows "10-bit selected" but never verifies bits below position 8 survive a round trip — verify with a known test pattern and ffprobe on a captured stream.
- [ ] **Wacom pressure:** Works in a Photoshop demo but not in Flame — verify specifically in Autodesk Flame's paint mode at server-side, with all of Intuos Pro, Cintiq Pro 16, Cintiq Pro 24.
- [ ] **Reconnect:** "It reconnects" but didn't reset modifiers — verify `Ctrl+Shift+Alt` held at the moment of disconnect doesn't leave server-side modifiers stuck.
- [ ] **Multi-monitor:** Works on two external 1080p but not on Retina + 4K — verify on the mixed-DPI configuration specifically.
- [ ] **Audio:** Works for 5 minutes in dev but not for 30 minutes — verify with a 60-minute session smoke test.
- [ ] **Audio echo:** Works with headphones but not with speakers — verify with client-side near-field speaker + mic setup.
- [ ] **USB/IP reconnect:** Attach works but reattach-after-disconnect doesn't — verify reconnection workflow.
- [ ] **TLS:** `wss://` works but client accepts any cert — verify cert rejection on a fake cert.
- [ ] **RPM install:** Works from local file but not from repo — verify GPG key trust and `dnf install` from a fresh Rocky 9 VM.
- [ ] **macOS notarization:** DMG mounts but is flagged "damaged" — verify stapling with `stapler validate` and Gatekeeper with `spctl --assess`.
- [ ] **Captive-portal network:** Works on home Wi-Fi but not at hotel — verify with a simulated captive-portal test.
- [ ] **Hotkeys:** All Flame hotkeys work in artificial test, but `Ctrl+Shift+Alt+M` doesn't in real session — verify with the extended `keydiag.py` covering all Flame chords.
- [ ] **Signed release:** Signing works locally but not on CI — verify the CI-built artifact installs on a fresh machine.
- [ ] **FreeIPA integration:** Works with GSSAPI but silently falls back to `id -Gn` — verify explicit GSSAPI success in logs.
- [ ] **`/status` endpoint:** Returns JSON but `uptime_s=0` on Mac — verify platform-aware implementation.
- [ ] **File transfer:** Works for a 10 MB PDF but fails silently on a 2 GB file — verify large-file and path-traversal edge cases.

## Recovery Strategies

When pitfalls happen despite prevention, how to recover fast.

| Pitfall | Recovery Cost | Recovery Steps |
|---|---|---|
| Stuck modifier on reconnect | LOW | Client sends explicit `KEY_RESET_MODIFIERS`; also accessible as a UI button "Release All Keys" |
| 10-bit degraded silently | MEDIUM | Health overlay red-status warns user; user reconnects or selects 8-bit explicitly; report via in-app bug report |
| Video frozen but "connected" | LOW | Auto-detect via media watchdog; auto-reconnect with modifier reset |
| Xvfb/Xorg crash after long session | HIGH | Session manager watchdog; auto-restart session with warning to user; session state partially lost |
| Audio silent | MEDIUM | Watchdog detects; auto-reinit capture pipeline |
| Cursor offset on HiDPI | LOW | User can toggle "use device pixels" in UI |
| TCC denied on Mac | LOW | Dialog with step-by-step link to System Settings |
| Notary failure on release | MEDIUM | Runbook in repo; retry with fresh submission UUID |
| SELinux denial at install | MEDIUM | `audit2allow -M teraguchi < /var/log/audit/audit.log` → load module; document in install docs |
| Wacom stuck mid-stroke | LOW | Re-raise proximity event on focus; UI button "Reset Tablet" |
| Tailscale relay path | LOW | Health overlay shows relay status; user investigates their Tailscale config |
| Captive portal re-auth | LOW | Detect via media watchdog + captive-portal check; notify user to re-login |
| Bundle ID change breaks TCC | HIGH | Every user has to re-grant permissions; avoid via stable bundle ID from start |
| Broker secret rotated | HIGH | All existing sessions die; restart clients with new token |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---|---|---|
| Latency death by a thousand cuts | P1 Stability / latency baseline | Per-stage latency histogram in health overlay; CI perf regression test |
| 10-bit pipeline degrades silently | P2 Color fidelity | End-to-end 10-bit test pattern round-trip test; bit-exact assertion |
| Wacom pressure bugs | P2 Input fidelity | Extended `keydiag.py` with tablet module; smoke matrix Intuos + Cintiq |
| Modifier mangling | P2 Input fidelity | `qt_key_to_linux_scancode` table tests; reconnect-with-held-modifier test |
| Multi-monitor / DPI | P3 Display / topology | Mixed-DPI smoke matrix; CustomEDID; document per-session topology selection |
| Audio 30-min failures | P4 Audio hardening | 60-minute audio continuity smoke test; echo-cancel test; device-change test |
| Network roaming / MTU | P5 Network / transport | Hotel-Wi-Fi network emulator test; path-MTU probe; captive-portal detection |
| Env assumptions / works-on-my-machine | P1 CI + installer | Fresh-VM install smoke on Mac + Rocky 9 GHA runners |
| Signing / notarization rabbit hole | P6 Distribution hardening | Signed artifacts built in CI; stapling verified; `spctl --assess` test |
| Solo maintainer burnout | P7 OSS polish / governance | `CONTRIBUTING.md`, `SECURITY.md`, issue templates, stale-bot, second committer invited |
| VFX / Flame industry specifics | P3 + P7 (Flame-specific docs) | Dedicated Flame smoke workflow; EDID bake-in; WM recommendation |
| Session persistence / reconnect | P1 + P5 | Long-running session integration test; reconnect-with-state-sync test |
| Security posture | P1 + P6 | argon2id migration; per-IP rate limit test; TLS pin verification |
| Test coverage gaps | P1 (baseline) | >50% line coverage on `common/`, `broker/tokens.py`, `server/auth.py` at v1 |

**Suggested phase ordering (orchestrator decides final roadmap):**
1. **P1: Stability + CI + test baseline.** No real progress on anything else until this exists.
2. **P2: Input & color fidelity.** The *user-visible* core value of the project.
3. **P3: Display / multi-monitor.** Expected feature parity with PCoIP.
4. **P4: Audio.** Client-review workflow enabler.
5. **P5: Network / transport hardening.** Travel scenarios.
6. **P6: Distribution (signing, notarization, RPM).** Go-to-market.
7. **P7: OSS polish / governance.** Sustainability.

## Historical Failures — Why Other PCoIP Replacements Fell Short

Worth studying as cautionary examples:

- **TurboVNC / TigerVNC.** Excellent bandwidth tech (JPEG tiles + background RLE), but zero input fidelity for Wacom, no 10-bit path, no modifier-heavy hotkey perfection. Lives on as a "good enough for office use" VNC variant [TurboVNC about page](https://turbovnc.org/About/TigerVNC). Never threatened PCoIP because Flame artists rejected it.
- **x11vnc.** Reuses existing X display rather than spawning per-user, which is the opposite of what PCoIP does. Screen is whatever the logged-in user sees. Wrong architecture for multi-user studio servers.
- **xpra ("screen for X").** Persistent sessions + H.264, closer to the right idea, but input latency never dropped below ~80 ms for interactive use. Audio integration was always flaky [xpra high-latency examples](https://feeding.cloud.geek.nz/posts/high-latency-vnc-tech-support/).
- **Moonlight + Sunshine.** Actually solved the video latency problem beautifully (~15 ms LAN). But targeted gaming — the assumption is "no Wacom, no color critical, no modifiers beyond WASD+Ctrl+Shift." Multi-monitor is flaky, clipboard/file/USB are not really there. Teraguchi's lessons-learned bucket: start with Sunshine-class latency and add the creative-workflow concerns on top.
- **Apache Guacamole / Chrome Remote Desktop / similar browser clients.** Browser is a poor client for color-critical work (browser's compositor touches the pixels). Non-starter for Flame.
- **NICE DCV.** Technically excellent but proprietary, $$, and not Mac-client-friendly in the way creative users need.
- **Parsec.** Great for gaming, has a business model, but Flame artists report "feels okay, not perfect" and the color/pressure fidelity isn't quite there.

**Common thread of failures:** They optimized for one axis (bandwidth, or latency, or persistence) and let others drift. Teraguchi's challenge is doing *all* axes well enough simultaneously.

## Sources

- Existing codebase concerns: `.planning/codebase/CONCERNS.md`, `.planning/codebase/TESTING.md`, `.planning/codebase/ARCHITECTURE.md`, `.planning/PROJECT.md`
- [Moonlight/Sunshine: frame pacing and NTSC capture rate (#2286)](https://github.com/LizardByte/Sunshine/issues/2286)
- [Moonlight macOS 26 decode latency (#1696)](https://github.com/moonlight-stream/moonlight-qt/issues/1696)
- [Sunshine optimal encoder guide](https://pulsegeek.com/articles/optimize-sunshine-encoder-for-the-moonlight-client/)
- [Parsec latency technology](https://parsec.app/blog/description-of-parsec-technology-b2738dcc3842)
- [Parsec troubleshooting latency](https://support.parsec.app/hc/en-us/articles/32381352822804-Troubleshooting-Lag-Latency-and-Quality-Issues)
- [WebRTC vs WebSocket A/V sync analysis](https://getstream.io/blog/webrtc-websocket-av-sync/)
- [MediaMTX WebRTC B-frame / HoL discussion](https://github.com/bluenviron/mediamtx/discussions/2818)
- [MediaMTX WebRTC blocking issue](https://github.com/bluenviron/mediamtx/issues/2937)
- [NVENC 10-bit / Main10 encoder creation (NVIDIA forums)](https://forums.developer.nvidia.com/t/nvenc-10bit-hevc/199535)
- [OBS: NVENC cannot perform 10-bit encode (#165772)](https://obsproject.com/forum/threads/nvenc-error-cannot-perform-10-bit-encode-on-this-encoder.165772/)
- [HandBrake NVENC 10-bit support (#3901, #4447)](https://github.com/HandBrake/HandBrake/issues/3901)
- [iina 10-bit HEVC P010 stutter (#5796)](https://github.com/iina/iina/issues/5796)
- [Apple WWDC24 "Capture HDR content with ScreenCaptureKit"](https://developer.apple.com/videos/play/wwdc2024/10088/)
- [Apple Technote TN3121: AVCapture pixel formats](https://developer.apple.com/documentation/technotes/tn3121-selecting-a-pixel-format-for-an-avcapturevideodataoutput)
- [Apple WWDC21 "Explore HDR rendering with EDR"](https://developer.apple.com/videos/play/wwdc2021/10161/)
- [Apple WWDC22 "Explore EDR on iOS"](https://developer.apple.com/videos/play/wwdc2022/10113/)
- [Prolost on Apple EDR](https://prolost.com/blog/edr)
- [Qt HighDPI documentation](https://doc.qt.io/qt-6/highdpi.html)
- [Qt tablet event documentation](https://doc.qt.io/qt-6/qtabletevent.html)
- [Qt focus documentation](https://doc.qt.io/qt-6/focus.html)
- [autokey Qt focus-lost bug (#169)](https://github.com/autokey/autokey/issues/169)
- [Mozilla Bugzilla mixed-DPI Mac (794038)](https://bugzilla.mozilla.org/show_bug.cgi?id=794038)
- [Wacom macOS troubleshooting](https://support.wacom.com/hc/en-us/articles/1500006343942-Why-is-my-tablet-not-working-on-Mac-OS)
- [Wacom Cintiq Pro sleep-wake disconnect](https://support.wacom.com/hc/en-us/community/posts/30167202319639-Cintiq-Pro-24-PT-Disconnects-after-System-Wakes-Up-from-Hibernation)
- [Apple community: Wacom on new Mac](https://discussions.apple.com/thread/251419723)
- [Tailscale: peer MTU discovery (#311)](https://github.com/tailscale/tailscale/issues/311)
- [Tailscale: captive portal support (#1634)](https://github.com/tailscale/tailscale/issues/1634)
- [Tailscale: MTU on GCP (#246)](https://github.com/tailscale/tailscale/issues/246)
- [WireGuard MTU fix analysis (Kerem Erkan)](https://keremerkan.dev/posts/wireguard-mtu-fixes/)
- [NVIDIA Xorg SIGSEGV on HDMI disconnect](https://forums.developer.nvidia.com/t/xorg-sigsegv-nvidia-drm-warning-on-hdmi-hotplug-disconnect-rtx-5090-max-q-gb203m-reproducible-across-580-119-02-580-126-18-580-142-595-58/366327)
- [Autodesk Flame 2026 system requirements](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/flame-2026-sysreqs.html)
- [Autodesk Flame Linux configuration (Wacom disable touchpad note)](https://help.autodesk.com/cloudhelp/2017/ENU/Flame-Installation/files/GUID-6FA721D6-9A14-4ABC-896B-164CA79BAB8D.htm)
- [Autodesk Flame Linux install (Rocky 9.3/9.5)](https://help.autodesk.com/cloudhelp/2024/ENU/FLAME-install-software-os/files/installation/Linux/INSTALL_LINUX.html)
- [PipeWire echo-cancel module docs](https://docs.pipewire.org/page_module_echo_cancel.html)
- [PyInstaller macOS hardened runtime crash (#4629)](https://github.com/pyinstaller/pyinstaller/issues/4629)
- [PyInstaller Apple notarization issue (#7937)](https://github.com/pyinstaller/pyinstaller/issues/7937)
- [Haim Dev: Signing and notarizing a Python macOS UI app](https://haim.dev/posts/2020-08-08-python-macos-app)
- [Apple notarization troubleshooting docs](https://developer.apple.com/documentation/security/resolving-common-notarization-issues)
- [Rocky Linux GPG keys](https://rockylinux.org/resources/gpg-key-info)
- [RHEL DNF GPG check troubleshooting](https://oneuptime.com/blog/post/2026-03-04-fix-gpg-check-failed-installing-rpm-packages-rhel/view)
- [Rocky/RHEL SELinux troubleshooting](https://mindfulchase.com/explore/troubleshooting-tips/operating-systems/troubleshooting-red-hat-enterprise-linux-fixing-dnf-errors,-selinux-blocks,-systemd-failures,-kernel-module-issues,-and-subscription-conflicts.html)
- [Kernel uinput documentation](https://www.kernel.org/doc/html/v4.12/input/uinput.html)
- [evremap uinput permission issue (#21)](https://github.com/wez/evremap/issues/21)
- [TurboVNC about / history](https://turbovnc.org/About/TigerVNC)
- [Xpra high-latency tech support](https://feeding.cloud.geek.nz/posts/high-latency-vnc-tech-support/)
- [Socket.dev: solo OSS maintainers report](https://socket.dev/blog/the-unpaid-backbone-of-open-source)
- [XDA: single-maintainer OSS time bomb](https://www.xda-developers.com/single-maintainer-open-source-ticking-time-bomb/)
- [Open Source Guide: maintaining balance](https://opensource.guide/maintaining-balance-for-open-source-maintainers/)
- [Open Source Pledge: burnout is structural](https://opensourcepledge.com/blog/burnout-in-open-source-a-structural-problem-we-can-fix-together/)
- [asyncio memory-leak bug reports (bpo-32574, bpo-31620)](https://bugs.python.org/issue32574)
- [asyncio Queue thread-safety discussion](https://discuss.python.org/t/can-asyncio-queue-be-safely-created-outside-of-the-event-loop-thread/49215)

---
*Pitfalls research for: high-performance open-source remote workstation (PCoIP replacement) for VFX/Flame, macOS→Rocky and macOS→macOS*
*Researched: 2026-04-18*
