---
phase: 03-display-multi-monitor-clipboard
plan: 02
subsystem: client-ui
tags: [phase-03, client-ui, bookmark, mode-selector, capture-mode-negotiation, mode-badge]

# Dependency graph
requires:
  - phase: 03-display-multi-monitor-clipboard
    provides: "Wave 0 wire-shape contract (Plan 01): ClientHelloMsg.capture_mode + picked_monitor_id + picked_monitor_name (D-02); ConnectionProfile.monitor_mode + picked_monitor_id + picked_monitor_name + 4 clipboard toggles (D-01/D-04/D-15); filter-unknown-keys from_dict pattern for safe load"
  - phase: 02-input-color-fidelity
    provides: "Per-bookmark + in-session override pattern (D-10 Cmd↔Ctrl swap), setter-on-protocol → stored-attr → construction-site read pattern (set_swap_cmd_ctrl), atomic _save() migration discipline (WR-01), ConnectionDialog QComboBox shape + property accessors"
  - phase: 01-stability-ci-test-baseline
    provides: "pytest + mock-at-OS-boundary test discipline, in-process Session/Protocol instantiation without live wiring, QT_QPA_PLATFORM=offscreen headless Qt test pattern"
provides:
  - "Connect-dialog ModeSelector widget (UI-SPEC Surface 1 verbatim copy): 3 radios + help text + pick-one sub-selector; mode_changed(mode, id, name) signal"
  - "MonitorSelector radio mode (D-04): single-check semantics, Select All/None hidden, label shows picked monitor's name; monitor_missing(name) signal + set_picked()/selected_ids()/selected_names() API"
  - "BookmarkManager Phase 3 migration block: pre-Phase-3 JSON loads with safe defaults (mirror_all + clipboard toggles ON per D-16); atomic _save() writes new shape on next load (Pitfall 9); whitelist enum check on monitor_mode reverts invalid values to mirror_all (T-03-05 STRIDE mitigation)"
  - "ClientProtocol.set_capture_mode(mode, picked_id, picked_name) setter + ClientHelloMsg construction populates the three Phase 3 fields on every connect (D-02); whitelist enforcement rejects unknown modes (T-03-07)"
  - "Session.connect() extended with monitor_mode / picked_monitor_id / picked_monitor_name kwargs — pushes capture_mode via protocol.set_capture_mode BEFORE protocol.connect fires so the first handshake carries the user's picked mode"
  - "Session.connect_with_profile() convenience that pulls the 7 Phase 3 fields off a ConnectionProfile and delegates to connect()"
  - "FullscreenToolbar._ModeBadge QFrame (UI-SPEC Surface 2) renders Mode: single|mirror|pick:{name}|pick → primary with ACCENT/WARNING underline + GOLD locked dot; update_capture_mode(mode, picked_name, degraded) slot for session drivers"
  - "D-03 mid-session lock surface: ModeSelector.set_disabled_during_session(True) grays the widget + applies Qt.ForbiddenCursor + sets the 'Disconnect and reconnect to change monitor mode.' tooltip verbatim"

affects: [03-03-server-capture-mode-hotplug, 03-04-client-cursor-math-dpr, 03-05-edid-remap-banner, 03-06-clipboard-image-chunked, 03-07-client-clipboard-ui]

# Tech tracking
tech-stack:
  added:
    - "ModeSelector widget (new class in client/main_window.py) composing QRadioButton + QButtonGroup + existing MonitorSelector"
    - "_ModeBadge (QFrame subclass in client/fullscreen_toolbar.py) with paintEvent-driven 2px underline"
  patterns:
    - "UI-SPEC-driven verbatim copy contract: all user-visible strings grep-verifiable against 03-UI-SPEC.md Surface 1/2/3/4 (no paraphrasing at the code layer)"
    - "Phase 3 extends Phase 2 D-10 migration-block shape — add one migrated=True trigger per new-field-group at load time, let atomic _save() rewrite on next load"
    - "QSignalBlocker in MonitorSelector radio mode: when toggling one action ON, iterate siblings and block-signal the uncheck so _on_toggled doesn't re-enter"
    - "Intrinsic visibility assertion (isHidden() not isVisible()) in headless Qt tests — parent-chain visibility is irrelevant when asserting widget state on an un-shown toolbar"
    - "Session-layer connect_with_profile() shim: 7-field ConnectionProfile flows through a thin adapter into the existing connect(host,port,...) contract, keeping Phase 2 call sites untouched"

