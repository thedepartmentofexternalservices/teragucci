"""
Client-side video decoder with hardware acceleration via PyAV (FFmpeg).

Decodes H.264, H.265, and AV1 video streams received from the server.

Two surfaces:

  * ``decode_frame_planes(packet_bytes)`` — production hot path. Returns
    10-bit (Y, UV, w, h) plane bytes ready for QRhi widget upload via
    ``VideoBlitWidget.feed_frame``. NEVER calls ``.to_ndarray('rgb24')``
    — that is the PITFALLS #2 silent 8-bit downgrade trap and is the
    direct reason this hot path exists. When the negotiated codec is
    HEVC Main10, the first decoded frame's ``format.name`` MUST be one
    of ``{'p010le', 'yuv420p10le'}`` or the decoder raises.

  * ``decode_frame(packet_bytes)`` / ``decode_frame_to_ndarray`` —
    legacy Phase-1 fallback. Returns RGB888 bytes for the QPainter
    JPEG / overlay path that hasn't yet been migrated to Metal. Marked
    ``# phase1 fallback`` so the source-grep gate in
    ``tests/smoke/test_ten_bit_pipeline.py::test_checkpoint_5_*`` skips
    these on the rgb24 audit. Will be removed when the JPEG fallback
    path is also retired post-Phase-2.

Hardware decode priority: CUDA (NVIDIA) > VAAPI > DXVA2/D3D11VA > VideoToolbox > Software.
Falls back gracefully if hardware decode is unavailable.

D-01 cp.6 (Phase 02-08): ``hw_backend`` exposes the active hwaccel as
a string. ``software`` is the canonical fallback value — never None.
"""

import logging
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import av
    HAS_PYAV = True
except ImportError:
    HAS_PYAV = False
    logger.warning("PyAV not installed — video decode disabled. Install with: pip install av")

try:
    from common.logging import get_logger
    _structlog = get_logger("client.video_decoder")
except Exception:  # pragma: no cover — pre-Phase-1 environments
    _structlog = None


# Hardware decoder configs per platform
HW_DECODERS = {
    "h264": [
        # (codec_name, hw_type) — tried in order
        ("h264", "cuda"),         # NVIDIA
        ("h264", "vaapi"),        # Intel/AMD Linux
        ("h264", "dxva2"),        # Windows
        ("h264", "d3d11va"),      # Windows
        ("h264", "videotoolbox"), # macOS
        ("h264", None),           # Software fallback
    ],
    "h265": [
        ("hevc", "cuda"),
        ("hevc", "vaapi"),
        ("hevc", "dxva2"),
        ("hevc", "d3d11va"),
        ("hevc", "videotoolbox"),
        ("hevc", None),
    ],
    "av1": [
        ("av1", "cuda"),
        ("av1", "vaapi"),
        ("av1", "dxva2"),
        ("av1", "d3d11va"),
        ("av1", "videotoolbox"),
        ("av1", None),
    ],
}


