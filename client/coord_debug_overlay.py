"""Phase 3 D-07 — F12 coord-debug dev overlay.

Dev-only widget for diagnosing the "cursor lands a bit left of where I
click" class of bugs (mixed-DPI Cintiq + Retina — Pitfall 1). Gated on
``TERAGUCHI_DEBUG=1`` at **handler-install time** in
``client.viewer.RemoteViewer.__init__`` (per Pitfall 8: gate handler
installation, not visibility). Release builds leave
``RemoteViewer._coord_overlay = None`` and the F12 keyPressEvent branch
never fires — F12 falls through to the existing paste-detect + key-
wire pipeline.

UI-SPEC: Surface 6. Six text rows in the bottom-left corner of the
viewer widget, 11 pt monospace, bottom-left 12 px inherited margin
(health_display.py convention), 85% opacity `BG_PRIMARY`, 6 px rounded
corners, 1 px `BORDER`. `WA_TransparentForMouseEvents` so it never
blocks clicks.
"""
from __future__ import annotations

import logging
import sys
from typing import Optional, Tuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

from client import theme

logger = logging.getLogger(__name__)


# Spacing tokens per UI-SPEC (lg = 24 px, md = 16 px). _MARGIN 12 px is
# the inherited health_display.py / fullscreen_toolbar.py convention,
# NOT a new Phase 3 token (UI-SPEC explicit).
_MARGIN = 12
_PAD_X = 24   # lg inner horizontal padding
_PAD_Y = 16   # md inner vertical padding
_LINE_H = 16  # monospace 11 pt line height
_WIDTH = 380  # max 400 px per UI-SPEC; budget under that


class CoordDebugOverlay(QWidget):
    """UI-SPEC Surface 6 — F12 dev coord overlay.

    Renders:
      * Header `F12 · coord debug · {TERAGUCHI_DEBUG=1}` in ACCENT
      * `widget px : x, y`
      * `server px : x, y`
      * `DPR       : dpr (screen_name)`
      * `monitor   : name WxH+x+y`
      * `crop rect : WxH+x+y` (pick-one only)
      * `delta px  : dx, dy` (WARNING color when non-zero)
      * `F12 to hide` footer in TEXT_MUTED

    Call ``update_coords(...)`` on every mouseMoveEvent. Call
    ``update_screen(dpr, name)`` on screenChanged. Call ``reposition()``
    when the parent widget is resized.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        # Never blocks clicks — transparent-for-mouse + translucent bg.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setVisible(False)

        font_family = "SF Mono" if sys.platform == "darwin" else "Monospace"
        self._font = QFont(font_family, 11)
        self._footer_font = QFont(font_family, 10)

        self._widget_x: float = 0.0
        self._widget_y: float = 0.0
        self._server_x: int = 0
        self._server_y: int = 0
        self._dpr: float = 1.0
        self._screen_name: str = ""
        self._monitor: Optional[dict] = None
        self._crop: Optional[Tuple[int, int, int, int]] = None
        self._delta: Tuple[int, int] = (0, 0)

        self.resize(_WIDTH, 7 * _LINE_H + 2 * _PAD_Y)

    # ---- Public API ------------------------------------------------

    def update_coords(
        self,
        widget_x: float,
        widget_y: float,
        server_x: int,
        server_y: int,
        dpr: float,
        screen_name: str,
        monitor: Optional[dict] = None,
        crop: Optional[Tuple[int, int, int, int]] = None,
    ) -> None:
        """Refresh every coord / monitor / crop row and repaint."""
        self._widget_x = float(widget_x)
        self._widget_y = float(widget_y)
        self._server_x = int(server_x)
        self._server_y = int(server_y)
        self._dpr = float(dpr)
        self._screen_name = str(screen_name)
        self._monitor = monitor
        self._crop = crop
        self._delta = (
            int(widget_x) - int(server_x),
            int(widget_y) - int(server_y),
        )
        self.update()

    def update_screen(self, dpr: float, screen_name: str) -> None:
        """Refresh just the DPR + screen_name rows (screenChanged fast path)."""
        self._dpr = float(dpr)
        self._screen_name = str(screen_name)
        self.update()

    def reposition(self) -> None:
        """Anchor bottom-left of parent with _MARGIN px gap."""
        parent = self.parentWidget()
        if parent is None:
            return
        parent_h = parent.height()
        self.move(_MARGIN, max(_MARGIN, parent_h - self.height() - _MARGIN))

    def sizeHint(self) -> QSize:
        return QSize(_WIDTH, 7 * _LINE_H + 2 * _PAD_Y)

    # ---- Paint -----------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setFont(self._font)

        # Background — BG_PRIMARY at 85% opacity + BORDER 1 px + 6 px rounded.
        bg = QColor(theme.BG_PRIMARY)
        bg.setAlphaF(0.85)
        p.setBrush(bg)
        p.setPen(QColor(theme.BORDER))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 6, 6)

        x = _PAD_X
        y = _PAD_Y + 11  # first baseline (11 px font ascent)

        # Header — ACCENT (UI-SPEC Surface 6 line "header").
        p.setPen(QColor(theme.ACCENT))
        p.drawText(x, y, "F12 · coord debug · {TERAGUCHI_DEBUG=1}")
        y += _LINE_H + 2  # extra gap after header

        p.setPen(QColor(theme.TEXT_PRIMARY))

        p.drawText(x, y, f"widget px : {self._widget_x:.0f}, {self._widget_y:.0f}")
        y += _LINE_H
        p.drawText(x, y, f"server px : {self._server_x}, {self._server_y}")
        y += _LINE_H
        p.drawText(
            x, y,
            f"DPR       : {self._dpr:.2f} ({self._screen_name or 'unknown'})",
        )
        y += _LINE_H

        mon = self._monitor or {}
        m_name = mon.get("name", "—")
        m_w = int(mon.get("width", 0))
        m_h = int(mon.get("height", 0))
        m_x = int(mon.get("x", 0))
        m_y = int(mon.get("y", 0))
        p.drawText(x, y, f"monitor   : {m_name} {m_w}x{m_h}+{m_x}+{m_y}")
        y += _LINE_H

        if self._crop is not None:
            cx, cy, cw, ch = self._crop
            p.drawText(x, y, f"crop rect : {cw}x{ch}+{cx}+{cy}")
            y += _LINE_H

        # Delta — WARNING color when non-zero (UI-SPEC Surface 6 line 6).
        dx, dy = self._delta
        if dx != 0 or dy != 0:
            p.setPen(QColor(theme.WARNING))
        else:
            p.setPen(QColor(theme.TEXT_PRIMARY))
        p.drawText(x, y, f"delta px  : {dx}, {dy}")
        y += _LINE_H

        # Footer — TEXT_MUTED, 10 pt mono.
        p.setPen(QColor(theme.TEXT_MUTED))
        p.setFont(self._footer_font)
        p.drawText(x, y, "F12 to hide")
