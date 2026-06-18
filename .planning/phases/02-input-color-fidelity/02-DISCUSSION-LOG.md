# Phase 2: Input + Color Fidelity — Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in 02-CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-18
**Phase:** 02-input-color-fidelity
**Areas discussed:** Color fidelity strategy, macOS pen pressure, Hotkey correctness, Wacom hardware verification

---

## Color fidelity strategy

### Q1: How should VIDEO-01/VIDEO-02's bit-exact fixture be built?

| Option | Description | Selected |
|--------|-------------|----------|
| 9 checkpoint byte-equality | Generate a known 10-bit ramp (SMPTE RP 2111 / bit-9-toggled). Assert pixel format + bottom-2-bits preserved at all 9 pipeline points. Any single regression fails CI. Strongest guarantee. | ✓ |
| End-to-end only | Inject at capture, assert bit-equal bottom-2-bits at decoded output. Doesn't pinpoint WHICH stage dropped bits. Simpler. | |
| Banding detector (post-hoc) | Decode a gradient, flag 'values on multiples of 4' as 10→8→10 signature. Probabilistic. | |

**User's choice:** 9 checkpoint byte-equality
**Notes:** Matches Phase 2 Success Criterion #1 literally — "one regression fails CI."

### Q2: How far does 'end-to-end 10-bit' extend on the client display?

| Option | Description | Selected |
|--------|-------------|----------|
| Metal/QRhi direct texture | Upload P010 / YUV420P10LE as Metal texture. Metal does YUV→RGB + display. Only way to honor 10-bit on MBP XDR. Biggest client-side change. | ✓ |
| QImage Format_RGBA64 (16-bit) | Decode to 16-bit QImage, blit via QPainter. Preserves bits in buffer but Qt compositor is 8-bit on non-EDR. Simpler. | |
| Accept 8-bit blit | Wire is 10-bit, QImage 8-bit. Document that 'end-to-end' stops at wire. Smallest change; honest about Qt's limits. | |

**User's choice:** Metal/QRhi direct texture
**Notes:** The claim "indistinguishable from local" requires the display path to be honest, not just the wire.

### Q3: How do we gate the 10-bit negotiation when hardware can't actually encode Main10?

| Option | Description | Selected |
|--------|-------------|----------|
| Refuse + explicit overlay | Probe at startup. If HW can't encode Main10, refuse to advertise 10-bit in ServerHelloMsg. Client overlay shows badge. No silent fallback. | ✓ |
| Probe + warn only | Probe + log warning, but attempt Main10 encode and let NVENC fall back silently. Overlay shows final negotiated profile. | |
| Configured, not probed | User opts in via bookmark / CLI flag. Encoder fails loudly if HW can't deliver. | |

**User's choice:** Refuse + explicit overlay

### Q4: Build the VTCompressionSession PyObjC wrapper in Phase 2 (VIDEO-04)?

| Option | Description | Selected |
|--------|-------------|----------|
| Full wrapper in Phase 2 | Write server/mac_video_encoder.py using pyobjc-framework-VideoToolbox with low-latency rate control. Saves 5-8ms/frame per WWDC21. | ✓ |
| FFmpeg subprocess + defer | Keep existing hevc_videotoolbox subprocess. Accept +5-8ms on Mac server. Revisit Phase 5/optimization. | |
| Hybrid: H.264 direct, HEVC via FFmpeg | PyObjC wrapper only for H.264 low-latency. HEVC Main10 via FFmpeg. Split by codec. | |

**User's choice:** Full wrapper in Phase 2

### Q5: What's the 4:4:4 / 4:2:2 chroma subsampling policy for v1?

| Option | Description | Selected |
|--------|-------------|----------|
| 4:2:0 default, 4:2:2 opt-in | HEVC Main10 4:2:0 default. 4:2:2 negotiated only where both ends support (Blackwell NVENC, M3+ decode, M4 encode). 4:4:4 in ENCODER_DEFS but not user-facing. | ✓ |
| 4:2:0 only for v1 | Lock v1 to Main10 4:2:0. Simplest handshake. | |
| Automatic best-available | Server probes + picks highest chroma both ends support, no user input. | |

**User's choice:** 4:2:0 default, 4:2:2 opt-in where both ends support