key-files:
  created: []
  modified:
    - "client/bookmarks.py — Phase 3 migration block + T-03-05 whitelist enum check (+36 lines)"
    - "client/main_window.py — new ModeSelector class + ConnectionDialog integration + 3 accessors (+211 lines)"
    - "client/monitor_selector.py — radio mode + monitor_missing signal + set_picked/selected_ids/selected_names + menu title switch (+123 / -33 lines net rewrite)"
    - "client/protocol.py — set_capture_mode setter + ClientHelloMsg construction site passes Phase 3 fields (+31 lines)"
    - "client/session.py — connect() extended with 3 kwargs + protocol.set_capture_mode pre-handshake call + connect_with_profile convenience + capture_mode/picked_monitor_name props (+59 lines)"
    - "client/fullscreen_toolbar.py — _ModeBadge class + _build_ui insertion + update_capture_mode + mode_badge property (+120 lines)"
    - "tests/client/test_bookmarks.py — 3 new migration + whitelist tests (+137 lines)"
    - "tests/client/test_connect_dialog_mode.py — 8 GREEN tests replacing 3 RED skeletons (+197 / -33 lines)"
    - "tests/client/test_fullscreen_toolbar_mode_badge.py — 7 GREEN tests replacing 3 RED skeletons (+172 / -30 lines)"

key-decisions:
  - "Intrinsic visibility (isHidden()==False) rather than isVisible()==True in headless-Qt badge tests. The toolbar itself is never shown in unit tests, so Qt's effective-visibility semantics would false-negative on visible children. Tests assert the widget's intrinsic 'not explicitly hidden' flag which is the actual observable driven by update_capture_mode(mode='') vs update_capture_mode(mode='single')."
  - "Session.connect_with_profile() added as a new method rather than overloading connect(). Keeps the Phase 2 signature stable for the existing BookmarkPanel._add_bookmark / _edit_bookmark call sites while offering a one-line ConnectionProfile→session path that Plan 03 server-side and future UI paths can use."
  - "MonitorSelector.set_mode preserves the Select All/Select None QActions (setVisible(False) only) so mode toggling is reversible — a session that switches from pick_one back to mirror_all restores the multi-select quick actions without re-building the menu."
  - "Badge update method renamed update_state() internally (not update()) to avoid shadowing QWidget.update() — we still call super().update() at the end of update_state() to trigger the paintEvent for the underline repaint."
  - "bookmark migration triggers _save() even when all Phase 3 fields are present BUT the monitor_mode is out-of-whitelist. Ensures any tampered JSON loaded with from_dict gets sanitized and persisted in the next write, closing a race where the next add()/update() wouldn't rewrite the field."

patterns-established:
  - "Phase 3 Task 1 GREEN gate: RED test → migration block + ModeSelector + monitor_selector radio-mode in a single atomic commit per plan; pytest for client+common 3620/0/111 baseline preserved."
  - "Phase 3 Task 2 GREEN gate: RED test → protocol setter + session push + toolbar badge in a single atomic commit; full quick-suite regression 3816 passed / 0 failed / 145 skipped."
  - "UI-SPEC copy verbatim-grep pattern: acceptance criteria grep for ``\"Single monitor\"``, ``\"Show one server monitor at a time. Lowest bandwidth.\"``, ``\"Mode: pick: {name}\"``, ``\"Monitor mode is fixed for this session. Disconnect and reconnect to change.\"`` — if the exact strings don't land in source, the plan fails its own acceptance. Avoids paraphrase drift between UI-SPEC and implementation."

requirements-completed: [DISP-01, DISP-07]

# Metrics
duration: 10min
completed: 2026-04-20
---

# Phase 3 Plan 02: Client Mode UX Summary

**Per-session monitor-mode picker (single / mirror-all / pick-one) lives in the connect dialog with a radio-group sub-selector for pick-one, is stored per-bookmark across reloads via a Phase 3 migration block, flows onto the wire through ClientHelloMsg.capture_mode + picked_monitor_id + picked_monitor_name before the handshake, and surfaces in the fullscreen toolbar as a read-only mode badge with UI-SPEC Surface 2 verbatim copy — ready for Plan 03 server-side crop.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-04-20T12:52:25Z
- **Completed:** 2026-04-20T13:01:50Z
- **Tasks:** 2 / 2
- **Files created:** 1 (this SUMMARY)
- **Files modified:** 9 (3 tests + 6 client source files)
- **Commits:** 2 atomic task commits
  - `8167847` feat(03-02): bookmark migration + ModeSelector widget + MonitorSelector radio mode
  - `e354468` feat(03-02): push capture_mode on ClientHelloMsg + toolbar mode badge

