# Feature Research — Teraguchi

**Domain:** High-performance remote workstation for VFX / creative professionals (open-source PCoIP replacement)
**Researched:** 2026-04-18
**Confidence:** HIGH for PCoIP/DCV/Parsec/Sunshine feature lists (vendor docs + community forums); MEDIUM for user complaint patterns (small forum sample); HIGH for prototype cross-reference (direct code read).

## Scope of This Research

The question: *what feature surface must a production remote-workstation tool for 1-10 person VFX studios have?* The author's existing prototype covers a lot of ground — this research validates the prototype's coverage against every credible competitor's feature list, flags gaps the author hasn't considered, and explicitly marks features that would be scope creep for a solo-maintainer project.

Prototype cross-reference key (each feature row below is tagged):
- **HAVE** — working in the existing prototype, cited in `.planning/codebase/ARCHITECTURE.md`
- **PARTIAL** — scaffolded but not production-grade
- **MISSING** — not in prototype
- **OOS** — explicitly out of v1 scope per `.planning/PROJECT.md`

## Feature Landscape

### Table Stakes (Users Expect These — Missing = Product Feels Broken)

These are non-negotiable. PCoIP/DCV/Parsec all ship these; a Flame artist trialing Teraguchi in a client session and hitting a missing one walks away.

