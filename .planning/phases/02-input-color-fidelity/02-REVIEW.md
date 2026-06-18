---
phase: 02-input-color-fidelity
reviewed: 2026-04-19T00:00:00Z
depth: standard
files_reviewed: 47
files_reviewed_list:
  - .github/workflows/ci.yml
  - client/app.py
  - client/bookmarks.py
  - client/connection_supervisor.py
  - client/key_diagnostic.py
  - client/main_window.py
  - client/protocol.py
  - client/session.py
  - client/shaders/video_blit.frag
  - client/shaders/video_blit.vert
  - client/tcc_detect.py
  - client/video_decoder.py
  - client/viewer.py
  - common/messages.py
  - common/session_fsm.py
  - docs/build-pyav-macos.md
  - docs/release.md
  - requirements-dev.txt
  - requirements-server.txt
  - scripts/build_shaders.sh
  - server/input_injector.py
  - server/mac_input_injector.py
  - server/mac_screen_capture.py
  - server/mac_video_encoder.py
  - server/nvfbc/nvfbc_backend.py
  - server/nvfbc/nvfbc_capture.c
  - server/platform_backends.py
  - server/screen_capture.py
  - server/session_manager.py
  - server/session_runtime.py
  - server/video_encoder.py
  - tests/client/test_protocol_modifier_wiring.py
  - tests/client/test_tcc_detection.py
  - tests/client/test_video_decoder.py
  - tests/client/test_viewer_modifier_triggers.py
  - tests/client/test_viewer_proximity.py
  - tests/client/test_viewer_qrhi_video_layer.py
  - tests/common/test_session_fsm_pen.py
  - tests/integration/conftest.py
  - tests/integration/test_crazy_hotkeys.py
  - tests/integration/test_modifier_stress.py
  - tests/server/test_mac_pen_injector.py
  - tests/server/test_mac_video_encoder.py
  - tests/server/test_modifier_dispatch.py
  - tests/server/test_platform_backends_video.py
  - tests/server/test_video_encoder_mock.py
  - tests/server/test_wacom_matrix_emission.py
  - tests/smoke/test_ten_bit_pipeline.py
  - tools/wacom_quant_analysis.py
findings:
  critical: 1
  warning: 8
  info: 6
  total: 15
status: issues_found
---

# Phase 2: Code Review Report

**Reviewed:** 2026-04-19
**Depth:** standard
**Files Reviewed:** 47
**Status:** issues_found

## Summary

Phase 2 (input-color-fidelity) lands a substantial body of work: 10-bit YUV
pipeline plumbing (capture → encode → wire → decode → blit), client-side
modifier-state hardening (D-10 swap, D-11 release-all, D-13 X-repeat-off,
D-14 lock-state bits, D-15 IME passthrough, D-19 pen-proximity FSM), QRhi
Metal video blit (D-02), TCC + Wacom diagnostic UI (D-20), and the direct
VideoToolbox encoder delegate (D-04).

Architecture and tests look strong. Hard constraints from `CLAUDE.md` are
honored: no `ssl.CERT_NONE` regression in production code paths (the only
remaining occurrence is the documented Phase-1 double-gated dev escape
hatch in `common/tls_opt_out.py` + `common/quic_transport.py:429`), the
`mac_input_injector.pen_event` mouse-click downgrade is documented as a
v1 known limitation in `docs/release.md` (D-07 FAIL outcome), the BT.709
shader explicitly excludes HDR matrices, and `decode_frame_planes` raises
loudly on Main10 silent fallback.

The single Critical finding below is a real correctness bug in the new
direct VideoToolbox encoder path (D-04): `MacVideoEncoder.feed_frame`
allocates an empty `CVPixelBuffer` and submits it to VT, then explicitly
discards the captured P010 plane bytes via `del p010_bytes` with a TODO.
On a Mac server that picks the direct-VT path (any host with PyObjC
VideoToolbox installed), every encoded frame will be black/garbage. The
TODO is documented but the code is wired into the production dispatch
(`server.platform_backends._MAC_VIDEO_ENC_AVAILABLE` flips to True
whenever the import succeeds), and `server.video_encoder.start()` will
prefer it over the FFmpeg fallback. This makes Mac-server video output
unusable in the normal install flow.

The Warnings cover real but non-show-stopping issues: a thread-safety
concern in `bookmarks.py`, an event loop assumption in `client/protocol.py
::_broker_handshake`, a stale-MagicMock pattern, several unused imports,
and a couple of platform-specific edge cases. The Info items are mostly
code-smell observations and a small inconsistency around the `nvfbc_capture
--push 1` mode flags.

