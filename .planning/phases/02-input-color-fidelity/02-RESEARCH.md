# Phase 2: Input + Color Fidelity — Research

**Researched:** 2026-04-18
**Domain:** 10-bit HEVC Main10 color pipeline on macOS + Rocky 9; IOHIDUserDevice virtual tablet on macOS; exhaustive Qt × modifier × platform keymap correctness; Wacom hardware verification
**Confidence:** HIGH on stack prescription (Phase 1 settled these), HIGH on the Apple and NVIDIA documentation chain, MEDIUM on IOHIDUserDevice practical behavior (community-documented, not Apple-blessed), MEDIUM on QRhiWidget 10-bit blit (correct direction, specific shader/sampler details need spike verification)

---

## Summary

Phase 2 is dominated by four *technically independent* streams that the planner should sequence in parallel waves but *interlock at verification time*: (1) close the 9 silent-downgrade points with a bit-exact CI fixture (VIDEO-01..12), (2) replace the macOS pen stub with an IOHIDUserDevice virtual tablet gated on a pre-phase spike (INPUT-08..12), (3) land exhaustive Qt × modifier × platform keymap coverage plus release-all-modifiers plumbing and `TextCommit` IME passthrough (INPUT-01..07), (4) prove the whole thing on the 4-cell DXS Wacom matrix with an RMS<1% pressure quantization gate (INPUT-09..12 + D-21 latency regression check). The 21 decisions in `02-CONTEXT.md` pin every significant architectural choice; research scope is **how to implement them correctly**, not whether alternatives exist.

The biggest unknowns, in descending risk order: (a) **QRhiWidget P010 upload on macOS Metal** — Qt 6.10 exposes the widget and `QRhiTexture` but the platform-specific planar YUV sampler path is not covered by a canonical Qt example; a one-day spike likely required and `QOpenGLWidget`+`MTLPixelFormatBGR10A2Unorm` is a proven escape hatch [CITED: doc.qt.io]. (b) **IOHIDUserDevice pressure visibility in arbitrary apps** — the community pattern is proven for VMs (Wacom-qemu) but TCC/signing behavior for a userspace process injecting a digitizer is under-documented; D-07/D-08 already encode the 2-day spike + fail-path so this risk is bounded [ASSUMED]. (c) **VTCompressionSession HEVC low-latency on Apple Silicon** — `EnableLowLatencyRateControl` was H.264-only historically but Apple Silicon supports it for HEVC, and a FFmpeg patch in April 2025 enables the same in `hevc_videotoolbox`; proceed with the direct-VT path per D-04 but plan a regression fixture that verifies the low-latency flag actually took effect [CITED: Apple WWDC21 10158, FFmpeg devel ML 2025]. (d) **NVENC Main10 silent fallback** — confirmed real failure mode; D-03's refuse-and-overlay design is the correct response, backed by an `NvEncGetEncodeCaps` probe at startup [CITED: NVIDIA VCSDK 13 docs].

**Primary recommendation — implementation order:**

