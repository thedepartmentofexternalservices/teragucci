"""
Client-side health monitor display widget.

Shows connection quality metrics as an overlay and in the status bar:
- Latency (RTT)
- FPS (actual vs target)
- Bandwidth usage
- Dropped frames
- Codec and chroma info
- Encode/capture timing
"""

import time
import collections
import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QFont, QPen
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QGridLayout, QFrame

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# D-03: color-capability badge for the health overlay
# ---------------------------------------------------------------------------


def render_color_badge(color_caps: Any) -> str:
    """Render the '10-bit: <state>' badge line from ServerHelloMsg.color_caps.

    Accepts either a dataclass (ServerColorCaps) or a plain dict (the
    client-side parsed JSON before dataclass promotion). Defaults to
    'not_supported' when the field is missing — the conservative
    degraded-UX answer per D-03.
    """
    state = getattr(color_caps, "negotiated_state", None)
    if state is None and isinstance(color_caps, dict):
        state = color_caps.get("negotiated_state")
    if not state:
        state = "not_supported"
    return f"10-bit: {state}"


class HealthData:
    """Accumulates health data from server stats and local measurements."""

    def __init__(self):
        # From server HealthStats
        self.rtt_ms: float = 0.0
        self.fps_actual: float = 0.0
        self.fps_target: float = 0.0
        self.bandwidth_mbps: float = 0.0
        self.frames_sent: int = 0
        self.frames_dropped: int = 0
        self.encode_time_ms: float = 0.0
        self.capture_time_ms: float = 0.0
        self.input_latency_ms: float = 0.0
        self.codec: str = ""
        self.chroma: str = ""
        self.resolution: str = ""
        self.encoder_backend: str = ""  # "nvenc", "vaapi", "amf", "software"
        self.decoder_backend: str = ""  # "cuda", "vaapi", "software"

        # Transport
        self.transport_mode: str = "tcp"  # "tcp" or "udp"
        self.packet_loss_pct: float = 0.0
        self.jitter_ms: float = 0.0
        self.buffer_depth_ms: float = 0.0

        # D-03: server-advertised color capability negotiation. Populated
        # from ServerHelloMsg.color_caps on handshake. Rendered as the
        # '10-bit: <state>' badge in the overlay. The value can be a
        # ServerColorCaps dataclass (once common.messages lands it) or a
        # plain dict parsed off the wire.
        self.color_caps: Any = None

        # Local measurements
        self._ping_times: collections.deque = collections.deque(maxlen=60)
        self._local_frame_count = 0
        self._local_fps_timer = time.time()
        self._local_fps: float = 0.0

    def update_from_server(self, stats: dict):
        """Update from a server HealthStats message."""
        self.rtt_ms = stats.get("rtt_ms", 0.0)
        self.fps_actual = stats.get("fps_actual", 0.0)
        self.fps_target = stats.get("fps_target", 0.0)
        self.bandwidth_mbps = stats.get("bandwidth_mbps", 0.0)
        self.frames_sent = stats.get("frames_sent", 0)
        self.frames_dropped = stats.get("frames_dropped", 0)
        self.encode_time_ms = stats.get("encode_time_ms", 0.0)
        self.capture_time_ms = stats.get("capture_time_ms", 0.0)
        self.input_latency_ms = stats.get("input_latency_ms", 0.0)
        self.codec = stats.get("codec", "")
        self.chroma = stats.get("chroma", "")
        self.resolution = stats.get("resolution", "")

    def record_local_ping(self, rtt_ms: float):
        self._ping_times.append(rtt_ms)

    def record_frame_received(self):
        self._local_frame_count += 1
        now = time.time()
        elapsed = now - self._local_fps_timer
        if elapsed >= 1.0:
            self._local_fps = self._local_frame_count / elapsed
            self._local_frame_count = 0
            self._local_fps_timer = now

    @property
    def local_fps(self) -> float:
        return self._local_fps

    @property
    def local_avg_rtt(self) -> float:
        if not self._ping_times:
            return 0.0
        return sum(self._ping_times) / len(self._ping_times)

    @property
    def quality_color(self) -> str:
        """Return a color representing overall connection quality."""
        rtt = self.rtt_ms if self.rtt_ms > 0 else self.local_avg_rtt
        if rtt < 30:
            return "#00ff00"   # Green - excellent
        elif rtt < 60:
            return "#88ff00"   # Yellow-green - good
        elif rtt < 100:
            return "#ffaa00"   # Orange - fair
        elif rtt < 200:
            return "#ff4400"   # Red-orange - poor
        else:
            return "#ff0000"   # Red - bad


