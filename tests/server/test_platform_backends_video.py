"""Phase 2 / 02-05 / D-04 — platform_backends Mac video encoder dispatch.

Guards the import-time gating that decides whether the macOS server
prefers the direct VTCompressionSession path (server.mac_video_encoder)
or falls back to the FFmpeg hevc_videotoolbox subprocess (the Phase 1
path).

Linux CI runs this test file too — the dispatch surface MUST behave
identically on non-Mac (no MacVideoEncoder import attempted; flag is
False; no FFmpeg fallback triggered because no ServerHelloMsg path is
exercised here).
"""
from __future__ import annotations

import sys


def test_platform_backends_exposes_mac_video_enc_available_flag():
    """platform_backends MUST publish ``_MAC_VIDEO_ENC_AVAILABLE`` so the
    video_encoder dispatcher can test it without re-running the probe.
    """
    from server import platform_backends

    assert hasattr(platform_backends, "_MAC_VIDEO_ENC_AVAILABLE"), (
        "platform_backends.py must expose _MAC_VIDEO_ENC_AVAILABLE per D-04"
    )
    # On Linux CI (no PyObjC + VideoToolbox), the flag MUST be False so
    # the FFmpeg path stays in charge.
    if sys.platform != "darwin":
        assert platform_backends._MAC_VIDEO_ENC_AVAILABLE is False, (
            "On non-Mac the flag must default False — the FFmpeg path is "
            "the documented fallback per D-04 + plan 02-05 verify spec."
        )


def test_video_encoder_branches_on_mac_vt_flag(monkeypatch):
    """VideoEncoder.start() must consult ``_MAC_VIDEO_ENC_AVAILABLE`` and
    delegate to MacVideoEncoder when on darwin + flag True. We patch
    sys.platform + the flag to drive the branch on Linux CI without
    requiring a real PyObjC stack.
    """
    # Patch sys.platform BEFORE importing VideoEncoder so the dispatcher
    # check sees darwin.
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        "server.platform_backends._MAC_VIDEO_ENC_AVAILABLE", True
    )

    # Sentinel MacVideoEncoder so we can prove start() routed there.
    delegate_calls: dict[str, int] = {"start": 0, "stop": 0,
                                       "feed": 0, "kf": 0}

    class FakeMacEnc:
        def __init__(self, width, height, settings, available_encoders=None):
            self.width = width
            self.height = height

        def start(self, on_encoded_frame):
            delegate_calls["start"] += 1

        def feed_frame(self, *args, **kwargs):
            delegate_calls["feed"] += 1

        def request_keyframe(self):
            delegate_calls["kf"] += 1

        def stop(self):
            delegate_calls["stop"] += 1

    # The VideoEncoder dispatcher imports MacVideoEncoder lazily inside
    # start(); patch the symbol at the source module so the lazy import
    # picks up our fake.
    monkeypatch.setattr(
        "server.mac_video_encoder.MacVideoEncoder", FakeMacEnc, raising=False
    )

    from common.messages import QualitySettings
    from server.video_encoder import VideoEncoder

    enc = VideoEncoder(
        1920, 1080, QualitySettings(),
        available_encoders={"h264": [], "h265": [], "av1": []},
    )

    enc.start(on_encoded_frame=lambda *a: None)
    assert delegate_calls["start"] == 1, (
        f"MacVideoEncoder.start must be called when on darwin + flag True; "
        f"calls={delegate_calls}"
    )
    assert getattr(enc, "_using_mac_direct_vt", False) is True

    enc.request_keyframe()
    assert delegate_calls["kf"] == 1

    enc.stop()
    assert delegate_calls["stop"] == 1


def test_video_encoder_falls_back_to_ffmpeg_when_mac_vt_unavailable(
    monkeypatch,
):
    """When ``_MAC_VIDEO_ENC_AVAILABLE`` is False (PyObjC missing on Mac
    or running on Linux), VideoEncoder must use the existing FFmpeg
    subprocess path. We assert the flag-driven branch chooses the legacy
    path by checking ``_using_mac_direct_vt`` stays False — full
    subprocess behavior is covered by test_video_encoder_mock.py.
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        "server.platform_backends._MAC_VIDEO_ENC_AVAILABLE", False
    )

    # Stub subprocess.Popen so the FFmpeg fallback's start() does not
    # try to spawn a real ffmpeg.
    from unittest import mock
    fake_proc = mock.MagicMock()
    fake_proc.stdin = mock.MagicMock()
    fake_proc.stdout = mock.MagicMock()
    fake_proc.stdout.read.return_value = b""
    fake_proc.poll.return_value = None
    monkeypatch.setattr(
        "server.video_encoder.subprocess.Popen", lambda *a, **kw: fake_proc
    )

    from common.messages import QualitySettings
    from server.video_encoder import VideoEncoder

    enc = VideoEncoder(
        1920, 1080, QualitySettings(),
        available_encoders={"h264": [], "h265": [], "av1": []},
    )
    try:
        enc.start(on_encoded_frame=lambda *a: None)
        assert getattr(enc, "_using_mac_direct_vt", False) is False
        assert enc._process is fake_proc, (
            "FFmpeg fallback must spawn the subprocess; got: "
            f"{enc._process!r}"
        )
    finally:
        try:
            enc.stop()
        except Exception:
            pass