### Q6: ICC profile / display calibration — does Phase 2 touch any of it?

| Option | Description | Selected |
|--------|-------------|----------|
| Out of scope, document only | PROJECT.md already lists ICC pipeline as Out of Scope. Phase 2 ships 10-bit pass-through only. Docs (Phase 7) advise Reference Mode. | ✓ |
| Detect + warn in client | Client reads macOS display state (Night Shift, True Tone, non-Reference-Mode). Shows overlay warning. Small code. | |
| Full detection + Reference Mode auto-advise | ColorSync API inspection, refuse 'color-accurate' status when OS filters on. More PyObjC. | |

**User's choice:** Out of scope, document only

---

## macOS pen pressure

### Q1: If the IOHIDUserDevice spike fails, what's the v1 plan?

| Option | Description | Selected |
|--------|-------------|----------|
| Document + ship | Mac-server pen pressure becomes documented v1 limitation. INPUT-08 re-scoped to mouse-click fallback + overlay warning. Flame on Rocky is production path. HIDDriverKit → v1.1. | ✓ |
| HIDDriverKit system extension in Phase 2 | Skip IOHIDUserDevice, go straight to signed DriverKit extension. Apple approval gated; adds weeks. | |
| Drop Mac server for v1 | Pull Mac server entirely from v1. Rocky-only server ships in v1. Throws away existing SCK / CGEventPost work. | |

**User's choice:** Document + ship
**Notes:** Rocky is the Flame production path regardless — this keeps v1 shippable even if IOHIDUserDevice is unworkable.

### Q2: What does 'spike succeeded' actually mean?

| Option | Description | Selected |
|--------|-------------|----------|
| Pressure visible in target apps | Unsigned PyObjC IOKit script creates virtual HID tablet that Photoshop / Preview reads non-zero NSEvent.pressure from across a stroke. Pattern proven. | ✓ |
| End-to-end through mac_input_injector | Higher bar: spike must wire into pen_event() and deliver events from Mac client → Mac server round trip. | |
| Flame 2026 on Mac responds correctly | Strictest: Flame-on-Mac-server must respond. If Flame ignores synthesized pressure, stuck. | |

**User's choice:** Pressure visible in target apps
**Notes:** Success bar is feasibility of the HID pattern; productionization into the server path is Phase 2 proper work, not the spike.

### Q3: Once the IOHIDUserDevice helper lands, how does it relate to the existing mac_input_injector.py?

| Option | Description | Selected |
|--------|-------------|----------|
| New module, dispatcher decides | Keep mac_input_injector.py for mouse/keyboard/scroll. Add mac_pen_injector.py owning IOHIDUserDevice. Mirrors Linux split. | ✓ |
| Extend mac_input_injector.py | Drop IOHIDUserDevice into existing file. One module for all Mac input. | |
| Unified cross-platform PenInjector | Abstract Linux uinput + Mac IOHIDUserDevice behind common/pen_injector.py. Risky for Phase 2. | |

**User's choice:** New module, dispatcher decides

---

## Hotkey correctness

### Q1: What's the default Cmd↔Ctrl translation policy Mac client → Linux server?

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-swap, per-bookmark override | Default: Mac Cmd → Linux Ctrl. Mac Ctrl stays as Ctrl. Bookmark checkbox ON for Linux, OFF for Mac. Swap visible in overlay. | ✓ |
| Always swap, no toggle | Hard-coded Cmd→Ctrl in common/keymap.py. Breaks non-Flame workflows where Cmd=Super. | |
| Never swap, user maps keys | Pass Cmd through as Meta/Super. User rebinds Flame shortcuts on Linux side. | |

**User's choice:** Auto-swap, per-bookmark override

### Q2: When should client-side release-all-modifiers fire?

| Option | Description | Selected |
|--------|-------------|----------|
| WindowDeactivate | Cmd-Tab / focus loss → send KEY_RESET_MODIFIERS immediately. Fixes 'Ctrl stuck after Cmd-Tab'. | ✓ |
| Reconnect | After reconnect, first message = release-all-modifiers. Prevents key-repeat runaway. | ✓ |
| Periodic safety net | Every ~10s idle, server-side InputInjector.reset_modifiers() fires. Belt-and-suspenders. | ✓ |
| Explicit user shortcut | F9 panic button releases all modifiers on demand. Artist escape hatch. | ✓ |

