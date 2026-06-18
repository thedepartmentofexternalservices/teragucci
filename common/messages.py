"""
Teraguchi Protocol Message Definitions - v3

Comprehensive protocol supporting:
- H.264/H.265 video with YUV 4:4:4 chroma
- Audio streaming (Opus)
- Pen/tablet with full pressure sensitivity
- Connection health monitoring
- Authentication
- Clipboard sync
- Multi-monitor
- Quality control (sharpness ↔ temporal stability)
- Bookmarks

Binary frame format (video):
  [1 byte: frame type] [1 byte: codec] [1 byte: chroma] [1 byte: flags]
  [4 bytes: timestamp_ms uint32 BE]
  [2 bytes: monitor_id uint16 BE]
  [payload...]

Binary frame format (audio):
  [1 byte: frame type = 0x10] [1 byte: codec] [2 bytes: reserved]
  [4 bytes: timestamp_ms uint32 BE]
  [payload...]

JSON control messages for everything else.
"""

import json
import struct
import time
import hashlib
import secrets
from enum import IntEnum
from dataclasses import dataclass, asdict, field
from typing import Optional, List


# ============================================================
# Binary Frame Types
# ============================================================

class FrameType(IntEnum):
    VIDEO_FULL = 0x01       # Full keyframe
    VIDEO_PARTIAL = 0x02    # Dirty-rect update (JPEG fallback mode)
    VIDEO_H264 = 0x03       # H.264 NAL unit
    VIDEO_H265 = 0x04       # H.265 NAL unit
    VIDEO_AV1 = 0x05        # AV1 OBU frame
    AUDIO = 0x10            # Audio frame


class VideoCodec(IntEnum):
    JPEG = 0x00
    H264 = 0x01
    H265 = 0x02
    VP9 = 0x03
    AV1 = 0x04


class ChromaSubsampling(IntEnum):
    YUV420 = 0x00   # Standard, good compression
    YUV422 = 0x01   # Better color for text
    YUV444 = 0x02   # Full color fidelity (sharp text, no color bleed)


class AudioCodec(IntEnum):
    OPUS = 0x00
    PCM = 0x01


class VideoFrameFlags(IntEnum):
    NONE = 0x00
    KEYFRAME = 0x01
    END_OF_STREAM = 0x02


# Binary headers
VIDEO_HEADER_SIZE = 10  # type(1) + codec(1) + chroma(1) + flags(1) + timestamp(4) + monitor(2)
AUDIO_HEADER_SIZE = 8   # type(1) + codec(1) + reserved(2) + timestamp(4)
JPEG_HEADER_SIZE = 9    # type(1) + x(2) + y(2) + w(2) + h(2) — legacy compat


def encode_video_header(frame_type: FrameType, codec: VideoCodec,
                        chroma: ChromaSubsampling, flags: int,
                        timestamp_ms: int, monitor_id: int = 0) -> bytes:
    return struct.pack("!BBBBIH", frame_type, codec, chroma, flags,
                       timestamp_ms, monitor_id)


def decode_video_header(data: bytes) -> tuple:
    """Returns (frame_type, codec, chroma, flags, timestamp_ms, monitor_id, payload)"""
    ft, codec, chroma, flags, ts, mon = struct.unpack("!BBBBIH", data[:VIDEO_HEADER_SIZE])
    return FrameType(ft), VideoCodec(codec), ChromaSubsampling(chroma), flags, ts, mon, data[VIDEO_HEADER_SIZE:]


def encode_audio_header(codec: AudioCodec, timestamp_ms: int) -> bytes:
    return struct.pack("!BBHI", FrameType.AUDIO, codec, 0, timestamp_ms)


def decode_audio_header(data: bytes) -> tuple:
    """Returns (codec, timestamp_ms, payload)"""
    _, codec, _, ts = struct.unpack("!BBHI", data[:AUDIO_HEADER_SIZE])
    return AudioCodec(codec), ts, data[AUDIO_HEADER_SIZE:]


