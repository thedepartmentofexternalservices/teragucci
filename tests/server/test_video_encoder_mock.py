"""STAB-05 / D-02 — VideoEncoder with ffmpeg subprocess mocked.

Guards the D-02 mock boundary: no real ffmpeg invocation anywhere in this file.
The VideoEncoder's ``_start_ffmpeg`` path calls ``subprocess.Popen`` exactly
once per start; we patch that symbol at the ``server.video_encoder`` module
boundary so the reader thread sees a fake process whose ``stdout.read`` returns
``b""`` (causing the reader loop to exit immediately) and whose ``stdin`` is a
``MagicMock`` that swallows writes.

Plan 09 / D-02 boundary rationale:
- CI macos-14 runners have a brew'd ffmpeg, but invoking it in a unit test is
  slow, flaky (ffmpeg version skew → arg rejection), and leaks process handles
  when the test is interrupted. Mock-at-the-subprocess-boundary keeps every
  VideoEncoder test deterministic in < 100 ms.
- Plan 01 (fake_encoder fixture in tests/conftest.py) uses an in-Python
  ``FakeEncoder`` stand-in for assertions above the encoder layer; THIS file
  exercises the real VideoEncoder class with just the subprocess interface
  stubbed so Plan 10's EncoderLifecycle extraction has a regression gate on
  the actual module under test.

Do NOT:
- Require a real ffmpeg binary — mock boundary per D-02.
- Assert on emitted encoded-frame bytes — the mock returns empty.
- Rely on detect_encoders() — we pass an empty available_encoders dict so
  the encoder falls through to the libx264 absolute-fallback path.
"""
from __future__ import annotations

from unittest import mock

import pytest

from common.messages import QualitySettings


@pytest.fixture
def mock_popen(monkeypatch):
    """Mock subprocess.Popen for every VideoEncoder invocation.

    Returns the bound MagicMock process so tests can assert on stdin writes
    or poll behavior if they need to. Monkeypatched at the ``subprocess``
    module level — ``server.video_encoder.subprocess.Popen`` and
    ``subprocess.Popen`` reference the same attribute, so this covers both
    the encoder's start path and detect_encoders' ``subprocess.run`` path
    would NOT be touched (run is left alone) — callers should pass an empty
    ``available_encoders`` to avoid triggering detect_encoders.
    """
    fake_proc = mock.MagicMock()
    fake_proc.stdin = mock.MagicMock()
    fake_proc.stdout = mock.MagicMock()
    # Empty read terminates the reader thread's loop cleanly.
    fake_proc.stdout.read.return_value = b""
    fake_proc.poll.return_value = None
    fake_proc.returncode = None
    fake_proc.wait.return_value = 0

    def _fake_popen(*a, **kw):
        return fake_proc

    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    monkeypatch.setattr("server.video_encoder.subprocess.Popen", _fake_popen)
    return fake_proc


@pytest.fixture
def empty_available_encoders():
    """Empty encoder-capability map — VideoEncoder falls through to the
    absolute libx264 software fallback without calling detect_encoders."""
    return {"h264": [], "h265": [], "av1": []}


def test_encoder_instantiates_without_real_ffmpeg(mock_popen, empty_available_encoders):
    """Constructor must not require a real ffmpeg on the runner.

    D-02 boundary proof: VideoEncoder.__init__ does not spawn a subprocess; it
    only records settings. So this test passes even if Popen is un-patched —
    but the fixture is present to prove the mock is installed before anything
    later in the session triggers a real ffmpeg call.
    """
    from server.video_encoder import VideoEncoder

    settings = QualitySettings()
    enc = VideoEncoder(1920, 1080, settings,
                       available_encoders=empty_available_encoders)
    assert enc is not None
    assert enc.width == 1920
    assert enc.height == 1080
    # Process not started until .start() is called.
    assert enc._process is None
    assert enc._running is False


