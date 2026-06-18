"""Phase 3 CLIP-02 / D-13 / D-14 / D-16 / D-17 — PNG image clipboard integration.

Plan 03-07 Wave 5 — flips the Wave 0 RED skeletons (Plan 03-01 Task 2)
GREEN. In-process loopback exercises the PNG round-trip:
  - Client ``send_clipboard("image/png", bytes)`` produces
    ``ClipboardChunkMsg`` frames with base64-encoded payload.
  - Shared ``ClipboardChunkAssembler`` reassembles the b64 string.
  - base64-decode recovers the original PNG bytes.
  - ``sha256(in) == sha256(out)`` and the first 8 bytes match PNG_MAGIC.

Covers:
  - tiny RGBA PNG (fixture_png): sha256 round-trip + alpha preservation
    via byte-equality.
  - generated >1 MB PNG: chunked transport reassembles correctly.
  - oversize (>64MB) PNG: triggers ``on_oversize_image`` BEFORE any
    chunk ships; D-14 toast contract.
  - image_c2s False: short-circuits before wire send.
  - image_s2c False at chunk-0 boundary: drops sequence + continuation
    via _dropped_seqs (Pitfall 7).
  - W-6 mid-stream cancellation: per-chunk c2s re-check breaks loop.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
from typing import List

import pytest

from client.protocol import ClientProtocol, PNG_MAGIC, PNG_MAX_BYTES
from common.clipboard_chunks import ClipboardChunkAssembler
from common.messages import MsgType


def _capture_protocol_send(prot: ClientProtocol) -> List[dict]:
    out: list = []
    prot._connected = True
    prot.send_input = lambda msg: out.append(msg)  # type: ignore[assignment]
    return out


def _reassemble_image_chunks(chunks: List[dict]) -> bytes:
    """Feed image chunks through the shared assembler; return raw PNG bytes."""
    if not chunks:
        return b""
    seq = int(chunks[0]["sequence_id"])
    total = int(chunks[0]["total_chunks"])
    asm = ClipboardChunkAssembler(
        sequence_id=seq, total_chunks=total, content_type="image/png",
    )
    full_b64 = None
    for c in chunks:
        res = asm.add(int(c["chunk_index"]), c.get("data", ""))
        if res is not None:
            full_b64 = res
    assert full_b64 is not None, "Chunks did not reassemble"
    return base64.b64decode(full_b64)


def _generate_rgba_png(width: int, height: int, noisy: bool = False) -> bytes:
    """Generate a valid PIL-verify-clean RGBA PNG of given dimensions.

    ``noisy=False`` renders a deterministic gradient (tiny PNG, great
    for the alpha-preservation check). ``noisy=True`` renders pseudo-
    random pixels seeded deterministically so the PNG does not compress
    well — used by the chunked-transport test to force a payload that
    chunks into multiple frames.
    """
    PIL = pytest.importorskip("PIL.Image")
    img = PIL.new("RGBA", (width, height))
    px = img.load()
    if noisy:
        import random
        rng = random.Random(42)
        for y in range(height):
            for x in range(width):
                px[x, y] = (
                    rng.randint(0, 255),
                    rng.randint(0, 255),
                    rng.randint(0, 255),
                    rng.randint(0, 255),
                )
    else:
        for y in range(height):
            for x in range(width):
                r = x * 255 // max(1, width - 1)
                g = y * 255 // max(1, height - 1)
                b = (x + y) * 255 // max(1, width + height - 2)
                a = (x * y * 255) // max(1, (width - 1) * (height - 1))
                px[x, y] = (r, g, b, a)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_png_round_trip_sha256_match(fixture_png):
    """CLIP-02 — sha256(in) == sha256(out) after full client↔server round-trip."""
    prot = ClientProtocol()
    sent = _capture_protocol_send(prot)

    prot.send_clipboard("image/png", fixture_png)

    chunk_msgs = [m for m in sent if m.get("type") == MsgType.CLIPBOARD_CHUNK]
    assert len(chunk_msgs) >= 1
    assert all(m["content_type"] == "image/png" for m in chunk_msgs)

    raw = _reassemble_image_chunks(chunk_msgs)
    assert raw[:8] == PNG_MAGIC
    assert hashlib.sha256(fixture_png).hexdigest() == \
        hashlib.sha256(raw).hexdigest()


def test_png_alpha_channel_preserved():
    """D-13 — RGBA PNG (alpha channel) survives round-trip intact."""
    png_in = _generate_rgba_png(32, 32)
    assert png_in[:8] == PNG_MAGIC

    prot = ClientProtocol()
    sent = _capture_protocol_send(prot)
    prot.send_clipboard("image/png", png_in)

    chunk_msgs = [m for m in sent if m.get("type") == MsgType.CLIPBOARD_CHUNK]
    raw = _reassemble_image_chunks(chunk_msgs)
    assert hashlib.sha256(png_in).hexdigest() == hashlib.sha256(raw).hexdigest()

    # Decode and verify the alpha channel survived.
    PIL = pytest.importorskip("PIL.Image")
    img_in = PIL.open(io.BytesIO(png_in))
    img_out = PIL.open(io.BytesIO(raw))
    assert img_in.mode == "RGBA"
    assert img_out.mode == "RGBA"
    # Compare a sample of alpha values
    px_in = img_in.load()
    px_out = img_out.load()
    for (x, y) in [(0, 0), (15, 15), (31, 31), (7, 23), (23, 7)]:
        assert px_in[x, y] == px_out[x, y], f"Pixel mismatch at ({x}, {y})"


def test_png_chunked_transport_reassembles():
    """D-17 — >1 MB PNG splits into ClipboardChunkMsg frames + reassembles correctly."""
    # Generate a noise-filled PNG so compression doesn't shrink the wire
    # size below the chunk threshold. 800×800 RGBA random pixels yields
    # ~2+ MB after PNG encode which guarantees >=2 chunks post-base64.
    png_in = _generate_rgba_png(800, 800, noisy=True)
    # After base64 the chunker operates on the b64 string which grows
    # by ~33%; ensure the b64 size exceeds 1 chunk.
    assert len(base64.b64encode(png_in)) > 1_048_576

    prot = ClientProtocol()
    sent = _capture_protocol_send(prot)
    prot.send_clipboard("image/png", png_in)

    chunk_msgs = [m for m in sent if m.get("type") == MsgType.CLIPBOARD_CHUNK]
    # Must produce multiple chunks
    assert len(chunk_msgs) >= 2
    # All share a single sequence_id and a matching total_chunks
    seqs = {m["sequence_id"] for m in chunk_msgs}
    assert len(seqs) == 1
    totals = {m["total_chunks"] for m in chunk_msgs}
    assert len(totals) == 1
    assert totals.pop() == len(chunk_msgs)

    raw = _reassemble_image_chunks(chunk_msgs)
    assert hashlib.sha256(png_in).hexdigest() == hashlib.sha256(raw).hexdigest()


def test_image_c2s_gate_short_circuits_before_wire():
    """D-15 — ``clipboard_image_c2s=False`` blocks outbound BEFORE chunk dispatch."""
    prot = ClientProtocol()
    sent = _capture_protocol_send(prot)
    prot.set_clipboard_toggles(
        text_c2s=True, text_s2c=True,
        image_c2s=False, image_s2c=True,
    )

    png_in = _generate_rgba_png(64, 64)
    prot.send_clipboard("image/png", png_in)

    # Zero wire traffic
    assert sent == []


def test_oversize_png_triggers_toast_before_chunks(caplog):
    """D-14 — image > 64MB invokes ``on_oversize_image`` and emits zero chunks."""
    prot = ClientProtocol()
    sent = _capture_protocol_send(prot)

    toast_calls: list = []
    prot.on_oversize_image = lambda size_mb: toast_calls.append(size_mb)

    # Fake oversize: valid magic + length > PNG_MAX_BYTES
    oversize = PNG_MAGIC + b"A" * (PNG_MAX_BYTES + 1)
    with caplog.at_level(logging.WARNING):
        prot.send_clipboard("image/png", oversize)

    assert len(toast_calls) == 1
    assert toast_calls[0] > 64  # size reported in MB, must exceed cap
    # No wire traffic
    assert sent == []
    # Telemetry: the oversize event was logged
    assert any("clipboard.image_oversize" in r.getMessage() for r in caplog.records)


def test_bad_magic_png_rejected():
    """D-16 — non-PNG bytes with no magic signature are dropped silently."""
    prot = ClientProtocol()
    sent = _capture_protocol_send(prot)
    prot.send_clipboard("image/png", b"GIF89a\x00\x00\x00corrupted")

    assert sent == []


def test_w6_midstream_cancellation_emits_telemetry(caplog):
    """W-6 Pitfall 7 — flipping image_c2s mid-stream stops chunk dispatch.

    Stage a 3-chunk send; flip the c2s toggle AFTER the first chunk
    lands on the wire, then assert the loop breaks and the
    ``clipboard.chunk_send_cancelled_mid_stream`` event is logged.
    """
    prot = ClientProtocol()
    sent: list = []
    prot._connected = True

    # Flip the toggle off the instant chunk 0 arrives in `sent`.
    def spy_send(msg):
        sent.append(msg)
        # After the first chunk lands, disable image c2s.
        if len(sent) == 1:
            prot._clipboard_image_c2s = False

    prot.send_input = spy_send  # type: ignore[assignment]

    # Generate a noise PNG that chunks into at least 2 frames after b64.
    big_png = _generate_rgba_png(800, 800, noisy=True)
    assert len(base64.b64encode(big_png)) > 1_048_576

    with caplog.at_level(logging.INFO):
        prot.send_clipboard("image/png", big_png)

    # Exactly 1 chunk made it onto the wire before the cancellation
    # kicked in (per-chunk re-check runs at loop top so chunk 0 goes,
    # then the i=1 iteration sees c2s False and breaks).
    chunk_msgs = [m for m in sent if m.get("type") == MsgType.CLIPBOARD_CHUNK]
    assert len(chunk_msgs) == 1
    assert chunk_msgs[0]["chunk_index"] == 0

    # Telemetry event fired
    assert any(
        "clipboard.chunk_send_cancelled_mid_stream" in r.getMessage()
        for r in caplog.records
    )


def test_inbound_image_chunk_s2c_gate_at_chunk_zero():
    """D-15 / Pitfall 7 — ``image_s2c=False`` drops the sequence at chunk 0,
    and subsequent chunks for the SAME sequence_id are silently no-opped
    via ``_dropped_seqs`` continuation tracking."""
    prot = ClientProtocol()
    prot.set_clipboard_toggles(
        text_c2s=True, text_s2c=True,
        image_c2s=True, image_s2c=False,
    )
    received: list = []
    prot.on_clipboard = lambda ct, d: received.append((ct, d))

    # 3 chunks, all tagged image/png. Chunk 0 triggers the drop decision.
    for i in range(3):
        prot._handle_clipboard_chunk({
            "type": MsgType.CLIPBOARD_CHUNK,
            "sequence_id": 77,
            "chunk_index": i,
            "total_chunks": 3,
            "content_type": "image/png",
            "data": "aaaa",
        })

    # Callback never fires; sequence id is recorded in _dropped_seqs.
    assert received == []
    assert 77 in prot._dropped_seqs


def test_inbound_image_chunk_round_trip_calls_on_clipboard(fixture_png):
    """End-to-end client inbound: CLIPBOARD_CHUNK stream → on_clipboard(
    'image/png', raw_bytes) where raw_bytes passes D-16 re-validation."""
    prot = ClientProtocol()
    received: list = []
    prot.on_clipboard = lambda ct, d: received.append((ct, d))

    data_b64 = base64.b64encode(fixture_png).decode("ascii")
    prot._handle_clipboard_chunk({
        "type": MsgType.CLIPBOARD_CHUNK,
        "sequence_id": 91,
        "chunk_index": 0,
        "total_chunks": 1,
        "content_type": "image/png",
        "data": data_b64,
    })

    assert len(received) == 1
    ct, raw = received[0]
    assert ct == "image/png"
    assert raw[:8] == PNG_MAGIC
    assert hashlib.sha256(raw).hexdigest() == hashlib.sha256(fixture_png).hexdigest()