## Critical Issues

### CR-01: MacVideoEncoder discards captured P010 frame bytes (Mac-server video output is broken)

**File:** `server/mac_video_encoder.py:222-280`
**Issue:** `feed_frame()` constructs an empty `CVPixelBuffer` via
`CV.CVPixelBufferCreate(None, w, h, P010, None, None)` and submits it
directly to `VTCompressionSessionEncodeFrame`. The captured plane bytes
are explicitly discarded:

```python
# TODO: lock base address and memcpy the Y + UV planes from
# ``p010_bytes`` into the buffer once SCK delivers P010 directly
# (Task 2 plumbs the negotiation flag). Until then we submit an
# empty allocation so the encoder pipeline shape is exercised.
del p010_bytes  # unused until plane-copy lands
```

This module is wired into the live production dispatch in
`server/platform_backends.py:65-83` — `_MAC_VIDEO_ENC_AVAILABLE` flips to
True the moment `pyobjc-framework-VideoToolbox` is importable
(it's a hard requirement in `requirements-server.txt:38-40` for `darwin`).
`server/video_encoder.py:230-253` then prefers the Mac direct-VT delegate
over the FFmpeg `hevc_videotoolbox` fallback.

Net effect: any Mac server running through the documented install path
encodes uninitialized `CVPixelBuffer` memory for every frame. The client
will receive valid HEVC NAL units (so the wire shape works), but the
displayed frames will be black or whatever stale heap memory the buffer
was given. The capability badge will read "10-bit confirmed" while the
actual content is undefined.

**Fix:** Either complete the plane copy (preferred — see TODO at line
250-253), OR make the dispatch refuse to use the direct-VT path until
the plane copy lands. A minimal interim guard:

```python
# server/platform_backends.py — top of the IS_MACOS branch
_MAC_VIDEO_ENC_AVAILABLE = False  # Disabled until plane copy lands
# OR mark the encoder __init__ to raise NotImplementedError so the
# fallback to FFmpeg hevc_videotoolbox kicks in deterministically.
```

If keeping the direct path live, replace the empty allocation with a
real plane copy:

```python
# In feed_frame, after creating pb:
CV.CVPixelBufferLockBaseAddress(pb, 0)
try:
    y_size = self._width * self._height * 2  # P010: 2 bytes/sample
    uv_size = (self._width // 2) * (self._height // 2) * 4  # half-res RG16
    if len(p010_bytes) < y_size + uv_size:
        log.warning("mac_video_encoder.short_p010_buffer",
                    got=len(p010_bytes), expected=y_size + uv_size)
        return
    y_addr = CV.CVPixelBufferGetBaseAddressOfPlane(pb, 0)
    uv_addr = CV.CVPixelBufferGetBaseAddressOfPlane(pb, 1)
    ctypes.memmove(int(y_addr), p010_bytes[:y_size], y_size)
    ctypes.memmove(int(uv_addr), p010_bytes[y_size:y_size + uv_size], uv_size)
finally:
    CV.CVPixelBufferUnlockBaseAddress(pb, 0)
```

Either approach must land before this code is reachable in production. A
follow-up regression test should feed a known-pattern frame and verify
the encoded NAL output is non-trivial (not just SPS/PPS + empty slice).

## Warnings

### WR-01: BookmarkManager._save can race against concurrent writers (no locking, no atomic write)

**File:** `client/bookmarks.py:151-158, 197-218`
**Issue:** `_save()` opens `bookmarks.json` directly for write and dumps the
serialized dict. Two concerns:

1. **No atomic write:** A crash mid-write leaves a truncated/corrupt JSON
   file. Subsequent client launches hit the bare `except Exception` in
   `_load()` (line 136) which logs and silently continues — meaning
   *every saved bookmark is lost* with no warning to the user.
2. **No locking:** `add()`, `update()`, `remove()`, `mark_connected()`,
   and `import_bookmarks()` all call `_save()` without coordination. If
   two simultaneous client instances run (Randy's documented multi-machine
   workflow per CLAUDE.md), the last writer wins silently.

**Fix:** Use atomic write (write to `bookmarks.json.tmp`, fsync, then
`os.replace()`). For multi-process safety also add a `fcntl.flock`
or use a per-bookmark file (one JSON per UUID). Minimal version:

```python
def _save(self):
    try:
        data = {bid: p.to_dict() for bid, p in self._profiles.items()}
        tmp = self._bookmarks_file.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._bookmarks_file)
    except Exception as e:
        logger.error("Failed to save bookmarks: %s", e)
```

### WR-02: ClientProtocol._broker_handshake uses asyncio.get_event_loop() which is deprecated and racy

**File:** `client/protocol.py:625, 769`
**Issue:** Both broker-handshake paths call
`asyncio.get_event_loop().create_future()`. Per Python 3.12+ deprecation,
`get_event_loop()` issues a DeprecationWarning when there's no running
loop and behaves nondeterministically (auto-creates a loop in some
versions, raises in others, returns the wrong loop in threaded contexts).

The broker handshake already runs inside `_run_loop` which `set_event_loop`s
correctly, so the safe replacement is `asyncio.get_running_loop()`:

```python
self._machine_selection_future = asyncio.get_running_loop().create_future()
```

CI is on Python 3.12 (`.github/workflows/ci.yml:20, 75`); this will
generate DeprecationWarning noise today and likely break on Python 3.14.

### WR-03: ConnectionDialog default swap_cmd_ctrl checkbox always defaults True regardless of destination_kind

**File:** `client/main_window.py:102-106`
**Issue:** The new "Swap Cmd/Ctrl for this server (Mac client → Linux Flame)"
checkbox is hardcoded to `setChecked(True)` at construction. The class
docstring on `BookmarkManager.default_swap_for_destination` says default
should depend on destination kind (Mac server → swap OFF), and the
underlying `_load()` migration honors this for saved bookmarks.

But the *new connection* dialog (the path most users hit on first run)
doesn't surface destination_kind anywhere — there's no Linux/Mac picker —
and unconditionally defaults to swap=ON. A user who points the dialog at
a Mac server will silently get swap-on behavior that mangles every
Cmd-shortcut. The bookmark-edit path also resets to True before reading
profile state in `_edit_bookmark` (line 447 reads correctly, but the
constructor at line 105 has already overridden the saved value with True;
saved by-name binding works for the existing case but the dialog UX is
misleading).

**Fix:** Either add a destination_kind combobox alongside the swap
checkbox so users can pick Mac/Linux explicitly (matching
`BookmarkManager.add()` behavior at lines 176-179), OR make the swap
checkbox label explicit about Linux-only being the assumption and
default to True only when it was unset. The current label already says
"Mac client → Linux Flame" so it's *sort of* documented, but Mac-server
users have no in-dialog path to flip it without saving the bookmark
first then editing.

### WR-04: Race in client/protocol.py _connect_and_receive on broker auto-detect path

**File:** `client/protocol.py:550-556, 806`
**Issue:** When `_handle_auth` detects an inadvertent broker (auto-switch
from direct mode), it raises `_BrokerRedirect`. The outer
`_connect_to_server` catches it and recursively calls `_connect_to_server`
on the redirect target. But the original websocket context manager has
just exited (the `async with` unwinds), and `self._ws` has been set to
the now-closed ws by the inner code at line 497. Subsequent calls to
`send_input` (especially `_client_health_ping_loop` if it survives) may
write into the dead websocket. The reconnect supervisor / FSM handles
the eventual recovery, but the moment between context exit and the
recursive call is a window where `self._connected` is still True and
sends will fail with `OSError`.

**Fix:** Set `self._ws = None` immediately before raising
`_BrokerRedirect`, and gate `_client_health_ping_loop` on a per-loop
nonce so leftover pings from the old connection don't try to send.

### WR-05: VideoBlitWidget uses `setBindings([...])` then `create()` but tex/sampler may be None on first render

**File:** `client/viewer.py:267-280`
**Issue:** `_create_resources` builds the SRB once, binding `self._tex_y`
and `self._tex_uv`. If `feed_frame()` later sets these to None (line
202-203 invalidates them on size change), the SRB still holds stale
pointers until the next `_create_resources` call. The `render()` path
re-calls `initialize` (line 360) when textures are None, which calls
`_create_resources` — but the SRB block at line 266 has `if self._srb
is None` guard, so the SRB is never rebuilt with the new textures. The
new texture handles are bound, but the SRB still references the old
ones.

In practice, Qt may handle this gracefully (the QRhi backend caches
sampledTexture binding state internally and re-resolves), but on backend
swaps or after texture re-creation the previously-baked SRB may
reference invalid handles. Recommend rebuilding the SRB whenever
textures are recreated:

**Fix:** In `_create_resources`, after recreating `_tex_y`/`_tex_uv`,
reset `_srb` to None so the next pass rebuilds it:

```python
if self._tex_y is None:
    self._tex_y = rhi.newTexture(...)
    self._tex_y.create()
    self._srb = None  # force rebind
if self._tex_uv is None:
    self._tex_uv = rhi.newTexture(...)
    self._tex_uv.create()
    self._srb = None
```

### WR-06: server/mac_screen_capture.py reinit() doesn't honor want_10bit on re-setup

**File:** `server/mac_screen_capture.py:500-510`
**Issue:** `reinit()` and `switch_monitor()` both tear down the SCK stream
and call `_start_stream()` again. `_start_stream()` reads
`self._want_10bit` correctly (line 381), so the reconfigure preserves
the 10-bit flag. Good. **However**, the runtime fallback path on
`hdrLocalDisplay_set_failed` (line 396-402) silently continues with the
P010 pixel format but no HDR dynamic-range tag, which per the docstring
at lines 379-381 produces "tone-map to 8-bit even when the surface
format is P010" on XDR displays. The warning is logged but the
capability state isn't downgraded (no `runtime_capability_state` analogue
on this class — only the Linux ScreenCapture has one).

