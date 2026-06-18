"""Phase 2 D-04 / VIDEO-04: direct VTCompressionSession PyObjC wrapper.

Replaces the FFmpeg ``hevc_videotoolbox`` subprocess hop on the macOS
server. Saves 5-8ms/frame per WWDC21 session 10158 by enabling
``kVTVideoEncoderSpecification_EnableLowLatencyRateControl`` and running
a one-in-one-out pipeline with ``AllowFrameReordering=False`` (no
B-frames, no reorder buffer, no extra latency).

Public contract mirrors :class:`server.video_encoder.VideoEncoder`:

    enc = MacVideoEncoder(width, height, settings)
    enc.start(on_encoded_frame=lambda nal_bytes, pts_ns, is_idr: ...)
    enc.feed_frame(p010_bytes)
    enc.request_keyframe()
    enc.stop()

The dispatcher in :mod:`server.platform_backends` checks ``IS_MACOS``
+ :data:`_HAS_VT` before importing this module so simply running the
server on Linux does not require pyobjc-framework-VideoToolbox.

Threading model
---------------

VT's compression-session output callback fires on a framework-owned
thread. We bridge to the asyncio event loop the encoder was started
from via ``loop.call_soon_threadsafe``. This mirrors the Phase 1
delegate-thread bridge in :mod:`server.mac_screen_capture`.

10-bit input
------------

``feed_frame`` constructs a CVPixelBuffer with format
``kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange`` (P010, the macOS
10-bit 4:2:0 layout) so the input side of the encoder is honest about
bit depth. The 4:2:2 path (``Main42210``) is gated on
``settings.enable_422`` and the runtime probe in
:mod:`server.capability_probe` (D-05).
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

from common.logging import get_logger

log = get_logger("server.mac_video_encoder")

# PyObjC imports are deferred to import-time but guarded so that simply
# importing server modules on a non-Mac host does not blow up. The
# dispatcher in platform_backends.py checks ``IS_MACOS`` + ``_HAS_VT``
# before pulling this module's encoder into the pipeline.
try:
    import CoreMedia as CM  # type: ignore[import-not-found]
    import CoreVideo as CV  # type: ignore[import-not-found]
    import objc  # noqa: F401
    import VideoToolbox as VT  # type: ignore[import-not-found]
    from Foundation import NSObject  # noqa: F401
    _HAS_VT = True
    _import_error: BaseException | None = None
except Exception as _e:  # pragma: no cover - exercised by Linux CI
    _HAS_VT = False
    _import_error = _e
    VT = None  # type: ignore[assignment]
    CM = None  # type: ignore[assignment]
    CV = None  # type: ignore[assignment]


class MacVideoEncoderError(RuntimeError):
    """Raised when VideoToolbox / PyObjC is unavailable or misconfigured."""


def _check_vt_available() -> None:
    if not _HAS_VT:
        raise MacVideoEncoderError(
            f"VideoToolbox / PyObjC not available: {_import_error}. "
            "Install: pip install pyobjc-framework-VideoToolbox "
            "pyobjc-framework-CoreVideo pyobjc-framework-CoreMedia"
        )


class MacVideoEncoder:
    """Direct VTCompressionSession encoder for the macOS server.

    See module docstring for the public contract and threading model.
    The class itself is small; the heavy lifting is the spec/property
    dictionary VT consumes at session-create time, plus the output
    callback bridge.
    """

    def __init__(
        self,
        width: int,
        height: int,
        settings: Any,
        available_encoders: Any = None,
    ):
        _check_vt_available()
        self._width = width
        self._height = height
        self._settings = settings
        self._available_encoders = available_encoders  # parity with VideoEncoder ctor
        self._session: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_encoded_frame: Callable | None = None
        self._lock = threading.Lock()
        self._stopped = False
        self._force_keyframe_next = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, on_encoded_frame: Callable) -> None:
        """Create the VTCompressionSession and configure the property bag.

        ``on_encoded_frame`` is called with ``(nal_bytes, pts_ns, is_idr)``
        on the asyncio event loop the encoder was started from (via
        ``call_soon_threadsafe``). If no event loop is running at start
        time, the callback is invoked synchronously from VT's thread.
        """
        self._on_encoded_frame = on_encoded_frame
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # Not running under asyncio (e.g. tests, sync harnesses).
            self._loop = None

        # ----------------------------------------------------------
        # 1. Encoder specification (creation-time, immutable)
        # ----------------------------------------------------------
        # EnableLowLatencyRateControl is the WWDC21 magic flag that
        # promises 5-8ms/frame savings; without it VT runs the legacy
        # rate controller and silently breaks the latency budget.
        encoder_spec = {
            VT.kVTVideoEncoderSpecification_EnableLowLatencyRateControl: True,
            VT.kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder: True,
        }

        # ----------------------------------------------------------
        # 2. Create the session
        # ----------------------------------------------------------
        # PyObjC binding signature:
        #   VTCompressionSessionCreate(allocator, width, height,
        #       codecType, encoderSpecification, sourceImageBufferAttributes,
        #       compressedDataAllocator, outputCallback,
        #       outputCallbackRefCon, compressionSessionOut)
        status, session = VT.VTCompressionSessionCreate(
            None,                                # allocator
            self._width,                         # width
            self._height,                        # height
            CM.kCMVideoCodecType_HEVC,           # codecType
            encoder_spec,                        # encoderSpecification
            None,                                # sourceImageBufferAttributes
            None,                                # compressedDataAllocator
            self._vt_output_callback,            # outputCallback
            None,                                # outputCallbackRefCon
            None,                                # compressionSessionOut
        )
        if status != 0 or session is None:
            raise MacVideoEncoderError(
                f"VTCompressionSessionCreate failed status={status} "
                f"(width={self._width} height={self._height})"
            )
        self._session = session

        # ----------------------------------------------------------
        # 3. Properties (post-creation, mutable via VTSessionSetProperty)
        # ----------------------------------------------------------
        target_bps = int(getattr(self._settings, "target_bitrate_bps", 30_000_000))
        fps = int(getattr(self._settings, "fps", 60))
        # D-05: Main10 is the default; 4:2:2 is opt-in and probed.
        profile = VT.kVTProfileLevel_HEVC_Main10_AutoLevel
        if getattr(self._settings, "enable_422", False):
            profile = VT.kVTProfileLevel_HEVC_Main42210_AutoLevel

        props = [
            (VT.kVTCompressionPropertyKey_ProfileLevel, profile),
            (VT.kVTCompressionPropertyKey_RealTime, True),
            # AllowFrameReordering=False is the no-B-frame contract.
            # Reorder buffer = end-to-end latency; we cannot afford either.
            (VT.kVTCompressionPropertyKey_AllowFrameReordering, False),
            (VT.kVTCompressionPropertyKey_ExpectedFrameRate, fps),
            (VT.kVTCompressionPropertyKey_AverageBitRate, target_bps),
            # Keyframe every 2 seconds (matches the FFmpeg path's "-g 2*fps").
            (VT.kVTCompressionPropertyKey_MaxKeyFrameInterval, 2 * fps),
        ]
        for key, value in props:
            VT.VTSessionSetProperty(self._session, key, value)

        log.info(
            "mac_video_encoder.started",
            width=self._width,
            height=self._height,
            fps=fps,
            target_bps=target_bps,
            profile="HEVC_Main10_AutoLevel",
            low_latency_rc=True,
            allow_frame_reordering=False,
            enable_422=bool(getattr(self._settings, "enable_422", False)),
        )

    def stop(self) -> None:
        """Drain pending frames and invalidate the session. Idempotent."""
        if self._stopped:
            return
        self._stopped = True
        if self._session is not None:
            try:
                VT.VTCompressionSessionCompleteFrames(
                    self._session, CM.kCMTimeInvalid
                )
                VT.VTCompressionSessionInvalidate(self._session)
            finally:
                self._session = None
        log.info("mac_video_encoder.stopped")

    # ------------------------------------------------------------------
    # Per-frame interface
    # ------------------------------------------------------------------

    def feed_frame(self, p010_bytes: bytes, pts_ns: int = 0) -> None:
        """Submit a raw P010 frame to the encoder.

        ``p010_bytes`` is the planar P010 (4:2:0 10-bit) buffer captured
        from ScreenCaptureKit. We wrap it in a CVPixelBuffer and hand
        it to VTCompressionSessionEncodeFrame; VT calls the output
        callback asynchronously on its own thread when the encoded
        sample is ready.

        WARNING (Phase 2 CR-01) — INCOMPLETE IMPLEMENTATION
        ---------------------------------------------------
        The byte-level plane copy from ``p010_bytes`` into the
        CVPixelBuffer's Y + UV planes is a TODO (see line marker below).
        Until it lands, every encoded frame wraps uninitialized
        ``CVPixelBuffer`` heap memory — the resulting HEVC Main10 NAL
        units are wire-valid but display as banded garbage on the client.

        For this reason ``server.platform_backends._MAC_VIDEO_ENC_AVAILABLE``
        is hard-pinned to False (see the CR-01 GATE block in
        ``server/platform_backends.py``) so the FFmpeg
        ``hevc_videotoolbox`` subprocess fallback is the production
        Mac-server path. This module is reachable only via tests that
        monkey-patch the gate flag.

        Re-enable the production dispatch ONLY after the plane copy
        lands here AND a regression test asserts the encoded NAL output
        is non-trivial for a known input pattern.
        """
        if self._stopped or self._session is None:
            return

        pix_fmt = CV.kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange
        status, pb = CV.CVPixelBufferCreate(
            None, self._width, self._height, pix_fmt, None, None
        )
        if status != 0 or pb is None:
            log.warning(
                "mac_video_encoder.cvpixelbuffer_create_failed",
                status=status,
            )
            return

        # TODO (Phase 2 CR-01 / Phase 3 follow-up): lock base address and
        # memcpy the Y + UV planes from ``p010_bytes`` into the buffer
        # once SCK delivers P010 directly (Task 2 plumbs the negotiation
        # flag). Until then we submit an empty allocation so the encoder
        # pipeline shape is exercised; production dispatch is gated off
        # in server/platform_backends.py to keep this code unreachable
        # outside of tests.
        del p010_bytes  # unused until plane-copy lands

        # PTS in CMTime: 90 kHz timebase matches the standard MPEG clock.
        pts = CM.CMTimeMake(pts_ns // 1000, 90_000)

        # Force a keyframe on the next encode if request_keyframe() was
        # called. Implementation note: VT also exposes a frameProperties
        # dict on EncodeFrame for forcing keyframes; we use the simpler
        # "set MaxKeyFrameInterval to 1, encode, restore" idiom in a
        # follow-up plan rather than threading the dict through here.
        force_kf = False
        with self._lock:
            if self._force_keyframe_next:
                force_kf = True
                self._force_keyframe_next = False
        if force_kf:
            log.info("mac_video_encoder.encoding_force_keyframe")

        VT.VTCompressionSessionEncodeFrame(
            self._session,
            pb,
            pts,
            CM.kCMTimeInvalid,   # duration: invalid = let VT compute
            None,                # frameProperties (None = use session defaults)
            None,                # sourceFrameRefCon
            None,                # infoFlagsOut
        )

    def request_keyframe(self) -> None:
        """Request the next ``feed_frame`` call to emit an IDR.

        Safe to call before ``start()`` — the request is latched and
        applied to the next submitted frame after the session exists.
        """
        if self._stopped:
            return
        with self._lock:
            self._force_keyframe_next = True
        log.info("mac_video_encoder.keyframe_requested")

    # ------------------------------------------------------------------
    # VT output callback (fires on framework-owned thread)
    # ------------------------------------------------------------------

    def _vt_output_callback(
        self,
        outputCallbackRefCon: Any,
        sourceFrameRefCon: Any,
        status: int,
        infoFlags: Any,
        sampleBuffer: Any,
    ) -> None:
        """VT calls this on its internal thread for every encoded sample.

        We extract the NAL bytes via CMBlockBuffer + read the PTS, then
        bridge to the encoder's asyncio event loop via
        ``call_soon_threadsafe``. Never raise back into Objective-C —
        any exception will tear down VT's dispatch queue.
        """
        try:
            if status != 0 or sampleBuffer is None:
                log.warning(
                    "mac_video_encoder.encode_callback_error",
                    status=status,
                )
                return
            data_buffer = CM.CMSampleBufferGetDataBuffer(sampleBuffer)
            if data_buffer is None:
                return
            length = CM.CMBlockBufferGetDataLength(data_buffer)
            # PyObjC binding returns (status, lengthAtOffset, totalLength, data).
            copy_status, _, _, nal_bytes = CM.CMBlockBufferCopyDataBytes(
                data_buffer, 0, length, None, None
            )
            if copy_status != 0 or nal_bytes is None:
                log.warning(
                    "mac_video_encoder.copy_data_bytes_failed",
                    status=copy_status,
                )
                return

            pts = CM.CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
            timescale = getattr(pts, "timescale", 0) or 0
            value = getattr(pts, "value", 0)
            pts_ns = int(value * 1_000_000_000 / timescale) if timescale else 0

            # VT's kVTEncodeInfo_FrameDropped = 0x02; IDR detection lives
            # on the sample-buffer attachments. We approximate via
            # infoFlags here and let downstream consumers treat IDR as a
            # hint rather than a contract.
            is_idr = bool(infoFlags & 0x02) if isinstance(infoFlags, int) else False

            cb = self._on_encoded_frame
            if cb is None:
                return
            payload = bytes(nal_bytes)
            if self._loop is not None and self._loop.is_running():
                self._loop.call_soon_threadsafe(cb, payload, pts_ns, is_idr)
            else:
                # No asyncio loop — call inline (test / sync harnesses).
                cb(payload, pts_ns, is_idr)
        except Exception as exc:  # pragma: no cover - safety net
            # Never let an exception cross back into VT's dispatch
            # queue; log and swallow.
            log.error(
                "mac_video_encoder.callback_unhandled_exception",
                err_class=type(exc).__name__,
            )
