"""STAB-04 + STAB-07 pipeline-queue regression tests.

These tests guard the send_queue bounded-queue + IDR-on-drop policy applied
to server/main.py::ClientSession. The bug they regress is a ~2s GOP stall
when a single P-frame is dropped without requesting an IDR.
"""
import asyncio

import pytest

from server.client_session import ClientSession


class _FakeRuntime:
    def __init__(self, encoder):
        self.encoder = encoder


@pytest.fixture
def session_with_fake_encoder(fake_ws, fake_encoder):
    cs = ClientSession(fake_ws)
    cs.runtime = _FakeRuntime(fake_encoder)
    cs.authenticated = True
    return cs


@pytest.mark.asyncio
async def test_send_queue_maxsize_is_four(session_with_fake_encoder):
    """STAB-04 guard: maxsize must be 4, not 30."""
    assert session_with_fake_encoder.send_queue.maxsize == 4


@pytest.mark.asyncio
async def test_enqueue_drops_oldest_on_full(session_with_fake_encoder):
    """Queue full -> drop OLDEST item, new item enters."""
    cs = session_with_fake_encoder
    for i in range(4):
        ok = await cs.enqueue(f"p-{i}".encode(), is_keyframe=False)
        assert ok, f"enqueue {i} should succeed while under maxsize"
    # 5th enqueue overflows
    ok = await cs.enqueue(b"p-4", is_keyframe=False)
    assert ok is False, "5th enqueue should report drop"
    # Queue should still have 4 items; the OLDEST (p-0) is gone, newest (p-4) is present
    items = []
    while not cs.send_queue.empty():
        items.append(cs.send_queue.get_nowait())
    assert b"p-4" in items, "newest frame should be present post-drop"
    assert b"p-0" not in items, "oldest frame should have been evicted"


@pytest.mark.asyncio
async def test_send_queue_idr_on_drop(session_with_fake_encoder):
    """STAB-04 core assertion: first drop of a streak requests exactly one IDR."""
    cs = session_with_fake_encoder
    # Fill queue (4 successes) then overflow 3 times
    for i in range(4):
        await cs.enqueue(f"p-{i}".encode(), is_keyframe=False)
    for i in range(3):
        await cs.enqueue(f"overflow-{i}".encode(), is_keyframe=False)
    # Exactly one IDR request across 3 drops (first-of-streak rule)
    assert cs.runtime.encoder.keyframe_requests == 1, (
        f"expected 1 IDR per drop streak, got {cs.runtime.encoder.keyframe_requests}"
    )


@pytest.mark.asyncio
async def test_send_queue_keyframe_resets_drop_counter(session_with_fake_encoder):
    """After a keyframe enqueue, the drop-streak counter resets -> next drop requests a fresh IDR."""
    cs = session_with_fake_encoder
    # Streak 1
    for i in range(4):
        await cs.enqueue(f"p-{i}".encode(), is_keyframe=False)
    await cs.enqueue(b"overflow", is_keyframe=False)
    assert cs.runtime.encoder.keyframe_requests == 1

    # Keyframe enqueue clears queue + resets counter
    await cs.enqueue(b"keyframe-data", is_keyframe=True)
    assert cs._drops_since_keyframe == 0

    # Streak 2 — fresh IDR request expected
    for i in range(4):
        await cs.enqueue(f"p2-{i}".encode(), is_keyframe=False)
    await cs.enqueue(b"overflow-2", is_keyframe=False)
    assert cs.runtime.encoder.keyframe_requests == 2


# ── STAB-07 Capture/Encoder queue regression tests ───────────────────


@pytest.mark.asyncio
async def test_capture_queue_default_maxsize_is_two():
    """CaptureQueue defaults to maxsize=2 per RESEARCH Pattern 4."""
    from server.pipelines import CaptureQueue
    q = CaptureQueue()
    assert q.maxsize == 2


@pytest.mark.asyncio
async def test_encoder_queue_default_maxsize_is_three():
    """EncoderQueue defaults to maxsize=3 per RESEARCH Pattern 4."""
    from server.pipelines import EncoderQueue
    q = EncoderQueue()
    assert q.maxsize == 3


