# Teraguchi — Research Synthesis

**Synthesizes:** STACK.md + FEATURES.md + ARCHITECTURE.md + PITFALLS.md
**Date:** 2026-04-18
**Overall confidence:** MEDIUM-HIGH

## Executive Summary

Teraguchi is a production-hardening project, not a greenfield build. The prototype delivers end-to-end video, input, audio, clipboard, file transfer, and multi-monitor across Mac→Rocky and Mac→Mac. The architecture is fundamentally sound and matches the patterns used by Sunshine, Parsec, and NICE DCV. The v1 milestone is about making it bulletproof for 8-hour Flame sessions, verifying every promise made in the spec (10-bit color, Wacom pressure, modifier-chord fidelity), and shipping signed installable artifacts that small studios can actually adopt.

The market window is real and time-bounded. HP Anyware new sales end May 7, 2026; existing customers must migrate by October 31, 2029. Every small independent VFX studio (1-10 person shops) running Flame, Nuke, or Resolve over PCoIP is now on a clock. NICE DCV is the technically strongest competitor but is AWS-native, closed, and expensive. Parsec has Wacom support but is SaaS-only, 8-bit-only, Windows-server-only, and costs money. Sunshine/Moonlight have no pen tablet support at all. The gap Teraguchi fills — self-hosted, open-source, 10-bit end-to-end, first-class Wacom, macOS server — is uncontested.

The primary risk is not technical; it is compounding fragility from zero tests and zero CI, combined with the reality of a solo maintainer. Every phase of the roadmap must be held to a CI/test gate. No feature work ships without a regression test. The second existential risk is the pipeline latency time-bomb: the current `send_queue(maxsize=30)` drops frames but never requests IDR recovery, meaning a single dropped frame can stall the stream for up to 2 seconds. This must be fixed in Phase 1 before any other work is validated against it.

---

## Open Questions for User to Resolve Before Roadmap Locks

1. **License choice.** AGPL-3.0 vs Apache 2.0 vs MIT. Apache 2.0 maximizes studio adoption; AGPL closes the "run it as a service" loophole. This is a values decision, not a technical one. Sunshine uses GPL-3.0.

2. **60fps vs 30fps as the v1 bar.** PROJECT.md says 60fps LAN. Research recommendation: commit to 60fps LAN delivery, 30fps for WAN Tailscale degraded mode. Needs explicit confirmation.

3. **Rocky 9 only vs also Ubuntu/Debian.** Research recommendation: Rocky 9 only for v1. State it explicitly in the README.

4. **Audio codec explicit confirmation.** Research says Opus (WebRTC-mandated, tunable to <10ms). The prototype codec is unspecified. Lock to Opus — confirm this is the choice.

5. **Apple Developer ID enrollment status.** Signed + notarized Mac distribution gates on Developer ID ($99/yr). If not already enrolled, this is the first action item — takes 24-48 hours.

---

## Critical Path (Blocking Items)

| Item | Why Blocking | Confidence |
|------|-------------|------------|
| Apple Developer ID enrollment | Gates every Mac distribution artifact | HIGH |
| `send_queue` maxsize=30 → maxsize=4 + IDR-on-drop | Broken pipeline makes every latency measurement invalid; 5-line fix with outsized impact | HIGH |
| TLS cert verification OFF everywhere (`CERT_NONE`) | No actual transport security today; must fix before v1 ships | HIGH |
| CI on GitHub Actions (Mac + Rocky 9) | Zero CI today; regression risk compounds every commit | HIGH |
| pytest baseline (`common/`, auth, tokens) | Zero tests today; refactoring pipeline without tests is pure risk | HIGH |
| macOS USB/IP feasibility spike (2 days) | Mac-as-server receiving USB/IP is not well-paved; may need alternate mechanism | MEDIUM |
| IOHIDUserDevice pen pressure spike (2 days) | `mac_input_injector.pen_event` downgrades pen to mouse click; CGEventPost silently loses pressure | MEDIUM |
| 10-bit end-to-end verification with test pattern | 9 silent downgrade points; must verify before claiming 10-bit support | HIGH |

---

## Stack Prescription (v1 — exact versions chosen)

| Layer | Tool / Version | Rationale |
|-------|---------------|-----------|
| Python runtime | 3.12 (floor) | EOL-safe; first-class Apple Silicon wheels; asyncio improvements |
| Client UI | PySide6 6.10 | Only toolkit with full QTabletEvent pressure+tilt; QRhiWidget Metal path |
| Video decode (client) | PyAV 17.x vs system FFmpeg 7.1+ w/ VideoToolbox | Bundled PyPI wheels lack full VideoToolbox hwaccel |
| Video encode (Linux/NVIDIA) | FFmpeg 7.1.1+ subprocess, `hevc_nvenc` Main10 | Crash isolation; 4-8ms LAN; Blackwell-ready |
| Video encode (macOS server) | VTCompressionSession direct via PyObjC | FFmpeg VT wrapper doesn't expose low-latency mode; saves 5-8ms/frame |
| Screen capture (Linux) | NvFBC (primary) / mss (fallback) | Zero-copy on NVIDIA; X11 universal fallback |
| Screen capture (macOS) | ScreenCaptureKit via PyObjC, P010 pixel format | 10-bit HDR capture; macOS 14.4+ |
| Input inject (Linux) | uinput + XTest | Flame 2026 is X11-only; Rocky 10 NOT a v1 target |
| Input inject (macOS server) | CGEventPost (mouse/keyboard) + IOHIDUserDevice (tablet) | CGEventPost alone silently loses NSEvent.pressure |
| Transport | aioquic 1.3.0 (QUIC primary) + websockets 15.x (fallback) | QUIC = connection migration, 0-RTT, single-port TLS 1.3; NICE DCV default since 2024 |
| Audio codec | Opus (libopus in FFmpeg) | WebRTC-mandated; tunable to <10ms LAN; prototype has unspecified codec |
| Audio capture (Linux) | PipeWire via pipewire-pulse compat + PulseAudio fallback | Both Rocky 9 and Rocky 10 covered |
| Audio capture (macOS) | AVAudioEngine tap with device-change notification handling | Auto-reattaches on AirPods connect/disconnect |
| FEC | zfec Reed-Solomon, 30-50% overhead configurable | Keyframe protection mandatory |
| Packaging (macOS) | PyInstaller 6.11+ + codesign + notarytool + create-dmg | Existing path; adding hardened runtime + notarization |
| Packaging (Linux) | rpmbuild in Rocky 9 container + rpm-sign with GPG | GitHub Releases as distribution |
| CI | GitHub Actions: macos-14 runner + rockylinux:9 container | Standard pattern |
| Logging | structlog 25.x JSON | Structured JSON to stdout |
| Config format | TOML (tomllib stdlib) | Three-level hierarchy; no YAML whitespace sensitivity |