# Legacy JPEG frame compat
def encode_jpeg_header(frame_type: FrameType, x: int, y: int, w: int, h: int) -> bytes:
    return struct.pack("!BHHHH", frame_type, x, y, w, h)


def decode_jpeg_header(data: bytes) -> tuple:
    ft, x, y, w, h = struct.unpack("!BHHHH", data[:JPEG_HEADER_SIZE])
    return FrameType(ft), x, y, w, h, data[JPEG_HEADER_SIZE:]


# ============================================================
# JSON Control Message Types
# ============================================================

class MsgType:
    # --- Input (Client → Server) ---
    MOUSE_MOVE = "mouse_move"
    MOUSE_BUTTON = "mouse_button"
    MOUSE_SCROLL = "mouse_scroll"
    KEY_EVENT = "key_event"
    PEN_EVENT = "pen_event"

    # Phase 2 additions (D-11, D-15, D-19):
    KEY_RESET_MODIFIERS = "key_reset_modifiers"
    TEXT_COMMIT = "text_commit"
    PEN_PROXIMITY = "pen_proximity"

    # Phase 3 additions (D-02, D-17):
    SESSION_CONFIGURE = "session_configure"
    CLIPBOARD_CHUNK = "clipboard_chunk"

    # --- Handshake ---
    CLIENT_HELLO = "client_hello"
    SERVER_HELLO = "server_hello"
    AUTH_REQUEST = "auth_request"
    AUTH_RESPONSE = "auth_response"
    AUTH_RESULT = "auth_result"

    # --- Control ---
    REQUEST_FULL_FRAME = "request_full_frame"
    QUALITY_SETTINGS = "quality_settings"
    SELECT_MONITOR = "select_monitor"
    RESIZE_REQUEST = "resize_request"

    # --- Health ---
    HEALTH_PING = "health_ping"
    HEALTH_PONG = "health_pong"
    HEALTH_STATS = "health_stats"

    # --- Clipboard ---
    CLIPBOARD_SEND = "clipboard_send"
    CLIPBOARD_RECV = "clipboard_recv"

    # --- File Transfer ---
    FILE_OFFER = "file_offer"          # Client → Server: offer a file
    FILE_ACCEPT = "file_accept"        # Server → Client: ready to receive
    FILE_CHUNK = "file_chunk"          # Client → Server: file data chunk
    FILE_DONE = "file_done"            # Client → Server: transfer complete
    FILE_ACK = "file_ack"             # Server → Client: transfer result
    FILE_CANCEL = "file_cancel"        # Either direction: abort transfer

    # --- USB Passthrough ---
    USB_DEVICE_LIST = "usb_device_list"   # Client → Server: available devices
    USB_ATTACH = "usb_attach"             # Either: request attach
    USB_DETACH = "usb_detach"             # Either: request detach
    USB_ATTACHED = "usb_attached"         # Server → Client: attach result
    USB_DETACHED = "usb_detached"         # Server → Client: detach result
    USB_ERROR = "usb_error"               # Either: error

    # --- Multi-monitor ---
    MONITOR_LIST = "monitor_list"

    # --- Cursor ---
    CURSOR_UPDATE = "cursor_update"

    # --- Broker ---
    BROKER_HELLO = "broker_hello"              # Broker → Client: machine list + admin status
    BROKER_ASSIGN = "broker_assign"            # Broker → Client: assigned machine + token
    BROKER_MACHINE_REQUEST = "broker_machine_request"  # Client → Broker: request specific machine
    BROKER_STATUS = "broker_status"            # Client → Broker: request status refresh
    BROKER_RELEASE = "broker_release"          # Client → Broker: release machine assignment

    # --- Structured protocol errors (OBS-01 + error taxonomy) ---
    ERROR = "error"                            # Either direction: carries a ProtocolErrorMsg


# ============================================================
# Quality Control
# ============================================================