## Accomplishments

### Task 1 — Bookmark migration + ModeSelector + MonitorSelector radio mode

- **Phase 3 D-01/D-15 migration block in client/bookmarks.py::_load.** Any pre-Phase-3 bookmark (missing monitor_mode / picked_monitor_id / picked_monitor_name / 4 clipboard toggles) flips migrated=True so the existing atomic _save() rewrites the JSON with the full Phase 3 shape. Dataclass defaults give the safe values per D-16 ("all directions on") + mirror_all. Closes Pitfall 9 ("user enables a toggle but it never persists because the field was never in the JSON").
- **T-03-05 STRIDE mitigation inline.** Same `_load` block whitelist-checks monitor_mode against `single`/`mirror_all`/`pick_one`. Hand-edited JSON carrying `monitor_mode: "rce"` (or anything else) logs a warning, reverts to mirror_all, and triggers the same _save() path so the sanitized value persists. Test asserts the file on disk is rewritten.
- **ModeSelector widget** (new class in client/main_window.py). QWidget composing a QButtonGroup (exclusive) of 3 QRadioButtons ("Single monitor" / "Mirror all" / "Pick one"), three help labels with verbatim UI-SPEC Surface 1 copy, and a pick-one sub-selector — the existing MonitorSelector in `radio` mode — that only reveals when the Pick one radio is checked. Emits `mode_changed(mode, picked_monitor_id, picked_monitor_name)` on every radio toggle AND sub-selector pick. Exposes monitor_mode / picked_monitor_id / picked_monitor_name properties + set_mode() pre-fill + update_monitors() passthrough + set_disabled_during_session() D-03 lock helper.
- **ConnectionDialog integration.** mode_selector is instantiated once per dialog and inserted below the swap_cmd_ctrl_check row. Three new `@property` accessors on ConnectionDialog (monitor_mode / picked_monitor_id / picked_monitor_name) delegate to the sub-widget, matching the Phase 2 destination_kind accessor shape.
- **MonitorSelector gains a `mode` attribute** (default `checkbox`, flip to `radio` for pick-one). In radio mode: (a) Select All / Select None actions are setVisible(False) — reversibly, so mode switches stay clean; (b) each `toggled` ON action blocks signals + unchecks every sibling via QSignalBlocker, enforcing single-check; (c) the label shows the picked monitor's name (not "All (N)" or "N of M"); (d) menu title switches to "Pick one monitor" (UI-SPEC Surface 4). New `monitor_missing(str)` signal + `set_picked(id, name)` method fire when a bookmarked monitor isn't in the current list, falling back to the primary monitor + emitting the toast signal for Plan 03 surface wiring. New `selected_ids()` / `selected_names()` accessors round out the pick-one read path.
- **16 bookmarks tests + 8 ModeSelector tests GREEN** (including 3 new migration/whitelist coverage). Client+common baseline 3620 passed / 0 failed / 111 skipped — no regressions.

### Task 2 — ClientHelloMsg push + mode badge + session wiring

- **client/protocol.py::set_capture_mode(mode, picked_id, picked_name).** Mirrors the Phase 2 D-10 set_swap_cmd_ctrl shape. Three new attrs (_capture_mode / _picked_monitor_id / _picked_monitor_name) initialized to mirror_all / -1 / "" in `__init__` preserve pre-Phase-3 behavior. Whitelist-checks mode — unknown values log a structlog warning and fall back to mirror_all (T-03-07 client-side defense-in-depth).
- **ClientHelloMsg construction site upgraded.** The inline `ClientHelloMsg()` call after the server_hello handshake now passes `capture_mode=self._capture_mode, picked_monitor_id=..., picked_monitor_name=...` — the single place the three fields go on the wire. Every reconnect picks up the latest set_capture_mode state.
- **client/session.py::connect() extended** with 3 new kwargs (monitor_mode / picked_monitor_id / picked_monitor_name). A new call to `protocol.set_capture_mode(...)` lands BEFORE `protocol.connect(...)` so the very first websocket handshake carries the user's picked mode — test asserts the ordering via monkeypatch of both methods. Session also tracks the three values as `_monitor_mode` / `_picked_monitor_id` / `_picked_monitor_name` so future events (reconnect, hot-plug) can refresh the toolbar badge without a fresh profile lookup.
- **Session.connect_with_profile(profile, password)** convenience. One-line `session.connect_with_profile(profile, password=pw)` replaces 10 lines of `session.connect(host=profile.host, port=profile.port, ..., monitor_mode=profile.monitor_mode, ...)`. Consumers (MainWindow, future bookmark UI) stay tight.
- **FullscreenToolbar._ModeBadge** (new QFrame subclass in client/fullscreen_toolbar.py). Renders one of four state texts verbatim per UI-SPEC Surface 2:
  - `Mode: single`, `Mode: mirror`, `Mode: pick: {name}`, `Mode: pick → primary` (degraded fallback)
  - Tooltip `Monitor mode is fixed for this session. Disconnect and reconnect to change.` (active) or `{monitor_name} disappeared. Showing primary monitor instead.` (degraded)
  - Paints a 2px underline beneath the label in `theme.ACCENT` (active) / `theme.WARNING` (degraded)
  - GOLD-colored "locked" dot indicates informational/locked state (distinct from selectable accent)
  - Badge is `setVisible(False)` until update_state(mode) receives a non-empty mode; clearing via `update_state("")` re-hides it (used on disconnect)
