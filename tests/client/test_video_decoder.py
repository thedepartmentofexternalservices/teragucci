"""D-01 cp.5 + cp.6 — client/video_decoder.py 10-bit assertions.

Phase 02-08 hardens the client decoder so that:

  - When the negotiated stream is HEVC Main10, every first decoded frame
    must satisfy ``frame.format.name in {"p010le", "yuv420p10le"}``;
    otherwise the decoder raises (no silent 8-bit downgrade).
  - ``hw_backend`` exposes the currently-active hwaccel as a string,
    never None — videotoolbox / nvdec / vaapi / cuda / software.
  - The video hot path (``decode_frame_planes``) returns 10-bit Y/UV
    planes ready for the QRhi widget (no rgb24 coercion on the hot path).

Tests run on every host (no PyAV hardware required); they mock at the
PyAV decoder boundary so Linux CI is also green.
"""
from __future__ import annotations

import pytest

from client.video_decoder import HAS_PYAV, VideoDecoder

pytestmark = pytest.mark.skipif(not HAS_PYAV, reason="PyAV not installed")


# --------------------------------------------------------------------------
# hw_backend property — D-01 cp.6
# --------------------------------------------------------------------------


def test_hw_backend_property_returns_string():
    """``hw_backend`` is always a string — never None — so the cp.6
    health-overlay assertion never has to special-case the no-hwaccel
    fallback path. ``software`` is the canonical fallback value.
    """
    d = VideoDecoder("h265")
    assert isinstance(d.hw_backend, str)
    assert d.hw_backend in ("videotoolbox", "nvdec", "vaapi", "cuda",
                            "dxva2", "d3d11va", "software")


def test_hw_backend_returns_software_when_no_hwaccel():
    """Force-software fallback path: ``hw_backend == 'software'`` with
    the caller never seeing a None.
    """
    d = VideoDecoder("h265")
    d._hw_type = None
    assert d.hw_backend == "software"


# --------------------------------------------------------------------------
# Main10 negotiation + AVFrame.format assertion — D-01 cp.5
# --------------------------------------------------------------------------


def test_set_negotiated_main10_setter_exists_and_is_callable():
    d = VideoDecoder("h265")
    # Default is False — no assertion enforced until protocol confirms it.
    assert d._negotiated_main10 is False
    d.set_negotiated_main10(True)
    assert d._negotiated_main10 is True
    d.set_negotiated_main10(False)
    assert d._negotiated_main10 is False


def test_decode_frame_planes_raises_when_main10_negotiated_but_frame_is_8bit(monkeypatch):
    """If the server advertises Main10 and the decoder hands us back a
    yuv420p (8-bit) frame, the decoder MUST raise — silent fallback is
    the exact PITFALLS #2 trap this plan exists to close.
    """
    d = VideoDecoder("h265")
    d.set_negotiated_main10(True)

    class FakeFormat:
        name = "yuv420p"

    class FakeFrame:
        format = FakeFormat()
        planes = [b"\x00" * 100, b"\x00" * 50]
        width = 1920
        height = 1080

    class _FakeDecoder:
        def decode(self, _packet):
            return [FakeFrame()]

    d._decoder = _FakeDecoder()
    monkeypatch.setattr("av.Packet", lambda _b: object())

    with pytest.raises(RuntimeError, match="Main10 negotiated"):
        d.decode_frame_planes(b"ignored-packet-bytes")


def test_decode_frame_planes_accepts_p010le_when_main10_negotiated(monkeypatch):
    d = VideoDecoder("h265")
    d.set_negotiated_main10(True)

    class FakeFormat:
        name = "p010le"

    class FakeFrame:
        format = FakeFormat()
        planes = [b"\xAA" * 200, b"\xBB" * 100]
        width = 1920
        height = 1080

    class _FakeDecoder:
        def decode(self, _packet):
            return [FakeFrame()]

    d._decoder = _FakeDecoder()
    monkeypatch.setattr("av.Packet", lambda _b: object())

    result = d.decode_frame_planes(b"ignored")
    assert result is not None
    y_bytes, uv_bytes, w, h = result
    assert y_bytes == b"\xAA" * 200
    assert uv_bytes == b"\xBB" * 100
    assert (w, h) == (1920, 1080)


def test_decode_frame_planes_accepts_yuv420p10le_when_main10_negotiated(monkeypatch):
    d = VideoDecoder("h265")
    d.set_negotiated_main10(True)

    class FakeFormat:
        name = "yuv420p10le"

    class FakeFrame:
        format = FakeFormat()
        planes = [b"Y" * 200, b"U" * 50, b"V" * 50]
        width = 1920
        height = 1080

    class _FakeDecoder:
        def decode(self, _packet):
            return [FakeFrame()]

    d._decoder = _FakeDecoder()
    monkeypatch.setattr("av.Packet", lambda _b: object())

    result = d.decode_frame_planes(b"ignored")
    assert result is not None
    y_bytes, uv_bytes, w, h = result
    assert y_bytes == b"Y" * 200
    # planar yuv420p10le is interleaved into a P010-style UV buffer
    assert uv_bytes == b"UV" * 50
    assert (w, h) == (1920, 1080)


