---
phase: 2
slug: input-color-fidelity
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-04-18
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Source: `02-RESEARCH.md` §Validation Architecture (Nyquist signals for each of the 5 ROADMAP success criteria + the 9 silent 10-bit downgrade checkpoints).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (Phase 1 baseline: 229 tests + 1 xfail) |
| **Config file** | `pyproject.toml` (pytest + ruff + mypy), `.github/workflows/ci.yml` (4 CI gates) |
| **Quick run command** | `pytest -x -q tests/common tests/client tests/server` |
| **Full suite command** | `pytest` (includes `tests/integration` + `tests/smoke`) |
| **Estimated runtime** | ~90 seconds quick; ~6 minutes full (including the 1-hour smoke harness excluded by default marker) |

Additional gates (inherited from Phase 1, must stay green):

- `pytest` (all marker-default tests)
- `ruff check . && mypy .` (lint + type)
- Build artifact smoke (`python -m client --version`, `python -m server --version`)
- Synthetic latency p99 < 25ms (Phase 1 D-08/D-09 gate; Phase 2 must not regress)

Phase 2 adds:

- **9-checkpoint 10-bit fixture** — gated in `pytest tests/smoke/test_ten_bit_pipeline.py` (fast path: checkpoints 1–6 via mocks; slow path: checkpoints 7–9 via GPU-optional mark, skipped on GHA)
- **Exhaustive keymap matrix** — `pytest tests/common/test_keymap.py` (~2000 parametrized cases, <5s)
- **Crazy hotkeys integration** — `pytest tests/integration/test_crazy_hotkeys.py` (in-process loopback)
- **Wacom pressure RMS** — `tools/wacom_quant_analysis.py <jsonl>` — manual, gated in Phase 2 verification only

---

## Sampling Rate

- **After every task commit:** Run `pytest -x -q <files_touched>` (at minimum the targeted test files)
- **After every plan wave:** Run `pytest -x -q tests/common tests/client tests/server` + `ruff check .`
- **Before `/gsd-verify-work`:** Full suite green + 9-checkpoint fixture green + exhaustive keymap green + synthetic latency gate green
- **Max feedback latency:** ~90s quick cycle; ~6 min full cycle

---

## Per-Task Verification Map