@dataclass
class QualitySettings:
    """
    Quality control settings.

    The quality_bias slider goes from 0.0 (favor temporal stability / smooth motion)
    to 1.0 (favor sharpness / image quality). This maps to encoder parameters:

    Bias 0.0 (Smooth):  Lower CRF, higher FPS, YUV420, more B-frames, temporal AQ
    Bias 0.5 (Balanced): Medium CRF, medium FPS, YUV422
    Bias 1.0 (Sharp):    Higher CRF, lower FPS if needed, YUV444, no B-frames, spatial AQ

    Additional overrides are available for power users.
    """
    type: str = MsgType.QUALITY_SETTINGS
    quality_bias: float = 0.5       # 0.0 = smooth motion, 1.0 = sharp/crisp
    max_fps: int = 60               # Maximum frame rate
    max_bandwidth_mbps: float = 50.0  # Bandwidth cap in Mbps
    codec: str = "h264"             # "h264", "h265", "av1", "jpeg"
    chroma: str = "yuv422"          # "yuv420", "yuv422", "yuv444"
    force_lossless: bool = False    # True = use lossless H.264 (YUV444 auto)
    enable_audio: bool = True
    audio_bitrate_kbps: int = 128

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    def effective_crf(self) -> int:
        """Calculate CRF from quality bias. Lower CRF = better quality."""
        if self.force_lossless:
            return 0
        # Map bias 0.0→28 (lower quality, save bandwidth for FPS) to 1.0→15 (high quality)
        return int(28 - (self.quality_bias * 13))

    def effective_preset(self) -> str:
        """FFmpeg preset based on bias."""
        if self.quality_bias < 0.3:
            return "ultrafast"
        elif self.quality_bias < 0.6:
            return "veryfast"
        elif self.quality_bias < 0.8:
            return "fast"
        else:
            return "medium"

    def effective_chroma(self) -> ChromaSubsampling:
        if self.force_lossless or self.chroma == "yuv444":
            return ChromaSubsampling.YUV444
        elif self.chroma == "yuv422":
            return ChromaSubsampling.YUV422
        return ChromaSubsampling.YUV420

    def effective_fps(self) -> int:
        """Target FPS adjusted by quality bias."""
        if self.quality_bias > 0.8:
            return min(self.max_fps, 30)  # Cap FPS when favoring sharpness
        return self.max_fps


# ============================================================
# Authentication
# ============================================================

@dataclass
class AuthRequest:
    """Server sends this to request authentication."""
    type: str = MsgType.AUTH_REQUEST
    auth_methods: list = field(default_factory=lambda: ["password", "token"])
    challenge: str = ""  # Random challenge for password hashing
    salt: str = ""       # User salt (sent after username is known, or per-user)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class AuthResponse:
    """Client sends credentials."""
    type: str = MsgType.AUTH_RESPONSE
    method: str = "password"
    username: str = ""
    credential: str = ""  # Hashed password or token
    screen_width: int = 0
    screen_height: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class AuthResult:
    """Server sends authentication result."""
    type: str = MsgType.AUTH_RESULT
    success: bool = False
    message: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class ProtocolErrorMsg:
    """Structured protocol-level error envelope (OBS-01 + error taxonomy).

    Wire format for reporting :class:`common.errors.TeraguchiError` subclasses to
    the peer. The ``code`` field is stable across versions; the client FSM can
    use it to decide deterministic reactions (e.g. ``TERA_AUTH_FAILED`` → do NOT
    reconnect; ``TERA_TRANSPORT`` → exponential back-off + retry).

    Threat T-1-05 rule: callers MUST NOT put raw secrets into ``message`` —
    the field is free-form human text. The `_redact_secrets` structlog
    processor does not run on wire payloads.
    """
    type: str = MsgType.ERROR
    code: str = "TERA_UNKNOWN"
    message: str = ""
    session_id: str = ""
    recoverable: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_dict(cls, d: dict) -> "ProtocolErrorMsg":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def hash_password(password: str, challenge: str) -> str:
    """Hash password with server challenge (challenge-response auth)."""
    return hashlib.sha256(f"{password}:{challenge}".encode()).hexdigest()


def generate_challenge() -> str:
    """Generate a random authentication challenge."""
    return secrets.token_hex(32)


