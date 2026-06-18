# Stack Research — Teraguchi (Remote Workstation for VFX)

**Domain:** High-performance remote desktop / remote workstation for VFX & creative workflows (Autodesk Flame, Nuke, Resolve, Houdini) — PCoIP/HP Anyware replacement for small independent studios.
**Researched:** 2026-04-18
**Overall confidence:** MEDIUM-HIGH. Version numbers and encoder/decoder capability claims verified against vendor docs and PyPI; latency targets and some protocol internals (Parsec BUD, Sunshine's NVENC bypass) verified via secondary sources rather than vendor documentation — flagged inline.

> This document prescribes a v1 stack. Where the existing Teraguchi prototype is already right, it says "keep it." Where it needs to change for production, it calls it out explicitly. It does **not** re-confirm what `.planning/codebase/STACK.md` already documents.

---

## TL;DR — v1 stack recommendation

| Layer | Keep (from prototype) | Change for v1 | Revisit post-v1 |
|---|---|---|---|
| Language/runtime | Python 3.12+ with asyncio | Drop ≤3.11 support; pin 3.12 as floor (PEP 703 / faster CPython, ARM64 wheels, PySide6 6.10) | Python 3.13 free-threaded build once PySide6 supports it |
| Client UI | PySide6 6.10 | Add `QRhiWidget` Metal path for viewer repaint (replaces `QWidget.update()` of `QImage`) | Swift+Metal native client only if Qt repaint costs show up in profiling |
| Video decode (client) | PyAV 17.x | Build PyAV against system FFmpeg 7.x with VideoToolbox + CUDA enabled; add 10-bit Main10 decode path (currently PyAV defaults to 8-bit) | `AVSampleBufferDisplayLayer` Swift shim only if VTDecompressionSession→QImage proves lossy |
| Video encode (server Linux) | FFmpeg subprocess | Stay on FFmpeg subprocess; pin **FFmpeg 7.1.1+** for Blackwell 4:2:2 support; expose an encoder-capability matrix (codec × chroma × bits) to the client at handshake | Direct NVENC API (via `nvenc-python` or pybind11 wrapper) only if FFmpeg subprocess IPC shows up as >2ms in frame-timing traces |
| Video encode (server macOS) | FFmpeg via `h264_videotoolbox`/`hevc_videotoolbox` | Switch macOS encode to **direct VideoToolbox via PyObjC** (VTCompressionSession low-latency mode) — FFmpeg's videotoolbox wrapper does not expose the low-latency mode flag | — |
| Transport | WebSocket control + UDP media + experimental QUIC | Promote **QUIC (aioquic 1.3.0) to primary** for WAN/Tailscale; keep WebSocket as TLS control channel for LAN/compat; retire the bespoke UDP path post-v1 | — |
| Jitter buffer / FEC | Custom `common/jitter_buffer.py` | Add Reed–Solomon FEC (30–50% overhead configurable) via `zfec` or roll-your-own. Current buffer is reorder-only — no FEC, no retransmit | RTP/RTCP if we ever want WebRTC interop |
| Input injection (Linux) | uinput + XTest, X11 assumption | **KEEP as v1 target** — Flame 2026 is explicitly X11/Xorg only. Add `libei` as a capability flag for future Wayland sessions (GNOME Remote Desktop, KDE Plasma RDP targets) | Wayland-only path post-Flame-port |
| Input injection (macOS) | CoreGraphics `CGEventPost` | KEEP for mouse/keyboard; add **IOHIDUserDevice path for tablet pressure** — CoreGraphics does not expose `NSEvent.pressure` injection cleanly | HIDDriverKit signed virtual device only if studios need pressure on sandboxed apps |
| Audio | PulseAudio monitor / AVFoundation, playback via `QAudioSink` | Switch Linux to **PipeWire** detection with PulseAudio fallback (Rocky 9 still ships PulseAudio, Fedora/Rocky 10 ship PipeWire); use **Opus** codec explicitly (currently raw/unspecified) | JACK path if a studio asks |
| Clipboard (Linux) | `xclip`/`xsel` subprocess | Replace `subprocess` with `python-xlib`/`wl-clipboard` direct bindings for lower overhead and Wayland-future-proofing | — |
| Screen capture (Linux NVIDIA) | NvFBC via C helper subprocess | KEEP. Author's DXS fleet is NVIDIA + Rocky 9 + X11 — NvFBC is still the lowest-latency zero-copy path. Flag: NvFBC does not work under Wayland/XWayland; document clearly. | PipeWire portal capture when Flame ports to Wayland |
| Screen capture (macOS) | ScreenCaptureKit via PyObjC | Add **10-bit HDR capture** via `SCStreamConfiguration.captureDynamicRange = .hdrLocalDisplay` + `pixelFormat = kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange` (WWDC24, macOS 14.4+) | — |
| Packaging (macOS) | PyInstaller onedir | KEEP PyInstaller. Add `--codesign-identity` + hardened-runtime entitlements + notarytool via GitHub Actions. `briefcase` is not worth the churn for an already-working PyInstaller bundle. | — |
| Packaging (Linux) | `install-server.sh` into `/opt/teraguchi` | Add **signed RPM build** via `rpmbuild` in GHA, hosted via GitHub Releases; keep the install script for airgapped installs | COPR/OBS repo only if we want `dnf install teraguchi-server` |
| Observability | `logging` + `/status` JSON + health overlay | Add **structured JSON logging** (`structlog`), `/metrics` Prometheus endpoint, optional Sentry SDK for crash reports | OpenTelemetry traces once multi-node broker scenarios matter |
| CI | none | **GitHub Actions** matrix: `macos-14` (Apple Silicon signing), `rocky-9` container (RPM build), lint/type/test on all PRs | — |

---

## Core Technologies

### Languages & Runtime

| Technology | Version | Purpose | Why |
|---|---|---|---|
| **Python** | **3.12** (floor) / 3.13 (target) | All three tiers | PEP 703 free-threaded and PEP 668 are non-issues for us. 3.12 gets substantially better asyncio performance, fixes `sys.monitoring`, and is the first Python with first-class Apple Silicon universal2 wheels across every dep we need. 3.10 (current floor) is EOL in Oct 2026; pinning to 3.12 now means we don't have to bump a security-critical boundary mid-v1. Existing prototype runs on 3.10–3.13; dropping 3.10 and 3.11 removes two wheel matrices from CI. Confidence: **HIGH**. |
| **CPython** | CPython only | — | PyPy and Nuitka are off the table because PyAV, PySide6, PyObjC all depend on CPython ABI. GraalPython likewise. Confidence: **HIGH**. |
| **C** | C17 | `nvfbc_capture` helper only | Already isolated in `server/nvfbc/`. No need to expand. Confidence: **HIGH**. |

### Client GUI Framework

| Technology | Version | Purpose | Why |
|---|---|---|---|
| **PySide6** | **6.10.x** (min 6.8) | Qt6 GUI, input capture, tablet events | 6.10 released Oct 2025 with Qt 6.10 features, including refined `QRhiWidget` for Metal/Vulkan/D3D12 painting. `QTabletEvent` is the only toolkit that exposes full Wacom pressure+tilt+rotation in a cross-platform way (AppKit's `NSEvent.pressure` is Mac-only; GTK's event model is buggy on macOS). The existing prototype is correct — do not churn. Confidence: **HIGH**. |
| `QRhiWidget` | (bundled with PySide6 6.8+) | Metal-backed paint surface for viewer | Current `RemoteViewer` calls `QWidget.update()` with a `QImage` — that's a CPU→CPU path with a software blit. `QRhiWidget` exposes a Metal/Vulkan/D3D12 texture surface. For a 4K60 viewer, this removes ~2–4ms of CPU copy on every frame. Confidence: **MEDIUM** (we haven't profiled the current path). |

