---
phase: 03-display-multi-monitor-clipboard
plan: 01
subsystem: wire-protocol + test-infrastructure
tags: [phase-03, tdd-scaffolding, wire-protocol, test-infrastructure, backward-compat]

# Dependency graph
requires:
  - phase: 02-input-color-fidelity
    provides: "Dataclass-promotion + backward-compat pattern (Phase 2 D-14 KeyEventMsg extension), add-new-message pattern (Phase 2 D-11 KeyResetModifiersMsg), per-bookmark + in-session override pattern (Phase 2 D-10 swap_cmd_ctrl), parse_message contract (thin json.loads + unknown-type passthrough), mock-at-OS-boundary test discipline"
  - phase: 01-stability-ci-test-baseline
    provides: "pytest + structlog baseline, in-process loopback integration pattern, bounded-queue STAB-07 discipline, 4 CI gates, ConnectionProfile.from_dict filter-unknown-keys pattern"
provides:
  - "Phase 3 wire-shape contract: MsgType.SESSION_CONFIGURE + MsgType.CLIPBOARD_CHUNK constants, server_x/server_y on 5 input message dataclasses, ClipboardChunkMsg dataclass, extended ClientHelloMsg (capture_mode + picked_monitor_id + picked_monitor_name), extended ConnectionProfile (7 Phase 3 fields)"
  - "Three shared Phase 3 pytest fixtures in tests/conftest.py: mock_nsscreen, fake_mss_monitor_list, fixture_png"
  - "19 RED test skeleton files with informative pytest.skip stubs referencing the implementing plan + D-XX / REQ-XX"
  - "2 extensions to existing test files (test_bookmarks monitor_mode_persistence, test_clipboard newline_preservation)"
affects: [03-02-bookmark-migration-mode-picker, 03-03-server-capture-mode-hotplug, 03-04-client-cursor-math-dpr, 03-05-edid-remap-banner, 03-06-clipboard-image-chunked, 03-07-client-clipboard-ui]

# Tech tracking
tech-stack:
  added:
    - "MouseMoveMsg / MouseButtonMsg / MouseScrollMsg dataclass shapes (previously raw dicts in client/session.py)"
    - "ClipboardChunkMsg dataclass (D-17 chunk envelope)"
  patterns:
    - "Sentinel -1 for 'client did not compute physical px' on server_x/server_y (backward-compat extension pattern)"
    - "New dataclass fields added at END of dataclass (preserves Phase 2 D-10 migration block compat)"
    - "Default-sentinel wire compat: every new field defaults to a value that makes pre-Phase-3 peers behave identically"
    - "Wave 0 pytest.skip skeleton pattern: one stub per VALIDATION.md row, pytest.importorskip on new modules, explicit 'Wave 0 skeleton — implementation in Plan NN' reason"
    - "Threat-mitigation documentation in dataclass docstrings (T-03-01/-02/-03 referenced inline)"

key-files:
  created:
    - ".planning/phases/03-display-multi-monitor-clipboard/03-01-SUMMARY.md (this file)"
    - "tests/common/test_cursor_math.py (35 lines — 2 RED stubs for D-05/D-06)"
    - "tests/common/test_clipboard_chunking.py (55 lines — 5 RED stubs for D-17)"
    - "tests/server/test_screen_capture_hotplug.py (30 lines — 2 RED stubs for D-09/D-12)"
    - "tests/server/test_mac_screen_capture_hotplug.py (30 lines — 2 RED stubs for D-11)"
    - "tests/server/test_clipboard_image.py (43 lines — 4 RED stubs for D-14/D-16)"
    - "tests/server/test_mac_clipboard_image.py (31 lines — 3 RED stubs for D-13/D-16)"
    - "tests/server/test_clipboard_toggles.py (30 lines — 3 RED stubs for D-15)"
    - "tests/server/test_session_manager_edid.py (24 lines — 1 RED stub for DISP-04)"
    - "tests/server/test_capture_crop.py (39 lines — 3 RED stubs for D-02, 1 marker=ten_bit_smoke)"
    - "tests/server/test_clipboard.py (24 lines — 1 RED stub for CLIP-01/D-16; Rule 3 auto-created, did not previously exist)"
    - "tests/client/test_connect_dialog_mode.py (34 lines — 3 RED stubs for D-01/D-04)"
    - "tests/client/test_fullscreen_toolbar_mode_badge.py (30 lines — 3 RED stubs for D-01/D-03/D-09)"
    - "tests/client/test_viewer_dpr.py (25 lines — 2 RED stubs for D-06)"
    - "tests/client/test_viewer_screen_changed.py (30 lines — 3 RED stubs for D-06 + D-19)"
    - "tests/client/test_clipboard_toggle_menu.py (30 lines — 3 RED stubs for D-15 / C-07)"
    - "tests/integration/test_monitor_hotplug.py (21 lines — 2 RED stubs for D-09/D-12)"
    - "tests/integration/test_capture_mode.py (21 lines — 2 RED stubs for D-02/D-09)"
    - "tests/integration/test_clipboard_text_large.py (24 lines — 2 RED stubs for CLIP-01/D-16)"
    - "tests/integration/test_clipboard_image.py (25 lines — 3 RED stubs for CLIP-02/D-13/D-17)"
  modified:
    - "common/messages.py (+149 lines — Phase 3 wire-shape extensions)"
    - "tests/common/test_messages_phase2.py (+204 lines — 6 new round-trip tests + module docstring update)"
    - "tests/conftest.py (+62 lines — 3 Phase 3 fixtures)"
    - "tests/client/test_bookmarks.py (+17 lines — test_monitor_mode_persistence skeleton)"