**User's choice:** All four selected
**Notes:** Multi-select question. All four mechanisms land in Phase 2.

### Q3: How exhaustive should INPUT-01's table-driven keymap tests be?

| Option | Description | Selected |
|--------|-------------|----------|
| Exhaustive Qt→Linux+Mac × all modifiers | Every Qt key × {none, Shift, Ctrl, Alt, Meta, all 2/3-key combos} × {Linux scancode, Mac vkey}. ~2000 parameterized cases. Flame hotkey set as named subset. | ✓ |
| Flame critical-path only | Documented Flame 'crazy hotkey combos' × both platforms. ~200 cases. Misses non-Flame workflows. | |
| Property-based via hypothesis | hypothesis generates random (key, modifier set) pairs. Lighter maintenance; harder to pin coverage. | |

**User's choice:** Exhaustive Qt→Linux+Mac × all modifiers

### Q4: How deep does Phase 2 go on international keyboard / dead-key / IME handling (INPUT-05)?

| Option | Description | Selected |
|--------|-------------|----------|
| US/UK/DE/JP layouts + IME passthrough | Test matrix 4 layouts. Viewer accepts QInputMethodEvent, forwards commit strings as unicode. Matches REQ literally. | ✓ |
| US/UK only; IME as v1.1 | Lock v1 to US + UK. Flag DE/JP/IME as known limitation. | |
| Full i18n (all common layouts + AltGr + dead keys) | US, UK, DE, FR, ES, IT, JP, KR, CN, AltGr, dead keys. Bigger Phase 2. | |

**User's choice:** US/UK/DE/JP layouts + IME passthrough

### Q5: How should server-side key-repeat runaway be prevented?

| Option | Description | Selected |
|--------|-------------|----------|
| Server xset r off + client-driven repeats | Linux virtual display: xset r off. Client sends explicit N repeats. Network stall → repeat stops. Matches PCoIP. | ✓ |
| Client bounds repeat rate | Client rate-limits key-down events. Server keeps auto-repeat on. | |
| Per-key timeout on server | Server tracks key-down timestamps, synthesizes key-up if no release within bounded window. | |

**User's choice:** Server xset r off + client-driven repeats

### Q6: How do we sync Caps Lock state per INPUT-06?

| Option | Description | Selected |
|--------|-------------|----------|
| Bit in every KeyEvent | Client includes caps_lock_on bool in every KeyEvent. Server auto-corrects on mismatch. Zero overhead. | ✓ |
| Dedicated sync message on change | KeyLockStateMsg only on change. Drifts if message dropped. | |
| Server-authoritative, client mirrors | Server owns state, client LED mirrors. Loses battle with OS-owned client Caps LED. | |

**User's choice:** Bit in every KeyEvent

---

## Wacom hardware verification

### Q1: How often does the real-hardware Wacom matrix actually get run?

| Option | Description | Selected |
|--------|-------------|----------|
| One-shot gate + per-release manual ritual | Run 4-cell matrix once at Phase 2 completion + every release tag (runbook). No self-hosted CI runners. Preserves Phase 1 D-05. | ✓ |
| Self-hosted DXS runner for continuous checks | Self-hosted GHA runner with Cintiq Pro 24 attached. Runs Wacom matrix on every PR. Heavy investment. | |
| One-shot gate only, no release ritual | Run once, sign off, don't gate future releases. Lightest ops; highest regression risk. | |

**User's choice:** One-shot gate + per-release manual ritual

### Q2: What does 'passing' the Wacom matrix mean — what's measurable?

| Option | Description | Selected |
|--------|-------------|----------|
| Hybrid: protocol + automated counters | Checklist protocol (ramp, eraser, tilt, proximity, buttons, reconnect). Each step has automated counter. Pass = all counters + manual sign-off on 'pressure curve looks right in Flame.' Video recorded. | ✓ |
| Protocol + video only | Manual checklist + recording. No counters. Faster; misses quiet regressions. | |
| Quantization-error metric only | Single RMS pressure metric across all cells. May miss eraser/tilt/button bugs. | |

**User's choice:** Hybrid: protocol + automated counters

