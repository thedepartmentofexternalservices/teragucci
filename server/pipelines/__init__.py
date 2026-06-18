"""Bounded pipeline queues (STAB-07).

Per RESEARCH Pattern 4, the capture → encode → transport pipeline has
four queues, each with a documented bound + drop/block policy:

  * Capture → Encoder:        maxsize=2, drop-OLDEST (freshness > order)
  * Encoder → Broadcaster:    maxsize=3, drop-OLDEST (freshness > order)
  * Broadcaster → per-client: maxsize=4, drop-OLDEST + IDR-on-drop
                              (STAB-04, lives in ``server/client_session.py``)
  * Input  (client → server): maxsize=64, BLOCK producer (input must
                              never drop — back-pressure the client)

This package provides the first two as dedicated classes so the drop
policy + OBS-03 ``on_drop`` telemetry hook are centralised and testable.
The per-client send queue stays in ``ClientSession`` because its drop
handling depends on session-local state (the ``_drops_since_keyframe``
streak counter). The input queue is a plain ``asyncio.Queue`` with an
explicit ``maxsize=64`` argument — blocking on full is the stdlib
default and no wrapper is required.
"""
from server.pipelines.capture_queue import CaptureQueue
from server.pipelines.encoder_queue import EncoderQueue

__all__ = ["CaptureQueue", "EncoderQueue"]