key-decisions:
  - "Rule 3 auto-fix: tests/server/test_clipboard.py was listed as 'existing' in the plan but did not exist in the repo — Phase 2 shipped clipboard code without unit tests. Created as a new file containing only the Wave 0 skeleton test the plan specified. Plan 06 will flesh out the real CLIP-01/D-16 tests."
  - "MouseMoveMsg / MouseButtonMsg / MouseScrollMsg promoted from raw dicts (client/session.py sends them as dicts today) to dataclasses so the D-05 server_x/server_y integer fields can attach via the standard asdict idiom. Mirrors Phase 2 D-14 which did the same for KEY_EVENT."
  - "All 5 input message dataclasses got server_x/server_y at the END of their field list (not in the middle) so JSON field-ordering stays stable — pre-Phase-3 message parsers keyed by position would not break (none exist today, but the discipline matches PATTERNS.md 'Do not deviate')."
  - "ConnectionProfile Phase 3 fields land AFTER the Phase 2 D-10 swap_cmd_ctrl field — the Phase 2 D-10 bookmark migration block in client/bookmarks.py._load keys off field existence, so appending is safe. Whitelist enforcement on monitor_mode values deferred to Plan 03-02's migration block (T-03-01 mitigation staged)."
  - "Skeletons use pytest.skip + pytest.importorskip (not pytest.xfail) — matches the Phase 1 + Phase 2 scaffolding discipline so CI stays GREEN at Wave 0 commit; plans 02-06 remove the skip when implementation lands."
  - "MonitorInfo NOT extended with physical_width/dpi — Plan 04 will decide whether the D-05 cursor math can work on the existing width/height/scale fields. Adding speculative fields now would be a wire-shape churn with no consumer."

patterns-established:
  - "Phase 3 Wave 0 scaffolding: 19 skeleton files + 2 existing-file extensions + 3 shared fixtures + 1 wire-shape contract file, landed in 2 atomic commits. Plans 02-06 have a target test contract to write code against (TDD discipline preserved)."

requirements-completed: []
# Requirements covered by the plan (DISP-01..07, CLIP-01..03) are only
# partially ready for verification: the wire-shape contract ships here
# but the behavior each REQ-ID describes lives in Plans 02-06. No
# completed requirements until implementation plans land.

# Metrics
duration: ~25m
completed: 2026-04-20
---

# Phase 3 Plan 01: Wire Shapes + Test Scaffolding Summary

**Phase 3 wire-shape contract (D-02 capture_mode, D-05 server_x/server_y, D-13/D-17 clipboard chunking, ConnectionProfile bookmark fields) locked in common/messages.py with full backward-compat defaults, plus 19 RED test skeletons covering every DISP / CLIP requirement so Plans 02-06 have a test contract to TDD against.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-04-20T07:20Z
- **Completed:** 2026-04-20T07:45Z
- **Tasks:** 2 / 2
- **Files created:** 20 (19 test scaffolds + this SUMMARY)
- **Files modified:** 4 (common/messages.py, tests/common/test_messages_phase2.py, tests/conftest.py, tests/client/test_bookmarks.py)
- **Commits:** 2 atomic task commits
  - `a24ea35` feat(03-01): extend common/messages.py with Phase 3 wire shapes
  - `40cc866` test(03-01): add Phase 3 RED test skeletons + conftest fixtures
