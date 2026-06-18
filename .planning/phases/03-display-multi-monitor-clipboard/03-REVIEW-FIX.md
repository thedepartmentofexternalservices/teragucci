---
phase: 03-display-multi-monitor-clipboard
fixed_at: 2026-04-20T00:00:00Z
review_path: .planning/phases/03-display-multi-monitor-clipboard/03-REVIEW.md
iteration: 1
findings_in_scope: 7
fixed: 7
skipped: 0
status: all_fixed
---

# Phase 3: Code Review Fix Report

**Fixed at:** 2026-04-20T00:00:00Z
**Source review:** `.planning/phases/03-display-multi-monitor-clipboard/03-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 7 (CR-01 + WR-01..WR-06)
- Fixed: 7
- Skipped: 0
- Baseline before fixes: 3920 passed / 0 failed / 105 skipped (quick suite, head `8fce42e`)
- Baseline after fixes: 3922 passed / 0 failed / 105 skipped (quick suite; +2 from CR-01 regression tests)

## Fixed Issues

### CR-01: Degradation routing references wrong attribute — pick_one banner never fires

**Files modified:** `client/session.py`, `tests/client/test_session_monitor_list_wiring.py`
**Commit:** `68c9314`
**Applied fix:** Renamed the two `getattr(self, "_capture_mode", "mirror_all")`
sites in `_on_monitor_list_with_degradations` (lines 611 + 649) to read
`_monitor_mode`, which is the attribute `connect()` actually sets at line
173. Updated the three existing tests that stubbed `_capture_mode` on a
SimpleNamespace so the fake-Session harness now exercises the real code
path. Also added two regression tests (commit `96e03c6`) that specifically
assert:
1. `pick_missing` banner fires via the single-session heuristic when
   `_client_token=""` + `_monitor_mode="pick_one"` + one degradation entry.
2. `single_change` banner (not `mirror_remove`) is selected when a
   `pick_one` session sees a topology-change with no degradation for us.

Both regression tests would have failed pre-fix because `getattr` with a
missing `_capture_mode` defaulted to `"mirror_all"` and silently fell
through.

### WR-01: Stale `_monitor` dict after `detect_hotplug` can crash capture

**Files modified:** `server/screen_capture.py`
**Commit:** `370a2a2`
**Applied fix:** Added `self._select_monitor(self.monitor_index)` call
immediately after the `_sct` swap + `_refresh_monitor_info()` in
`detect_hotplug`. This re-derives `self._monitor` from the new
`_sct.monitors` dict so subsequent `capture_raw_bgra()` calls use
topology-correct coordinates. `_select_monitor` already clamps
out-of-range indices back to primary (line 356-357), so a removed
monitor falls back cleanly. Hotplug tests (9 tests across
`tests/server/test_screen_capture_hotplug.py` +
`tests/integration/test_monitor_hotplug.py`) remain green.

### WR-02: `_dropped_seqs` grows unbounded over protocol/session lifetime

**Files modified:** `client/protocol.py`, `server/client_session.py`, `server/session_runtime.py`
**Commit:** `97bceab`
**Applied fix:** Introduced `_DROPPED_SEQS_MAX = 4096` module-level
constant on both sides of the wire:
- Client: `ClientProtocol._track_dropped_seq()` method pops an arbitrary
  entry when over cap; both chunk-0 drop sites in `_handle_clipboard_chunk`
  call it instead of `_dropped_seqs.add()`.
- Server: module-level `track_dropped_seq()` helper in
  `server/client_session.py` (placed at module scope so
  `server/session_runtime.py` can import at runtime without hitting the
  TYPE_CHECKING cycle). Both chunk-0 drop sites in the server
  `_handle_clipboard_chunk` call it.

Using `set.pop()` is non-deterministic (CPython set iteration is hash
order, not insertion order) — NOT strict FIFO, but the security
invariant here is "bounded size", not ordering. 4096 entries is ~32 KiB
of int storage — well below any realistic chunk-window scale.

This also shrinks the WR-04 wrap-collision probability to negligible.

### WR-03: `_enforce_max_visible` drops only one toast per call — stack can exceed cap during bursts

**Files modified:** `client/toasts.py`
**Commit:** `121e29a`
**Applied fix:** Added per-toast `_dismiss_started` flag set in `dismiss()`.
`_enforce_max_visible` now snapshots the active (not-yet-dismissing)
toasts in arrival order, computes the overflow, and dismisses that many
entries in a single pass. Iteration is bounded by the snapshot slice so
a pathological `dismiss()` implementation can't hang the GUI thread.
The prior "dismiss one then break" workaround for headless tests is no
longer needed because we skip already-dismissing toasts by the flag,
not by reliance on fade-out drain. All 6 existing `test_toasts.py`
tests still pass.

### WR-04: Clipboard sequence_id wrap can collide with stale `_dropped_seqs` entries

**Files modified:** `server/session_runtime.py` (client side doc added in WR-02 commit)
**Commit:** `fb12b3c`
**Applied fix:** Documented the dependency of the 32-bit wrap safety on
the WR-02 bounded-size cap in both `_next_clipboard_seq` docstrings
(client's updated in the WR-02 commit, server's in this one). No code
change — the effective mitigation is the bounded-size cap from WR-02.

### WR-05: `apply_capture_mode` leaves stale picked_monitor state on no-capture path

**Files modified:** `server/session_runtime.py`
**Commit:** `9083be9`
**Status:** fixed: requires human verification
**Applied fix:** Added a defensive `if mode == "mirror_all":` clear of
`session.picked_monitor_id` / `picked_monitor_name` inside the
`not self.capture` early-return branch. Note: this is DEAD CODE today
because the prior `if mode == "mirror_all":` block at line 699 returns
before the no-capture branch is reached — so the REVIEW's flagged
scenario (pick_one -> mirror_all before capture init) is already
handled. The inserted code is defense-in-depth against a future
refactor that might collapse the early return into a unified path.
The docstring comment makes this explicit.

**Human verification needed:** confirm that today's
`apply_capture_mode` flow already clears picks on `mirror_all` (it
does, per the early return at ~line 702-704), and that the
defense-in-depth block is acceptable (it's cheap and unreachable but
prevents future drift).

### WR-06: Large-text clipboard bypasses chunked path on server outbound

**Files modified:** `server/session_runtime.py`
**Commit:** `46e02cf`
**Applied fix:** `_on_clipboard_change` text branch now thresholds on
`CHUNK_BYTES = 1024 * 1024`:
- Text <= 1 MB: unchanged — rides the single `CLIPBOARD_RECV`
  envelope (zero overhead).
- Text > 1 MB: routed through `_enqueue_chunked_clipboard` using the
  raw UTF-8 slice path (not base64). The client's
  `_handle_clipboard_chunk` text branch (`client/protocol.py:~1411`)
  already reassembles by concatenation, so no client change required.

All 39 clipboard tests (server + integration + common) remain green.

## Skipped Issues

None — all 7 findings in scope were fixed.

## Additional Notes

- **Baseline maintained:** Full quick suite passes with 3922 passed / 0
  failed / 105 skipped, up from the 3920/0/105 baseline due to the 2
  new CR-01 regression tests.
- **No unrelated files touched.** Every fix was scoped narrowly to the
  finding.
- **Commit atomicity:** Each finding is a separate commit. CR-01 has
  one additional follow-on commit (`96e03c6`) for the regression tests,
  tagged `test(03):` not `fix(03):` since it adds tests, not code fix.
- **WR-04 is documentation-only** — the effective mitigation is WR-02's
  bounded-size cap. Commit adds a note on both `_next_clipboard_seq`
  docstrings.
- **WR-05 is defense-in-depth dead code** — the REVIEW-flagged scenario
  is already handled by the `mirror_all` early return. Flagged as
  "requires human verification" per the logic-bug semantic-correctness
  disclaimer.

---

_Fixed: 2026-04-20T00:00:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
