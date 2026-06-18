"""Encoder → Broadcaster bounded queue (STAB-07).

Sits between the FFmpeg encoder subprocess callback and the per-session
broadcaster that fans encoded frames out to every connected client.
Policy: drop-OLDEST on overflow (same rationale as CaptureQueue — an
encoded frame the broadcaster never sent is a frame the decoder never
depended on, so dropping it costs at most one GOP's worth of staleness
and the encoder's next IDR repairs anything the decoder missed).

Distinct from ``server/client_session.py::ClientSession.send_queue``
(maxsize=4, drop-oldest + IDR-on-drop — STAB-04). The send_queue handles
per-client back-pressure with session-local state (``_drops_since_keyframe``
streak counter). EncoderQueue sits one layer up: it bounds the shared
encoder-output path before the broadcaster multiplexes frames to clients.

``on_drop`` is the OBS-03 (Plan 01-14) telemetry hook for per-queue
drop counters.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional


logger = logging.getLogger("teraguchi.server.pipelines.encoder")


class EncoderQueue:
    """Bounded asyncio queue with drop-OLDEST on full.

    Args:
        maxsize: queue capacity. Default 3 per RESEARCH Pattern 4.
        on_drop: optional zero-arg callback invoked each time a drop
            occurs. Exceptions raised by the callback are caught and
            logged at DEBUG — put() must never fail for telemetry reasons.
    """

    def __init__(
        self,
        maxsize: int = 3,
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
            True if the enqueue was clean. False on drop.
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
                pass
            self.dropped += 1
            if self._on_drop is not None:
                try:
                    self._on_drop()
                except Exception as exc:
                    logger.debug(
                        "encoder_queue.on_drop_callback_raised exc=%r", exc)
            return False

    async def get(self) -> Any:
        return await self._q.get()

    def qsize(self) -> int:
        return self._q.qsize()

    def empty(self) -> bool:
        return self._q.empty()