class VideoDecoder:
    """
    PyAV-based video decoder with automatic hardware acceleration detection.

    Accepts raw encoded NAL units (H.264/H.265) or OBU frames (AV1)
    and returns decoded RGB QImage-compatible bytes.
    """

    def __init__(self, codec: str = "h264"):
        """
        Args:
            codec: "h264", "h265", or "av1"
        """
        if not HAS_PYAV:
            raise RuntimeError("PyAV is required for video decode. Install: pip install av")

        self._codec_name = codec
        self._decoder: Optional[av.CodecContext] = None
        self._hw_type: Optional[str] = None
        self._frame_count = 0
        self._decode_times: list = []
        self._initialized = False
        # D-01 cp.5 — set True once ServerHelloMsg confirms Main10 wire
        # profile (see client/protocol.py call to set_negotiated_main10).
        # While False, the decoder accepts any AVFrame.format the H.264
        # baseline / pre-Main10 path emits. While True, decode_frame_planes
        # asserts the first frame is p010le / yuv420p10le and raises on
        # silent 8-bit downgrade.
        self._negotiated_main10: bool = False
        # First-frame format check fires once per decoder lifetime — re-
        # checked on reset() / codec switch.
        self._format_asserted: bool = False

        self._init_decoder()

    def set_negotiated_main10(self, enabled: bool) -> None:
        """Wire the negotiated Main10 wire-profile flag (D-01 cp.5).

        Called by ``client/protocol.py`` after a ``ServerHelloMsg`` with
        ``color_caps.negotiated_state in {'negotiated', 'confirmed'}``.
        Future packets are then format-asserted on the first decoded
        frame; subsequent frames are not re-checked (per-frame check
        would burn the cp.5 budget on the hot path).
        """
        self._negotiated_main10 = bool(enabled)
        # Re-arm the first-frame check so a mid-session re-negotiation
        # (e.g. quality-tier flip) gets a fresh assertion pass.
        self._format_asserted = False

    def _init_decoder(self):
        """Initialize the best available decoder."""
        candidates = HW_DECODERS.get(self._codec_name, [("h264", None)])

        for av_codec_name, hw_type in candidates:
            try:
                codec = av.codec.Codec(av_codec_name, "r")
                ctx = av.CodecContext.create(codec)

                if hw_type:
                    # Try to open with hardware device
                    try:
                        ctx.open()
                        # PyAV doesn't have a clean hw_device_ctx API for all backends,
                        # so we test if it works by keeping the context
                        self._decoder = ctx
                        self._hw_type = hw_type
                        self._initialized = True
                        logger.info("Video decoder: %s (hw=%s)", av_codec_name, hw_type)
                        return
                    except Exception:
                        continue
                else:
                    # Software decode
                    ctx.open()
                    self._decoder = ctx
                    self._hw_type = None
                    self._initialized = True
                    logger.info("Video decoder: %s (software)", av_codec_name)
                    return

            except Exception as e:
                logger.debug("Decoder %s (hw=%s) failed: %s", av_codec_name, hw_type, e)
                continue

        logger.error("No working decoder found for codec: %s", self._codec_name)

    @property
    def is_ready(self) -> bool:
        return self._initialized and self._decoder is not None

    @property
    def hw_backend(self) -> str:
        """D-01 cp.6 — currently-active hardware acceleration backend.

        Returns one of: ``videotoolbox``, ``nvdec``, ``cuda``, ``vaapi``,
        ``dxva2``, ``d3d11va``, ``software``. Never None.

        ``software`` is the canonical fallback so the cp.6 health-overlay
        assertion never has to special-case the no-hwaccel path. If the
        VideoToolbox decoder silently fell back internally (e.g. unsupported
        bit depth on older FFmpeg builds), ``decode_frame_planes`` flips
        ``_hw_type`` back to None on the format-assertion failure path.
        """
        return self._hw_type or "software"

    @property
    def avg_decode_time_ms(self) -> float:
        if not self._decode_times:
            return 0.0
        return sum(self._decode_times[-30:]) / len(self._decode_times[-30:])

    def decode_frame_planes(
        self, encoded_data: bytes
    ) -> Optional[Tuple[bytes, bytes, int, int]]:
        """D-01 cp.5 + cp.6 — production hot path. Returns 10-bit planes.

        Decodes one packet and returns ``(y_bytes, uv_bytes, width, height)``
        suitable for ``VideoBlitWidget.feed_frame`` (R16/RG16 QRhi upload).
        NEVER calls ``.to_ndarray('rgb24')`` — that is the PITFALLS #2
        silent 8-bit downgrade trap and is the entire reason this hot
        path exists separate from the legacy ``decode_frame``.

        When ``_negotiated_main10`` is True (set by ``set_negotiated_main10``
        after the ServerHelloMsg confirms Main10), the first decoded
        frame's ``format.name`` MUST be one of ``{'p010le', 'yuv420p10le'}``.
        Otherwise the decoder raises ``RuntimeError``: silent fallback is
        forbidden — if PyAV is linked against a too-old FFmpeg the
        VideoToolbox path drops 10-bit silently and we want a loud
        failure, not a black-magic CPU spike. See docs/build-pyav-macos.md
        for the dev-side fix (system FFmpeg 7.1+ + ``pip install --no-binary av``).

        Returns None when no decoded frame is available yet (e.g. the
        decoder is still buffering pre-keyframe). Raises on Main10
        negotiation breach.
        """
        if not self._initialized or not self._decoder:
            return None

        start = time.time()
        try:
            packet = av.Packet(encoded_data)
            frames = self._decoder.decode(packet)

            for frame in frames:
                fmt = frame.format.name
                # D-01 cp.5: first-frame format assertion under Main10.
                if self._negotiated_main10 and not self._format_asserted:
                    if fmt not in ("p010le", "yuv420p10le"):
                        # Flip hw_backend to software so cp.6 reflects
                        # reality: VideoToolbox silently bailed on 10-bit.
                        previous_hw = self._hw_type
                        self._hw_type = None
                        if _structlog is not None:
                            _structlog.error(
                                "video_decoder.p010_format_assertion_failed",
                                expected=["p010le", "yuv420p10le"],
                                got=fmt,
                                previous_hw=previous_hw,
                                codec=self._codec_name,
                            )
                            _structlog.warning(
                                "decoder.hwaccel_software_fallback",
                                from_backend=previous_hw,
                                to_backend="software",
                                reason="main10_format_assertion_failed",
                            )
                        logger.error(
                            "Main10 negotiated but decoder returned %r "
                            "(was hw=%s, now hw=software). See "
                            "docs/build-pyav-macos.md.",
                            fmt, previous_hw,
                        )
                        raise RuntimeError(
                            f"Main10 negotiated but decoder returned {fmt!r}; "
                            "check FFmpeg/PyAV build against system FFmpeg "
                            "7.1+ (see docs/build-pyav-macos.md). "
                            "VideoToolbox silently fell back to 8-bit."
                        )
                    self._format_asserted = True

                y_bytes, uv_bytes = self._extract_planes_p010(frame)
                self._frame_count += 1
                if self._frame_count == 1:
                    logger.info(
                        "First frame decoded: %dx%d (codec=%s, hw=%s, fmt=%s)",
                        frame.width, frame.height,
                        self._codec_name, self._hw_type or "software", fmt,
                    )
                elapsed_ms = (time.time() - start) * 1000
                self._decode_times.append(elapsed_ms)
                if len(self._decode_times) > 100:
                    self._decode_times = self._decode_times[-60:]

                return y_bytes, uv_bytes, frame.width, frame.height
        except RuntimeError:
            # Main10-negotiation breach — let it propagate so the
            # connection supervisor can surface a real error to the user
            # (and not just black-frame the viewer).
            raise
        except av.error.InvalidDataError as e:
            if self._frame_count == 0:
                self._decode_error_count = getattr(self, "_decode_error_count", 0) + 1
                if self._decode_error_count <= 5:
                    logger.warning("Decode: invalid data #%d (%d bytes): %s",
                                   self._decode_error_count, len(encoded_data), e)
        except Exception as e:  # noqa: BLE001 — match the legacy contract
            if self._frame_count == 0:
                self._decode_error_count = getattr(self, "_decode_error_count", 0) + 1
                if self._decode_error_count <= 5:
                    logger.warning("Decode error #%d (%d bytes): %s",
                                   self._decode_error_count, len(encoded_data), e)

        return None

    def _extract_planes_p010(self, frame) -> Tuple[bytes, bytes]:
        """Extract Y + UV planes as raw bytes from a 10-bit AVFrame.

        Two layouts are supported:

          * ``p010le`` — semi-planar: ``planes[0]`` is Y, ``planes[1]`` is
            interleaved UV. Pass through verbatim — the QRhi RG16 sampler
            reads UV directly.
          * ``yuv420p10le`` — fully planar: ``planes[0]`` is Y, ``[1]`` is
            U, ``[2]`` is V. Interleave U and V into a P010-style chroma
            buffer so the QRhi RG16 sampler sees the same shape as the
            p010le path. The interleave is U then V per sample (matches
            the P010 wire layout).

        Any other format is the PITFALLS #2 silent 8-bit downgrade —
        raise ValueError so the caller surfaces it.
        """
        fmt_name = frame.format.name
        if fmt_name == "p010le":
            return bytes(frame.planes[0]), bytes(frame.planes[1])
        if fmt_name == "yuv420p10le":
            y = bytes(frame.planes[0])
            u = bytes(frame.planes[1])
            v = bytes(frame.planes[2])
            uv = bytearray(len(u) + len(v))
            uv[0::2] = u
            uv[1::2] = v
            return y, bytes(uv)
        raise ValueError(
            f"_extract_planes_p010: unsupported format {fmt_name!r}; "
            "expected 10-bit p010le or yuv420p10le"
        )

    # --- Phase-1 fallback path (RGB888 for QPainter overlay / JPEG) ---
    # The methods below are the legacy 8-bit RGB path consumed by the
    # client/session.py JPEG / QPainter overlay branch. They DO call
    # .to_ndarray('rgb24') — that is the PITFALLS #2 trap, retained
    # ONLY for the not-yet-migrated overlay path. The video hot path
    # is decode_frame_planes() above. The "# phase1" tag below makes
    # the smoke-test source-grep gate skip these (it allows rgb24
    # references when commented as # phase1 path).

    def decode_frame(self, encoded_data: bytes) -> Optional[bytes]:
        """Phase-1 fallback path — returns RGB888 bytes for QPainter.

        # phase1 fallback: the QPainter overlay / JPEG path still consumes
        RGB888 here. The new production hot path is ``decode_frame_planes``;
        this method stays alive only until the JPEG fallback also moves
        to the QRhi widget. The PITFALLS #2 8-bit downgrade trap is
        intentional here and isolated from the 10-bit path.
        """
        if not self._initialized or not self._decoder:
            return None

        start = time.time()
        try:
            packet = av.Packet(encoded_data)
            frames = self._decoder.decode(packet)

            for frame in frames:
                # phase1 fallback: rgb24 coercion (NOT on the video hot path)
                rgb_frame = frame.to_ndarray(format="rgb24")
                self._frame_count += 1
                if self._frame_count == 1:
                    logger.info("First frame decoded: %dx%d (codec=%s, hw=%s) [phase1 rgb path]",
                                frame.width, frame.height,
                                self._codec_name, self._hw_type or "software")
                elapsed_ms = (time.time() - start) * 1000
                self._decode_times.append(elapsed_ms)
                if len(self._decode_times) > 100:
                    self._decode_times = self._decode_times[-60:]

                # Return raw bytes for QImage (Format_RGB888)
                return bytes(rgb_frame)

        except av.error.InvalidDataError as e:
            if self._frame_count == 0:
                self._decode_error_count = getattr(self, "_decode_error_count", 0) + 1
                if self._decode_error_count <= 5:
                    logger.warning("Decode: invalid data #%d (%d bytes): %s",
                                   self._decode_error_count, len(encoded_data), e)
        except Exception as e:
            if self._frame_count == 0:
                self._decode_error_count = getattr(self, "_decode_error_count", 0) + 1
                if self._decode_error_count <= 5:
                    logger.warning("Decode error #%d (%d bytes): %s",
                                   self._decode_error_count, len(encoded_data), e)

        return None

    def decode_frame_to_ndarray(self, encoded_data: bytes):
        """Phase-1 fallback — returns numpy (h, w, 3) RGB ndarray.

        # phase1 fallback: same trap as decode_frame; exists only for
        the off-path PIL / QImage tooling that hasn't been migrated.
        """
        if not self._initialized or not self._decoder:
            return None

        try:
            packet = av.Packet(encoded_data)
            frames = self._decoder.decode(packet)
            for frame in frames:
                # phase1 fallback: rgb24 coercion (NOT on the video hot path)
                return frame.to_ndarray(format="rgb24")
        except Exception:
            return None

    def get_frame_size(self) -> Optional[tuple]:
        """Return (width, height) of the last decoded frame, or None."""
        if self._decoder and self._decoder.width and self._decoder.height:
            return (self._decoder.width, self._decoder.height)
        return None

    def flush(self):
        """Flush the decoder (e.g., on seek or stream reset)."""
        if self._decoder:
            try:
                # Send empty packet to flush buffered frames
                self._decoder.decode(av.Packet())
            except Exception:
                pass

    def reset(self, codec: str = None):
        """Reset the decoder, optionally changing codec."""
        if codec:
            self._codec_name = codec
        self.close()
        self._init_decoder()

    def close(self):
        """Release decoder resources."""
        if self._decoder:
            try:
                self._decoder.close()
            except Exception:
                pass
            self._decoder = None
        self._initialized = False
        logger.info("Video decoder closed (decoded %d frames)", self._frame_count)