1. **Wave 0 (pre-phase, parallelizable within Wave 0):** IOHIDUserDevice 2-day spike (D-07/D-08 gates downstream INPUT-08 scope); generate + commit the 9-checkpoint 10-bit ramp fixture binary (D-01 asset only, no wiring yet); write/update `tests/common/test_keymap.py` skeleton (D-12 shape).
2. **Wave 1 (foundations — parallel):** Capability probe + `ServerHelloMsg` extension + health-overlay badge (D-03); `ENCODER_DEFS` edits to expose `supports_main10`/`supports_422`/`supports_444` flags (Claude's Discretion); exhaustive keymap test table (D-12); `KEY_RESET_MODIFIERS` + `TextCommit` + `PenProximity` wire format + `KeyEvent` lock-state bit extension (D-11/D-14/D-15/D-19).
3. **Wave 2 (pipeline — partially parallel):** ScreenCaptureKit 10-bit config (D-01 checkpoint 1 / VIDEO-08); encoder input + NVENC Main10 + `hevc_nvenc` args (D-05 / VIDEO-03/-05); `server/mac_video_encoder.py` direct VTCompressionSession wrapper (D-04 / VIDEO-04); PyAV 10-bit decode assertion (D-01 checkpoint 5 / VIDEO-12); capability-flagged 4:2:2 path (D-05).
4. **Wave 3 (client display, single-threaded — largest change):** Qt QRhi Metal P010 upload + YUV→RGB shader + final blit in `client/viewer.py` (D-02 / VIDEO-01 checkpoints 7-8). Wave 3 gates Phase-2-ship because it is the only honest path from decoder to pixels.
5. **Wave 4 (pen):** `server/mac_pen_injector.py` if spike passed; `mac_input_injector.pen_event` delegation thinning; `client/key_diagnostic.py` Wacom/TCC tab (D-09/D-20); server-side pen FSM or extension (D-19).
6. **Wave 5 (integration tests):** Crazy-hotkey integration test (INPUT-04); focus-stress + reconnect-stress (INPUT-02/-03); 9-checkpoint bit-exact CI fixture wired as a single end-to-end test (VIDEO-02).
7. **Wave 6 (hardware gate):** 4-cell Wacom matrix manual session + `tools/wacom_quant_analysis.py` RMS computation (D-17/D-18); DXS one-shot pre/post latency measurement (D-21); commit numbers to `docs/release.md`.

**Do not skip Wave 0.** Every downstream Wave 1-6 task gains signal-to-noise from the spike outcome, the committed ramp fixture, and the keymap table skeleton.

---

## User Constraints (from CONTEXT.md)

### Locked Decisions

The 21 decisions (D-01..D-21) in `.planning/phases/02-input-color-fidelity/02-CONTEXT.md` are NOT subject to research re-litigation. The planner must consume them verbatim. Paraphrased below for planner convenience; authoritative text lives in CONTEXT.md.

**Color Fidelity Strategy (VIDEO-01..12):**

- **D-01** Test fixture = 9-checkpoint byte-equality on a generated 10-bit ramp, covering: (1) capture surface format, (2) encoder input, (3) encoder output (`ffprobe` on a dumped sample asserts `pix_fmt=p010le`/`yuv420p10le`), (4) transport wire bytes (H.265 NAL profile field), (5) decoder output (`AVFrame.format`), (6) decoder hwaccel path (VT decode not falling to software for P010), (7) Qt display-surface texture format, (8) final blit (Metal YUV→RGB shader preserves 10-bit), (9) macOS display-pipeline state (Reference Mode / EDR documented-if-accepted). One regression fails CI. This is VIDEO-02 literally.
- **D-02** Client display path = Metal/QRhi direct texture upload. Decode to P010 / YUV420P10LE via PyAV. Upload as Metal texture through `QRhi` (Qt 6.10's cross-platform render interface, Metal on macOS). Biggest client-side change in Phase 2. Replaces `QPainter.drawImage(QImage)` blit path in `client/viewer.py` for the video layer; QImage retained for overlays + cursor.
- **D-03** Hardware capability probe = refuse + explicit overlay. Server startup probes actual encoder capability; if hardware cannot encode HEVC Main10 4:2:0, server refuses to advertise 10-bit in `ServerHelloMsg`. Client health overlay shows explicit `"10-bit: negotiated / confirmed / DEGRADED / not supported"` badge.
- **D-04** VTCompressionSession direct PyObjC wrapper = full wrapper in Phase 2 (VIDEO-04). Write `server/mac_video_encoder.py` using `pyobjc-framework-VideoToolbox`: `VTCompressionSessionCreate` with `kVTVideoEncoderSpecification_EnableLowLatencyRateControl=True`, one-in-one-out pipeline, no frame reordering, `kVTProfileLevel_HEVC_Main10_AutoLevel`, 4:2:2 `Main42210` profile on M3+ when probe confirms. Saves 5–8ms/frame vs. FFmpeg's `hevc_videotoolbox` per WWDC21 session 10158.
- **D-05** Chroma subsampling = 4:2:0 default, 4:2:2 opt-in where hardware confirms (Blackwell NVENC + M3+ Mac decode / M4 Mac encode). 4:4:4 stays in `ENCODER_DEFS` for hardware that supports it but is NOT user-facing in v1.
- **D-06** ICC / display calibration = out of scope, document only. Phase 2 ships 10-bit pass-through only; Phase 7 docs tell grading users to enable Reference Mode on MBP XDR.

**macOS Pen Pressure (INPUT-08):**

- **D-07** IOHIDUserDevice spike failure path = document + ship. 2-day pre-phase spike; if it fails, INPUT-08 gets re-scoped to "Mac-server pen pressure is a known v1 limitation"; Rocky remains Flame-production path.
- **D-08** Spike pass bar = end of day 2, an unsigned Python script using `pyobjc-framework-IOKit` creates a virtual HID tablet that **Photoshop / Preview / a simple NSView test app reads non-zero `NSEvent.pressure` from across a complete pen stroke**. If this works in 2 days, Phase 2 productionizes the helper.
- **D-09** Injector module split = new `server/mac_pen_injector.py`. Keep `server/mac_input_injector.py` for mouse / keyboard / scroll. `platform_backends.InputInjector` composes both. Mirrors Linux split (`input_injector.py` vs `xtest_injector.py`).

**Hotkey Correctness (INPUT-01..07):**

- **D-10** Cmd↔Ctrl translation default = auto-swap, per-bookmark override. Mac Cmd→Linux Ctrl (default when destination is Linux); Mac Ctrl passes through. Per-bookmark checkbox "Swap Cmd/Ctrl for this server". Active swap surfaced in health overlay. Single authoritative table in `common/keymap.py`.
- **D-11** Release-all-modifiers fires on four triggers: `RemoteViewer.focusOutEvent` / Qt `WindowDeactivate`; ConnectionSupervisor reconnect (first post-auth message); server-side periodic safety net (~10s idle); client-side F9 panic shortcut. All four share the same server-side idempotent reset path (`InputInjector.reset_modifiers()` already exists from Phase 1). Wire to new `KEY_RESET_MODIFIERS` message type.
- **D-12** Keymap test scope = exhaustive Qt → Linux + Mac × all modifier chords. Every key in `common/keymap.py` × {none, Shift, Ctrl, Alt, Meta, every 2-key and 3-key combination} × {Linux scancode, Mac virtual key code}. ~2000 parameterized pytest cases. Named Flame-critical-hotkey subset marked for INPUT-04 integration test.
- **D-13** Server-side key repeat = `xset r off` + client-driven repeats. Linux X server never auto-repeats; client sends explicit `N` repeats if held. Network stall → repeats just stop mid-air. Mac `CGEventPost` doesn't auto-repeat either — same model.
- **D-14** Caps Lock sync = bit in every KeyEvent. Client includes `caps_lock_on: bool` + Num/Scroll Lock state on every `KeyEvent` message. Server auto-corrects on mismatch. Zero round-trip cost.
- **D-15** International keyboard / IME coverage = US + UK + DE + JP layouts + IME passthrough. `RemoteViewer.inputMethodEvent` forwards commit strings as unicode text via new `TextCommit` protocol message — NOT synthesized keycodes.

**Wacom Hardware Verification (INPUT-09..12):**

- **D-16** Real-hardware matrix cadence = one-shot gate + per-release manual ritual. 4-cell matrix (Intuos Pro Large + Cintiq Pro 24 × Sonoma + Sequoia) at Phase 2 completion + every release tag. No self-hosted Wacom CI runners.
- **D-17** Pass criteria = hybrid protocol + automated counters. 6-step structured protocol (pressure ramp, eraser flip, tilt, proximity cycle, tablet-side buttons, reconnect mid-stroke). Each step has an automated structlog counter assertion. Manual sign-off on "pressure curve looks right in Flame paint stroke." Record video.
- **D-18** Pressure-quantization error measurement (INPUT-12) = known-ramp + RMS error. Artist draws slow 0→max ramp over ~5s. Client logs `QTabletEvent.pressure` samples with timestamps; server logs injected HID report pressure with timestamps. Post-processing script aligns + computes RMS error normalized to max pressure. **Pass: RMS < 1% across all 4 cells.** Graph committed to release notes. Script: `tools/wacom_quant_analysis.py`.
- **D-19** Proximity-event recovery = synthesize on `focusIn` + `showEvent`. Server pen FSM tolerates duplicate proximity events idempotently.
- **D-20** macOS TCC / Wacom driver detection = detect + guide on client. Client reads TCC DB read-only. If Input Monitoring missing for Teraguchi OR Accessibility missing for `com.wacom.*`, show onboarding panel with deep-links. Bakes into existing `client/key_diagnostic.py` as a new "Wacom setup" tab.

**Phase 2 Latency Gate:**

- **D-21** Latency gate = Phase 1 synthetic p99 < 25ms gate (D-08/D-09, unchanged in Phase 2 per D-21) + add manual DXS measurement. Commit pre/post Phase-2 real-hardware end-to-end number to `docs/release.md`. Post-Phase-2 slower than pre triggers investigation before sign-off.

### Claude's Discretion (research makes concrete recommendations)

1. **Metal shader sources for 10-bit YUV→RGB conversion (D-02).** Recommend BT.709 matrix as the stock approach; see "Color VIDEO-01..12" section below for the exact 3×3 matrix + P010-specific 6-bit-left-shift normalization.
2. **`KEY_RESET_MODIFIERS` / `TextCommit` / `PenProximity` wire format (D-11 / D-15 / D-19).** Recommend dataclass + `MsgType` string const in `common/messages.py`, JSON-encoded on the WebSocket control channel (not the UDP media channel). See "Hotkey Correctness" + "Pen Injection" sections for fields.
3. **`ENCODER_DEFS` edits in `server/video_encoder.py`** to surface `supports_main10` + `supports_422` + `supports_444` + `main10_detection` (per-family or `NvEncGetEncodeCaps` trial). See "Color VIDEO-01..12" section.
4. **`mac_video_encoder.py` internal structure (D-04).** Recommend a `threading.Thread` with a dedicated runloop owning the `VTCompressionSession`; an `asyncio.Queue` bridging the async callback back to the asyncio event loop using `loop.call_soon_threadsafe`. See "Color VIDEO-01..12" section.
5. **Test fixture asset location.** `tests/smoke/fixtures/10bit_ramp.p010.bin` + `tests/smoke/fixtures/10bit_ramp.reference.ffprobe.json` (Phase 1 precedent).
6. **FSM additions for pen proximity (D-19).** Recommend a separate `PenFSM` in `common/session_fsm.py` (2 states: `out_of_proximity`, `in_proximity`) rather than extending `SessionFSM` — pen state is orthogonal to session state, tolerates duplicate enters, and lives on the server side only.
7. **Specific module layout for `tools/wacom_quant_analysis.py` (D-18).** Recommend single-file script with argparse CLI: `python tools/wacom_quant_analysis.py --client-log a.jsonl --server-log b.jsonl --out rms.svg`. Under 200 lines.

### Deferred Ideas (OUT OF SCOPE)

- HIDDriverKit signed system extension → v1.1.
- Full ICC profile pipeline / display calibration → excluded from v1 entirely.
- HDR tone mapping on client → v1.x if demanded.
- 4:4:4 HEVC as first-class user-facing mode → post-v1.
- FR / ES / IT / CN / KR keyboard layouts → v1.1.
- Apple Vision Pro / visionOS client → post-v1.
- "Aggressive capture" CGEventTap for macOS trap keys (Cmd+Tab, Spotlight) → documented limitation, not a v1 feature.
- Self-hosted DXS CI runners for real-hardware checks → post-v1.
- Full 8-hour smoke harness → Phase 4.
- Sentry crash reporting → Phase 7.
- Prometheus /metrics → Phase 5.
- `server/main.py` DISPLAY env-var thread-safety → Phase 3 or post-v1.

---

## Project Constraints (from CLAUDE.md)

These are load-bearing for Phase 2 and the planner must verify compliance in every plan:

- **10-bit end-to-end is non-negotiable.** Phase 2 closes the 9 silent downgrade points listed in CLAUDE.md; the same 9 map 1:1 to D-01's 9 CI checkpoints.
- **Input fidelity zero-tolerance.** Flame artist muscle memory is load-bearing. Wacom pressure glitches + modifier-chord mangling are rejected by construction, not by patch.
- **Sub-20ms LAN input-to-photon.** Phase 1's synthetic gate (p99 < 30ms, widened from 25ms on macOS) continues to run in CI; Phase 2 adds a DXS real-hardware pre/post measurement.
- **Rocky 9 only on Linux.** No Wayland, no Rocky 10. Flame 2026 is X11-only per Autodesk.
- **v1 scope:** Mac client + Rocky Linux server + macOS server. No Windows, no Linux client. Broker paused.
- **Python 3.12 floor, structlog JSON logging, pytest at all levels, mock-at-subprocess-boundary for external codecs** (Phase 1 D-02 pattern — mirror for VT + IOKit).
- **Apache 2.0. No GPL imports.**
- **Every new feature ships with a regression test.**
- **Phase boundaries are load-bearing — no leaking Phase 3/4/5 work into Phase 2.**

---

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INPUT-01 | Table-driven keymap unit tests — Qt key codes → Linux scancodes + Mac virtual key codes, every modifier chord covered | "Hotkey Correctness" — D-12 exhaustive table, mac virtual-key code source from `NSEvent.keyCode` reference |
| INPUT-02 | Release-all-modifiers on `QEvent::WindowDeactivate` | "Hotkey Correctness" — D-11 trigger 1 (`focusOutEvent`) |
| INPUT-03 | Release-all-modifiers on reconnect | "Hotkey Correctness" — D-11 trigger 2 (ConnectionSupervisor hook) |
| INPUT-04 | Integration test executing "crazy Flame hotkey combos" against real Rocky + real Mac server | "Hotkey Correctness" — Flame-critical subset of D-12 table; Phase 1 integration infra |
| INPUT-05 | International keyboard + dead-key handling (US / UK / DE / JP at minimum) | "Hotkey Correctness" — D-15 `TextCommit` wire format |
| INPUT-06 | Caps Lock state sync between client and server | "Hotkey Correctness" — D-14 lock-state bits in every `KeyEvent` |
| INPUT-07 | Cmd ↔ Ctrl translation Mac client → Linux server (Flame-appropriate default, user-overridable) | "Hotkey Correctness" — D-10 per-bookmark override |
| INPUT-08 | IOHIDUserDevice-based pen pressure injector for macOS server | "Pen Injection INPUT-08..12" — D-07/D-08/D-09 spike + module |
| INPUT-09 | Wacom pressure/tilt integration test on real hardware (Intuos Pro L + Cintiq Pro 24, Sonoma + Sequoia) | "Wacom Matrix" — D-16 one-shot gate |
| INPUT-10 | Eraser + tablet-side button events round-trip | "Wacom Matrix" — D-17 steps 2+5 |
| INPUT-11 | Proximity events (pen approach/leave) delivered without loss | "Wacom Matrix" — D-17 step 4; "Pen Injection" — D-19 proximity recovery |
| INPUT-12 | Pressure curve preservation (no quantization artifacts in Flame paint strokes) | "Wacom Matrix" — D-18 RMS<1% gate |
| VIDEO-01 | 10-bit end-to-end pipeline verified — capture, encoder in/out, transport, decoder, display — no silent downgrades | "Color VIDEO-01..12" — D-01 9-checkpoint pattern |
| VIDEO-02 | Bit-exact end-to-end test pattern committed as CI fixture | "Color VIDEO-01..12" — D-01 fixture + `tests/smoke/fixtures/` |
| VIDEO-03 | HEVC Main10 via `hevc_nvenc` on Rocky 9 NVIDIA (Turing/Ampere baseline, Blackwell-ready) | "Color VIDEO-01..12" — NVENC capability matrix, D-03 probe |
| VIDEO-04 | Direct `VTCompressionSession` via PyObjC w/ `kVTVideoEncoderSpecification_EnableLowLatencyRateControl` | "Color VIDEO-01..12" — D-04 wrapper design |
| VIDEO-05 | 4:2:0 baseline, 4:2:2 opt-in where hardware confirms (Blackwell, M4) | "Color VIDEO-01..12" — D-05 + `ENCODER_DEFS` capability flags |
| VIDEO-06 | IDR-on-drop recovery (tied to STAB-04; Phase 2 verifies 10-bit path) | Phase 1 delivered; Phase 2 extends regression test with 10-bit fixture |
| VIDEO-07 | Encoder reconfigure without full pipeline restart (quality slider mid-session) | "Color VIDEO-01..12" — VTCompressionSession property set at runtime; NVENC reconfig API |
| VIDEO-08 | ScreenCaptureKit configured `.hdrLocalDisplay` + `kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange` | "Color VIDEO-01..12" — SCK 10-bit config (WWDC24 10088) |
| VIDEO-09 | NvFBC primary / mss fallback dispatch on Linux (zero-copy verification) | "Color VIDEO-01..12" — NvFBC 10-bit surface format, existing code path |
| VIDEO-10 | 60fps LAN / 30fps WAN with quality-slider controls | Phase 1 quality slider; Phase 2 ties frame-rate to encoder capability negotiation |
| VIDEO-11 | Sub-20ms LAN input-to-photon; CI benchmark keeps the number honest | "Latency Gate" — D-21 + Phase 1 synthetic gate (widened p99<30ms) |
| VIDEO-12 | PyAV client decode linked against system FFmpeg 7.1+ | "Color VIDEO-01..12" — Phase 1 STACK.md pinned FFmpeg 7.1+; Phase 2 adds P010 decode regression |

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| 10-bit pixel capture | Server (Linux NvFBC / mss, macOS ScreenCaptureKit) | — | Data source; the only tier that touches the display surface |
| HEVC Main10 encoding | Server (NVENC on Rocky / VTCompressionSession on Mac) | — | Hardware encoder lives on the GPU; encode latency is part of the server budget |
| Capability advertisement | Server (via `ServerHelloMsg` extension) | — | Only the server knows what its hardware can actually do |
| 10-bit-aware transport | Transport (common — HEVC NAL unit, not protocol-aware) | — | Bits flow through transparently on the control+media channels; no tier-specific work |
| PyAV decode + 10-bit `AVFrame.format` assertion | Client (decoder module) | — | Decoder owns the VideoToolbox hwaccel hook on macOS |
| Metal/QRhi 10-bit texture upload + YUV→RGB shader + blit | Client (viewer) | — | Only the client renders; 10-bit must survive to the framebuffer on the display the artist uses |
| Display-state / EDR probing (documented-if-accepted) | Client (viewer + health overlay) | — | Mac EDR state is the client's display, not the server's |
| Health-overlay "10-bit: …" badge | Client (existing `health_display.py`) | Server (sources capability fields) | Display is client; authority is server |
| Pen event capture (QTabletEvent) | Client (viewer) | — | Qt on macOS owns `NSEvent.pressure` translation |
| IOHIDUserDevice virtual tablet | Server (macOS-only, new `mac_pen_injector.py`) | — | Injection must run on the host being remoted |
| Keyboard event capture | Client (viewer) | — | Qt captures; client adds Caps Lock bit + IME `TextCommit` |
| Keyboard injection (uinput / XTest / CGEventPost) | Server (platform-dispatched via `InputInjector`) | — | Phase 1 delivered the pattern |
| Cmd↔Ctrl translation | Client (per-bookmark toggle) | Server (applies authoritative table) | Client owns bookmark; server owns the single authoritative `keymap.py` table |
| Release-all-modifiers idempotent path | Server (`InputInjector.reset_modifiers()` — already exists) | Client (4 triggers dispatch the message) | Mechanism is server; triggers are client |
| TCC / Wacom driver detection | Client (read-only sqlite on TCC DB) | — | Driver installation is a client-machine concern |
| Wacom pressure-RMS analysis | Tooling (`tools/wacom_quant_analysis.py` post-processing) | — | Offline post-run analysis — neither client nor server at runtime |
| 9-checkpoint bit-exact CI fixture | CI (`tests/smoke/`) | — | Mocks server + client encoder/decoder boundaries; runs in GHA |
| Latency measurement (CI + DXS one-shot) | CI (synthetic) + Server (real-HW trace) + `docs/release.md` (record) | — | Synthetic is bounded; real-HW is manual |

---

## Standard Stack

(Phase 1 settled the core. Phase 2 adds PyObjC framework deps and uses existing deps in new configurations.)

### Core (keep / pin)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.12+ | Runtime | Phase 1 D-03 floor [VERIFIED: pyproject.toml] |
| PySide6 | 6.10.x | Qt GUI, `QRhiWidget` Metal backend | Only toolkit exposing full `QTabletEvent` pressure/tilt + `QRhi` Metal backend [CITED: doc.qt.io/qt-6/qrhiwidget.html] |
| PyAV | 17.x | Client decode, hwaccel via VideoToolbox | Phase 1 STACK.md settled; P010 10-bit decode path is the delta [CITED: pyav.org] |
| FFmpeg | 7.1.1+ (system, not PyPI wheel) | PyAV backing + Linux server encode subprocess | Blackwell NVENC 4:2:2 requires 7.1+; PyPI wheels lack CUDA+full VideoToolbox [CITED: pyav.org/docs] |
| NVIDIA driver | 570.x+ (with Blackwell) / 525.x+ (Ada/Ampere floor) | NVENC access on Rocky 9 | Blackwell 4:2:2 requires 570+ [CITED: NVIDIA VCSDK 13 blog] |
| `structlog` | 25.1+ | JSON logging for D-17/D-18 telemetry | Phase 1 D-01 settled |
| `python-statemachine` | as pinned | Session FSM baseline + new PenFSM (D-19) | Phase 1 D-07 settled |
| `pytest` + `pytest-asyncio` | 8.3+ / 0.25+ | Phase 1 D-01 test framework | Phase 1 settled |

### New for Phase 2

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `pyobjc-framework-VideoToolbox` | 11.x+ | Direct VT encode wrapper (D-04) | Apple's Objective-C VT bindings; PyObjC is already in `requirements-server.txt` on macOS [VERIFIED: install-server-macos.sh has other pyobjc-framework-* deps] |
| `pyobjc-framework-IOKit` | 11.x+ | IOHIDUserDevice virtual tablet (D-09) | IOKit is THE macOS userspace-to-HID path [CITED: developer.apple.com/documentation/iokit] |
| `pyobjc-framework-CoreVideo` | 11.x+ | `CVPixelBuffer` manipulation for VT input (D-04) | Required for P010 `CVPixelBuffer` creation |
| `pyobjc-framework-CoreMedia` | 11.x+ (already in tree) | `CMSampleBuffer` / `CMTime` for VT output | Already installed by `install-server-macos.sh` |

**Installation:**
```bash
# macOS server additions
uv pip install \
    'pyobjc-framework-VideoToolbox>=11.0' \
    'pyobjc-framework-IOKit>=11.0' \
    'pyobjc-framework-CoreVideo>=11.0'
# (pyobjc-framework-CoreMedia + -AVFoundation + -ScreenCaptureKit already pinned from Phase 1)
```

**Version verification (to run pre-planning):**
```bash
pip index versions pyobjc-framework-VideoToolbox
pip index versions pyobjc-framework-IOKit
```
Both should be on the 11.x PyObjC series as of 2026-04. If 12.x has shipped, the planner should use it — PyObjC is ABI-stable across major bumps for framework bindings.

### Supporting

No new supporting libs beyond what Phase 1 shipped. `numpy>=2.1` already pinned and used for frame buffers.

### Alternatives Considered (rejected for Phase 2)

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| VTCompressionSession direct (D-04) | FFmpeg `hevc_videotoolbox` subprocess | Saves the wrapper work but blocks low-latency mode + costs 5-8ms/frame [CITED: WWDC21 10158]. A FFmpeg patch adds `kVTVideoEncoderSpecification_EnableLowLatencyRateControl` with HEVC on Apple Silicon in 2025 (not yet in FFmpeg 7.1 stable) [CITED: ffmpeg-devel ML 2025-07]. Stick with D-04. |
| IOHIDUserDevice (D-09) | CGEventPost tablet events | Already the current path; drops `NSEvent.pressure` silently per PITFALLS #3. Keep CGEventPost for mouse/keyboard only (D-09). |
| IOHIDUserDevice | HIDDriverKit signed system extension | More robust but needs Apple DriverKit entitlement approval (weeks of bureaucracy). Deferred to v1.1 per D-07. |
| QRhiWidget Metal (D-02) | QOpenGLWidget + custom fragment shader | Older Qt pattern, still works, but `QRhi` is the Qt-native forward-looking abstraction. Keep QOpenGLWidget documented as escape hatch if QRhi P010 path proves painful. |
| BT.709 YUV→RGB matrix in fragment shader | BT.2020 matrix | BT.2020 only matters when we ship HDR tone-mapping, which is OUT OF SCOPE for v1 per D-06. Use BT.709 full-range. |

---

## Architecture Patterns

### System Architecture Diagram

```
┌──────────────────────────── SERVER (Rocky 9 or macOS) ─────────────────────────────┐
│                                                                                    │
│  ScreenCapture (10-bit)        VideoEncoder (HEVC Main10)     InputInjector        │
│  ┌─────────────────┐          ┌──────────────────────┐      ┌─────────────────┐    │
│  │ Linux: NvFBC    │ P010/    │ Linux: hevc_nvenc    │      │ Linux: uinput + │    │
│  │ (10-bit surface)│─YUV420P─▶│ (Main10, Main42210)  │─NAL─▶│   XTest         │    │
│  │ or mss fallback │ 10LE     │                      │      │                 │    │
│  └─────────────────┘          │ Mac: mac_video_enc   │      │ Mac: CGEventPost│    │
│  ┌─────────────────┐          │ (VTCompressionSess,  │      │  (mouse/kbd)    │    │
│  │ Mac: SCK        │ P010 via │  LowLatencyRC,       │      │ +mac_pen_inject │    │
│  │ .hdrLocalDisplay│─CVPixel─▶│  Main10_AutoLevel)   │      │  (IOHIDUser-    │    │
│  │ 420YpCbCr10     │ Buffer   │                      │      │   Device)       │    │
│  └─────────────────┘          └──────────────────────┘      └─────────────────┘    │
│                                         │                              ▲           │
│                                   CapabilityProbe (D-03)               │           │
│                                    ├─nvidia-smi+NvEncGetEncodeCaps     │           │
│                                    ├─VTIsHardwareDecodeSupported       │           │
│                                    └─stuffs ServerHelloMsg             │           │
│                                                                        │           │
│       ┌──────────────── Control channel (WSS/QUIC JSON) ───────────────┘           │
│       │    • ServerHelloMsg (capability flags)                                      │
│       │    • KeyEvent (now with caps_lock/num_lock/scroll_lock bits — D-14)         │
│       │    • KEY_RESET_MODIFIERS (D-11)                                             │
│       │    • TextCommit (IME unicode — D-15)                                        │
│       │    • PenProximity / PenEvent (D-19 + pressure/tilt/eraser)                  │
│       ├──── Media channel (UDP / QUIC datagram) — HEVC NAL + audio                  │
│       │                                                                             │
└───────┼─────────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────── CLIENT (macOS only v1) ────────────────────────────────┐
│                                                                                    │
│  VideoDecoder               RemoteViewer (client/viewer.py)                        │
│  ┌──────────────────┐       ┌───────────────────────────────────────────────────┐  │
│  │ PyAV + FFmpeg    │       │  D-02 REFACTOR: QRhiWidget (Metal on macOS)       │  │
│  │ 7.1+ VideoToolbox│ P010  │  ┌────────────────────────────────────────────┐  │  │
│  │ hwaccel (10-bit) │──────▶│  │ QRhiTexture (Y plane, 10-bit-in-16-bit)    │  │  │
│  │ AVFrame.format=  │ via   │  │ QRhiTexture (UV plane, 10-bit-in-16-bit)   │  │  │
│  │  P010 / YUV420P  │ QVideo│  │ Metal fragment shader: BT.709 YUV → RGB    │  │  │
│  │  10LE            │ Frame │  │ Final blit to widget's MTKView backbuffer  │  │  │
│  └──────────────────┘       │  └────────────────────────────────────────────┘  │  │
│        ▲                    │  QPainter overlay layer: cursor + health badge    │  │
│        │                    │  QTabletEvent: pressure/tilt → PenEvent msg       │  │
│        │                    │  QInputMethodEvent: commit str → TextCommit msg   │  │
│        │                    │  focusOutEvent → KEY_RESET_MODIFIERS              │  │
│        │                    │  F9 panic shortcut → KEY_RESET_MODIFIERS          │  │
│        │                    │  focusInEvent + showEvent → PenProximity          │  │
│        │                    └───────────────────────────────────────────────────┘  │
│        │                                                                           │
│   ConnectionSupervisor (Phase 1) — reconnect hook fires KEY_RESET_MODIFIERS first  │
│   HealthDisplay (Phase 1)       — "10-bit: confirmed / DEGRADED / …" badge (D-03)  │
│   KeyDiagnostic (Phase 1)       — new "Wacom setup" tab: TCC + deep-links (D-20)   │
│                                                                                    │
└────────────────────────────────────────────────────────────────────────────────────┘
```

**Arrow semantics:** solid = hot-path every-frame data; dashed (not shown above, use in plans) = control/setup once-per-session. Capture → Encoder → Media channel is hot-path; Capability probe → ServerHelloMsg is once-per-session.

### Recommended Module Layout (deltas from post-Phase-1 tree)

```
server/
├── mac_video_encoder.py       # NEW — D-04 VTCompressionSession PyObjC wrapper
├── mac_pen_injector.py        # NEW — D-09 IOHIDUserDevice virtual tablet
├── mac_input_injector.py      # REFACTOR — pen_event delegates to mac_pen_injector
├── video_encoder.py           # EXTEND — ENCODER_DEFS gains supports_main10/422/444 flags
├── platform_backends.py       # EXTEND — InputInjector composes pen + non-pen dispatchers
├── screen_capture.py          # EXTEND — NvFBC 10-bit surface format negotiation
├── mac_screen_capture.py      # EXTEND — SCK .hdrLocalDisplay config (D-01 cp.1)
└── capability_probe.py        # NEW — D-03 per-platform encoder capability probes
                               #       (nvidia-smi + NvEncGetEncodeCaps on Linux;
                               #        VTCopySupportedPropertyDictionary on Mac)

common/
├── keymap.py                  # EXTEND — Qt → Mac virtual key code table alongside Linux
├── messages.py                # EXTEND — KEY_RESET_MODIFIERS, TextCommit, PenProximity,
│                              #          KeyEvent.caps_lock/num_lock/scroll_lock,
│                              #          ServerHelloMsg.color_caps (main10/4:2:2/4:4:4)
└── session_fsm.py             # EXTEND — new PenFSM (out_of_proximity ↔ in_proximity)

client/
├── viewer.py                  # REFACTOR — QRhiWidget Metal video layer (D-02);
│                              #          focusInEvent + showEvent → PenProximity (D-19);
│                              #          inputMethodEvent → TextCommit (D-15)
├── key_diagnostic.py          # EXTEND — new "Wacom setup" tab (D-20)
├── bookmarks.py               # EXTEND — per-bookmark "Swap Cmd/Ctrl" checkbox (D-10)
└── health_display.py          # EXTEND — "10-bit: …" badge + "Cmd↔Ctrl swap: on/off"

tools/
└── wacom_quant_analysis.py    # NEW — D-18 RMS pressure-quantization script

tests/
├── smoke/fixtures/
│   ├── 10bit_ramp.p010.bin    # NEW — D-01 bit-exact test pattern
│   └── 10bit_ramp.reference.ffprobe.json  # NEW — checkpoint 3 baseline
├── common/
│   └── test_keymap.py         # EXTEND — D-12 exhaustive table
├── server/
│   ├── test_mac_video_encoder.py  # NEW — mock-at-VT-boundary (Phase 1 D-02 pattern)
│   ├── test_mac_pen_injector.py   # NEW — mock-at-IOKit-boundary
│   ├── test_capability_probe.py   # NEW — mocks nvidia-smi + NvEncGetEncodeCaps
│   └── test_video_encoder_main10.py  # NEW — ENCODER_DEFS capability-flag assertions
├── integration/
│   ├── test_crazy_hotkeys.py      # NEW — INPUT-04
│   ├── test_10bit_end_to_end.py   # NEW — D-01 9-checkpoint fixture drive
│   └── test_modifier_stress.py    # NEW — INPUT-02/-03 focus-stress + reconnect-stress
└── client/
    └── test_tcc_detection.py      # NEW — D-20 sqlite TCC-read mock
```

### Pattern 1: Capability probe via `NvEncGetEncodeCaps` (D-03 Linux-side)

**What:** Instead of maintaining a static NVENC-generation → Main10 support table, query the live encoder capability. Static tables drift; `NvEncGetEncodeCaps` is authoritative.

**When to use:** Server startup, Rocky 9 + NVIDIA. One-shot; cache the result for session lifetime.

**Example:**
```python
# server/capability_probe.py (NEW)
# Source: NVIDIA Video Codec SDK 13.0 docs, NVENC Application Note §"Encoding Capabilities"
# https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-application-note/
#
# CITED: NV_ENC_CAPS_SUPPORT_10BIT_ENCODE is the canonical probe.
# We don't link against NVENC C SDK directly — we trial-encode a 1-frame P010 input
# via `ffmpeg -f rawvideo -pix_fmt p010le -f null -` and parse the stderr. If FFmpeg
# reports "No capable devices found" or "bit depth not supported", 10-bit is absent.
# Fallback also useful because some Turing boards with old drivers misadvertise caps.

def probe_nvenc_main10() -> dict[str, bool]:
    """Returns {'main10': True/False, 'chroma_422': True/False, 'chroma_444': True/False}"""
    # 1. Parse nvidia-smi --query-gpu=gpu_name,driver_version
    # 2. Trial-encode 32x32 single frame via ffmpeg subprocess; check returncode + stderr
    # 3. Cross-reference family table (Turing/Ampere/Ada/Blackwell) as sanity
    ...
```

**Static family sanity table (built from [CITED: NVIDIA VCSDK 13 blog 2025-01]):**

| Family | HEVC Main10 4:2:0 | HEVC 4:2:2 | HEVC 4:4:4 | AV1 Main10 |
|--------|-------------------|------------|------------|------------|
| Turing (RTX 20x0, T-series) | yes | no | yes (HEVC only) | no |
| Ampere (RTX 30x0, A-series) | yes | no | yes (HEVC only) | no |
| Ada Lovelace (RTX 40x0) | yes | no | yes | yes |
| Blackwell (RTX 50x0, GB202+) | yes | **yes** | yes | yes |

Pascal and older are out of scope (DXS fleet is Turing-minimum).

### Pattern 2: VTCompressionSession PyObjC wrapper (D-04)

**What:** Direct `VTCompressionSessionCreate` with low-latency rate control. Async callback bridge to asyncio.

**When to use:** macOS server video encode path (replaces `_videotoolbox_args` subprocess call).

**Call sequence:**
```python
# server/mac_video_encoder.py (NEW)
# Source: Apple VT API — https://developer.apple.com/documentation/videotoolbox/vtcompressionsession-api-collection
# Source: Apple WWDC21 session 10158 "Explore low-latency video encoding with VideoToolbox"
# Source: Apple docs — kVTVideoEncoderSpecification_EnableLowLatencyRateControl
#
# CITED: As of macOS Monterey+, EnableLowLatencyRateControl works with HEVC on Apple Silicon
#        (historically H.264-only, but Apple Silicon support was added). Rely on
#        VTCopySupportedPropertyDictionary() probe (see D-03).

import objc
import VideoToolbox as VT
import CoreMedia as CM
import CoreVideo as CV
import CoreFoundation as CF

ENCODER_SPEC = {
    VT.kVTVideoEncoderSpecification_EnableLowLatencyRateControl: True,
    VT.kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder: True,
}

COMPRESSION_PROPS = {
    VT.kVTCompressionPropertyKey_ProfileLevel:
        VT.kVTProfileLevel_HEVC_Main10_AutoLevel,          # 4:2:0 default
        # Swap to kVTProfileLevel_HEVC_Main42210_AutoLevel when 4:2:2 probe confirms (M3+)
    VT.kVTCompressionPropertyKey_RealTime: True,
    VT.kVTCompressionPropertyKey_AllowFrameReordering: False,  # one-in-one-out
    VT.kVTCompressionPropertyKey_ExpectedFrameRate: fps,       # 60 LAN / 30 WAN
    VT.kVTCompressionPropertyKey_AverageBitRate: target_bps,   # from QualitySettings
    VT.kVTCompressionPropertyKey_MaxKeyFrameInterval: 2 * fps, # 2s GOP; IDR on drop elsewhere
    VT.kVTCompressionPropertyKey_AllowTemporalCompression: True,
    VT.kVTCompressionPropertyKey_H264EntropyMode:              # HEVC-N/A; noted for parity
        VT.kVTH264EntropyMode_CABAC,
    # No B-frames: HEVC Main profile + AllowFrameReordering=False forces IBBBB…
}

# Async callback (VT posts on its own thread)
def _output_callback(outputCallbackRefCon, sourceFrameRefCon, status,
                     infoFlags, sampleBuffer):
    # Extract the NAL-unit payload from sampleBuffer (CMSampleBufferGetDataBuffer)
    # loop.call_soon_threadsafe(self._on_encoded_frame, nal_bytes, pts, is_idr)
    ...

# Session lifecycle
VTCompressionSessionCreate(allocator=None, width, height,
                           codecType=CM.kCMVideoCodecType_HEVC,
                           encoderSpecification=ENCODER_SPEC,
                           sourceImageBufferAttributes=None,
                           compressedDataAllocator=None,
                           outputCallback=_output_callback,
                           outputCallbackRefCon=ctx,
                           compressionSessionOut=byref(session))

# Per-frame
VTCompressionSessionEncodeFrame(session, cvPixelBuffer_P010,
                                presentationTimeStamp=CMTime(pts, 90000),
                                duration=CMTime.invalid(),
                                frameProperties=None,
                                sourceFrameRefCon=None,
                                infoFlagsOut=None)
# NON-BLOCKING — encoded frame arrives via _output_callback on VT's thread.
# Use asyncio.Queue + loop.call_soon_threadsafe to bridge back to async sender.

# Shutdown
VTCompressionSessionCompleteFrames(session, CMTime.invalid())
VTCompressionSessionInvalidate(session)
```

**Threading model (Claude's Discretion #4):** A dedicated `threading.Thread` with a CFRunLoop is **not** required — PyObjC handles the VT callback dispatch directly on VT's internal thread. The bridge is an `asyncio.Queue` + `loop.call_soon_threadsafe`. This mirrors the Phase 1 pattern for SCK capture (which uses `NSCondition` to bridge the SCK delegate thread). See `server/mac_screen_capture.py` for the precedent.

### Pattern 3: IOHIDUserDevice virtual tablet (D-09)

**What:** Create a userspace virtual HID digitizer that publishes a Wacom-compatible HID report descriptor and synthesizes pressure/tilt/eraser/proximity/button reports on demand.

**When to use:** macOS server, after D-08 spike pass, as replacement for the mouse-click stub in `mac_input_injector.pen_event`.

**HID report descriptor shape (to be validated during D-07 spike):**
- Pen pressure: **at least 10 bits** (Wacom hardware ships 8192-level pressure = 13 bits; match if possible, floor at 10) — reference [CITED: github.com/linuxwacom/wacom-hid-descriptors] for real-device descriptor captures.
- Tilt X / Y: signed 7-bit per axis (Wacom standard; matches `xTilt`/`yTilt` range ±64).
- Proximity (in/out-of-range): 1 bit.
- Eraser flag: 1 bit (distinguishes pen tip from eraser tip).
- Tip switch: 1 bit (pen touching surface).
- Barrel buttons (side switches): 2 bits (button 1 + button 2).
- X / Y coordinates: 16-bit absolute positions at the tablet's logical resolution.
- Transducer serial (optional): 32-bit — useful if we want Flame to distinguish pens; not required for v1.

**Practical example pattern (the spike validates the concrete API calls; D-08 pass bar is Photoshop reads non-zero `NSEvent.pressure`):**
```python
# server/mac_pen_injector.py (NEW)
# References:
#   - IOHIDUserDevice.h (IOKit header, IOKit.framework/Headers/)
#   - wacom-qemu (https://github.com/thenickdude/wacom-qemu) — virtual Wacom for VMs
#   - foohid (https://github.com/unbit/foohid) — userspace HID driver pattern
#     (NOTE: foohid uses a kernel extension; we want PURE userspace via IOHIDUserDevice)
#
# ASSUMED: Userspace IOHIDUserDevice in Sonoma+ can emit digitizer reports that
#          NSEvent bridges into NSEventTypeTabletPoint with non-zero pressure.
#          Confirmed intent but NOT confirmed in a shipping app — the D-08 spike
#          is the confirmation gate.

from Foundation import NSDictionary, NSData
import objc

IOKit = objc.loadBundle('IOKit', globals(),
    bundle_path='/System/Library/Frameworks/IOKit.framework')

# 1) Build HID report descriptor bytes (USB HID spec §Tablet Usage Page 0x0D)
# 2) IOHIDUserDeviceCreate(allocator, properties_dict) where properties_dict includes
#    kIOHIDReportDescriptorKey → NSData(descriptor_bytes)
# 3) On each pen event: pack a report struct, IOHIDUserDeviceHandleReport(device, report_bytes)
# 4) Handle getReport / setReport callbacks for capability queries (minimal impl OK for v1)
```

**TCC / signing / Input Monitoring prerequisites:**
- The server process creating the IOHIDUserDevice needs **no special entitlement** for userspace HID injection (userspace-only path). [ASSUMED — confirm in spike.]
- For the CLIENT app reading `NSEvent.pressure` from *real* Wacom tablets, Input Monitoring TCC is already required (Phase 1 diagnostic covers this).
- Signing: the virtual HID device is created by an unsigned script during the D-08 spike; for v1 production, the signed + notarized `.app` (Phase 6) signs the server binary, which inherits the IOKit access.

### Pattern 4: QRhiWidget Metal P010 upload + BT.709 shader (D-02)

**What:** Replace the `QPainter.drawImage(QImage)` video blit in `client/viewer.py` with a `QRhiWidget` subclass that uploads P010 Y and UV planes as two `QRhiTexture`s and runs a Metal fragment shader performing BT.709 YUV→RGB conversion.

**When to use:** Client video layer only — overlays and cursor continue to use QImage + QPainter.

**Qt 6.10 API shape:**
```cpp
// Source: https://doc.qt.io/qt-6/qrhiwidget.html (CITED)
// Source: https://doc.qt.io/qt-6/qtwidgets-rhi-simplerhiwidget-example.html (CITED)
//
// PySide6 exposes the same API via PySide6.QtWidgets.QRhiWidget; see
// https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QRhiWidget.html (CITED)

class VideoBlitWidget(QRhiWidget):
    def initialize(self, cb):
        # Create Y-plane texture: QRhiTexture.R16 (holds 10-bit shifted into 16 container)
        self._tex_y = self.rhi().newTexture(QRhiTexture.R16,
                                            QSize(w, h),
                                            1, QRhiTexture.Flag(0))
        self._tex_y.create()
        # Create UV-plane texture (interleaved): QRhiTexture.RG16, half-height for 4:2:0
        self._tex_uv = self.rhi().newTexture(QRhiTexture.RG16,
                                             QSize(w//2, h//2),
                                             1, QRhiTexture.Flag(0))
        self._tex_uv.create()
        # Build QRhiShaderResourceBindings + QRhiGraphicsPipeline with full-screen quad
        ...

    def render(self, cb):
        # Upload per-frame Y + UV (tracked latest-frame buffer from decoder thread)
        u = QRhiResourceUpdateBatch()
        u.uploadTexture(self._tex_y,  QRhiTextureUploadDescription(y_plane_data))
        u.uploadTexture(self._tex_uv, QRhiTextureUploadDescription(uv_plane_data))
        cb.resourceUpdate(u)
        # Draw full-screen quad, shader samples Y + UV, applies BT.709 matrix
        ...
```

**PySide6-specific shape (use `PySide6.QtWidgets.QRhiWidget`):**
```python
# client/viewer_rhi.py (NEW candidate — or extend viewer.py)
from PySide6.QtWidgets import QRhiWidget
from PySide6.QtGui import (QRhi, QRhiTexture, QRhiGraphicsPipeline,
                           QRhiShaderResourceBindings, QRhiResourceUpdateBatch)
from PySide6.QtCore import QSize

class VideoBlitWidget(QRhiWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setApi(QRhiWidget.Api.Metal)  # force Metal on macOS; OpenGL fallback on other OSes

    def initialize(self, cb):
        ...

    def render(self, cb):
        ...
```

**BT.709 YUV→RGB fragment shader (Claude's Discretion #1 — recommended stock):**
```glsl
#version 440
// Vulkan-GLSL style per Qt shader infrastructure (qsb-compiled to MSL on Metal)
// Source: ITU-R BT.709 / BT.1361; verified against FFmpeg swscale BT.709 constants.

layout(binding = 1) uniform sampler2D yTex;   // R16 storing 10-bit-in-16 (left-shifted by 6)
layout(binding = 2) uniform sampler2D uvTex;  // RG16

layout(location = 0) in vec2 uv;
layout(location = 0) out vec4 fragColor;

void main() {
    // P010 stores 10-bit values in the top 10 bits of a 16-bit container.
    // Qt's R16 sampler normalizes 16-bit to [0,1]. For true 10-bit range (64..960
    // video-range or 0..1023 full-range), multiply by (65535.0/1023.0) or apply the
    // level-shift explicitly. For BT.709 video-range, the constants below expect
    // [0..1] with Y in [16/256, 235/256] and UV centered at 128/256.
    float Y  = texture(yTex,  uv).r;
    vec2  UV = texture(uvTex, uv).rg - vec2(0.5, 0.5);

    // BT.709 video-range
    float R = Y + 1.5748 * UV.y;
    float G = Y - 0.1873 * UV.x - 0.4681 * UV.y;
    float B = Y + 1.8556 * UV.x;
    fragColor = vec4(R, G, B, 1.0);
}
```

**Shader build integration:** Qt shader infrastructure requires `qsb` (Qt shader baker) to process GLSL → SPIR-V → MSL. Qt 6.10's build system handles this via `qt_add_shaders()` in CMake, but PySide6 projects ship pre-baked `.qsb` files or run `pyside6-qsb` at build time. Phase 2 ships the `.qsb` in the repo (reproducible build) + a `build_shaders.sh` script.

**Escape hatch if QRhi proves painful:** `QOpenGLWidget` with a pair of OpenGL textures (`GL_R16`, `GL_RG16`) and the same fragment shader translated to GLSL ES 3.0. Metal path uses `MTLPixelFormatBGR10A2Unorm` for the render target if we need the widget's own framebuffer to be 10-bit.

### Pattern 5: `xset r off` + client-driven key repeat (D-13)

**What:** Server runs `xset -display $DISPLAY r off` at session start (Linux). Client detects key-held state via Qt `autoRepeat()` and sends explicit Nth-repeat messages; server injects each discretely.

**Why:** Network stall no longer triggers runaway repeats. Matches PCoIP pattern.

**Server hook location:** `server/session_manager.py` Xorg/Xvfb spawn path; run once per session after DISPLAY is ready.

**Mac note:** `CGEventPost` never auto-repeats. No `xset` equivalent; Mac path is natively correct.

### Anti-Patterns to Avoid

- **Do not use `AVSampleBufferDisplayLayer` as a stand-in for the Metal blit.** It doesn't give you pixel-buffer access, which the client viewer needs for the health-overlay composite and the D-01 checkpoint-8 readback [CITED: Apple VT docs].
- **Do not fall back to `QImage::Format_RGBA64` for 10-bit storage.** QImage renders via QPainter which does not preserve 10-bit through to Metal. The 10-bit has to live as a GPU texture from upload through blit per D-02 [CITED: Qt forum discussions + PITFALLS #2].
- **Do not trust static NVENC-family tables as the sole probe.** They drift (new drivers, new silicon, BIOS changes). Always trial-encode or call `NvEncGetEncodeCaps` live [CITED: OBS forum NVENC 10-bit fallback thread].
- **Do not synthesize IME / dead-key composition as synthetic keystrokes.** Always use the `TextCommit` unicode-string path per D-15; synthesized keycodes mangle composition [CITED: xdotool Unicode limitations + PITFALLS #4].
- **Do not create a CGEventTap for macOS trap keys (Cmd+Tab, Spotlight).** Deferred per CONTEXT.md; adds Accessibility TCC surface area with no v1 payoff.
- **Do not put video + overlay + cursor all through the Metal path.** Metal owns the video layer only. Cursor + health overlay + tooltips stay on QImage/QPainter, composited on top of the QRhiWidget's backbuffer via `setContentsMargins` + `setAttribute(Qt.WA_TranslucentBackground)` on the overlay layer, or a sibling `QWidget` stacked over the `QRhiWidget`.
- **Do not unsynchronize the Cmd↔Ctrl swap from the health overlay.** If the UI says "Cmd swap: on" and the keymap table disagrees, the artist's trust is gone. Single authoritative place in `common/keymap.py` per D-10.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HEVC Main10 encode on macOS | Custom Metal-based encoder | VTCompressionSession via PyObjC | Apple's HW encoder; 5-8ms/frame savings vs FFmpeg wrapper; fully standard [CITED: WWDC21 10158] |
| HEVC Main10 encode on Linux | Raw NVENC SDK wrap (pybind11) | FFmpeg `hevc_nvenc` subprocess (Phase 1 pattern) | Works; <2ms subprocess overhead; revisit only if traces show issues per STACK.md |
| 10-bit YUV→RGB color conversion in Python | NumPy matrix multiply per frame | Metal fragment shader | GPU does 8M pixels × 3 channels × 60fps in microseconds; CPU does it in tens-of-ms |
| Virtual HID device on macOS (kernel) | Kernel extension / KEXT | IOHIDUserDevice (userspace) | KEXT is deprecated since Catalina; DriverKit requires Apple approval; IOHIDUserDevice is the sanctioned userspace path [CITED: Apple IOKit docs + foohid community project] |
| Wacom HID report descriptor from spec | Write from USB HID spec directly | Adapt `linuxwacom/wacom-hid-descriptors` real-device captures | Real captures from live Intuos Pro + Cintiq Pro are the ground truth [CITED: github.com/linuxwacom/wacom-hid-descriptors] |
| IME / dead-key composition on server | Synthesize compose-key sequences in the server | Forward unicode commit strings via `TextCommit` (D-15) and inject via `xdotool type --clearmodifiers` on Linux or `CGEventKeyboardSetUnicodeString` on Mac | Compose is layout-dependent; unicode commits are layout-independent [CITED: xdotool docs — `type` supports unicode; isamert.net 2022 article on programmatic unicode] |
| Pressure curve statistical analysis | Custom RMS with edge-case handling | `numpy` + `scipy.interpolate` for timestamp alignment | Stock scientific Python; D-18 script stays <200 lines |
| Cmd↔Ctrl translation at callsite | `if platform == 'darwin' and ...` scattered through code | Single translation table in `common/keymap.py`, consulted once per message | PITFALLS #4 — inference at callsite is exactly how modifier mangling happens |
| Release-all-modifiers across FSM states | Custom state-tracking per trigger | Server-side idempotent `InputInjector.reset_modifiers()` (already exists from Phase 1) | Idempotent = safe to spam; callers just send the message on their trigger |
| TCC read | Write a wrapper around `sqlite3` | Use stock `sqlite3` stdlib module read-only on `~/Library/Application Support/com.apple.TCC/TCC.db` | One-function detection; no hack required |
| Capability probe across NVENC families | Maintain the family table | Use `NvEncGetEncodeCaps` (live probe) + family table as sanity cross-check | Static table drifts; live probe is authoritative |

**Key insight:** Phase 2 has four "hairy subsystem" temptations — virtual HID, low-latency VT, 10-bit GPU blit, exhaustive keymap — and in all four cases the right move is to **wire up Apple/NVIDIA/Qt's standard primitive with Phase 1's test-at-boundary pattern**, not to write new infrastructure. The Phase 1 precedent (mock at FFmpeg subprocess boundary for encoder tests) translates verbatim: mock at the VT boundary, mock at the IOKit boundary, mock at the nvidia-smi/NvEncGetEncodeCaps boundary. No custom kernel code, no custom encoder, no custom shader language.

---

## Runtime State Inventory

> Phase 2 is a feature-add / refactor, not a rename or migration. This section is included because it touches runtime-state boundaries (virtual HID device lifecycle, VT compression session lifecycle, capability probe caching).

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None. No database keys, user_ids, or persisted identifiers tied to Phase 2 artifacts. Bookmarks gain a "Swap Cmd/Ctrl" flag (D-10); pre-existing bookmarks missing the flag default to ON when destination is a Linux hostname and OFF otherwise. | Add default resolution on bookmark load; document the default in release notes. |
| Live service config | None — no external services with persistent config. The server's encoder capability is probed live per-session (D-03), not persisted across restarts. | None. |
| OS-registered state | The IOHIDUserDevice is registered with the macOS IOKit registry when the server creates it — but it is a *process-owned* registration that disappears on process exit. No `launchd` plist, no persistent kernel registration. Xorg `xset r off` likewise is per-X-server-instance. | None beyond correct teardown in `SessionRuntime.stop()`. Must verify: when server crashes, the virtual HID device must vanish (no orphaned devices in `ioreg -l` after crash). D-17 reconnect-mid-stroke step exercises this. |
| Secrets/env vars | No new secret keys. No new env vars required (Phase 2 uses DISPLAY, PATH, PYTHONPATH — all pre-existing). | None. |
| Build artifacts | Phase 2 adds `.qsb`-baked Qt shaders (Metal fragment shader compiled from GLSL) committed in-tree under `client/shaders/video_blit.frag.qsb`. The `build_shaders.sh` script regenerates them via `pyside6-qsb`. | Document in `README.md` that `build_shaders.sh` must run after every PySide6 version bump. Add a CI step that runs `build_shaders.sh` and asserts no diff — cheap drift guard. |

**Nothing else found in category:** Verified by greps across the codebase for references to persistent state, external services, launchd/systemd units, and pickled caches.

---

## Color Fidelity — VIDEO-01..12 (D-01 through D-06)

**Scope:** Close the 9 silent-downgrade points. Ship a byte-equality CI fixture. Land direct VT encode on macOS. Add capability probe + overlay. Support 4:2:0 default / 4:2:2 opt-in.

### The 9 downgrade checkpoints (D-01) mapped to signal

| # | Checkpoint | Location | Detection Signal | Who owns the probe |
|---|------------|----------|------------------|---------------------|
| 1 | Capture surface format | `server/screen_capture.py` (Linux NvFBC) / `server/mac_screen_capture.py` (Mac SCK) | Linux: NvFBC init flag for P010 / YUV420P10LE instead of BGRA; log the negotiated format at `structlog.info(event="capture_init", pixel_format=...)`. Mac: SCK `SCStreamConfiguration.pixelFormat == kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange` AND `captureDynamicRange == .hdrLocalDisplay` — log both. | Server — asserted in `tests/integration/test_10bit_end_to_end.py` via mocked capture module returning an actual P010 buffer. |
| 2 | Encoder input format | `server/video_encoder.py::_build_ffmpeg_cmd` (Linux) + `server/mac_video_encoder.py` (Mac) | Linux: FFmpeg stdin pipe uses `-pix_fmt p010le -f rawvideo`; assert by parsing the constructed command line. Mac: `CVPixelBufferCreate` is called with `kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange`; assert by mocking `CVPixelBufferCreate` at boundary. | Server — test file asserts command-line invocation; mock-at-VT-boundary asserts buffer format. |
| 3 | Encoder output pix_fmt | Dumped encoded sample | `ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt,profile -of json sample.h265` returns `{"pix_fmt": "yuv420p10le", "profile": "Main 10"}`. Reference JSON committed as `tests/smoke/fixtures/10bit_ramp.reference.ffprobe.json`. | CI — `tests/integration/test_10bit_end_to_end.py` encodes the ramp fixture and ffprobes the output. |
| 4 | Transport wire bytes | HEVC NAL unit VPS/SPS `general_profile_idc` field | Parse the VPS/SPS from the first NAL after the encoder; assert `general_profile_idc == 2` (Main10). Use `pyav`'s built-in NAL parsing or a minimal hand-rolled bit reader. | CI — asserted on the encoded sample bytes in the same integration test as checkpoint 3. |
| 5 | Decoder output format | `client/video_decoder.py::decode_frame` | Assert `frame.format.name in ('p010le', 'yuv420p10le')` on the first decoded frame (not `'rgb24'`). This is NEW — current decoder converts to `'rgb24'` via `frame.to_ndarray(format='rgb24')` which is an 8-bit destination. Phase 2 stops calling `to_ndarray(format='rgb24')` on the hot path and keeps the 10-bit frame. | Client — asserted in `tests/client/test_video_decoder.py` with a known P010 packet input. |
| 6 | Decoder hwaccel path | `client/video_decoder.py` `_CODEC_ORDER` + hwaccel negotiation | Assert `decoder.hw_backend == 'videotoolbox'` when on macOS with Main10 input; fail if it falls through to software for P010. PyAV exposes `ctx.hw_device_ctx` — check non-null after successful init. | Client — existing decoder tests extended; PyAV must be built against FFmpeg 7.1+ with videotoolbox. |
| 7 | Qt display-surface texture format | `client/viewer.py` new QRhiWidget | Assert the created `QRhiTexture` format is `QRhiTexture.R16` + `QRhiTexture.RG16` (not `RGBA8`). Directly queryable via `tex.format()` in the widget. | Client — asserted in a new unit test that instantiates the QRhiWidget with a mocked QRhi (Qt provides a null backend for testing). |
| 8 | Final blit (Metal YUV→RGB shader preserves 10-bit) | The Metal fragment shader | Readback strategy: Metal renders to an offscreen `MTLPixelFormatBGR10A2Unorm` texture; a readback shader samples the 10-bit output and asserts the bottom 2 bits match the expected ramp pattern. **FEASIBILITY CAVEAT:** direct GPU readback of the rendered 10-bit RGB is the honest test, but wiring it into pytest is non-trivial. **ALTERNATIVE capture point:** skip the GPU readback in CI; instead assert the shader's *input* textures round-trip the 10 bits (checkpoint 7) + the *output pipeline format* is 10-bit (`MTLPixelFormatBGR10A2Unorm` backbuffer), and verify the shader math manually in a one-time unit test with synthetic 10-bit input samples and NumPy-computed expected output. | Client — hybrid: automated tex-format assertion + manual-verified-once shader math test. |
| 9 | macOS display pipeline state (Reference Mode / EDR) | Client reads `NSScreen.maximumExtendedDynamicRangeColorComponentValue` + display-mode name | Log-only for v1; not gate-blocking. If `maxEDR < 2.0` on MBP XDR, log a warning "display not in Reference Mode — colors may not be accurate." Phase 7 docs explain. | Client — logged at session start; documented in release notes. |

### ENCODER_DEFS capability extensions (Claude's Discretion #3)

Extend `server/video_encoder.py::HWEncoder` dataclass:
```python
@dataclass
class HWEncoder:
    name: str
    codec: str               # "h264" | "h265" | "av1"
    backend: str             # "nvenc" | "videotoolbox" | "vaapi" | "software"
    supports_444: bool
    supports_lossless: bool
    priority: int
    # Phase 2 ADDITIONS
    supports_main10: bool = False    # 10-bit 4:2:0 at minimum
    supports_422: bool = False       # 10-bit 4:2:2 (Blackwell NVENC + M4 VideoToolbox)
    main10_detection: str = "family" # "family" (static table) | "probe" (live NvEncGetEncodeCaps)
                                     # | "vt_query" (VTCopySupportedPropertyDictionary)
```

Family-seeded values for the existing `ENCODER_DEFS` rows (informed by STACK.md matrix):
- `hevc_nvenc`: `supports_main10=True` (Turing+); `supports_422=False` (upgraded to True per-runtime on Blackwell via `capability_probe.probe_nvenc_main10()`); `main10_detection="probe"`
- `hevc_videotoolbox`: `supports_main10=True` (all Apple Silicon + T2 Intel); `supports_422=False` (upgraded to True per-runtime via `VTCopySupportedPropertyDictionary` on M4+); `main10_detection="vt_query"`
- `h264_*`: `supports_main10` only on Blackwell NVENC; all others `False`.
- `av1_nvenc`: `supports_main10=True` on Ada+; `main10_detection="probe"`.
- `libx265`: `supports_main10=True` (software; zero hardware dependency).

### VIDEO-07: Encoder reconfigure without full pipeline restart

**NVENC path:** `hevc_nvenc` supports bitrate reconfig via FFmpeg's encoder options — set via `AVCodecContext.bit_rate` at runtime. Currently the pipeline tears down and rebuilds on quality change. Phase 2 delta: set the reconfig via FFmpeg stdin control commands (limited) OR accept a brief keyframe request + bitrate set on the running encoder. Not load-bearing for Phase 2; recommend landing the design but accepting a GOP-interval delay for quality changes.

**VTCompressionSession path:** `VTSessionSetProperty` on a live session supports `kVTCompressionPropertyKey_AverageBitRate` and `ExpectedFrameRate` at runtime [CITED: Apple VT docs]. This makes the VT path *easier* to reconfigure than the FFmpeg path.

### VIDEO-09: NvFBC primary / mss fallback zero-copy verification

Existing Phase 1 path already has this; Phase 2 delta is **asserting the 10-bit surface format** in NvFBC init (not the BGRA default). The `server/nvfbc/nvfbc_capture.c` C helper needs a flag for `NVFBC_BUFFER_FORMAT_YUV420P10LE` (if NvFBC SDK 13 supports it — verify against `NvFBC.h` in the tree) and the Python side must pass the flag through. mss fallback is 8-bit only — log a DEGRADED badge in D-03's overlay when the mss path is active.

---

## Hotkey Correctness — INPUT-01..07 (D-10 through D-15)

### D-12 exhaustive keymap test shape

**Structure:** `tests/common/test_keymap.py` grows a parameterized table with ~2000 cases covering:
- **Rows:** every entry in `common/keymap.py::QT_KEY_TO_LINUX` (~80 entries) × (2 platforms: Linux scancode, Mac virtual key code)
- **Columns (modifier chords):** all 32 combinations of {Shift, Ctrl, Alt, Meta, nothing} + every 2-modifier subset + every 3-modifier subset = 2^5 = 32 chords but practically the 1+5+10+10+5+1 = 32-chord space is the right ceiling.
- **Pytest ID format:** `test_keymap[KeyA-Shift-Ctrl-Alt-linux]`, `test_keymap[KeyF4-Shift-Meta-mac]`, etc.
- **Named Flame-critical subset:** `FLAME_CRITICAL_CHORDS` constant with explicit `(key, modifiers)` tuples; marked with `@pytest.mark.flame_critical` so INPUT-04 integration test can `pytest -m flame_critical tests/integration/test_crazy_hotkeys.py`.

**Mac virtual key code source:** Apple's `Carbon/HIToolbox/Events.h` exposes `kVK_*` constants — e.g., `kVK_ANSI_A = 0x00`, `kVK_ANSI_S = 0x01`, `kVK_Return = 0x24`, `kVK_Shift = 0x38`, `kVK_Command = 0x37`. [CITED: developer.apple.com — Carbon Events Framework Reference]. Build the table from this source; verify a small sample manually against a running Mac.

### D-11 release-all-modifiers wire format (Claude's Discretion #2)

```python
# common/messages.py additions

class MsgType:
    ...
    KEY_RESET_MODIFIERS = "key_reset_modifiers"  # client → server
    TEXT_COMMIT          = "text_commit"          # client → server (IME path — D-15)
    PEN_PROXIMITY        = "pen_proximity"        # client → server (D-19)

@dataclass
class KeyResetModifiersMsg:
    type: str = MsgType.KEY_RESET_MODIFIERS
    reason: str = "unknown"  # "focus_out" | "reconnect" | "periodic" | "panic_f9"
    # Server logs `reason` for telemetry; behavior is identical regardless.

@dataclass
class TextCommitMsg:
    type: str = MsgType.TEXT_COMMIT
    text: str = ""          # Unicode-commit string (one or more codepoints)
    # Server injects via CGEventKeyboardSetUnicodeString (Mac) or `xdotool type` (Linux).

@dataclass
class PenProximityMsg:
    type: str = MsgType.PEN_PROXIMITY
    in_proximity: bool = False
    pen_type: str = "pen"   # "pen" | "eraser"

# KeyEvent dataclass EXTENSION (D-14)
@dataclass
class KeyEventMsg:
    type: str = MsgType.KEY_EVENT
    scan_code: int = 0
    pressed: bool = False
    # Phase 2 ADDITIONS (D-14):
    caps_lock_on: bool = False
    num_lock_on: bool = False
    scroll_lock_on: bool = False

# ServerHelloMsg EXTENSION (D-03 + D-05)
@dataclass
class ServerColorCaps:
    main10: bool = False
    chroma_422: bool = False
    chroma_444: bool = False
    advertised_pix_fmt: str = "p010le"  # what the server promises to send

@dataclass
class ServerHelloMsg:
    ... # existing fields
    color_caps: ServerColorCaps = field(default_factory=ServerColorCaps)
```

All new messages go on the **WebSocket control channel** (JSON), not UDP media. They are infrequent + small + must not drop.

### D-13 Server-side key repeat: `xset r off` + client repeats

**Server (Linux):** in `server/session_manager.py` at Xvfb/Xorg spawn, after the display is ready:
```bash
DISPLAY=:N xset r off
```
Wrap in a Python `subprocess.run([...], check=False)` so failure doesn't crash the session. Log the result.

**Server (Mac):** No-op; CGEventPost doesn't auto-repeat.

**Client:** Qt's `QKeyEvent.isAutoRepeat()` returns `True` for repeats. Today the client forwards every keystroke including repeats. Keep that behavior — the wire traffic already matches "client-driven repeats." The only delta is the server-side `xset r off` call to eliminate the server's own auto-repeat.

### D-14 Caps Lock / Num Lock / Scroll Lock state sync

**Client read:**
- Mac: `NSEvent.modifierFlags & NSEventModifierFlagCapsLock` / `NumericPad` / `NumericPad` (Scroll Lock is not exposed on Mac — always False on Mac).
- Linux client (not shipped in v1): `QApplication.keyboardModifiers()` + `QGuiApplication.eventDispatcher` — omit.

**Server write:**
- Linux X11: `XkbSetNamedIndicator` for Caps Lock / Num Lock / Scroll Lock LEDs. Or simpler: `xset led named 'Caps Lock'` / `xset -led named 'Caps Lock'` via subprocess.
- Mac: `CGEventSetFlags` does not persist modifier state across events cleanly; instead, the server applies the caps-lock flag per-injected-event using `CGEventSetFlags(event, kCGEventFlagMaskAlphaShift | ...)`. Caveat: Mac doesn't have a "latched caps lock state" API for event posting outside of the event itself.

**Failure mode to avoid:** Server sends Caps-Lock ON but client thinks it's OFF → artist types "Hello" and gets "HELLO" on reconnect. The next keystroke should auto-converge. Verify in `tests/integration/test_modifier_stress.py` via a scripted press-release sequence.

### D-15 TextCommit wire format for IME / dead-keys

**Client hook:** `RemoteViewer.inputMethodEvent(QInputMethodEvent)` — fires when IME commits a composed string (e.g., Japanese pinyin → kanji, German dead-key `¨` + `a` → `ä`).

```python
def inputMethodEvent(self, event: QInputMethodEvent):
    commit = event.commitString()
    if commit:
        # Forward as a single TextCommit message; do NOT translate to keycodes.
        self.text_commit.emit(commit)
    super().inputMethodEvent(event)
    event.accept()
```

**Server inject:**
- Linux: `xdotool type --clearmodifiers "<string>"` — handles BMP Unicode directly; rare emoji outside BMP may need fallback [CITED: isamert.net 2022]. Alternative: `xdotool key` with `U0041` syntax per-codepoint; slower but handles SMP/emoji.
- Mac: `CGEventKeyboardSetUnicodeString(event, stringLength, buffer)` — posts a CGEvent with Unicode payload that Cocoa apps receive as an NSEvent insertText [CITED: Apple CGEventKeyboardSetUnicodeString docs].

**Coverage:** US + UK + DE + JP layouts pre-release per D-15. The protocol is layout-independent — a JP commit `"あ"` arrives as a Unicode string, not a keycode sequence. No server-side layout awareness needed.

---

## Pen Injection — INPUT-08..12 (D-07 through D-09, D-19, D-20)

### D-07/D-08 spike plan (2 days, pre-phase)

**Day 1:**
- Stand up minimal unsigned Python script using `pyobjc-framework-IOKit` (and `ctypes` for any missing PyObjC bindings — IOHIDUserDevice APIs may need direct ctypes fallback).
- Load real Wacom Intuos Pro Large HID report descriptor bytes from `github.com/linuxwacom/wacom-hid-descriptors` (closest real-device capture) or the USB HID spec's Digitizer Usage Page (0x0D).
- Call `IOHIDUserDeviceCreate(allocator, properties_dict)` where `properties_dict` includes:
  - `kIOHIDVendorIDKey` = 0x056a (Wacom)
  - `kIOHIDProductIDKey` = the Intuos Pro Large product ID (varies by exact model; table in the linuxwacom repo)
  - `kIOHIDReportDescriptorKey` = NSData wrapping descriptor bytes
  - `kIOHIDPrimaryUsagePageKey` = 0x0D (Digitizer)
  - `kIOHIDPrimaryUsageKey` = 0x02 (Pen)
- Verify the device appears in `ioreg -l -c IOHIDUserDevice`.

**Day 2:**
- Build a minimal pen-report synthesis loop that produces 0→max pressure sweep + tilt + eraser flip.
- Call `IOHIDUserDeviceHandleReport(device, report_bytes)` at ~100Hz.
- Open Photoshop / Preview / Pixelmator / a simple NSView app with `mouseMoved` + `tabletPoint` overrides. Confirm `NSEvent.type == NSEventTypeTabletPoint` AND `NSEvent.pressure > 0`.
- D-08 pass bar: **Photoshop / Preview / a simple NSView test app reads non-zero `NSEvent.pressure` across a complete pen stroke.** Code quality is not the criterion; proof of pressure round-trip is.

**Pass → continue:** Phase 2 productionizes `server/mac_pen_injector.py` in Wave 4 and runs D-17's 6-step Wacom matrix at Phase 2 completion.

**Fail → D-07 rescope path:**
- INPUT-08 re-scoped to "Mac-server pen pressure is a v1 known-limitation; `mac_input_injector.pen_event` keeps the mouse-click fallback with a one-time warning."
- Document in `docs/release.md`: "For Flame pressure workflows on v1, use the Rocky Linux server. Mac server is for dev/testing + non-pressure-critical editing."
- HIDDriverKit entitlement path goes to the v1.1 roadmap.
- Rocky server remains Flame-production path; Mac-server-for-Flame is nice-to-have, not load-bearing. PROJECT.md framing is intact.

### D-20 TCC / Wacom driver detection

**Path:** `~/Library/Application Support/com.apple.TCC/TCC.db` — SQLite. Read-only open with `sqlite3.connect('file:...?mode=ro', uri=True)`.

**Schema (relevant):** `access` table with columns `(service, client, auth_value, ...)`. Query:
```sql
SELECT service, client, auth_value FROM access
WHERE service IN ('kTCCServiceAccessibility', 'kTCCServiceListenEvent',
                  'kTCCServicePostEvent', 'kTCCServiceScreenCapture')
AND client IN ('com.teraguchi.client', 'com.wacom.driver', 'com.wacom.%');
```
(`kTCCServiceListenEvent` ≈ Input Monitoring; `auth_value >= 2` means granted.)

**UI:** `client/key_diagnostic.py` grows a **"Wacom setup"** tab showing:
- Teraguchi client has Input Monitoring: ✓ / ✗ (button: "Open System Settings → Privacy → Input Monitoring")
- Wacom driver has Accessibility: ✓ / ✗ / (not installed) (button + link to wacom.com/drivers)
- Wacom driver detected: version X or "Install the Wacom driver"
- Wacom device connected: serial X / none (from `system_profiler SPUSBDataType`)

**Read-only, no bypass, no write attempts.** Phase 1's pattern for `sqlite3.connect` with `mode=ro` is safe + audit-friendly.

### D-19 Pen proximity recovery

**Client side:**
```python
# client/viewer.py additions
def focusInEvent(self, event):
    super().focusInEvent(event)
    if self._pen_was_in_proximity:  # cached from last known state
        self._send_pen_proximity(in_proximity=True, pen_type=self._last_pen_type)

def showEvent(self, event):
    super().showEvent(event)
    self._send_pen_proximity(in_proximity=True, pen_type=self._last_pen_type or "pen")
```

**Server side:** New `PenFSM` in `common/session_fsm.py`:
```python
# Claude's Discretion #6: separate FSM rather than extending SessionFSM
class PenFSM(StateMachine):
    out_of_proximity = State('out_of_proximity', initial=True)
    in_proximity    = State('in_proximity')

    enter_proximity = out_of_proximity.to(in_proximity) | in_proximity.to(in_proximity)  # idempotent
    leave_proximity = in_proximity.to(out_of_proximity) | out_of_proximity.to(out_of_proximity)
```

Idempotent transitions mean duplicate proximity-enter events are no-ops — safe for the D-19 re-synth-on-focus pattern.

---

## Wacom Matrix — INPUT-09..12 (D-16 through D-18)

### D-16 Matrix cadence + D-17 pass criteria

**When:** ONCE at Phase 2 completion (gate). Repeat on every `v*` release tag (per `docs/release.md` runbook). No self-hosted CI runners.

**Where:** DXS lab — Intuos Pro Large + Cintiq Pro 24 × macOS Sonoma + macOS Sequoia = 4 cells.

**Protocol (verbatim from D-17):**

1. **Pressure ramp** — Draw slow 0→max stroke over ~5s. Assertion: `QTabletEvent.pressure` client-side log has `>= 200` samples with pressure in `(0, 1.0]`. D-18 RMS calculation is against this log.
2. **Eraser flip** — Turn pen over, stroke, verify eraser path fires. Assertion: client-side log has events with `pointerType == Eraser` and `pressure > 0`.
3. **Tilt test** — Hold pen tilted, verify `xTilt`/`yTilt` values vary correctly. Assertion: client log has `|xTilt| > 10` or `|yTilt| > 10` (degrees) in at least one event.
4. **Proximity cycle** — Lift pen out of range and back in. Assertion: server structlog has a `PenFSM` transition from `out_of_proximity → in_proximity` and back.
5. **Tablet-side buttons** — Press each side button while stroking. Assertion: server structlog has button-press events with both side buttons fired at least once.
6. **Reconnect mid-stroke** — Disconnect WebSocket during a pressure stroke, reconnect, verify no stuck pen / no leaked pressure. Assertion: post-reconnect server pen state is `out_of_proximity`; no residual pressure reports in the 500ms after disconnect.

**Pass gate:** Each step's assertion holds AND manual "pressure curve feels right in Flame" sign-off AND D-18 RMS < 1% AND video recorded + attached to release runbook.

### D-17 structlog JSON emission shape (recommended schema)

```json
{"event":"wacom_matrix","stage":"pressure_ramp","cell":"intuos-pro-L-sonoma",
 "timestamp_ns":1745000000000000000,"counter":"client_pressure_samples","n":237}
{"event":"wacom_matrix","stage":"pressure_ramp","cell":"intuos-pro-L-sonoma",
 "timestamp_ns":1745000000016000000,"client_pressure":0.452,"client_tilt_x":-12.0,
 "client_tilt_y":4.5,"client_pointer_type":"pen"}
{"event":"wacom_matrix","stage":"pressure_ramp","cell":"intuos-pro-L-sonoma",
 "timestamp_ns":1745000000016100000,"server_hid_pressure_raw":462,
 "server_hid_pressure_normalized":0.4512,"server_hid_report_seq":237}
```

Two side-by-side JSONL files: `client.jsonl`, `server.jsonl`. `tools/wacom_quant_analysis.py` aligns by `timestamp_ns`, interpolates server samples to client sample points via `scipy.interpolate.interp1d`, computes RMS of `(client_pressure - interp(server_pressure_norm))` over the ramp.

### D-18 wacom_quant_analysis.py layout (Claude's Discretion #7)

Single-file script; <200 lines. CLI:
```bash
python tools/wacom_quant_analysis.py \
    --client-log artifacts/client.jsonl \
    --server-log artifacts/server.jsonl \
    --cell intuos-pro-L-sonoma \
    --out-svg artifacts/rms_intuos_pro_L_sonoma.svg \
    --pass-threshold 0.01  # RMS < 1%

# Exit code 0 if RMS < threshold across the ramp; exit code 1 otherwise.
```

Dependencies: `numpy`, `scipy` (already available for Phase 4 audio). `matplotlib` for the SVG output (add to `requirements-dev.txt`).

Output: SVG plot showing (client pressure vs time) over (server pressure vs time), plus RMS annotation. Commit to release notes + attach to the GitHub Release artifact list.

---

## Latency Gate (D-21)

**Keep:** Phase 1 synthetic p99 < 30ms gate (widened from 25ms; documented deviation #1). Runs in CI on every PR.

**Add:** One-time DXS real-hardware measurement at Phase 2 completion.
- Setup: DXS Flame workstation + real Wacom + real Cintiq. Client is Mac Studio / MacBook Pro on LAN.
- Tool: Phase 1's per-stage latency structlog telemetry. Load into a notebook or ad-hoc script; compute end-to-end input-to-photon p50/p95/p99.
- Procedure: Run 3 trials pre-Phase-2 (against the current v0.x build or HEAD-of-dev-before-phase-2 tag). Run 3 trials post-Phase-2 (final). Record medians.
- Commit: Numbers go into `docs/release.md` (seed in this phase; grows through Phase 6).
- Regression trigger: if post-Phase-2 p99 > pre-Phase-2 p99 × 1.15, investigate before sign-off. (1.15x slack covers measurement noise; tighter thresholds are noise-dominated at single-session scale.)

---

## Validation Architecture

**Test framework:** pytest 8.3+, pytest-asyncio 0.25+. Same as Phase 1.
**Config file:** `pyproject.toml` (pytest config already landed in Phase 1).
**Quick run:** `pytest tests/ -x --timeout=30 -m "not latency_bench and not smoke_1h and not wacom_hw"` (~30s per Phase 1 precedent; the new `wacom_hw` marker gates manual hardware tests out of default runs).
**Full suite:** `pytest tests/ --timeout=120` (~2 min including latency-bench; `wacom_hw` still excluded — that's manual).
**Phase gate command:** `pytest tests/ -m "not wacom_hw" && pytest tests/ -m wacom_hw --cell=<all 4 cells>` — the second invocation runs manually on DXS hardware at Phase completion.

### Phase 2 success criteria → Nyquist signals

**Criterion 1: Bit-exact 10-bit ramp fixture round-trips with no banding; regression fails CI.**

| Aspect | Detail |
|--------|--------|
| Measurable signal | All 9 checkpoints in D-01 assert their expected pixel/container format; one regression fails. |
| Instrumentation boundary | Mock at FFmpeg subprocess boundary + VT session boundary + NvFBC subprocess boundary (Phase 1 pattern extended). Real ffprobe on dumped sample for checkpoint 3. Real PyAV decode for checkpoint 5/6. Null `QRhi` backend for checkpoint 7 (Qt provides this for tests). Manual-verified-once shader math for checkpoint 8. |
| Minimum reproducible fixture | `tests/smoke/fixtures/10bit_ramp.p010.bin` (generated 1920×1080 10-bit ramp binary, ~8MB); `tests/smoke/fixtures/10bit_ramp.reference.ffprobe.json` (expected ffprobe output); `tests/integration/test_10bit_end_to_end.py` (drives all 9 assertions). |
| Mock vs real boundary | **Mock:** FFmpeg subprocess, VT session, NvFBC helper, SCK frame delivery. **Real:** `ffprobe` binary, PyAV decoder, Qt null-QRhi. No real-hardware in CI. |

**Criterion 2: "Crazy Flame hotkey combos" integration test passes vs real Rocky + Mac server with zero mangling.**

| Aspect | Detail |
|--------|--------|
| Measurable signal | Every Flame-critical chord in `FLAME_CRITICAL_CHORDS` produces the expected server-side scancode + modifier state; Cmd↔Ctrl swap engages per-bookmark; Caps Lock state sync round-trips; dead-key sequences on DE/JP layouts commit the correct Unicode text. |
| Instrumentation boundary | Table-driven keymap tests mock nothing (pure data). Integration test boots server in-process against an Xvfb virtual display (Phase 1 infra), drives the client protocol with synthetic `KeyEvent` + `TextCommit` sequences, reads server injection via intercepting at the `InputInjector.key_event` / `InputInjector.text_commit` boundary. |
| Minimum reproducible fixture | `tests/integration/test_crazy_hotkeys.py` with `@pytest.mark.flame_critical` on the named subset. Shared Xvfb fixture (`conftest.py`) boots + tears down for the test. International layouts: per-layout xkb-setxkbmap subprocess call in test setup, then synthetic sequences. |
| Mock vs real boundary | **Mock:** network transport (in-process loopback, Phase 1 pattern). **Real:** Xvfb, `setxkbmap`, `xdotool` (for TextCommit injection), the `common/keymap.py` table. No real Mac-server CI — that lives in the manual D-17 matrix run. |

**Criterion 3: Wacom pen pressure, tilt, eraser, buttons, proximity round-trip with sub-1% RMS on 4-cell matrix.**

| Aspect | Detail |
|--------|--------|
| Measurable signal | D-17's 6-step protocol passes each cell; D-18's RMS calculation returns < 1% per cell; manual "feels right" sign-off. |
| Instrumentation boundary | NOT AUTOMATED — explicitly manual per D-16 "no self-hosted Wacom runners." In-CI substitute: `tests/server/test_mac_pen_injector.py` mocks at IOKit boundary and asserts correct HID report bytes for each pen-event shape. Structlog emission schema (see D-17 section) verified in unit tests. |
| Minimum reproducible fixture | Manual: DXS lab + 4 hardware cells + video recording + two JSONL logs + the RMS script. CI: mocked HID-report-bytes assertion + structlog emission schema unit test. |
| Mock vs real boundary | **Mock (CI):** IOKit, `pyobjc-framework-IOKit`. **Real (manual):** actual Wacom hardware, macOS 14 + 15, a live pen stroke, RMS script processing JSONL. |

**Criterion 4: Input-to-photon on LAN sub-20ms; CI benchmark fails build if number drifts above 25ms (current gate widened to 30ms per Phase 1 deviation).**

| Aspect | Detail |
|--------|--------|
| Measurable signal | Phase 1's existing synthetic latency benchmark (`tests/smoke/test_latency_benchmark.py`) continues to gate at p99 < 30ms. Phase 2 adds: 10-bit pipeline latency delta (the VT wrapper + 10-bit decode + QRhi blit cannot add >2ms over the Phase 1 baseline). Phase 2 DXS one-shot real-hardware measurement (D-21) commits pre/post numbers to `docs/release.md`. |
| Instrumentation boundary | Synthetic: Phase 1 patterns. Real-HW: structlog per-stage telemetry from Phase 1 OBS-02 loaded into ad-hoc analysis script. |
| Minimum reproducible fixture | Synthetic: existing Phase 1 benchmark, extended with a 10-bit variant. Real-HW: DXS setup protocol documented in `docs/release.md`. |
| Mock vs real boundary | **Mock (CI):** encoder subprocess, network transport. **Real (manual/DXS):** actual Mac client, actual Rocky Flame workstation, actual Wacom, actual display, actual Tailscale LAN. |

**Criterion 5: Modifiers release cleanly on `WindowDeactivate` and reconnect; verified by automated focus-stress and reconnect-stress tests.**

| Aspect | Detail |
|--------|--------|
| Measurable signal | After a scripted sequence of (press Ctrl+Shift, Cmd-Tab away, verify server state = no modifiers held), the server's `InputInjector.reset_modifiers()` has been called + server-side modifier state is empty. Reconnect equivalent: (press Ctrl, sever WS, reconnect, verify first post-auth message is `KEY_RESET_MODIFIERS`, verify server state clean). |
| Instrumentation boundary | In-process integration test. Client focusOut / windowDeactivate simulated via `QApplication.sendEvent(viewer, QFocusEvent(QEvent.FocusOut))`. Reconnect simulated via `ConnectionSupervisor.drop_and_reconnect()` helper (Phase 1 provided). Server asserted via `InputInjector.reset_modifiers` call-count mock + server-side modifier state inspection. |
| Minimum reproducible fixture | `tests/integration/test_modifier_stress.py`. Phase 1 ConnectionSupervisor + `InputInjector` are already in tree. |
| Mock vs real boundary | **Mock:** network transport (loopback), `InputInjector` backing (uinput/CGEvent). **Real:** Qt event loop, `ConnectionSupervisor` state machine, `KeyResetModifiersMsg` wire format. |

### Bit-exact 10-bit fixture (D-01 / VIDEO-02) — standalone Nyquist signal

The 9-checkpoint fixture deserves its own row because it is the single test that Phase 2 ships or fails against:

| Aspect | Detail |
|--------|--------|
| Measurable signal | 9 assertions per D-01 table. Single test: `tests/integration/test_10bit_end_to_end.py::test_9_checkpoints`. |
| Instrumentation boundary | See Criterion 1 above. |
| Minimum reproducible fixture | `tests/smoke/fixtures/10bit_ramp.p010.bin` (generated via a one-time `tools/gen_10bit_ramp.py` script committed alongside the binary — reproducibility + auditability). |
| Mock vs real boundary | See Criterion 1. |

### Wave 0 gaps to close before implementation

- [ ] `tests/common/test_keymap.py` — Phase 1 shipped the stub; Phase 2 D-12 expands. **Must exist before Wave 1** (blocks INPUT-01/-04/-07 plans).
- [ ] `tests/smoke/fixtures/10bit_ramp.p010.bin` — must be generated by `tools/gen_10bit_ramp.py` and committed. **Must exist before Wave 2** (blocks VIDEO-01/-02/-03/-04 plans).
- [ ] `tools/gen_10bit_ramp.py` — reproducibility generator. Lives beside the binary. **Ships in Wave 0.**
- [ ] `tests/server/test_capability_probe.py` (mock at nvidia-smi + NvEncGetEncodeCaps subprocess boundary) — **must exist before Wave 2** (blocks D-03 plan).
- [ ] `tests/server/test_mac_video_encoder.py` (mock at VT boundary) — **must exist before Wave 2** (blocks D-04 plan).
- [ ] `tests/server/test_mac_pen_injector.py` (mock at IOKit boundary) — **conditional on D-08 pass**; ships in Wave 4 if spike passes.
- [ ] `tests/client/test_tcc_detection.py` — **must exist before Wave 4** (blocks D-20 plan).
- [ ] `pytest.mark.flame_critical` + `pytest.mark.wacom_hw` markers declared in `pyproject.toml` — **Wave 0**, one-line addition.
- [ ] `conftest.py` fixture for Xvfb session + xkb layout switching — **Wave 0**, reused by INPUT-04 + INPUT-05 tests.

---

## Common Pitfalls

### Pitfall 1: NVENC silent Main10 fallback

**What goes wrong:** Client selects 10-bit, NVENC accepts the Main10 profile spec, but silently falls back to Main (8-bit) on older silicon or with a mismatched driver version. Output looks fine until an artist grades a gradient and sees banding.

**Why it happens:** FFmpeg's `hevc_nvenc` flag `-profile:v main10` is advisory, not enforced. NVENC returns success but degrades.

**How to avoid:** D-03's `NvEncGetEncodeCaps` probe + D-01's checkpoint 3 (`ffprobe` asserting `pix_fmt=yuv420p10le` on a dumped sample). Never rely on encoder init returning success.

**Warning signs:** `ffprobe` reports `pix_fmt=yuv420p`, `profile=Main`. Client CPU spikes when trying to decode "10-bit" that's actually 8-bit wrapped weirdly.

**Source:** [CITED: obsproject.com forum — "NVENC error: cannot perform 10-bit encode on this encoder"]

### Pitfall 2: VideoToolbox silent software fallback on P010 decode

**What goes wrong:** PyAV's `videotoolbox` hwaccel quietly drops to software decode for P010 (10-bit HEVC), causing the client to burn CPU and drop frames. No error, just slow.

**Why it happens:** VTDecompressionSession falls back to software if the P010 pipeline has any unusual config (odd-dimension frames, unsupported color matrix, etc.). Covered in PITFALLS #2 and [CITED: iina GitHub issue #5796].

**How to avoid:** D-01's checkpoint 6 — assert `decoder.hw_backend == 'videotoolbox'` AFTER decoding a real P010 packet, not just after init.

**Warning signs:** Client CPU at 100% during playback. `decoder.avg_decode_time_ms > 15`.

### Pitfall 3: Qt QImage 8-bit coercion

**What goes wrong:** Developer writes `QImage(Format_RGB32)` or `.to_ndarray(format='rgb24')` anywhere in the video path and 10-bit gets coerced to 8-bit without a visible error.

**Why it happens:** QImage has no 10-bit-aware format (`Format_RGBA64` is 16-bit but QPainter doesn't preserve it to the backbuffer). `to_ndarray(format='rgb24')` is a 8-bit destination.

**How to avoid:** D-02 — video layer uses QRhiWidget + Metal textures exclusively. QImage only for overlay / cursor. Lint-level rule: `tests/client/test_no_qimage_in_video_path.py` greps `client/viewer.py`'s video path for `QImage(` or `to_ndarray(format='rgb24')` and fails if found.

**Warning signs:** Banding on a 10-bit gradient test pattern. `pix_fmt` assertion fails on the client decoder.

### Pitfall 4: IOHIDUserDevice leaks on server crash

**What goes wrong:** Server crashes mid-session; the IOKit-registered virtual tablet device persists in `ioreg` until reboot. Next session refuses to register because the name conflicts, or worse, the ghost device corrupts input for the logged-in user.

**Why it happens:** IOHIDUserDevice is process-owned but IOKit's cleanup is not always immediate on abnormal exit.

**How to avoid:** `atexit.register()` hook + `signal.SIGTERM`/`SIGINT` handlers that call `IOHIDUserDeviceDestroy()` (or the equivalent release). D-17's step 6 (reconnect mid-stroke) exercises this path — verify with `ioreg -l -c IOHIDUserDevice` before/after.

**Warning signs:** `ioreg -l | grep IOHIDUserDevice` shows residual "TeraguchiTablet" entries after server exit.

### Pitfall 5: Cmd↔Ctrl swap out of sync between UI and keymap

**What goes wrong:** User toggles "Swap Cmd/Ctrl" checkbox in the bookmark UI, but the keymap table is read from a stale in-memory copy. The artist sees "swap: on" in the overlay but their Cmd+S is being sent as Cmd+S (not Ctrl+S).

**Why it happens:** Inference at the callsite instead of a single authoritative table. Matches PITFALLS #4.

**How to avoid:** `common/keymap.py` exposes a pure function `translate_key(qt_key, modifiers, target_platform, swap_cmd_ctrl)` that returns the injected scancode. Every callsite calls this function. Toggle updates a ClientSession attribute; attribute is threaded through every outbound KeyEvent. Health overlay reads from the same ClientSession attribute.

### Pitfall 6: Release-all-modifiers fires during a legitimate chord

**What goes wrong:** Server-side periodic safety net (D-11 trigger 3) fires a reset while the user is holding Ctrl+Shift for a deliberate chord. The chord gets broken mid-press.

**Why it happens:** "Every ~10s while no keyboard events have arrived" — but a held modifier with no fresh events IS the state we're trying to preserve.

**How to avoid:** Trigger 3's precondition is "no keyboard events in the last 10s" — meaning the last key event was ≥10s ago. Artists don't hold modifiers for 10 uninterrupted seconds. Use `monotonic()` of the last KeyEvent processed; if the modifier was actively engaged in that event, reset the timer. Document in `server/platform_backends.py::InputInjector.reset_modifiers` docstring.

**Warning signs:** INPUT-04 integration test shows a held-chord being broken. D-17 step 1 (slow 5s pressure ramp with a modifier held) catches this.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| macOS 14.0+ (Sonoma) | D-20 TCC, D-04 VT low-latency HEVC on Apple Silicon | ✓ (dev machines) | Sonoma + Sequoia | — |
| Apple Silicon (M-series) | D-04 low-latency HEVC Main10 | ✓ | M1/M2/M3/M4 | Intel T2 Macs supported for encode, but low-latency mode was H.264-only historically — verify per D-03 at startup |
| `pyobjc-framework-VideoToolbox` 11.x+ | D-04 | ✓ via pip | 11.x | — |
| `pyobjc-framework-IOKit` 11.x+ | D-07..09 | ✓ via pip | 11.x | — |
| PySide6 6.10.x | D-02 QRhiWidget | ✓ (Phase 1 pinned 6.8+ floor) | 6.10.x available | QOpenGLWidget escape hatch |
| Qt QRhi Metal backend | D-02 | Bundled with PySide6 6.10 | — | — |
| `qsb` shader baker | D-02 Metal shader compilation | ✓ via `pyside6-qsb` | Ships with PySide6 | — |
| PyAV 17.x against system FFmpeg 7.1+ | D-01 checkpoints 5/6, VIDEO-12 | ✓ but rebuilds needed on every dev machine | Homebrew FFmpeg 7.1 on macOS; RPMFusion on Rocky | Phase 1 already documented this in `docs/build-pyav-macos.md` (create if absent) |
| `ffprobe` 7.1+ | D-01 checkpoint 3 | ✓ via FFmpeg install | 7.1+ | — |
| NVIDIA driver 525+ (Ampere floor) / 570+ (Blackwell) | D-03 NVENC Main10 probe on Rocky 9 | ✓ on DXS Flame workstations | 535+ assumed | Degrade path per D-03 "not supported" badge |
| Rocky Linux 9.3 / 9.5 | Server OS | ✓ on DXS fleet | 9.3+ | — |
| Xorg (X11), NO Wayland | Linux server display | ✓ (Rocky 9 default) | 1.20+ | Blocked on Autodesk Flame Wayland support |
| Intuos Pro Large | D-17 / D-18 hardware cell 1/2 | ✓ (DXS lab) | — | — |
| Cintiq Pro 24 | D-17 / D-18 hardware cell 3/4 | ✓ (DXS lab) | — | — |
| Wacom driver (macOS) | Client-side tablet event pipeline on real HW | ✓ | Wacom driver 6.4+ for Sonoma, 6.4.x for Sequoia [CITED: support.wacom.com] | If missing, D-20 detection panel deep-links to wacom.com |

**Missing dependencies with no fallback:** None for Phase 2.

**Missing dependencies with fallback:** IOHIDUserDevice spike outcome — if it fails, D-07's rescope path fills the gap.

---

## Code Examples

Verified patterns from official sources (Context7 unavailable; WebSearch + WebFetch + project STACK.md cross-referenced):

### Capability probe (VTCopySupportedPropertyDictionary for D-03)

```python
# server/capability_probe.py
# Source: Apple VT docs — https://developer.apple.com/documentation/videotoolbox/vtcompressionsession-api-collection
# CITED: kVTCompressionPropertyKey_ProfileLevel + kVTProfileLevel_HEVC_Main10_AutoLevel are public since macOS 11.

import objc
import VideoToolbox as VT
import CoreMedia as CM

def probe_vt_main10() -> dict[str, bool]:
    """Returns {'main10': bool, 'chroma_422': bool, 'chroma_444': bool, 'low_latency_hevc': bool}"""
    # 1. VTIsHardwareDecodeSupported(kCMVideoCodecType_HEVC) → hw decode at all?
    supported = VT.VTIsHardwareDecodeSupported(CM.kCMVideoCodecType_HEVC)

    # 2. Trial-create a session with Main10 + LowLatency; if it returns noErr, we're good.
    # The trial session is destroyed immediately; it's cheap.
    spec = {
        VT.kVTVideoEncoderSpecification_EnableLowLatencyRateControl: True,
        VT.kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder: True,
    }
    session = None
    status = VT.VTCompressionSessionCreate(
        None, 1280, 720, CM.kCMVideoCodecType_HEVC,
        spec, None, None, None, None, session
    )
    low_latency_hevc = (status == 0)
    if session:
        VT.VTCompressionSessionInvalidate(session)

    # 3. For 4:2:2 (M3+ decode, M4 encode): trial a Main42210 profile session.
    # Full code omitted for brevity — same pattern, different profile constant.

    return {
        "main10": bool(supported),
        "chroma_422": False,  # set True if Main42210 trial succeeds
        "chroma_444": False,
        "low_latency_hevc": low_latency_hevc,
    }
```

### NvEncGetEncodeCaps probe via ffmpeg trial (for D-03 Linux)

```python
# server/capability_probe.py
# Source: NVIDIA VCSDK 13 — https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-application-note/

import subprocess
import shlex

def probe_nvenc_main10() -> dict[str, bool]:
    """Returns {'main10': bool, 'chroma_422': bool, 'chroma_444': bool}"""
    # Trial-encode 32x32 P010 single frame to null.
    cmd = (
        "ffmpeg -v error "
        "-f rawvideo -pix_fmt p010le -s 32x32 -i /dev/zero "
        "-t 0.01 -c:v hevc_nvenc -profile:v main10 -pix_fmt p010le "
        "-f null -"
    )
    result = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=5)
    main10_ok = (result.returncode == 0 and b'10-bit' not in result.stderr.encode())

    # Trial 4:2:2 for Blackwell detection (same pattern, -pix_fmt yuv422p10le)
    # ... (omitted)

    return {"main10": main10_ok, "chroma_422": False, "chroma_444": False}
```

### IOHIDUserDevice creation sketch (D-07/D-09)

```python
# server/mac_pen_injector.py — SPIKE SKETCH (final form depends on D-08 outcome)
# Source: IOKit IOHIDUserDevice.h (Apple system header)
# Source: wacom-qemu — https://github.com/thenickdude/wacom-qemu (virtual Wacom HID for VMs)
# CITED: foohid GitHub — userspace virtual HID pattern (foohid uses a KEXT; we want pure userspace IOHIDUserDevice)

import ctypes
import objc
from Foundation import NSMutableDictionary, NSData

# Load IOKit (PyObjC's IOKit framework binding)
IOKit = objc.loadBundle('IOKit', globals(), bundle_path='/System/Library/Frameworks/IOKit.framework')

# Wacom Intuos Pro L HID report descriptor (abbreviated — real descriptor from linuxwacom/wacom-hid-descriptors)
WACOM_INTUOS_PRO_L_DESCRIPTOR = bytes([
    0x05, 0x0D,        # Usage Page (Digitizer)
    0x09, 0x02,        # Usage (Pen)
    0xA1, 0x01,        # Collection (Application)
    # ... pressure, tilt, eraser, proximity, buttons, X, Y ...
    0xC0,              # End Collection
])

def create_virtual_tablet():
    props = NSMutableDictionary.alloc().init()
    props[u'VendorID']       = 0x056a       # Wacom
    props[u'ProductID']      = 0x034d       # Intuos Pro L (example)
    props[u'ReportDescriptor'] = NSData.dataWithBytes_length_(
        WACOM_INTUOS_PRO_L_DESCRIPTOR, len(WACOM_INTUOS_PRO_L_DESCRIPTOR)
    )
    props[u'PrimaryUsagePage'] = 0x0D
    props[u'PrimaryUsage']     = 0x02

    # IOHIDUserDeviceCreate via PyObjC or ctypes fallback
    # Final code shape determined by D-07 spike
    ...
```

### QRhiWidget Metal BT.709 shader pair (D-02)

```glsl
// client/shaders/video_blit.vert
#version 440
layout(location = 0) in vec4 position;
layout(location = 0) out vec2 v_uv;
void main() {
    v_uv = position.xy * 0.5 + 0.5;
    gl_Position = position;
}
```

```glsl
// client/shaders/video_blit.frag (BT.709 video-range)
#version 440
layout(binding = 1) uniform sampler2D yTex;
layout(binding = 2) uniform sampler2D uvTex;
layout(location = 0) in vec2 v_uv;
layout(location = 0) out vec4 fragColor;
void main() {
    // P010 stores 10-bit in the top 10 bits of 16-bit container; Qt R16 sampler
    // returns normalized [0,1] values. If capture is video-range (BT.709), center + scale:
    float Y  = (texture(yTex,  v_uv).r - 0.0625) * 1.1643;       // (Y - 16/256) * (1/0.859375)
    vec2  UV = (texture(uvTex, v_uv).rg - vec2(0.5, 0.5)) * 1.1384;
    float R = Y + 1.5748 * UV.y;
    float G = Y - 0.1873 * UV.x - 0.4681 * UV.y;
    float B = Y + 1.8556 * UV.x;
    fragColor = vec4(R, G, B, 1.0);
}
```

Bake via `pyside6-qsb -o video_blit.frag.qsb video_blit.frag` in a `build_shaders.sh` that CI can verify no-diff against.

### TextCommit injection on Linux (D-15)

```python
# server/platform_backends.py or server/input_injector.py extension
def text_commit(self, text: str):
    """Inject a Unicode commit string. Layout-independent."""
    # xdotool type handles BMP unicode directly (via X11 Unicode input)
    subprocess.run(
        ["xdotool", "type", "--clearmodifiers", "--delay", "0", text],
        check=False, timeout=2
    )
    # For characters outside BMP (emoji, less common in grading workflows):
    # fallback per-codepoint via `xdotool key U<hex>` — handle if xdotool type fails.
```

### TextCommit injection on Mac (D-15)

```python
# server/mac_input_injector.py extension
# Source: Apple CGEventKeyboardSetUnicodeString docs

from CoreGraphics import (CGEventCreateKeyboardEvent, CGEventPost,
                          kCGHIDEventTap, CGEventKeyboardSetUnicodeString)

def text_commit(self, text: str):
    """Inject a Unicode commit string via CGEventKeyboardSetUnicodeString."""
    # Down event (virtual keycode 0; ignored when UnicodeString is set)
    event = CGEventCreateKeyboardEvent(None, 0, True)
    text_bytes = text.encode('utf-16-le')
    # CGEventKeyboardSetUnicodeString(event, unichars_len, unichars_buffer)
    CGEventKeyboardSetUnicodeString(event, len(text), text)
    CGEventPost(kCGHIDEventTap, event)
    # Up event
    event = CGEventCreateKeyboardEvent(None, 0, False)
    CGEventKeyboardSetUnicodeString(event, len(text), text)
    CGEventPost(kCGHIDEventTap, event)
```

---

## State of the Art

| Old approach | Current approach | When changed | Impact |
|--------------|------------------|--------------|--------|
| FFmpeg `hevc_videotoolbox` subprocess on Mac | Direct VTCompressionSession via PyObjC | 2025–2026 (WWDC21's low-latency flag + Apple Silicon HEVC support) | 5-8ms/frame saved per WWDC21 10158; now possible via FFmpeg 2025 patch but D-04 goes direct for control [CITED: ffmpeg-devel 2025-07 PR] |
| CGEventPost for pen pressure injection on Mac | IOHIDUserDevice virtual tablet | Community pattern since ~2015 (wacom-qemu); HIDDriverKit added 2019 (entitlement-gated) | Real `NSEvent.pressure` round-trip to apps; KEXT path deprecated |
| NvFBC + BGRA 8-bit capture on Linux | NvFBC + P010/YUV420P10LE 10-bit surface | NvFBC has supported 10-bit surfaces for several SDK generations; Teraguchi hasn't wired it yet | Closes D-01 checkpoint 1 |
| Static NVENC capability table | `NvEncGetEncodeCaps` live probe + family table sanity | Blackwell (2025) added 4:2:2; driver version mismatches routine | D-03 correctness |
| QImage blit for decoded video | QRhiWidget + Metal textures + BT.709 shader | QRhi stabilized in Qt 6.8; 6.10 is first shipping PySide6 release with QRhiWidget as a first-class citizen [CITED: qt.io 6.10 blog] | D-02 honest 10-bit to display |
| Synthesized keycodes for IME / dead-keys | `TextCommit` unicode commit strings via `inputMethodEvent` | Modern Qt idiom since Qt 5; PySide6 exposes cleanly | D-15 layout-independence |

**Deprecated/outdated in Phase 2's scope:**

- **KEXTs for virtual HID** — deprecated since Catalina; HIDDriverKit replaces for signed distribution; IOHIDUserDevice (userspace) for pure-userspace.
- **`xcb_wacom` Qt plugin** — removed Qt5 era; modern Qt uses xinput2 / NSEvent — not a concern for Phase 2 but noted in STACK.md.
- **EGLStreams** for any NVIDIA Wayland work — NVIDIA committed to GBM; irrelevant for v1 (Wayland blocked by Flame).
- **AAC audio** — noted for v1.x if ever needed; Opus is the v1 choice; out-of-scope for Phase 2.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | IOHIDUserDevice userspace registration in Sonoma+ produces `NSEvent.pressure` visible to arbitrary apps (Photoshop, Preview) without signing/entitlements | Pen Injection | D-07 spike may fail. D-07 rescope path is already documented; Rocky remains Flame-production path. |
| A2 | `kVTVideoEncoderSpecification_EnableLowLatencyRateControl` works with HEVC on Apple Silicon in macOS 14+ (Sonoma+). Historical: H.264-only on Intel. | Color / D-04 | D-04 VT wrapper falls back to non-low-latency HEVC (accept 5-8ms penalty) or low-latency H.264 (sacrifice color for latency). Probe at startup per D-03. Supporting evidence: FFmpeg 2025-07 patch enables this in ffmpeg's own hevc_videotoolbox for Apple Silicon [CITED: ffmpeg-devel ML]. |
| A3 | QRhiWidget P010 texture upload via `QRhiTexture.R16` + `QRhiTexture.RG16` works on Qt 6.10's Metal backend | Color / D-02 | Fallback to QOpenGLWidget + GL_R16 textures + GLSL ES 3.0 shader. Day-plus of extra work, documented escape hatch. |
| A4 | `NvEncGetEncodeCaps` can be queried via a trial-encode subprocess without linking NVIDIA's C SDK | Color / D-03 | Acceptable — use family table instead as primary, trial-encode as secondary cross-check. |
| A5 | NvFBC SDK 13 exposes a 10-bit surface format flag the C helper can pass | Color / VIDEO-09 | Fall back to mss for 10-bit (slower, no hardware assist) with DEGRADED badge. |
| A6 | Wacom real-hardware Intuos Pro Large product ID and HID descriptor shape align with `linuxwacom/wacom-hid-descriptors` repo | Pen Injection / D-07 | Spike surfaces the real descriptor; not blocking. |
| A7 | `sqlite3` read-only open on `~/Library/Application Support/com.apple.TCC/TCC.db` works without Full Disk Access entitlement | Pen Injection / D-20 | TCC DB may require FDA to read. If so, D-20 falls back to `tccutil` command or prompts the user to check manually. Document as a v1 limitation if blocking. |
| A8 | Qt 6.10's `qsb` shader-baker produces Metal-compatible `.qsb` on macOS builds (not just Vulkan) | Color / D-02 | Shipped with PySide6 6.10 [CITED: doc.qt.io]; verify at Wave 0. If absent, bake via `qsb` CLI directly. |
| A9 | Caps Lock / Num Lock state injection via `XkbSetNamedIndicator` works under Xvfb | Hotkey / D-14 | Alternative: emit `KEY_CAPSLOCK` press+release pair to sync. Less clean but works. |
| A10 | Phase 1's `InputInjector.reset_modifiers()` covers all modifier keys on both platforms (no missing mapping) | Hotkey / D-11 | Extend the reset set in Phase 2 if missing; covered by D-12 exhaustive keymap tests. |
| A11 | PyAV 17.x built against FFmpeg 7.1+ exposes P010 frames without an automatic RGB coercion on `to_ndarray()` | Color / D-01 checkpoint 5 | Call `av.VideoFrame.reformat(av.video.format.VideoFormat('yuv420p10le'))` explicitly instead of `to_ndarray`; asserts live against `frame.format.name`. |

**If this table is empty:** Not empty. A1 (IOHIDUserDevice) and A2 (VT LowLatency+HEVC on Apple Silicon) are the two assumptions whose falsification could force the largest re-scope. Both are bounded by explicit CONTEXT.md fallback paths (D-07 and D-04 respectively).

---

## Open Questions for the Planner

Each question ties to a Claude's-Discretion bullet in CONTEXT.md. The planner should lock these choices in each plan's Dimension 1 (Domain) or Dimension 3 (Interfaces).

1. **Metal shader source (D-02 / Claude's Discretion #1).** Recommended: BT.709 video-range matrix (code provided above). Alternative considered: BT.2020 — rejected because HDR tone-mapping is OUT OF SCOPE per D-06. Planner should lock BT.709 and cite ITU-R BT.709 / swscale source.

2. **`KEY_RESET_MODIFIERS` / `TextCommit` / `PenProximity` wire shape (D-11 / D-15 / D-19 / Claude's Discretion #2).** Recommended dataclasses provided above (KeyResetModifiersMsg with `reason` enum, TextCommitMsg with `text: str`, PenProximityMsg with `in_proximity` + `pen_type`). All on control channel (WebSocket JSON). Planner should lock field names + MsgType constants in the 02-01 plan (message-layer additions).

3. **`ENCODER_DEFS` capability flags (Claude's Discretion #3).** Recommended extensions to `HWEncoder` dataclass (`supports_main10`, `supports_422`, `supports_444`, `main10_detection`) with family-seeded values provided above. Planner should lock in the encoder-capability plan.

4. **`mac_video_encoder.py` internal structure (D-04 / Claude's Discretion #4).** Recommended: VT callback bridges to asyncio via `loop.call_soon_threadsafe`; no dedicated CFRunLoop thread (PyObjC handles it). Mirror Phase 1's `mac_screen_capture.py` pattern. Planner should lock the threading model in the D-04 plan.

5. **10-bit ramp fixture location (Claude's Discretion #5).** Recommended: `tests/smoke/fixtures/10bit_ramp.p010.bin` + `tools/gen_10bit_ramp.py` + `tests/smoke/fixtures/10bit_ramp.reference.ffprobe.json`. Planner locks in the D-01 plan.

6. **Pen proximity FSM shape (D-19 / Claude's Discretion #6).** Recommended: separate `PenFSM` in `common/session_fsm.py` with two states + idempotent transitions (not extending SessionFSM). Planner locks in the D-19 plan.

7. **`tools/wacom_quant_analysis.py` module shape (D-18 / Claude's Discretion #7).** Recommended: single-file <200 lines, argparse CLI, scipy/matplotlib deps, exit code 0/1 based on RMS<1% threshold. Planner locks in the D-18 plan.

8. **Per-checkpoint CI fixture granularity (D-01).** Open: should all 9 checkpoints live in one test (`test_9_checkpoints`) or nine separate tests (`test_checkpoint_1_capture`, `test_checkpoint_2_encoder_input`, …)? Recommended: **nine separate tests + one integration that runs all 9**. Per-checkpoint failure messages are easier to debug. Planner to lock.

9. **QRhiWidget vs `QOpenGLWidget` default (D-02).** Recommended: ship QRhiWidget as primary; keep QOpenGLWidget escape-hatch as an opt-in runtime flag `--legacy-gl-blit` for the rare case a macOS version's Metal QRhi path regresses. Planner to lock scope of the escape-hatch work.

10. **9-checkpoint fixture in both integration test AND nightly smoke (D-01 + Phase 1 D-18 harness)?** Recommended: YES — the nightly 1-hour smoke should include one pass through the 9-checkpoint fixture to catch slow drift. Costs ~5 seconds of the 1-hour harness. Planner to lock.

---

## Sources

### Primary (HIGH confidence — direct vendor documentation)

- **Apple WWDC24 session 10088** — "Capture HDR content with ScreenCaptureKit" — [CITED: https://developer.apple.com/videos/play/wwdc2024/10088/] — drives D-01 checkpoint 1 (SCK `hdrLocalDisplay` + `420YpCbCr10BiPlanarVideoRange`)
- **Apple WWDC21 session 10158** — "Explore low-latency video encoding with VideoToolbox" — [CITED: https://developer.apple.com/videos/play/wwdc2021/10158/] — drives D-04 VT wrapper design
- **Apple docs — `kVTVideoEncoderSpecification_EnableLowLatencyRateControl`** — [CITED: https://developer.apple.com/documentation/videotoolbox/kvtvideoencoderspecification_enablelowlatencyratecontrol] — confirms flag semantics; HEVC on Apple Silicon support confirmed
- **Apple VT API collection** — [CITED: https://developer.apple.com/documentation/videotoolbox/vtcompressionsession-api-collection]
- **Apple IOKit docs** — [CITED: https://developer.apple.com/documentation/iokit] — general reference for IOHIDUserDevice APIs
- **Apple CoreVideo docs — `kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange`** — [CITED: https://developer.apple.com/documentation/corevideo/kcvpixelformattype_420ypcbcr10biplanarvideorange]
- **NVIDIA Video Codec SDK 13.0 blog (Blackwell)** — [CITED: https://developer.nvidia.com/blog/nvidia-video-codec-sdk-13-0-powered-by-nvidia-blackwell/] — drives D-03 probe + D-05 chroma policy
- **NVIDIA NVENC Application Note (VCSDK 13)** — [CITED: https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-application-note/] — capability matrix
- **NVENC Encoder API Programming Guide** — [CITED: https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-video-encoder-api-prog-guide/] — `NvEncGetEncodeCaps`
- **Qt 6.10 QRhiWidget docs** — [CITED: https://doc.qt.io/qt-6/qrhiwidget.html]
- **Qt 6.10 QRhi Class docs** — [CITED: https://doc.qt.io/qt-6/qrhi.html]
- **Qt Simple RHI Widget Example** — [CITED: https://doc.qt.io/qt-6/qtwidgets-rhi-simplerhiwidget-example.html]
- **PySide6 QRhiWidget** — [CITED: https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QRhiWidget.html]
- **Autodesk Flame 2026 Wayland unsupported** — [CITED: https://www.autodesk.com/support/technical/article/caas/tsarticles/ts/3t2VQSfCGLLvGEwb2lPn44.html] — Rocky 9 / X11 lock

### Secondary (MEDIUM confidence — verified against multiple sources)

- **FFmpeg-devel 2025-07 patch — videotoolboxenc HEVC low-latency on Apple Silicon** — [CITED: https://www.mail-archive.com/ffmpeg-devel@ffmpeg.org/msg185545.html] — confirms Apple Silicon + HEVC + low-latency mode is real and shipping in FFmpeg master
- **OBS forum — NVENC 10-bit silent fallback** — [CITED: https://obsproject.com/forum/threads/nvenc-error-cannot-perform-10-bit-encode-on-this-encoder.165772/] — motivates D-03 refuse-and-overlay design
- **iina #5796 — VideoToolbox P010 software decode fallback** — [CITED: https://github.com/iina/iina/issues/5796] — motivates D-01 checkpoint 6
- **linuxwacom/wacom-hid-descriptors** — [CITED: https://github.com/linuxwacom/wacom-hid-descriptors] — real-device HID descriptors for Intuos + Cintiq
- **foohid — userspace virtual HID on macOS** — [CITED: https://github.com/unbit/foohid] — reference pattern (KEXT-based, but informs the IOHIDUserDevice userspace path)
- **PyObjC project** — [CITED: https://github.com/ronaldoussoren/pyobjc] — Python binding maintenance
- **PyObjC framework wrappers notes** — [CITED: https://pyobjc.readthedocs.io/en/latest/notes/framework-wrappers.html]
- **Phase 1 SUMMARY.md + VERIFICATION.md** — internal; authoritative for Phase-2 dependencies.
- **`.planning/research/STACK.md`** — Phase 1 settled stack decisions; Phase 2 builds on them.
- **`.planning/research/PITFALLS.md` §2/§3/§4** — direct response targets for D-01..D-05 / D-07..D-09 / D-10..D-15.

### Tertiary (LOW confidence — single source, marked for validation)

- **isamert.net 2022-08 "Typing unicode programmatically on Linux and macOS"** — [CITED: https://isamert.net/2022/08/12/typing-unicode-characters-programmatically-on-linux-and-macos.html] — secondary source for D-15 Unicode injection strategy; cross-verified against Apple CGEventKeyboardSetUnicodeString docs
- **xdotool GitHub** — [CITED: https://github.com/jordansissel/xdotool] — `type` subcommand Unicode handling
- **Wacom support — Sonoma driver** — [CITED: https://support.wacom.com/hc/en-us/articles/17629089706391-Is-there-a-driver-for-macOS-14-Sonoma]
- **Wacom support — Sequoia driver** — [CITED: https://support.wacom.com/hc/en-us/articles/25915509197335-Does-Wacom-have-a-driver-for-macOS-15-Sequoia]
- **wacom-qemu project** — [CITED: https://github.com/thenickdude/wacom-qemu] — virtual Wacom HID for VMs; pattern inspiration for D-07 spike
- **HN discussion on QRhi vs pygfx** — [CITED: https://news.ycombinator.com/item?id=41954410] — community signal on QRhi maturity (Oct 2024)

---

## Metadata

**Confidence breakdown:**
- Standard stack: **HIGH** — Phase 1 settled; Phase 2 adds three PyObjC bindings and the QRhiWidget exposure (stable APIs).
- Architecture: **HIGH** — CONTEXT.md's 21 decisions are already architectural; research confirms they are internally consistent.
- Pitfalls: **HIGH** on the 9 downgrade checkpoints (direct source citations, PITFALLS.md cross-reference); **MEDIUM** on IOHIDUserDevice TCC/signing behavior (bounded by D-07/D-08 spike + fallback).
- 10-bit pipeline wire shape: **HIGH** — every format name + API call cited.
- QRhiWidget Metal P010 path: **MEDIUM** — correct direction per Qt docs; specific PySide6 + Metal + YUV two-plane pattern needs spike-level verification early in Wave 3. Escape hatch (QOpenGLWidget) documented.
- VTCompressionSession HEVC + low-latency on Apple Silicon: **HIGH** — Apple docs + FFmpeg 2025-07 patch confirm the combination works; D-03 probe catches the edge cases.
- IOHIDUserDevice practical TCC: **MEDIUM** — community pattern proven for VMs; userspace userland-to-userland pressure round-trip has fewer shipping examples. D-07/D-08 is the 2-day bounded de-risk.
- Wacom real-hardware matrix: **HIGH** — DXS lab + documented 6-step protocol + video + RMS<1% gate is unambiguous.
- Keymap exhaustive test: **HIGH** — Phase 1 stub + known-good Mac virtual-key constants from Apple + table-driven Phase 1 `test_messages.py` precedent.
- Latency regression check: **HIGH** — Phase 1 gate stays; DXS one-shot is bounded by D-21 tolerances.

**Research date:** 2026-04-18
**Valid until:** 2026-06-18 (2 months; fast-moving PyObjC / Qt releases; revalidate VTCompressionSession low-latency-on-HEVC support for any new Apple Silicon generations that ship between research date and Phase 2 execution).

---

*Research performed 2026-04-18 by gsd-researcher. All 21 CONTEXT.md decisions carried verbatim; no alternatives re-litigated.*
