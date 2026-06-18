"""STAB-01 — common/messages.py JSON round-trip + binary header codec coverage.

Target-read divergence notes:
- ``parse_message`` is a thin ``json.loads`` wrapper — it returns a ``dict`` with the
  ``type`` as a plain string (e.g. ``"health_ping"``). ``MsgType`` is a plain class of
  string constants (not an IntEnum), so ``parsed["type"] == MsgType.HEALTH_PING`` works.
- ``HealthPing.to_json()`` / ``HealthPong.to_json()`` mutate ``timestamp_ms`` /
  ``server_timestamp_ms`` as a side effect before serializing.
- ``encode_video_header`` accepts an optional ``monitor_id`` arg and
  ``decode_video_header`` returns a 7-tuple
  ``(frame_type, codec, chroma, flags, timestamp_ms, monitor_id, payload)``.
- ``encode_audio_header`` / ``decode_audio_header`` return
  ``(codec, timestamp_ms, payload)``.
"""
import json

from common.messages import (
    AUDIO_HEADER_SIZE,
    VIDEO_HEADER_SIZE,
    AudioCodec,
    AuthRequest,
    AuthResponse,
    AuthResult,
    ChromaSubsampling,
    ClientHelloMsg,
    FrameType,
    HealthPing,
    HealthPong,
    HealthStats,
    MonitorInfo,
    MonitorListMsg,
    MsgType,
    ServerHelloMsg,
    VideoCodec,
    VideoFrameFlags,
    decode_audio_header,
    decode_video_header,
    encode_audio_header,
    encode_video_header,
    parse_message,
)


# ─── JSON control message round-trips ──────────────────────────────────────


def test_healthping_roundtrip():
    raw = HealthPing(sequence=7).to_json()
    parsed = parse_message(raw)
    assert parsed["type"] == MsgType.HEALTH_PING
    assert parsed["sequence"] == 7
    # to_json() stamps the current time as a side effect
    assert parsed["timestamp_ms"] > 0


def test_healthpong_roundtrip():
    raw = HealthPong(ping_timestamp_ms=1_700_000_000_000, sequence=3).to_json()
    parsed = parse_message(raw)
    assert parsed["type"] == MsgType.HEALTH_PONG
    assert parsed["sequence"] == 3
    assert parsed["ping_timestamp_ms"] == 1_700_000_000_000
    assert parsed["server_timestamp_ms"] > 0


def test_healthstats_roundtrip():
    stats = HealthStats(
        rtt_ms=4.2,
        fps_actual=59.8,
        fps_target=60.0,
        bandwidth_mbps=12.3,
        frames_sent=1000,
        frames_dropped=2,
        encode_time_ms=4.1,
        capture_time_ms=1.0,
        input_latency_ms=0.5,
        codec="h265",
        chroma="yuv422",
        resolution="1920x1080",
        clients_connected=1,
    )
    parsed = json.loads(stats.to_json())
    assert parsed["type"] == MsgType.HEALTH_STATS
    assert parsed["rtt_ms"] == 4.2
    assert parsed["codec"] == "h265"
    assert parsed["frames_dropped"] == 2
    assert parsed["resolution"] == "1920x1080"


def test_authrequest_roundtrip():
    msg = AuthRequest(challenge="abc", auth_methods=["pam", "token"], salt="salty")
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.AUTH_REQUEST
    assert parsed["challenge"] == "abc"
    assert parsed["auth_methods"] == ["pam", "token"]
    assert parsed["salt"] == "salty"


def test_authresponse_roundtrip():
    msg = AuthResponse(
        method="password",
        username="alice",
        credential="hashed",
        screen_width=1920,
        screen_height=1080,
    )
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.AUTH_RESPONSE
    assert parsed["username"] == "alice"
    assert parsed["credential"] == "hashed"
    assert parsed["screen_width"] == 1920
    assert parsed["screen_height"] == 1080


def test_authresult_roundtrip():
    msg = AuthResult(success=True, message="welcome")
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.AUTH_RESULT
    assert parsed["success"] is True
    assert parsed["message"] == "welcome"


def test_clienthello_serverhello_roundtrip():
    client = ClientHelloMsg(screen_width=2560, screen_height=1440, supports_av1=True)
    server = ServerHelloMsg(server_name="teraguchi-srv", requires_auth=True)
    pc = parse_message(client.to_json())
    ps = parse_message(server.to_json())
    assert pc["type"] == MsgType.CLIENT_HELLO
    assert pc["screen_width"] == 2560
    assert pc["supports_av1"] is True
    assert ps["type"] == MsgType.SERVER_HELLO
    assert ps["server_name"] == "teraguchi-srv"
    assert ps["requires_auth"] is True