For consistency with the Linux side (D-03 / VIDEO-09 honest capability
reporting), the Mac capture should expose a similar `runtime_capability_state`
property and flip it to "degraded" when HDR tag setting fails.

**Fix:** Add a `_hdr_set_ok: bool = False` field, set True after a
successful `setCaptureDynamicRange_` call, and add:

```python
@property
def runtime_capability_state(self) -> str:
    if not self._want_10bit:
        return "not_supported"
    if not self._hdr_set_ok:
        return "degraded"
    return "confirmed"
```

This closes the symmetry gap with `server/screen_capture.py:455-472`.

### WR-07: NvFBC backend rejects 10-bit framing as "absurd" if YUV420P10LE size > 256MB

**File:** `server/nvfbc/nvfbc_backend.py:251-253`
**Issue:** The reader-loop sanity check (`sz > 256 * 1024 * 1024`) is fine
for 8K BGRA but is also the absolute cap. A YUV420P10LE buffer at
8K (7680 × 4320) is roughly 8K × 8K × 1.5 × 2 ≈ 200 MB, which sneaks
under the cap. But future 16K or oversampled-capture paths would hit
the cap and the reader would silently exit (`break`) — leaving the
helper alive with nothing reading its stdout, producing the worst
possible failure mode (capture pipeline frozen, no logs after the
single error line).

