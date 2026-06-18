"""Phase 3 D-17 — clipboard chunk reassembler.

Used by both ``client/protocol.py`` (inbound broadcast receive) and
``server/session_runtime.py`` (inbound CLIPBOARD_CHUNK dispatch) to
reassemble base64-encoded chunks from large clipboard payloads —
notably images, which are transported via :class:`ClipboardChunkMsg`
in 1 MB chunks (D-17).

Indexed by ``chunk_index`` (NOT arrival order) so future QUIC multiplexed
delivery and HTTP/2 stream reordering do not corrupt reassembly
(Pitfall 6 — stale-chunk attack family). Returns the full concatenated
base64 string once every ``chunk_index`` in ``[0, total_chunks)`` has
been received; returns None until that boundary.

Security bounds (T-03-03 / T-03-24 / T-03-25 / T-03-26 / T-03-27):

- ``MAX_TOTAL_CHUNKS = 256`` caps attacker-chosen ``total_chunks`` at
  __post_init__; over-cap allocation fails fast with ValueError.
- ``MAX_CHUNK_BYTES = 2 MB`` caps per-chunk base64 payload size; the
  chunker on the send side targets 1 MB, +100% slack covers base64
  expansion (≈33%) plus protocol overhead.
- ``CHUNK_TIMEOUT_S = 30s`` drops incomplete sequences so a peer that
  starts a chunk stream with ``chunk_index=0`` and never completes it
  cannot pin memory. Callers invoke :meth:`is_stale` on every inbound
  chunk to reap orphan assemblers.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


# D-17 constants — tuned per ``.planning/phases/03-display-multi-monitor-
# clipboard/03-CONTEXT.md`` and ``03-RESEARCH.md § Common Pitfalls #6``.
CHUNK_TIMEOUT_S: float = 30.0            # drop incomplete sequences after 30s
MAX_TOTAL_CHUNKS: int = 256              # T-03-03/T-03-26 mitigation: ≈256 MB cap
MAX_CHUNK_BYTES: int = 2 * 1024 * 1024    # 1 MB target + slack for base64 expansion


@dataclass
class ClipboardChunkAssembler:
    """D-17 chunk-reassembly state.

    Indexed by ``chunk_index`` (NOT arrival order) so out-of-order
    delivery over future QUIC multiplexed streams works. Returns the
    full concatenated base64 string once all chunks are present;
    returns None until that boundary.

    Fields on the wire:
      * ``sequence_id`` — per-session monotonic id identifying one
        logical clipboard payload
      * ``total_chunks`` — expected number of chunks for this sequence
      * ``content_type`` — mime type of the reassembled payload

    Internal state:
      * ``_slots`` — chunk_index → base64 string
      * ``_started_at`` — monotonic-clock time the assembler was created,
        used by :meth:`is_stale`
    """

    sequence_id: int
    total_chunks: int
    content_type: str
    _slots: Dict[int, str] = field(default_factory=dict)
    _started_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.total_chunks <= 0 or self.total_chunks > MAX_TOTAL_CHUNKS:
            raise ValueError(
                f"total_chunks {self.total_chunks} out of range "
                f"[1, {MAX_TOTAL_CHUNKS}]"
            )

    def add(self, chunk_index: int, data: str) -> Optional[str]:
        """Append one chunk; return the concatenated base64 string when complete.

        Semantics:
          * out-of-range ``chunk_index`` (negative, or ≥ total_chunks) →
            dropped silently, returns None (Pitfall 6 / T-03-03)
          * duplicate ``chunk_index`` for this sequence → idempotent
            ignore, returns None (Pitfall 6 — never overwrite an already
            stored slot with replay bytes)
          * oversized ``data`` (> MAX_CHUNK_BYTES) → dropped silently,
            returns None (T-03-27 per-chunk cap)
          * otherwise → stored in ``_slots`` under ``chunk_index``. If
            every index in ``[0, total_chunks)`` is now present, return
            the concatenation in index order; else return None.
        """
        if not (0 <= chunk_index < self.total_chunks):
            return None  # out-of-range drop (T-03-03 / Pitfall 6)
        if chunk_index in self._slots:
            return None  # duplicate idempotent ignore (Pitfall 6)
        if len(data) > MAX_CHUNK_BYTES:
            return None  # per-chunk cap (T-03-27)
        self._slots[chunk_index] = data
        if len(self._slots) == self.total_chunks:
            return "".join(self._slots[i] for i in range(self.total_chunks))
        return None

    def is_stale(self, now: Optional[float] = None) -> bool:
        """True when the assembler has outlived CHUNK_TIMEOUT_S without completing.

        Callers iterate the active-assembler dict on every inbound chunk
        and reap stale entries via this predicate (T-03-25 mitigation).
        ``now`` parameter is injectable for deterministic unit tests;
        defaults to :func:`time.monotonic`.
        """
        current = now if now is not None else time.monotonic()
        return (current - self._started_at) > CHUNK_TIMEOUT_S
