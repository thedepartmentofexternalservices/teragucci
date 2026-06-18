"""Phase 3 CLIP-01 / D-16 / D-17 — large text clipboard integration.

Plan 03-07 Wave 5 — flips the Wave 0 RED skeletons (Plan 03-01 Task 2)
GREEN. In-process loopback: the client-side chunker in
``ClientProtocol._send_chunked`` produces ``ClipboardChunkMsg`` frames
that the server-side ``ClipboardChunkAssembler`` reassembles, and
symmetrically the server-side chunker in
``SessionRuntime._enqueue_chunked_clipboard`` produces frames the
client-side ``ClientProtocol._handle_clipboard_chunk`` reassembles via
``ClipboardChunkAssembler``. Both paths share the single
``common.clipboard_chunks.ClipboardChunkAssembler`` implementation, so
one loopback round-trip exercises the full client<->server wire.

Covers the PCoIP feature-parity ask: artist pastes a 2 MB Flame render
log into a Slack message and it doesn't corrupt. CRLF / LF / CR all
round-trip byte-equal.
"""
from __future__ import annotations

import hashlib
import json
from typing import List

import pytest

from client.protocol import ClientProtocol
from common.clipboard_chunks import ClipboardChunkAssembler
from common.messages import MsgType


def _capture_protocol_send(prot: ClientProtocol) -> List[dict]:
    """Override ``send_input`` so _send_chunked routes to an in-memory list
    instead of a live websocket. Returns the list (mutated in place)."""
    out: list = []
    prot._connected = True  # trick send_input past the gate; we override below
    prot.send_input = lambda msg: out.append(msg)  # type: ignore[assignment]
    return out


def _reassemble_chunks(chunks: List[dict]) -> bytes:
    """Feed a list of CLIPBOARD_CHUNK dicts through the shared assembler
    and return the reassembled raw bytes (or b'' if incomplete)."""
    if not chunks:
        return b""
    # All chunks share the same sequence_id + total_chunks.
    seq = int(chunks[0]["sequence_id"])
    total = int(chunks[0]["total_chunks"])
    content_type = chunks[0].get("content_type", "text/plain")
    asm = ClipboardChunkAssembler(
        sequence_id=seq,
        total_chunks=total,
        content_type=content_type,
    )
    full_b64 = None
    for c in chunks:
        res = asm.add(int(c["chunk_index"]), c.get("data", ""))
        if res is not None:
            full_b64 = res
    if full_b64 is None:
        return b""
    if content_type == "text/plain":
        # text/plain payloads go over the wire as UTF-8 text, NOT base64;
        # _send_chunked for text just slices the string. Return as bytes.
        return full_b64.encode("utf-8")
    # image/png (and future binary types) are base64-wrapped.
    import base64
    return base64.b64decode(full_b64)