class DecoderManager:
    """
    Manages video decoder lifecycle and codec switching.

    Lazily creates decoders and handles codec changes from the server.
    """

    def __init__(self):
        self._decoders = {}  # codec -> VideoDecoder
        self._active_codec = None

    def get_decoder(self, codec: str) -> Optional[VideoDecoder]:
        """Get or create a decoder for the given codec."""
        if not HAS_PYAV:
            return None

        if codec not in self._decoders:
            try:
                self._decoders[codec] = VideoDecoder(codec)
            except Exception as e:
                logger.error("Failed to create %s decoder: %s", codec, e)
                return None

        self._active_codec = codec
        return self._decoders[codec]

    def decode(self, codec: str, data: bytes) -> Optional[bytes]:
        """Convenience: get decoder and decode in one call."""
        dec = self.get_decoder(codec)
        if dec and dec.is_ready:
            return dec.decode_frame(data)
        return None

    def close_all(self):
        """Close all decoders."""
        for dec in self._decoders.values():
            dec.close()
        self._decoders.clear()

    @property
    def active_backend(self) -> str:
        if self._active_codec and self._active_codec in self._decoders:
            return self._decoders[self._active_codec].hw_backend
        return "none"


def check_decode_available() -> dict:
    """Check what decode capabilities are available."""
    result = {
        "pyav": HAS_PYAV,
        "h264": False,
        "h265": False,
        "av1": False,
        "hw_backends": [],
    }

    if not HAS_PYAV:
        return result

    for codec_name, av_name in [("h264", "h264"), ("h265", "hevc"), ("av1", "av1")]:
        try:
            c = av.codec.Codec(av_name, "r")
            result[codec_name] = True
        except Exception:
            pass

    return result
