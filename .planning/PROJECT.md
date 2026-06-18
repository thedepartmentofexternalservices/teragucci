# Teraguchi

## What This Is

Open-source high-performance remote workstation for creative and VFX workflows. Replaces HP Anyware (PCoIP) for small, independent VFX studios — PCoIP has been announced EOL and there is no comparable drop-in for Autodesk Flame, Nuke, Resolve, and the color-critical grading/finishing tools that depend on under-desk-class responsiveness. Client runs on macOS; server runs on Rocky Linux or macOS. Built to treat Flame-style workflows as first-class citizens: full Wacom Pro pressure/tilt, deep modifier-heavy hotkey fidelity, 10-bit end-to-end color, sub-20ms LAN input-to-photon.

## Core Value

**A Flame artist can work an 8-hour client session remotely and not notice they're remote** — input latency, color accuracy, and reliability all match sitting in front of the machine. If that holds, everything else matters. If it doesn't, the project has failed regardless of feature count.

## Requirements

### Validated

<!-- Inferred from existing vibe-coded prototype. Proven to work end-to-end; not yet production-grade. -->

- ✓ Three-tier architecture: client (PySide6) / broker (optional, FreeIPA-gated) / server (Linux + macOS) — existing
- ✓ Shared protocol library in `common/` — message dataclasses, keymap, hybrid transport, jitter buffer, QUIC transport — existing
- ✓ TLS WebSocket control channel + optional UDP media channel on the same port — existing
- ✓ Experimental QUIC transport via `aioquic` — existing (not yet production path)
- ✓ Per-user X11 session isolation on Linux (Xvfb or real NVIDIA-accelerated Xorg, spawned per PAM-authenticated user) — existing
- ✓ macOS server backends (ScreenCaptureKit, CoreGraphics input, NSPasteboard clipboard, AVFoundation audio, LaunchAgent packaging) — existing (landed in last ~5 commits, rough)
- ✓ H.264 / HEVC / AV1 video encode (server) and decode (client via PyAV) with hardware-accel fallback chain — existing
- ✓ Wacom pen pressure + tilt via Qt `QTabletEvent` → protocol → server injection — existing
- ✓ Multi-monitor protocol + client-side monitor picker — existing
- ✓ Bidirectional text clipboard sync + image clipboard (partial) — existing
- ✓ Chunked file transfer client → server with SHA-256 integrity — existing
- ✓ USB/IP forwarding from client to Linux server — existing (Linux server only)
- ✓ Session persistence across client disconnects (PCoIP-style) — existing
- ✓ Broker with FreeIPA LDAP / `id -Gn` fallback + HMAC-SHA256 redirect tokens + web admin UI — existing
- ✓ Health monitoring (RTT, FPS, bandwidth) with overlay — existing
- ✓ Linux server RPM-friendly install via `install-server.sh` + systemd unit — existing
- ✓ macOS client app bundle via PyInstaller (unsigned) — existing
- ✓ Windows client installer scaffolding (`install-client.ps1`) — existing (not a v1 deliverable target)

### Active

<!-- v1 milestone. Production-grade for small VFX studios. -->

**Client (macOS) — production-grade**

- [ ] Bulletproof stability — no crashes or wedges in 8-hour Flame sessions
- [ ] Native Qt input path delivers full-fidelity keyboard (every Flame modifier combination), mouse, and Wacom Pro pressure/tilt across Mac→Linux and Mac→Mac — zero pressure glitches, zero hotkey mangling
- [ ] Per-session flexible multi-monitor mode selection on connect: single-monitor, mirror-all, pick-one
- [ ] 10-bit video decode end-to-end (H.264 Main10 / HEVC Main10 / AV1) with VideoToolbox hardware acceleration
- [ ] Bidirectional text + image clipboard
- [ ] File drag-drop client → server
- [ ] Bulk file pull server → client (new)
- [ ] Full-duplex low-latency audio (playback + mic capture)
- [ ] Signed + notarized `.app` distributed via GitHub release
- [ ] USB/IP forwarding of control surfaces (Loupedeck, Tangent Element / Wave, etc.) from client to server

**Server (Rocky Linux) — production-grade**

- [ ] Bulletproof stability — capture/encode pipeline survives monitor hot-plug, encoder failures, user disconnect/reconnect
- [ ] 10-bit capture + encode end-to-end (NVENC Main10, VAAPI HEVC 10-bit where available)
- [ ] Signed/packaged RPM for Rocky Linux with idempotent systemd unit installation
- [ ] Direct PAM auth path against local users (broker bypass)
- [ ] Network-adaptive transport — LAN, Tailscale WAN, and public internet all first-class; adaptive bitrate, FEC / retransmit strategy, QUIC path hardened as a production option
- [ ] Full-duplex low-latency audio (PulseAudio/PipeWire monitor source + mic injection)
- [ ] USB/IP control-surface forwarding target
- [ ] Observable health endpoints usable from the client overlay

