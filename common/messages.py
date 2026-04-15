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
    AUDIO = 0x10            # Audio frame (server → client)
    MIC = 0x11              # Microphone audio frame (client → server)


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
MIC_HEADER_SIZE = 8     # type(1) + codec(1) + reserved(2) + timestamp(4)  [same layout as AUDIO]
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


def encode_mic_header(codec: AudioCodec, timestamp_ms: int) -> bytes:
    """Encode microphone frame header (client → server)."""
    return struct.pack("!BBHI", FrameType.MIC, codec, 0, timestamp_ms)


def decode_mic_header(data: bytes) -> tuple:
    """Returns (codec, timestamp_ms, payload)"""
    _, codec, _, ts = struct.unpack("!BBHI", data[:MIC_HEADER_SIZE])
    return AudioCodec(codec), ts, data[MIC_HEADER_SIZE:]


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

    # --- Power Management (Client → Server) ---
    # Server executes the action on the local machine (requires sudo privileges)
    POWER_ACTION = "power_action"              # action: "power_off" | "reboot"


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

    def to_json(self) -> str:
        self.timestamp_ms = int(time.time() * 1000)
        return json.dumps(asdict(self))


@dataclass
class HealthPong:
    type: str = MsgType.HEALTH_PONG
    ping_timestamp_ms: int = 0
    sequence: int = 0
    server_timestamp_ms: int = 0

    def to_json(self) -> str:
        self.server_timestamp_ms = int(time.time() * 1000)
        return json.dumps(asdict(self))


@dataclass
class HealthStats:
    """Periodic health statistics from server."""
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

    def to_json(self) -> str:
        return json.dumps(asdict(self))


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

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# ============================================================
# Input Messages
# ============================================================

@dataclass
class PenEventMsg:
    """Pen/stylus event with full tablet data."""
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

    # Power management (optional — leave empty to disable)
    # power_backend: "ssh" | "wol" | "teraguchi" | "none" | ""
    #   ssh       — SSH commands for off/reboot; wol_mac for power-on
    #   wol       — Wake-on-LAN only (power-on); no off/reboot
    #   teraguchi — in-band off/reboot via live session; falls back to ssh
    #   none / "" — power buttons hidden
    power_backend: str = ""
    power_os: str = "linux"              # "linux" | "windows" (controls shutdown command)
    # SSH overrides — normally empty; host/user are inherited from profile.host/username
    power_ssh_host: str = ""            # override SSH host (e.g. bastion); empty = profile.host
    power_ssh_user: str = ""            # override SSH user; empty = profile.username
    power_ssh_port: int = 22            # SSH port (always 22, not the teraguchi streaming port)
    power_ssh_key: str = ""             # path to SSH private key; empty = SSH agent
    # Wake-on-LAN
    power_wol_mac: str = ""             # MAC address for WoL (e.g. "aa:bb:cc:dd:ee:ff")
    power_wol_broadcast: str = "255.255.255.255"

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