# ============================================================
# Health Monitoring
# ============================================================

@dataclass
class HealthPing:
    type: str = MsgType.HEALTH_PING
    timestamp_ms: int = 0
    sequence: int = 0
    # STAB-06 — FSM state serialized verbatim from common.session_fsm.ClientFSM
    # HealthPing ORIGINATES ON THE CLIENT (Plan 01-08 inverts prior direction).
    # Empty string indicates "state not reported by this peer" (backward compat).
    client_state: str = ""

    def to_json(self) -> str:
        self.timestamp_ms = int(time.time() * 1000)
        return json.dumps(asdict(self))


@dataclass
class HealthPong:
    type: str = MsgType.HEALTH_PONG
    ping_timestamp_ms: int = 0
    sequence: int = 0
    server_timestamp_ms: int = 0
    # STAB-06 — FSM state serialized verbatim from common.session_fsm.ServerFSM
    # HealthPong ORIGINATES ON THE SERVER (Plan 01-08 inverts prior direction).
    server_state: str = ""

    def to_json(self) -> str:
        self.server_timestamp_ms = int(time.time() * 1000)
        return json.dumps(asdict(self))


@dataclass
class HealthStats:
    """Periodic health statistics from server.

    Plan 01-14 (OBS-02 + OBS-03) adds 5 new fields to surface per-stage latency
    breakdown + keyframe telemetry on the wire:

    * ``transmit_time_ms``, ``decode_time_ms``, ``display_time_ms``: rolling
      average of the stage deques in :class:`server.health.HealthMonitor`
    * ``keyframe_requested``, ``keyframe_emitted``: monotonic counters backed
      by the STAB-04 IDR-on-drop path (request) and the encoder keyframe
      callback (emit)

    All five default to 0.0 / 0 so existing call sites don't need to change.
    """
    type: str = MsgType.HEALTH_STATS
    rtt_ms: float = 0.0           # Round-trip time
    fps_actual: float = 0.0       # Actual frame rate
    fps_target: float = 0.0       # Target frame rate
    bandwidth_mbps: float = 0.0   # Current bandwidth usage
    frames_sent: int = 0
    frames_dropped: int = 0       # Frames dropped due to backpressure
    encode_time_ms: float = 0.0   # Average encode time per frame
    capture_time_ms: float = 0.0  # Average capture time per frame
    input_latency_ms: float = 0.0 # Input processing latency
    # OBS-02 — per-stage latency (client-reported stages submitted via Plan
    # 17 smoke harness; server-side transmit populated in Plan 14+).
    transmit_time_ms: float = 0.0
    decode_time_ms: float = 0.0
    display_time_ms: float = 0.0
    # OBS-03 — keyframe telemetry counters
    keyframe_requested: int = 0
    keyframe_emitted: int = 0
    codec: str = "h264"
    chroma: str = "yuv444"
    resolution: str = "1920x1080"
    clients_connected: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# ============================================================
# Multi-Monitor
# ============================================================

@dataclass
class MonitorInfo:
    id: int = 0
    name: str = ""
    width: int = 1920
    height: int = 1080
    x: int = 0       # Position in virtual desktop
    y: int = 0
    primary: bool = False
    scale: float = 1.0


@dataclass
class MonitorListMsg:
    type: str = MsgType.MONITOR_LIST
    monitors: list = field(default_factory=list)
    # Phase 3 D-09 / Plan 03-05 — per-client fallback events triggered
    # by this hot-plug. Each entry is a dict of:
    #   - ``client_token``: server-side session identifier
    #     (currently ``ClientSession.client_id``)
    #   - ``previous_pick``: the monitor name the client was bookmarked to
    #   - ``now_showing``: the fallback monitor (typically the primary)
    # Empty list when no sessions needed auto-fallback (mirror_all
    # sessions, or pick_one sessions whose picked monitor stayed
    # present). Pre-Plan-05 servers ship an empty list — wire-compat for
    # older clients that ignore the field.
    degradations: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class SelectMonitorMsg:
    type: str = MsgType.SELECT_MONITOR
    monitor_id: int = 0      # -1 = all monitors (virtual desktop)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# ============================================================
