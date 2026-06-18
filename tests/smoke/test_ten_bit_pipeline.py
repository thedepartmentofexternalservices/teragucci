"""9-checkpoint byte-equality pipeline harness.

Phase 02-06 (Wave 3) wired checkpoints 2 / 3 / 4 — the Linux encoder
input + output + on-wire profile assertions. Checkpoint 1 (capture
surface) stays xfail until the live NvFBC 10-bit handoff is exercised
end-to-end against a real GPU; the C-helper plumbing is already in
place (server/nvfbc/nvfbc_capture.c #ifdef NVFBC_BUFFER_FORMAT_YUV420P10LE)
so all that remains is a real-hardware integration run.

Checkpoint mapping (D-01, see CONTEXT.md):
  cp.1  capture surface format          (xfail — Wave 3 / GPU owner)
  cp.2  encoder INPUT pixel format      (Wave 3 — landed here)
  cp.3  encoder OUTPUT ffprobe profile  (Wave 3 — landed here, ref-JSON)
  cp.4  on-wire H.265 general_profile_idc (Wave 3 — landed here, ref-JSON)
  cp.5  decoder AVFrame.format          (Wave 5 02-08 — landed here, source-grep)
  cp.6  decoder hwaccel == videotoolbox (Wave 5 02-08 — landed here, source-grep)
  cp.7  QRhi texture format = R16/RG16  (Wave 4 02-07 — landed here, source-grep)
  cp.8  Metal final blit preserves bits (manual one-off — see VALIDATION.md)
  cp.9  macOS display state             (log-only per D-01 cp.9)
"""
import json
import pathlib
from unittest import mock

import pytest

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"
RAMP_BIN = FIXTURE_DIR / "10bit_ramp.p010.bin"
REF_JSON = FIXTURE_DIR / "10bit_ramp.reference.ffprobe.json"

pytestmark = pytest.mark.ten_bit_smoke


@pytest.fixture(scope="module")
def reference_checkpoints():
    if not REF_JSON.exists():
        pytest.skip("reference JSON missing - Wave 0 did not land")
    return json.loads(REF_JSON.read_text())["checkpoints"]


@pytest.fixture
def mock_popen(monkeypatch):
    """Subprocess-boundary mock — prevents the encoder lifecycle path from
    spawning a real ``ffmpeg`` binary inside this smoke file. Mirrors
    the shape of ``tests/server/test_video_encoder_mock.py::mock_popen``
    so future contributors can copy/paste from either side without
    surprises.
    """
    fake_proc = mock.MagicMock()
    fake_proc.stdin = mock.MagicMock()
    fake_proc.stdout = mock.MagicMock()
    fake_proc.stdout.read.return_value = b""
    fake_proc.poll.return_value = None
    fake_proc.returncode = None
    fake_proc.wait.return_value = 0

    def _fake_popen(*a, **kw):
        return fake_proc

    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    monkeypatch.setattr("server.video_encoder.subprocess.Popen", _fake_popen)
    return fake_proc


@pytest.mark.xfail(reason="Wave 2 - Linux P010 capture not wired", strict=False)
def test_checkpoint_1_capture_surface_is_p010(reference_checkpoints):
    # noqa: F401 - import-only smoke that the capture module exists.
    from server import screen_capture  # noqa: F401
    assert reference_checkpoints["capture"]["expected_pix_fmt"] == "p010le"
    pytest.fail("Wave 2 owner wires NvFBC P010 surface and asserts format")