| Feature | Why Expected | Complexity | Prototype | Notes |
|---------|--------------|------------|-----------|-------|
| Lossy video streaming (H.264/HEVC) of remote desktop | Foundation of every remote protocol | HIGH | HAVE | Full FFmpeg pipeline: NVENC/VAAPI/VideoToolbox/AMF fallback chain per `server/video_encoder.py`. |
| Hardware-accelerated decode on client | Soft decode on a 4K stream at 60fps cooks laptop CPUs | MEDIUM | HAVE | PyAV with CUDA/VideoToolbox/VAAPI. |
| Keyboard input with full modifier-chord fidelity | Flame/Nuke hotkeys are modifier-heavy; one missed Shift is a day ruined | HIGH | HAVE | `common/keymap.py` + XTest/uinput/CGEvent injector. Prototype does it; productionization is the stability/testing push in v1. |
| Mouse input with pixel-accurate coords | Baseline | LOW | HAVE | `RemoteViewer` + `InputInjector`. |
| Multi-monitor display from server | Finishing suites run 2–3 displays; Resolve uses a dedicated UI/scopes screen | MEDIUM | HAVE | `MonitorListMsg`, monitor picker, per-session mode selection planned. |
| Bidirectional text clipboard | Copying a shot name / path / LUT between client and server is constant | LOW | HAVE | `ClipboardSync` both platforms. |
| Image clipboard (at minimum client → server) | Artists paste reference images into Flame/Nuke | MEDIUM | PARTIAL | Partial per PROJECT.md ("image clipboard (partial)"). |
| Audio playback (server → client) | Review sessions need dialogue/music; Flame timeline scrubbing needs audio feedback | MEDIUM | PARTIAL | `AudioCapture` exists; "Full-duplex low-latency audio" listed as Active v1 work. |
| Audio capture (client → server, mic) | Remote artist speaking into client-side mic needs to reach server-side comms / recorded takes | MEDIUM | PARTIAL | Listed as Active v1. |
| Session persistence across disconnect | Artist's laptop sleeps / WiFi drops mid-comp; work-in-progress must not vanish | MEDIUM | HAVE | `SessionRuntime` keeps running per `.planning/codebase/ARCHITECTURE.md`. PCoIP does this, DCV does this, missing it = deal-breaker. |
| Graceful reconnect after network interruption | Same as above from client POV | MEDIUM | HAVE | TLS WS reconnect + UDP re-probe. |
| Authentication (real, not just passwords in cleartext) | Studios have compliance obligations; PAM on Linux, something equivalent on Mac | MEDIUM | HAVE | PAM + broker token modes. TLS WSS. |
| TLS-encrypted transport | Baseline 2026 expectation | LOW | HAVE | `wss://` with `--tls-cert`/`--tls-key`. |
| File drag-drop client → server | "Give me this plate" is a daily workflow | MEDIUM | HAVE | Chunked `FILE_OFFER`/`FILE_CHUNK` with SHA-256. |
| Bulk file pull server → client | Delivering a quicktime to the client for review without uploading through a web app | MEDIUM | MISSING | Listed as Active v1 ("Bulk file pull server → client (new)"). |
| Wacom pen with pressure | Paint/roto/matte-painting artists cannot work without it; PCoIP/NoMachine both support it | HIGH | HAVE | `QTabletEvent` → pen protocol → injector. DCV and Parsec both advertise this; Moonlight/Sunshine notably DO NOT (see [moonlight issue #1247](https://github.com/moonlight-stream/moonlight-qt/issues/1247), [issue #1540](https://github.com/moonlight-stream/moonlight-qt/issues/1540)). |
| Wacom pen with tilt | Brush dynamics in paint tools (Flame paint, Mischief, Nuke roto) | HIGH | HAVE | Same path as pressure. |
| 10-bit color end-to-end | Grading and finishing die at 8-bit. [Color-critical remote has historically been an unsolved problem](https://blog.frame.io/2020/05/04/workflow-from-home-episode-9-live-remote-color-grading/); Teraguchi's differentiator is delivering it. | HIGH | PARTIAL | Encoders exist (Main10 profiles); needs verified end-to-end pipeline validation. Listed as Active v1. |
| YUV 4:4:4 color option | Text legibility in Flame's dense UI, accurate color for review. Parsec and PCoIP Ultra both offer this as a quality tier. | MEDIUM | HAVE | `supports_yuv444` flag in `ClientHelloMsg`. Encoder definitions include `supports_444`. |
| Quality profiles / bandwidth control | Artist at a hotel on 5Mbps vs artist on fiber need different settings | LOW | HAVE | `QualitySettings` + client quality panel. |
| Adaptive bitrate under network stress | Dropping from 60fps→30fps under congestion is standard ([PCoIP does this](https://anyware.hp.com/products/hp-anyware/2024.07/documentation/session-planning-guide/pcoip-performance-optimization)) | MEDIUM | PARTIAL | `BandwidthEstimator` in transport; policy needs v1 work. |
| Signed, notarized client distribution | A Mac install that requires right-click-to-open has failed; PCoIP/DCV/Parsec are all signed | LOW | MISSING | In Active v1. Requires Apple Developer ID. |
| Install-and-it-works server bootstrapping | `install-server.sh` exists; the bar is RPM + systemd on Rocky with no manual fiddling | MEDIUM | PARTIAL | RPM signing/packaging listed as Active v1. |
| Health / connection-quality visible to user | Is this laggy because of my WiFi or the server? — artist must be able to tell at a glance | LOW | HAVE | `HealthData` + `HealthOverlay` with RTT/FPS/bandwidth. |

### Differentiators (Why An Artist Switches From PCoIP/DCV to Teraguchi)

Not required, but this is where we win vs Parsec/Sunshine (which lack pro features) and vs PCoIP/DCV (which are expensive and/or Linux-only-for-server or AWS-bound).

| Feature | Value Proposition | Complexity | Prototype | Notes |
|---------|-------------------|------------|-----------|-------|
| **Free / open-source** | $0/seat vs PCoIP ~$180/yr/seat, DCV licensing on non-AWS, Parsec Teams $39.99/user/month | N/A | HAVE | The meta-differentiator. |
| **First-class Wacom pressure + tilt over WAN** | Parsec has it (Teams/Warp tier only). Sunshine/Moonlight don't. DCV has "stylus remotization". PCoIP has it but with latency issues in bridged mode per [HP docs](https://anyware.hp.com/knowledge/faqs-peripheral-devices). Teraguchi has it for free on the base tier. | HIGH | HAVE | Already in prototype. Real win: the `common/keymap.py` + `QTabletEvent` path was designed for Flame artists specifically, not gamers. |
| **10-bit end-to-end for grading/finishing** | Parsec is "perfect 8-bit 4:4:4" not 10-bit. PCoIP Ultra does 10-bit but Anyware is EOL. DCV has 10-bit on AWS. Self-hosted 10-bit for an indie shop = no competitor. | HIGH | PARTIAL | Active v1 work. |
| **Self-hostable, Tailscale-native** | No cloud dependency, no account system. Contrast DCV (AWS-native), Parsec (SaaS account model), Anyware (cloud broker). | MEDIUM | HAVE | Broker optional; direct tailnet hostname connection model. |
| **Hardware-efficient NvFBC capture on NVIDIA** | Tear-free zero-copy capture = lower server CPU than VNC/RDP-style X11 grabbers | HIGH | HAVE | `server/nvfbc/nvfbc_capture.c` isolated subprocess per architecture doc. Major technical differentiator most OSS alternatives lack. |
| **Per-user X11 session isolation (Linux)** | 6 Flame artists share 1 GPU host, each gets their own Xorg display. PCoIP does this via Horizon; OSS tools typically don't. | HIGH | HAVE | `XSessionManager` + PAM per architecture doc. |
| **Mac-as-server path** | Parsec and DCV don't ship Mac servers. Sunshine runs on Mac but is gaming-focused. Teraguchi running Flame on a Mac Studio from a MacBook Pro on the road = a workflow no commercial competitor covers. | HIGH | HAVE | Already landed in recent commits (`server/mac_*.py`). |
| **Pluggable transport: WSS + UDP + QUIC** | QUIC path is experimental but present — rare in OSS remote-desktop. PCoIP uses proprietary UDP; DCV uses custom over UDP; nobody OSS has QUIC yet. | HIGH | HAVE | `common/hybrid_transport.py` + `common/quic_transport.py`. |
| **Per-session multi-monitor mode** (single / mirror / pick-one on connect) | DCV has fixed "all monitors" UX. PCoIP has configuration-file-level multi-monitor. Choosing per-session matches how artists actually work (desk vs laptop vs review suite). | MEDIUM | PARTIAL | Client-side monitor picker exists; per-session-on-connect selector is listed as Active v1. |
| **Control-surface (Loupedeck / Tangent) USB/IP forwarding** | Nobody does this well. PCoIP bridges USB but Loupedeck CT and Tangent Element often need vendor driver install on the server side; USB/IP to a Linux session + server-side driver is cleanly solvable. [Loupedeck driver support under Linux exists](https://github.com/scottlaird/loupedeck). Big differentiator for finishing artists. | HIGH | HAVE | `USBForwardingManager` (Linux server only). Listed as Active v1 for Mac server target. |
| **Tabbed multi-session client** | Connect to 2 Flames + 1 grading station in one window. PCoIP has multi-session but separate windows. Jump Desktop has tabs. Great small-studio UX. | LOW | HAVE | `QTabWidget` in `client/main.py`. |
| **Bookmarks / saved sessions with per-host quality** | Save "Home Mac → DXS Flame 03 on Tailscale, quality=high" once; reconnect in two clicks | LOW | HAVE | Bookmark panel in `MainWindow`. |
| **HMAC-token broker with FreeIPA group gating** | Power users running studio infrastructure. Broker is paused for v1 but already works. | HIGH | HAVE | `broker/*`. Deferred per PROJECT.md but a story to tell. |
| **Open-wire-protocol that developers can implement** | OSS alternative client implementations become possible. Think Moonlight ↔ Sunshine ecosystem but for VFX. | LOW | PARTIAL | `common/messages.py` is documented; formal spec listed as Active v1 ("on-the-wire protocol reference"). |

### Quality-of-Life Features (Nice To Have, Low Cost, Real Delight)

Small wins that make the product feel polished. Not deal-makers, but collectively they're why users recommend a tool.

| Feature | Why Valued | Complexity | Prototype | Notes |
|---------|-----------|------------|-----------|-------|
| In-session FPS / bandwidth / RTT overlay (toggle) | "Is this slow because of my network?" — answered in 2 seconds | LOW | HAVE | `HealthOverlay`. |
| Live quality slider (no reconnect) | Adjust bitrate mid-session when WiFi craps out at the coffee shop | LOW | PARTIAL | `apply_quality` exists; per PROJECT.md "Config reload without disconnecting" is an implicit v1 goal. |
| Fullscreen toggle with all hotkeys captured | Flame needs the Cmd / Super key; macOS swallows it in windowed mode | MEDIUM | HAVE | Fullscreen toolbar in `MainWindow`. |
| Drag-drop file transfer (not just a file picker) | Flame's Conform bin expects drag-drop; artists trained on it | LOW | HAVE | `RemoteViewer` drag-drop events. |
| Clipboard image paste | Reference images → Flame batch → Nuke roto | MEDIUM | PARTIAL | Text done; image partial. |
| Automatic tail-matching resolution (client monitor → server render) | Connecting from a 14" MacBook Air to a 3×4K workstation: server renders at a reasonable pixel count | MEDIUM | PARTIAL | `client_screen_width`/`height` sent in `ClientHelloMsg`; actual resize policy is not fully hardened. |
| Session health endpoint (`/status`) for infra monitoring | Studio admin can Prometheus-scrape from Grafana; author already uses dxs-grafana | LOW | HAVE | `handle_http` in `server/main.py`. |
| Cursor shape round-trip (native client-side cursor) | Flame swaps between arrow / crosshair / resize / rotation cursors constantly; rendering them server-side in the video stream looks laggy | MEDIUM | HAVE | `CursorTracker` via XFixes; `QCursor` client-side. Notable technical detail. |
| "Bring your own monitor layout" (flexible on connect) | Artist at home one-monitor can still connect to a 3-monitor workstation; pick one monitor at connect time | MEDIUM | PARTIAL | Per-session multi-monitor mode selector in v1 plan. |
| CI builds for every tagged release | Professional signal; contributors can trust a build came from `main` not the author's laptop | LOW | MISSING | Listed as Active v1 (GitHub Actions). |
| Quickstart + architecture + protocol docs | Without these, no contributors, no studio adoption. | LOW | MISSING | Listed as Active v1. |
| Diagnostic bundle command (`teraguchi diagnose`) | "Run this, paste the output" — dramatically lowers support load | LOW | MISSING | Worth planning; infrastructure already has `/status`, logs, health monitor. |

### Anti-Features (Commonly Requested, Deliberately NOT Building)

These sound good on a feature-comparison spreadsheet but will wreck a solo-maintainer OSS project.

| Feature | Why Requested | Why Problematic For Teraguchi | Alternative |
|---------|---------------|-------------------------------|-------------|
| **Browser / WebRTC client** | "It'd be so cool to just open a URL" — Guacamole, DCV Web do this | WebRTC stack is a career's worth of complexity; browser color is 8-bit sRGB (kills the 10-bit differentiator); no native Wacom pressure in browsers; auth story doubles. | PySide6 native client only. If someone wants browser, use Guacamole. |
| **Mobile client (iPad/Android)** | "Let me at least view my render on iPad" | Different input paradigm, different color story, different auth UX. [Jump Desktop](https://jumpdesktop.com/) already does this well. | Deferred; explicitly OOS per PROJECT.md. |
| **Zero-client hardware** | PCoIP ships this ([Tera2 chipsets](https://anyware.hp.com/web-help/pcoip_zero_client/tera2/26.01/ref_smart_cards/)); some studios love the dedicated hardware | Hardware business model is incompatible with a 1-person OSS project. Any Mac/Linux with the client app fills this role. | Signed Mac app bundle. |
| **Smart-card / CAC authentication** | Enterprise / government use cases | 1-10 person VFX studios don't need CAC. Adds PKCS#11 complexity. | PAM + broker tokens for v1. Leave door open for `auth_methods` protocol extension. |
| **Session recording for compliance** | DCV has it; some legal/VFX contracts require it | Video/audio recording of an artist's session is a privacy minefield, storage burden, legal question. Different tool's job. | OOS per PROJECT.md. Suggest artists use OBS client-side for dailies. |
| **Real-time collaboration / observer mode** ([DCV's collab feature](https://docs.aws.amazon.com/dcv/latest/userguide/managing-sessions-session-collaboration.html)) | "Let me show my supervisor what I'm working on" | Protocol complexity (multi-stream auth, quality negotiation per viewer, permissions model); also a separate workflow from "remote my workstation". [Frame.io already owns review](https://blog.frame.io/2020/05/04/workflow-from-home-episode-9-live-remote-color-grading/). | Use Frame.io, Zoom screen-share, or Tuesday.video for review. |
| **Game controller / gamepad emulation** | Parsec/Sunshine/Moonlight have this | VFX artists don't use gamepads. Dead weight. | Skip. |
| **"Arcade" / discovery directory** (Parsec's public join model) | Consumer gaming UX | Inappropriate for a tool users will remote into their own workstations from | Tailnet hostname / bookmarks. |
| **Printer redirection** | Standard enterprise RDP feature | Nobody in a 1-10 person VFX studio is printing. | Skip. |
| **Drive mapping** (RDP-style shared drive from client to server) | Enterprise RDP expectation | File transfer already solves the real use case. Drive mapping = SMB complexity = security surface. | File transfer. |
| **Full ICC profile pipeline / on-the-fly color transform** | "I want my HDR MacBook Pro to show exactly what the server's HDR reference monitor shows" | A multi-year problem. [No existing remote protocol solves this well for HDR](https://blog.frame.io/2020/05/04/workflow-from-home-episode-9-live-remote-color-grading/). 10-bit-end-to-end + artist doing critical judgments on a calibrated display is the practical answer. | OOS per PROJECT.md. Revisit only when a real grading studio demands it. |
| **USB dongle (iLok / HASP) forwarding** | License dongles for Autodesk, Boris, Sapphire | Dongles typically live on the server already (that's where the software runs). Rare that client-side dongle needs forwarding. Complex driver-side dance. | OOS per PROJECT.md. |
| **Stream Deck / keypad forwarding** | Same category as Tangent/Loupedeck in user mind | Most artists already bind macros on the CLIENT OS (it's a keyboard-emulating HID); no forwarding needed. | Deferred per PROJECT.md. |
| **Built-in VPN / NAT traversal / STUN/TURN relay** | "I want to connect from anywhere without networking setup" | Tailscale already exists and is free for personal; Netbird, Twingate, ZeroTier for studios. Don't rebuild the mesh VPN. | Tailscale-first assumption in PROJECT.md. |
| **License dongle emulation** | "I want to run licensed software remotely" | Legal minefield, technical minefield, not the point of the tool. | Out. |
| **Full PAM password DB management / user CRUD** | "Let me add users from the admin UI" | Broker has rudimentary admin UI; adding full user management replicates FreeIPA/LDAP badly. | Use FreeIPA, or system `useradd`. |
| **Horizon-style pool scheduling / desktop provisioning** | Enterprise VDI territory | Overkill for 1-10 person studios. Broker has pool-ish assignment; that's enough. | Tailnet hostname + per-user auth. |
| **Embedded installer for Flame / Nuke / Resolve** | "Just give me a Flame workstation image" | That's a deployment project, not a remote-desktop project. Ansible/Salt/etc. | Point users at `ansible` repo separately. |

## Feature Dependencies

```
Signed Mac notarized .app
    └──requires──> Apple Developer ID ($99/yr, author responsibility)

10-bit end-to-end video
    ├──requires──> Encoder Main10 profile selection in `video_encoder.py`
    ├──requires──> Decoder pixel format preservation in PyAV path
    ├──requires──> Capture path delivering 10-bit source
    │                └──requires──> NvFBC 10-bit mode on NVIDIA, or X11 DEPTH_30, or SCK 10-bit frame
    └──requires──> Client display hand-off without truncating to 8-bit (QImage format choice)

Wacom pressure + tilt round-trip
    ├──requires──> Qt QTabletEvent capture (client, HAVE)
    ├──requires──> Pen message type in protocol (HAVE)
    └──requires──> Injector implementing pen path per platform
           ├──Linux──> uinput /dev/uinput (HAVE) OR XTest pen extension
           └──macOS──> CGEvent tablet (VERIFY — needs audit vs. rumored gaps)

Control-surface USB/IP forwarding
    ├──requires──> usbip client on Mac (macOS usbip support is thin; VERIFY)
    ├──requires──> USB/IP server on Linux target (HAVE)
    ├──requires──> Driver available on server-side OS for forwarded device
    └──requires──> Matching macOS server path
           └──> CRITICAL GAP: macOS usbip story is not well-trodden; may need alternate forwarding mechanism on Mac-server target

Multi-session client (tabbed)
    └──requires──> One `Session` per tab (HAVE)

Full-duplex audio
    ├──requires──> Server-side capture (PulseAudio monitor / AVFoundation) + mic injection (PulseAudio source / AVFoundation device)
    ├──requires──> Low-latency audio codec (Opus obvious choice; ensure 48kHz end-to-end)
    └──requires──> Client-side bidirectional (QAudioSink + QAudioSource)

Session persistence
    ├──requires──> Server keeps capture/encode pipeline alive post-disconnect (HAVE)
    ├──requires──> Auth session state holds user→runtime mapping (HAVE)
    └──requires──> Client reconnect protocol resuming same SessionRuntime (HAVE)

Bookmarks / saved sessions
    ├──requires──> Local config store on client (HAVE)
    └──requires──> Connection params per bookmark (host/port/quality/monitor-mode)
           └──enhances──> Tailscale-first model (hostname in bookmark is tailnet name)

Per-user X session isolation (Linux)
    ├──requires──> PAM auth (HAVE)
    ├──requires──> XSessionManager spawning per-uid Xorg/Xvfb (HAVE)
    └──requires──> Encoder picking correct display (HAVE)

Signed RPM for Rocky
    └──requires──> Signing key + automated CI build + repo publication story

CI signed releases
    ├──requires──> GitHub Actions with secrets
    ├──requires──> Apple Developer credentials in Actions secrets (notarization)
    └──requires──> RPM signing key in Actions secrets

Real-time collaboration (ANTI-FEATURE, but noting dependency to prove why it's bad)
    ├──would-require──> Multi-client-attach-to-one-runtime (PARTIAL — runtime supports it, protocol doesn't permission-gate it)
    ├──would-require──> Per-client quality negotiation
    ├──would-require──> Observer mode (input disabled per client)
    └──would-require──> Collaboration invite/URL flow (would need account system or share-code)
```

### Dependency Notes

- **10-bit is a pipeline property, not a feature:** every stage (capture, encode, transport, decode, display) has to preserve 10-bit or it's 8-bit. Roadmap should test this end-to-end with a test pattern (ramp detect, no banding) not assume it works because encoders support Main10.
- **Wacom injection on macOS is the riskiest dependency in v1:** prototype has it, but `CGEvent` tablet path is less well-documented than the Linux uinput path. Worth a dedicated validation phase.
- **macOS USB/IP is a grey area:** Linux-to-Linux USB/IP is solid; Mac client → Linux server works via `usbipd` on the client; Mac-as-server (receive USB devices) is not well-trodden. v1 plan calls out control-surface forwarding to Mac server — this might need an alternate approach (vendor SDK pass-through rather than USB/IP).
- **Multi-client-attach (one `SessionRuntime`, multiple `ClientSession`s) is a latent capability:** the runtime supports it. This is either a free differentiator (family-office or supervisor-looks-on use case) or an anti-feature depending on whether we permission-gate and protocol-design for it. Current answer: latent, undocumented, don't turn into a product feature in v1.
- **Session persistence enables everything else:** every "reconnect", "switch networks", "laptop sleep" scenario rides on it. If persistence breaks, all the premium features feel broken.
- **Signed distribution blocks everything else for Mac:** an unsigned/non-notarized app that requires right-click-to-open will drive away every small-studio user in the first 10 seconds. Apple Developer ID is a precondition for v1 ship.

## MVP Definition

### Launch With (v1) — the "indistinguishable from local" promise

v1 is shipped when a Flame artist at DXS and at one external test studio can do a full 8-hour day on a Mac client → Rocky server over Tailscale, and not notice they're remote. Per PROJECT.md Core Value: if that holds, everything else matters; if not, feature count is irrelevant.

- [x] **Video**: H.264 / HEVC / AV1 encode, hardware fallback chain, 10-bit Main10 end-to-end (HAVE; 10-bit ACTIVE v1)
- [x] **Audio**: full-duplex low-latency Opus (server↔client) — PARTIAL, ACTIVE v1
- [x] **Input**: keyboard / mouse / Wacom pressure+tilt with zero modifier drops in 8-hour session — HAVE, needs hardening
- [x] **Multi-monitor**: per-session mode selector (single / mirror / pick-one) — PARTIAL, ACTIVE v1
- [x] **Clipboard**: bidirectional text + image — PARTIAL, ACTIVE v1
- [x] **Files**: drag-drop client→server + bulk pull server→client — PARTIAL, ACTIVE v1
- [x] **USB forwarding**: Loupedeck / Tangent control surfaces from Mac client to Rocky server AND Mac server — PARTIAL, ACTIVE v1
- [x] **Session persistence**: Mac client disconnect → reconnect without losing state — HAVE
- [x] **Auth**: direct PAM to server (broker bypass path) — HAVE
- [x] **Transport**: TLS WS + UDP media, QUIC as production option, LAN sub-20ms, Tailscale WAN — PARTIAL, ACTIVE v1
- [x] **Distribution**: signed + notarized Mac `.app`, signed RPM for Rocky — MISSING, ACTIVE v1
- [x] **Observability**: FPS/RTT/bandwidth overlay, quality slider live — HAVE
- [x] **Project polish**: README + ARCHITECTURE + PROTOCOL docs, CI, LICENSE/CONTRIBUTING/SECURITY — MISSING, ACTIVE v1

### Add After Validation (v1.x) — contingent on v1 traction

Add these only if v1 has real adopters asking for them. Don't pre-build.

- [ ] **Windows client** — Phase 3 of author's roadmap. Installer scaffolding stays; signed build happens when Windows users materialize.
- [ ] **Windows server** — same trigger.
- [ ] **Linux client** (packaged) — the PySide6 client runs on Linux; adding signed `.deb` / `.rpm` is low effort when someone asks.
- [ ] **Broker hardening + FreeIPA admin UI** — graduate from "paused" once a small studio wants pool scheduling.
- [ ] **Session recording for dailies export** (OPTIONAL, client-side only — host-side violates anti-features) — client can opt-in record its own received video to MP4.
- [ ] **Prometheus `/metrics` endpoint** — `/status` exists; Prometheus format is a small delta.
- [ ] **License dongle USB/IP** — deferred per PROJECT.md; add if a studio actually needs it.
- [ ] **ICC profile hand-off** (informational, not transforming) — send client display profile to server so color tools know. Not doing transforms.

### Future Consideration (v2+)

- [ ] **Native 10-bit HDR pipeline** — when the ecosystem (Resolve/Flame/Nuke) has a clean HDR remote story, not before.
- [ ] **Real-time multi-viewer / observer mode** — ONLY if v1 users ask and anti-feature reasoning no longer holds. Likely remains anti-feature.
- [ ] **Stream Deck / MIDI control surface forwarding** — deferred per PROJECT.md.
- [ ] **Alternate-client ecosystem** (e.g. iOS viewer-only) — only if the protocol has stabilized and someone else writes it.
- [ ] **GPU passthrough VDI use case** (multiple artist sessions on one GPU-heavy host with scheduler) — enterprise territory; requires broker graduation.

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Stability hardening (no crashes in 8hr) | HIGH | HIGH | P1 |
| 10-bit end-to-end validation | HIGH | MEDIUM | P1 |
| Wacom pressure+tilt production-grade (no dropped events) | HIGH | MEDIUM | P1 |
| Signed + notarized Mac .app | HIGH | LOW (process) | P1 |
| Signed RPM for Rocky | HIGH | LOW (process) | P1 |
| Per-session multi-monitor mode selector | HIGH | MEDIUM | P1 |
| Full-duplex low-latency audio | HIGH | MEDIUM | P1 |
| Control-surface USB/IP (Loupedeck/Tangent), Mac+Linux server | HIGH | HIGH | P1 |
| Bulk file pull server → client | MEDIUM | LOW | P1 |
| Image clipboard | MEDIUM | MEDIUM | P1 |
| QUIC production hardening | MEDIUM | HIGH | P1 |
| Network-adaptive bitrate policy | HIGH | MEDIUM | P1 |
| Diagnostic bundle command | MEDIUM | LOW | P2 |
| TCC onboarding UX for Mac server | MEDIUM | MEDIUM | P1 |
| Protocol reference doc | MEDIUM | LOW | P1 |
| GitHub Actions CI (Mac + Rocky) | MEDIUM | MEDIUM | P1 |
| Broker hardening | LOW (for v1) | HIGH | P3 |
| Windows client/server | LOW (for v1) | HIGH | P3 |
| Linux client packaging | LOW | LOW | P2 |
| Session-recording (client-side, optional) | LOW | MEDIUM | P3 |
| Real-time observer mode | LOW | HIGH | P3 (anti-feature) |
| Browser client | LOW | HIGH | P3 (anti-feature) |
| Mobile client | LOW | HIGH | P3 (anti-feature) |

**Priority key:**
- **P1**: Must have for v1 launch per PROJECT.md Active section
- **P2**: Should have when a low-cost opportunity appears
- **P3**: Defer; either post-v1 or permanently anti-feature

## Competitor Feature Analysis

High-level feature coverage. **Y** = yes, production-quality. **~** = partial / tier-gated / gotchas. **N** = no / broken.

| Feature | PCoIP (HP Anyware) | NICE DCV | Parsec | Sunshine+Moonlight | Guacamole | NoMachine | Teraguchi (v1 target) |
|---------|:------------------:|:--------:|:------:|:------------------:|:---------:|:---------:|:---------------------:|
| Open source | N | N | N | Y | Y | ~ (NX 1 GPL2, modern closed) | **Y** |
| Self-hostable, no vendor cloud | ~ (broker optional) | Y | N (SaaS required) | Y | Y | Y | **Y** |
| Linux server | Y | Y | N (Windows-first) | Y (Linux+Windows+Mac) | Y (proxy to Linux via RDP/VNC/SSH) | Y | **Y** |
| macOS server | N | N | N | Y (limited) | N | Y | **Y** |
| Windows server | Y | Y | Y | Y | Y (via RDP) | Y | N (Phase 3) |
| macOS client | Y | Y | Y | Y | Y (browser) | Y | **Y** |
| Linux client | Y | Y | ~ | Y | Y (browser) | Y | ~ (unpackaged) |
| H.264 encode | Y | Y | Y | Y | Y (via protocols) | Y | **Y** |
| HEVC / H.265 | Y (Ultra) | Y | Y | Y | Limited | Y | **Y** |
| AV1 encode | N | ~ | N | Y (recent) | N | N | **Y** |
| 10-bit color end-to-end | Y (Ultra) | Y | N (8-bit 4:4:4 only) | ~ (YUV 4:4:4 recent, HDR limited) | N | ~ | **Y** (ACTIVE) |
| YUV 4:4:4 | Y (Ultra, software) | Y | Y (paid tiers) | Y (Intel+NVIDIA) | N | ~ | **Y** |
| HDR | ~ (roadmap) | ~ | N | Y (host-side) | N | N | N (v2+) |
| Multi-monitor | Y | Y (up to 4×4K) | Y | Y | N (primary only) | Y | **Y** |
| Wacom pressure | Y (local-term mode best) | Y | Y (Teams/Warp tier) | N | ~ (via RDP) | Y (via USB/IP) | **Y** |
| Wacom tilt | Y | Y | Y | N | N | Y (via USB/IP) | **Y** |
| Control-surface USB forwarding | Y (USB bridge) | Y (USB redirection) | N | N | N | Y (USB/IP) | **Y** (target) |
| License dongle forwarding | Y | Y | ~ | N | N | ~ | N (OOS) |
| Smart card auth | Y | Y | N | N | ~ | N | N (anti) |
| File transfer | Y | Y | Y | N (limited) | Y | Y | **Y** |
| Clipboard (text) | Y | Y | Y | N | Y | Y | **Y** |
| Clipboard (image) | Y | Y | ~ | N | ~ | Y | ~ (ACTIVE) |
| Full-duplex audio | Y | Y | Y (host audio only for gaming; mic in paid tier) | Y | Limited | Y | **Y** (ACTIVE) |
| Session persistence | Y | Y | ~ | Y | Y | Y | **Y** |
| Adaptive bitrate | Y | Y | Y | Y | ~ | Y | **Y** |
| FEC / packet-loss resilience | Y (UDP + error concealment) | Y (QUIC 2024+) | Y (BUD — best-effort UDP) | ~ | N (TCP only) | Y | ~ (QUIC experimental) |
| QUIC transport | N | Y | N | N | N | N | **Y** (exp → prod) |
| Bookmarks / saved sessions | Y | ~ (via URL) | Y | Y (Moonlight) | Y | Y | **Y** |
| Tabbed multi-session | N (separate windows) | N | N | N | ~ (browser tabs) | Y | **Y** |
| Session recording | ~ (third-party) | Y (2022+) | N | N | ~ | ~ | N (anti) |
| Collaboration / observer | N | Y | Y (couch co-op) | N | N | N | N (anti) |
| Browser / WebRTC client | N | Y (DCV Web) | N | N (but web client exists third-party) | Y (primary model) | Y | N (anti) |
| Mobile client | Y (iPad, Android) | Y (iOS, Android) | Y | Y | Y (browser) | Y | N (OOS) |
| Zero-client hardware | Y (Tera2) | N | N | N | N | N | N (anti) |
| License cost per seat (approx 2026) | ~$180/yr | $0 on AWS, ~$150/yr off-AWS | Warp $39.99/user/mo, Teams tiered | $0 | $0 | Free up to 2, Enterprise $$ | **$0** |
| OSS license | n/a | n/a | n/a | GPLv3 (Sunshine) | Apache 2.0 | Closed | TBD (Apache 2 / MIT / AGPL choice per PROJECT.md not yet stated) |
| Gaming-optimized controller input | ~ | Y | Y | Y | N | N | N |
| Real-time multi-user collab for gaming | N | Y | Y (couch co-op) | Y | N | N | N |

### Feature Comparison Takeaways

1. **Nobody below DCV pricing does 10-bit end-to-end, Wacom pressure+tilt, control-surface USB forwarding, and self-hostable open source.** That's the exact gap Teraguchi occupies.
2. **Sunshine/Moonlight are the closest OSS architectural cousin**, but they have zero pen tablet support per [Moonlight issue #1247](https://github.com/moonlight-stream/moonlight-qt/issues/1247) and [#1540](https://github.com/moonlight-stream/moonlight-qt/issues/1540), and no production 10-bit story. They're optimized for gamepads and 4:2:0, not Flame.
3. **Parsec is the closest feature cousin** (Wacom, 4:4:4, multi-monitor), but closed-source, SaaS-dependent, Windows-server-only, 8-bit only, expensive per-user.
4. **DCV is the closest feature peer** for color work, but AWS-native, closed, and for non-AWS deployments requires NI-SP licensing. No Mac server.
5. **PCoIP's EOL is the window.** HP Anyware new sales end [May 7, 2026](https://anyware.hp.com/knowledge/teradici-workstation-access-software-end-of-life-eol); every small VFX studio on PCoIP needs a migration path by Oct 31, 2029. ThinLinc, Arch Platform, Splashtop, DCV are fighting for enterprise customers. **The 1-10 person studio segment is underserved.**
6. **The prototype already covers most of the table-stakes features.** v1 is about hardening, signing, documenting, and closing the last few gaps — not a new feature blitz.

## Real User Pain Points (Things Teraguchi Must NOT Reproduce)

These come from PCoIP / HP Anyware community forums, AWS DCV discussions, Logik Forums (the Flame community), and Moonlight/Sunshine issue trackers. Sources in each row.

| Pain | Source | Teraguchi's response |
|------|--------|----------------------|
| Wacom lag in "bridged" mode when RTT > 25ms | [HP Anyware peripheral FAQ](https://anyware.hp.com/knowledge/faqs-peripheral-devices) | Direct pen protocol (not USB/IP bridged), with coordinates round-tripping in the control channel, not through a USB proxy. Already in prototype. |
| Audio lip-sync drift at 30fps due to 100ms audio buffer | [HP Anyware lip sync doc](https://anyware.hp.com/knowledge/poor-audio-lip-sync-with-hp-anyware) | Opus with small buffer (10–20ms), adaptive jitter buffer. Test explicit A/V sync on timeline scrub. |
| Blurry screen when bandwidth-constrained | [PCoIP troubleshooting](https://anyware.hp.com/knowledge/troubleshooting-pcoip-display-issues) | Artist-facing quality slider + visible network stats so they understand what's happening; don't silently blur. |
| Session disconnect after 15min idle | Old Teradici Omnissa KB | Our session persistence keeps the runtime alive. Configurable idle timeout, defaults generous (1hr+). |
| Multi-touch auto-forwarded USB misaligned on multi-monitor | HP Anyware release notes | Native multi-monitor protocol + explicit device forwarding, not auto-USB-grabbing of touchscreens. |
| PCoIP on Rocky Linux: "won't allow clicking the Desktop GUI" | [Logik Forums thread](https://forum.logik.tv/t/pcoip-wont-allow-clicking-the-desktop-gui-on-rocky/9092) | Rocky + Xorg with NVIDIA is the primary test environment. Smoke-test the artist-login → click-desktop → open-Flame path in CI. |
| Flame hotkey issues on PCoIP | [Logik Forums: pcoip and flame hotkeys](https://forum.logik.tv/t/pcoip-and-flame-hotkeys/6247) | Use Flame as an integration test corpus. Every Flame modifier chord must round-trip. Ship a `tools/test-hotkey-fidelity.py` that fires every modifier combination through the protocol and asserts scancode equivalence. |
| Moonlight: no Wacom pen support | [moonlight-qt#1247](https://github.com/moonlight-stream/moonlight-qt/issues/1247), [#1540](https://github.com/moonlight-stream/moonlight-qt/issues/1540), [#332](https://github.com/moonlight-stream/moonlight-qt/issues/332) | Our differentiator. Already in prototype. |
| Parsec 4:4:4 requires paid tier, and still only 8-bit | [Parsec Wacom article](https://support.parsec.app/hc/en-us/articles/32381684728852-Using-Wacom-Tablets) | Ours is free, and 10-bit. |
| DCV requires AWS or paid NI-SP license off AWS | [NI-SP DCV VFX page](https://www.ni-sp.com/products/nice-dcv-vfx/) | Ours is self-hosted, no license server. |
| HP Anyware is EOL, existing customers need a path | [HP lifecycle announcement](https://anyware.hp.com/knowledge/teradici-workstation-access-software-end-of-life-eol) | Target precisely this migrating population. Write a "migrating from PCoIP" doc. |
| Apache Guacamole: no multi-monitor on RDP | [GUACAMOLE-288](https://issues.apache.org/jira/browse/GUACAMOLE-288) | We have native multi-monitor in the protocol. |
| Remote color-critical work historically unsolved | [Frame.io "Remote Color Grading" post](https://blog.frame.io/2020/05/04/workflow-from-home-episode-9-live-remote-color-grading/) | 10-bit end-to-end + calibrated client display is the practical answer. Document that it's not magical HDR transforms. |
| NoMachine Wacom requires USB/IP, not native pen protocol | [NoMachine KB](https://kb.nomachine.com/AR07R01094) | We ship native pen protocol (lower latency, no vendor driver dance). |

## Implementation Risks Specific To Feature Set

- **10-bit pipeline verification**: encoder claims support, decoder claims support, but end-to-end 10-bit is rarely tested. Ship a ramp-gradient test pattern + banding detector in `tools/` and run in CI.
- **Wacom on macOS server**: CGEvent tablet events are less documented than Linux uinput. Audit `server/mac_input_injector.py` carefully against real Wacom hardware + Flame/Painter tests.
- **USB/IP on macOS**: Mac-as-server receiving USB/IP devices is not a well-paved path. Might need to forward via libusb / IOKit instead. Worth a 2-day spike before committing v1 scope.
- **Signed/notarized Mac build**: first-time Apple notarization adds 1-3 days plus recurring CI plumbing; underestimate at your peril.
- **LaunchAgent lifecycle on Mac server**: Mac server packaging is 5 commits old per git log; it works but hasn't been stress-tested through sleep/wake cycles, multiple users, FileVault boot.
- **Testing modifier-chord fidelity end-to-end**: need an actual Flame workstation + actual Wacom + actual timing harness. Cannot test this in cloud CI alone.

## Sources

**Competitor product documentation (HIGH confidence):**
- [HP Anyware peripheral FAQ](https://anyware.hp.com/knowledge/faqs-peripheral-devices)
- [HP Anyware PCoIP Zero Client Firmware 26.01 Smart Cards guide](https://anyware.hp.com/web-help/pcoip_zero_client/tera2/26.01/ref_smart_cards/)
- [HP Anyware Lifecycle / EOL announcement](https://anyware.hp.com/lifecycle/hp-anyware)
- [Teradici Workstation Access Software EOL](https://anyware.hp.com/knowledge/teradici-workstation-access-software-end-of-life-eol)
- [PCoIP Performance Optimization](https://anyware.hp.com/products/hp-anyware/2024.07/documentation/session-planning-guide/pcoip-performance-optimization)
- [Poor audio lip sync with HP Anyware](https://anyware.hp.com/knowledge/poor-audio-lip-sync-with-hp-anyware)
- [Troubleshooting PCoIP Display Issues](https://anyware.hp.com/knowledge/troubleshooting-pcoip-display-issues)
- [PCoIP Ultra: Setting New Boundaries for Image Quality](https://connect.teradici.com/blog/image-quality)
- [PCoIP Ultra overview](https://www.teradici.com/pcoip-technology/pcoip-ultra)
- [PCoIP session disconnect timeout](https://anyware.hp.com/knowledge/what-is-the-session-disconnect-timeout-for-a-pcoip-session)
- [Amazon DCV — What is DCV](https://docs.aws.amazon.com/dcv/latest/adminguide/what-is-dcv.html)
- [NICE DCV 2022.0 release notes](https://www.ni-sp.com/23-2-2022-nice-releases-dcv-2022-0-including-new-features/)
- [NICE DCV VFX product page](https://www.ni-sp.com/products/nice-dcv-vfx/)
- [NICE DCV vs Parsec (NI-SP)](https://www.ni-sp.com/nice-dcv-vs-parsec/)
- [DCV session collaboration guide](https://docs.aws.amazon.com/dcv/latest/userguide/managing-sessions-session-collaboration.html)
- [Parsec drawing tablet support](https://parsec.app/blog/yes-parsec-supports-drawing-tablets-92f5d6bf548b)
- [Parsec Wacom tablets KB](https://support.parsec.app/hc/en-us/articles/32381684728852-Using-Wacom-Tablets)
- [Parsec virtual displays](https://support.parsec.app/hc/en-us/articles/32381733729044-Multiple-Monitors-and-Virtual-Displays)
- [Sunshine release notes (LizardByte)](https://github.com/LizardByte/Sunshine/releases)
- [Moonlight PC streaming guide](https://moonlight-stream.org/)
- [Apache Guacamole architecture](https://guacamole.apache.org/doc/gug/guacamole-architecture.html)
- [Guacamole multi-monitor tracking issue](https://issues.apache.org/jira/browse/GUACAMOLE-288)
- [Jump Desktop changelog](https://changelog.jumpdesktop.com)
- [NoMachine graphics tablet KB](https://kb.nomachine.com/AR07R01094)
- [NX protocol on Wikipedia](https://en.wikipedia.org/wiki/NX_technology)
- [Xpra project home](https://xpra.org/)

**Community forums / real user pain (MEDIUM confidence):**
- [Logik Forums: Remote](https://forum.logik.tv/t/remote/14357)
- [Logik Forums: DCV setup](https://forum.logik.tv/t/dcv-setup/13855)
- [Logik Forums: PCoIP Flame hotkeys](https://forum.logik.tv/t/pcoip-and-flame-hotkeys/6247)
- [Logik Forums: PCoIP won't allow clicking desktop on Rocky](https://forum.logik.tv/t/pcoip-wont-allow-clicking-the-desktop-gui-on-rocky/9092)
- [Rocky Linux forum: Open source remote desktop alternatives](https://forums.rockylinux.org/t/fast-remote-desktops-open-source-alternatives-teradici-hp-rgs-hp-anywhere-nice-dcv/14202)
- [Moonlight issue #1247 (pen pressure)](https://github.com/moonlight-stream/moonlight-qt/issues/1247)
- [Moonlight issue #1540 (pen tablets support)](https://github.com/moonlight-stream/moonlight-qt/issues/1540)
- [Moonlight issue #332 (Wacom on Linux)](https://github.com/moonlight-stream/moonlight-qt/issues/332)

**Industry context (MEDIUM confidence):**
- [Frame.io: Live Remote Color Grading (Workflow From Home #9)](https://blog.frame.io/2020/05/04/workflow-from-home-episode-9-live-remote-color-grading/)
- [Netflix Partner Help: Remote Color Grading and Reviews](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360053935833-Remote-Color-Grading-and-Reviews)
- [HP Anyware alternative (Cendio ThinLinc analysis)](https://www.cendio.com/blog/hp-anyware-alternative/)
- [Splashtop blog: HP Anyware End of Life](https://www.splashtop.com/blog/hp-anyware-end-of-life)
- [Arch Platform: HP Anyware alternative](https://www.archpt.io/arch-platform-hp-anyware)
- [MASV: Top Remote Desktop Software for Video Editing](https://massive.io/gear-guides/remote-desktop-software-for-video-editing/)

**Teraguchi prototype cross-reference (HIGH confidence — direct code read):**
- `.planning/PROJECT.md`
- `.planning/codebase/ARCHITECTURE.md`

---
*Feature research for: open-source VFX remote-workstation tool*
*Researched: 2026-04-18*