# Clipboard
# ============================================================

@dataclass
class ClipboardMsg:
    type: str = MsgType.CLIPBOARD_SEND
    content_type: str = "text/plain"  # MIME type
    data: str = ""  # Base64-encoded for binary, plain for text

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class ClipboardChunkMsg:
    """D-17 — chunked transport for clipboard payloads exceeding 1 MB.

    Mirrors :class:`KeyResetModifiersMsg` (Phase 2 D-11) shape — type +
    payload fields + one-line ``to_json``. Assembler lives in
    ``common/clipboard_chunks.py`` (added by Plan 06).

    Fields:
    - ``sequence_id``: per-session monotonic id identifying one logical
      clipboard payload
    - ``chunk_index``: 0-indexed position within the payload
    - ``total_chunks``: expected number of chunks (assembler completes when
      all indices 0..total_chunks-1 have arrived)
    - ``content_type``: mime type of the reassembled payload (``text/plain``
      or ``image/png``)
    - ``data``: base64-encoded payload chunk (raw UTF-8 text for text/plain
      chunks, base64 bytes otherwise)

    Threat T-03-03 note: Plan 06's assembler MUST enforce
    ``total_chunks <= 256`` (≈256 MB cap at the 1 MB/chunk ceiling) so
    an attacker cannot set ``total_chunks`` to maxint to exhaust memory.
    """
    type: str = MsgType.CLIPBOARD_CHUNK
    sequence_id: int = 0
    chunk_index: int = 0
    total_chunks: int = 1
    content_type: str = "text/plain"
    data: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# ============================================================
# Connection Handshake
# ============================================================

@dataclass
class ClientHelloMsg:
    type: str = MsgType.CLIENT_HELLO
    client_name: str = "Teraguchi Client"
    version: str = "3.0.0"
    screen_width: int = 1920
    screen_height: int = 1080
    supports_h264: bool = True
    supports_h265: bool = True
    supports_av1: bool = True
    supports_yuv444: bool = True
    supports_audio: bool = True
    supports_pen: bool = True
    decoder_backend: str = ""  # "cuda", "vaapi", "videotoolbox", "software"
    # Phase 3 D-02 — capture-mode negotiation. Defaults preserve the
    # pre-Phase-3 always-full-virtual-desktop behavior — mirror_all
    # means "ship the whole virtual desktop unchanged" which is what
    # current servers already do. Threat T-03-02 — server-side
    # ``apply_capture_mode`` (Plan 03) MUST whitelist-check this string
    # and fall back to mirror_all + structlog warning on unknown values.
    capture_mode: str = "mirror_all"   # "single" | "mirror_all" | "pick_one"
    picked_monitor_id: int = -1
    picked_monitor_name: str = ""
    # Phase 3 D-15 — per-direction clipboard toggles (Plan 06). Defaults
    # preserve pre-Phase-3 always-on clipboard behavior + D-16 "secure
    # defaults = all directions ON". Wire field names match the bookmark
    # field names on ConnectionProfile so the mental model stays simple:
    # client reads toggles from ConnectionProfile → pushes them on the
    # hello → server mirrors them onto ClientSession.clipboard_* and
    # gates outbound broadcast + inbound dispatch at the chunk-0
    # boundary (Pitfall 7 mid-stream toggle race fix).
    clipboard_text_c2s: bool = True
    clipboard_text_s2c: bool = True
    clipboard_image_c2s: bool = True
    clipboard_image_s2c: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class ServerColorCaps:
    """Server-advertised color pipeline capabilities (D-03, D-05).

    Populated by the server-side capability probe (Plan 02-04) — ``main10``
    requires NVENC Main10 on Turing+ or VT HEVC Main10 AutoLevel on Mac,
    ``chroma_422`` requires Blackwell NVENC / M3+ VT, ``chroma_444`` stays
    non-user-facing in v1 (HEVC 4:4:4 is bandwidth-heavy + software-decode
    only on older Macs).

    Default instance (all False + ``negotiated_state='not_supported'``) is
    the "probe failed or not run yet" sentinel — server refuses to claim
    a capability it can't honestly deliver. No silent fallback: the client
    health overlay renders ``negotiated_state`` so artists see the actual
    state of the pipeline at a glance.
    """
    main10: bool = False
    chroma_422: bool = False
    chroma_444: bool = False
    advertised_pix_fmt: str = "p010le"  # canonical name the server will send
    # negotiated_state values:
    # "negotiated" | "confirmed" | "degraded" | "not_supported"
    negotiated_state: str = "not_supported"