> One row per task across all 12 plans. Every task must either link to an automated test command here OR declare a Wave 0 dependency. No task may ship with "manual only" unless it appears in `## Manual-Only Verifications`.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 0 | INPUT-01, INPUT-04, INPUT-08, VIDEO-02 | T-02-01, T-02-02 | accept (repo hygiene), mitigate (size cap) | unit | `python tools/gen_10bit_ramp.py --width 1920 --height 1080 --out /tmp/ramp_verify.bin && test $(stat -f%z /tmp/ramp_verify.bin 2>/dev/null || stat -c%s /tmp/ramp_verify.bin) -eq 7776000 && test -s tests/smoke/fixtures/10bit_ramp.p010.bin && python -c "import json; d=json.load(open('tests/smoke/fixtures/10bit_ramp.reference.ffprobe.json')); assert d['checkpoints']['encoder_output']['ffprobe_pix_fmt']=='yuv420p10le'"` | ✅ W0 | ⬜ pending |
| 02-01-02 | 01 | 0 | INPUT-01, INPUT-04, INPUT-08, VIDEO-02 | T-02-03 | accept (operator-local) | unit | `pytest -x --co -q tests/smoke/test_ten_bit_pipeline.py tests/common/test_keymap.py tests/integration/test_crazy_hotkeys.py tests/server/test_mac_video_encoder.py tests/server/test_mac_pen_injector.py tests/server/test_hw_capability_probe.py tests/common/test_messages_phase2.py && python tools/wacom_quant_analysis.py --client-log /dev/null --server-log /dev/null --cell test --out-svg /tmp/x.svg; [ $? -eq 2 ]` | ✅ W0 | ⬜ pending |
| 02-01-03 | 01 | 0 | INPUT-01, INPUT-04, INPUT-08, VIDEO-02 | — | N/A | unit | `grep -E "flame_critical\|wacom_hw\|ten_bit_smoke" pyproject.toml && python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); markers=d['tool']['pytest']['ini_options']['markers']; assert any('flame_critical' in m for m in markers)"` | ✅ W0 | ⬜ pending |
| 02-02-01 | 02 | 1 | INPUT-01, INPUT-02, INPUT-03, INPUT-05, INPUT-06, INPUT-07, INPUT-11, VIDEO-03, VIDEO-05, VIDEO-09 | T-02-04 | mitigate (post-auth + reason whitelist) | unit | `pytest tests/common/test_messages_phase2.py -x -q` | ✅ W0 | ⬜ pending |
| 02-02-02 | 02 | 1 | INPUT-02, INPUT-03, INPUT-05, INPUT-06, INPUT-07, INPUT-11, VIDEO-03, VIDEO-05, VIDEO-09 | T-02-05 | mitigate (schema validation) | unit | `pytest tests/common/test_messages_phase2.py -x -q` | ✅ W0 | ⬜ pending |
| 02-03-01 | 03 | 1 | INPUT-02, INPUT-04, INPUT-05 | T-02-06 | accept (table-only, no execution) | unit | `pytest tests/common/test_keymap.py -x -q` | ✅ W0 | ⬜ pending |
| 02-03-02 | 03 | 1 | INPUT-02, INPUT-04, INPUT-05 | T-02-07 | mitigate (parametrized coverage) | unit | `pytest tests/common/test_keymap.py -x -q` | ✅ W0 | ⬜ pending |
| 02-04-01 | 04 | 1 | VIDEO-03, VIDEO-05, VIDEO-08, VIDEO-09 | T-02-10, T-02-11 | mitigate (post-auth + timeouts) | unit | `pytest tests/server/test_hw_capability_probe.py -x -q && ruff check server/capability_probe.py tests/server/test_hw_capability_probe.py && mypy server/capability_probe.py` | ✅ W0 | ⬜ pending |
| 02-04-02 | 04 | 1 | VIDEO-03, VIDEO-05, VIDEO-08, VIDEO-09 | T-02-12 | accept (server claim cross-checked by decoder checkpoints) | unit | `pytest tests/server/test_video_encoder_main10.py tests/server/test_hw_capability_probe.py tests/client/test_health_display.py -x -q && ruff check server/video_encoder.py server/bootstrap.py client/health_display.py && mypy server/video_encoder.py server/bootstrap.py` | ✅ W0 | ⬜ pending |
| 02-05-01 | 05 | 2 | VIDEO-01, VIDEO-02, VIDEO-04 | T-02-13 | mitigate (threading discipline) | unit | `pytest tests/server/test_mac_video_encoder.py -x -q` | ✅ W0 | ⬜ pending |
| 02-05-02 | 05 | 2 | VIDEO-01, VIDEO-02, VIDEO-04 | T-02-14 | mitigate (deferred PyObjC guard) | unit | `pytest tests/server/test_mac_video_encoder.py -x -q` | ✅ W0 | ⬜ pending |
| 02-05-03 | 05 | 2 | VIDEO-01, VIDEO-02, VIDEO-04 | T-02-15 | mitigate (call_soon_threadsafe bridge) | unit | `pytest tests/server/test_mac_video_encoder.py -x -q` | ✅ W0 | ⬜ pending |
| 02-06-01 | 06 | 3 | VIDEO-01, VIDEO-02, VIDEO-03, VIDEO-05, VIDEO-06, VIDEO-07, VIDEO-10, VIDEO-11 | T-02-17, T-02-18 | mitigate (STAB-04 queue fix + color_caps assert) | unit | `pytest tests/server/test_video_encoder_mock.py -x -q && ruff check server/video_encoder.py` | ✅ W0 | ⬜ pending |
| 02-06-02 | 06 | 3 | VIDEO-01, VIDEO-02, VIDEO-05, VIDEO-09 | T-02-16 | accept (server-owned setup_params) | smoke | `pytest tests/smoke/test_ten_bit_pipeline.py::test_checkpoint_2_encoder_input_is_p010 tests/smoke/test_ten_bit_pipeline.py::test_checkpoint_3_encoder_output_profile_main10 tests/smoke/test_ten_bit_pipeline.py::test_checkpoint_4_wire_general_profile_idc_is_2 -x -q && ruff check server/screen_capture.py` | ✅ W0 | ⬜ pending |
| 02-07-01 | 07 | 4 | VIDEO-01, VIDEO-02 | T-02-19 | mitigate (CI no-diff check) | unit | `test -f client/shaders/video_blit.vert && test -f client/shaders/video_blit.frag && test -f scripts/build_shaders.sh && test -x scripts/build_shaders.sh && grep -q "1.5748" client/shaders/video_blit.frag && grep -q "1.1643" client/shaders/video_blit.frag && grep -q "BT.709" client/shaders/video_blit.frag && ! grep -q "BT.2020" client/shaders/video_blit.frag` | ✅ W0 | ⬜ pending |
| 02-07-02 | 07 | 4 | VIDEO-01, VIDEO-02 | T-02-20, T-02-21 | mitigate (legacy-GL escape hatch), accept (Metal path is v1 purpose) | unit | `pytest tests/smoke/test_ten_bit_pipeline.py::test_checkpoint_7_qrhi_texture_formats_are_r16_rg16 -x -q && grep -q "QRhiWidget" client/viewer.py && grep -q "VideoBlitWidget" client/viewer.py && grep -q "video_blit.frag.qsb" client/viewer.py && grep -qE "legacy-gl-blit\|LEGACY_GL_BLIT" client/viewer.py` | ✅ W0 | ⬜ pending |
| 02-08-01 | 08 | 5 | VIDEO-01, VIDEO-02, VIDEO-04 | T-02-22 | mitigate (hwaccel backend assertion) | unit | `pytest tests/client/test_video_decoder_p010.py -x -q` | ❌ W0 | ⬜ pending |
| 02-08-02 | 08 | 5 | VIDEO-01, VIDEO-02, VIDEO-04 | T-02-23 | mitigate (plane copy bounds) | smoke | `pytest tests/smoke/test_ten_bit_pipeline.py::test_checkpoint_5_decoder_output_format_is_p010 tests/smoke/test_ten_bit_pipeline.py::test_checkpoint_6_decoder_hwaccel_is_videotoolbox -x -q` | ✅ W0 | ⬜ pending |
| 02-09-01 | 09 | 6 | INPUT-01, INPUT-02, INPUT-05, INPUT-06 | T-02-24, T-02-25 | mitigate (wire contract tests) | unit | `pytest tests/client/test_viewer_modifier_triggers.py tests/common/test_keymap.py -x -q && grep -qE "reset_modifiers_requested\|focusOutEvent" client/viewer.py && grep -q "inputMethodEvent" client/viewer.py && grep -q "text_commit" client/viewer.py` | ❌ W0 | ⬜ pending |
| 02-09-02 | 09 | 6 | INPUT-02, INPUT-05, INPUT-07 | T-02-24 | mitigate (signal plumbing) | unit | `pytest tests/client/test_viewer_modifier_triggers.py -x -q && grep -q "swap_cmd_ctrl" client/bookmarks.py && grep -qE "F9\|QKeySequence" client/main_window.py` | ❌ W0 | ⬜ pending |
| 02-09-03 | 09 | 6 | INPUT-01, INPUT-03, INPUT-05, INPUT-06 | T-02-24, T-02-25 | mitigate (dispatch plumbing + xset) | unit | `grep -qE "reset_modifiers\(\)" server/client_session.py server/session_runtime.py 2>/dev/null && grep -qE "xset.*r off" server/session_manager.py && grep -qE "text_commit" server/input_injector.py server/mac_input_injector.py` | ✅ existing | ⬜ pending |
| 02-09-04 | 09 | 6 | INPUT-02, INPUT-03, INPUT-05, INPUT-06 | T-02-26 | mitigate (`_should_fire_periodic_reset` held-chord guard) | integration | `pytest tests/integration/test_modifier_stress.py -x -q` | ❌ W0 | ⬜ pending |
| 02-10-00 | 10 | 7 | INPUT-08, INPUT-11 | T-02-27 | accept (user-space HID API) | checkpoint | MISSING — D-07/D-08 spike (manual checkpoint, gates Task 1 vs Task 2 branch) | N/A | ⬜ pending |
| 02-10-01 | 10 | 7 | INPUT-08, INPUT-11 | T-02-28 | mitigate (atexit + SIGTERM/SIGINT cleanup) | unit | `pytest tests/server/test_mac_pen_injector.py -x -q && ruff check server/mac_pen_injector.py server/mac_input_injector.py server/platform_backends.py server/session_runtime.py` | ✅ W0 | ⬜ pending |
| 02-10-02 | 10 | 7 | INPUT-08 | T-02-27, T-02-28 | accept (v1 documented limitation) | unit | `test -f docs/release.md && grep -qE "INPUT-08\|v1 known-limitation\|re-scoped" docs/release.md && pytest tests/server/test_mac_pen_injector.py -x -q` | ✅ W0 | ⬜ pending |
| 02-10-03 | 10 | 7 | INPUT-08, INPUT-11 | T-02-29 | accept (public Wacom IDs) | unit | `pytest tests/common/test_session_fsm_pen.py -x -q && pytest tests/client/test_viewer_proximity.py -x -q && grep -q "class PenFSM" common/session_fsm.py && grep -qE "focusInEvent\|showEvent" client/viewer.py && grep -qE "pen_proximity" client/viewer.py` | ❌ W0 (new `tests/client/test_viewer_proximity.py`) | ⬜ pending |
| 02-11-01 | 11 | 8 | INPUT-04 | T-02-24 | mitigate (loopback contract) | integration | `pytest tests/integration/test_crazy_hotkeys.py -m flame_critical -x -q` | ✅ W0 | ⬜ pending |
| 02-11-02 | 11 | 8 | D-20 onboarding UX (not INPUT-09/-10 event fidelity) | T-02-30..T-02-33 | accept (read-only sqlite3), mitigate (sqlite3.Error catch) | unit | `pytest tests/client/test_tcc_detection.py tests/server/test_mac_pen_injector.py -x -q && grep -q "WacomSetupTab" client/key_diagnostic.py && grep -qE "x-apple.systempreferences\|Privacy_ListenEvent\|Privacy_Accessibility" client/key_diagnostic.py client/tcc_detect.py && grep -q "mode=ro" client/tcc_detect.py` | ✅ W0 (tcc test new; pen-dispatch test extends W0 mac_pen_injector skeleton) | ⬜ pending |
| 02-12-01 | 12 | 9 | INPUT-12 | T-02-34, T-02-35, T-02-36 | accept (operator-local JSONL), mitigate (malformed-line skip + exit 2) | unit | `pytest tests/server/test_wacom_matrix_emission.py -x -q && python tools/wacom_quant_analysis.py --help | grep -qE "RMS\|quant" && test $(wc -l < tools/wacom_quant_analysis.py) -le 250` | ❌ W0 (test file net-new) | ⬜ pending |
| 02-12-02 | 12 | 9 | INPUT-09, INPUT-10, INPUT-11, INPUT-12 | — | N/A | manual | MISSING — 4-cell Wacom matrix (see Manual-Only Verifications) | N/A | ⬜ pending |
| 02-12-03 | 12 | 9 | VIDEO-11 | — | N/A | manual | MISSING — DXS pre/post-Phase-2 latency measurement (see Manual-Only Verifications; gate cites Phase 1 synthetic p99 < 25ms gate, unchanged per D-21) | N/A | ⬜ pending |
| 02-12-04 | 12 | 9 | INPUT-09, INPUT-10, INPUT-11, INPUT-12, VIDEO-11 | — | N/A | unit | `grep -qE "Wacom matrix ritual\|Phase 2 matrix results\|Phase 2 latency measurement" docs/release.md && grep -q "IOHIDUserDevice spike outcome" docs/release.md` | ✅ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

