"""STAB-01 — JitterBuffer reordering + keyframe flush + stats.

Target-read divergence notes (from ``common/jitter_buffer.py``):
- ``push(channel, flags, timestamp_ms, data, is_keyframe=False)`` — NOT
  ``push(seq, payload)``. No capacity argument either; the buffer is
  unbounded and depth is instead regulated by the adaptive target-depth
  time window + the playback thread.
- No public ``pop()``. Frames are delivered to an ``on_frame_ready``
  callback from a background thread started by ``start()``. Unit tests
  avoid threading by inspecting the internal ``_buffer`` deque directly
  (deliberate white-box test — the reorder behavior IS the critical
  property we're locking in).
- Keyframes flush prior frames (fast recovery after loss).
"""
from common.jitter_buffer import JitterBuffer


def test_in_order_push_preserves_order():
    jb = JitterBuffer()
    jb.push(channel=0, flags=0, timestamp_ms=100, data=b"a")
    jb.push(channel=0, flags=0, timestamp_ms=200, data=b"b")
    jb.push(channel=0, flags=0, timestamp_ms=300, data=b"c")
    timestamps = [f.timestamp_ms for f in jb._buffer]
    assert timestamps == [100, 200, 300]


def test_out_of_order_push_reorders_by_timestamp():
    """The core jitter-smoothing contract — late packets must be slotted
    into the correct chronological position."""
    jb = JitterBuffer()
    jb.push(channel=0, flags=0, timestamp_ms=300, data=b"c")
    jb.push(channel=0, flags=0, timestamp_ms=100, data=b"a")
    jb.push(channel=0, flags=0, timestamp_ms=200, data=b"b")
    timestamps = [f.timestamp_ms for f in jb._buffer]
    payloads = [f.data for f in jb._buffer]
    assert timestamps == [100, 200, 300]
    assert payloads == [b"a", b"b", b"c"]


def test_duplicate_timestamp_does_not_break_ordering():
    """Two frames with the same timestamp must not crash the insertion search."""
    jb = JitterBuffer()
    jb.push(channel=0, flags=0, timestamp_ms=100, data=b"a1")
    jb.push(channel=0, flags=0, timestamp_ms=100, data=b"a2")
    jb.push(channel=0, flags=0, timestamp_ms=50, data=b"earlier")
    timestamps = [f.timestamp_ms for f in jb._buffer]
    assert timestamps == [50, 100, 100]


def test_keyframe_push_flushes_buffered_frames():
    """Keyframe recovery: dropping back-logged frames must be fast so we don't
    render stale content after a network blip."""
    jb = JitterBuffer()
    jb.push(channel=0, flags=0, timestamp_ms=100, data=b"old1")
    jb.push(channel=0, flags=0, timestamp_ms=200, data=b"old2")
    assert len(jb._buffer) == 2

    jb.push(channel=0, flags=0, timestamp_ms=300, data=b"kf", is_keyframe=True)
    # After a keyframe, only the keyframe remains
    assert len(jb._buffer) == 1
    assert jb._buffer[0].is_keyframe is True
    assert jb._buffer[0].data == b"kf"
    # Stats must reflect that the two older frames were dropped
    assert jb._frames_dropped >= 2


def test_frames_in_counter_increments_on_every_push():
    jb = JitterBuffer()
    for i in range(5):
        jb.push(channel=0, flags=0, timestamp_ms=i * 10, data=b"x")
    assert jb._frames_in == 5


def test_stats_dict_shape():
    """Guard: stats property exposes exactly the fields the health overlay expects."""
    jb = JitterBuffer()
    jb.push(channel=0, flags=0, timestamp_ms=0, data=b"x")
    stats = jb.stats
    for key in (
        "buffer_depth_ms",
        "target_depth_ms",
        "avg_jitter_ms",
        "frames_in",
        "frames_out",
        "frames_dropped",
        "buffered_count",
    ):
        assert key in stats, f"missing stats key: {key}"
    assert stats["frames_in"] == 1
    assert stats["buffered_count"] == 1
