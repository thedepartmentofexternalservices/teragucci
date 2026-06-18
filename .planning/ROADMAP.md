# Teraguchi v1 Roadmap

**Milestone:** v1.0 — Production-grade Mac client + Rocky Linux server + macOS server
**Granularity:** standard (7 phases)
**Coverage:** 108/108 v1 requirements mapped (100%)
**Generated:** 2026-04-18 from REQUIREMENTS.md + research/SUMMARY.md

## Core Value (drives every phase's success criteria)

> A Flame artist can work an 8-hour client session remotely and not notice they're remote.
> Input latency, color accuracy, and reliability all match sitting in front of the machine.

This is brownfield. A working vibe-coded prototype already covers the happy path end-to-end on Mac→Rocky and Mac→Mac. Each phase below is **hardening existing code plus closing a missing piece**, not building from zero.

## Phases

- [x] **Phase 1: Stability + CI + Test Baseline** — Fix the critical bugs (`send_queue`, TLS-off, monolithic `main.py`), add CI, add pytest, instrument observability so all later phases have a measurement substrate (completed 2026-04-19)
- [x] **Phase 2: Input + Color Fidelity** — 10-bit end-to-end verified, IOHIDUserDevice pen pressure on Mac server, modifier-chord and Wacom round-trips proven against real hardware (completed 2026-04-19; HUMAN-UAT pending DXS hardware checkpoints)
- [ ] **Phase 3: Display + Multi-Monitor + Clipboard** — Per-session monitor mode selector, mixed-DPI math, hot-plug handlers, image clipboard
- [ ] **Phase 4: Audio** — Opus 48kHz full-duplex on both server platforms, mac_audio_capture, watchdog + 60-min continuity
- [ ] **Phase 5: Network, Transport, Side-Channels** — QUIC promoted to primary, FEC, SESSION_RESUME, file transfer hardening, USB/IP control surfaces (incl. mac-server spike)
- [ ] **Phase 6: Distribution + Security Hardening** — Signed/notarized .app, signed RPM, TLS pinning + Tailscale-native certs, keychain bookmarks, security audit, TCC onboarding UX
- [ ] **Phase 7: OSS Polish + Governance** — Quickstart README, on-the-wire protocol reference, demo video, LICENSE / CONTRIBUTING / SECURITY / CODE_OF_CONDUCT, second-committer invite

## Phase Details

### Phase 1: Stability + CI + Test Baseline

**Goal**: Make the existing prototype trustworthy. Fix the bugs that invalidate every other phase's measurements (queue depth, TLS-off, no tests, no CI), and instrument observability so subsequent phases can prove their own success criteria.

**Critical-path note**: Three blockers documented in research/SUMMARY.md must clear here before any other phase has meaning:
1. `server/main.py` `send_queue(maxsize=30)` → `maxsize=4` with request-IDR-on-drop (otherwise every latency / "smooth playback" claim is a lie)
2. `ssl.CERT_NONE` everywhere in client/broker/QUIC/aiohttp (otherwise every "secure" claim later is a lie — addressed via SEC-01 here; full security hardening lives in Phase 6)
3. Zero tests, zero CI (otherwise every refactor in phases 2-7 ships regressions silently)

**Depends on**: Nothing. This phase IS the foundation.
**Requirements**: STAB-01, STAB-02, STAB-03, STAB-04, STAB-05, STAB-06, STAB-07, STAB-08, STAB-09, SEC-01, OBS-01, OBS-02, OBS-03, OBS-05
**Success Criteria** (what must be TRUE):
  1. `pytest` runs green locally and in GitHub Actions CI on every PR (Mac runner + Rocky 9 container), covering `common/` (messages, keymap, transport, jitter buffer), server auth/tokens, and client bookmarks
  2. The 8-hour smoke session harness completes with zero crashes, zero memory leaks, no audio/video degradation, and zero modifier-stuck events — and the harness itself runs in CI nightly
  3. A single dropped video frame triggers an IDR within one frame interval (fixes the 2-second GOP-stall bug); `send_queue` depth is bounded at 4 and instrumented
  4. Per-stage latency breakdown is visible in the client health overlay (capture / encode / transmit / decode / display) using structlog JSON traces — measurable, not anecdotal
  5. TLS certificate verification is ON by default in client/broker/QUIC/aiohttp probes (full Tailscale-cert + TOFU + corporate-CA story lands in Phase 6, but `CERT_NONE` is removed here)
