"""
Client-side video decoder with hardware acceleration via PyAV (FFmpeg).

Decodes H.264, H.265, and AV1 video streams received from the server
into raw RGB frames for display in the Qt viewer.

Hardware decode priority: CUDA (NVIDIA) > VAAPI > DXVA2/D3D11VA > VideoToolbox > Software
Falls back gracefully if hardware decode is unavailable.
"""

import logging
import sys
import time
from typing import Optional, Callable, List

logger = logging.getLogger(__name__)

try:
    import av
    HAS_PYAV = True
except ImportError:
    HAS_PYAV = False
    logger.warning("PyAV not installed — video decode disabled. Install with: pip install av")


def _build_hw_decoders() -> dict:
    """
    Build platform-aware hardware decoder priority lists.

    CUDA is deliberately skipped on macOS: ctx.open() succeeds (generic codec
    context) but actual CUDA GPU operations segfault at runtime because macOS
    has no NVIDIA CUDA runtime. VideoToolbox is the correct native accelerator.
    """
    is_mac = sys.platform == "darwin"
    is_win = sys.platform == "win32"

    if is_mac:
        return {
            "h264": [("h264", "videotoolbox"), ("h264", None)],
            "h265": [("hevc", "videotoolbox"), ("hevc", None)],
            "av1":  [("av1",  "videotoolbox"), ("av1",  None)],
        }
    if is_win:
        return {
            "h264": [("h264", "d3d11va"), ("h264", "dxva2"), ("h264", "cuda"), ("h264", None)],
            "h265": [("hevc", "d3d11va"), ("hevc", "dxva2"), ("hevc", "cuda"), ("hevc", None)],
            "av1":  [("av1",  "d3d11va"), ("av1",  "dxva2"), ("av1",  "cuda"), ("av1",  None)],
        }
    # Linux: NVIDIA first, then VAAPI (Intel/AMD), then software
    return {
        "h264": [("h264", "cuda"), ("h264", "vaapi"), ("h264", None)],
        "h265": [("hevc", "cuda"), ("hevc", "vaapi"), ("hevc", None)],
        "av1":  [("av1",  "cuda"), ("av1",  "vaapi"), ("av1",  None)],
    }


HW_DECODERS = _build_hw_decoders()


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

        self._init_decoder()

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
        return self._hw_type or "software"

    @property
    def avg_decode_time_ms(self) -> float:
        if not self._decode_times:
            return 0.0
        return sum(self._decode_times[-30:]) / len(self._decode_times[-30:])

    def decode_frame(self, encoded_data: bytes) -> Optional[bytes]:
        """
        Decode an encoded frame and return raw RGB888 bytes.

        Args:
            encoded_data: Raw H.264/H.265 NAL units or AV1 OBU data

        Returns:
            RGB888 bytes (width * height * 3) or None if decode failed
        """
        if not self._initialized or not self._decoder:
            return None

        start = time.time()
        try:
            packet = av.Packet(encoded_data)
            frames = self._decoder.decode(packet)

            for frame in frames:
                # Convert to RGB24
                rgb_frame = frame.to_ndarray(format="rgb24")
                self._frame_count += 1
                if self._frame_count == 1:
                    logger.info("First frame decoded: %dx%d (codec=%s, hw=%s)",
                                frame.width, frame.height,
                                self._codec_name, self._hw_type or "software")
                elapsed_ms = (time.time() - start) * 1000
                self._decode_times.append(elapsed_ms)
                if len(self._decode_times) > 100:
                    self._decode_times = self._decode_times[-60:]

                # Return raw bytes for QImage (Format_RGB888)
                return bytes(rgb_frame)

        except av.error.InvalidDataError as e:
            # Log at warning the first few times so we can see whether we're
            # stuck waiting for a keyframe or actually failing to decode.
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
        """
        Decode and return as numpy array (height, width, 3) RGB.
        Useful for display with PIL or direct QImage creation.
        """
        if not self._initialized or not self._decoder:
            return None

        try:
            packet = av.Packet(encoded_data)
            frames = self._decoder.decode(packet)
            for frame in frames:
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