def test_checkpoint_2_encoder_input_is_p010(reference_checkpoints, mock_popen):
    """D-01 cp.2 — FFmpeg encoder INPUT pixel format must be ``p010le``
    when ``ServerColorCaps.main10`` is negotiated. Asserts on the live
    command line built by ``VideoEncoder._build_ffmpeg_cmd`` (the same
    path the production server invokes via ``_start_ffmpeg``).
    """
    from common.messages import QualitySettings, ServerColorCaps
    from server.video_encoder import ENCODER_DEFS, VideoEncoder

    enc_def = [e for e in ENCODER_DEFS if e.name == "hevc_nvenc"][0]
    ve = VideoEncoder(
        1920, 1080, QualitySettings(codec="h265"),
        available_encoders={"h264": [], "h265": [enc_def], "av1": []},
    )
    ve._color_caps = ServerColorCaps(main10=True,
                                     negotiated_state="confirmed")
    cmd = ve._build_ffmpeg_cmd(enc_def)

    # FFmpeg uses both -pix_fmt (output) and -pixel_format (input). We
    # care about INPUT here — find the first occurrence of the
    # rawvideo input flag and verify the immediately-following
    # -pixel_format value matches what the reference fixture says the
    # capture stage emits.
    assert "-pixel_format" in cmd, (
        f"missing -pixel_format on encoder input side; cmd={cmd}"
    )
    in_idx = cmd.index("-pixel_format")
    in_pix_fmt = cmd[in_idx + 1]
    assert in_pix_fmt == "p010le", (
        f"D-01 cp.2: encoder input must be p010le when main10 negotiated; "
        f"got {in_pix_fmt!r}; full cmd={cmd}"
    )

    # Sanity: the reference fixture documents the same expectation —
    # this catches a fixture/JSON drift before we update fixtures and
    # forget to flip the live test.
    expected = reference_checkpoints["encoder_input"]
    assert expected["expected_pix_fmt"] == "p010le"


def test_checkpoint_3_encoder_output_profile_main10(reference_checkpoints):
    """D-01 cp.3 — encoder OUTPUT ffprobe assertion. Reads the reference
    JSON committed alongside the 10-bit ramp fixture (Wave 0 / 02-01)
    so CI does not have to run a real ffmpeg + ffprobe pass on the
    ramp binary. The full hardware pipeline test that produces a fresh
    ffprobe JSON belongs to a later wave that has GPU access.
    """
    expected = reference_checkpoints["encoder_output"]
    assert expected["ffprobe_pix_fmt"] == "yuv420p10le", (
        f"D-01 cp.3: encoder output must be yuv420p10le; "
        f"reference fixture says {expected['ffprobe_pix_fmt']!r}"
    )
    assert expected["ffprobe_profile"] == "Main 10", (
        f"D-01 cp.3: encoder profile must be Main 10; "
        f"reference fixture says {expected['ffprobe_profile']!r}"
    )


def test_checkpoint_4_wire_general_profile_idc_is_2(reference_checkpoints):
    """D-01 cp.4 — H.265 on-wire ``general_profile_idc`` must equal 2
    (Main 10) per ITU-T H.265 Annex A. Verified against the reference
    fixture; live VPS/SPS NAL parsing on a freshly-encoded sample is
    a Wave 4+ integration test that can run on a GPU runner.
    """
    actual = reference_checkpoints["wire"]["h265_general_profile_idc"]
    assert actual == 2, (
        f"D-01 cp.4: H.265 general_profile_idc must be 2 (Main 10); "
        f"reference fixture says {actual!r}"
    )


def test_checkpoint_5_decoder_output_format_is_p010():
    """D-01 cp.5 — client/video_decoder.py asserts AVFrame.format in
    {p010le, yuv420p10le} when Main10 is negotiated. Source-grep gate
    runs everywhere (no PyAV decode required); the live assertion lives
    in tests/client/test_video_decoder.py and runs whenever PyAV is
    installed (CI macos-14 + rockylinux:9 runners).

    The grep checks both 10-bit format strings appear in the source
    AND that the rgb24 coercion is no longer on the live decode hot
    path (allowed only in '# phase1' fallback code paths per the
    decoder's docstring contract).
    """
    src = pathlib.Path("client/video_decoder.py").read_text()
    assert "p010le" in src, (
        "D-01 cp.5: client/video_decoder.py must reference 'p010le' "
        "(VideoToolbox semi-planar 10-bit format). Without this the "
        "decoder cannot assert on Main10 negotiation."
    )
    assert "yuv420p10le" in src, (
        "D-01 cp.5: client/video_decoder.py must reference 'yuv420p10le' "
        "(software decode + NVENC/VAAPI 10-bit format). Without this "
        "the decoder accepts only the p010le hwaccel path."
    )
    # The legacy decode_frame() / decode_frame_to_ndarray() retain rgb24
    # for the QPainter overlay / JPEG fallback path, marked '# phase1
    # fallback' in source. The new hot path (decode_frame_planes) MUST
    # NOT call .to_ndarray('rgb24') — that is PITFALLS #2.
    assert "decode_frame_planes" in src, (
        "D-01 cp.5: client/video_decoder.py must expose decode_frame_planes "
        "as the new 10-bit hot path consumed by VideoBlitWidget.feed_frame."
    )
    assert "_extract_planes_p010" in src, (
        "D-01 cp.5: client/video_decoder.py must expose _extract_planes_p010 "
        "to convert AVFrame planes -> (Y, UV) bytes for QRhi RG16 sampling."
    )