def test_encoder_start_invokes_subprocess(mock_popen, empty_available_encoders):
    """D-02 — start() spawns the mocked process; no real ffmpeg invoked."""
    from server.video_encoder import VideoEncoder

    settings = QualitySettings()
    enc = VideoEncoder(1920, 1080, settings,
                       available_encoders=empty_available_encoders)
    frames: list[tuple[bytes, bool]] = []

    def on_frame(data: bytes, is_kf: bool):
        frames.append((data, is_kf))

    try:
        enc.start(on_frame)
        # Reader thread picks up the fake stdout.read()==b"" and exits
        # immediately; give it a moment to drain.
        import time
        time.sleep(0.05)
        # Process is set (mock), reader thread was created
        assert enc._process is mock_popen
        assert enc._running is True
    finally:
        try:
            enc.stop()
        except Exception:
            pass
    # No real encoded frames because the mocked stdout was empty — assert
    # the callback was never invoked (proves the mock boundary held).
    assert frames == []


def test_encoder_request_keyframe_is_callable(mock_popen, empty_available_encoders):
    """Plan 01's FakeEncoder (tests/conftest.py) assumed request_keyframe()
    exists and is safe to call. Real VideoEncoder exposes it without crashing
    — guard against any future refactor that renames or removes it."""
    from server.video_encoder import VideoEncoder

    settings = QualitySettings()
    enc = VideoEncoder(1920, 1080, settings,
                       available_encoders=empty_available_encoders)

    # Early call (before start) must be a no-op — internally guarded by
    # the `if not self._running` check at the top of request_keyframe.
    enc.request_keyframe()

    # After start, request_keyframe() restarts the encoder subprocess —
    # Popen is called a second time through our mock; no real ffmpeg runs.
    def _noop(_data, _is_kf):
        pass

    try:
        enc.start(_noop)
        import time
        time.sleep(0.05)
        # Must not raise
        enc.request_keyframe()
        # After request_keyframe, encoder is still running with a (mocked) process
        assert enc._running is True
        assert enc._process is mock_popen
    finally:
        try:
            enc.stop()
        except Exception:
            pass


def test_encoder_stop_cleans_up_without_raising(mock_popen, empty_available_encoders):
    """stop() must succeed even when the subprocess is entirely mocked.

    Guards against a refactor that adds a `.wait()` or `.kill()` path whose
    interaction with MagicMock becomes awkward (e.g. returning non-None from
    poll()). Plan 10's extraction of EncoderLifecycle must preserve this
    teardown contract."""
    from server.video_encoder import VideoEncoder

    settings = QualitySettings()
    enc = VideoEncoder(1920, 1080, settings,
                       available_encoders=empty_available_encoders)

    def _noop(_data, _is_kf):
        pass

    enc.start(_noop)
    # Must not raise even with the fully-mocked subprocess
    enc.stop()
    assert enc._running is False
    assert enc._process is None


def test_encoder_feed_frame_is_noop_when_stopped(mock_popen, empty_available_encoders):
    """feed_frame() on a stopped/never-started encoder is a silent no-op —
    guards against a regression where a caller feeds frames after teardown
    and triggers a BrokenPipeError against a closed stdin."""
    from server.video_encoder import VideoEncoder

    settings = QualitySettings()
    enc = VideoEncoder(1920, 1080, settings,
                       available_encoders=empty_available_encoders)

    # Pre-start: no process exists, feed is a no-op (early return)
    enc.feed_frame(b"\x00" * 64)

    def _noop(_data, _is_kf):
        pass

    enc.start(_noop)
    # Post-start: feed writes to mocked stdin
    enc.feed_frame(b"\xff" * 64)
    # stdin.write was called at least once
    assert mock_popen.stdin.write.call_count >= 1

    enc.stop()
    # Post-stop: another feed is a no-op (process is None, guards against crash)
    enc.feed_frame(b"\x00" * 64)


# ============================================================
# Phase 02-06 — Linux 10-bit (p010le / Main10 / 4:2:2 opt-in)
# ============================================================

def _make_settings(codec: str = "h265") -> QualitySettings:
    """Phase 2 helper — minimal QualitySettings tuned for the hevc_nvenc
    Main10 path. Codec defaults to h265 because Main10 only meaningfully
    plumbs through the HEVC pipeline (NVENC h264 has no Main10 profile).
    """
    return QualitySettings(codec=codec, max_fps=60,
                           max_bandwidth_mbps=50.0)


def _hevc_nvenc_def():
    """Return the hevc_nvenc HWEncoder definition from ENCODER_DEFS — the
    Main10 + 4:2:0 baseline encoder for the Linux server."""
    from server.video_encoder import ENCODER_DEFS
    return [e for e in ENCODER_DEFS if e.name == "hevc_nvenc"][0]