@dataclass
class ServerHelloMsg:
    type: str = MsgType.SERVER_HELLO
    server_name: str = "Teraguchi Server"
    version: str = "3.0.0"
    screen_width: int = 1920
    screen_height: int = 1080
    monitors: list = field(default_factory=list)
    supports_h264: bool = True
    supports_h265: bool = False
    supports_av1: bool = False
    supports_yuv444: bool = True
    supports_audio: bool = True
    supports_pen: bool = True
    requires_auth: bool = False
    # Encoder details for client display
    encoder_backend: str = ""           # "nvenc", "vaapi", "amf", "software"
    available_encoders: dict = field(default_factory=dict)  # codec -> [encoder info]
    # Phase 2 addition (D-03) — structured color-capability block.
    # ``asdict`` recurses into nested dataclasses so the wire format is
    # ``color_caps: {main10, chroma_422, chroma_444, advertised_pix_fmt,
    # negotiated_state}``. Default factory preserves Phase 1 wire compat:
    # servers that don't set color_caps ship the probe-failed sentinel.
    color_caps: ServerColorCaps = field(default_factory=ServerColorCaps)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# ============================================================
# Input Messages
# ============================================================

@dataclass
class MouseMoveMsg:
    """Mouse move event (Phase 3 D-05 — promoted from dict to dataclass).

    Phase 1/2 client callsite (``client/session.py``) emits the raw dict
    form ``{"type": "mouse_move", "x": float, "y": float}``. The new
    ``server_x`` / ``server_y`` integer fields carry the client-computed
    server-physical-pixel coordinates (D-05). Default ``-1`` is the
    "client did not compute physical px" sentinel — server prefers the
    integer fields when ``server_x >= 0`` and falls back to the
    normalized floats otherwise. Pre-Phase-3 clients that omit the
    fields hit the sentinel default and server behavior is identical.
    """
    type: str = MsgType.MOUSE_MOVE
    x: float = 0.0
    y: float = 0.0
    # Phase 3 D-05 — server physical-pixel integer coords.
    server_x: int = -1
    server_y: int = -1

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class MouseButtonMsg:
    """Mouse button event (Phase 3 D-05 — promoted from dict to dataclass).

    ``button`` follows the Phase 1 wire convention (1 = left, 2 = middle,
    3 = right). ``server_x`` / ``server_y`` default ``-1`` sentinel per
    D-05 (backward compat for pre-Phase-3 clients).
    """
    type: str = MsgType.MOUSE_BUTTON
    x: float = 0.0
    y: float = 0.0
    button: int = 1
    pressed: bool = False
    # Phase 3 D-05 — server physical-pixel integer coords.
    server_x: int = -1
    server_y: int = -1

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class MouseScrollMsg:
    """Mouse scroll event (Phase 3 D-05 — promoted from dict to dataclass).

    ``dx`` / ``dy`` are wheel-delta units. ``server_x`` / ``server_y``
    default ``-1`` sentinel per D-05 (backward compat).
    """
    type: str = MsgType.MOUSE_SCROLL
    x: float = 0.0
    y: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    # Phase 3 D-05 — server physical-pixel integer coords.
    server_x: int = -1
    server_y: int = -1

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class KeyEventMsg:
    """Keyboard key event (D-14 — promoted from dict to dataclass).

    Phase 1 wire shape (``{"type": "key_event", "scan_code": N,
    "pressed": bool}``) remains compatible — the three new lock-state
    fields default to False so the parsed dict on the server side
    behaves identically when older clients omit them.

    D-14 — every KeyEvent carries ``caps_lock_on`` / ``num_lock_on`` /
    ``scroll_lock_on``; server auto-corrects virtual-display lock state
    on mismatch. State re-converges on the next keystroke, so zero
    round-trip cost.

    Phase 3 D-05 — every KeyEvent also carries the client-computed
    server-physical-pixel coords (``server_x`` / ``server_y``) so
    cursor-position-sensitive shortcuts (e.g. Flame's wheel-menus
    anchored to cursor) land on the correct pixel on mixed-DPI clients.
    Default ``-1`` sentinel = "client did not compute physical px".

    Note: Phase 1 client callsite in ``client/session.py::_send_key_event``
    still emits the dict form with a ``modifiers`` field. Plan 02-09
    (``viewer-modifier-triggers``) wires the client to emit the new
    dataclass with live lock-state bits. 02-02's scope is the wire
    shape — landing the dataclass here unblocks that downstream plan.
    """
    type: str = MsgType.KEY_EVENT
    scan_code: int = 0
    pressed: bool = False
    # Phase 2 additions (D-14) — lock-state bits sent on every KeyEvent.
    caps_lock_on: bool = False
    num_lock_on: bool = False
    scroll_lock_on: bool = False
    # Phase 3 D-05 — server physical-pixel integer coords.
    server_x: int = -1
    server_y: int = -1

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class PenEventMsg:
    """Pen/stylus event with full tablet data.

    Phase 3 D-05 — ``server_x`` / ``server_y`` carry the client-computed
    server-physical-pixel coords. Default ``-1`` sentinel = "client did
    not compute physical px" (pre-Phase-3 wire compat).
    """
    type: str = MsgType.PEN_EVENT
    x: float = 0.0
    y: float = 0.0
    pressure: float = 0.0
    tilt_x: float = 0.0
    tilt_y: float = 0.0
    rotation: float = 0.0
    button: int = 0
    pressed: bool = False
    hovering: bool = False
    pen_type: str = "pen"
    # Phase 3 D-05 — server physical-pixel integer coords.
    server_x: int = -1
    server_y: int = -1

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# Phase 2 additions (D-11, D-15, D-19) — release-all-modifiers,
# IME commit-string passthrough, and pen-proximity re-synthesis.
# These mirror the PenEventMsg dataclass shape (type field + to_json
# using json.dumps(asdict(self))) established in Phase 1.


