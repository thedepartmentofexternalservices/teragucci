---
phase: 03-display-multi-monitor-clipboard
reviewed: 2026-04-20T00:00:00Z
depth: standard
files_reviewed: 51
files_reviewed_list:
  - client/bookmarks.py
  - client/clipboard_toggle_menu.py
  - client/coord_debug_overlay.py
  - client/fullscreen_toolbar.py
  - client/icons.py
  - client/main_window.py
  - client/monitor_selector.py
  - client/protocol.py
  - client/remap_banner.py
  - client/session.py
  - client/toasts.py
  - client/viewer.py
  - common/clipboard_chunks.py
  - common/messages.py
  - docs/release.md
  - server/client_session.py
  - server/clipboard.py
  - server/mac_clipboard.py
  - server/mac_screen_capture.py
  - server/monitor_hotplug.py
  - server/screen_capture.py
  - server/session_manager.py
  - server/session_runtime.py
  - server/stream_loop.py
  - tests/client/test_bookmarks.py
  - tests/client/test_clipboard_toggle_menu.py
  - tests/client/test_connect_dialog_mode.py
  - tests/client/test_fullscreen_toolbar_mode_badge.py
  - tests/client/test_icon_clipboard.py
  - tests/client/test_remap_banner.py
  - tests/client/test_session_monitor_list_wiring.py
  - tests/client/test_toasts.py
  - tests/client/test_viewer_dpr.py
  - tests/client/test_viewer_screen_changed.py
  - tests/common/test_clipboard_chunking.py
  - tests/common/test_cursor_math.py
  - tests/common/test_messages_phase2.py
  - tests/conftest.py
  - tests/integration/test_capture_mode.py
  - tests/integration/test_clipboard_image.py
  - tests/integration/test_clipboard_text_large.py
  - tests/integration/test_monitor_hotplug.py
  - tests/server/test_capture_crop.py
  - tests/server/test_clipboard.py
  - tests/server/test_clipboard_image.py
  - tests/server/test_clipboard_toggles.py
  - tests/server/test_mac_clipboard_image.py
  - tests/server/test_mac_screen_capture_hotplug.py
  - tests/server/test_screen_capture_hotplug.py
  - tests/server/test_session_manager_edid.py
findings:
  critical: 1
  warning: 6
  info: 7
  total: 14
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-04-20T00:00:00Z
**Depth:** standard
**Files Reviewed:** 51
**Status:** issues_found

## Summary

Phase 3 (Display + Multi-Monitor + Clipboard) delivers a cleanly-factored
implementation across 7 waves: per-session monitor mode, mixed-DPI cursor
math, BGRA crop pipeline, monitor hot-plug UX, and bidirectional text+image
clipboard with chunked transport. The security posture is strong —
defense-in-depth PNG validation at both ingress boundaries, explicit
whitelist-guard on `capture_mode` / `monitor_mode` (T-03-05 / T-03-09),
chunk-0 boundary toggle gating (Pitfall 7), stale-chunk reaping (T-03-25),
and informational-reason-only `KEY_RESET_MODIFIERS` discipline.

One **Critical** bug landed in the client session's degradation-routing
code path: `client/session.py::_on_monitor_list_with_degradations` reads
`self._capture_mode` while the attribute that's actually set on connect
is `self._monitor_mode`. The fallback via `getattr(..., "mirror_all")` masks
the AttributeError, so the pick_one single-session matching heuristic
(T-03-18 mitigation) and the topology-change banner-case selector both
silently fall through to the `mirror_all` branch. This breaks the Surface 5
remap-banner flow for pick_one bookmarks — the exact flow UI-SPEC C-05
exists to protect.

Secondary concerns:

- **WR-01** `server/screen_capture.py::detect_hotplug` swaps `self._sct`
  but does not refresh `self._monitor` — subsequent `capture_raw_bgra`
  calls `self._sct.grab(self._monitor)` with a stale dict from the old
  `_sct`. On physical monitor removal this can crash the stream.
- **WR-02** Client-side `_dropped_seqs` set (and server-side per-session
  equivalent) grows unboundedly across the protocol lifetime.
- **WR-03** `toasts.py::_enforce_max_visible` only dismisses one toast
  per invocation and relies on fade-out animation completion to prune;
  T-03-19 stack cap is weaker than advertised during burst arrivals.
- **WR-04** `_clipboard_seq` wraps at 2³² and can collide with stale
  `_dropped_seqs` entries — improbable but silent-data-loss-class.
- **WR-05** `apply_capture_mode` returns True on the `not self.capture`
  branch without clearing `picked_monitor_id` / `picked_monitor_name`,
  leaving stale state if the mode flips `pick_one → mirror_all` before
  capture is initialized.