**Server (macOS) — production-grade**

- [ ] Bulletproof stability — SCK capture, CoreGraphics injection, LaunchAgent lifecycle all hardened
- [ ] 10-bit capture + encode (VideoToolbox H.264/HEVC Main10)
- [ ] Signed + notarized installer / packaged bundle
- [ ] Full-duplex low-latency audio (AVFoundation)
- [ ] Control-surface USB/IP forwarding target (Mac-side receiver)
- [ ] TCC onboarding UX that doesn't require a PhD to configure

**Network / connectivity**

- [ ] Tailscale-first + manual hostname connection model (`wss://host.tailnet:443` or saved bookmark list)
- [ ] LAN performance benchmark: sub-20ms input-to-photon, 60fps at native monitor resolution
- [ ] WAN (Tailscale) and public-internet targets with graceful degradation and visible quality telemetry

**Project / distribution polish**

- [ ] Professional GitHub repo: quickstart + architecture + on-the-wire protocol reference
- [ ] CI via GitHub Actions: tests, lint/typecheck on Mac + Rocky, signed release builds triggered on git tag
- [ ] Reference deployments: homelab, 3-person studio, 10-person studio, GCP-hosted
- [ ] OSS hygiene: LICENSE, CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, issue/PR templates
- [ ] Short demo video + marketing README suitable for sharing with the wider Flame/VFX community

### Out of Scope

<!-- Explicit exclusions for v1, with reasoning. -->

- **Broker hardening / feature work** — broker works for the author's DXS infra and stays in the tree, but is not a v1 deliverable. Small studios will use direct client→server PAM. Revisit once v1 is shipping and broker's value proposition (pool scheduling, FreeIPA SSO) is demanded by real users.
- **Windows client** — Phase 3 of the author's roadmap. Windows installer scaffolding stays in tree but is not tested or shipped in v1.
- **Windows server** — Phase 3. No active work.
- **Linux client** — Client is macOS-only in v1. Existing PySide6 client *can* run on Linux but will not be packaged, signed, or supported.
- **License dongle (iLok / HASP) USB/IP forwarding** — deferred. Control surfaces only in v1. Most dongles live on the server side already.
- **Stream Deck / keypad forwarding** — deferred; many artists already bind macros on the client OS.
- **Full ICC profile pipeline / display calibration management** — v1 ships 10-bit end-to-end but does not attempt to preserve or transform client ICC profiles. Revisit when a real grading studio demands it.
- **Session recording / playback for QA** — interesting but not a v1 commitment.
- **Browser / WebRTC client** — PySide6 native only. No browser path.
- **Mobile client (iPad / Android)** — not considered for v1.

## Context

**Origin.** Teraguchi started as a personal vibe-coded proof of concept to solve Randy McEntee's own problem — running Autodesk Flame on Rocky Linux workstations from Mac clients without paying HP Anyware / PCoIP licenses. The prototype is working end-to-end (all three tiers, both server platforms, most of the core input/video/audio/clipboard/USB paths), but it is not yet robust, signed, documented, or approachable by anyone outside the author's environment.

**Why now.** HP Anyware has publicly announced PCoIP end-of-life. Every small independent VFX studio that relies on PCoIP for their Flame / Nuke / Resolve / Houdini workstations is now on a deadline. Existing alternatives (NICE DCV, Parsec, Moonlight, Sunshine) don't meet the combined demand for Wacom pressure + 10-bit color + modifier-heavy hotkeys + under-desk latency that Flame-class workflows require.

**Target users.** Small, independent VFX studios — the 1-10 person shops where buying a $300/seat/yr remote-desktop license per artist isn't financially sustainable. Also the author's own infrastructure (DXS, ~6 Flame workstations, mixed Rocky/macOS, FreeIPA, Tailscale mesh).

**Existing codebase state.** Python 3.10+, asyncio-driven throughout. PySide6 client with tabbed sessions, bookmarks, quality control, health overlay. Server with per-user Xvfb/Xorg on Linux + ScreenCaptureKit on macOS. Broker (paused). Shared `common/` library with messages, keymap, hybrid transport, jitter buffer, QUIC. FFmpeg via PyAV for decode, direct `ffmpeg` subprocess for encode. NvFBC zero-copy capture on NVIDIA hosts. See `.planning/codebase/` for full analysis.

**Network substrate assumption.** Tailscale mesh is expected in the deployment model — users reach their remote servers by tailnet hostname. This drastically simplifies discovery, NAT traversal, and public-internet concerns vs. bare WAN.

## Constraints