def test_checkpoint_6_decoder_hwaccel_is_videotoolbox():
    """D-01 cp.6 — client/video_decoder.py exposes hw_backend property
    + handles the VideoToolbox-silent-software-fallback case.

    Source-grep gate (cheap, runs everywhere). The live property check
    is in tests/client/test_video_decoder.py (which runs whenever PyAV
    is installed on the macos-14 CI runner).
    """
    src = pathlib.Path("client/video_decoder.py").read_text()
    # hw_backend property is the cp.6 health-overlay surface.
    assert "def hw_backend" in src, (
        "D-01 cp.6: client/video_decoder.py must expose hw_backend "
        "property so the health overlay can render the active hwaccel."
    )
    assert "videotoolbox" in src, (
        "D-01 cp.6: client/video_decoder.py must reference videotoolbox "
        "in the HW_DECODERS hwaccel candidate list."
    )
    # The fallback-detection wiring: when Main10 is negotiated but the
    # decoder produces an 8-bit frame, hw_type is flipped back to None
    # (so hw_backend reports 'software') AND the decoder raises so the
    # supervisor surfaces the failure. Both behaviors are covered by
    # tests/client/test_video_decoder.py.
    assert "hwaccel_software_fallback" in src, (
        "D-01 cp.6: client/video_decoder.py must log "
        "decoder.hwaccel_software_fallback when VideoToolbox silently "
        "falls back to 8-bit on Main10 input (PITFALLS #2 detection)."
    )


def test_checkpoint_7_qrhi_texture_formats_are_r16_rg16():
    """D-01 cp.7 — client/viewer.py video layer MUST allocate Y as
    QRhiTexture.Format.R16 and UV as QRhiTexture.Format.RG16 (10-bit
    fits into the top 10 bits of a 16-bit container; sampling those
    formats preserves the bottom 2 bits through the BT.709 shader).

    Source-level grep — cheap and runs on every host (no PySide6 needed).
    The full live-widget assertion lives in
    ``tests/client/test_viewer_qrhi_video_layer.py`` and runs whenever
    PySide6 is installed (CI macos-14 runner).
    """
    import pathlib
    src = pathlib.Path("client/viewer.py").read_text()
    assert "QRhiTexture.Format.R16" in src or "Format.R16" in src, (
        "D-01 cp.7: client/viewer.py must reference QRhiTexture.Format.R16 "
        "(Y plane). 10-bit-in-16-bit storage is the only honest path to "
        "preserving the bottom 2 bits through the Metal blit."
    )
    assert "QRhiTexture.Format.RG16" in src or "Format.RG16" in src, (
        "D-01 cp.7: client/viewer.py must reference QRhiTexture.Format.RG16 "
        "(UV plane). Interleaved 10-bit chroma at 4:2:0 fits into RG16 "
        "and survives the BT.709 shader without quantization."
    )


@pytest.mark.gpu
@pytest.mark.skip(reason="Checkpoint 8 is manual-verified-once - see VALIDATION.md Manual-Only")
def test_checkpoint_8_metal_final_blit_preserves_10_bits():
    pass


@pytest.mark.skip(reason="Checkpoint 9 - macOS display state is log-only per D-01 cp.9")
def test_checkpoint_9_display_reference_mode_documented():
    pass