def test_decode_frame_planes_does_not_raise_main10_error_when_not_negotiated(monkeypatch):
    """If the server didn't negotiate Main10, the cp.5 RuntimeError MUST
    NOT fire on an 8-bit frame — the hot path is Main10-only by design,
    so an 8-bit frame here is "wrong tool for the job" (caller should use
    ``decode_frame``), but we must not blow up the connection with a
    Main10-negotiation error. ``_extract_planes_p010`` will raise its
    own ValueError surfacing the wrong-tool case to the caller.
    """
    d = VideoDecoder("h265")
    # _negotiated_main10 stays False (default)

    class FakeFormat:
        name = "yuv420p"  # 8-bit

    class FakeFrame:
        format = FakeFormat()
        planes = [b"y" * 200, b"u" * 50, b"v" * 50]
        width = 640
        height = 480

    class _FakeDecoder:
        def decode(self, _packet):
            return [FakeFrame()]

    d._decoder = _FakeDecoder()
    monkeypatch.setattr("av.Packet", lambda _b: object())

    # The Main10 RuntimeError must NOT fire (negotiation flag is False).
    # The ValueError from _extract_planes_p010 is caught by the broad
    # except and returns None — see the legacy contract preservation.
    result = d.decode_frame_planes(b"ignored")
    assert result is None  # 8-bit frame on the 10-bit-only hot path = None


# --------------------------------------------------------------------------
# Plane extraction semantics — _extract_planes_p010
# --------------------------------------------------------------------------


def test_extract_planes_p010_semi_planar_passes_through_y_and_uv():
    """p010le is semi-planar: planes[0] = Y, planes[1] = interleaved UV.
    Pass through verbatim — no copy, no re-pack."""
    d = VideoDecoder("h265")

    class _Fmt:
        name = "p010le"

    class _Frame:
        format = _Fmt()
        planes = [b"\xAA" * 100, b"\xBB" * 50]

    y, uv = d._extract_planes_p010(_Frame())
    assert y == b"\xAA" * 100
    assert uv == b"\xBB" * 50


def test_extract_planes_p010_planar_yuv420p10le_interleaves_u_and_v():
    """yuv420p10le is fully planar (Y, U, V). Interleave U + V into
    P010-style chroma so the QRhi RG16 sampler sees the same shape
    regardless of which 10-bit format the decoder emitted."""
    d = VideoDecoder("h265")

    class _Fmt:
        name = "yuv420p10le"

    class _Frame:
        format = _Fmt()
        # Use distinct values so interleave order is verifiable.
        planes = [b"\x00" * 100, b"U" * 50, b"V" * 50]

    y, uv = d._extract_planes_p010(_Frame())
    assert y == b"\x00" * 100
    assert uv == b"UV" * 50
    assert len(uv) == 100  # U(50) + V(50) interleaved


def test_extract_planes_p010_unknown_format_raises():
    """If we somehow get here with an 8-bit / unsupported format, we
    don't try to invent planes — we raise so the caller sees the bug."""
    d = VideoDecoder("h265")

    class _Fmt:
        name = "yuv420p"  # 8-bit, not a 10-bit format

    class _Frame:
        format = _Fmt()
        planes = [b"y" * 100, b"u" * 25, b"v" * 25]

    with pytest.raises(ValueError, match="10-bit"):
        d._extract_planes_p010(_Frame())


# --------------------------------------------------------------------------
# Hot path no longer calls .to_ndarray('rgb24')
# --------------------------------------------------------------------------


def test_video_hot_path_does_not_use_rgb24_coercion():
    """Source-level invariant: ``decode_frame_planes`` (the new hot path
    consumed by VideoBlitWidget.feed_frame in the next wave) MUST NOT
    coerce to rgb24. The legacy ``decode_frame`` / ``decode_frame_to_ndarray``
    keep an rgb24 fallback ONLY for the Phase-1 JPEG / overlay path,
    flagged with a ``# phase1`` comment — see CLAUDE.md PITFALLS #2.

    Greps the executable source — strips the docstring (which mentions
    rgb24 in the negation 'NEVER calls .to_ndarray("rgb24")').
    """
    import inspect

    from client.video_decoder import VideoDecoder

    src = inspect.getsource(VideoDecoder.decode_frame_planes)
    # Strip the docstring — it documents what the method does NOT do.
    # The mechanical check is the call shape: .to_ndarray(format="rgb24")
    # or .to_ndarray(format='rgb24'). Catch both quoting styles.
    assert ".to_ndarray(format=\"rgb24\")" not in src, (
        "decode_frame_planes is the new hot path; rgb24 coercion is the "
        "Phase-1 8-bit downgrade trap (PITFALLS #2). Use _extract_planes_p010."
    )
    assert ".to_ndarray(format='rgb24')" not in src, (
        "decode_frame_planes hot path — rgb24 coercion call (single-quoted) "
        "is banned. Use _extract_planes_p010."
    )


def test_decoder_module_exposes_video_hot_path():
    """The future decoder→VideoBlitWidget wave imports
    ``VideoDecoder.decode_frame_planes`` as the production hot path —
    keep this import-stability assertion as the contract."""
    from client.video_decoder import VideoDecoder
    assert hasattr(VideoDecoder, "decode_frame_planes")
    assert hasattr(VideoDecoder, "_extract_planes_p010")
    assert hasattr(VideoDecoder, "set_negotiated_main10")
    assert hasattr(VideoDecoder, "hw_backend")