**Planner obligation:** when a PLAN.md task lists `requirements: [INPUT-XX]` or `requirements: [VIDEO-XX]`, it MUST reference the corresponding row(s) here and supply the `automated_command` field. Rows without a command will block the plan-checker.

---

## Wave 0 Requirements

Wave 0 closes infrastructure gaps that block the rest of the phase. Derived from `02-RESEARCH.md` §Validation Architecture "Wave-0 gaps":

- [ ] `tests/smoke/fixtures/ten_bit_ramp.y4m` + `tests/smoke/fixtures/ten_bit_ramp_pixels.json` — synthetic 10-bit ramp with known bottom-2-bit checksum (D-01 checkpoint reference data)
- [ ] `tests/smoke/test_ten_bit_pipeline.py` — the 9-checkpoint byte-equality test harness (stubs allowed; real capture/encode paths hooked by later waves)
- [ ] `tests/common/test_keymap.py` — upgrade Phase 1 stub to the full parametrized table (Qt × modifier × {linux scancode, mac virtual key code}); D-12
- [ ] `tests/integration/test_crazy_hotkeys.py` — in-process loopback scaffold (INPUT-04)
- [ ] `tests/server/test_mac_video_encoder.py` — mock-at-VT-boundary skeleton (mirrors Phase 1 D-02 pattern for FFmpeg)
- [ ] `tests/server/test_mac_pen_injector.py` — mock-at-IOKit-boundary skeleton
- [ ] `tests/server/test_hw_capability_probe.py` — stubs for `supports_main10`/`supports_422`/`supports_444` negotiation (D-03)
- [ ] `tests/common/test_messages_phase2.py` — `KEY_RESET_MODIFIERS`, `TextCommit`, `PenProximity` wire-round-trip (extends `common/messages.py`)
- [ ] `tools/wacom_quant_analysis.py` — argparse script skeleton + one dry-run fixture (D-18)
- [ ] `.github/workflows/ci.yml` — wire the new test selectors; keep the 4 Phase 1 gates unchanged