- **WR-06** `_on_clipboard_change` server text path builds a single
  `ClipboardMsg` for the whole payload — large text (>1 MB) rides the
  per-client `send_queue` (maxsize=4) as one huge message, risking
  IDR-on-drop noise during legitimate clipboard traffic.

## Critical Issues

### CR-01: Degradation routing references wrong attribute — pick_one banner never fires

**File:** `client/session.py:611` and `client/session.py:649`
**Issue:** `_on_monitor_list_with_degradations` reads
`getattr(self, "_capture_mode", "mirror_all")` but the attribute set by
`connect()` at line 173 is `self._monitor_mode` (no leading underscore-c).
The attribute `_capture_mode` never exists, so `getattr` always returns
the `"mirror_all"` fallback. Consequences:

1. The pick_one single-session match heuristic (line 611) will never fire
   because `_capture_mode == "pick_one"` is never True — every fallback
   event falls through, violating T-03-18's client-token filter intent
   and the UI-SPEC Surface 5 pick_missing banner contract.
2. The topology-change case selector (line 649) always picks the
   `mirror_add` / `mirror_remove` branch regardless of actual session
   mode, breaking `single_change` banner rendering for `single` /
   `pick_one` sessions.

The `Session.capture_mode` property at line 525 reads `self._monitor_mode`
correctly — the bug is localized to the two `getattr` sites in
`_on_monitor_list_with_degradations`.

**Fix:** Rename the two references (or add an `_capture_mode` alias at
`connect()` time for minimal diff):

```python
# client/session.py ~line 611
elif (len(degradations) == 1
      and getattr(self, "_monitor_mode", "mirror_all") == "pick_one"):

# client/session.py ~line 649
mode = getattr(self, "_monitor_mode", "mirror_all")
```

Add a regression test in `tests/client/test_session_monitor_list_wiring.py`
that exercises the pick_one single-session path and asserts
`show_for_case("pick_missing", ...)` fires — the current test suite does
not catch the silent fallback because every path lands in the `mirror_all`
branch by default.

## Warnings

### WR-01: Stale `_monitor` dict after `detect_hotplug` can crash capture

**File:** `server/screen_capture.py:443-464`
**Issue:** `detect_hotplug` constructs a new `mss.mss()` instance,
closes the old one, and assigns `self._sct = new_sct`. It calls
`_refresh_monitor_info()` (xrandr metadata) but never re-derives
`self._monitor` from the new `_sct.monitors`. The next `capture_raw_bgra`
call invokes `self._sct.grab(self._monitor)` with a dict whose coordinates
reference the OLD topology. If a monitor was removed and
`self.monitor_index` no longer exists in the new `_sct.monitors`, mss
may raise or return out-of-bounds pixel data.

The `MonitorHotplug.run` caller invokes `encoder_lifecycle.restart()` but
that helper only reads `runtime.capture.width / height`, it does not call
`capture.reinit()` — so the stale `self._monitor` persists across the
restart.

**Fix:** Re-select the monitor after the `_sct` swap:

```python
# server/screen_capture.py ~line 451
self._sct = new_sct
self._refresh_monitor_info()
# Re-select so self._monitor references the NEW _sct.monitors.
# Clamps inside _select_monitor fall back to primary if index is out of range.
self._select_monitor(self.monitor_index)
new_sig = [...]
```

### WR-02: `_dropped_seqs` grows unbounded over protocol/session lifetime

**File:** `client/protocol.py:222`, `server/client_session.py:95`
**Issue:** The Pitfall 7 mitigation tracks sequences dropped at chunk-0
via a `set()` that is never pruned. Over a long session with many toggle
flips, or an attacker-controlled peer spamming chunk-0 with random
`sequence_id` values, the set grows without bound. The per-chunk
`if seq_id in self._dropped_seqs` check remains O(1), but memory is
unbounded — a soft DoS vector that didn't make the STRIDE register.

**Fix:** Cap the set with an LRU-ish eviction, or TTL-expire entries:

```python
# On add — evict oldest when over cap.
_DROPPED_SEQS_MAX = 4096
if len(self._dropped_seqs) >= _DROPPED_SEQS_MAX:
    # Drop arbitrary element; set iteration order is insertion-order
    # in CPython 3.7+ so this approximates FIFO eviction.
    self._dropped_seqs.pop()
self._dropped_seqs.add(seq_id)
```

Alternatively, pair each dropped seq with a monotonic timestamp and evict
on stale cleanup (piggy-back on the assembler cleanup at
`client/protocol.py:1396` and `server/session_runtime.py:1186`).

### WR-03: `_enforce_max_visible` drops only one toast per call — stack can exceed cap during bursts