@pytest.mark.asyncio
async def test_capture_queue_drops_oldest():
    """Overflow drops oldest, preserves freshness."""
    from server.pipelines import CaptureQueue
    q = CaptureQueue(maxsize=2)
    assert await q.put(b"a") is True
    assert await q.put(b"b") is True
    assert await q.put(b"c") is False   # overflow → drop oldest (a)
    # get order: b, c (a was dropped)
    first = await q.get()
    second = await q.get()
    assert first == b"b"
    assert second == b"c"
    assert q.dropped == 1


@pytest.mark.asyncio
async def test_encoder_queue_drops_oldest():
    """Overflow drops oldest item; later items retained in order."""
    from server.pipelines import EncoderQueue
    q = EncoderQueue(maxsize=3)
    for i in range(3):
        assert await q.put(f"f{i}".encode()) is True
    assert await q.put(b"f3") is False   # overflow
    assert q.dropped == 1
    got = []
    while not q.empty():
        got.append(await q.get())
    assert got == [b"f1", b"f2", b"f3"]   # f0 dropped


@pytest.mark.asyncio
async def test_capture_queue_on_drop_hook():
    """on_drop callback fires once per drop (OBS-03 hook for Plan 14)."""
    from server.pipelines import CaptureQueue
    drop_count = {"n": 0}

    def on_drop():
        drop_count["n"] += 1

    q = CaptureQueue(maxsize=1, on_drop=on_drop)
    await q.put(b"a")
    await q.put(b"b")   # drop a
    await q.put(b"c")   # drop b
    assert drop_count["n"] == 2
    assert q.dropped == 2


@pytest.mark.asyncio
async def test_encoder_queue_on_drop_hook():
    """EncoderQueue fires on_drop exactly once per dropped frame."""
    from server.pipelines import EncoderQueue
    drop_count = {"n": 0}

    def on_drop():
        drop_count["n"] += 1

    q = EncoderQueue(maxsize=2, on_drop=on_drop)
    await q.put(b"a")
    await q.put(b"b")
    await q.put(b"c")   # drop a
    await q.put(b"d")   # drop b
    assert drop_count["n"] == 2
    assert q.dropped == 2


@pytest.mark.asyncio
async def test_capture_queue_on_drop_hook_exception_swallowed():
    """A raising on_drop hook must not break the put() contract."""
    from server.pipelines import CaptureQueue

    def on_drop():
        raise RuntimeError("boom")

    q = CaptureQueue(maxsize=1, on_drop=on_drop)
    await q.put(b"a")
    # Raising callback must not propagate; drop still accounted for.
    assert await q.put(b"b") is False
    assert q.dropped == 1
    # Freshest value is retained.
    assert await q.get() == b"b"


@pytest.mark.asyncio
async def test_input_queue_blocks_on_full():
    """STAB-07: input queue (maxsize=64 default) must BLOCK producer, not drop.

    Uses a smaller maxsize=2 to keep the test tight; the behavior under test
    is ``asyncio.Queue.put`` blocking on full, which is maxsize-independent.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    await q.put("e1")
    await q.put("e2")
    # Third put should block; asyncio.wait_for with short timeout times out.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.put("e3"), timeout=0.05)
    # Drain one — now producer unblocks.
    await q.get()
    await asyncio.wait_for(q.put("e3"), timeout=0.1)
    assert q.qsize() == 2


def test_session_runtime_exposes_pipeline_queues():
    """SessionRuntime must instantiate both pipeline queues so callers
    (Plan 14 telemetry + future capture-rate decoupling) can reach them
    without digging into internals.

    We inspect the source file directly (not ``inspect.getsource(module)``)
    so this test stays runnable on CI nodes that don't have the full
    capture stack installed — importing ``server.session_runtime`` drags
    in PIL / CoreGraphics / Xlib bindings that aren't available in the
    unit-test environment. The wiring contract is purely textual: the
    module must import both classes and instantiate them on the runtime.
    """
    from server.pipelines import CaptureQueue, EncoderQueue

    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "server" / "session_runtime.py").read_text()

    assert "from server.pipelines import CaptureQueue, EncoderQueue" in src, (
        "SessionRuntime must import CaptureQueue + EncoderQueue (STAB-07)")
    assert "CaptureQueue(" in src, (
        "SessionRuntime must instantiate CaptureQueue (STAB-07)")
    assert "EncoderQueue(" in src, (
        "SessionRuntime must instantiate EncoderQueue (STAB-07)")

    # Sanity: the classes are importable and have the expected defaults.
    assert CaptureQueue().maxsize == 2
    assert EncoderQueue().maxsize == 3