### Q3: How is pressure-quantization error measured for INPUT-12?

| Option | Description | Selected |
|--------|-------------|----------|
| Known-ramp + RMS error | Slow 0→max linear ramp over ~5s. Client QTabletEvent.pressure vs server HID report. RMS error <1%. Graph committed. | ✓ |
| Per-level discrete test | Discrete levels (10%, 25%, 50%, 75%, 100%). Hold each 1s. Misses curve-shape bugs. | |
| Visual-only (no metric) | Draw a gradient, eyeball it. No numeric gate. Subjective. | |

**User's choice:** Known-ramp + RMS error

### Q4: How do we handle the 'proximity event eaten by lockscreen / minimization' bug?

| Option | Description | Selected |
|--------|-------------|----------|
| Synthesize proximity on focusIn + showEvent | Viewer synthesizes proximity-enter toward server. Server pen FSM tolerates duplicates idempotently. | ✓ |
| Client-side pen state heartbeat | Every 250ms client sends PenState(in_proximity, pressure, x, y). More bandwidth. | |
| Server-side pen watchdog only | Server resets pen FSM if no events for >1s. Delays first-stroke responsiveness. | |

**User's choice:** Synthesize proximity on focusIn + showEvent

### Q5: Phase 2 latency gate — promote to real-hardware or keep Phase 1's synthetic p99<25ms?

| Option | Description | Selected |
|--------|-------------|----------|
| Keep synthetic gate + manual DXS number | CI keeps synthetic p99<25ms. Phase 2 verification adds one-time real-HW DXS measurement to docs/release.md. No new CI gate. | ✓ |
| Tighten synthetic gate to 20ms | Drop Phase 1's 5ms drift headroom. Fail build if synthetic harness can't stay under 20ms. | |
| New real-HW CI gate via self-hosted runner | Self-hosted DXS runner with closed-loop photon-to-input rig on every PR. Contradicts GitHub-hosted-only discipline. | |

**User's choice:** Keep synthetic gate + manual DXS number

### Q6: How does Phase 2 handle the macOS TCC / driver permissions for Wacom?

| Option | Description | Selected |
|--------|-------------|----------|
| Detect + guide on client | Client reads TCC DB read-only. Missing permissions → onboarding panel with deep links. | ✓ |
| Document only, no detection | docs/wacom-setup.md runbook. Client assumes correct permissions. | |
| Full TCC automation | Client opens System Settings panes via `x-apple.systempreferences:` URLs during first-launch. | |

**User's choice:** Detect + guide on client

---

## Claude's Discretion

- Metal shader sources for 10-bit YUV→RGB conversion (D-02) — planner picks a stock BT.709 approach.
- `KEY_RESET_MODIFIERS` / `TextCommit` / `PenProximity` wire format in `common/messages.py` — follow existing dataclass + MsgType pattern.
- Exact `HWEncoder` dataclass edits in `server/video_encoder.py` (capability flags for supports_main10, supports_422, supports_444).
- `server/mac_video_encoder.py` internal structure and async wrapper shape around VTCompressionSession.
- Test fixture asset location for the 10-bit ramp binary (suggested `tests/smoke/fixtures/`).
- FSM extension vs separate pen FSM for D-19 proximity handling.
- `tools/wacom_quant_analysis.py` module layout.

## Deferred Ideas

- HIDDriverKit signed system extension (→ v1.1; Apple approval bureaucracy).
- Full ICC profile pipeline / display calibration (→ out of scope per PROJECT.md).
- HDR tone-mapping on client (→ out of scope, same bucket).
- 4:4:4 HEVC as user-facing mode (→ post-v1 enhancement).
- FR / ES / IT / CN / KR keyboard layouts (→ v1.1).
- "Aggressive capture" CGEventTap for trap keys (→ document the limitation; revisit if Mac-server-for-Flame becomes load-bearing).
- Self-hosted DXS CI runners for real-hardware checks (→ carried forward from Phase 1 deferred list).
- Full 8-hour smoke harness (→ Phase 4).
- Sentry crash reporting (→ Phase 7 observability polish).
- Prometheus /metrics (→ explicitly Phase 5; don't pull forward).
- `server/main.py` DISPLAY env-var thread-safety (→ Phase 3 if session-manager work lands there).
