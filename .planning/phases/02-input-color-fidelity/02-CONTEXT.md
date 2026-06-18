# Phase 2: Input + Color Fidelity — Context

**Gathered:** 2026-04-18
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver the user-visible core value of Teraguchi: **10-bit color end-to-end with zero silent downgrades, full-fidelity Wacom pressure/tilt/eraser/proximity round-trip on real hardware, and zero modifier-chord mangling on Flame's hotkey workflows.** This is the phase where an "indistinguishable from local" claim becomes defensible or falls apart.

Phase 2 closes the 9 documented silent 10-bit downgrade points (BGRA capture default, encoder input format, NVENC Main10 fallback, chroma subsampling default, VideoToolbox P010 software fallback, Qt QImage 8-bit formats, macOS EDR tone-mapping, Reference Mode requirement, ICC interference), replaces the `mac_input_injector.pen_event` pressure-dropping stub with a working IOHIDUserDevice helper, promotes the Mac-server encoder path to direct VTCompressionSession via PyObjC for low-latency HEVC Main10, and proves the whole stack on real Wacom hardware on both macOS Sonoma and Sequoia.

Phase 2 ships when: (1) the 9-checkpoint bit-exact 10-bit ramp fixture is green in CI, (2) the IOHIDUserDevice spike has landed or has been explicitly re-scoped with a documented v1 limitation, (3) the exhaustive Qt-key × modifier-chord keymap test covers both Linux and Mac virtual key codes, and (4) the DXS real-hardware Wacom matrix (Intuos Pro L + Cintiq Pro 24 × Sonoma + Sequoia) passes the hybrid protocol-plus-counters gate.

**In scope:** INPUT-01..12 (12 requirements — keymap unit tests, modifier release, reconnect-hygiene, crazy-hotkey integration test, i18n / dead-keys, Caps Lock sync, Cmd↔Ctrl translation, IOHIDUserDevice pen injector, Wacom hardware matrix, eraser / side-buttons, proximity, pressure curve preservation) and VIDEO-01..12 (12 requirements — 10-bit end-to-end verification, bit-exact fixture, HEVC Main10 via hevc_nvenc, direct VTCompressionSession PyObjC wrapper, chroma subsampling policy, IDR-on-drop regression (already wired in Phase 1 — Phase 2 verifies 10-bit path specifically), encoder reconfigure without restart, ScreenCaptureKit HDR config, NvFBC primary / mss fallback, 60fps LAN / 30fps WAN, sub-20ms latency gate, PyAV against system FFmpeg 7.1+).