- **Tech stack**: Python 3.10+ — already committed, changing languages mid-project is not on the table. Existing investment in `common/` protocol library + Qt client is load-bearing.
- **Color depth**: 10-bit end-to-end is non-negotiable for v1. Grading/finishing dies at 8-bit. Encoder/decoder choices, codec profiles, and capture paths must all preserve 10 bits.
- **Input fidelity**: zero tolerance for pressure/hotkey glitches. Flame's modifier-chord muscle memory (Ctrl+Shift+Alt+letter combos, tablet-side buttons, chorded drag operations) must round-trip perfectly. One dropped modifier during a client grading session is enough to end artist trust.
- **Distribution**: signed + notarized Mac bundle (requires Apple Developer ID), signed RPM for Rocky. No "download the unsigned dmg, right-click → open" shipping in v1.
- **Single maintainer today**: Randy is the only committer. Roadmap should favor scope that one person can ship, plus explicit slots for OSS polish that unlocks contributors.
- **Dependency on Tailscale-style mesh**: v1 documents and tests against Tailscale as the reference WAN substrate. Users on other mesh VPNs (WireGuard, Netbird, Twingate) should work but are not primary test targets.
- **Hardware reality**: author's DXS lab (6 Flame workstations, NVIDIA GPUs on Rocky) is the primary integration testbed. Other studios' hardware profiles drive smoke tests only.
- **No license cost for end users**: project remains open source, freely usable. Monetization (if any) is not a v1 concern.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Scope v1 to Phase 1 + Phase 2 (Mac client + Rocky server + Mac server, no Windows) | Author's actual deployment need is Mac→Rocky and Mac→Mac. Windows is valuable but not load-bearing for DXS or for most small VFX studios. Defer to Phase 3. | — Pending |
| Pause broker hardening for v1 | Broker works in DXS already. Direct client→server PAM over Tailscale covers the small-studio deployment model without broker complexity. Broker can graduate to v1.x later. | — Pending |
| 10-bit end-to-end in v1 (not deferred to v2) | "Indistinguishable from local" includes color accuracy. Deferring 10-bit makes v1 unusable for grading/finishing — the exact workflows PCoIP artists care about. | — Pending |
| Tailscale-first connection model (no broker required for discovery) | Small studios already run Tailscale or similar mesh. Tailnet hostname > rolling our own discovery protocol. Broker handles pool scheduling, which small studios don't need. | — Pending |
| Control-surface USB/IP in v1, license-dongle USB/IP deferred | Control surfaces (Loupedeck, Tangent) are client-side hardware artists physically touch — must forward. Dongles typically live server-side already. | — Pending |
| Per-session multi-monitor mode (single / mirror / pick-one) on connect | Artists use different topologies in different sessions (laptop vs. desktop, client review vs. solo work). Fixing the mode at server config time is wrong. | — Pending |
| Signed + notarized distribution in v1 | "Professional GitHub for small studios" means the download works on a stock Mac without right-click-override. Investing in Apple notarization and RPM signing up front. | — Pending |
| Keep Python 3.10+ and `common/` protocol library | Rewriting in Rust/Go is tempting for performance but throws away working code. Profile first, optimize inner loops in C extensions only if measurements demand it. | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-19 after Phase 2 (Input + Color Fidelity) completion — 12/12 plans, status human_needed (5 DXS hardware checkpoints persisted as 02-HUMAN-UAT.md). Phase 2 delivered 9-checkpoint pipeline fixture (6/9 cps automated GREEN; cp.8 manual-once on MBP XDR; cps 1/9 deferred), QRhi/Metal 10-bit video blit with BT.709 video-range shader replacing the QPainter 8-bit silent downgrade, hevc_nvenc Main10 + p010le on Linux, VTCompressionSession PyObjC wrapper on Mac (dispatch gated off via `_MAC_VIDEO_ENC_AVAILABLE=False` per CR-01 fix; FFmpeg `hevc_videotoolbox` subprocess remains the production Mac encode path until plane copy lands — see deferred-items.md item 7), 4-trigger modifier reset (focusOut + reconnect + F9 panic + periodic safety net) funneled through one idempotent reset_modifiers, per-bookmark Cmd↔Ctrl swap with destination-kind picker, Caps/Num/Scroll lock bits in every KeyEvent, IME TextCommit passthrough, FLAME_CRITICAL_CHORDS integration sweep + dead-key composition, PenFSM + D-19 client proximity re-synth on focusIn/showEvent. INPUT-08 (Mac-server pen pressure) re-scoped as documented v1 limitation per planned D-07 FAIL branch — Flame on Rocky stays the production path. INPUT-09/-10/-11/-12 + VIDEO-11 hardware-verification leg awaits the 4-cell DXS Wacom matrix + pre/post latency comparison. All 24 Phase 2 Active requirements remain ACTIVE pending DXS sign-off.*
