"""Encoder lifecycle — spawn, restart, stop. Extracted from SessionRuntime.

D-11 extraction (Plan 01-10 Task 2). Single responsibility: own the
``VideoEncoder`` (or ``JpegFallbackEncoder``) singleton for a
SessionRuntime and keep the ``SessionRuntime.encoder`` attribute (the
back-compat handle used by ClientSession's IDR-on-drop path from Plan
01-01) in sync.

Why we keep a back-reference instead of having callers go through
``encoder_lifecycle.encoder``:

The STAB-04 fix (Plan 01-01) put ``self.runtime.encoder.request_keyframe()``
inside ``ClientSession.enqueue()``. Other call sites (``handle_client``,
``handle_http``, ``tests/server/test_pipelines.py``) already reach the
encoder via ``runtime.encoder``. Preserving that attribute is part of the
zero-behavior-change contract; the lifecycle helper just keeps it
populated via assignment after every spawn/restart/stop.

The ``sw_only`` filter + the ``codec in (h264,h265,av1) and
ffmpeg_caps.get(codec, False)`` check live in ``spawn()`` (original init
logic). ``restart()`` skips those checks — it's only called after we
already know an encoder exists, so it reuses ``self.encoder._available``
the way the original ``_restart_encoder`` did (preserves NVENC fallback
state).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from server.video_encoder import VideoEncoder, JpegFallbackEncoder

if TYPE_CHECKING:
    # D-11 / Plan 01-11 Task 2: SessionRuntime moved to server/session_runtime.py
    from server.session_runtime import SessionRuntime


logger = logging.getLogger("teraguchi.server.encoder_lifecycle")


class EncoderLifecycle:
    """Owns the VideoEncoder / JpegFallbackEncoder for one SessionRuntime."""

    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        self.encoder: Optional[VideoEncoder] = None

    def spawn(self, sw_only: bool, jpeg_quality: int) -> None:
        """Create the initial encoder based on ``runtime.quality.codec``
        and the discovered ``runtime.ffmpeg_caps``.

        Mirrors the original ``SessionRuntime.__init__`` encoder block
        exactly:

          * If codec ∈ {h264, h265, av1} and ffmpeg supports it →
            VideoEncoder with the (optionally sw-filtered) encoder list.
            Sets ``runtime.use_h264 = True`` + ``runtime.encoder`` +
            starts the encoder with the runtime's ``_on_encoded_frame``
            callback.
          * Otherwise → JpegFallbackEncoder stashed on
            ``runtime.jpeg_encoder``; ``runtime.use_h264`` stays False.
        """
        runtime = self._runtime
        codec = runtime.quality.codec
        ffmpeg_caps = runtime.ffmpeg_caps
        available_encoders = runtime.available_encoders

        if codec in ("h264", "h265", "av1") and ffmpeg_caps.get(codec, False):
            runtime.use_h264 = True
            enc_list = available_encoders
            if sw_only:
                enc_list = {}
                for c, encs in available_encoders.items():
                    enc_list[c] = [e for e in encs if e.backend == "software"]
            self.encoder = VideoEncoder(
                runtime.capture.width, runtime.capture.height,
                runtime.quality, available_encoders=enc_list,
                input_pix_fmt=getattr(runtime.capture, "output_format", "bgra"))
            self.encoder.start(runtime._on_encoded_frame)
            # Publish the handle — ClientSession.enqueue (STAB-04) reaches
            # it via ``self.runtime.encoder``; tests/server/test_pipelines.py
            # asserts on ``cs.runtime.encoder.keyframe_requests``.
            runtime.encoder = self.encoder
            logger.info("[%s] Encoder: %s (%s) %s", runtime.username,
                        codec.upper(), self.encoder.active_backend,
                        runtime.quality.chroma.upper())
        else:
            runtime.jpeg_encoder = JpegFallbackEncoder(quality=jpeg_quality)
            logger.info("[%s] Using JPEG fallback encoder", runtime.username)

    def restart(self) -> None:
        """Stop current, spawn new with the same quality settings. Used
        by quality-slider flips (JPEG → H264), monitor hot-plug, resize
        requests, and monitor-select messages.

        Preserves the NVENC fallback state by reusing the current
        encoder's ``_available`` dict (same shape as original
        ``_restart_encoder``).
        """
        runtime = self._runtime
        if self.encoder:
            old_available = self.encoder._available
            self.encoder.stop()
        else:
            old_available = None
        self.encoder = VideoEncoder(
            runtime.capture.width, runtime.capture.height,
            runtime.quality, available_encoders=old_available,
            input_pix_fmt=getattr(runtime.capture, "output_format", "bgra"))
        self.encoder.start(runtime._on_encoded_frame)
        runtime.encoder = self.encoder   # keep the back-compat handle fresh
        runtime.health.current_resolution = (
            f"{runtime.capture.width}x{runtime.capture.height}")
        # Keep the health monitor's encode-time source pointed at the live
        # encoder after a re-spawn. (Fork: macOS health metric.)
        runtime.health.encoder_ref = self.encoder

    def stop(self) -> None:
        if self.encoder:
            try:
                self.encoder.stop()
            except Exception as e:
                logger.warning("encoder stop error: %s", e)
            self.encoder = None
            self._runtime.encoder = None