**Do NOT use:** Wayland as server display, FFmpeg videotoolbox wrappers on macOS server, AAC audio, WebRTC DataChannel as transport, PyAV bundled PyPI FFmpeg wheels, libaom AV1, Nuitka packaging, `send_queue(maxsize=30)`.

---

## Phase-Order Recommendation

The right Phase 1 is Stability + CI + Tests + Critical Bugs. Without CI, every subsequent phase ships regressions silently. The `send_queue` bug makes every latency measurement invalid. TLS being off means every "security" later claim is built on sand.

**7-phase ordering (synthesized from Architecture 6-phase + Pitfalls 7-phase):**

1. **Stability + CI + Test Baseline** — Fix blocking bugs (send_queue, TLS), add CI, add pytest baseline, decompose `server/main.py`, add explicit FSM, add bounded queues throughout. Nothing else is trustworthy without this.

2. **Input and Color Fidelity** — 10-bit end-to-end verified with test pattern; IOHIDUserDevice pen injector for macOS server; modifier release on focus loss and reconnect; table-driven keymap unit tests. The user-visible core value.

3. **Display and Multi-Monitor** — Per-session connect-time mode selector hardened; cursor coord math in server physical pixels; CustomEDID for Xvfb; SCK display-change handler; mixed-DPI smoke matrix.

4. **Audio** — `mac_audio_capture.py` via AVAudioEngine; 48kHz Opus locked end-to-end; audio stream watchdog; echo-cancel client-side; 60-minute continuity smoke test.

5. **Network and Transport Hardening** — QUIC promoted to primary; FEC via zfec; MTU ceiling 1280; UDP path health probe; captive portal detection; rules-based adaptive bitrate; SESSION_RESUME reconnect message.

6. **Distribution and Security Hardening** — Signed + notarized .app + DMG in CI; signed RPM + GPG public key published; stable bundle ID; SELinux policy module; TCC onboarding UX; bookmark passwords to keychain; release runbook.

7. **OSS Polish and Governance** — README + architecture + protocol reference docs; CONTRIBUTING.md with explicit scope; SECURITY.md; issue templates; stale-bot; demo video; reference deployment guides; "Migrating from PCoIP" guide; second committer invited.

### Research Flags by Phase

- Phase 2: **Spike needed** — IOHIDUserDevice feasibility (2 days) and real-hardware Wacom sleep/wake verification
- Phase 3: **Spike needed** — Real Cintiq Pro + Retina setup for topology math (1 day)
- All others: Standard patterns; no additional research phase needed

---

## Anti-Features (Deliberately NOT Building for v1)

From the synthesized research: scope discipline is survival for a solo maintainer. The following are PCoIP/DCV/Parsec features that will NOT ship in Teraguchi v1, with rationale.

- **Browser / WebRTC client** — kills 10-bit fidelity; PySide6 native only
- **Mobile client (iPad / Android)** — not VFX-relevant
- **Smart-card / CAC authentication** — enterprise overbuild
- **Session recording for compliance** — only a compliance feature; artists don't need it
- **Real-time collaboration / observer mode** — interesting but adds an order of magnitude of complexity
- **Game controller forwarding** — wrong audience
- **Zero-client hardware** — we are software-only
- **Full ICC profile pipeline** — 10-bit end-to-end without ICC is still better than PCoIP's 8-bit; defer ICC
- **Printer redirection** — nobody prints from Flame
- **Drive mapping (Windows-ism)** — file transfer covers the use case
- **Broker feature work** — explicitly paused in PROJECT.md
- **Windows server + Windows client** — Phase 3 of overall project plan
- **Linux client (as a packaged/signed deliverable)** — PySide6 runs on Linux but no v1 distribution commitment
- **License dongle (iLok/HASP) USB/IP forwarding** — dongles usually live server-side
- **Stream Deck / keypad USB/IP forwarding** — control surfaces only in v1
- **Session recording for QA/dailies** — interesting, not a v1 commitment

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Vendor docs, PyPI releases, Apple WWDC sessions |
| Features | HIGH | Vendor docs + direct code read of prototype |
| Architecture | HIGH | Prototype code + industry reference systems well-documented |
| Pitfalls | MEDIUM-HIGH | Latency/color/input verified; macOS Wacom regressions need real-hardware validation |
| macOS server stability | LOW | 5 commits old; not stress-tested |
| macOS USB/IP server receive | LOW | Not well-paved path |
| IOHIDUserDevice pen injection | MEDIUM | Community-documented; needs implementation spike |
| Sub-20ms LAN achievable | MEDIUM | Extrapolated from Parsec/Sunshine; not measured on this codebase |

**Overall: MEDIUM-HIGH**
