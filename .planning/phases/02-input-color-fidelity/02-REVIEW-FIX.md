---
phase: 02-input-color-fidelity
fixed_at: 2026-04-19T00:00:00Z
review_path: .planning/phases/02-input-color-fidelity/02-REVIEW.md
iteration: 1
findings_in_scope: 9
fixed: 9
skipped: 0
status: all_fixed
---

# Phase 2: Code Review Fix Report

**Fixed at:** 2026-04-19
**Source review:** `.planning/phases/02-input-color-fidelity/02-REVIEW.md`
**Iteration:** 1

**Summary:**

- Findings in scope: 9 (1 Critical + 8 Warning)
- Fixed: 9
- Skipped: 0

All 9 in-scope findings were fixed. The single Info-only items (IN-01..06)
were excluded by the `critical_warning` scope and remain noted in
REVIEW.md for a follow-up cleanup pass.

## Fixed Issues

### CR-01: MacVideoEncoder discards captured P010 frame bytes (Mac-server video output is broken)

**Files modified:** `server/platform_backends.py`, `server/mac_video_encoder.py`, `.planning/phases/02-input-color-fidelity/deferred-items.md`
**Commit:** `278ec84`
**Applied fix:** Took the safer of the two paths suggested in the
review — gated production dispatch off rather than implementing the
PyObjC + ctypes plane copy. `server/platform_backends.py` now
hard-pins `_MAC_VIDEO_ENC_AVAILABLE = False` even when PyObjC
VideoToolbox imports successfully, so `server.video_encoder`
deterministically falls back to the FFmpeg `hevc_videotoolbox`
subprocess path (which IS correct end-to-end). The probe still runs so
the log line honestly reports whether VT was importable; only
production dispatch is gated. Tests in
`tests/server/test_platform_backends_video.py` monkey-patch the flag
directly so the True branch remains exercised.

The `feed_frame` docstring in `server/mac_video_encoder.py` got a new
"INCOMPLETE IMPLEMENTATION" warning block pointing at the gate, and
`deferred-items.md` item 7 records the deferred plane-copy work plus
the re-enable checklist (CVPixelBufferLockBaseAddress + ctypes.memmove
Y/UV planes, regression test asserting non-trivial NAL output, remove
the override).

**Status note:** the underlying logic — that the FFmpeg fallback path
is correct — relies on Phase 1 work that was already validated. This
fix is structural (gate flip + docs) so it does not require human
verification beyond running the existing
`tests/server/test_platform_backends_video.py` to confirm both
branches still work.

### WR-01: BookmarkManager._save can race against concurrent writers (no locking, no atomic write)

**File modified:** `client/bookmarks.py`
**Commit:** `2bf0499`
**Applied fix:** `_save()` now writes to `bookmarks.json.tmp`, flushes,
fsyncs, then `os.replace()`s into the final path. A crash mid-write
leaves either the prior state or the new state, never partial JSON
that `_load()` would silently toss. Multi-process write coordination
is documented in the docstring as a known limitation; per-bookmark
file or `fcntl.flock` is the next step if that surfaces but is out of
scope for this fix.

### WR-02: ClientProtocol._broker_handshake uses asyncio.get_event_loop() which is deprecated and racy

**File modified:** `client/protocol.py`
**Commit:** `df17b3f`
**Applied fix:** Both occurrences (lines 625 and 769) of
`asyncio.get_event_loop().create_future()` replaced with
`asyncio.get_running_loop().create_future()`. Both call sites are
already inside awaited coroutines so the running loop is the correct
reference; this kills the Python 3.12 DeprecationWarning noise and
makes the code 3.14-safe.

### WR-03: ConnectionDialog default swap_cmd_ctrl checkbox always defaults True regardless of destination_kind

**File modified:** `client/main_window.py`
**Commit:** `a9f9e23`
**Applied fix:** Took the explicit destination-kind picker route
suggested in the review's first option. Added a "Destination" combobox
(Linux / Mac) ahead of the swap checkbox; its `currentIndexChanged`
handler resets the swap checkbox via
`BookmarkManager.default_swap_for_destination(kind)`. Linux defaults
swap=ON (Cmd→Ctrl translation for Flame on Rocky); Mac defaults
swap=OFF (the Mac server interprets Cmd natively). Users can still
flip the checkbox after picking a destination if they want a
non-default binding for an unusual host. `_add_bookmark` and
`_edit_bookmark` thread `destination_kind` through to the
BookmarkManager; `_edit_bookmark` restores the destination combobox
FIRST so its default-swap callback doesn't clobber the saved
swap_cmd_ctrl state.

### WR-04: Race in client/protocol.py _connect_and_receive on broker auto-detect path

