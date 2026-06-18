---
phase: 03
slug: display-multi-monitor-clipboard
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-19
---

# Phase 03 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Source of truth for test design: `03-RESEARCH.md` § "Validation Architecture".
> The planner MUST keep the per-task table below in sync with task IDs in PLAN.md files.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.0+ (already in `requirements-dev.txt`); pytest-asyncio auto mode (set up in Phase 1 STAB-01) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` — existing markers: `latency_bench`, `smoke_1h`, `flame_critical`, `wacom_hw`, `ten_bit_smoke`, `gpu` |
| **Quick run command** | `pytest -m "not wacom_hw and not gpu and not smoke_1h and not latency_bench" -x` |
| **Full suite command** | `pytest` (default `-m 'not wacom_hw'`) |
| **Estimated runtime** | ~30s quick / ~3min full (without smoke_1h or latency_bench) |

---

## Sampling Rate

- **After every task commit:** Run quick command scoped to the file changed: `pytest tests/{server,client,common}/test_<file>.py -x`
- **After every plan wave:** Run quick command across all phase tests
- **Before `/gsd-verify-work`:** Full suite must be green; D-08 4-corner cursor test executed manually on real mixed-DPI client (Retina MBP + external non-Retina monitor) + 2× NVIDIA Xorg topology; ten_bit_smoke fixture re-run with crop active
- **Max feedback latency:** 30s (quick), 3min (full)

---

## Per-Task Verification Map

> The planner fills task IDs ({phase}-{plan}-{task}) once PLAN.md frontmatter is written. Requirement → test rows below are pre-locked from RESEARCH.md § Validation Architecture.

| REQ | Behavior under test | Test Type | Automated Command | File State |
|-----|---------------------|-----------|-------------------|-----------|
| DISP-01 | ModeSelector emits correct (mode, picked_id, name) tuple per radio choice | unit | `pytest tests/client/test_connect_dialog_mode.py -x` | ❌ Wave 0 |
| DISP-01 | Bookmark stores last-used mode and reloads | unit | `pytest tests/client/test_bookmarks.py::test_monitor_mode_persistence -x` | ⚠ extend |
| DISP-01 | Mode badge renders correct text per active session | unit | `pytest tests/client/test_fullscreen_toolbar_mode_badge.py -x` | ❌ Wave 0 |
| DISP-02 | Mocked mss hot-plug fires `MonitorListMsg` broadcast | unit | `pytest tests/server/test_screen_capture_hotplug.py -x` | ❌ Wave 0 |
| DISP-02 | In-process loopback: monitor-gone mid-session → client receives MonitorListMsg, FSM stays streaming | integration | `pytest tests/integration/test_monitor_hotplug.py -x` | ❌ Wave 0 |
| DISP-03 | `_widget_to_remote` produces correct server physical pixels for fixture mixed-DPI topology | unit | `pytest tests/common/test_cursor_math.py -x` | ❌ Wave 0 |
| DISP-04 | CustomEDID profile name advertises Flame-approved monitor model | unit | `pytest tests/server/test_session_manager_edid.py::test_flame_profile -x` | ❌ Wave 0 |
| DISP-05 | `screenChanged` signal triggers cache recompute + scaling update | unit | `pytest tests/client/test_viewer_screen_changed.py -x` | ❌ Wave 0 |
| DISP-05 | Per-screen DPR lookup uses widget's current screen, not primary | unit | `pytest tests/client/test_viewer_dpr.py -x` | ❌ Wave 0 |
| DISP-06 | Mocked SCK display change triggers detect_hotplug + push callback path | unit | `pytest tests/server/test_mac_screen_capture_hotplug.py -x` | ❌ Wave 0 |
| DISP-07 | Pick-one mode + bookmarked monitor exists → server crops to that monitor | integration | `pytest tests/integration/test_capture_mode.py -x` | ❌ Wave 0 |
| DISP-07 | Pick-one mode + bookmarked monitor missing → server falls back to primary + emits toast | integration | `pytest tests/integration/test_capture_mode.py::test_pick_fallback -x` | ❌ Wave 0 |
| CLIP-01 | Bidirectional text >1MB round-trip preserves CRLF | integration | `pytest tests/integration/test_clipboard_text_large.py -x` | ❌ Wave 0 |
| CLIP-01 | LF / CRLF / CR encodings preserved verbatim each direction | unit | `pytest tests/server/test_clipboard.py::test_newline_preservation -x` | ⚠ extend |
| CLIP-02 | Bidirectional PNG image round-trip preserves bytes (sha256 equality) | integration | `pytest tests/integration/test_clipboard_image.py -x` | ❌ Wave 0 |
| CLIP-02 | PNG magic-byte validation rejects malformed payloads | unit | `pytest tests/server/test_clipboard_image.py::test_magic_byte -x` | ❌ Wave 0 |
| CLIP-02 | 64MB cap enforcement on send + receive sides | unit | `pytest tests/server/test_clipboard_image.py::test_size_cap -x` | ❌ Wave 0 |
| CLIP-02 | Chunked transport: in-order, out-of-order, duplicate, missing assembly | unit | `pytest tests/common/test_clipboard_chunking.py -x` | ❌ Wave 0 |
| CLIP-03 | Per-direction toggle gates dispatch (4 directions × on/off matrix) | unit | `pytest tests/server/test_clipboard_toggles.py -x` | ❌ Wave 0 |
| CLIP-03 | Toolbar 4-checkbox menu emits correct toggle state changes | unit | `pytest tests/client/test_clipboard_toggle_menu.py -x` | ❌ Wave 0 |
| D-18 latency | P1 1-hour synthetic smoke with crop + chunk paths active stays p99 < 25 ms | smoke (nightly) | `pytest -m smoke_1h tests/smoke/test_synthetic_1h.py` | ⚠ verify with crop active |
| D-12 hot-plug | Hot-plug mid-stream + auto-fallback → client UX path verified | integration | `pytest tests/integration/test_monitor_hotplug.py::test_pick_vanish_fallback -x` | ❌ Wave 0 |

*Status legend: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky / needs extension*

---

## Wave 0 Requirements

The 19 test files marked `❌ Wave 0` above must be created (skeleton + fixtures) before Wave 1 implementation begins. This is a hard gate — no implementation task may merge until its test stub exists.

- [ ] `tests/client/test_connect_dialog_mode.py` — DISP-01 mode selector
- [ ] `tests/client/test_fullscreen_toolbar_mode_badge.py` — DISP-01 mode badge
- [ ] `tests/server/test_screen_capture_hotplug.py` — DISP-02 server hot-plug
- [ ] `tests/integration/test_monitor_hotplug.py` — DISP-02/D-12 end-to-end hot-plug + fallback
- [ ] `tests/common/test_cursor_math.py` — DISP-03 `_widget_to_remote` rewrite
- [ ] `tests/server/test_session_manager_edid.py` — DISP-04 CustomEDID profile
- [ ] `tests/client/test_viewer_screen_changed.py` — DISP-05 `screenChanged` signal handler
- [ ] `tests/client/test_viewer_dpr.py` — DISP-05 per-screen DPR
- [ ] `tests/server/test_mac_screen_capture_hotplug.py` — DISP-06 SCK hot-plug
- [ ] `tests/integration/test_capture_mode.py` — DISP-07 capture mode (pick-one happy + fallback)
- [ ] `tests/integration/test_clipboard_text_large.py` — CLIP-01 large text round-trip
- [ ] `tests/integration/test_clipboard_image.py` — CLIP-02 PNG round-trip
- [ ] `tests/server/test_clipboard_image.py` — CLIP-02 magic-byte + cap
- [ ] `tests/common/test_clipboard_chunking.py` — CLIP-02 chunk assembly
- [ ] `tests/server/test_clipboard_toggles.py` — CLIP-03 dispatch matrix
- [ ] `tests/client/test_clipboard_toggle_menu.py` — CLIP-03 menu emission
- [ ] `tests/server/test_clipboard.py::test_newline_preservation` — CLIP-01 newline (extend existing)
- [ ] `tests/client/test_bookmarks.py::test_monitor_mode_persistence` — DISP-01 bookmark (extend existing)
- [ ] `tests/conftest.py` — fixture extensions (mock NSScreen, fake mss monitor list, fixture PNGs)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| 4-corner cursor accuracy on real DXS hardware | DISP-03 / D-08 | Cursor math depends on physical pixel topology that cannot be faithfully mocked (Retina MBP + external non-Retina monitor + 2× NVIDIA Xorg) | Spike: open viewer fullscreen, click each of 4 corners on remote desktop, confirm server cursor lands within 1 px of corner. Repeat at all 3 monitor mode settings. |
| HEVC Main10 fidelity preserved with crop active | DISP-07 / VIDEO-01 | Visual color verification against ten_bit_smoke fixture must be eyeballed on a 10-bit Eizo CG | Run ten_bit_smoke fixture in pick-one mode + crop active. Compare against reference frame on Eizo CG279X. No banding, no posterization. |
| Hot-plug auto-fallback UX feel | DISP-02 / D-12 | Banner + toast timing/UX subjective | Connect Mac client to remote, start session in pick-one mode, physically unplug bookmarked monitor on host. Observe banner + toast within 250 ms. |
| Clipboard interop with Flame in-app paste | CLIP-01/CLIP-02 | Flame's clipboard handling has known quirks (TIFF preference, large-text drops) | Copy 4K PNG from Mac client, paste into Flame on host. Copy 5 MB project notes from Flame, paste into Mac client TextEdit. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (19 new test files + 2 extensions listed above)
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s quick / 3min full
- [ ] D-18 latency CI gate (p99 < 25 ms) passes with crop + chunk paths active
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