class HealthOverlay(QWidget):
    """
    Semi-transparent overlay widget showing connection health stats.

    Drawn on top of the remote viewer, toggled with a hotkey.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = HealthData()
        self._visible = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def toggle(self):
        self._visible = not self._visible
        self.update()

    @property
    def is_showing(self) -> bool:
        return self._visible

    def paintEvent(self, event):
        if not self._visible:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background — semi-transparent panel with subtle border
        margin = 12
        box_w = 280
        box_h = 388  # +18 for the '10-bit: <state>' badge line (D-03)
        x = self.width() - box_w - margin
        y = margin

        # Panel background
        bg = QColor(10, 10, 16, 210)
        painter.setBrush(bg)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(x, y, box_w, box_h), 10, 10)

        # Subtle border
        border = QColor(38, 38, 64, 120)
        painter.setPen(border)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(x, y, box_w, box_h), 10, 10)

        # Accent line at top
        accent = QColor(0, 200, 120, 160)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(x + 1, y + 1, box_w - 2, 2), 1, 1)

        # Text — monospace for that command center feel
        font = QFont("SF Mono", 10)
        font.setStyleHint(QFont.Monospace)
        painter.setFont(font)
        painter.setPen(QColor(self.data.quality_color))

        d = self.data
        line_h = 18
        tx = x + 12
        ty = y + 20

        transport = "UDP" if d.transport_mode == "udp" else "TCP"
        enc_info = d.encoder_backend.upper() if d.encoder_backend else "SW"
        dec_info = d.decoder_backend.upper() if d.decoder_backend else "SW"
        color_badge = (
            render_color_badge(d.color_caps)
            if d.color_caps is not None
            else "10-bit: not_supported"
        )
        lines = [
            f"Transport:  {transport}",
            f"Latency:    {d.rtt_ms:.0f} ms",
            f"FPS:        {d.fps_actual:.0f} / {d.fps_target:.0f}",
            f"Local FPS:  {d.local_fps:.0f}",
            f"Bandwidth:  {d.bandwidth_mbps:.1f} Mbps",
            f"Codec:      {d.codec.upper()} {d.chroma.upper()}",
            color_badge,
            f"Encoder:    {enc_info}",
            f"Decoder:    {dec_info}",
            f"Resolution: {d.resolution}",
            "",
            f"Encode:     {d.encode_time_ms:.1f} ms",
            f"Capture:    {d.capture_time_ms:.1f} ms",
            f"Input lag:  {d.input_latency_ms:.1f} ms",
            "",
            f"Pkt loss:   {d.packet_loss_pct:.1f}%",
            f"Jitter:     {d.jitter_ms:.1f} ms",
            f"Buffer:     {d.buffer_depth_ms:.1f} ms",
            f"Sent:       {d.frames_sent}",
            f"Dropped:    {d.frames_dropped}",
        ]

        for i, line in enumerate(lines):
            painter.drawText(tx, ty + i * line_h, line)

        painter.end()


class HealthStatusWidget(QFrame):
    """
    Compact health indicator for the status bar.

    Shows a colored dot and key metrics in a single line.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = HealthData()
        layout = QGridLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        self._dot = QLabel()
        self._dot.setFixedSize(10, 10)
        self._dot.setStyleSheet("border-radius: 5px; background: #666;")
        layout.addWidget(self._dot, 0, 0)

        self._latency_label = QLabel("-- ms")
        self._latency_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._latency_label, 0, 1)

        self._fps_label = QLabel("-- fps")
        self._fps_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._fps_label, 0, 2)

        self._bw_label = QLabel("-- Mbps")
        self._bw_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._bw_label, 0, 3)

        self._codec_label = QLabel("")
        self._codec_label.setStyleSheet("font-size: 11px; color: #888;")
        layout.addWidget(self._codec_label, 0, 4)

    def update_display(self):
        """Refresh the status display from current health data."""
        d = self.data
        color = d.quality_color
        self._dot.setStyleSheet(f"border-radius: 5px; background: {color};")
        self._latency_label.setText(f"{d.rtt_ms:.0f} ms")
        self._fps_label.setText(f"{d.fps_actual:.0f} fps")
        self._bw_label.setText(f"{d.bandwidth_mbps:.1f} Mbps")
        codec_str = f"{d.codec.upper()}"
        if d.chroma:
            codec_str += f" {d.chroma.upper()}"
        self._codec_label.setText(codec_str)