**Plans**: 17 plans
  - [x] 01-01-PLAN.md — STAB-04 send_queue fix (maxsize=4 + IDR-on-drop) + regression tests
  - [x] 01-02-PLAN.md — SEC-01 remove ssl.CERT_NONE at all 4 sites + real-CA integration tests
  - [x] 01-03-PLAN.md — pyproject.toml + requirements-dev.txt toolchain (pytest, ruff, mypy, Python 3.12 floor, Apache-2.0 fix)
  - [x] 01-04-PLAN.md — STAB-01 critical-path unit tests (common/, auth, tokens, bookmarks + T-1-02/T-1-03 guards)
  - [x] 01-05-PLAN.md — STAB-02/STAB-03 GitHub Actions CI (ci.yml + build-artifacts.yml + RPM spec dry-run)
  - [x] 01-06-PLAN.md — OBS-01 structlog + error taxonomy + ProtocolErrorMsg + redaction (T-1-04)
  - [x] 01-07-PLAN.md — STAB-06 ClientFSM + ServerFSM + ALLOWED_PAIRS in common/session_fsm.py
  - [x] 01-08-PLAN.md — STAB-06 HealthPing/HealthPong state serialization + FSM wiring into server + client
  - [x] 01-09-PLAN.md — STAB-05 prep: characterization tests (server bootstrap + VideoEncoder mock)
  - [x] 01-10-PLAN.md — STAB-05 server decomp 1/2: StreamLoop + HealthLoop + EncoderLifecycle + MonitorHotplug
  - [x] 01-11-PLAN.md — STAB-05 server decomp 2/2: SessionRuntime + ClientSession + thin __main__.py
  - [x] 01-12-PLAN.md — STAB-05 client decomp + STAB-08 ConnectionSupervisor + reconnect integration
  - [x] 01-13-PLAN.md — STAB-07 bounded pipeline queues (CaptureQueue, EncoderQueue, InputQueue audit)
  - [x] 01-14-PLAN.md — OBS-02/OBS-03 per-stage latency + keyframe telemetry + clock-offset helper
  - [x] 01-15-PLAN.md — OBS-05 diagnostic bundle (T-1-05 redaction regression guard)
  - [x] 01-16-PLAN.md — D-08/D-09 synthetic latency benchmark (p99 < 25 ms gate) + CI job
  - [x] 01-17-PLAN.md — STAB-09 1-hour synthetic smoke harness + nightly GHA workflow (D-15/D-16/D-17/D-18)
**Size**: L
**UI hint**: no

---

### Phase 2: Input + Color Fidelity

**Goal**: Deliver the user-visible promise of the project — 10-bit color end-to-end with no silent downgrades, full-fidelity Wacom pressure/tilt/eraser/proximity round-trip, and zero modifier-chord mangling on Flame's brutal hotkey workflows. This is where Teraguchi earns the "indistinguishable from local" claim.

**Pre-phase spike** (2 days, blocks the rest of Phase 2):
- IOHIDUserDevice feasibility on macOS — replace `mac_input_injector.pen_event` (which silently drops `NSEvent.pressure`) with a virtual HID tablet device. Community-documented (wacom-qemu pattern) but not Apple-blessed. If the spike fails, Mac-server pen pressure is a known-broken v1 limitation and we re-scope INPUT-08.

**In-phase hardware session** (1 day):
- Real Wacom hardware matrix: Intuos Pro Large + Cintiq Pro 24, macOS Sonoma + Sequoia. Required to validate INPUT-09 / INPUT-10 / INPUT-11 / INPUT-12 against physical tablets (not synthesized events).

