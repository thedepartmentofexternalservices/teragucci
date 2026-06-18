"""D-04 / VIDEO-04 - mac_video_encoder.py with VTCompressionSession mocked.

Mirrors tests/server/test_video_encoder_mock.py D-02 mock-at-subprocess
boundary, except the boundary is VideoToolbox (PyObjC) rather than ffmpeg
subprocess. The module-under-test (server.mac_video_encoder) ships in
plan 02-05 (Wave 2).

Approach: monkeypatch the deferred PyObjC symbols (VT, CM, CV) and the
``_HAS_VT`` import-guard flag on the server.mac_video_encoder module so
the encoder constructs without a real VideoToolbox runtime. The five
tests assert the constructor + start() + request_keyframe() + stop() +
property-set sequence are wired correctly without exercising real VT.
"""
from __future__ import annotations

from unittest import mock

import pytest


class _FakeSession:
    """Sentinel returned in place of a real VTCompressionSessionRef."""


@pytest.fixture
def mock_vt(monkeypatch):
    """Replace VT / CM / CV PyObjC bindings on server.mac_video_encoder.

    The deferred-import block in mac_video_encoder.py either imports the
    real frameworks (on a Mac with PyObjC installed) or falls through with
    ``_HAS_VT = False``. On Linux CI we land in the second branch, so the
    module attributes ``VT``, ``CM``, ``CV`` do not exist. We create the
    fakes here, attach them to the module, and force ``_HAS_VT = True``
    so the constructor's ``_check_vt_available`` guard does not raise.
    """
    import server.mac_video_encoder as mve

    fake_VT = mock.MagicMock(name="VideoToolbox")
    # VTCompressionSessionCreate signature in PyObjC returns
    # (status, sessionOut). Tests only need a non-None session sentinel.
    fake_VT.VTCompressionSessionCreate = mock.MagicMock(
        return_value=(0, _FakeSession())
    )
    fake_VT.VTSessionSetProperty = mock.MagicMock(return_value=0)
    fake_VT.VTCompressionSessionEncodeFrame = mock.MagicMock(return_value=0)
    fake_VT.VTCompressionSessionCompleteFrames = mock.MagicMock(return_value=0)
    fake_VT.VTCompressionSessionInvalidate = mock.MagicMock(return_value=0)
    # Constants — string sentinels are fine since the production code only
    # passes them through; it never compares to a real CFString.
    fake_VT.kVTVideoEncoderSpecification_EnableLowLatencyRateControl = (
        "EnableLowLatencyRateControl"
    )
    fake_VT.kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder = (
        "EnableHardwareAcceleratedVideoEncoder"
    )
    fake_VT.kVTCompressionPropertyKey_ProfileLevel = "ProfileLevel"
    fake_VT.kVTProfileLevel_HEVC_Main10_AutoLevel = "HEVC_Main10_AutoLevel"
    fake_VT.kVTProfileLevel_HEVC_Main42210_AutoLevel = "HEVC_Main42210_AutoLevel"
    fake_VT.kVTCompressionPropertyKey_RealTime = "RealTime"
    fake_VT.kVTCompressionPropertyKey_AllowFrameReordering = "AllowFrameReordering"
    fake_VT.kVTCompressionPropertyKey_ExpectedFrameRate = "ExpectedFrameRate"
    fake_VT.kVTCompressionPropertyKey_AverageBitRate = "AverageBitRate"
    fake_VT.kVTCompressionPropertyKey_MaxKeyFrameInterval = "MaxKeyFrameInterval"
    monkeypatch.setattr(mve, "VT", fake_VT, raising=False)
    monkeypatch.setattr(mve, "_HAS_VT", True, raising=False)

    fake_CM = mock.MagicMock(name="CoreMedia")
    fake_CM.kCMVideoCodecType_HEVC = "HEVC"
    fake_CM.CMTimeMake = mock.MagicMock(
        side_effect=lambda v, ts: mock.MagicMock(value=v, timescale=ts)
    )
    # kCMTimeInvalid is consumed by stop() via VTCompressionSessionCompleteFrames.
    fake_CM.kCMTimeInvalid = mock.MagicMock(name="kCMTimeInvalid")
    monkeypatch.setattr(mve, "CM", fake_CM, raising=False)

    fake_CV = mock.MagicMock(name="CoreVideo")
    fake_CV.kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange = "P010"
    fake_CV.CVPixelBufferCreate = mock.MagicMock(
        return_value=(0, mock.MagicMock(name="CVPixelBufferRef"))
    )
    monkeypatch.setattr(mve, "CV", fake_CV, raising=False)

    return {"VT": fake_VT, "CM": fake_CM, "CV": fake_CV}


