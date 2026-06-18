"""Capture → Encoder bounded queue (STAB-07).

Single-producer / single-consumer queue sitting between screen-capture
output and the video-encoder feed. Policy: drop-OLDEST on overflow.
Rationale — if the encoder falls behind the capture rate, we'd rather
keep the freshest BGRA frame and drop whatever raw pixels were stale:
the visible artefact of a dropped raw frame is one extra 1/60s of
staleness, which the encoder will recover from on the next GOP. The
alternative (back-pressure the capture loop) would desync the wall-clock
FPS cadence and lengthen input-to-photon latency.

The optional ``on_drop`` callback is the hook OBS-03 (Plan 01-14) uses
to surface per-queue drop counters into the HealthMonitor / HealthStats.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional


logger = logging.getLogger("teraguchi.server.pipelines.capture")


class CaptureQueue:
    """Bounded asyncio queue with drop-OLDEST on full.

    Args:
        maxsize: queue capacity. Default 2 per RESEARCH Pattern 4.
        on_drop: optional zero-arg callback invoked each time a drop
            occurs. Exceptions raised by the callback are caught and
            logged at DEBUG — put() must never fail for telemetry reasons.
    """

    def __init__(
        self,
        maxsize: int = 2,
        on_drop: Optional[Callable[[], None]] = None,
    ) -> None:
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._on_drop = on_drop
        self.maxsize = maxsize
        self.dropped = 0

    async def put(self, item: Any) -> bool:
        """Enqueue ``item``. On full, drop the OLDEST existing item and
        enqueue ``item`` in its place.

        Returns:
            True if the enqueue was clean (no drop). False if a drop
            occurred (caller can use this to emit drop-specific telemetry).
        """
        try:
            self._q.put_nowait(item)
            return True
        except asyncio.QueueFull:
            try:
                self._q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._q.put_nowait(item)
            except asyncio.QueueFull:
                # Defensive — shouldn't happen after get_nowait above.
                pass
            self.dropped += 1
            if self._on_drop is not None:
                try:
                    self._on_drop()
                except Exception as exc:
                    logger.debug(
                        "capture_queue.on_drop_callback_raised exc=%r", exc)
            return False

    async def get(self) -> Any:
        return await self._q.get()

    def qsize(self) -> int:
        return self._q.qsize()

    def empty(self) -> bool:
        return self._q.empty()