@dataclass
class KeyResetModifiersMsg:
    """Client → Server: release all held modifiers on the server side (D-11).

    Fired on 4 triggers: focusOutEvent / WindowDeactivate (fixes
    "Ctrl stuck after Cmd-Tab"), ConnectionSupervisor reconnect
    (fixes network-stall repeat-runaway), server-periodic safety
    net (~10s belt-and-suspenders), and F9 client-side panic shortcut.

    Server dispatches to InputInjector.reset_modifiers() idempotently.
    The ``reason`` field is log-only — per threat T-02-04, the server
    always performs the same reset regardless of reason, so nothing is
    exploitable even if an attacker forged the field.
    """
    type: str = MsgType.KEY_RESET_MODIFIERS
    reason: str = "unknown"  # "focus_out" | "reconnect" | "periodic" | "panic_f9" | "unknown"

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class TextCommitMsg:
    """Client → Server: IME / dead-key commit string passthrough (D-15).

    Forwarded as Unicode text, NOT as synthesized keycodes — synthesizing
    keycodes mangles dead-key composition across layouts. Server injects
    via ``xdotool type`` on Linux or ``CGEventKeyboardSetUnicodeString``
    on macOS. Covers REQ-INPUT-05 (US / UK / DE / JP layouts + IME).
    """
    type: str = MsgType.TEXT_COMMIT
    text: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class PenProximityMsg:
    """Client → Server: pen proximity re-synthesis on focusIn / showEvent (D-19).

    Idempotent on the server side — PenFSM tolerates duplicate
    proximity-enter transitions as a no-op. Fixes the "proximity event
    eaten by lockscreen" class of bug after screen lock, minimize/restore,
    or Cmd-Tab cycles.
    """
    type: str = MsgType.PEN_PROXIMITY
    in_proximity: bool = False
    pen_type: str = "pen"  # "pen" | "eraser"

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# ============================================================
# Cursor Shape (local-cursor rendering)
# ============================================================