- **FullscreenToolbar.update_capture_mode(mode, picked_name, degraded)** is the session-facing slot; delegates to `_ModeBadge.update_state`. Badge inserted between MonitorSelector and the stretch, with a `# Plan 06 inserts ClipboardToggleButton here` comment at the reserved insertion point for UI-SPEC Surface 7.
- **7 toolbar badge tests GREEN** (text per state, tooltip verbatim, degraded underline, hide-on-clear, protocol plumbing, session ordering).

## Task Commits

Each task was committed atomically:

1. **Task 1: Bookmark migration + ModeSelector + MonitorSelector radio mode** — `8167847` (feat)
2. **Task 2: ClientHelloMsg push + toolbar mode badge** — `e354468` (feat)

**Plan metadata:** pending final commit with SUMMARY.md + STATE.md + ROADMAP.md.

## Files Created/Modified

- `client/bookmarks.py` — Phase 3 migration block + T-03-05 whitelist check (+36 lines)
- `client/main_window.py` — new ModeSelector class + ConnectionDialog mode accessors (+211 lines)
- `client/monitor_selector.py` — radio mode + monitor_missing signal + set_picked/selected_ids/selected_names (net rewrite, +123 / -33 lines)
- `client/protocol.py` — set_capture_mode + ClientHelloMsg construction (+31 lines)
- `client/session.py` — capture_mode push + connect_with_profile (+59 lines)
- `client/fullscreen_toolbar.py` — _ModeBadge + update_capture_mode + mode_badge property (+120 lines)
- `tests/client/test_bookmarks.py` — 3 new tests (migration + whitelist) (+137 lines)
- `tests/client/test_connect_dialog_mode.py` — 8 tests (Wave 0 RED skeletons → GREEN) (+197 / -33 lines)
- `tests/client/test_fullscreen_toolbar_mode_badge.py` — 7 tests (Wave 0 RED → GREEN) (+172 / -30 lines)
- `.planning/phases/03-display-multi-monitor-clipboard/03-02-SUMMARY.md` — this file (new)

## Decisions Made

- **isHidden() over isVisible() in headless Qt tests.** Qt's `isVisible()` returns False for children of non-shown parents, so asserting the badge's intrinsic "not-explicitly-hidden" state is the right semantic in `QT_QPA_PLATFORM=offscreen` test runs where the FullscreenToolbar itself never `.show()`s. Documented inline in the two tests.
- **New `connect_with_profile()` convenience method on Session** (additive, not replacing `connect()`) so Phase 2 callers stay unchanged while Plan 03 server-side and future UI paths get a one-liner ConnectionProfile→session bridge.
- **Whitelist check fires inside the migration block** (not just on new bookmarks) so hand-edited JSON sanitization always rewrites the file — the fix persists on next load rather than waiting for the next add()/update().
- **MonitorSelector.set_mode() hides (not removes) Select All/None.** Mode switches are rare but reversible; `setVisible(False)` preserves the signal connections + QMenu order.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Qt visibility semantics in headless badge tests**
- **Found during:** Task 2 (first test run after landing the badge)
- **Issue:** Original test asserted `tb.mode_badge.isVisible() is True` after `update_capture_mode("single")`. Qt's `isVisible()` is effective visibility — it returns False for any widget whose parent-chain isn't shown. In the test the FullscreenToolbar itself is never `.show()`'d, so `isVisible()` falsely returns False for the child badge even though the badge's own hidden flag is cleared.
- **Fix:** Switched to `isHidden()` (intrinsic "explicitly hidden" flag, unaffected by parent state). This is the correct assertion for headless unit tests — the real behavior we care about is whether `update_capture_mode` sets/clears the widget's hidden flag.
- **Files modified:** tests/client/test_fullscreen_toolbar_mode_badge.py (2 assertion lines)
- **Verification:** 7/7 tests in that file GREEN; full quick suite 3816 passed / 0 failed.
- **Commit:** e354468 (Task 2 commit)