**Out of scope:**
- Display / multi-monitor / mixed-DPI math (Phase 3)
- Clipboard image + per-direction toggles (Phase 3)
- Audio (Phase 4)
- QUIC promotion, FEC, session resume, file transfer, USB control surfaces (Phase 5)
- Packaging, signing, notarization, Tailscale-native certs (Phase 6)
- Docs, CONTRIBUTING, demo video, second committer (Phase 7)
- **ICC profile pipeline / display calibration management** (out of scope for v1 entirely per PROJECT.md)
- **HDR tone-mapping on client** (same reason)
- **HIDDriverKit-based virtual HID** (deferred to v1.1 — Apple DriverKit entitlement approval is weeks of bureaucracy and not guaranteed on Logik Academy Pro's default Developer ID)
- **Wayland / Rocky 10 server** (blocked on Autodesk Flame Wayland support)

</domain>

<decisions>
## Implementation Decisions

### Color Fidelity Strategy (VIDEO-01..12)

- **D-01: Test fixture = 9-checkpoint byte-equality.** Generate a known 10-bit ramp test pattern (SMPTE RP 2111-ish, or a bit-9-toggled gradient) at session start in a CI fixture. Assert pixel format + bottom-2-bits preserved at all 9 documented downgrade points: (1) capture output format (NvFBC / ScreenCaptureKit surface), (2) encoder input (FFmpeg rawvideo / VTCompressionSession input buffer), (3) encoder output probe (`ffprobe` on a dumped sample asserts `pix_fmt=p010le` / `yuv420p10le`), (4) transport wire bytes (H.265 NAL unit profile field), (5) decoder output (PyAV `AVFrame.format`), (6) decoder hwaccel path (VT decode not falling to software for P010), (7) Qt display-surface path (Metal texture format), (8) final blit (Metal YUV→RGB shader preserving 10-bit), (9) macOS display pipeline (Reference Mode / EDR state documented if accepted). Any single regression fails CI. This is VIDEO-02 literally — the bit-exact end-to-end test pattern committed as a CI fixture.
- **D-02: Client display path = Metal/QRhi direct texture upload.** Decode to P010 / YUV420P10LE via PyAV. Upload as Metal texture through `QRhi` (Qt's cross-platform render interface, sits on Metal on macOS) or a `QMetalWidget` / `QOpenGLWidget` inside `RemoteViewer`. Metal does the YUV→RGB conversion and final blit — the pixels never touch a QImage. This is the only way "end-to-end 10-bit" honestly reaches the display on MBP XDR / Pro Display XDR. Biggest client-side change in Phase 2. Replaces the existing `QPainter.drawImage(QImage)` blit in `client/viewer.py` for the video layer; QImage remains for overlays and cursor.
- **D-03: Hardware capability probe = refuse + explicit overlay.** Server startup probes actual encoder capability: `nvidia-smi --query-gpu=gpu_name` + known Main10-support table on Rocky, `VTIsHardwareDecodeSupported(kCMVideoCodecType_HEVC)` + `kVTCompressionPropertyKey_ProfileLevel = HEVC_Main10_AutoLevel` trial on Mac. If hardware cannot encode HEVC Main10 4:2:0, server refuses to advertise 10-bit in `ServerHelloMsg`. Client health overlay shows explicit badge: **"10-bit: negotiated / confirmed / DEGRADED / not supported"**. No silent fallback — if the claim can't hold, the UI says so.
- **D-04: VTCompressionSession direct PyObjC wrapper = full wrapper in Phase 2 (VIDEO-04).** Write `server/mac_video_encoder.py` using `pyobjc-framework-VideoToolbox`: `VTCompressionSessionCreate` with `kVTVideoEncoderSpecification_EnableLowLatencyRateControl=True`, one-in-one-out pipeline, no frame reordering, `kVTProfileLevel_HEVC_Main10_AutoLevel`, 4:2:2 Main42210 profile opted in on M3+ when probe confirms support. Saves 5–8ms/frame vs. FFmpeg's `hevc_videotoolbox` subprocess path per WWDC21 session 10158. Replaces the Mac-server FFmpeg subprocess for video encode; Linux server continues using FFmpeg + `hevc_nvenc`.
- **D-05: Chroma subsampling policy = 4:2:0 default, 4:2:2 opt-in.** HEVC Main10 4:2:0 is the default path (covers DXS fleet Turing/Ampere + every shipping Mac). 4:2:2 advertised + negotiated only where both ends confirm hardware support (Blackwell NVENC on the Rocky side, M3+ decode / M4 encode on the Mac side). Quality-tier "Grading mode" slider flips to 4:2:2 when available. 4:4:4 stays in `ENCODER_DEFS` for hardware that can do it (Turing+ NVENC HEVC 4:4:4) but is not user-facing in v1 — it's bandwidth-expensive and software-decode-only on older Macs.
- **D-06: ICC / display calibration = out of scope, document only.** PROJECT.md already lists "Full ICC profile pipeline" as Out of Scope for v1. Phase 2 ships 10-bit pass-through only. Phase 7 docs (DOCS-04 migration guide + DOCS-07 troubleshooting) tell grading users: enable Reference Mode on MBP XDR, disable Night Shift / True Tone, use a calibrated external monitor. No Phase 2 code for display-state inspection. Leave as a v1.1 enhancement if demand surfaces.

### macOS Pen Pressure (INPUT-08)

- **D-07: IOHIDUserDevice spike failure path = document + ship.** If the 2-day pre-phase spike fails, INPUT-08 gets re-scoped to "Mac-server pen pressure is a known v1 limitation; `mac_input_injector.pen_event` keeps its current mouse-click fallback with a one-time warning." Documentation tells Flame artists: run Flame on the Rocky Linux server (the primary production path); use Mac server for non-pressure-critical work. HIDDriverKit signed system extension becomes a v1.1 roadmap item — Apple DriverKit entitlement approval is weeks of bureaucracy the solo-maintainer budget can't absorb in v1. **Rocky remains v1's Flame-production path regardless of spike outcome**; Mac-server-for-Flame is a nice-to-have, not load-bearing.
- **D-08: Spike pass bar = pressure visible in target apps.** End of spike day 2, success means: an unsigned Python script using `pyobjc-framework-IOKit` creates a virtual HID tablet that **Photoshop / Preview / a simple NSView test app reads non-zero `NSEvent.pressure` from across a complete pen stroke**. Code can be messy. TCC / permissions / signing requirements are noted. The pattern is **proven** to round-trip pressure to userspace apps. If THIS works in 2 days, Phase 2 has enough to productionize the helper.
- **D-09: Injector module split = new `server/mac_pen_injector.py`.** Keep `server/mac_input_injector.py` for mouse / keyboard / scroll (CGEventPost is the right tool there). Add `server/mac_pen_injector.py` owning IOHIDUserDevice lifecycle + pressure / tilt / eraser / proximity injection. `platform_backends.InputInjector` composes both (pen + non-pen dispatcher). Mirrors the Linux split (`input_injector.py` vs `xtest_injector.py`). `mac_input_injector.pen_event` becomes a thin delegation to `mac_pen_injector` when available, or the existing mouse-click fallback when not.

### Hotkey Correctness (INPUT-01..07)

- **D-10: Cmd↔Ctrl translation default = auto-swap, per-bookmark override.** Default policy: Mac Cmd → Linux Ctrl (matches Flame muscle memory on Linux where Ctrl is the hotkey anchor). Mac Ctrl passes through as Linux Ctrl (so Cmd+S saves, Ctrl+C still interrupts). Per-bookmark checkbox **"Swap Cmd/Ctrl for this server"** is ON by default when destination is Linux, OFF by default when destination is Mac. Active swap is surfaced in the health overlay so artists can see state at a glance. A single authoritative table in `common/keymap.py` — never inferred at call site per PITFALLS #4.
- **D-11: Release-all-modifiers fires on four triggers:**
  - `RemoteViewer.focusOutEvent` / Qt `WindowDeactivate` — immediate `KEY_RESET_MODIFIERS` protocol message (fixes "Ctrl stuck after Cmd-Tab")
  - ConnectionSupervisor reconnect — first message sent after re-auth is release-all (fixes network-stall repeat-runaway)
  - Server-side periodic safety net — `InputInjector.reset_modifiers()` every ~10s while no keyboard events have arrived (belt-and-suspenders)
  - Client-side panic shortcut — **F9** (or similar, surfaced in Help menu) releases all modifiers on demand as an explicit user escape hatch
  All four mechanisms share the same server-side idempotent reset path. `server/platform_backends.py::InputInjector.reset_modifiers()` already exists from Phase 1 — wire it to a `KEY_RESET_MODIFIERS` message type in `common/messages.py`.
- **D-12: Keymap test scope = exhaustive Qt → Linux + Mac × all modifier chords.** Every key in `common/keymap.py` × {none, Shift, Ctrl, Alt, Meta, and every 2-key and 3-key modifier combination} × {Linux scancode, Mac virtual key code}. ~2000 parameterized pytest cases driven by a table in `tests/common/test_keymap.py` (the file exists from Phase 1 with a stub). Named subset for the Flame critical-path hotkey set marked explicitly for the INPUT-04 integration test. Extends rather than replaces the Phase-1 `test_keymap.py` scaffolding.
- **D-13: Server-side key repeat = `xset r off` + client-driven repeats.** On Linux virtual displays, run `xset -display $DISPLAY r off` at session start so the X server never auto-repeats. Client sends explicit `N` repeats if a key is held. Network stall → repeat just stops mid-air. Matches the PCoIP pattern. On Mac, `CGEventPost` doesn't auto-repeat either — same client-driven model.
- **D-14: Caps Lock sync = bit in every KeyEvent.** Client includes `caps_lock_on: bool` (plus Num / Scroll Lock state bits while we're in there) in every `KeyEvent` message. Server auto-corrects its virtual display's lock state on mismatch. Zero round-trip cost; state re-converges on the next keystroke. Extends the existing `KeyEvent` dataclass in `common/messages.py`.
- **D-15: International keyboard / IME coverage = US + UK + DE + JP layouts + IME passthrough.** REQ-INPUT-05 is hit literally. Test matrix covers US, UK, DE, JP layouts (documented in a test plan; manual smoke-tested pre-release on DXS lab Macs). `RemoteViewer.inputMethodEvent` handler forwards commit strings as unicode text via a new `TextCommit` protocol message — NOT synthesized keycodes, which mangle dead-key composition. Non-covered layouts (FR, ES, CN, KR, etc.) are documented as "likely works, unsupported in v1" in the troubleshooting docs.

### Wacom Hardware Verification (INPUT-09..12)

- **D-16: Real-hardware matrix cadence = one-shot gate + per-release manual ritual.** Run the 4-cell matrix (Intuos Pro Large + Cintiq Pro 24 × macOS Sonoma + macOS Sequoia) ONCE at Phase 2 completion to gate phase verification. Repeat on every release tag (documented in `docs/release.md` as a Phase 6 runbook item). **No self-hosted CI runners for Wacom in v1** — preserves Phase 1 D-05 "GitHub-hosted only" discipline. Regressions between releases are caught by synthetic keymap tests + code review.
- **D-17: Pass criteria = hybrid protocol + automated counters.** Structured checklist protocol (drawn from PITFALLS #3):
  1. Pressure ramp — draw a slow 0→max stroke over ~5s (INPUT-12 measurement — see D-18)
  2. Eraser flip — turn pen over, stroke, verify eraser path fires (INPUT-10)
  3. Tilt test — hold pen tilted, verify `xTilt` / `yTilt` values vary correctly
  4. Proximity cycle — lift pen out of range and back in, verify proximity-enter / proximity-leave events (INPUT-11)
  5. Tablet-side buttons — press each side button while stroking, verify button events round-trip (INPUT-10)
  6. Reconnect mid-stroke — disconnect WebSocket during a pressure stroke, reconnect, verify no stuck pen / no leaked pressure
  Each step has an automated counter on the server side (via the existing `structlog` per-stage telemetry from Phase 1): "pressure samples received: N", "tilt events: M", "proximity transitions: 2", "button events: K". Pass = every counter meets its assertion **AND** manual sign-off on "pressure curve looks right in a Flame paint stroke." **Record video** of the session; attach to the release runbook.
- **D-18: Pressure-quantization error measurement (INPUT-12) = known-ramp + RMS error.** Artist draws a slow linear pressure ramp (0 → max over ~5s). Client logs `QTabletEvent.pressure` samples with timestamps; server logs injected HID report pressure values with timestamps. Python post-processing script aligns timestamps, computes RMS error normalized to max pressure. **Pass gate: RMS < 1% across all 4 cells.** Graph committed to the release notes so future regressions are visible at a glance. Script lives in `tools/wacom_quant_analysis.py`.
- **D-19: Proximity-event recovery = synthesize on `focusIn` + `showEvent`.** `RemoteViewer.focusInEvent` and `showEvent` send a synthesized proximity-enter toward the server so server pen FSM converges after screen lock, minimize/restore, or Cmd-Tab cycles. Server's pen FSM tolerates duplicate proximity events idempotently (state machine transition `in_proximity → in_proximity` is a no-op). Fixes the PITFALLS #3 "proximity event eaten by lockscreen" class of bug.
- **D-20: macOS TCC / Wacom driver detection = detect + guide on client.** Client reads TCC DB read-only (`sqlite3` on `~/Library/Application Support/com.apple.TCC/TCC.db`). If **Input Monitoring** is missing for the Teraguchi client, OR **Accessibility** is missing for the installed Wacom driver (`com.wacom.*`), show an onboarding panel with deep-link buttons: "Open System Settings → Privacy → Input Monitoring", etc. Surfaces "why did my pen stop working" into the UX instead of into the logs. Bakes into the existing `client/key_diagnostic.py` dialog as a new "Wacom setup" tab.

### Phase 2 Latency Gate

- **D-21: Latency gate = keep Phase 1 synthetic gate + add manual DXS measurement.** CI retains the Phase 1 synthetic p99 < 25ms gate against mocked encoders (D-08/09 from Phase 1). Phase 2 verification step adds a one-time real-hardware DXS latency measurement — compare pre-Phase-2 to post-Phase-2 end-to-end input-to-photon on a DXS Flame workstation + real Wacom + real Cintiq. Commit the number to `docs/release.md`. If post-Phase-2 is slower than pre-Phase-2 (despite all the new work), that's a regression per the "Indistinguishable from local" core-value principle and triggers investigation before phase sign-off.

### Claude's Discretion

The planner has freedom on these — they fall out naturally from the decisions above:

- **Metal shader sources** for 10-bit YUV→RGB conversion (D-02) — pick a stock approach (BT.709 matrix) and cite the reference.
- **`KEY_RESET_MODIFIERS` / `TextCommit` protocol message wire format** (D-11 / D-15) — extend `common/messages.py` following the existing pattern; match the server-side dispatcher shape.
- **Exact `ENCODER_DEFS` edits in `server/video_encoder.py`** to surface `supports_main10`, `supports_422`, `supports_444` capability flags per encoder per GPU family.
- **`mac_video_encoder.py` internal structure** (D-04) — how `VTCompressionSession` lifecycle is managed (async wrapper around the synchronous VT callback).
- **Test fixture asset location** — where the 10-bit ramp binary lives (suggest `tests/smoke/fixtures/` consistent with Phase 1).
- **FSM additions for pen proximity** (D-19) — extend the existing `common/session_fsm.py` with a pen sub-state or run a separate pen FSM on the server.
- **Specific module layout for the Wacom quantization analysis tool** (D-18) — one-file script vs module with CLI.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents (researcher, planner, executor) MUST read these before planning or implementing.**

### Project / Phase context

- `.planning/PROJECT.md` — Core Value, 10-bit non-negotiable constraint, Out-of-Scope (ICC / HDR tone-mapping / HIDDriverKit etc.), Key Decisions
- `.planning/REQUIREMENTS.md` — full 108 requirements; Phase 2 owns INPUT-01..12 (12) and VIDEO-01..12 (12)
- `.planning/ROADMAP.md` §"Phase 2: Input + Color Fidelity" — goal, requirement map, 5 success criteria, pre-phase + in-phase spikes, dependency on Phase 1
- `.planning/STATE.md` — Performance Metrics targets (sub-20ms LAN, 60fps LAN, 30fps WAN fallback, 10-bit end-to-end)
- `.planning/phases/01-stability-ci-test-baseline/01-CONTEXT.md` — Phase 1 locked decisions (D-01..D-18) that Phase 2 builds on: FSM library, test-at-FFmpeg-subprocess-boundary, synthetic latency gate, structlog telemetry, GHA runner topology
- `.planning/phases/01-stability-ci-test-baseline/01-VERIFICATION.md` — proof that Phase 1 actually delivered (229 tests pass + 1 xfail, 4 CI gates, 1-hour smoke harness)
- `CLAUDE.md` — project guide; lists 9 silent 10-bit downgrade points as known traps

### Research outputs (consult before planning Phase 2 work)

- `.planning/research/PITFALLS.md` §"Pitfall 2: 10-Bit Pipeline Silently Degrades to 8-Bit" — the 9 downgrade points in table form with warning signs; D-01 / D-02 / D-03 are direct responses
- `.planning/research/PITFALLS.md` §"Pitfall 3: Wacom Pressure Dropping, Tilt Inverted, Eraser Ignored" — informs D-16 / D-17 / D-19 / D-20 (real-hardware matrix, proximity recovery, TCC detection)
- `.planning/research/PITFALLS.md` §"Pitfall 4: Modifier Key Desync" — informs D-10 / D-11 / D-13 (Cmd↔Ctrl, release-all triggers, server `xset r off`)
- `.planning/research/STACK.md` §"Video Encode (Server, Linux + NVIDIA)" — NVENC hardware capability matrix by GPU generation; drives D-03 probe implementation and D-05 chroma policy
- `.planning/research/STACK.md` §"Video Encode (Server, macOS)" — VideoToolbox API direct-use rationale (5-8ms savings); drives D-04 wrapper construction
- `.planning/research/STACK.md` §"Input Injection" — IOHIDUserDevice pattern, Wacom-qemu reference; drives D-07 / D-08 / D-09 spike and module split
- `.planning/research/STACK.md` §"Screen Capture" — ScreenCaptureKit `.hdrLocalDisplay` + `kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange` per WWDC24 session 10088
- `.planning/research/SUMMARY.md` §"Critical Path (Blocking Items)" — Phase 2 builds on Phase 1's foundation; do not re-litigate settled choices

### Existing-code analysis (codebase as-it-is-now, post-Phase-1)

- `.planning/codebase/STRUCTURE.md` — current directory layout and "Where to Add New Code" recipes (client/, server/, common/)
- `.planning/codebase/CONCERNS.md` §"Known Bugs" — `mac_input_injector.pen_event` silently drops pen pressure (addressed by D-07..D-09); `xrandr` resize crashes NVIDIA (Phase 3 territory, not Phase 2)
- `.planning/codebase/CONCERNS.md` §"Platform Parity Gaps" — "Pen tablet: no IOHIDUserDevice helper" (D-07 addresses)
- `.planning/codebase/CONCERNS.md` §"Test Coverage Gaps" — keymap round-trip not tested (D-12 addresses), macOS backends freshly added (D-04 mac_video_encoder needs tests)
- `.planning/codebase/TESTING.md` — Phase 1 delivered tests/ layout with common/client/server/integration/smoke subdirs; Phase 2 extends tests/common/test_keymap.py and adds tests/server/test_mac_video_encoder.py (mocked at VT subprocess boundary) + tests/server/test_mac_pen_injector.py (mocked at IOKit boundary)

### Relevant external docs (informs but does not dictate planning)

- Apple WWDC21 Session 10158 — `kVTVideoEncoderSpecification_EnableLowLatencyRateControl` — drives D-04
- Apple WWDC24 Session 10088 — ScreenCaptureKit HDR / 10-bit config — drives capture-side of D-01
- NVIDIA Video Codec SDK 13.0 docs — NVENC Main10 / 4:2:2 / 4:4:4 capability matrix — drives D-03 probe
- Wacom-qemu (community project) — IOHIDUserDevice virtual tablet pattern — reference for the D-07 spike
- OBS forum "NVENC error: cannot perform 10-bit encode on this encoder" — real-world NVENC silent-fallback evidence; drives D-03

### Phase 2 will produce or update

- `server/mac_video_encoder.py` — **NEW** — PyObjC wrapper around VTCompressionSession with low-latency rate control (D-04)
- `server/mac_pen_injector.py` — **NEW** — IOHIDUserDevice-based virtual HID tablet (D-07..D-09)
- `tools/wacom_quant_analysis.py` — **NEW** — RMS pressure-quantization error script (D-18)
- `tests/smoke/fixtures/` — **EXTEND** — 10-bit ramp test pattern binary + reference decoder output (D-01)
- `tests/common/test_keymap.py` — **EXTEND** — exhaustive Qt × modifier × platform table (D-12)
- `tests/server/test_mac_video_encoder.py` — **NEW** — mock-at-VT-boundary tests (mirrors Phase 1 D-02 philosophy)
- `tests/server/test_mac_pen_injector.py` — **NEW** — mock-at-IOKit-boundary tests
- `tests/integration/test_crazy_hotkeys.py` — **NEW** — in-process loopback INPUT-04 (uses Phase 1 integration infra)
- `client/viewer.py` — **REFACTOR** — Metal/QRhi direct texture upload for video layer (D-02)
- `common/keymap.py` — **EXTEND** — Qt → Mac virtual key code table added alongside Qt → Linux scancode
- `common/messages.py` — **EXTEND** — `KEY_RESET_MODIFIERS`, `TextCommit`, `PenProximity` message types; extend `KeyEvent` with lock-state bits
- `server/platform_backends.py` — **EXTEND** — capability-probe results surfaced to `ServerHelloMsg`
- `server/mac_input_injector.py` — **REFACTOR** — thin delegation to `mac_pen_injector` for pen events (D-09)
- `client/key_diagnostic.py` — **EXTEND** — new "Wacom setup" tab covering TCC detection + deep-links (D-20)
- `docs/release.md` — **SEED** — capture the Wacom matrix runbook (D-16) + DXS latency number (D-21); lives on through Phase 6

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- `common/keymap.py` — 120 lines, Qt → Linux scancode only; Phase 2 extends with Qt → Mac virtual key code and broader modifier coverage. Table-driven shape already correct for the exhaustive-test expansion in D-12.
- `common/messages.py` — 588 lines; dataclass + `MsgType` + binary header encoders already in place. Phase 2 additions (KEY_RESET_MODIFIERS, TextCommit, PenProximity, capability flags on ServerHelloMsg) follow established patterns.
- `common/session_fsm.py` — Phase 1 deliverable; `python-statemachine`. Pen-proximity FSM (D-19) can extend or run alongside the session FSM.
- `server/video_encoder.py` — 870 lines; `ENCODER_DEFS`, `_nvenc_args`, `_videotoolbox_args`, pipeline. D-03 capability flags slot into `HWEncoder` dataclass. D-04 replaces `_videotoolbox_args` path on Mac with a call-out to `mac_video_encoder.py`.
- `server/mac_input_injector.py` — 449 lines; `pen_event()` at lines 358-398 is the downgrade-to-mouse stub. D-09 keeps this file but thins out the pen path to delegation.
- `server/platform_backends.py` — dispatcher. `InputInjector` composition grows to hold both `mac_input_injector` and `mac_pen_injector`.
- `client/viewer.py` — 537 lines; `tabletEvent` handler at line 315 already forwards pressure / tilt / rotation. D-02 replaces the `QPainter.drawImage` blit path for video. D-19 adds `focusInEvent` / `showEvent` proximity re-synthesis.
- `client/video_decoder.py` — 298 lines; PyAV with videotoolbox hwaccel tuple already in `_CODEC_ORDER`. D-01 checkpoint 5 asserts `frame.format` membership.
- `client/key_diagnostic.py` — Phase 1's diagnostic pattern. D-20 extends as a new tab for Wacom / TCC UX.
- `tests/common/test_keymap.py` — Phase 1 stub; D-12 builds the exhaustive parameterized table.
- `tests/server/test_video_encoder_mock.py` — Phase 1 scaffolding; D-04's `mac_video_encoder` gets a parallel test file.
- `tests/integration/` directory + `conftest.py` — Phase 1's in-process loopback fixtures; INPUT-04 integration test extends the pattern.
- `tests/smoke/fixtures/` — Phase 1's smoke-test fixtures location; D-01's 10-bit ramp lives here.

### Established Patterns

- Platform split follows `mac_<feature>.py` vs plain-named Linux file convention — `mac_pen_injector.py` fits.
- `structlog` JSON logging with per-stage telemetry from Phase 1 — D-17's Wacom matrix counters slot in as new telemetry fields.
- Mock-at-FFmpeg-subprocess-boundary for encoder tests (Phase 1 D-02) — mirror with mock-at-VT-boundary and mock-at-IOKit-boundary.
- `pyobjc-framework-*` usage already established on the Mac server (SCK, CoreGraphics, NSPasteboard) — adding `pyobjc-framework-VideoToolbox` + `pyobjc-framework-IOKit` is additive.
- `python-statemachine` FSM idioms from Phase 1 — pen FSM / proximity handling applies the same pattern.
- `requirements-server.txt` is the right place for new PyObjC framework deps; `requirements-dev.txt` for any new testing libraries.

### Integration Points

- **10-bit pipeline checkpoints** (D-01 / D-02): capture (`server/screen_capture.py::capture_raw_bgra` + `server/mac_screen_capture.py`), encoder input (`server/video_encoder.py::_build_ffmpeg_cmd`), encoder output (ffprobe on dumped sample), transport (on-wire HEVC NAL type bytes), decoder output (`client/video_decoder.py` `AVFrame.format` assertion), display surface (new Metal path in `client/viewer.py`).
- **Capability probe surface** (D-03): `server/main.py` server bootstrap wires probe results into `ServerHelloMsg` via `common/messages.py`. `client/protocol.py` parses the probe fields; `client/health_display.py` (from Phase 1) renders the `"10-bit: …"` badge.
- **Reset-modifiers protocol wiring** (D-11): `common/messages.py` adds `MsgType.KEY_RESET_MODIFIERS`. `client/viewer.py::focusOutEvent` + `client/connection_supervisor.py` reconnect hook + F9 shortcut all dispatch the same message. `server/platform_backends.py::InputInjector.reset_modifiers` consumes.
- **IOHIDUserDevice lifecycle** (D-07/D-09): `server/mac_pen_injector.py` owns a long-lived virtual HID device per session. `SessionRuntime` (from Phase 1 D-11) creates + tears down. Pen events from protocol dispatch to `mac_pen_injector.pen_event` via `platform_backends.InputInjector`.
- **Wacom matrix counters** (D-17): `structlog` events tagged `event=wacom_matrix`, `stage=<pressure|tilt|proximity|button|reconnect>`; `tools/wacom_quant_analysis.py` consumes the JSON log stream for D-18 RMS calculation.
- **TCC detection** (D-20): read-only sqlite3 against `~/Library/Application Support/com.apple.TCC/TCC.db`. No write path, no bypass attempt. Results feed into `client/key_diagnostic.py` Wacom tab.

</code_context>

<specifics>
## Specific Ideas

- **"Indistinguishable from local" → Phase 2 owns the color + input halves of that promise.** If Phase 2 ships and a Flame artist can't feel the 10-bit color or their pen pressure has any quantization artifact, Phase 2 didn't deliver. The hybrid pass-criteria of D-17 + the RMS <1% gate of D-18 exist because "we think it's fine" isn't enough — Flame artists will tell us within 5 minutes.
- **Rocky server is the Flame production path.** D-07's "document + ship" failure plan leans on this: if Mac-server pen pressure can't be made honest in v1, the Flame workflow is not blocked — it just lives on the Rocky side. Mac server is dev / testing / light-editing in v1, not Flame-primary. This framing removes the temptation to over-invest in HIDDriverKit for a use-case that isn't load-bearing for DXS.
- **The IOHIDUserDevice spike is a Phase 2 pre-gate, not a Phase 2 deliverable per se.** The spike itself lands in Phase 2's timeline but conceptually it decides scope. If spike succeeds, Phase 2 has a new module (`mac_pen_injector.py`) + tests + Wacom matrix pass; if spike fails, Phase 2 has a re-scoped INPUT-08 + a docs note + v1.1 backlog entry. The planner should sequence the spike first and branch the rest of the phase on the outcome.
- **The Metal/QRhi rewrite of the video blit (D-02) is the single largest client-side change in Phase 2.** Worth its own plan. Accept a QImage-based fallback path for overlays + cursor; video layer goes Metal. The Qt 6.10 QRhi API is stable enough (per Phase 1 STACK.md version lock). If QRhi proves too painful, escape hatch is a `QOpenGLWidget` with a custom shader pair — same idea, older Qt pattern.
- **Bulletproof definition, narrowed to Phase 2**: Phase 1 already covered "zero crashes / disconnects" via FSM + supervisor + queue fix. Phase 2 directly owns the remaining three: zero Wacom pressure glitches (D-16..D-20), zero hotkey combo mangling (D-10..D-15), and zero video stutter or color shift (D-01..D-06 + D-21). After Phase 2 ships, all four bulletproof-failure-modes have at least one structural defense.
- **Do not regress Phase 1.** All Phase 2 work must pass Phase 1's 4 CI gates unchanged (pytest, ruff+mypy, build artifacts, synthetic latency p99<25ms). The Phase 2 verification step re-runs the 1-hour synthetic smoke harness to prove no stability regression.

</specifics>

<deferred>
## Deferred Ideas

- **HIDDriverKit signed system extension** → v1.1. Apple DriverKit entitlement (`com.apple.developer.driverkit.family.hid.virtual.device`) requires special Apple approval not granted on default Developer IDs. Even if approved, system-extension distribution path breaks the "download a .app and run it" model. Revisit when a real studio demands it.
- **Full ICC profile pipeline / display calibration management** → out of scope for v1 entirely per PROJECT.md (not deferred — excluded). Revisit when a grading studio asks.
- **HDR tone mapping on client** → same bucket. v1 ships 10-bit pass-through only; HDR / EDR-aware tone mapping is a v1.x feature if it surfaces.
- **4:4:4 HEVC as a first-class user-facing mode** → stays in `ENCODER_DEFS` for completeness but not surfaced in the quality slider. Post-v1 enhancement when Blackwell + M4 encode is ubiquitous and software-decode isn't a latency penalty on the client.
- **FR / ES / IT / CN / KR keyboard layouts** → v1.1. Phase 2 hits the REQ-INPUT-05 minimum (US / UK / DE / JP). International VFX studios beyond those can file an issue for v1.1 coverage.
- **Apple Vision Pro / visionOS client** → way post-v1 if ever. Out of Scope.
- **"Aggressive capture" CGEventTap for trap keys (Cmd+Tab, Spotlight)** → mentioned in PITFALLS #4 as a fix for macOS swallowing certain key chords. Phase 2 does NOT add a CGEventTap — requires Accessibility TCC and adds a non-trivial UX explanation. Document the limitation instead; revisit if Flame-on-Mac-server becomes load-bearing (which D-07 says it isn't in v1).
- **Self-hosted DXS CI runners for real-hardware checks** → preserved from Phase 1 deferred list. Same tradeoff (ties contributors to DXS infra). Reconsider in v1.1 if synthetic testing leaks too many real-HW bugs.
- **Full 8-hour smoke harness** → Phase 4. Phase 2 continues to run the 1-hour version from Phase 1 D-18. Long-window behavior (audio, sample-rate drift) needs Phase 4's scope anyway.
- **Sentry crash reporting** → Phase 7 (observability polish). Phase 2 uses structlog per Phase 1 D-01.
- **Prometheus `/metrics` endpoint (OBS-04)** → explicitly Phase 5. Don't pull forward.
- **`server/main.py` `DISPLAY` env-var thread-safety** (CONCERNS.md fragile area) → Phase 3 if session-manager work lands there, otherwise leave for a post-v1 cleanup pass.

### Reviewed Todos (not folded)

None — `node ~/.claude/get-shit-done/bin/gsd-tools.cjs list-todos` returns `count: 0`.

</deferred>

---

*Phase: 02-input-color-fidelity*
*Context gathered: 2026-04-18*
