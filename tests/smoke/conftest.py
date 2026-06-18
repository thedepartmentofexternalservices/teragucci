"""Smoke-scope fixtures for the 1-hour synthetic harness (STAB-09 / D-15).

Provides the synthetic input generator + frame sink helpers used by
``tests/smoke/test_synthetic_1h.py``. Kept scope-local so unit/integration
tests don't accidentally pull in the smoke-specific timing assumptions.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any, Callable, Coroutine

import pytest


# Events/second for synthetic input — tuned to the Wacom pressure-stream
# + keyboard + mouse mix described in D-15. Realistic for an artist
# actively painting/rotoscoping; conservative enough to not saturate a
# small GHA runner's event loop.
SYNTHETIC_EVENT_RATE_HZ: int = 120


async def _synthetic_input_generator(
    stop_event: asyncio.Event,
    queue: asyncio.Queue,
) -> None:
    """Produce realistic-rate Wacom pressure + keyboard + mouse events.

    Periodically issues modifier chords (Ctrl+Shift+P) that exercise the
    modifier-release-on-deactivate code path (INPUT-02 precursor). The
    emitted dicts are intentionally shallow JSON so a real websocket +
    server pipeline could consume them unchanged; in the Phase 1 harness
    they simply flow into an :class:`asyncio.Queue` backing a numpy sink.
    """
    interval = 1.0 / SYNTHETIC_EVENT_RATE_HZ
    t = 0
    while not stop_event.is_set():
        t += 1
        if t % 60 == 0:
            event: dict[str, Any] = {
                "type": "key",
                "modifiers": ["ctrl", "shift"],
                "key": "P",
                "down": True,
            }
        elif t % 5 == 0:
            event = {
                "type": "pen",
                "x": random.uniform(0, 1920),
                "y": random.uniform(0, 1080),
                "pressure": random.uniform(0.0, 1.0),
            }
        else:
            event = {
                "type": "mouse_move",
                "x": random.uniform(0, 1920),
                "y": random.uniform(0, 1080),
            }
        try:
            await asyncio.wait_for(queue.put(event), timeout=0.5)
        except asyncio.TimeoutError:
            # Input queue blocked — the harness records this as a queue
            # pressure signal, but we never crash the producer.
            pass
        await asyncio.sleep(interval)


@pytest.fixture
def synthetic_input_events() -> Callable[
    [asyncio.Event, asyncio.Queue], Coroutine[Any, Any, None]
]:
    """Factory fixture returning the synthetic input coroutine.

    Usage inside a test::

        async def test_something(synthetic_input_events):
            stop = asyncio.Event()
            q: asyncio.Queue = asyncio.Queue(maxsize=64)
            producer = asyncio.ensure_future(synthetic_input_events(stop, q))
            ...
            stop.set()
            await producer
    """
    return _synthetic_input_generator