def test_large_text_round_trip_byte_equal():
    """CLIP-01 — >1 MB UTF-8 text survives chunked round-trip byte-equal.

    Build a 2.5 MB UTF-8 string with mixed ASCII + multi-byte characters
    (emoji, CJK) and run it through the client chunker -> shared
    assembler -> UTF-8 decode. Assert sha256(in) == sha256(out).
    """
    prot = ClientProtocol()
    sent = _capture_protocol_send(prot)

    # 2.5 MB of mixed content — ASCII + CJK + emoji so multi-byte
    # boundary handling is tested.
    unit = "The quick brown fox jumps over the lazy dog. 犬が走る。🦊\n"
    text_in = unit * (2_500_000 // len(unit.encode("utf-8")) + 1)
    assert len(text_in.encode("utf-8")) > 1 * 1024 * 1024

    prot.send_clipboard("text/plain", text_in)

    # Must have chunked (>= 2 chunks)
    chunk_msgs = [m for m in sent if m.get("type") == MsgType.CLIPBOARD_CHUNK]
    assert len(chunk_msgs) >= 2
    # All chunks share the same sequence_id and content_type
    seqs = {m["sequence_id"] for m in chunk_msgs}
    assert len(seqs) == 1
    assert all(m["content_type"] == "text/plain" for m in chunk_msgs)

    reassembled = _reassemble_chunks(chunk_msgs)
    text_out = reassembled.decode("utf-8")

    assert len(text_out) == len(text_in)
    assert hashlib.sha256(text_in.encode("utf-8")).hexdigest() == \
        hashlib.sha256(text_out.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("newline", ["\r\n", "\n", "\r"])
def test_newline_encoding_preserved(newline):
    """D-16 — sender's native newline encoding (CRLF / LF / CR) round-trips verbatim.

    Build a >1 MB text payload using a specific newline convention and
    verify the newline bytes survive the chunk round-trip exactly.
    """
    prot = ClientProtocol()
    sent = _capture_protocol_send(prot)

    # Build >1 MB with a given newline
    line = "render pass " + ("x" * 80) + newline
    line_bytes = len(line.encode("utf-8"))
    text_in = line * ((1_100_000 // line_bytes) + 1)
    assert len(text_in.encode("utf-8")) > 1 * 1024 * 1024

    prot.send_clipboard("text/plain", text_in)

    chunk_msgs = [m for m in sent if m.get("type") == MsgType.CLIPBOARD_CHUNK]
    assert len(chunk_msgs) >= 2

    reassembled = _reassemble_chunks(chunk_msgs)
    text_out = reassembled.decode("utf-8")

    # Exact byte equality preserves newline convention
    assert text_out == text_in
    # Belt and suspenders: the specific newline is still present
    assert newline in text_out


def test_small_text_uses_one_shot_path():
    """CLIP-01 — <=1MB text takes the 1-shot CLIPBOARD_SEND path, not chunked."""
    prot = ClientProtocol()
    sent = _capture_protocol_send(prot)

    prot.send_clipboard("text/plain", "hello world")

    assert len(sent) == 1
    assert sent[0]["type"] == MsgType.CLIPBOARD_SEND
    assert sent[0]["content_type"] == "text/plain"
    assert sent[0]["data"] == "hello world"


def test_text_c2s_gate_blocks_outbound():
    """D-15 — text_c2s False short-circuits before any wire send."""
    prot = ClientProtocol()
    sent = _capture_protocol_send(prot)
    prot.set_clipboard_toggles(
        text_c2s=False, text_s2c=True,
        image_c2s=True, image_s2c=True,
    )

    # Even a >1MB payload must not emit a single chunk.
    big = "x" * (2 * 1024 * 1024)
    prot.send_clipboard("text/plain", big)

    assert sent == []


def test_inbound_chunked_text_reassembles_byte_equal():
    """D-17 — inbound CLIPBOARD_CHUNK stream reassembles via
    ``_handle_clipboard_chunk`` and calls ``on_clipboard`` with the
    content_type + text payload."""
    prot = ClientProtocol()
    received: list = []
    prot.on_clipboard = lambda ct, data: received.append((ct, data))

    text_in = ("flame-log-line " * 100_000) + "\r\n" + ("x" * 100_000)

    # Produce chunks the same way the server would (slice in 1MB pieces).
    from client.protocol import CHUNK_BYTES
    total = max(1, (len(text_in) + CHUNK_BYTES - 1) // CHUNK_BYTES)
    for i in range(total):
        chunk = text_in[i * CHUNK_BYTES : (i + 1) * CHUNK_BYTES]
        prot._handle_clipboard_chunk({
            "type": MsgType.CLIPBOARD_CHUNK,
            "sequence_id": 42,
            "chunk_index": i,
            "total_chunks": total,
            "content_type": "text/plain",
            "data": chunk,
        })

    assert len(received) == 1
    ct, data = received[0]
    assert ct == "text/plain"
    assert data == text_in