**File:** `client/toasts.py:162-176`
**Issue:** The `while len(_toast_stack) > _MAX_VISIBLE` loop contains an
unconditional `break` after the first `dismiss()`, explained as a
workaround for "headless tests" where fade-out doesn't drain the stack
synchronously. In production, if 5 toasts arrive nearly simultaneously
(e.g. server emits 5 monitor-switched events on a chained hot-plug),
each `show()` calls `_enforce_max_visible` exactly once, dismisses the
oldest, but the stack remains at its peak size until each fade-out
animation (_FADE_OUT_MS=200ms) completes. The visible stack briefly
holds 4-5 toasts. T-03-19's "MAX_VISIBLE=3 caps on-screen widget count"
mitigation holds asymptotically but not during the burst window.

**Fix:** Track fade-out pending count separately from the stack length,
or dismiss a number equal to the overflow in a single pass:

```python
def _enforce_max_visible():
    overflow = len(_toast_stack) - _MAX_VISIBLE
    # Iterate a bounded slice so we can't infinite-loop even if dismiss()
    # fails to remove entries synchronously.
    for i in range(max(0, overflow)):
        _toast_stack[i].dismiss()
```

Cap the loop so a pathological `dismiss()` implementation can't hang.

### WR-04: Clipboard sequence_id wrap can collide with stale `_dropped_seqs` entries

**File:** `client/protocol.py:478-486`, `server/session_runtime.py:589-600`
**Issue:** `_next_clipboard_seq` wraps at 2³² (0xFFFFFFFF mask). If a
session runs long enough or an attacker forces enough clipboard events,
the counter wraps and a new `sequence_id` can match an old entry in
`_dropped_seqs`. The next chunk-0 check at `client/protocol.py:1335`
(`if seq_id in self._dropped_seqs`) silently drops legitimate payload.
The comment acknowledges the wrap is "benign unless a single client
receives 2³² clipboard events in one session (not a real threat)" but
combined with WR-02's unbounded growth, the collision probability is
non-zero over multi-day sessions.

**Fix:** Couple the fix with WR-02 — when `_dropped_seqs` has bounded
size + TTL eviction, the wrap collision window shrinks to practically
zero. Add a comment at `_next_clipboard_seq` noting the dependency:

```python
def _next_clipboard_seq(self) -> int:
    # 32-bit wrap depends on WR-02 fix for _dropped_seqs bounded size +
    # TTL eviction to stay collision-free in practice.
    self._clipboard_seq = (self._clipboard_seq + 1) & 0xFFFFFFFF
    return self._clipboard_seq
```

### WR-05: `apply_capture_mode` leaves stale picked_monitor state on no-capture path

**File:** `server/session_runtime.py:704-710`
**Issue:** When `apply_capture_mode` is invoked before `self.capture`
is initialized (unit test path, or session with capture failure), the
method returns `True` without resetting `session.picked_monitor_id` or
`session.picked_monitor_name`. A session that started in `pick_one`
mode and flips to `mirror_all` before capture comes up would retain
the old pick fields — later UI code that reads `picked_monitor_name`
for toolbar badge rendering will display stale data.

**Fix:** Clear the pick fields on the no-capture early-return:

```python
# server/session_runtime.py ~line 705
if not self.capture:
    session.crop_rect = None
    if mode == "mirror_all":
        session.picked_monitor_id = -1
        session.picked_monitor_name = ""
    return True
```

### WR-06: Large-text clipboard bypasses chunked path on server outbound

**File:** `server/session_runtime.py:580-587`
**Issue:** The server's `_on_clipboard_change` always emits a single
`ClipboardMsg(CLIPBOARD_RECV)` for text — even payloads over 1 MB. The
client sends >1 MB text via `_send_chunked`, but the server never
chunks outbound text. A 10 MB clipboard paste on the server-side
virtual display ships as one JSON message into `cs.send_queue`
(maxsize=4). On the wire this is one large websockets frame; the per-
client drop-OLDEST policy in `client_session.py::enqueue` could drop
legitimate video frames during the clipboard send, triggering IDR-on-drop
and wasting bandwidth.

**Fix:** Chunk outbound text symmetrically with the client's
`_send_chunked`:

```python
# server/session_runtime.py _on_clipboard_change text branch
if is_image:
    ...
else:
    # Small text rides CLIPBOARD_RECV; large text chunks via CLIPBOARD_CHUNK
    # to avoid blocking the per-client send queue. Mirrors client behavior.
    CHUNK_BYTES = 1024 * 1024
    if len(payload) <= CHUNK_BYTES:
        msg_json = ClipboardMsg(
            type=MsgType.CLIPBOARD_RECV,
            content_type="text/plain",
            data=payload,
        ).to_json()
        asyncio.run_coroutine_threadsafe(cs.enqueue(msg_json), self._event_loop)
    else:
        self._enqueue_chunked_clipboard(cs, "text/plain", payload)
```

Note: the client already handles inbound text chunks via
`_handle_clipboard_chunk` line 1381 — no client change required.

## Info