@dataclass
class CursorUpdateMsg:
    """Server → Client: cursor shape update.

    Sent whenever the X cursor serial changes (i.e. Flame swaps from
    arrow to crosshair, move, text, wait, etc.). The client caches
    the shape locally and renders the cursor at its own mouse
    position with zero latency, so cursor motion never round-trips
    through the video pipeline.

    ``rgba_b64`` is base64-encoded RGBA8888 with premultiplied alpha
    (XFixes returns pixels pre-multiplied). Render with Qt's
    ``QImage.Format_RGBA8888_Premultiplied``.
    """
    type: str = MsgType.CURSOR_UPDATE
    serial: int = 0
    width: int = 0
    height: int = 0
    hot_x: int = 0
    hot_y: int = 0
    rgba_b64: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# ============================================================
# Bookmark / Connection Profile
# ============================================================

@dataclass
class ConnectionProfile:
    """A saved connection with all settings."""
    name: str = ""
    host: str = ""
    port: int = 443
    username: str = ""
    password_encrypted: str = ""  # Encrypted with local machine key
    use_tls: bool = False
    auto_connect: bool = False
    quality_bias: float = 0.5
    preferred_codec: str = "h264"
    preferred_chroma: str = "yuv444"
    max_fps: int = 60
    max_bandwidth_mbps: float = 50.0
    enable_audio: bool = True
    monitor_id: int = -1  # -1 = all
    notes: str = ""
    last_connected: str = ""
    created: str = ""
    color_label: str = ""  # For visual organization
    mode: str = "direct"  # "direct" or "broker"
    # Phase 2 D-10 — per-bookmark Cmd<->Ctrl swap state. ``destination_kind``
    # is "linux" or "mac" and is the canonical signal driving the default
    # value of ``swap_cmd_ctrl``. v1 servers are Mac-client → Rocky/macOS
    # server only (Windows server is Phase 3); other strings round-trip
    # but are treated as "linux" for the swap-default policy because
    # Linux is the documented Flame production path.
    destination_kind: str = "linux"
    # Default True for Linux destinations (Cmd → Ctrl matches Flame
    # muscle memory on Linux). Default False when destination_kind == "mac"
    # is enforced at construction time by ``BookmarkManager._migrate_swap_default``
    # so existing saved bookmarks pre-Phase-2 get the right default on load.
    swap_cmd_ctrl: bool = True

    # Phase 3 D-01 / D-04 / D-15 — display + clipboard preferences.
    # All seven fields default to values that preserve pre-Phase-3
    # behavior: mirror_all matches the legacy always-full-virtual-desktop
    # capture path (D-02), no picked monitor (-1 / ""), and all four
    # clipboard directions ON (D-16 "security defaults = all directions
    # ON"). Pre-Phase-3 bookmark JSON files omit these fields entirely;
    # ``ConnectionProfile.from_dict`` filters unknown keys through
    # ``cls.__dataclass_fields__`` so missing-field load is safe
    # (threat T-03-01 mitigation). Whitelist enforcement on
    # ``monitor_mode`` deferred to Plan 02 migration block.
    monitor_mode: str = "mirror_all"   # "single" | "mirror_all" | "pick_one"
    picked_monitor_id: int = -1
    picked_monitor_name: str = ""
    clipboard_text_c2s: bool = True
    clipboard_text_s2c: bool = True
    clipboard_image_c2s: bool = True
    clipboard_image_s2c: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ConnectionProfile":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ============================================================
# Helpers
# ============================================================

def parse_message(json_str: str) -> dict:
    """Parse a JSON control message."""
    return json.loads(json_str)