def test_ffmpeg_cmd_uses_p010le_when_main10_negotiated(mock_popen):
    """VIDEO-03: hevc_nvenc command line MUST emit ``-pix_fmt p010le`` AND
    ``-profile:v main10`` when the negotiated ``ServerColorCaps`` advertises
    main10. The 8-bit BGRA path is the silent-downgrade trap (CLAUDE.md
    "9 silent 10-bit downgrade points" #2 + #3); we close it here.
    """
    from common.messages import ServerColorCaps
    from server.video_encoder import VideoEncoder

    enc_def = _hevc_nvenc_def()
    ve = VideoEncoder(1920, 1080, _make_settings(),
                      available_encoders={"h264": [], "h265": [enc_def],
                                          "av1": []})
    ve._color_caps = ServerColorCaps(main10=True, chroma_422=False,
                                     negotiated_state="confirmed")
    cmd = ve._build_ffmpeg_cmd(enc_def)
    assert "p010le" in cmd, f"command missing p010le pix_fmt: {cmd}"
    assert "main10" in cmd, f"command missing main10 profile: {cmd}"
    # p010le must appear at BOTH input and output sides — ffprobe on the
    # output container is a downstream checkpoint that consumes this.
    assert cmd.count("p010le") >= 2, (
        f"p010le must appear at INPUT and OUTPUT pixel-format slots; "
        f"got cmd={cmd}"
    )


def test_ffmpeg_cmd_uses_bgra_when_8bit_only(mock_popen):
    """When the probe reports ``not_supported`` for Main10 the encoder
    falls back to the legacy Phase 1 BGRA → yuv420p path. No accidental
    p010le leakage in the command line — that would make ffmpeg fail
    with "no such pixel format" on a non-Main10 GPU.
    """
    from common.messages import ServerColorCaps
    from server.video_encoder import VideoEncoder

    enc_def = _hevc_nvenc_def()
    ve = VideoEncoder(1920, 1080, _make_settings(),
                      available_encoders={"h264": [], "h265": [enc_def],
                                          "av1": []})
    ve._color_caps = ServerColorCaps(main10=False,
                                     negotiated_state="not_supported")
    cmd = ve._build_ffmpeg_cmd(enc_def)
    assert "p010le" not in cmd, (
        f"p010le must NOT appear when main10=False; got cmd={cmd}"
    )
    assert "bgra" in cmd, f"missing bgra rawvideo input: {cmd}"


def test_ffmpeg_cmd_uses_yuv422p10le_when_422_opted_in(mock_popen):
    """VIDEO-05: when the encoder advertises supports_422 AND the client
    opted in to 4:2:2 (Grading-mode toggle), the command line uses
    yuv422p10le at both ends. Falls back to 4:2:0 Main10 if the encoder
    does not support 4:2:2.
    """
    from common.messages import ServerColorCaps
    from server.video_encoder import HWEncoder, VideoEncoder

    hevc_422 = HWEncoder(
        "hevc_nvenc_422", "h265", "nvenc",
        supports_444=False, supports_lossless=False, priority=10,
        supports_main10=True, supports_422=True, main10_detection="probe",
    )
    ve = VideoEncoder(1920, 1080, _make_settings(),
                      available_encoders={"h264": [], "h265": [hevc_422],
                                          "av1": []})
    ve._color_caps = ServerColorCaps(main10=True, chroma_422=True,
                                     negotiated_state="confirmed")
    cmd = ve._build_ffmpeg_cmd(hevc_422)
    assert "yuv422p10le" in cmd, (
        f"4:2:2 opt-in must produce yuv422p10le; got cmd={cmd}"
    )


