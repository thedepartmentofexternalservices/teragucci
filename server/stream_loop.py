"""Stream loops (H264 + JPEG) — extracted from server/main.py::SessionRuntime.

D-11 extraction (Plan 01-10). Single responsibility: drive the capture →
encode → dispatch loop at the session's target FPS. One instance per
SessionRuntime. Owns no state beyond the running flag + task handle; all
shared state (capture, encoder, clients, health) is accessed via the
back-reference to SessionRuntime (``self._runtime``).

Behavior is preserved exactly from the pre-extraction monolith:

  * ``while self._running:`` outer loop, broken by ``stop()``
  * FPS-pacing via ``await asyncio.sleep(max(interval - elapsed, 0.001))``
  * Per-frame try/except around capture + encode (log and continue)
  * H264 path: full-frame BGRA capture + encoder feed
  * JPEG path: dirty-rect capture + per-region enqueue broadcast

Plan 01-14 (OBS-02) wiring: ``StageTimer`` from :mod:`common.logging`
wraps the capture + encode stages so every iteration emits
``stage.timing`` structlog events. ``HealthMonitor.record_capture_time``
remains the aggregate-path recorder (feeds the rolling average surfaced
in ``HealthStats.capture_time_ms``); the structlog event and the deque
serve different consumers (event log / dashboard vs. client overlay).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional

from common.logging import StageTimer
from common.messages import FrameType, encode_jpeg_header

if TYPE_CHECKING:
    # D-11 / Plan 01-11 Task 2: SessionRuntime now lives in
    # server/session_runtime.py. server/main.py re-exports it for backward
    # compat, but type-hint imports should go to the canonical home.
    from server.session_runtime import SessionRuntime


logger = logging.getLogger("teraguchi.server.stream_loop")


class StreamLoop:
    """Capture → encode → dispatch loop. One instance per SessionRuntime."""

    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def run_h264(self, fps: int) -> None:
        interval = 1.0 / fps
        while self._running:
            start = time.time()
            if self._runtime.clients and self._runtime.encoder:
                try:
                    # OBS-02 (Plan 01-14) — StageTimer emits stage.timing
                    # events for the capture + encode stages. The existing
                    # HealthMonitor.record_capture_time call is preserved so
                    # HealthStats.capture_time_ms (client overlay consumer)
                    # still reflects the rolling average.
                    # Phase 3 D-02 — per-session capture-mode crop. v1 is
                    # single-session-per-host for cropped paths (see
                    # docs/release.md "Multi-session crop per host
                    # (v1.1 follow-up)"); pull crop_rect off the first
                    # authenticated session. Legacy pre-Phase-3 clients
                    # default to crop=None → byte-equal to the Phase 2
                    # always-full-virtual-desktop path (preserves
                    # 9-checkpoint 10-bit fixture).
                    crop = None
                    for _ws, cs in self._runtime.clients.items():
                        if getattr(cs, "authenticated", False):
                            crop = getattr(cs, "crop_rect", None)
                            if crop is not None:
                                break
                    # capture_raw_bgra_with_crop is the Phase 3 entry;
                    # fall back to capture_raw_bgra for older capture
                    # backends (test stubs, Phase 1 MacScreenCapture
                    # before its Phase 3 upgrade lands). mirror_all
                    # (crop=None) is byte-equal to capture_raw_bgra in
                    # either case — preserves Phase 2 10-bit fixture.
                    with_crop = getattr(
                        self._runtime.capture,
                        "capture_raw_bgra_with_crop",
                        None,
                    )
                    t0 = time.time()
                    with StageTimer("capture"):
                        if with_crop is not None:
                            raw = with_crop(crop)
                        else:
                            raw = self._runtime.capture.capture_raw_bgra()
                    self._runtime.health.record_capture_time(
                        (time.time() - t0) * 1000)
                    with StageTimer("encode"):
                        self._runtime.encoder.feed_frame(raw)
                except Exception as e:
                    logger.error("[%s] H264 capture error: %s",
                                 self._runtime.username, e)
            elapsed = time.time() - start
            await asyncio.sleep(max(interval - elapsed, 0.001))

    async def run_jpeg(self, fps: int) -> None:
        interval = 1.0 / fps
        while self._running:
            start = time.time()
            if self._runtime.clients:
                try:
                    t0 = time.time()
                    regions = self._runtime.capture.capture_dirty_regions()
                    self._runtime.health.record_capture_time(
                        (time.time() - t0) * 1000)
                    for x, y, w, h, jpeg_data in regions:
                        ft = (FrameType.VIDEO_FULL
                              if (x == 0 and y == 0 and
                                  w == self._runtime.capture.width and
                                  h == self._runtime.capture.height)
                              else FrameType.VIDEO_PARTIAL)
                        header = encode_jpeg_header(ft, x, y, w, h)
                        data = header + jpeg_data
                        self._runtime.health.record_frame_sent(len(data))
                        for ws, cs in list(self._runtime.clients.items()):
                            if cs.authenticated:
                                if not await cs.enqueue(data):
                                    self._runtime.health.record_frame_dropped()
                except Exception as e:
                    logger.error("[%s] JPEG capture error: %s",
                                 self._runtime.username, e)
            elapsed = time.time() - start
            await asyncio.sleep(max(interval - elapsed, 0.001))

    def start(self, fps: int) -> None:
        """Kick off the appropriate streaming task based on the runtime's
        current codec decision (``use_h264`` / presence of encoder)."""
        self._running = True
        if self._runtime.use_h264 and self._runtime.encoder:
            self._task = asyncio.ensure_future(self.run_h264(fps))
        else:
            self._task = asyncio.ensure_future(self.run_jpeg(fps))

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