- **Total diff:** +1,019 lines / -1 line

## Accomplishments

### Task 1 — Wire-shape extensions (D-02, D-05, D-13, D-17, ConnectionProfile)

- **Two new MsgType constants** registered under `# Phase 3 additions (D-02, D-17):` — `SESSION_CONFIGURE = "session_configure"`, `CLIPBOARD_CHUNK = "clipboard_chunk"`. Downstream Plans 02-06 import these by name.
- **Three input messages promoted from raw dicts to dataclasses** — `MouseMoveMsg`, `MouseButtonMsg`, `MouseScrollMsg`. Previously sent via `client/session.py` as literal dicts; now each is a `@dataclass` with `to_json()` returning `json.dumps(asdict(self))`. Mirrors the Phase 2 D-14 `KeyEventMsg` promotion pattern.
- **D-05 `server_x` / `server_y` integer fields added to all 5 input message dataclasses** — `MouseMoveMsg`, `MouseButtonMsg`, `MouseScrollMsg`, `KeyEventMsg`, `PenEventMsg`. Default `-1` is the "client did not compute physical px" sentinel. Server prefers integer fields when `server_x >= 0`; falls back to the normalized floats otherwise (pre-Phase-3 wire compat).
- **New `ClipboardChunkMsg` dataclass (D-17)** — mirrors `KeyResetModifiersMsg` shape: `type + sequence_id + chunk_index + total_chunks + content_type + data`. Base64-encoded payload chunk per chunk. Threat T-03-03 bound (`total_chunks <= 256`) documented in the docstring; enforcement deferred to Plan 06 assembler.
- **`ClientHelloMsg` extended (D-02)** — three new fields with safe defaults: `capture_mode: str = "mirror_all"`, `picked_monitor_id: int = -1`, `picked_monitor_name: str = ""`. Defaults make pre-Phase-3 clients implicitly request mirror_all, which preserves the current always-full-virtual-desktop server behavior. Threat T-03-02 (server whitelist enforcement) deferred to Plan 03.
- **`ConnectionProfile` extended (D-01, D-04, D-15)** — 7 new fields appended after the Phase 2 D-10 `swap_cmd_ctrl` field: `monitor_mode`, `picked_monitor_id`, `picked_monitor_name`, `clipboard_text_c2s`, `clipboard_text_s2c`, `clipboard_image_c2s`, `clipboard_image_s2c`. All clipboard toggles default to `True` (D-16 "security defaults = all directions ON"). Pre-Phase-3 bookmark JSON files omit these fields; `from_dict`'s `__dataclass_fields__` filter pattern handles missing fields safely (threat T-03-01 mitigation).
- **Six new round-trip tests in `tests/common/test_messages_phase2.py`** — locks the wire contract for every new field group: MsgType constants, input-msg server_x/server_y, ClipboardChunkMsg, ClientHelloMsg capture_mode, ConnectionProfile Phase 3 defaults, ConnectionProfile unknown-key filtering. Phase 1 + Phase 2 suite stays green: **33/33 passed**.

### Task 2 — Test skeletons + conftest fixtures

- **Three new Phase 3 fixtures in `tests/conftest.py`:**
  - `mock_nsscreen` — two-screen DPR layout (Retina 2.0 + external 4K 1.0) matching the D-08 mixed-DPI spike topology
  - `fake_mss_monitor_list` — DXS flame-lab 2×2560×1600 desktop (5120×1600 virtual + 2 physical monitors per mss convention)
  - `fixture_png` — deterministic 1×1 black PNG (70 bytes, magic-byte-valid, threat T-03-04 disposition: accept — no PII risk)
- **Two existing test files extended:**
  - `tests/client/test_bookmarks.py` — new `test_monitor_mode_persistence` skeleton (DISP-01)
  - `tests/server/test_clipboard.py` — new file (Rule 3 fix; did not exist) with `test_newline_preservation` skeleton (CLIP-01 / D-16)