No architectural changes required. No Rule 4 decisions.

---

**Total deviations:** 1 auto-fixed (test semantic correction; no source changes)
**Impact on plan:** Minimal — the production code's badge-visibility behavior is correct (badge visually appears when parent is shown); the fix was to make the test assertion match the actual semantic we care about in headless.

## Issues Encountered

- **.venv was the wrong Python for tests initially.** System python3 (3.14 Homebrew) has no PySide6 installed, which made Qt-dependent tests silently skip via `pytest.importorskip("PySide6.QtWidgets")`. Solved by using `.venv/bin/python -m pytest` — the project's configured virtualenv has PySide6. Recorded for the next executor.

## Threat Flags

None — this plan only touches the three trust boundaries already enumerated in the plan's `<threat_model>`:
- `disk → client` (bookmark load): T-03-05 + T-03-06 mitigations landed via migration block.
- `client UI → protocol` (ModeSelector → set_capture_mode → ClientHelloMsg): T-03-07 client-side whitelist landed in `protocol.set_capture_mode`.
- `mode badge display` (T-03-08): no surface changes — monitor names on the badge are user-chosen labels, not sensitive.

## Next Phase Readiness

- Plan 03 (server-side capture-mode crop) has a complete wire-level contract to consume: ClientHelloMsg carries capture_mode + picked_monitor_id + picked_monitor_name on every connect.
- Plan 04 (client cursor math + DPR) can attach its D-05 / D-06 math to the MonitorSelector in radio mode — the widget already exposes selected_ids() / selected_names() / monitor_missing as the signal surface it needs.
- Plan 05 (remap banner) can wire into the monitor_missing signal for its D-09 fallback toast.
- Plan 07 (client clipboard UI) has the reserved toolbar insertion point marked with a comment at `# Plan 06 inserts ClipboardToggleButton here`.

## Self-Check: PASSED

**Files verified:**
- client/bookmarks.py — `grep phase3_fields` → 2 matches; `grep 'monitor_mode not in'` → 1 match
- client/main_window.py — `grep 'class ModeSelector'` → 1 match; `grep 'QRadioButton("Single monitor")'` → 1 match; `grep 'Show one server monitor at a time'` → 1 match; `grep 'Disconnect and reconnect to change monitor mode'` → 2 matches
- client/monitor_selector.py — `grep 'monitor_missing = Signal'` → 1 match; `grep -cE 'def set_mode|def set_picked|def selected_ids|def selected_names'` → 4 matches; `grep 'Pick one monitor'` → 2 matches
- client/protocol.py — `grep 'def set_capture_mode'` → 1 match; `grep 'capture_mode=self\._capture_mode'` → 1 match
- client/session.py — `grep 'set_capture_mode'` → 1 match
- client/fullscreen_toolbar.py — `grep 'class _ModeBadge'` → 1 match; `grep -cE 'Mode: single|Mode: mirror|Mode: pick'` → 8 matches (4 copy strings + 4 internal references); `grep 'Monitor mode is fixed for this session'` → 3 matches; `grep 'disappeared. Showing primary monitor instead'` → 1 match; `grep 'def update_capture_mode'` → 1 match; `grep -oE 'theme\.(ACCENT|WARNING|GOLD)' | wc -l` → 3 matches

**Commits verified:**
- `8167847` present in git log (feat Task 1)
- `e354468` present in git log (feat Task 2)

**Suite verified:**
- `pytest tests/client/test_bookmarks.py tests/client/test_connect_dialog_mode.py tests/client/test_fullscreen_toolbar_mode_badge.py` → 31 passed / 0 failed / 0 skipped
- `pytest -m "not wacom_hw and not gpu and not smoke_1h and not latency_bench" -q` → 3816 passed / 0 failed / 145 skipped (no regressions vs Plan 01 baseline 3798)

---
*Phase: 03-display-multi-monitor-clipboard*
*Completed: 2026-04-20*