**Not considered / rejected for client UI:**
- **Swift+Metal native client** — rewrite cost is massive; we'd lose Windows/Linux client as a free side-effect of PySide6 portability. Only consider if QRhiWidget's Metal path is still a perf bottleneck. Confidence this is the right call: **HIGH**.
- **Tauri/Flutter** — Both assume a webview or Skia canvas that is nowhere near the latency of a direct GPU-texture path. And both would require a rewrite of `common/protocol.py`. Reject. Confidence: **HIGH**.
- **Electron** — not even in the conversation.

### Video Decode (Client, macOS)

| Technology | Version | Purpose | Why |
|---|---|---|---|
| **PyAV** | **17.0.1** | H.264/HEVC/AV1 decode, hardware via VideoToolbox | 17.0.1 released 2026-04-18 (same day as this research). 16.1 added Intel QSV + AMD AMF hardware decode. 17.0 improved CUVID memory handling (export via `dlpack`). Supports VideoToolbox hwaccel for H.264 and HEVC Main10 on Apple Silicon. Confidence: **HIGH**. |
| **FFmpeg** (linked by PyAV) | **7.1.1+** | Underlying codec library | PyAV wheels on PyPI are built against a bundled FFmpeg **without** CUDA and **without** the full VideoToolbox hwaccel chain exposed. For Teraguchi v1, build PyAV from source against Homebrew FFmpeg on macOS (which includes `videotoolbox`) and against Rocky's RPMFusion FFmpeg on Linux. Confidence: **HIGH** ([PyAV docs](https://pyav.org/docs/stable/overview/caveats.html) explicitly note this). |

**Decode path tradeoff (VTDecompressionSession vs AVSampleBufferDisplayLayer):**
- `AVSampleBufferDisplayLayer` is a higher-level, layer-based display path. Render latency is good, but you **do not get pixel buffer access** — you give it CMSampleBuffers and it displays them. That's a problem for us because the viewer wants to composite cursor overlays, health HUD, and (eventually) OCIO display transforms on top of the decoded frame. **Reject for v1.**
- `VTDecompressionSession` gives you `CVPixelBuffer` output which we then hand to Metal (via `QRhiWidget`) or `QImage`. Lower-level, slightly more latency than SBDL (one more copy), but gives us the compositing control we need. **This is what PyAV+VideoToolbox already does under the hood.** Keep it.
- Only revisit if a native Swift viewer gets greenlit — then `AVSampleBufferDisplayLayer` with a Metal compositor on top becomes reasonable.