- **17 new RED skeleton files** covering the full DISP/CLIP test matrix with at least one `pytest.skip` stub each:
  - `tests/common/` (2): cursor_math, clipboard_chunking
  - `tests/server/` (7): screen_capture_hotplug, mac_screen_capture_hotplug, clipboard_image, mac_clipboard_image, clipboard_toggles, session_manager_edid, capture_crop
  - `tests/client/` (5): connect_dialog_mode, fullscreen_toolbar_mode_badge, viewer_dpr, viewer_screen_changed, clipboard_toggle_menu
  - `tests/integration/` (4): monitor_hotplug, capture_mode, clipboard_text_large, clipboard_image
- **Each skeleton carries an informative `pytest.skip` reason** naming the implementing plan and D-XX / REQ-XX so plans 02-06 have unambiguous TDD targets. `pytest.importorskip` is used on new modules (e.g. `client.clipboard_toggle_menu`) so the RED state includes the import-missing signal.

## Verification

- `python -m pytest tests/common/test_messages.py tests/common/test_messages_phase2.py -x` → **33 passed** (0 failures)
- `python -m pytest -m "not wacom_hw and not gpu and not smoke_1h and not latency_bench" -q` → **3798 passed, 152 skipped, 0 failed, 2 xfailed, 4 deselected** (no regressions; 51 new skeleton skips counted in the 152)
- `python -m pytest --collect-only tests/ 2>&1 | grep -c "<Function "` → **3888** collected (increased by ~52 vs Phase 2 baseline, exceeds the ≥30 planner gate)
- Python import smoke: `from common.messages import (MouseMoveMsg, MouseButtonMsg, MouseScrollMsg, KeyEventMsg, PenEventMsg, ClipboardChunkMsg, ConnectionProfile, MsgType)` plus the MsgType constant assertions — all exports importable by downstream plans.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] tests/server/test_clipboard.py listed as "existing" but did not exist**
- **Found during:** Task 2 (attempted to `Edit` the file after reading — file not found)
- **Issue:** The plan's `<files>` list and `<action>` Step C referenced `tests/server/test_clipboard.py` as an existing file to extend with `test_newline_preservation`, but Phase 2's clipboard work (`server/clipboard.py`, `server/mac_clipboard.py`) shipped without unit tests — no such file ever existed in the repo.
- **Fix:** Created `tests/server/test_clipboard.py` as a new file containing only the Wave 0 skeleton test the plan specified (single `test_newline_preservation` with `pytest.skip` pointing at Plan 06). Does not attempt to retro-build Phase 2 unit tests — that's out of scope for this plan.
- **Files modified:** tests/server/test_clipboard.py (new, 24 lines)
- **Commit:** 40cc866 (included in Task 2 commit)
- **Scope note:** `test_clipboard.py` was counted in both the plan's "files_modified" list AND in the 19 new-skeleton count because it is functionally a new skeleton with the same shape as the other Wave 0 stubs.

No other deviations. No RULE 4 architectural decisions needed.

### Out-of-Scope Discoveries (not fixed)

None.

## Known Stubs

Every RED skeleton test created in this plan is a known stub by design — Wave 0 scaffolding ships with `pytest.skip("Wave 0 skeleton — implementation in Plan NN (D-XX)")` calls so the suite stays GREEN at commit time. Plans 02-06 remove the skip when implementation lands. This is the explicit TDD contract of the plan (see the plan's `<behavior>` section: "Each test file ships RED — implementation lives in plans 02-06").

The stub count (51 tests, 18 skeleton files + 1 extended test) is tracked via `pytest --collect-only` vs skipped count so no stub escapes the expected set.

## Self-Check: PASSED

**Files verified:**
- common/messages.py — modified (MOUSE_MOVE dataclass, ClipboardChunkMsg, ClientHelloMsg capture_mode, ConnectionProfile Phase 3 fields all present via grep)
- tests/common/test_messages_phase2.py — 6 new tests pass
- tests/conftest.py — 3 fixtures (mock_nsscreen, fake_mss_monitor_list, fixture_png) present
- tests/client/test_bookmarks.py — test_monitor_mode_persistence present
- tests/server/test_clipboard.py — test_newline_preservation present
- 17 new skeleton files verified on disk (ls count == 18 including test_clipboard.py)

**Commits verified:**
- `a24ea35` present in `git log` (feat Task 1)
- `40cc866` present in `git log` (test Task 2)

**Suite verified:**
- 3798 passed / 152 skipped / 0 failed on quick suite
- 33/33 pass on wire-shape round-trip suite
