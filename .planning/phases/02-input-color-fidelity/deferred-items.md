# Phase 2 — Deferred Items (Out-of-Scope Findings)

Issues discovered during plan execution that are out-of-scope for the
current plan and intentionally NOT fixed. Fold into a future plan or
phase-wide cleanup pass.

## From 02-02 (message-wiring)

**1. Pre-existing ruff errors in `common/messages.py`** — noted during
02-02 ruff verification. Not introduced by 02-02. Scope boundary per
executor deviation rules.

- `common/messages.py:29` I001 — import block un-sorted (needs `ruff --fix`)
- `common/messages.py:36` F401 — `typing.Optional` unused
- `common/messages.py:36` F401 — `typing.List` unused
- `common/messages.py:93` E501 — `decode_video_header` return line 112 chars (legacy Phase 1 code)

Fix in a follow-up `style(common)` commit — trivial, no behavioral risk.
Phase 1 mypy is clean; ruff was not previously run on the whole file
with the strict config.

## From 02-04 (capability-probe)

**2. Pre-existing mypy error in `common/logging.py:343`** — noted during
02-04 mypy verification on `server/capability_probe.py`. The StageTimer
`__exit__` is typed `-> bool` but always returns `False` — mypy flags
this as `[exit-return]` per PEP 479 (literal-False should be annotated
as `Literal[False]` or `None`). Not introduced by 02-04. Scope boundary;
`server/capability_probe.py` itself is clean.

Fix in a follow-up `fix(common/logging)` commit — change `-> bool` to
`-> None` (since the function only returns `False`) and remove the
literal return. Trivial, no behavioral risk.

## From 02-10 (mac-pen-injector + PenFSM)

**3. Pre-existing ruff errors in `client/viewer.py`** — noted during
02-10 ruff verification on the D-19 viewer changes. Not introduced by
02-10:

- `client/viewer.py:451-452` UP045 — `Optional[QImage]` / `Optional[QPixmap]` should be `X | None`
- `client/viewer.py:659` F841 — local `rh` assigned but never used
- ~9 additional UP045 / I001 instances in the same file

Fix in a follow-up `style(client/viewer)` commit — `ruff --fix` handles
all of them. Phase 1 set the precedent (UP045 / F841 are not in the
strict-fail set). Trivial, no behavioral risk.

**4. Pre-existing test collection error in
`tests/client/test_health_display.py`** — top-level `from PySide6.QtCore
import ...` lacks a `pytest.importorskip("PySide6")` guard. Fails
collection on hosts without PySide6 installed (the executor sandbox).
Not introduced by 02-10; the file was added in an earlier wave.

Fix in a follow-up `test(client/test_health_display)` commit — add the
skip guard at top of module, mirroring the pattern in
`test_viewer_modifier_triggers.py`. Trivial.

**5. Pre-existing PAM auth test failures (`tests/server/test_pam_auth.py`)**
— 1 fail + 3 errors with `AttributeError` on the python-pam mock. Not
introduced by 02-10; reproducible at HEAD with 02-10 changes stashed.

Fix in a follow-up `test(server/test_pam_auth)` commit — likely a
python-pam version drift. Out of scope for Phase 2 (Phase 1 territory).

**6. Pre-existing F401 in `server/mac_input_injector.py`** — Phase 1
era code; 5 unused Quartz imports flagged by ruff. Not introduced by
02-10's docstring + WARNING-log update.

Fix in a follow-up `style(server/mac_input_injector)` commit.

## From 02 code review (2026-04-19)

**7. CR-01 — `server/mac_video_encoder.py::feed_frame` P010 plane copy
not implemented.** The direct VTCompressionSession path constructs an
empty `CVPixelBuffer` via `CV.CVPixelBufferCreate(...)` and explicitly
discards the captured P010 plane bytes (`del p010_bytes` with a TODO at
the marker). VT then encodes uninitialized heap memory; the resulting
HEVC Main10 NAL units are wire-valid but display as banded garbage on
the client.

**Interim mitigation (landed in REVIEW-FIX iteration 1):**
`server/platform_backends.py::_MAC_VIDEO_ENC_AVAILABLE` is hard-pinned
to `False` (see the CR-01 GATE comment block in that file) so the
FFmpeg `hevc_videotoolbox` subprocess fallback is the production
Mac-server video path. The direct-VT module remains tested and
import-clean — only its production dispatch is gated off.

**Deferred work to re-enable the direct-VT path:**

1. Implement the plane copy in `feed_frame`:
   - `CV.CVPixelBufferLockBaseAddress(pb, 0)`
   - Compute Y plane size = `width * height * 2` (P010 = 2 bytes/sample)
   - Compute UV plane size = `(width // 2) * (height // 2) * 4` (half-res RG16)
   - `ctypes.memmove` Y bytes into `CVPixelBufferGetBaseAddressOfPlane(pb, 0)`
   - `ctypes.memmove` UV bytes into `CVPixelBufferGetBaseAddressOfPlane(pb, 1)`
   - `CV.CVPixelBufferUnlockBaseAddress(pb, 0)` in a `finally:`
   - Length-validate `len(p010_bytes) >= y_size + uv_size` and
     warn-and-return on short buffer (do NOT memmove past end).

2. Add a regression test that feeds a known-pattern frame (e.g. all
   0x3FFF luma) and asserts the encoded NAL output is non-trivial
   (not just SPS/PPS + empty slice). Decode + spot-check a few luma
   samples to prove the bytes round-trip.

3. Remove the `_MAC_VIDEO_ENC_AVAILABLE = False` override at the bottom
   of the `if IS_MACOS:` block in `server/platform_backends.py`. Restore
   the prior probe-driven assignment.

4. Update the `feed_frame` docstring's "INCOMPLETE IMPLEMENTATION"
   warning + remove the "production dispatch is gated off" sentence
   from the TODO comment.

5. Re-run the VIDEO-01/VIDEO-02 10-bit fixture test (from the Phase 2
   ten-bit pipeline smoke test) on a real Mac with PyObjC VideoToolbox
   installed.

Hold for: a Mac dev workstation with PyObjC + a real HEVC Main10
fixture decoder available for the round-trip assertion. PyObjC + ctypes
lifetime details are subtle — the safer path until then is the FFmpeg
fallback, which is correct end-to-end.
