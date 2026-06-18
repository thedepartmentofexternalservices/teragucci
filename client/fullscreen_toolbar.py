"""
Auto-hiding slide-down toolbar for fullscreen mode.

Appears when the cursor touches the top edge of the screen.
Hides automatically when the cursor moves away.
"""

import logging

from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QPoint, QEasingCurve, Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton,
    QGraphicsDropShadowEffect, QFrame,
)

from client import theme

logger = logging.getLogger(__name__)

TOOLBAR_HEIGHT = 48
REVEAL_ZONE = 3          # pixels from top edge to trigger reveal
HIDE_DELAY_MS = 1200     # ms after mouse leaves before hiding


# ════════════════════════════════════════════════════
# Phase 3 D-01 / UI-SPEC Surface 2 — Mode badge
# ════════════════════════════════════════════════════


class _ModeBadge(QFrame):
    """Read-only mode badge per UI-SPEC Surface 2.

    Renders one of:
      * ``Mode: single``
      * ``Mode: mirror``
      * ``Mode: pick: {name}``
      * ``Mode: pick → primary``   (degraded; WARNING-colored underline)

    Tooltip locked verbatim:
      * normal:   "Monitor mode is fixed for this session. Disconnect
                   and reconnect to change."
      * degraded: "{monitor_name} disappeared. Showing primary monitor
                   instead."

    2 px accent underline painted under the label (ACCENT when active,
    WARNING when degraded). GOLD dot left of the label signals
    "locked informational state" per UI-SPEC color row (distinct from
    selectable-ACCENT). The badge is visible only while a session
    advertises a live mode — clearing via ``update("")`` hides it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = ""
        self._picked_name = ""
        self._degraded = False

        self._label = QLabel("")
        self._label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {theme.TEXT_PRIMARY};"
        )
        self._dot = QLabel("●")
        self._dot.setStyleSheet(
            f"color: {theme.GOLD}; font-size: 10px;"
        )
        self._dot.setToolTip(
            "Monitor mode is locked for the active session."
        )

        h = QHBoxLayout(self)
        h.setContentsMargins(8, 0, 8, 0)   # sm token
        h.setSpacing(4)                    # xs token
        h.addWidget(self._dot)
        h.addWidget(self._label)

        self.setVisible(False)  # only visible during an active session
        self.setToolTip(
            "Monitor mode is fixed for this session. "
            "Disconnect and reconnect to change."
        )

    def update_state(self, mode: str, picked_name: str = "",
                     degraded: bool = False) -> None:
        """Update badge text + tooltip + underline per mode/degraded.

        Named ``update_state`` (not ``update``) to avoid shadowing the
        QWidget.update() repaint method — we still call super().update()
        below to trigger a paintEvent for the underline.
        """
        self._mode = mode
        self._picked_name = picked_name
        self._degraded = degraded

        if not mode:
            self.setVisible(False)
            return

        locked_tip = (
            "Monitor mode is fixed for this session. "
            "Disconnect and reconnect to change."
        )

        if degraded and mode == "pick_one":
            text = "Mode: pick → primary"
            self.setToolTip(
                f"{picked_name} disappeared. Showing primary monitor instead."
            )
        elif mode == "pick_one":
            text = f"Mode: pick: {picked_name}"
            self.setToolTip(locked_tip)
        elif mode == "mirror_all":
            text = "Mode: mirror"
            self.setToolTip(locked_tip)
        else:
            text = "Mode: single"
            self.setToolTip(locked_tip)

        self._label.setText(text)
        self.setVisible(True)
        self.update()  # repaint underline

    def paintEvent(self, ev):
        super().paintEvent(ev)
        # 2 px underline beneath the label per UI-SPEC Surface 2
        p = QPainter(self)
        color_str = theme.WARNING if self._degraded else theme.ACCENT
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color_str))
        r = self._label.geometry()
        p.drawRect(r.left(), self.height() - 2, r.width(), 2)
        p.end()


class FullscreenToolbar(QWidget):
    """
    Slide-down toolbar that appears at the top of the screen in fullscreen mode.
    """

    exit_fullscreen = Signal()
    disconnect_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedHeight(TOOLBAR_HEIGHT)
        self.setMouseTracking(True)

        self._visible = False
        self._animation = QPropertyAnimation(self, b"pos")
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._slide_up)

        # Shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        # Connection label
        self._conn_label = QLabel("Not Connected")
        self._conn_label.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-weight: 600; font-size: 13px;")
        layout.addWidget(self._conn_label)

        layout.addSpacing(8)

        # Separator
        sep = QLabel("|")
        sep.setStyleSheet(f"color: {theme.BORDER};")
        layout.addWidget(sep)

        layout.addSpacing(8)

        # Monitor selector
        from client.monitor_selector import MonitorSelector
        self._monitor_selector = MonitorSelector()
        self._monitor_selector.setMinimumWidth(120)
        self._monitor_selector.setMaximumWidth(200)
        layout.addWidget(self._monitor_selector)

        # Phase 3 D-15 / UI-SPEC Surface 7 / Plan 03-07 — clipboard direction
        # toggle button (4-checkbox nested menu). Inserts between the
        # MonitorSelector and the mode badge so the clipboard and monitor
        # affordances sit side-by-side in the left half of the toolbar.
        from client.clipboard_toggle_menu import ClipboardToggleButton
        self._clipboard_toggle = ClipboardToggleButton()
        layout.addSpacing(8)   # sm token
        layout.addWidget(self._clipboard_toggle)

        # Phase 3 D-01 / UI-SPEC Surface 2 — read-only mode badge.
        # Inserts between monitor selector and the addStretch so the
        # badge stays in the left half of the toolbar near the other
        # session-state affordances.
        self._mode_badge = _ModeBadge()
        layout.addSpacing(8)   # sm token separation from MonitorSelector
        layout.addWidget(self._mode_badge)

        layout.addStretch()

        # Health summary
        self._health_dot = QLabel()
        self._health_dot.setFixedSize(8, 8)
        self._health_dot.setStyleSheet(
            f"border-radius: 4px; background: {theme.TEXT_MUTED};")
        layout.addWidget(self._health_dot)

        self._latency_label = QLabel("-- ms")
        self._latency_label.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self._latency_label)

        self._fps_label = QLabel("-- fps")
        self._fps_label.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self._fps_label)

        layout.addSpacing(12)

        # Settings button
        settings_btn = QPushButton("Settings")
        settings_btn.setFixedHeight(30)
        settings_btn.clicked.connect(self.settings_requested.emit)
        layout.addWidget(settings_btn)

        # Disconnect button
        dc_btn = QPushButton("Disconnect")
        dc_btn.setFixedHeight(30)
        dc_btn.setProperty("danger", True)
        dc_btn.clicked.connect(self.disconnect_requested.emit)
        layout.addWidget(dc_btn)

        # Exit fullscreen
        exit_btn = QPushButton("Exit Fullscreen")
        exit_btn.setFixedHeight(30)
        exit_btn.clicked.connect(self.exit_fullscreen.emit)
        layout.addWidget(exit_btn)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Semi-transparent dark panel with subtle bottom border
        p.setBrush(QColor(10, 10, 16, 235))
        p.setPen(Qt.NoPen)
        p.drawRect(0, 0, self.width(), self.height())
        # Accent line at bottom edge
        p.setBrush(QColor(0, 200, 120, 80))
        p.drawRect(0, self.height() - 1, self.width(), 1)
        p.end()

    def position_on_screen(self, screen_geo):
        """Position above the screen (hidden) ready for slide-down."""
        w = screen_geo.width()
        x = screen_geo.x()
        self.setFixedWidth(w)
        self._screen_geo = screen_geo
        self._hidden_pos = QPoint(x, screen_geo.y() - TOOLBAR_HEIGHT)
        self._shown_pos = QPoint(x, screen_geo.y())
        self.move(self._hidden_pos)

    def reveal(self):
        if self._visible:
            self._hide_timer.stop()
            return
        self._visible = True
        self.show()
        self.raise_()
        self._animation.stop()
        self._animation.setStartValue(self.pos())
        self._animation.setEndValue(self._shown_pos)
        self._animation.start()

    def _slide_up(self):
        if not self._visible:
            return
        self._visible = False
        self._animation.stop()
        self._animation.setStartValue(self.pos())
        self._animation.setEndValue(self._hidden_pos)
        self._animation.start()

    def enterEvent(self, event):
        self._hide_timer.stop()

    def leaveEvent(self, event):
        self._hide_timer.start(HIDE_DELAY_MS)

    def update_health(self, health_data):
        color = health_data.quality_color
        self._health_dot.setStyleSheet(f"border-radius: 4px; background: {color};")
        self._latency_label.setText(f"{health_data.rtt_ms:.0f} ms")
        self._fps_label.setText(f"{health_data.fps_actual:.0f} fps")

    def set_connection_label(self, text: str):
        self._conn_label.setText(text)

    def update_monitors(self, monitors: list):
        self._monitor_selector.update_monitors(monitors)

    def update_capture_mode(self, mode: str, picked_name: str = "",
                            degraded: bool = False) -> None:
        """Phase 3 D-01 — toolbar slot for session capture-mode state.

        Session calls this on connect / disconnect / degraded-fallback
        events. Empty ``mode`` hides the badge (used on disconnect).
        """
        self._mode_badge.update_state(mode, picked_name, degraded)

    @property
    def monitor_selector(self):
        return self._monitor_selector

    @property
    def mode_badge(self):
        return self._mode_badge

    @property
    def clipboard_toggle(self):
        """Phase 3 D-15 / UI-SPEC Surface 7 — clipboard direction button."""
        return self._clipboard_toggle
