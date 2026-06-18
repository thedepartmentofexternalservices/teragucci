# Teraguchi v1 Requirements

**Milestone:** v1.0 — Production-grade Mac client + Rocky Linux server + macOS server
**Target users:** Small independent VFX studios (1-10 people) using Flame, Nuke, Resolve, Houdini
**Core value:** A Flame artist can work an 8-hour client session remotely and not notice they're remote

## Decisions locked during requirements definition

| Decision | Value |
|----------|-------|
| License | Apache 2.0 |
| Frame rate | 60fps LAN, 30fps WAN fallback (Tailscale / public internet) |
| Linux server OS | Rocky Linux 9 only (RPM signed) |
| Audio codec | Opus, 48kHz, low-latency mode |
| Apple Developer ID | Logik Academy Pro account + contractor handles Phase 6 signing setup |
| Transport | QUIC (aioquic 1.3) primary for WAN; TLS-WS + UDP dual-channel primary for LAN |
| Video codec | HEVC Main10 (10-bit) — NVENC on Rocky, direct VideoToolbox on Mac |
| Broker | Paused. Direct client→server PAM is the v1 path |
| Connection discovery | Tailscale-first + manual hostname / saved bookmarks |

## v1 Requirements

### Stability + CI + Test Baseline (STAB)

- [ ] **STAB-01**: Pytest test suite covering `common/` (messages, keymap, transport, jitter buffer), server auth/tokens, client bookmarks
- [ ] **STAB-02**: GitHub Actions CI running tests on macos-14 runner + rockylinux:9 container on every PR
- [ ] **STAB-03**: Lint + type-check pipeline (ruff + mypy) gating CI
- [ ] **STAB-04**: Fix `server/main.py` `send_queue(maxsize=30)` → `maxsize=4` with request-IDR-on-drop recovery
- [ ] **STAB-05**: Decompose `server/main.py` into `SessionRuntime` / `ClientSession` / entrypoint modules
- [ ] **STAB-06**: Explicit `SessionFSM` on both client and server; state serialized into health pings so disagreement surfaces immediately
- [ ] **STAB-07**: Bounded async queues with correct drop policy throughout capture→encode→transport pipeline
- [ ] **STAB-08**: Client-side `ConnectionSupervisor` pattern — auto-reconnect, state preservation across drops
- [ ] **STAB-09**: 8-hour session smoke test harness (crash-free, memory-leak-free, no audio/video degradation)

### Input Fidelity (INPUT)