Confidence on this tradeoff: **MEDIUM** — based on [Apple VT docs](https://developer.apple.com/documentation/videotoolbox) and community comparisons; not independently benchmarked in our pipeline.

### Video Encode (Server, Linux + NVIDIA)

| Technology | Version | Purpose | Why |
|---|---|---|---|
| **FFmpeg** | **7.1.1+** | Encode pipeline subprocess | 7.1 added Blackwell GPU optimizations, AV1 improvements, and better NVENC `-preset p1`..`p7` passthrough. The existing subprocess-pipe approach is fine for v1. Direct NVENC API via pybind11 is a post-v1 optimization. Confidence: **HIGH**. |
| `h264_nvenc` / `hevc_nvenc` / `av1_nvenc` | FFmpeg 7.1+ | Hardware encoder backends | Per NVIDIA [Video Codec SDK 13.0 blog](https://developer.nvidia.com/blog/nvidia-video-codec-sdk-13-0-powered-by-nvidia-blackwell/): Blackwell adds 4:2:2 and H.264 10-bit; Ada adds AV1 10-bit; Ampere and later do HEVC Main10 4:2:0 + 4:4:4. Confidence: **HIGH**. |
| `libx264` / `libx265` / `libsvtav1` | FFmpeg 7.1+ | Software fallback | SVT-AV1 is the right AV1 software encoder (faster than libaom, aligned with Blackwell's AV1 path). Keep software fallback for smoke tests and CPU-only Intel/AMD boxes without Arc discrete. Confidence: **HIGH**. |

**NVENC hardware encoder coverage matrix (2026):**

| GPU Gen | NVENC Gen | Cards | H.264 8b | H.264 10b | HEVC 8b 4:2:0 | HEVC Main10 4:2:0 | HEVC 4:4:4 | HEVC 4:2:2 | AV1 8b 4:2:0 | AV1 10b 4:2:0 | AV1 4:2:2 | Low-latency preset |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Turing | 7th (NVENC) | RTX 20x0, T-series | ✓ | — | ✓ | ✓ | ✓ | — | — | — | — | ✓ (`p1`..`p4`) |
| Ampere | 7th | RTX 30x0, A-series | ✓ | — | ✓ | ✓ | ✓ | — | — | — | — | ✓ |
| Ada Lovelace | 8th | RTX 40x0, L-series | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | — | ✓ |
| Blackwell | 9th | RTX 50x0, GB202+ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

**Implications for v1:**
- Author's DXS fleet (`dxs-flame-01..06`) is mixed Turing/Ampere → **HEVC Main10 4:2:0 is the v1 color-accurate baseline**. 4:4:4 is available in hardware but bandwidth-expensive and software-decode-only on older Macs.
- Blackwell 4:2:2 is a future-proofing story — for grading work, 4:2:2 Main10 is the industry-correct chroma for most broadcast content. Expose it at handshake when both ends support it.
- AV1 is a nice-to-have for bandwidth-constrained WAN; not the primary v1 codec.

**Frame latency budget per encoder (empirical industry numbers, LOW-MEDIUM confidence — needs own benchmarks):**

| Path | Typical encode latency (1 frame) | Notes |
|---|---|---|
| NVENC P1 low-latency (Turing+) | 3–6 ms @ 1080p60 | What Parsec, Sunshine, GeForce NOW all use |
| NVENC P1 + HEVC 10-bit | 4–8 ms | ~1–2ms penalty vs 8-bit |
| NVENC P1 + AV1 | 5–10 ms | +2–3 frames reference latency over HEVC per arxiv.org/abs/2511.18687 |
| VAAPI (Intel QSV Arc) | 6–12 ms | Acceptable for Intel Xe workstations |
| VideoToolbox `kVTEncodeInfoFlag_Asynchronous` low-latency | 4–8 ms on M2+ | H.264-only in low-latency mode (Apple restriction); HEVC in normal mode adds ~5ms |
| VideoToolbox HEVC (normal mode) | 10–15 ms | Acceptable for LAN; not ideal for WAN |
| libx264 `-preset ultrafast -tune zerolatency` | 8–20 ms (CPU-dependent) | Software fallback only |
| libsvtav1 low-latency preset 11–13 | 10–18 ms | Software fallback |

Sources: NVIDIA Video Codec SDK 13 docs, Apple WWDC21 session 10158, Parsec BUD blog ("~7ms on LAN"), OBS NVENC tuning guide.

**VideoToolbox encoder direct API (macOS server):**
FFmpeg's `h264_videotoolbox` / `hevc_videotoolbox` **do not expose low-latency mode** (the WWDC21 `kVTVideoEncoderSpecification_EnableLowLatencyRateControl` flag). For macOS server production-grade v1, **write a small PyObjC wrapper around VTCompressionSession** with:
- `kVTVideoEncoderSpecification_EnableLowLatencyRateControl = True`
- One-in-one-out, no frame reordering
- H.264 or HEVC Main10 via `kVTProfileLevel_HEVC_Main10_AutoLevel`
- 4:2:2 via `kVTProfileLevel_HEVC_Main42210_AutoLevel` on M3+ (Apple Silicon)

Rationale: this is the one place in v1 where bypassing FFmpeg pays measurable latency dividends (5–8ms saved per frame per WWDC21 session). Confidence: **MEDIUM-HIGH**.

### Video Encode (Server, macOS)

| Technology | Version | Purpose | Why |
|---|---|---|---|
| **VideoToolbox** (direct via PyObjC) | macOS 12.3+ | H.264/HEVC encode | See above. Low-latency mode is H.264-only; use normal mode HEVC Main10 for grading work with ~5ms extra budget. Confidence: **MEDIUM-HIGH**. |
| **PyObjC framework bindings** | 10.x | Python → VideoToolbox glue | Already in the prototype (`pyobjc-framework-AVFoundation`, `-CoreMedia`). Add `pyobjc-framework-VideoToolbox` explicitly. Confidence: **HIGH**. |

**macOS hardware encoder coverage:**

| Silicon | H.264 | HEVC 8b | HEVC Main10 | HEVC 4:2:2 | AV1 | Low-latency mode |
|---|---|---|---|---|---|---|
| Intel Mac (T2 / AMD dGPU) | ✓ | ✓ | partial | — | — | ✓ (H.264) |
| M1 / M1 Pro/Max | ✓ | ✓ | ✓ | — | — | ✓ (H.264) |
| M2 / M2 Pro/Max | ✓ | ✓ | ✓ | — | — | ✓ (H.264) |
| M3 / M3 Pro/Max | ✓ | ✓ | ✓ | ✓ (HW decode) | — | ✓ (H.264) |
| M4 / M4 Pro/Max | ✓ | ✓ | ✓ | ✓ (HW encode+decode per reports) | — | ✓ (H.264) |

Confidence: **MEDIUM** — M4 4:2:2 hardware encode is reported but not yet exhaustively vendor-documented as I can cite.

### Transport / Networking

| Technology | Version | Purpose | Why |
|---|---|---|---|
| **websockets** | **15.x** | TLS control channel (`wss://`) | Keep. Standard, reliable, well-tested. 15.x has streaming-message improvements. Confidence: **HIGH**. |
| **aioquic** | **1.3.0** | QUIC media + control, primary WAN transport | 1.3.0 is the current stable (2026). The QUIC path in the prototype is experimental — **promote to v1 primary** for Tailscale/WAN deployments. Rationale: NICE DCV adopted QUIC in 2020.2 and made it the default in 2024; it's the production pattern. Also: 0-RTT reconnect, connection migration (Wi-Fi↔cell), single-port TLS 1.3, datagram support (RFC 9221). Confidence: **MEDIUM-HIGH** — aioquic is Python-only and won't match msquic/quiche throughput, but our frame budget is 20 Mbps, not 500. |
| **UDP media channel** (custom) | — | Legacy / LAN | Keep through v1 as fallback for networks where QUIC is blocked; deprecate in v1.1. Confidence: **HIGH**. |
| **DTLS** | (via aioquic's TLS 1.3 stack or OpenSSL) | UDP encryption when QUIC isn't used | The prototype mentions DTLS is referenced but experimental — finish it or retire the bare-UDP path in favor of QUIC. Confidence: **MEDIUM**. |

**Transport decision matrix:**

| Scenario | Primary | Fallback | Why |
|---|---|---|---|
| LAN, same subnet | bare UDP media + WS control | TCP-only over WS | Lowest possible overhead; no QUIC handshake penalty |
| Tailscale WAN | **QUIC** (aioquic) | WS over TCP | Single port, 0-RTT, connection migration when artist moves laptop |
| Public internet | **QUIC** | WS over TCP | QUIC handles packet loss gracefully; TCP head-of-line blocking kills interactive latency |
| Corporate firewall (QUIC/UDP blocked) | WS over TCP only | — | Graceful degradation with ~30ms extra latency |

**Rejected transport alternatives:**
- **WebRTC (DataChannel + RTP)** — overkill for client-server (WebRTC is P2P-first, bringing ICE/STUN/TURN complexity we don't need given Tailscale). PeerConnection setup latency (~500ms) is unacceptable for a remote-desktop session. Parsec's engineers explicitly rejected it for the same reason ([Parsec BUD blog](https://parsec.app/blog/a-networking-protocol-built-for-the-lowest-latency-interactive-game-streaming-1fd5a03a6007)). Reject. Confidence: **HIGH**.
- **WebTransport** — exciting, but server-side Python support is nascent and it's a superset of QUIC anyway. Revisit if we ever ship a browser client (we're not in v1). Reject. Confidence: **HIGH**.
- **SRT / RIST** — great for broadcast contribution (one-way video), wrong shape for interactive remote desktop. No bidirectional input support. Reject. Confidence: **HIGH**.
- **Custom BUD-like UDP protocol** (à la Parsec) — Parsec invested engineer-years; we have one maintainer. QUIC is good enough and gives us TLS 1.3 + migration for free. Reject. Confidence: **HIGH**.

### Input Injection

**Linux (server):**

| Technology | Version | Purpose | Why |
|---|---|---|---|
| **uinput** (kernel) | Linux 5.x+ | Virtual HID device for mouse, keyboard, pen | KEEP. Already in prototype. Mature, well-documented, works cleanly with Xorg. Confidence: **HIGH**. |
| **python-xlib** (XTest) | 0.33+ | X11 injection for Xvfb/Xorg sessions | KEEP. XTest is the right tool for virtual-display sessions where uinput devices aren't grabbed. Confidence: **HIGH**. |
| **libei** (1.x via `python-libei` or subprocess) | (future) | Wayland RemoteDesktop portal injection | ADD as a capability-flagged path for Rocky 10 / Fedora / Flame-post-port. GNOME Remote Desktop (RDP target on Rocky 10) uses libei under the hood. For v1, treat libei as reconnaissance — document the code path but don't ship it as a required dep. Confidence: **HIGH** on direction; **MEDIUM** on when it becomes load-bearing. |

**Critical fact:** [Autodesk Flame 2026 explicitly does not support Wayland](https://www.autodesk.com/support/technical/article/caas/tsarticles/ts/3t2VQSfCGLLvGEwb2lPn44.html). Flame requires X11/Xorg. This means:
- The v1 server target is **Rocky Linux 9.3/9.5 with X11/Xorg** — exactly what the prototype assumes.
- Rocky Linux 10 is Wayland-default and drops Xorg entirely ([Rocky 10 docs](https://docs.rockylinux.org/10/desktop/gnome/rdp-server/)). **Rocky 10 is NOT a v1 target** until Autodesk ports Flame.
- The xorg-teraguchi.conf + NVIDIA `ConnectedMonitor` approach is the right production setup.

**macOS (server):**

| Technology | Version | Purpose | Why |
|---|---|---|---|
| **CoreGraphics `CGEventPost`** | macOS 12+ | Mouse, keyboard, scroll | KEEP. Requires Accessibility + Input Monitoring TCC. Already in prototype. Confidence: **HIGH**. |
| **IOHIDUserDevice** (via PyObjC `IOKit`) | macOS 12+ | Virtual HID device for **tablet pressure** | ADD. `CGEventPost` does not cleanly inject `NSEvent.pressure` — the events fire but apps that read `[NSEvent pressure]` directly (Photoshop, some Flame tools) don't see the values. A virtual HID tablet device via IOHIDUserDevice exposes pressure as a real digitizer report, which apps honor. [Wacom-qemu](https://github.com/thenickdude/wacom-qemu) does this for VMs; we adapt the pattern for userspace. Confidence: **MEDIUM-HIGH** — community-documented pattern, not Apple-blessed. |
| **HIDDriverKit** (via separate signed system extension) | macOS 12+ | Sandbox-safe virtual HID | DEFER to post-v1. Requires a DriverKit entitlement from Apple (`com.apple.developer.driverkit.family.hid.virtual.device`) — not available on default Developer ID accounts; needs special Apple approval. Karabiner-DriverKit-VirtualHIDDevice is the reference implementation. Only needed if we ship as a Mac App Store app or sandboxed tool. Confidence: **HIGH** on direction. |

### Audio

| Technology | Version | Purpose | Why |
|---|---|---|---|
| **Opus** (via `libopus` in FFmpeg) | 1.4+ | Audio codec, both directions | Opus is the WebRTC-mandated codec and the de-facto real-time audio standard. 26.5ms algorithmic delay by default, tunable to <10ms for LAN. The prototype passes audio through FFmpeg with whatever codec — lock it to Opus explicitly. Confidence: **HIGH**. |
| **PipeWire** (via `pactl` compat layer or direct) | 1.4.x | Audio capture on modern Linux | Rocky 9 ships PulseAudio; Fedora 38+ and Rocky 10 ship PipeWire with PulseAudio compatibility socket. Existing `pactl` subprocess path works against both. For lower latency, consider direct PipeWire client via `python-pipewire` (alpha) or GStreamer's `pipewiresrc` post-v1. Confidence: **HIGH** on compat path. |
| **PulseAudio** monitor source | 16+ | Capture output on Rocky 9 | KEEP fallback. Confidence: **HIGH**. |
| **AVFoundation** audio capture | macOS 12+ | macOS server audio | KEEP. Requires ScreenCaptureKit audio path for system audio (TCC: "Screen & System Audio Recording"). Confidence: **HIGH**. |
| **QAudioSink** (Qt Multimedia) | PySide6 6.10 | Client audio playback | KEEP. Works well enough on all three client OSes. Confidence: **HIGH**. |
| **QAudioSource** (Qt Multimedia) | PySide6 6.10 | Client mic capture | ADD. Current prototype marks mic-capture as v1 target but not yet implemented. Confidence: **HIGH**. |

**Target mouth-to-ear latency (Opus @ 10ms frames, LAN):**
- Encode: 10ms (Opus frame)
- Network: 1–5ms (LAN) / 20–60ms (Tailscale WAN)
- Jitter buffer: 20–40ms (adaptive)
- Decode + playback: 5–10ms
- **Total LAN: 40–70ms. Total WAN: 60–120ms.**
- Confidence: **MEDIUM** — based on Opus+WebRTC industry numbers; needs our own measurement.

**Rejected:**
- **JACK** — fantastic for production audio; massive overhead for "artist talks to another artist through a screen" use case. Defer unless asked. Reject for v1.
- **Raw PCM over UDP** — wasteful bandwidth-wise, no quality benefit over Opus at 128+ kbps. Reject.

### Screen Capture

**Linux:**

| Technology | Purpose | Status | Notes |
|---|---|---|---|
| **NvFBC** (via `libnvidia-fbc.so.1` + C helper) | NVIDIA zero-copy | KEEP — primary for v1 | The fastest capture path on NVIDIA Linux. Currently isolated in a subprocess for crash safety. Works on X11/Xorg only. Does NOT work on Wayland/XWayland. |
| **mss** (mss Python lib) | Fallback capture via XShm | KEEP — fallback | Works on any X11. ~5–10ms capture latency. |
| **PipeWire portal capture** (via `pipewire` / `pygobject`) | Future Wayland capture | POST-V1 | Via xdg-desktop-portal. NVIDIA added a PipeWire capture backend to NvFBC recently. Only needed when Flame ports to Wayland. |
| **kmsgrab** (via FFmpeg `-f kmsgrab`) | Alternative low-level Wayland capture | NOT RECOMMENDED | Requires `CAP_SYS_ADMIN`. NVIDIA KMS driver doesn't implement `drm_auth` frame retrieval cleanly. |

**macOS:**

| Technology | Purpose | Status | Notes |
|---|---|---|---|
| **ScreenCaptureKit** (via PyObjC) | macOS 12.3+ capture | KEEP | Add HDR config: `captureDynamicRange = .hdrLocalDisplay`, `pixelFormat = kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange`. Confidence: **HIGH** per [WWDC24 session 10088](https://developer.apple.com/videos/play/wwdc2024/10088/). |

### Packaging & Distribution

**macOS client + macOS server:**

| Tool | Version | Purpose | Why |
|---|---|---|---|
| **PyInstaller** | **6.11+** | `.app` bundle, standalone runtime | KEEP. The existing prototype already builds `dist/Teraguchi.app`. Mature, handles PySide6 + PyAV + PyObjC correctly. Confidence: **HIGH**. |
| `codesign` + `notarytool` | Xcode 15+ | Apple code-signing + notarization | ADD. Requires Apple Developer ID (~$99/yr, already listed as v1 constraint). Use `--options runtime` hardened runtime + entitlements file allowing unsigned executable memory (required for CPython). See [federicoterzi.com post](https://federicoterzi.com/blog/automatic-code-signing-and-notarization-for-macos-apps-using-github-actions/) for the canonical GHA pattern. Confidence: **HIGH**. |
| `create-dmg` or `hdiutil` | 10+ | DMG installer | KEEP path — PyInstaller produces `.app`, then wrap in DMG for distribution. Confidence: **HIGH**. |

**Rejected for macOS packaging:**
- **Briefcase** (BeeWare) — lovely tool, but it's a rewrite-oriented workflow (project-file-centric), and the prototype's PyInstaller + bash script is already working. Rejecting change-for-change's-sake. Confidence: **HIGH**.
- **py2app** — predates PyInstaller as the community favorite; less actively maintained now; doesn't handle PySide6's plugin chain as cleanly. Reject. Confidence: **HIGH**.
- **Nuitka** — compiles Python to C. Good for startup time and obfuscation; **wrecks debuggability** and PyObjC/PySide6 integration is brittle. For a remote-desktop tool where crash reports are load-bearing, Nuitka would add days to debugging. Reject. Confidence: **MEDIUM-HIGH**.
- **PyOxidizer** — defunct-ish, author moved on. Reject. Confidence: **HIGH**.

**Linux server:**

| Tool | Purpose | Why |
|---|---|---|
| **rpmbuild** (in Rocky 9 container) | RPM build | Ship `teraguchi-server-1.0.0-1.el9.x86_64.rpm` via GitHub Releases. `install-server.sh` becomes a fallback for airgapped / non-RPM distros. Confidence: **HIGH**. |
| **rpm-sign** / GPG | RPM signing | Key lives in GHA secret, distributed via GitHub Releases page. Do NOT ship to RPMFusion / EPEL in v1 — too much bureaucracy for a single-maintainer tool. Confidence: **HIGH**. |
| `systemd-run-generator` / service file | Service lifecycle | Already in prototype (`teraguchi-server.service`). Keep. Confidence: **HIGH**. |

### CI/CD (GitHub Actions)

| Workflow | Runner | Purpose |
|---|---|---|
| `test.yml` | `ubuntu-22.04` + `macos-14` | pytest, ruff, mypy on every PR |
| `build-mac.yml` | `macos-14` (Apple Silicon) | PyInstaller + codesign + notarytool + DMG; triggered on `v*` tag |
| `build-rpm.yml` | `ubuntu-22.04` with `rockylinux:9` container | rpmbuild + rpm-sign; triggered on `v*` tag |
| `release.yml` | `ubuntu-22.04` | Collect `.dmg` + `.rpm`, attach to GitHub Release |

Secrets required:
- `APPLE_DEVELOPER_ID_CERT` (base64 `.p12`)
- `APPLE_DEVELOPER_ID_PASSWORD`
- `APPLE_ID` / `APPLE_TEAM_ID` / `APPLE_APP_SPECIFIC_PASSWORD` (for notarytool)
- `GPG_SIGNING_KEY` / `GPG_SIGNING_PASSPHRASE` (for RPM signing)

Confidence: **HIGH** — these are standard GHA patterns.

### Observability

| Concern | Tool | Notes |
|---|---|---|
| Logging | **`structlog`** 25.x | Structured JSON logs with `logger = structlog.get_logger()` + JSONRenderer. Backwards-compatible with stdlib `logging` — keep the existing logger names (`teraguchi.server`, `teraguchi.broker`), swap the formatter. Confidence: **HIGH**. |
| Crash reporting | **`sentry-sdk`** 3.x (optional, off by default) | Opt-in via CLI flag `--sentry-dsn`. Captures uncaught exceptions in the server and client. Small studios won't run their own Sentry; make sure it's a no-op if not configured. Confidence: **HIGH**. |
| Metrics | **`prometheus_client`** 0.22+ | Expose `/metrics` endpoint alongside `/status` via the existing `process_request` hook. Counters for sessions, frames-encoded, frames-dropped; histograms for encode-latency, capture-latency, rtt. Confidence: **HIGH**. |
| Tracing | **OpenTelemetry** (deferred to post-v1) | Only valuable when we have broker + multiple servers + a central collector. Over-engineering for v1. Confidence: **HIGH**. |

---

## Supporting Libraries

| Library | Version | Purpose | When to Use |
|---|---|---|---|
| `numpy` | **2.1+** | Raw pixel buffers, BGRA handling | Everywhere PyAV outputs frames. 2.x is ABI-stable and faster than 1.x on Apple Silicon. |
| `Pillow` | **11.0+** | JPEG dirty-rect fallback encoder | Only when NVENC/VT unavailable. Keep. |
| `python-pam` | **2.0.2+** | PAM auth on Linux server + broker | Keep as-is. |
| `python-xlib` | **0.33+** | XTest, XDamage, XFixes | Keep. Active maintenance; no real alternatives for these X11 extensions in Python. |
| `zfec` | **1.5+** | Reed-Solomon forward error correction for UDP media | ADD. FEC is critical for WAN video — 20–30% redundancy masks most loss without retransmit latency. |
| `cryptography` | **44.0+** | TLS primitives for aioquic, HMAC for broker tokens | Keep. |
| `aiohttp` | **3.11+** | Broker health probes (client side) | Keep. |
| `PyYAML` | **6.0.2+** | Broker config | Keep. |
| `pyobjc-framework-VideoToolbox` | **11.x+** | Direct VT encoder on macOS server | ADD (replaces FFmpeg-VT for low-latency). |
| `pyobjc-framework-IOKit` | **11.x+** | IOHIDUserDevice virtual tablet | ADD. |
| `structlog` | **25.1+** | Structured JSON logging | ADD. |
| `sentry-sdk` | **3.0+** | Opt-in crash reporting | ADD (optional dep). |
| `prometheus_client` | **0.22+** | `/metrics` endpoint | ADD. |
| `pytest` | **8.3+** | Tests | ADD committed test suite — none currently in the tree. |
| `pytest-asyncio` | **0.25+** | Async test support | ADD. |
| `ruff` | **0.11+** | Lint + format | ADD. Replace any existing black/flake8/isort. |
| `mypy` | **1.14+** | Type checking (target strict mode in `common/` + `client/protocol.py`) | ADD. |
| `rich` | **13.x** | CLI output formatting | Optional — makes installers friendlier. |

---

## Development Tools

| Tool | Purpose | Notes |
|---|---|---|
| `uv` 0.5+ | Package manager replacement for pip | 10–100x faster installs than pip. Drop-in for `pip install -r requirements-*.txt` via `uv pip install`. Keep `requirements-*.txt` flat-file format. Confidence: **HIGH**. |
| `pre-commit` | Local git hooks | Runs ruff + mypy on staged files. |
| `ffprobe` (FFmpeg) | Debugging encoder output | Already implied by FFmpeg dep. |
| `tcpdump` / `wireshark` | QUIC/UDP protocol debugging | Note: QUIC encryption makes live pcap inspection hard; use `SSLKEYLOGFILE` with aioquic. |
| `perf` / `py-spy` / `Instruments.app` | Profiling | py-spy for Linux/macOS CPU profiling; Instruments for macOS frame timing. |

---

## Installation

```bash
# Common (pinned for v1)
uv pip install \
    'websockets>=15.0,<16' \
    'aioquic>=1.3.0,<2' \
    'cryptography>=44.0,<46' \
    'numpy>=2.1,<3' \
    'PyYAML>=6.0.2'

# Client (macOS)
uv pip install \
    'PySide6>=6.10,<6.11' \
    'av>=17.0,<18' \
    'Pillow>=11.0,<12' \
    'structlog>=25.1'
# (rebuild PyAV against Homebrew FFmpeg with VideoToolbox: see docs/build-pyav-macos.md)

# Server (Linux, Rocky 9)
sudo dnf install -y \
    ffmpeg-free \
    python3.12 python3.12-devel \
    python3-pam \
    xorg-x11-server-Xvfb \
    pulseaudio-utils \
    xclip \
    usbip-utils
uv pip install \
    'python-pam>=2.0.2' \
    'python-xlib>=0.33' \
    'mss>=10.0' \
    'Pillow>=11.0' \
    'numpy>=2.1' \
    'prometheus_client>=0.22' \
    'structlog>=25.1' \
    'zfec>=1.5'

# Server (macOS)
brew install ffmpeg@7  # linked against videotoolbox
uv pip install \
    'pyobjc-core>=11.0' \
    'pyobjc-framework-Cocoa>=11.0' \
    'pyobjc-framework-Quartz>=11.0' \
    'pyobjc-framework-ScreenCaptureKit>=11.0' \
    'pyobjc-framework-AVFoundation>=11.0' \
    'pyobjc-framework-VideoToolbox>=11.0' \
    'pyobjc-framework-CoreMedia>=11.0' \
    'pyobjc-framework-IOKit>=11.0' \
    'structlog>=25.1' \
    'prometheus_client>=0.22'

# Broker (Linux)
uv pip install \
    'aiohttp>=3.11' \
    'python-pam>=2.0.2' \
    'python-ldap>=3.4' \
    'PyYAML>=6.0.2'

# Dev
uv pip install \
    'pytest>=8.3' \
    'pytest-asyncio>=0.25' \
    'ruff>=0.11' \
    'mypy>=1.14' \
    'pyinstaller>=6.11' \
    'pre-commit>=4.0' \
    'sentry-sdk>=3.0'
```

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|---|---|---|
| PySide6 | Swift + Metal native | If Qt repaint path shows up as bottleneck after QRhiWidget migration, or if Mac App Store distribution becomes a requirement |
| PyAV + system FFmpeg | Direct AVFoundation / VTDecompressionSession via PyObjC | If PyAV overhead measures >5ms per frame in traces (unlikely per benchmarks) |
| FFmpeg subprocess encode (Linux) | Direct NVENC API via pybind11 wrapper | If FFmpeg-stdin pipe adds >2ms, or if we need B-frame / LTR precise control |
| aioquic | msquic (C) via ctypes, or quiche (Rust) via maturin | If Python asyncio QUIC throughput caps at ~500Mbps and we need >1Gbps (unlikely for 4K60 HEVC at ~30Mbps) |
| uinput + XTest | libei (Wayland RemoteDesktop portal) | Mandatory once server targets a Wayland session (Rocky 10 + Flame port, GNOME Remote Desktop, KDE RDP) |
| PyInstaller | py2app / briefcase | Only if PyInstaller's onedir bundles become unwieldy at 400MB+ and we need Nuitka-style single-binary |
| WebSocket + aioquic hybrid | Pure WebRTC (DataChannel + RTP) | Only if a browser client becomes a v2 deliverable (rejected for v1) |
| VideoToolbox via PyObjC (macOS encode) | FFmpeg `h264_videotoolbox` subprocess | OK for LAN-only demos; unacceptable for WAN because low-latency mode isn't exposed |
| Opus | AAC-LC or AAC-LD | Only if we interop with proprietary broadcast pipelines; no reason for v1 |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|---|---|---|
| **Wayland as the server display** for v1 | Flame 2026 explicitly incompatible; Nuke and Houdini have mixed Wayland support. NvFBC doesn't work on Wayland. Rocky 9 is correct for v1; Rocky 10 is a post-v1 concern. | X11/Xorg with NVIDIA proprietary driver |
| **WebRTC DataChannel** as primary transport | 500ms+ handshake latency; ICE complexity; no advantage over QUIC for client-server topology | QUIC (aioquic) |
| **EGLStreams** for any NVIDIA Wayland work | Removed from XWayland in 2024; NVIDIA has committed to GBM | GBM (when Wayland ever matters) |
| **FFmpeg `h264_videotoolbox` / `hevc_videotoolbox`** on macOS server | Doesn't expose `kVTVideoEncoderSpecification_EnableLowLatencyRateControl`; frame latency is 2× what it should be | Direct VTCompressionSession via PyObjC |
| **libaom** for AV1 software encode | Slow (Netflix uses it for VOD, not interactive); PyAV 17 dropped it | SVT-AV1 (`libsvtav1`) |
| **xrdp / x11vnc** as the Linux server backend | Both rewrite-heavy in our pipeline; x11vnc has color-accuracy issues for grading; xrdp targets Wayland on Rocky 10 (not Flame-compatible) | Our own per-user Xvfb/Xorg + NvFBC/mss pipeline |
| **WebSockets over TCP for video on WAN** | TCP head-of-line blocking destroys interactive latency under any packet loss | QUIC or UDP media channel |
| **Nuitka** for packaging | Breaks PyObjC introspection, turns crash traces into gibberish | PyInstaller |
| **Briefcase** for packaging | Project restructuring cost with no measurable benefit over PyInstaller | PyInstaller |
| **Custom BUD-style UDP protocol** | Parsec took years to get BUD right; we have one maintainer and QUIC gets 90% of the way there for free | QUIC (aioquic) |
| **AAC audio codec** | Higher latency than Opus; licensing concerns for OSS distribution | Opus |
| **RSA-2048 for broker tokens** | We already use HMAC-SHA256 (correct). Don't "upgrade" to public-key | Keep HMAC-SHA256 |
| **PyAV's bundled FFmpeg wheels on PyPI** (for production) | No CUDA, limited VideoToolbox exposure | Build PyAV from source against system FFmpeg 7.1+ |
| **Amazon NICE DCV-compatible protocol** | Proprietary; we'd need to reverse-engineer it; no spec | Design our own protocol (already done in `common/messages.py`) |
| **Ethertype / raw sockets** for LAN optimization | Requires root + kernel module; not portable | QUIC over UDP is within 1–2ms of raw UDP |

---

## Stack Patterns by Variant

**If deploying on Rocky 9 + NVIDIA (DXS baseline, primary v1 test bed):**
- X11/Xorg with NVIDIA ConnectedMonitor `xorg-teraguchi.conf`
- NvFBC primary capture, mss fallback
- NVENC HEVC Main10 primary encoder, `av1_nvenc` secondary for Ada+
- uinput + XTest input injection
- PulseAudio monitor source (not yet PipeWire on Rocky 9)
- QUIC primary transport over Tailscale

**If deploying on macOS server (author's Mac Studio, secondary v1 test bed):**
- ScreenCaptureKit with HDR config
- VideoToolbox direct via PyObjC (low-latency mode for H.264, normal for HEVC Main10)
- CoreGraphics + IOHIDUserDevice tablet shim
- AVFoundation audio capture
- LaunchAgent (not LaunchDaemon) because SCK needs GUI session

**If deploying on Rocky 10 / Fedora 41+ (post-v1, speculative):**
- Wayland + gnome-remote-desktop integration? Or headless Weston session per user?
- PipeWire portal screen capture replacing NvFBC (via NVIDIA's new PipeWire backend)
- libei input injection via RemoteDesktop portal
- PipeWire audio capture (direct, not via pactl shim)
- **Blocked on:** Autodesk shipping a Wayland-compatible Flame, which is not announced as of 2026-04.

**If deploying client on Windows (Phase 3, post-v1):**
- PySide6 same
- PyAV + FFmpeg with DXVA2 / D3D11VA hwaccel
- Direct QUIC or fallback WS
- Not a v1 scope item

---

## Version Compatibility Matrix

| Package | Compatible With | Notes |
|---|---|---|
| `PySide6 6.10` | Python 3.10–3.13 | 6.10 is the first with full ARM64 wheels across all three OSes. Use 6.8+ floor. |
| `PyAV 17.x` | FFmpeg 6.x or 7.x | PyAV 17 dropped libaom; requires FFmpeg 7 for Blackwell NVENC 4:2:2 fully. |
| `aioquic 1.3.0` | Python 3.9–3.13, `cryptography >= 42` | TLS 1.3 via `cryptography`. Don't pin `cryptography` below 44 or you'll get OpenSSL 3 deprecation warnings. |
| `FFmpeg 7.1.1` | NVENC SDK 13.0, VideoToolbox (macOS 12.3+) | Needed for Blackwell 4:2:2, AV1 Ultra HQ, H.264 10-bit. |
| `pyobjc 11.x` | Python 3.10–3.13, macOS 12–15 | Pin `pyobjc-core >= 11.0`. |
| `websockets 15.x` | Python 3.9+ | API stable since 11.x. |
| NVIDIA driver `570.x+` | Rocky 9 kernel 5.14+, CUDA 12.8+ | Needed for Blackwell; `525.x` is the floor for Ada/Ampere. |
| Rocky Linux 9.3/9.5 | Flame 2026 supported | 9.6 and 10 not listed by Autodesk as of research date. |

---

## Confidence Summary

| Claim | Confidence | Verified via |
|---|---|---|
| PyAV 17.0.1 is current (Apr 2026) | HIGH | GitHub releases page |
| aioquic 1.3.0 is current | MEDIUM-HIGH | PyPI, libraries.io |
| PySide6 6.10 is current (Oct 2025) | HIGH | qt.io blog |
| NVENC 4:2:2 requires Blackwell | HIGH | NVIDIA Video Codec SDK 13 blog |
| NVENC AV1 10-bit is Ada+ | HIGH | NVIDIA Ada Lovelace blog |
| VideoToolbox low-latency mode is H.264-only | HIGH | Apple WWDC21 session 10158 |
| Flame 2026 does NOT support Wayland | HIGH | Autodesk official TS article |
| Rocky 10 is Wayland-default, no xrdp/x11vnc | HIGH | Rocky 10 docs |
| NvFBC doesn't work on Wayland | HIGH | LizardByte forums, NVIDIA forum thread |
| Parsec BUD achieves ~7ms LAN | MEDIUM | Parsec engineering blog (vendor-marketing, not peer-reviewed) |
| Sub-16ms total is achievable on LAN with our stack | MEDIUM | Extrapolation from Parsec/Sunshine published numbers |
| IOHIDUserDevice for tablet pressure works | MEDIUM | Wacom-qemu and community patterns; not Apple-blessed |
| M4 supports HEVC 4:2:2 hardware encode | MEDIUM | Community reports; Apple docs silent |
| FFmpeg subprocess overhead is <2ms | LOW | Has not been independently benchmarked on this codebase |
| QUIC Python throughput is adequate for 4K60 HEVC (~30Mbps) | MEDIUM-HIGH | aioquic benchmarks (Net TUM paper) show 200–500Mbps single-stream; we need 30Mbps |

---

## Sources

**Vendor / Official:**
- [NVIDIA Video Codec SDK 13.0 blog (Blackwell)](https://developer.nvidia.com/blog/nvidia-video-codec-sdk-13-0-powered-by-nvidia-blackwell/) — HIGH
- [NVIDIA NVENC Application Note](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-application-note/index.html) — HIGH
- [Apple Developer — Encoding video for low-latency conferencing](https://developer.apple.com/documentation/VideoToolbox/encoding-video-for-low-latency-conferencing) — HIGH
- [Apple WWDC24 — Capture HDR content with ScreenCaptureKit (session 10088)](https://developer.apple.com/videos/play/wwdc2024/10088/) — HIGH
- [Apple WWDC21 — Explore low-latency video encoding with VideoToolbox (session 10158)](https://developer.apple.com/videos/play/wwdc2021/10158/) — HIGH
- [Apple HIDDriverKit docs](https://developer.apple.com/documentation/hiddriverkit) — HIGH
- [Autodesk Flame 2026 system requirements](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/flame-2026-sysreqs.html) — HIGH
- [Autodesk — Wayland Display Server Not Supported](https://www.autodesk.com/support/technical/article/caas/tsarticles/ts/3t2VQSfCGLLvGEwb2lPn44.html) — HIGH
- [Rocky Linux 10 RDP docs](https://docs.rockylinux.org/10/desktop/gnome/rdp-server/) — HIGH
- [VFX Reference Platform CY2026](https://vfxplatform.com/) — HIGH
- [Qt for Python 6.10 release blog](https://www.qt.io/blog/qt-for-python-release-6.10-is-here) — HIGH

**Library / Ecosystem:**
- [PyAV GitHub releases](https://github.com/PyAV-Org/PyAV/releases) — HIGH (v17.0.1 confirmed)
- [PyAV caveats — bundled FFmpeg lacks CUDA](https://pyav.org/docs/stable/overview/caveats.html) — HIGH
- [aioquic PyPI](https://pypi.org/project/aioquic/) — HIGH (v1.3.0)
- [Who-T on libei design](http://who-t.blogspot.com/2020/08/libei-library-to-support-emulated-input.html) — HIGH
- [Phoronix — libei 1.0 release](https://www.phoronix.com/news/libei-1.0-Emulated-Input) — HIGH
- [Phoronix — XWayland drops EGLStream](https://www.phoronix.com/news/XWayland-Drops-EGLStream) — HIGH

**Reference / Competitive protocols:**
- [Parsec — Networking Protocol (BUD)](https://parsec.app/blog/a-networking-protocol-built-for-the-lowest-latency-interactive-game-streaming-1fd5a03a6007) — MEDIUM (vendor blog)
- [Parsec — Primer on Building UDP Networking Protocols](https://parsec.app/blog/a-primer-on-building-udp-networking-protocols-how-we-deliver-low-latency-cloud-gaming-1987806feb62) — MEDIUM
- [NICE DCV — enable QUIC docs](https://github.com/awsdocs/nice-dcv-admin-guide/blob/master/doc_source/enable-quic.md) — HIGH
- [NICE DCV color accuracy release notes](https://www.amazonaws.cn/en/new/2022/nice-dcv-color-accuracy-game-controller/) — HIGH
- [Sunshine encoder pipeline (DeepWiki)](https://deepwiki.com/LizardByte/Sunshine/5.2-video-encoding-pipeline) — MEDIUM (community-compiled)
- [Looking Glass project](https://looking-glass.io/) — HIGH (for contrast; not our architecture)

**Packaging / CI:**
- [Federico Terzi — Automatic Code-signing and Notarization for macOS apps using GitHub Actions](https://federicoterzi.com/blog/automatic-code-signing-and-notarization-for-macos-apps-using-github-actions/) — HIGH
- [indygreg/apple-code-sign-action](https://github.com/indygreg/apple-code-sign-action) — MEDIUM (alternative to notarytool, useful fallback)

**Academic / Measurement:**
- [Comparison of Different QUIC Implementations (TU München)](https://www.net.in.tum.de/fileadmin/TUM/NET/NET-2022-07-1/NET-2022-07-1_02.pdf) — HIGH
- [Evaluation of NVENC Split-Frame Encoding (arXiv 2511.18687)](https://arxiv.org/html/2511.18687v1) — MEDIUM-HIGH

---

*Stack research for Teraguchi v1 — remote workstation for VFX / creative workflows*
*Researched: 2026-04-18 by research agent on dev branch*