class _Settings:
    """Minimal QualitySettings stand-in.

    The real QualitySettings dataclass in common/messages.py is overkill
    for these tests; mac_video_encoder reads only the three attrs below
    via ``getattr(settings, name, default)`` so a plain class works.
    """
    target_bitrate_bps = 30_000_000
    fps = 60
    enable_422 = False


def test_encoder_instantiates_without_real_vt(mock_vt):
    """Constructor must not require a live VTCompressionSession."""
    from server.mac_video_encoder import MacVideoEncoder

    enc = MacVideoEncoder(1920, 1080, _Settings())
    assert enc is not None
    # Construction is cheap — no VT calls fired yet.
    mock_vt["VT"].VTCompressionSessionCreate.assert_not_called()


def test_encoder_start_creates_vt_session_with_low_latency(mock_vt):
    """start() invokes VTCompressionSessionCreate with low-latency RC enabled.

    D-04: kVTVideoEncoderSpecification_EnableLowLatencyRateControl is the
    single most important spec key — without it VT runs the legacy rate
    controller and silently drops the 5-8ms/frame WWDC21 promise.
    """
    from server.mac_video_encoder import MacVideoEncoder

    enc = MacVideoEncoder(1920, 1080, _Settings())
    enc.start(on_encoded_frame=lambda *a: None)

    create = mock_vt["VT"].VTCompressionSessionCreate
    create.assert_called_once()
    args, kwargs = create.call_args
    # encoderSpecification is the 5th positional arg per the PyObjC
    # binding (allocator, width, height, codecType, encoderSpecification, ...).
    encoder_spec = args[4] if len(args) > 4 else kwargs.get("encoderSpecification")
    assert encoder_spec is not None, (
        f"encoder_spec missing from call: args={args} kwargs={kwargs}"
    )
    assert "EnableLowLatencyRateControl" in encoder_spec, (
        f"encoder_spec missing low-latency key: {encoder_spec}"
    )
    assert encoder_spec["EnableLowLatencyRateControl"] is True


def test_encoder_start_sets_main10_profile_and_disallows_frame_reordering(mock_vt):
    """start() configures HEVC Main10 + AllowFrameReordering=False.

    Frame reordering MUST stay False — B-frames break the one-in-one-out
    pipeline contract and inject end-to-end latency. Main10 is required
    for the 10-bit end-to-end claim (VIDEO-01..03).
    """
    from server.mac_video_encoder import MacVideoEncoder

    enc = MacVideoEncoder(1920, 1080, _Settings())
    enc.start(on_encoded_frame=lambda *a: None)

    set_prop = mock_vt["VT"].VTSessionSetProperty
    assert set_prop.call_count >= 4, (
        f"Expected at least 4 VTSessionSetProperty calls (profile, realtime, "
        f"reordering, fps), got {set_prop.call_count}"
    )
    # Each call: VTSessionSetProperty(session, key, value)
    keys_set = {c.args[1]: c.args[2] for c in set_prop.call_args_list}
    assert "ProfileLevel" in keys_set
    assert keys_set["ProfileLevel"] == "HEVC_Main10_AutoLevel"
    assert "AllowFrameReordering" in keys_set
    assert keys_set["AllowFrameReordering"] is False, (
        f"AllowFrameReordering must be False, got {keys_set['AllowFrameReordering']}"
    )


def test_encoder_request_keyframe_is_callable(mock_vt):
    """request_keyframe() must be callable both before and after start()."""
    from server.mac_video_encoder import MacVideoEncoder

    enc = MacVideoEncoder(1920, 1080, _Settings())
    # Before start: must be a safe no-op (no VT calls fired).
    enc.request_keyframe()

    enc.start(on_encoded_frame=lambda *a: None)
    # After start: must not raise. Implementation latches a flag that
    # the next feed_frame consults; no direct VT call required here.
    enc.request_keyframe()


def test_encoder_stop_invalidates_session(mock_vt):
    """stop() drains pending frames and invalidates the session; idempotent."""
    from server.mac_video_encoder import MacVideoEncoder

    enc = MacVideoEncoder(1920, 1080, _Settings())
    enc.start(on_encoded_frame=lambda *a: None)
    enc.stop()

    mock_vt["VT"].VTCompressionSessionCompleteFrames.assert_called_once()
    mock_vt["VT"].VTCompressionSessionInvalidate.assert_called_once()

    # Idempotent — a second stop() must not double-invalidate or raise.
    enc.stop()
    assert mock_vt["VT"].VTCompressionSessionCompleteFrames.call_count == 1
    assert mock_vt["VT"].VTCompressionSessionInvalidate.call_count == 1