**Depends on**: Phase 1 (need CI green to assert non-regression on each fix; need observability to measure 10-bit assertions)
**Requirements**: INPUT-01, INPUT-02, INPUT-03, INPUT-04, INPUT-05, INPUT-06, INPUT-07, INPUT-08, INPUT-09, INPUT-10, INPUT-11, INPUT-12, VIDEO-01, VIDEO-02, VIDEO-03, VIDEO-04, VIDEO-05, VIDEO-06, VIDEO-07, VIDEO-08, VIDEO-09, VIDEO-10, VIDEO-11, VIDEO-12
**Success Criteria** (what must be TRUE):
  1. A bit-exact 10-bit ramp test pattern round-trips capture → encode → transport → decode → display with no banding (CI fixture asserts byte-equality on the bottom 2 bits at 9 known pipeline checkpoints; one regression fails CI)
  2. The "crazy Flame hotkey combos" integration test — every modifier chord (Ctrl+Shift+Alt+letter, Cmd↔Ctrl swap, Caps Lock state, dead-keys on US/UK/DE/JP layouts) — passes against both a real Rocky server and a real Mac server with zero mangling
  3. Wacom pen pressure, tilt, eraser, tablet-side buttons, and proximity events round-trip with sub-1% pressure quantization error on Intuos Pro Large AND Cintiq Pro 24, on macOS Sonoma AND Sequoia (4-cell hardware matrix passing)
  4. Input-to-photon latency on LAN measures sub-20ms with the Phase-1 instrumentation; CI benchmark fails the build if the number drifts above 25ms
  5. Modifiers release cleanly on `WindowDeactivate` (no more stuck-Ctrl after Cmd+Tab) and on reconnect — verified by automated focus-stress and reconnect-stress tests
**Plans**: 12 plans
  - [x] 02-01-PLAN.md — Wave 0 gap closure: 10-bit ramp fixture, 8 test skeletons, pytest markers, CI wiring
  - [x] 02-02-PLAN.md — common/messages.py extensions: KEY_RESET_MODIFIERS / TextCommit / PenProximity / KeyEvent lock bits / ServerColorCaps
  - [x] 02-03-PLAN.md — common/keymap.py Qt→Mac VK + Cmd↔Ctrl swap + FLAME_CRITICAL_CHORDS + ~2000-case exhaustive matrix
  - [x] 02-04-PLAN.md — server/capability_probe.py + ENCODER_DEFS main10/422/444 flags + client 10-bit badge
  - [x] 02-05-PLAN.md — server/mac_video_encoder.py VTCompressionSession wrapper + SCK .hdrLocalDisplay config
  - [x] 02-06-PLAN.md — Linux hevc_nvenc Main10 command line + NvFBC 10-bit surface + 9-checkpoint cp.2-4
  - [x] 02-07-PLAN.md — client/viewer.py QRhiWidget Metal P010 blit + BT.709 shader (biggest client-side change)
  - [x] 02-08-PLAN.md — client/video_decoder.py P010 format assertion + hw_backend property + docs/build-pyav-macos.md
  - [x] 02-09-PLAN.md — modifier discipline: 4 release-all triggers + bookmark swap UI + Caps/Num/Scroll + TextCommit + xset r off
  - [x] 02-10-PLAN.md — INPUT-08 IOHIDUserDevice spike + mac_pen_injector (branches on D-07 outcome) + PenFSM + proximity re-synth
  - [x] 02-11-PLAN.md — INPUT-04 crazy-hotkeys integration + Wacom setup tab with TCC detection (D-20)
  - [x] 02-12-PLAN.md — 4-cell Wacom matrix + RMS analysis + DXS pre/post latency measurement + docs/release.md seed
**Size**: L
**UI hint**: no

---

### Phase 3: Display + Multi-Monitor + Clipboard

**Goal**: Match how Flame artists actually use displays — different topologies for client review (mirror), solo work (single fullscreen), and laptop-on-the-go (pick-one). Plus the clipboard / image clipboard / per-direction toggle UX that artists expect.

