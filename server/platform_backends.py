"""
Platform backend dispatch for the Teraguchi server.

The server has three backend contracts that differ by OS:

    * screen capture   (Linux: mss/NvFBC/XDamage, macOS: ScreenCaptureKit)
    * input injection  (Linux: uinput/XTest,      macOS: CoreGraphics)
    * clipboard sync   (Linux: xclip/xsel,        macOS: NSPasteboard)

Rather than scattering ``if sys.platform == "darwin"`` checks through
``server/main.py``, we keep the dispatch here and re-export the correct
classes for the current platform. ``server/main.py`` imports the public
names from this module and the rest of the session runtime is
platform-agnostic.

On an unsupported platform we re-export the Linux classes so static
analysis still works; runtime behavior will of course be broken.
"""

import logging
import sys

logger = logging.getLogger(__name__)

IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# ----------------------------------------------------------------------
# Screen capture
# ----------------------------------------------------------------------

if IS_MACOS:
    from server.mac_screen_capture import MacScreenCapture as ScreenCapture  # noqa: F401
    logger.info("platform_backends: using MacScreenCapture (ScreenCaptureKit)")
else:
    from server.screen_capture import ScreenCapture  # noqa: F401

# ----------------------------------------------------------------------
# Input injection
# ----------------------------------------------------------------------

if IS_MACOS:
    from server.mac_input_injector import MacInputInjector as InputInjector  # noqa: F401
    # On macOS there is no Xvfb / XTest analogue — the virtual-display
    # injector is stubbed to the same class as the physical one so the
    # session runtime's "virtual vs physical" branch still compiles.
    XTestInputInjector = InputInjector  # type: ignore
    logger.info("platform_backends: using MacInputInjector (CoreGraphics)")
else:
    from server.input_injector import InputInjector  # noqa: F401
    from server.xtest_injector import XTestInputInjector  # noqa: F401

# ----------------------------------------------------------------------
# Video encode (Phase 2 D-04 / VIDEO-04)
# ----------------------------------------------------------------------
#
# When the macOS server has pyobjc-framework-VideoToolbox installed,
# the Mac path will eventually prefer the direct VTCompressionSession
# wrapper in server.mac_video_encoder (saves 5-8ms/frame per WWDC21
# session 10158 vs. the FFmpeg subprocess hop). Until that lands we fall
# back to the Phase 1 FFmpeg ``hevc_videotoolbox`` path documented in
# server/video_encoder.py.
#
# === Phase 2 CR-01 GATE — DISABLED IN PRODUCTION DISPATCH ===
#
# ``server.mac_video_encoder.MacVideoEncoder.feed_frame`` currently
# discards the captured P010 plane bytes (``del p010_bytes``) and submits
# an empty ``CVPixelBuffer`` to ``VTCompressionSessionEncodeFrame``. The
# encoded NAL units are valid HEVC Main10 wrapping uninitialized heap
# memory — the client decodes valid P010 and displays banded garbage
# while the capability badge advertises "10-bit confirmed". See
# ``.planning/phases/02-input-color-fidelity/deferred-items.md`` item 7
# for the deferred plane-copy work.
#
# We therefore force this flag to False even on Macs where the PyObjC
# import succeeds, so ``server.video_encoder`` deterministically falls
# back to the FFmpeg ``hevc_videotoolbox`` subprocess path (which IS
# correct end-to-end). Re-enable by removing the override below ONCE the
# CVPixelBufferLockBaseAddress + ctypes.memmove plane copy lands in
# server/mac_video_encoder.py::feed_frame AND a regression test asserts
# the encoded NAL output is non-trivial for a known input pattern.
#
# Tests that need to exercise the True branch monkey-patch
# ``server.platform_backends._MAC_VIDEO_ENC_AVAILABLE`` directly (see
# ``tests/server/test_platform_backends_video.py``); the override below
# does not interfere with that.
#
# server.video_encoder.VideoEncoder.start() reads this flag once at
# session creation; do not flip it at runtime.
_MAC_VIDEO_ENC_AVAILABLE: bool = False
if IS_MACOS:
    try:
        # Probe so we still log honestly whether VT itself would have
        # been importable. We do NOT propagate the result into the
        # production-dispatch flag — see the CR-01 GATE comment above.
        from server.mac_video_encoder import _HAS_VT as _has_vt_probe  # noqa: F401
        if _has_vt_probe:
            logger.warning(
                "platform_backends.mac_video_encoder_disabled_until_plane_copy "
                "— PyObjC VideoToolbox is importable but the direct-VT "
                "feed_frame path discards P010 plane bytes (Phase 2 CR-01); "
                "falling back to FFmpeg hevc_videotoolbox subprocess path"
            )
        else:
            logger.warning(
                "platform_backends.mac_video_encoder_pyobjc_missing — "
                "falling back to FFmpeg hevc_videotoolbox subprocess path"
            )
    except Exception as _e:
        logger.warning(
            "platform_backends.mac_video_encoder_unavailable: %s", _e
        )
    # Production override: stay False until the plane copy lands.
    _MAC_VIDEO_ENC_AVAILABLE = False

# ----------------------------------------------------------------------
# Clipboard
# ----------------------------------------------------------------------

if IS_MACOS:
    from server.mac_clipboard import MacClipboardSync as ClipboardSync  # noqa: F401
    logger.info("platform_backends: using MacClipboardSync (NSPasteboard)")
else:
    from server.clipboard import ClipboardSync  # noqa: F401