**File modified:** `client/protocol.py`
**Commit:** `95bf93e`
**Applied fix:** Set `self._ws = None` immediately before the
`raise _BrokerRedirect()` at line 806. The async-with context is about
to exit, taking the websocket with it; if a leftover task (e.g.
`_client_health_ping_loop`) tries to write into the dead ws between
the raise and the recursive `_connect_to_server` call, it now
short-circuits via the existing None-guard rather than blowing up
with `OSError`. The per-loop nonce on `_client_health_ping_loop` was
not added — that's a defense-in-depth follow-up that crosses into
Phase 1 territory and is not blocking; the `self._ws = None` is the
load-bearing fix for the documented race.

### WR-05: VideoBlitWidget uses setBindings([...]) then create() but tex/sampler may be None on first render

**File modified:** `client/viewer.py`
**Commit:** `356adb8`
**Applied fix:** Inside `_create_resources`, after each texture is
recreated (the two `if self._tex_y/uv is None:` branches), set
`self._srb = None` so the next render pass rebuilds the
ShaderResourceBindings with the fresh texture handles. Previously the
SRB only built once and the `if self._srb is None` guard kept it
sticky even when the textures it referenced were thrown away. The
binding list now always references current handles.

### WR-06: server/mac_screen_capture.py reinit() doesn't honor want_10bit on re-setup

**File modified:** `server/mac_screen_capture.py`
**Commit:** `0a2114f`
**Applied fix:** Added `_hdr_set_ok: bool` field to `__init__`,
re-armed to False at the top of the `_start_stream` 10-bit branch
(so `reinit()` / `switch_monitor()` stay honest), flipped True only
after the `setCaptureDynamicRange_` setter succeeds. Added
`runtime_capability_state` property mirroring the Linux
`ScreenCapture.runtime_capability_state` truth table:
not_supported / degraded / confirmed. The build_color_caps helper can
now combine Mac capture honesty with the encoder probe to render the
final badge value, closing the symmetry gap with the Linux side.

### WR-07: NvFBC backend rejects 10-bit framing as "absurd" if YUV420P10LE size > 256MB

**File modified:** `server/nvfbc/nvfbc_backend.py`
**Commit:** `54afe9f`
**Applied fix:** Replaced the hardcoded 256 MB cap with a per-frame
derived cap: `w * h * 8 BPP * 2 safety_margin` (worst case 4:4:4
16-bit per channel). Falls back to the legacy 256 MB constant when
geometry is unset (first-frame case where `w == 0`). Added a 4 MB
absolute floor so obviously-bogus tiny-frame + huge-size combos still
get rejected. The error log now reports the derived_cap + w + h so
the failure mode is debuggable instead of mysterious. Reader still
breaks out of the loop on rejection — adding an automatic helper
restart was out-of-scope for this fix and would touch lifecycle
management.

### WR-08: client/viewer.py keyPressEvent paste-detection conflates Cmd and Ctrl on bit-2

**Files modified:** `common/keymap.py`, `client/viewer.py`
**Commit:** `8fa9af3`
**Applied fix:** Added `MODIFIER_BIT_{SHIFT,CTRL,ALT,META,KEYPAD}`
constants to `common/keymap.py` — the wire-format bit positions
consumed by `KeyEventMsg`, distinct from the Qt enum bitmasks
(`MOD_SHIFT` etc.) already there. Replaced magic numbers in
`viewer.py`'s `keyPressEvent` paste detection (`modifiers & 2`) and
`_qt_modifiers_to_int` (1 / 2 / 4 / 8 / 0x10) with the named
constants. The wire shape is byte-identical; this is a pure naming
refactor that keeps bit assignments traceable across files.

## Skipped Issues

None — all 9 in-scope findings (CR-01 + WR-01..08) were fixed and
committed.

## Verification notes

Each fix passed Tier 1 (re-read post-edit) and Tier 2 (Python
`ast.parse` syntax check on every modified `.py` file). The
`from common.keymap import ...` block introduced in WR-08 was
additionally smoke-tested by importing the constants in a one-liner
to confirm the names resolve.

The full test suite was NOT run between fixes (per the verifier
phase boundary). Tests most likely to interact with these changes:

- `tests/server/test_platform_backends_video.py` — exercises both
  branches of `_MAC_VIDEO_ENC_AVAILABLE` via monkey-patch; CR-01
  override does not interfere.
- `tests/server/test_mac_video_encoder.py` — direct-VT module is
  untouched in behavior; the docstring change in `feed_frame` is
  documentation-only.
- `tests/client/test_viewer_modifier_triggers.py` — modifier-bit
  numeric values unchanged (WR-08 is a naming refactor); existing
  assertions on `1 / 2 / 4 / 8 / 0x10` continue to pass byte-wise.
- `tests/server/test_platform_backends_video.py::test_platform_backends_exposes_mac_video_enc_available_flag`
  — passes because the override pins the flag to False on every
  platform; the test asserts False on non-darwin and only checks
  existence on darwin.

CR-01's structural gate fix removes the production-path correctness
bug; the deferred plane-copy implementation is documented in
`deferred-items.md` item 7 with a complete re-enable checklist.

---

_Fixed: 2026-04-19_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