**Fix:** Either compute the cap from screen geometry × bytes/pixel × 2
safety margin, OR bump the absolute cap and add a structured log + a
recovery path that restarts the helper instead of silently breaking out.

### WR-08: client/viewer.py keyPressEvent paste-detection conflates Cmd and Ctrl on bit-2

**File:** `client/viewer.py:891`
**Issue:** The paste shortcut detection checks `modifiers & 2` to mean
"Ctrl pressed". Per `_qt_modifiers_to_int` (line 1009-1029), bit 2 on
darwin maps from EITHER `Qt.ControlModifier` OR `Qt.MetaModifier` (the
swap that makes Cmd → Ctrl on Linux). So Cmd+V on Mac correctly fires
paste_requested. **Good.**

But `modifiers & 2` is a magic number — the constant lives in
`_qt_modifiers_to_int` and isn't exported. If the bit assignment ever
changes (D-14 already uses bit 0x10 for keypad, and future modifier
bits could shuffle), this breaks silently. **Fix:** define the constant
once in `common/keymap.py` (e.g., `MODIFIER_BIT_CTRL = 2`) and import
both here and in the encoder.

## Info

### IN-01: Unused imports

**File:** `client/protocol.py:24-32`
**Issue:** `HealthPong`, `JPEG_HEADER_SIZE`, `decode_jpeg_header` are
imported but the `_handle_binary` path that consumes JPEG frames is
exercised, but `HealthPong` itself is never constructed in this module.
Cleanup pass after Phase 2.

**File:** `client/session.py:9-13`
**Issue:** `json`, `time`, `asdict` imported but unused (line 9, 11, 12).
`HealthPong, parse_message` imported but unused (line 28).

**Fix:** Run `ruff check --fix` — these are F401 violations that the
CI lint job at `.github/workflows/ci.yml:30` should already catch.
Worth investigating why they slipped through.

### IN-02: PyAV decoder error count uses getattr(self, "_decode_error_count", 0) instead of __init__ field

**File:** `client/video_decoder.py:282, 288, 372, 378`
**Issue:** `_decode_error_count` is created on first error via
`getattr(self, "_decode_error_count", 0) + 1`. It would be cleaner to
initialize it in `__init__` alongside `_decode_times`. Lazy-init
patterns can mask attribute typos and make state harder to introspect
in debuggers.