- [ ] **INPUT-01**: Table-driven keymap unit tests — Qt key codes → Linux scancodes + Mac virtual key codes, every modifier chord covered
- [ ] **INPUT-02**: Release-all-modifiers on `QEvent::WindowDeactivate` (Cmd-Tab no longer leaves Ctrl stuck)
- [ ] **INPUT-03**: Release-all-modifiers on reconnect (half-connected state can't leak modifier state)
- [ ] **INPUT-04**: Integration test executing a set of "crazy Flame hotkey combos" against a real Rocky server and a real Mac server; asserts no mangling
- [ ] **INPUT-05**: International keyboard + dead-key handling (verified on US, UK, DE, JP layouts at minimum)
- [ ] **INPUT-06**: Caps Lock state sync between client and server
- [ ] **INPUT-07**: Cmd ↔ Ctrl translation when Mac client → Linux server (Flame-appropriate default, user-overridable)
- [ ] **INPUT-08**: IOHIDUserDevice-based pen pressure injector for macOS server (replaces CGEventPost tablet path which silently loses `NSEvent.pressure`)
- [ ] **INPUT-09**: Wacom pressure/tilt integration test on real hardware — Intuos Pro Large + Cintiq Pro, macOS Sonoma + Sequoia matrix
- [ ] **INPUT-10**: Eraser + tablet-side button events round-trip
- [ ] **INPUT-11**: Proximity events (pen approach / leave) delivered without loss
- [ ] **INPUT-12**: Pressure curve preservation (no quantization artifacts visible in Flame paint strokes)

### Video + Color (VIDEO)

- [ ] **VIDEO-01**: 10-bit end-to-end pipeline verified — capture, encoder input format, encoder output, transport, decoder output, display — with no silent downgrades
- [ ] **VIDEO-02**: Bit-exact end-to-end test pattern (ramp gradient + banding detector) committed as CI fixture
- [ ] **VIDEO-03**: HEVC Main10 via `hevc_nvenc` on NVIDIA Rocky 9 servers (Turing/Ampere baseline, Blackwell-ready)
- [ ] **VIDEO-04**: Direct `VTCompressionSession` via PyObjC on macOS server with `kVTVideoEncoderSpecification_EnableLowLatencyRateControl` (saves 5-8ms/frame vs FFmpeg wrapper)
- [ ] **VIDEO-05**: 4:2:0 chroma subsampling baseline (Turing/Ampere capability). 4:2:2 opt-in where hardware supports (Blackwell, Apple Silicon M4)
- [ ] **VIDEO-06**: IDR-on-drop recovery (tied to STAB-04)
- [ ] **VIDEO-07**: Encoder reconfigure without full pipeline restart (quality slider changes mid-session are seamless)
- [ ] **VIDEO-08**: ScreenCaptureKit configured with `.hdrLocalDisplay` + `kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange`
- [ ] **VIDEO-09**: NvFBC primary / mss fallback dispatch on Linux (existing, verify zero-copy path under load)
- [ ] **VIDEO-10**: 60fps native-resolution delivery on LAN; 30fps fallback on WAN with quality-slider controls
- [ ] **VIDEO-11**: Sub-20ms LAN input-to-photon measured via instrumentation; CI benchmark keeps the number from drifting
- [ ] **VIDEO-12**: PyAV client decode linked against system FFmpeg 7.1+ for full VideoToolbox hwaccel (not the bundled PyPI wheels)

### Display + Multi-Monitor (DISP)

- [ ] **DISP-01**: Per-session multi-monitor mode selector at connect time: single-monitor / mirror-all-server-monitors / pick-one
- [x] **DISP-02**: Monitor hot-plug during session gracefully handled (no crash, offer remap UI)
- [ ] **DISP-03**: Cursor coordinates always computed in server physical pixels regardless of client DPR / retina / zoom
- [x] **DISP-04**: CustomEDID for Xvfb sessions matching Flame's monitor-config-dialog expectations
- [ ] **DISP-05**: Mixed-DPI client rendering (retina + external non-retina) without distortion
- [x] **DISP-06**: ScreenCaptureKit display-change handler for macOS server monitor hot-plug
- [x] **DISP-07**: Per-monitor fullscreen mode (client chooses which server monitor the client window corresponds to)

### Audio (AUDIO)

- [ ] **AUDIO-01**: Opus 48kHz low-latency mode, <10ms algorithmic delay
- [ ] **AUDIO-02**: Full-duplex: server timeline playback → client + client mic → server for client review sessions
- [ ] **AUDIO-03**: Mouth-to-ear latency target <80ms on LAN, <150ms on Tailscale WAN, measured and asserted
- [ ] **AUDIO-04**: Audio stream watchdog auto-restarts on the 30-minute PulseAudio hang bug
- [ ] **AUDIO-05**: Device-change handling — AirPods connect/disconnect mid-session re-attaches cleanly
- [ ] **AUDIO-06**: Rocky 9 capture via PipeWire (pipewire-pulse compat) with PulseAudio fallback
- [ ] **AUDIO-07**: New `server/mac_audio_capture.py` using AVAudioEngine tap
- [ ] **AUDIO-08**: Optional client-side echo cancellation for studios with speakers near mic
- [ ] **AUDIO-09**: 60-minute continuity smoke test (no dropouts, no sample-rate drift, no sync loss)

### Network + Transport (NET)

- [ ] **NET-01**: QUIC (aioquic 1.3.0) promoted from experimental to primary transport for WAN / public internet
- [ ] **NET-02**: TLS-WebSocket + UDP dual-channel retained as primary for LAN and as fallback
- [ ] **NET-03**: Tailscale-first connection model — client accepts `host.tailnet:443` hostname with MagicDNS
- [ ] **NET-04**: Client bookmark UI with recent hosts, saved passwords (keychain), per-host quality profile
- [ ] **NET-05**: FEC (zfec Reed-Solomon, 30-50% overhead, configurable) mandatory for keyframes
- [ ] **NET-06**: MTU ceiling 1280 (Tailscale/WireGuard safe)
- [ ] **NET-07**: Captive-portal detection + user-facing "reconnect when network recovers" UX
- [ ] **NET-08**: QUIC connection migration tested and working across Tailscale network changes (WiFi → cellular → back)
- [ ] **NET-09**: Rules-based adaptive bitrate driven by measured RTT + packet loss
- [ ] **NET-10**: `SESSION_RESUME` protocol message — client rebinds to existing server-side session after disconnect; session persists until idle timeout
- [ ] **NET-11**: Visible network quality telemetry in client overlay (bandwidth, RTT, loss, current codec bitrate)

### Security + Auth (SEC)

- [ ] **SEC-01**: TLS cert verification ON everywhere — remove all `ssl.CERT_NONE` in client WS, broker WS, QUIC, aiohttp probes
- [ ] **SEC-02**: Tailscale-native certs as the primary cert path (zero-config for tailscale-mesh deployments)
- [ ] **SEC-03**: TOFU (trust-on-first-use) fingerprint pinning fallback for deployments without Tailscale certs
- [ ] **SEC-04**: Optional corporate CA bundle support
- [ ] **SEC-05**: Direct client→server PAM authentication path hardened (broker-free)
- [ ] **SEC-06**: Bookmark password storage migrated from XOR obfuscation → macOS Keychain
- [ ] **SEC-07**: Session token lifetime bounded; rotation during long sessions
- [ ] **SEC-08**: Security audit pass on auth, token, and TLS paths before v1 release
- [ ] **SEC-09**: `SECURITY.md` with vulnerability disclosure policy and contact

### Clipboard (CLIP)

- [ ] **CLIP-01**: Bidirectional text clipboard — existing, harden against edge cases (large pastes, newline encoding)
- [ ] **CLIP-02**: Bidirectional image clipboard — PNG/JPEG transport, screenshots and paint reference
- [ ] **CLIP-03**: Per-direction clipboard toggles (user can disable client → server paste for privacy)

### File Transfer (FILE)

- [ ] **FILE-01**: Drag-drop client → server, existing path hardened (large files, many files)
- [ ] **FILE-02**: Bulk pull server → client for pulling renders back (new feature)
- [ ] **FILE-03**: Progress UI with cancel, resume, and queue
- [ ] **FILE-04**: SHA-256 integrity verification (existing, verify end-to-end)
- [ ] **FILE-05**: Resume of interrupted transfers after reconnect

### USB / Control Surfaces (USB)

- [ ] **USB-01**: Control-surface USB/IP forwarding Mac client → Linux server (Loupedeck, Tangent Wave/Element, similar)
- [ ] **USB-02**: Control-surface USB/IP forwarding Mac client → Mac server (spike first — `usbip` on macOS as server is not well-paved; may require alternate mechanism like libusb-userspace shim)
- [ ] **USB-03**: Device allow-list (only USB classes on the allow-list are forwardable)

### Distribution (DIST)

- [ ] **DIST-01**: Signed + notarized macOS `.app` bundle built in GitHub Actions on `v*` tags
- [ ] **DIST-02**: Signed RPM for Rocky 9 built in GitHub Actions on `v*` tags
- [ ] **DIST-03**: GitHub Releases with SHA-256 checksums for all artifacts
- [ ] **DIST-04**: RPM GPG public key published with install instructions
- [ ] **DIST-05**: Stable macOS bundle identifier (`com.teraguchi.client`, `com.teraguchi.server`) — never change post-v1
- [ ] **DIST-06**: Hardened runtime entitlements file for macOS bundle
- [ ] **DIST-07**: Uninstaller scripts for both Mac and Linux
- [ ] **DIST-08**: Upgrade-in-place support — client/server version-skew tolerance documented and tested
- [ ] **DIST-09**: Release runbook (`docs/release.md`)

### Observability (OBS)

- [ ] **OBS-01**: `structlog` JSON logging throughout (server, client, broker)
- [ ] **OBS-02**: Per-stage latency breakdown visible in client health overlay (capture / encode / transmit / decode / display)
- [ ] **OBS-03**: Bandwidth + frame-drop + keyframe telemetry in overlay (existing, expand)
- [ ] **OBS-04**: Optional Prometheus metrics endpoint on server
- [ ] **OBS-05**: Diagnostic bundle export command (zips logs + system info for bug reports)

### Documentation (DOCS)

- [ ] **DOCS-01**: Quickstart README — 15-minute setup on a fresh Mac client and fresh Rocky 9 server
- [ ] **DOCS-02**: Architecture overview document (based on `.planning/research/ARCHITECTURE.md`)
- [ ] **DOCS-03**: On-the-wire protocol reference — message types, binary frame headers, auth flow
- [ ] **DOCS-04**: "Migrating from HP Anyware / PCoIP" guide
- [ ] **DOCS-05**: Reference deployment guides — homelab, 3-person studio, 10-person studio, GCP-hosted
- [ ] **DOCS-06**: Short demo video (<3min) of a Flame session over Teraguchi
- [ ] **DOCS-07**: Troubleshooting guide (common issues + diagnostic steps)

### Governance + OSS Hygiene (GOV)

- [ ] **GOV-01**: `LICENSE` — Apache 2.0
- [ ] **GOV-02**: `CONTRIBUTING.md` — contribution flow + explicit scope boundaries + anti-feature list
- [ ] **GOV-03**: `CODE_OF_CONDUCT.md` — Contributor Covenant
- [ ] **GOV-04**: `SECURITY.md` — vulnerability disclosure policy
- [ ] **GOV-05**: Issue templates (bug report, feature request, performance report) + PR template
- [ ] **GOV-06**: Stale-bot configuration
- [ ] **GOV-07**: Invite second committer before v1.1 to de-risk solo-maintainer burnout

## Deferred (post-v1, on the roadmap)

- Windows client + Windows server (Phase 3 of overall project plan)
- Linux client packaged and signed (exists in tree, not distributed)
- Broker hardening and pool-scheduling features (broker works, paused)
- License dongle (iLok/HASP) USB/IP forwarding
- Stream Deck / keypad USB/IP forwarding
- Ubuntu / Debian server packages
- Rocky 10 / Wayland support (blocked on Autodesk Flame Wayland support)
- Full ICC color profile pipeline

## Out of Scope (v1 and beyond, with reasoning)

- **Browser / WebRTC client** — Incompatible with 10-bit color fidelity; PySide6 native only
- **Mobile client (iPad / Android)** — Wrong audience for VFX workflows
- **Smart-card / CAC authentication** — Enterprise overbuild, not needed for small studios
- **Session recording for compliance** — Compliance feature, not artist-facing; adds complexity
- **Real-time collaboration / observer mode** — Order-of-magnitude complexity for a solo-maintainer project
- **Game controller forwarding** — Wrong audience
- **Zero-client hardware** — We are software-only
- **Printer redirection** — Nobody prints from Flame
- **Drive mapping** — File transfer + clipboard cover the use case

## Traceability

Every v1 requirement is mapped to exactly one phase. Coverage: 108/108 (100%).

| REQ-ID | Phase | Status |
|--------|-------|--------|
| STAB-01 | Phase 1 | Pending |
| STAB-02 | Phase 1 | Pending |
| STAB-03 | Phase 1 | Pending |
| STAB-04 | Phase 1 | Pending |
| STAB-05 | Phase 1 | Pending |
| STAB-06 | Phase 1 | Pending |
| STAB-07 | Phase 1 | Pending |
| STAB-08 | Phase 1 | Pending |
| STAB-09 | Phase 1 | Pending |
| INPUT-01 | Phase 2 | Pending |
| INPUT-02 | Phase 2 | Pending |
| INPUT-03 | Phase 2 | Pending |
| INPUT-04 | Phase 2 | Pending |
| INPUT-05 | Phase 2 | Pending |
| INPUT-06 | Phase 2 | Pending |
| INPUT-07 | Phase 2 | Pending |
| INPUT-08 | Phase 2 | Pending |
| INPUT-09 | Phase 2 | Pending |
| INPUT-10 | Phase 2 | Pending |
| INPUT-11 | Phase 2 | Pending |
| INPUT-12 | Phase 2 | Pending |
| VIDEO-01 | Phase 2 | Pending |
| VIDEO-02 | Phase 2 | Pending |
| VIDEO-03 | Phase 2 | Pending |
| VIDEO-04 | Phase 2 | Pending |
| VIDEO-05 | Phase 2 | Pending |
| VIDEO-06 | Phase 2 | Pending |
| VIDEO-07 | Phase 2 | Pending |
| VIDEO-08 | Phase 2 | Pending |
| VIDEO-09 | Phase 2 | Pending |
| VIDEO-10 | Phase 2 | Pending |
| VIDEO-11 | Phase 2 | Pending |
| VIDEO-12 | Phase 2 | Pending |
| DISP-01 | Phase 3 | Pending |
| DISP-02 | Phase 3 | Complete (Plan 03-05) |
| DISP-03 | Phase 3 | Pending |
| DISP-04 | Phase 3 | Complete (Plan 03-03) |
| DISP-05 | Phase 3 | Pending |
| DISP-06 | Phase 3 | Complete (Plan 03-05 completes the UX consumer for Plan 03-03 SCK delegate) |
| DISP-07 | Phase 3 | Complete (Plan 03-02 client UX + Plan 03-03 server crop) |
| CLIP-01 | Phase 3 | Pending |
| CLIP-02 | Phase 3 | Pending |
| CLIP-03 | Phase 3 | Pending |
| AUDIO-01 | Phase 4 | Pending |
| AUDIO-02 | Phase 4 | Pending |
| AUDIO-03 | Phase 4 | Pending |
| AUDIO-04 | Phase 4 | Pending |
| AUDIO-05 | Phase 4 | Pending |
| AUDIO-06 | Phase 4 | Pending |
| AUDIO-07 | Phase 4 | Pending |
| AUDIO-08 | Phase 4 | Pending |
| AUDIO-09 | Phase 4 | Pending |
| NET-01 | Phase 5 | Pending |
| NET-02 | Phase 5 | Pending |
| NET-03 | Phase 5 | Pending |
| NET-04 | Phase 5 | Pending |
| NET-05 | Phase 5 | Pending |
| NET-06 | Phase 5 | Pending |
| NET-07 | Phase 5 | Pending |
| NET-08 | Phase 5 | Pending |
| NET-09 | Phase 5 | Pending |
| NET-10 | Phase 5 | Pending |
| NET-11 | Phase 5 | Pending |
| FILE-01 | Phase 5 | Pending |
| FILE-02 | Phase 5 | Pending |
| FILE-03 | Phase 5 | Pending |
| FILE-04 | Phase 5 | Pending |
| FILE-05 | Phase 5 | Pending |
| USB-01 | Phase 5 | Pending |
| USB-02 | Phase 5 | Pending (spike-gated) |
| USB-03 | Phase 5 | Pending |
| OBS-04 | Phase 5 | Pending |
| SEC-01 | Phase 1 | Pending (critical-path blocker) |
| SEC-02 | Phase 6 | Pending |
| SEC-03 | Phase 6 | Pending |
| SEC-04 | Phase 6 | Pending |
| SEC-05 | Phase 6 | Pending |
| SEC-06 | Phase 6 | Pending |
| SEC-07 | Phase 6 | Pending |
| SEC-08 | Phase 6 | Pending |
| SEC-09 | Phase 6 | Pending |
| DIST-01 | Phase 6 | Pending |
| DIST-02 | Phase 6 | Pending |
| DIST-03 | Phase 6 | Pending |
| DIST-04 | Phase 6 | Pending |
| DIST-05 | Phase 6 | Pending |
| DIST-06 | Phase 6 | Pending |
| DIST-07 | Phase 6 | Pending |
| DIST-08 | Phase 6 | Pending |
| DIST-09 | Phase 6 | Pending |
| OBS-01 | Phase 1 | Pending |
| OBS-02 | Phase 1 | Pending |
| OBS-03 | Phase 1 | Pending |
| OBS-05 | Phase 1 | Pending |
| DOCS-01 | Phase 7 | Pending |
| DOCS-02 | Phase 7 | Pending |
| DOCS-03 | Phase 7 | Pending |
| DOCS-04 | Phase 7 | Pending |
| DOCS-05 | Phase 7 | Pending |
| DOCS-06 | Phase 7 | Pending |
| DOCS-07 | Phase 7 | Pending |
| GOV-01 | Phase 7 | Pending |
| GOV-02 | Phase 7 | Pending |
| GOV-03 | Phase 7 | Pending |
| GOV-04 | Phase 7 | Pending |
| GOV-05 | Phase 7 | Pending |
| GOV-06 | Phase 7 | Pending |
| GOV-07 | Phase 7 | Pending |

### Coverage Summary by Phase

| Phase | Requirement Count | Categories |
|-------|-------------------|------------|
| Phase 1: Stability + CI + Test Baseline | 14 | STAB (9), SEC-01, OBS-01/-02/-03/-05 (4) |
| Phase 2: Input + Color Fidelity | 24 | INPUT (12), VIDEO (12) |
| Phase 3: Display + Multi-Monitor + Clipboard | 10 | DISP (7), CLIP (3) |
| Phase 4: Audio | 9 | AUDIO (9) |
| Phase 5: Network, Transport, Side-Channels | 20 | NET (11), FILE (5), USB (3), OBS-04 (1) |
| Phase 6: Distribution + Security Hardening | 17 | SEC (8 of 9; SEC-01 in Phase 1), DIST (9) |
| Phase 7: OSS Polish + Governance | 14 | DOCS (7), GOV (7) |
| **Total** | **108 / 108** | **All categories covered** |

---
*Generated 2026-04-18 from research/SUMMARY.md + PROJECT.md after user locked license/fps/audio/OS decisions. Traceability populated 2026-04-18 by gsd-roadmapper.*