**Pre-phase spike** (1 day):
- Real mixed-DPI hardware session (Retina MBP + external non-Retina monitor): validate cursor-coord math (DISP-03), per-monitor fullscreen (DISP-07), mixed-DPI rendering (DISP-05). The "client has Retina internal + external 4K, server has 2× Xorg screens" topology is exactly where the bugs live.

**Depends on**: Phase 2 (cursor coord math depends on a clean input pipeline; clipboard depends on Phase 1's bounded queues for the side channel)
**Requirements**: DISP-01, DISP-02, DISP-03, DISP-04, DISP-05, DISP-06, DISP-07, CLIP-01, CLIP-02, CLIP-03
**Success Criteria** (what must be TRUE):
  1. On connect, the user picks single-monitor / mirror-all / pick-one and the chosen mode behaves correctly across all server display configurations (1, 2, or 3 monitors); switching modes mid-session is explicitly unsupported and the UI says so
  2. Hot-plugging a monitor on either side mid-session does not crash the server or the client; the session continues and the user sees a remap UI (not a wedged stream)
  3. Cursor coordinates land on the correct pixel on a Retina-MacBook + external-4K client driving a 2×2560×1600 Rocky NVIDIA Xorg server — verified on real hardware
  4. Bidirectional text clipboard handles >1MB pastes and CRLF/LF/CR line endings without corruption; bidirectional image clipboard moves PNG and JPEG screenshots either direction; per-direction toggle UI lets the user disable client→server paste for privacy
  5. CustomEDID for Xvfb sessions advertises a Flame-approved monitor model so Flame's monitor-config dialog stops complaining
**Plans**: 7 plans
  - [x] 03-01-PLAN.md — Wave 0 TDD scaffolding (wire-protocol extensions + 19 RED test skeletons + ConnectionProfile fields)
  - [x] 03-02-PLAN.md — Wave 1 client mode UX (DISP-01/DISP-07): connect-dialog ModeSelector + bookmark migration + MonitorSelector radio mode + toolbar mode badge + ClientHelloMsg push
  - [x] 03-03-PLAN.md — Wave 1 server crop pipeline (DISP-04/DISP-07): BGRA capture_raw_bgra_with_crop + apply_capture_mode + full hot-plug signature + Flame-approved EDID + Mac SCK push delegate (P010 raw-crop seam deferred to Phase 3.5)
  - [ ] 03-04-PLAN.md — Wave 2 cursor math (DISP-03/DISP-05): _widget_to_remote rewrite + per-screen DPR + screenChanged hook + F12 dev overlay + D-08 4-corner DXS hardware spike (code complete; D-08 gate pending hardware session)
  - [x] 03-05-PLAN.md — Wave 3 hot-plug UX (DISP-02/DISP-06): MonitorListMsg.degradations + auto-fallback iteration + RemapBanner + InfoToast + monitor-switched toast + degraded mode badge
  - [x] 03-06-PLAN.md — Wave 4 server clipboard (CLIP-01/CLIP-02/CLIP-03): ClipboardChunkAssembler + PNG path on Linux + Mac + CRLF preservation + server-side per-direction gating with Pitfall 7 race fix
  - [x] 03-07-PLAN.md — Wave 4 client clipboard UI + integration (CLIP-01/CLIP-02/CLIP-03): ClipboardToggleButton + protocol send_clipboard refactor with W-6 per-chunk mid-stream cancellation + session wiring + integration tests
**Size**: M
**UI hint**: yes

---

### Phase 4: Audio

**Goal**: Full-duplex low-latency audio that survives an 8-hour session — server timeline playback to client + client mic to server for client-review sessions. Locked to Opus 48kHz, watchdogged against the 30-minute PulseAudio hang, AirPods-friendly.

**Depends on**: Phase 1 (audio stream watchdog needs the FSM + observability; 60-min continuity test depends on the smoke harness)
**Requirements**: AUDIO-01, AUDIO-02, AUDIO-03, AUDIO-04, AUDIO-05, AUDIO-06, AUDIO-07, AUDIO-08, AUDIO-09
**Success Criteria** (what must be TRUE):
  1. Opus 48kHz low-latency audio (algorithmic delay <10ms) flows in BOTH directions — server playback to client AND client mic to server — measured mouth-to-ear under 80ms LAN, under 150ms Tailscale WAN
  2. New `server/mac_audio_capture.py` using AVAudioEngine tap captures system audio on macOS server (closes the "audio silently disabled on Mac" gap from CONCERNS.md)
  3. The 60-minute audio continuity smoke test passes with zero dropouts, zero sample-rate drift, zero sync loss — and runs in CI nightly
  4. AirPods (or any Bluetooth audio device) connecting / disconnecting mid-session re-attaches cleanly; the audio stream watchdog auto-restarts on the documented 30-minute PulseAudio hang
  5. Optional client-side echo cancellation can be enabled for studios with speakers near the mic, off by default
**Plans**: TBD
**Size**: M
**UI hint**: no

---

### Phase 5: Network, Transport, and Side-Channels

**Goal**: Make Teraguchi work on the network artists actually have — Tailscale tailnet hostnames, public-internet WAN with packet loss, captive-portal coffee-shop wifi. Promote QUIC from experimental to primary, harden the side channels (file transfer, USB control surfaces) on the same transport substrate.

**In-phase spike** (2 days, blocks USB-02):
- USB/IP target on macOS-as-server feasibility — `usbip` on macOS as the server side is not well-paved. May need an alternate mechanism (libusb-userspace shim or a pure-userland forwarding daemon). If the spike concludes "infeasible without a kernel extension," USB-02 becomes a documented Mac-server limitation and we ship USB-01 only.

**Depends on**: Phase 1 (bounded queues, FSM, CI), Phase 2 (10-bit verified before transport plays bitrate games), Phase 4 (audio path stable so QUIC promotion doesn't regress audio)
**Requirements**: NET-01, NET-02, NET-03, NET-04, NET-05, NET-06, NET-07, NET-08, NET-09, NET-10, NET-11, FILE-01, FILE-02, FILE-03, FILE-04, FILE-05, USB-01, USB-02, USB-03, OBS-04
**Success Criteria** (what must be TRUE):
  1. QUIC (aioquic 1.3.0) is the primary transport for WAN/Tailscale connections; an artist can move their MacBook from desk wifi to phone hotspot to back without losing the session (QUIC connection migration verified end-to-end)
  2. A client disconnect-and-reconnect within the idle timeout reattaches to the same server-side `SessionRuntime` (SESSION_RESUME) — Flame state, monitor topology, and cursor position are preserved with no re-authentication prompt
  3. File transfer in BOTH directions (drag-drop client→server AND bulk-pull server→client) handles >10GB files and >1000-file batches with progress UI, cancel, resume-after-disconnect, and SHA-256 integrity verification end-to-end
  4. A Loupedeck control surface plugged into the Mac client appears as native USB hardware on a Rocky Linux server (USB-01 working); the device allow-list rejects unknown USB classes; Mac-as-server target is either working (USB-02) or documented-not-supported per the spike outcome
  5. Visible network quality telemetry (bandwidth, RTT, loss, current codec bitrate) renders in the client overlay; rules-based adaptive bitrate adjusts smoothly under measured RTT + loss; FEC-protected keyframes survive 30% packet loss; captive-portal detection shows "reconnect when network recovers" UX
**Plans**: TBD
**Size**: L
**UI hint**: yes

---

### Phase 6: Distribution + Security Hardening

**Goal**: Ship a download that works on a stock Mac without right-click→Open and a `dnf install` that works on a stock Rocky 9 box. Make the security claims real — Tailscale-native certs by default, keychain-backed bookmarks, audited auth/token/TLS paths.

**Depends on**: Phase 1 (CI must be green to gate signed releases); Phase 5 (transport finalized so we don't ship a v1 with QUIC marked experimental)
**Requirements**: SEC-02, SEC-03, SEC-04, SEC-05, SEC-06, SEC-07, SEC-08, SEC-09, DIST-01, DIST-02, DIST-03, DIST-04, DIST-05, DIST-06, DIST-07, DIST-08, DIST-09
**Success Criteria** (what must be TRUE):
  1. A `git tag v1.0.0` push triggers GitHub Actions to produce a signed + notarized macOS `.app` bundle (with hardened runtime entitlements) AND a GPG-signed Rocky 9 RPM, both attached to a GitHub Release with SHA-256 checksums and the public RPM signing key
  2. A new user installing the Mac client gets a TCC onboarding flow that explains exactly why Accessibility, Input Monitoring, and Screen Recording are needed — without requiring a PhD to configure (closes the "TCC onboarding UX" requirement)
  3. Bookmark passwords are stored in the macOS Keychain (no more XOR-obfuscation); the Tailscale-native cert path is the default, with TOFU fingerprint pinning as fallback for non-Tailscale deployments and optional corporate-CA bundle support
  4. A security audit pass on auth, token, TLS, and PAM paths produces a written sign-off (committed to docs/) before v1 release; SECURITY.md publishes the disclosure policy and contact
  5. Upgrade-in-place from v0.x to v1.0 works for both Mac and Linux deployments; client/server version-skew tolerance is documented; uninstaller scripts cleanly remove all artifacts on both platforms
**Plans**: TBD
**Size**: L
**UI hint**: yes

---

### Phase 7: OSS Polish + Governance

**Goal**: Make Teraguchi a real open-source project, not a personal repo someone happened to make public. A small studio operator can find it, install it in 15 minutes, file a useful bug report, and ideally contribute back. A second committer signs on before v1.1 to de-risk the solo-maintainer burnout problem.

**Depends on**: Phase 6 (signed downloads must exist before the README can tell people to download them); Phase 5 (transport story stable before the protocol reference is locked)
**Requirements**: DOCS-01, DOCS-02, DOCS-03, DOCS-04, DOCS-05, DOCS-06, DOCS-07, GOV-01, GOV-02, GOV-03, GOV-04, GOV-05, GOV-06, GOV-07
**Success Criteria** (what must be TRUE):
  1. A first-time user with a fresh Mac and a fresh Rocky 9 box gets to a working session in 15 minutes following only the Quickstart README — verified by a non-Randy human running through it cold
  2. The on-the-wire protocol reference (message types, binary frame headers, auth flow) and the architecture overview are committed under `docs/` and accurate enough that an outside contributor could write an alternate client against them
  3. A short (<3 min) demo video of an actual Flame session running over Teraguchi is published and linked from the README; the "Migrating from HP Anyware / PCoIP" guide and reference deployment guides (homelab, 3-person studio, 10-person studio, GCP-hosted) are committed
  4. LICENSE (Apache 2.0), CONTRIBUTING.md (with explicit scope boundaries + anti-feature list), CODE_OF_CONDUCT.md (Contributor Covenant), SECURITY.md (from Phase 6), issue templates (bug / feature / performance), PR template, and stale-bot config are all in place
  5. A second committer with merge rights is invited and accepts before v1.1 work begins
**Plans**: TBD
**Size**: M
**UI hint**: yes

---

## Summary Table

| # | Phase | Goal (1-line) | Reqs | Size | UI | Depends On |
|---|-------|---------------|------|------|----|------------|
| 1 | Stability + CI + Test Baseline | Make the prototype trustworthy; fix the 3 critical-path blockers; instrument | 14 | L | no | — |
| 2 | Input + Color Fidelity | 10-bit end-to-end + Wacom pressure + zero hotkey mangling (the user-visible core value) | 24 | L | no | Phase 1 |
| 3 | Display + Multi-Monitor + Clipboard | Per-session monitor mode selector, mixed-DPI math, image clipboard | 10 | M | yes | Phase 2 |
| 4 | Audio | Opus 48kHz full-duplex on Mac + Linux server, watchdog, 60-min continuity | 9 | M | no | Phase 1 |
| 5 | Network, Transport, Side-Channels | QUIC primary + SESSION_RESUME + file transfer + USB control surfaces | 20 | L | yes | Phases 1, 2, 4 |
| 6 | Distribution + Security Hardening | Signed/notarized Mac + signed RPM + TLS pinning + keychain + audit + TCC UX | 17 | L | yes | Phases 1, 5 |
| 7 | OSS Polish + Governance | README + protocol ref + demo video + LICENSE/CONTRIBUTING/SECURITY + 2nd committer | 14 | M | yes | Phases 5, 6 |

**Total v1 requirements:** 108 / 108 mapped (100%)

## Spike Index

| Spike | Phase | Estimated | Blocks |
|-------|-------|-----------|--------|
| IOHIDUserDevice pen pressure feasibility on macOS | Phase 2 (pre-phase) | 2 days | INPUT-08 (and the macOS server's claim to be "Flame-usable") |
| Real-hardware Wacom matrix (Intuos Pro L + Cintiq Pro 24, Sonoma + Sequoia) | Phase 2 (in-phase) | 1 day | INPUT-09, -10, -11, -12 |
| Real-hardware mixed-DPI session (Retina MBP + external non-Retina monitor) | Phase 3 (pre-phase) | 1 day | DISP-03, -05, -07 |
| USB/IP target on macOS-as-server feasibility | Phase 5 (in-phase) | 2 days | USB-02 (may re-scope to Linux-only USB target) |

## Critical-Path Notes (from research/SUMMARY.md)

Phase 1 is the critical-path blocker. Three documented blockers must clear before any other phase's measurements have meaning:

1. **`send_queue maxsize=30`** in `server/main.py` — drops frames without requesting IDR recovery, meaning a single dropped frame can stall the stream for up to 2 seconds. 5-line fix; outsized impact. Until fixed, every "smooth playback" claim is unverifiable.
2. **TLS certificate verification OFF everywhere** (`ssl.CERT_NONE` in client WS, broker WS, QUIC, aiohttp probes). Until fixed, every "secure" claim later is built on sand. SEC-01 lives in Phase 1; full security hardening (Tailscale-native certs, TOFU pinning, audit) is Phase 6.
3. **Zero tests, zero CI**. Refactoring `server/main.py` (1,191 lines) and the encoder pipeline without tests is pure risk. STAB-01, STAB-02, STAB-03 must land before any Phase 2-7 work touches those files.

## Anti-Features (Explicitly NOT in This Roadmap)

For scope discipline (single maintainer):

- Windows client + Windows server (Phase 3 of overall project plan, not v1)
- Linux client packaged + signed (exists in tree, no v1 distribution commitment)
- Broker hardening + pool-scheduling features (broker works, paused for v1; revisit when small-studio users demand it)
- License dongle (iLok / HASP) USB/IP forwarding
- Stream Deck / keypad USB/IP forwarding
- Browser / WebRTC client (incompatible with 10-bit fidelity)
- Mobile client (iPad / Android)
- Smart-card / CAC authentication (enterprise overbuild)
- Session recording for compliance
- Real-time collaboration / observer mode
- Full ICC color profile pipeline (10-bit pass-through is enough for v1)
- Wayland server display (blocked on Autodesk Flame Wayland support)

---

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Stability + CI + Test Baseline | 17/17 | Complete | 2026-04-19 |
| 2. Input + Color Fidelity | 12/12 | Complete | 2026-04-19 |
| 3. Display + Multi-Monitor + Clipboard | 0/7 | Planned | — |
| 4. Audio | 0/0 | Not started | — |
| 5. Network, Transport, Side-Channels | 0/0 | Not started | — |
| 6. Distribution + Security Hardening | 0/0 | Not started | — |
| 7. OSS Polish + Governance | 0/0 | Not started | — |

---

*Generated 2026-04-18 by gsd-roadmapper from REQUIREMENTS.md (108 v1 reqs) + research/SUMMARY.md (7-phase ordering) + research/STACK.md + research/PITFALLS.md + .planning/codebase/*