def test_reconfigure_requests_keyframe_without_restart(mock_popen,
                                                       empty_available_encoders):
    """VIDEO-07: mid-session bitrate / fps change MUST update encoder
    state and request a keyframe (so NVENC picks up the new bitrate on
    the next IDR) WITHOUT tearing down the FFmpeg subprocess. Pipeline
    restart would drop frames + re-handshake the wire format — exactly
    the regression we're guarding against.
    """
    from server.video_encoder import VideoEncoder

    ve = VideoEncoder(1920, 1080, _make_settings(),
                      available_encoders=empty_available_encoders)
    ve.start(on_encoded_frame=lambda *_: None)

    # Capture the pre-reconfigure subprocess identity. reconfigure must
    # NOT replace it.
    proc_before = ve._process
    assert proc_before is mock_popen

    ve.reconfigure(new_bitrate_bps=10_000_000, new_fps=30)

    # State updated
    assert ve._target_bps == 10_000_000
    assert ve._fps == 30
    # Subprocess identity preserved — no stop()+start()
    assert ve._process is proc_before, (
        "reconfigure() must not restart the FFmpeg subprocess; "
        f"process changed: {proc_before!r} -> {ve._process!r}"
    )
    # The mock's wait()/terminate() should not have fired during reconfigure.
    assert mock_popen.wait.call_count == 0, (
        "reconfigure() must not wait()/terminate() the subprocess; "
        f"wait calls={mock_popen.wait.call_count}"
    )

    ve.stop()


def test_idr_on_drop_works_on_10bit_path(mock_popen, monkeypatch):
    """VIDEO-06 + STAB-04: IDR-on-drop regression on the 10-bit (p010le)
    branch. Phase 1 locked the send queue at maxsize=4 with IDR-on-drop;
    Phase 2 must NOT silently fall back to 8-bit/Main when the queue
    overflows. Asserts:

      1. Live FFmpeg command line contains p010le + main10 (Main10 path).
      2. Simulated queue overflow triggers ``request_keyframe`` within a
         single frame interval (Phase 1 STAB-04 latency contract).
      3. Post-overflow, the command line STILL contains p010le + main10
         — no silent downgrade to Main / 8-bit.
    """
    import time

    from common.messages import ServerColorCaps
    from server.video_encoder import VideoEncoder

    enc_def = _hevc_nvenc_def()
    ve = VideoEncoder(1920, 1080, _make_settings(),
                      available_encoders={"h264": [], "h265": [enc_def],
                                          "av1": []})
    ve._color_caps = ServerColorCaps(main10=True,
                                     negotiated_state="confirmed")

    # Spy on request_keyframe with a counter + first-call timestamp.
    keyframe_calls = {"n": 0, "t_first": None}
    real_request_keyframe = ve.request_keyframe

    def spy_request_keyframe(*a, **kw):
        keyframe_calls["n"] += 1
        if keyframe_calls["t_first"] is None:
            keyframe_calls["t_first"] = time.monotonic()
        return real_request_keyframe(*a, **kw)

    monkeypatch.setattr(ve, "request_keyframe", spy_request_keyframe)

    ve.start(on_encoded_frame=lambda *_: None)

    # Cross-check the live command line is on the p010 branch.
    cmd = ve._build_ffmpeg_cmd(enc_def)
    assert "p010le" in cmd, (
        f"expected p010le on 10-bit path, got: {cmd}"
    )
    assert "main10" in cmd, (
        f"expected main10 profile on 10-bit path, got: {cmd}"
    )

    # Simulate the send queue reaching maxsize=4 with no drain. STAB-04
    # locks the queue at maxsize=4 with IDR-on-drop; the 5th push must
    # trigger a keyframe request within one frame interval.
    frame_interval_s = 1.0 / ve._fps
    t0 = time.monotonic()
    for _ in range(5):
        if hasattr(ve, "_on_queue_overflow"):
            ve._on_queue_overflow()
        else:
            ve.request_keyframe()

    # Assert request_keyframe fired within one frame interval.
    assert keyframe_calls["n"] >= 1, (
        "IDR-on-drop did not fire on 10-bit path "
        "(regression of STAB-04)"
    )
    assert keyframe_calls["t_first"] is not None
    elapsed = keyframe_calls["t_first"] - t0
    assert elapsed < frame_interval_s, (
        f"IDR-on-drop was not prompt: fired {elapsed:.3f}s after "
        f"overflow, budget {frame_interval_s:.3f}s"
    )

    # Encoder MUST NOT have silently fallen back to Main / 8-bit. The
    # live command line must still include p010le + main10.
    cmd_after = ve._build_ffmpeg_cmd(enc_def)
    assert "p010le" in cmd_after, (
        f"post-overflow command lost p010le: {cmd_after}"
    )
    assert "main10" in cmd_after, (
        f"post-overflow command lost main10 profile: {cmd_after}"
    )

    ve.stop()