### IN-01: Unused imports in `fullscreen_toolbar.py`

**File:** `client/fullscreen_toolbar.py:13,15`
**Issue:** `QFont`, `QApplication`, and `QComboBox` are imported but
never referenced in the module.
**Fix:** Remove the three unused names from the imports — will land a
single-line diff.

### IN-02: `_encrypt_password` is XOR-with-SHA256-hash, not real crypto

**File:** `client/bookmarks.py:65-85`
**Issue:** The docstring at line 44-45 already acknowledges "This is
NOT high-security encryption — it just prevents casual reading of saved
passwords." On macOS the machine key degrades further because
`/etc/machine-id` and `/var/lib/dbus/machine-id` don't exist, so the
key is derived from `platform.node()` + `platform.machine()` + `Path.home()`
— all trivially enumerable by anyone with a shell account on the Mac.
**Fix:** No change for v1 (scope-appropriate) — but document in
`docs/release.md` that saved passwords should be considered
pseudo-obfuscated, not encrypted, and recommend the Keychain on macOS as
a v1.1 follow-up. A single sentence in the release runbook closes the
gap between code comments and user-facing expectation.

### IN-03: EDID monitor-name descriptor padded with LF instead of space

**File:** `server/session_manager.py:258-260`
**Issue:** Per VESA EDID 1.3 § 3.10.3, ASCII descriptors shorter than 13
bytes should be terminated with `0x0A` and padded with `0x20` (space).
The generator uses `name_bytes = name.encode('ascii')[:13].ljust(13, b'\x0a')`
which fills every pad byte with LF. Most parsers are lenient (Flame
accepts it per the DISP-04 acceptance criteria), but strict parsers
could flag it.
**Fix:** Pad with `0x20` after the LF terminator:

```python
name_bytes = (name.encode('ascii')[:12] + b'\x0a').ljust(13, b'\x20')
```

### IN-04: `ScreenCapture.__init__` ignores `monitor_index=0` silently

**File:** `server/screen_capture.py:353-362`
**Issue:** `_select_monitor` clamps out-of-range indices to 1 (primary)
without logging. When `monitor_index=0` is passed (the "virtual desktop"
value mss uses at index 0), the clamp silently reroutes to the primary
monitor — surprising for callers that deliberately requested the full
virtual desktop.
**Fix:** Log the clamp so session-bootstrap paths reveal the silent
downgrade:

```python
if index >= len(monitors):
    logger.warning(
        "screen_capture.monitor_index_out_of_range requested=%d "
        "available=%d → primary",
        index, len(monitors),
    )
    index = 1
```

### IN-05: `MacScreenCapture._enumerate_displays` redundant `list()` on already-list content

**File:** `server/mac_screen_capture.py:422`
**Issue:** `displays = list(content.displays())` works but the PyObjC
`displays()` selector returns an `NSArray` which behaves as a sequence
— the list wrap makes a defensive copy. Not wrong, just worth a
comment noting the intent (the NSArray reference lifetime is tied to
`content`, so the `list(...)` detaches for safety).
**Fix:** Add a one-line comment clarifying intent:

```python
# list(...) detaches from the NSArray whose lifetime is tied to `content`.
displays = list(content.displays())
```

### IN-06: `ClientProtocol.set_capture_mode` whitelist is duplicated with bookmarks.py

**File:** `client/protocol.py:553`, `client/bookmarks.py:157-162`
**Issue:** The valid-mode tuple `("single", "mirror_all", "pick_one")`
appears inline in three places: `client/protocol.py:553`,
`client/bookmarks.py:157`, and `server/session_runtime.py:645`. A single
source-of-truth constant on `common/messages.py` (e.g. `CAPTURE_MODES`)
would prevent drift when future modes are added.
**Fix:** Add `common/messages.py::CAPTURE_MODES` as a tuple and import
it in all three consumers. Low-priority refactor — can ride the next
monitor-mode extension.

### IN-07: `viewer.py` paste-detect uses `MODIFIER_BIT_CTRL` check but Key_V check is not platform-qualified

**File:** `client/viewer.py:1135-1136`
**Issue:** The paste-detect branch at `keyPressEvent` fires on
`Qt.Key_V + MODIFIER_BIT_CTRL`. On macOS, the `_qt_modifiers_to_int`
helper folds `Qt.MetaModifier` (Cmd) into `MODIFIER_BIT_CTRL` (line 1273
comment confirms this is intentional). But the branch doesn't guard
against Key_V without a press_state mismatch — `paste_requested` could
fire on Ctrl+V keyDOWN and again on a subsequent chord permutation. Not
a bug in current code (isAutoRepeat is guarded at L1106), but the logic
is fragile if modifier handling changes. No action required — worth
noting if future work touches the paste-detect path.

---

_Reviewed: 2026-04-20T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