**Fix:** Add `self._decode_error_count: int = 0` to `__init__`.

### IN-03: BookmarkManager get_password swallows decryption failures silently

**File:** `client/bookmarks.py:75-85`
**Issue:** `_decrypt_password` catches `Exception` and returns empty
string. A user whose machine-id changed (motherboard swap, fresh
install, restored from backup) will silently get blank passwords for
every bookmark with no warning. The connection dialog will then prompt
for password re-entry, but the user has no way to know *why* —
"network error?" "broker down?" — when the actual cause is the
machine-key derivation drifted.

**Fix:** Log a structured warning on decryption failure with a hint:

```python
except Exception as e:
    logger.warning(
        "bookmarks.decrypt_failed: %s — machine identifiers may have "
        "changed since password was saved", e
    )
    return ""
```

### IN-04: nvfbc_capture.c push-model dwSamplingRateMs ignored when push=1

**File:** `server/nvfbc/nvfbc_capture.c:293, 364`
**Issue:** The C helper sets `cs.dwSamplingRateMs = 1000/fps` even when
`push_model=1`. NvFBC documents that `dwSamplingRateMs` is ignored when
the push model is enabled (the driver pushes new frames only on
`bIsNewFrame`). The line works because it's silently ignored, but it's
a semantic noise that future maintainers will misread as "this is the
fps cap".

**Fix:** Only set `dwSamplingRateMs` when `push_model=0`, and add a
comment explaining why it's omitted otherwise.

### IN-05: server/screen_capture.py global lambda for missing helper sentinel

**File:** `server/screen_capture.py:41`
**Issue:** `_nvfbc_helper_available = lambda: False  # noqa: E731` is
a stylistic violation suppressed inline. A nested `def` would be one
line and avoid the noqa. Minor — keep if you prefer the lambda for
brevity, but the noqa exists because ruff already flagged it.

### IN-06: TextCommitMsg empty-string handling differs between platforms

**File:** `server/input_injector.py:495-496`, `server/mac_input_injector.py:407-408`
**Issue:** Both injectors guard against empty text with `if not text: return`,
which is correct. But `client/protocol.py:354-362` has its own comment
saying "Empty strings are intentionally allowed here — the viewer
guards the upstream emit". The wire path will happily ship empty
text_commit messages (even though the viewer at line 990-992 already
filters empty), creating a path where a bug in the viewer (or a
malicious client) could spam empty wire messages. The server eats them
silently, which is correct, but the client-side `send_text_commit` could
also short-circuit for defense-in-depth and to avoid burning a wire
slot. Tiny improvement.

---

## Notes on hard-constraint compliance

**v1 scope:** No Windows / Linux client code introduced. ✓

**Color (10-bit end-to-end):** Strong defense in depth — the BT.709 shader
explicitly excludes HDR matrices with a CI-grep test
(`client/shaders/video_blit.frag:8-13`), `decode_frame_planes` raises on
silent fallback (`client/video_decoder.py:213-258`), the HEVC profile
selection in `_nvenc_args` properly threads main10/main422-10
(`server/video_encoder.py:457-466`). **The CR-01 bug breaks this end-to-end
on Mac server** because the encoder receives empty buffers — but the wire
contract still claims Main10. Fix CR-01 to preserve the constraint.

**Input fidelity:** D-11 release-all, D-13 X-repeat-off, D-14 lock-state
auto-correct, D-15 IME passthrough, D-19 pen-proximity FSM, all idempotent
where required, all guarded with broad except-and-log so a malformed wire
message can't kill the session loop. The Mac pen pressure downgrade is
documented in `docs/release.md:8-72` per the D-07 FAIL outcome.

**Latency target:** No new blocking I/O on the hot path. The QRhi widget
update path latches under the GIL and renders on the GUI thread — Qt's
update() coalescing keeps the per-frame cost bounded. `MacVideoEncoder`'s
output callback bridges to asyncio via `call_soon_threadsafe` (correct
pattern). CI synthetic gate is preserved at p99 < 25 ms in
`.github/workflows/ci.yml:107-127`.

**Linux OS target (Rocky 9 only):** No Wayland code introduced. ✓

**Transport:** No QUIC/TLS-WS regressions. The double-gated TLS dev
escape hatch (Phase 1 SEC-01) is untouched. ✓

**License:** No new dependencies with incompatible licenses (PyObjC
frameworks, scipy, matplotlib, numpy are all permissive). ✓

---

_Reviewed: 2026-04-19_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
