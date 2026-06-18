"""Phase 3 D-17 — ClipboardChunkAssembler (chunked clipboard transport).

Plan 03-06 implementation of Plan 03-01 Wave 0 RED skeletons. Locks
the 5 edge-case behaviors called out in the plan's Test 1:

  * in-order assembly returns full payload on the last chunk
  * out-of-order delivery produces correct bytes
  * duplicate ``chunk_index`` is an idempotent ignore
  * out-of-range ``chunk_index`` is dropped (T-03-03)
  * ``is_stale`` flips True after CHUNK_TIMEOUT_S (T-03-25)

Also covers the init-time ``total_chunks`` bound (T-03-26) and the
per-chunk size cap (T-03-27).
"""
import pytest


def test_assembler_in_order_returns_full_payload():
    """D-17 — in-order chunk assembly emits full payload on the last chunk."""
    from common.clipboard_chunks import ClipboardChunkAssembler
    asm = ClipboardChunkAssembler(
        sequence_id=1, total_chunks=3, content_type="text/plain",
    )
    assert asm.add(0, "AA") is None
    assert asm.add(1, "BB") is None
    # Last chunk emits the concatenation.
    assert asm.add(2, "CC") == "AABBCC"


def test_assembler_handles_out_of_order_chunks():
    """D-17 — chunk order-independence (JSON control channel may reorder)."""
    from common.clipboard_chunks import ClipboardChunkAssembler
    asm = ClipboardChunkAssembler(
        sequence_id=2, total_chunks=3, content_type="image/png",
    )
    # Arrive 2, 0, 1 — must still reassemble in index order.
    assert asm.add(2, "CC") is None
    assert asm.add(0, "AA") is None
    assert asm.add(1, "BB") == "AABBCC"


def test_assembler_idempotent_duplicate_chunk_index():
    """D-17 — duplicate ``chunk_index`` for a sequence is an idempotent ignore."""
    from common.clipboard_chunks import ClipboardChunkAssembler
    asm = ClipboardChunkAssembler(
        sequence_id=3, total_chunks=2, content_type="text/plain",
    )
    assert asm.add(0, "AA") is None
    # Replay of chunk_index=0 — must NOT overwrite with different bytes,
    # must NOT decrement completion count.
    assert asm.add(0, "ZZ") is None
    # Original bytes preserved (idempotent ignore, not overwrite).
    assert asm.add(1, "BB") == "AABB"


def test_assembler_drops_out_of_range_chunk_index():
    """Threat T-03-03 — chunk_index outside [0, total_chunks) is dropped."""
    from common.clipboard_chunks import ClipboardChunkAssembler
    asm = ClipboardChunkAssembler(
        sequence_id=4, total_chunks=2, content_type="text/plain",
    )
    # Negative index → drop.
    assert asm.add(-1, "XX") is None
    # chunk_index == total_chunks (boundary; valid range is [0, total_chunks)) → drop.
    assert asm.add(2, "YY") is None
    # Real chunks still complete normally.
    assert asm.add(0, "AA") is None
    assert asm.add(1, "BB") == "AABB"


def test_assembler_rejects_over_cap_total_chunks_at_init():
    """Threat T-03-26 — total_chunks > MAX_TOTAL_CHUNKS raises at __post_init__."""
    from common.clipboard_chunks import (
        MAX_TOTAL_CHUNKS,
        ClipboardChunkAssembler,
    )
    # Exactly cap is allowed.
    ok = ClipboardChunkAssembler(
        sequence_id=5, total_chunks=MAX_TOTAL_CHUNKS, content_type="image/png",
    )
    assert ok.total_chunks == MAX_TOTAL_CHUNKS
    # One over cap raises.
    with pytest.raises(ValueError):
        ClipboardChunkAssembler(
            sequence_id=6,
            total_chunks=MAX_TOTAL_CHUNKS + 1,
            content_type="image/png",
        )
    # Zero / negative also rejected.
    with pytest.raises(ValueError):
        ClipboardChunkAssembler(
            sequence_id=7, total_chunks=0, content_type="text/plain",
        )


def test_assembler_drops_oversized_chunk():
    """Threat T-03-27 — per-chunk cap MAX_CHUNK_BYTES enforced in add()."""
    from common.clipboard_chunks import (
        MAX_CHUNK_BYTES,
        ClipboardChunkAssembler,
    )
    asm = ClipboardChunkAssembler(
        sequence_id=8, total_chunks=1, content_type="text/plain",
    )
    oversized = "X" * (MAX_CHUNK_BYTES + 1)
    # Oversized drop — no completion, no state change.
    assert asm.add(0, oversized) is None
    # Replacement with a valid-sized chunk still completes the sequence.
    assert asm.add(0, "ok") == "ok"


def test_assembler_is_stale_after_timeout():
    """D-17 — :meth:`ClipboardChunkAssembler.is_stale` trims orphan sequences.

    Stops an attacker from pinning memory by sending chunk 0 of 256 and
    never completing the sequence. Timeout constant lives in the
    assembler module (CHUNK_TIMEOUT_S).
    """
    from common.clipboard_chunks import (
        CHUNK_TIMEOUT_S,
        ClipboardChunkAssembler,
    )
    asm = ClipboardChunkAssembler(
        sequence_id=9, total_chunks=4, content_type="image/png",
    )
    # Pin the started-at to a known value so we don't race the clock.
    asm._started_at = 1000.0
    # Within the window — not stale.
    assert asm.is_stale(now=1000.0) is False
    assert asm.is_stale(now=1000.0 + CHUNK_TIMEOUT_S) is False
    # Past the window — stale. Use >, not >=, per the implementation's contract.
    assert asm.is_stale(now=1000.0 + CHUNK_TIMEOUT_S + 0.001) is True