def test_monitor_list_roundtrip():
    monitors = [
        MonitorInfo(id=1, x=0, y=0, width=1920, height=1080, primary=True),
        MonitorInfo(id=2, x=1920, y=0, width=2560, height=1600),
    ]
    # MonitorListMsg.monitors is a plain list — dataclass instances serialize via asdict
    msg = MonitorListMsg(monitors=[m.__dict__ for m in monitors])
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.MONITOR_LIST
    assert len(parsed["monitors"]) == 2
    assert parsed["monitors"][0]["id"] == 1
    assert parsed["monitors"][1]["width"] == 2560


def test_parse_message_returns_dict_on_plain_json():
    """parse_message is a thin json.loads — should return a dict for any valid JSON object."""
    result = parse_message("{}")
    assert isinstance(result, dict)


def test_parse_message_unknown_type_passes_through():
    """Unknown ``type`` values must not raise — unknown control frames are handled
    upstream, not in the parser."""
    raw = json.dumps({"type": "novel_msg_type", "foo": 42})
    parsed = parse_message(raw)
    assert parsed["type"] == "novel_msg_type"
    assert parsed["foo"] == 42


# ─── Binary video/audio header codec round-trips ──────────────────────────


def test_video_header_codec_roundtrip_h265_yuv422():
    ts = 0x12345678
    header = encode_video_header(
        FrameType.VIDEO_H265,
        VideoCodec.H265,
        ChromaSubsampling.YUV422,
        VideoFrameFlags.KEYFRAME,
        ts,
        monitor_id=7,
    )
    payload_suffix = b"\x00\x01\x02canned-nalu"
    ft, codec, chroma, flags, out_ts, mon, rest = decode_video_header(
        header + payload_suffix
    )
    assert ft == FrameType.VIDEO_H265
    assert codec == VideoCodec.H265
    assert chroma == ChromaSubsampling.YUV422
    assert flags & VideoFrameFlags.KEYFRAME
    assert out_ts == ts
    assert mon == 7
    assert rest == payload_suffix


def test_video_header_size_matches_constant():
    header = encode_video_header(
        FrameType.VIDEO_H264,
        VideoCodec.H264,
        ChromaSubsampling.YUV420,
        VideoFrameFlags.NONE,
        0,
    )
    assert len(header) == VIDEO_HEADER_SIZE


def test_audio_header_codec_roundtrip_opus():
    ts = 0xABCDEF00
    header = encode_audio_header(AudioCodec.OPUS, ts)
    payload_suffix = b"opus-frame-bytes"
    codec, out_ts, rest = decode_audio_header(header + payload_suffix)
    assert codec == AudioCodec.OPUS
    assert out_ts == ts
    assert rest == payload_suffix


def test_audio_header_size_matches_constant():
    header = encode_audio_header(AudioCodec.PCM, 0)
    assert len(header) == AUDIO_HEADER_SIZE


# ── STAB-06 HealthPing/HealthPong FSM state serialization ─────────────


def test_healthping_includes_client_state():
    msg = HealthPing(sequence=1, client_state="streaming")
    parsed = json.loads(msg.to_json())
    assert parsed["client_state"] == "streaming"
    assert parsed["type"] == MsgType.HEALTH_PING


def test_healthping_default_client_state_is_empty():
    msg = HealthPing(sequence=1)
    parsed = json.loads(msg.to_json())
    assert parsed["client_state"] == ""


def test_healthpong_includes_server_state():
    msg = HealthPong(ping_timestamp_ms=1700000000000, sequence=1,
                     server_state="reconfiguring")
    parsed = json.loads(msg.to_json())
    assert parsed["server_state"] == "reconfiguring"


def test_healthpong_default_server_state_is_empty():
    msg = HealthPong(sequence=1)
    parsed = json.loads(msg.to_json())
    assert parsed["server_state"] == ""


def test_healthping_backward_compat_parse_without_client_state():
    """Old clients don't send client_state — parse_message still yields a dict."""
    old_style_json = '{"type":"health_ping","timestamp_ms":123,"sequence":7}'
    parsed = parse_message(old_style_json)
    assert parsed["type"] == MsgType.HEALTH_PING
    assert parsed.get("client_state", "") == ""


def test_healthping_client_state_restricted_to_declared_set():
    """Serialization convention: client_state must be one of CLIENT_STATES.

    This test doesn't enforce at the dataclass level (str is accepted),
    but documents the contract and guards against typos in wiring code."""
    from common.session_fsm import CLIENT_STATES
    for state in CLIENT_STATES:
        msg = HealthPing(client_state=state)
        parsed = json.loads(msg.to_json())
        assert parsed["client_state"] == state