*Wave 0 is a single plan (suggested 02-01) and must land before any of Waves 1–6 start.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real Wacom hardware matrix — Intuos Pro Large + Cintiq Pro 24 × macOS Sonoma + macOS Sequoia, full D-17 6-step protocol per cell | INPUT-09, INPUT-10, INPUT-11, INPUT-12 | Physical tablet required; Phase 1 D-05 locked "GitHub-hosted CI only" — no self-hosted runners for Wacom in v1 | Follow `docs/release.md` Wacom runbook (seeded in this phase per D-16). Record video. Attach structlog JSONL to release notes. Pass gate: every counter meets assertion AND `tools/wacom_quant_analysis.py` reports RMS < 1% per cell. |
| Pressure-curve qualitative sign-off | INPUT-08, INPUT-12 | "Looks right in a Flame paint stroke" is a perceptual judgment | Flame artist draws standard test stroke on Cintiq Pro 24, signs off that quantization artifacts are not visible. Record the session. |
| DXS end-to-end input-to-photon latency (pre vs post Phase 2) | Success criterion 4 | Requires DXS Flame workstation + real Cintiq + measurement rig — cannot run in GHA | Execute the Phase 1 DXS latency script on a Rocky Flame box with Phase 1 HEAD; repeat with Phase 2 HEAD. Commit the number to `docs/release.md`. Fails phase sign-off if post > pre. CI gate cites Phase 1 synthetic p99 < 25ms (D-08/D-09; unchanged per D-21); real-hardware DXS target remains sub-20ms per CLAUDE.md core value. |
| Reference Mode / EDR / ICC state on MBP XDR | VIDEO-01, D-01 checkpoint 9 | macOS display-pipeline state can't be queried reliably from user-space apps; operator must visually confirm | Document in `docs/release.md` Phase 6 runbook: Reference Mode ON, True Tone OFF, Night Shift OFF, no ICC profile overriding the display. Client health overlay shows the negotiated state (D-03 badge) but the final display path is trusted. |
| 4:2:2 Grading-mode perceptual check | VIDEO-05 | Subjective — "grading feels right" on an M3+ Mac decoding 4:2:2 Main10 | Artist grades a standard reference clip on the 4:2:2 path; signs off. Compared against the 4:2:0 baseline. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or a Wave 0 dependency linked above
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers every MISSING `File Exists` reference
- [ ] No watch-mode flags in any CI command
- [ ] Feedback latency < 90s (quick) / < 360s (full) — confirmed in CI runtime
- [ ] 9-checkpoint 10-bit fixture green in GHA
- [ ] Exhaustive keymap matrix green in GHA
- [ ] Phase 1's 4 CI gates still green (no regression)
- [ ] Manual-only checks above are scheduled in Phase 2 verification runbook
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
