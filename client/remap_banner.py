"""Phase 3 D-09 / DISP-02 — non-modal topology-change banner.

UI-SPEC Surface 5 / C-05. 48 px tall, full viewer width, slides in
from the top with 200 ms OutCubic. WARNING 3 px left border. Sticky
until dismissed — no auto-timeout (mid-session topology changes are
consequential; a 6 s auto-dismiss could be missed while an artist is
mid-stroke).

Keyboard:
  - Esc: dismiss the banner
  - Enter: activate the action link (only present in pick_missing case)

Copy dictionary (verbatim per UI-SPEC Surface 5):
  - pick_missing:  "Showing primary monitor. Click to choose a different one."
                   + "Choose monitor →" action link
  - mirror_add:    "A new monitor is now visible." (no action)
  - mirror_remove: "A monitor was removed. Continuing with the rest." (no action)
  - single_change: "Monitor layout changed. Continuing on primary." (no action)

Threat T-03-19 mitigation: single-instance widget — subsequent
show_for_case() calls update the existing banner's text rather than
spawning a stack.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import (
    Qt, Signal, QPropertyAnimation, QEasingCurve, QPoint,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from client import theme

logger = logging.getLogger(__name__)

_HEIGHT = 48   # toolbar-rhythm dimension per UI-SPEC


# UI-SPEC Surface 5 verbatim copy contract.
# Key: case id; Value: (body_text, has_action_link)
_COPY: dict[str, tuple[str, bool]] = {
    "pick_missing":  (
        "Showing primary monitor. Click to choose a different one.", True,
    ),
    "mirror_add":    ("A new monitor is now visible.", False),
    "mirror_remove": (
        "A monitor was removed. Continuing with the rest.", False,
    ),
    "single_change": (
        "Monitor layout changed. Continuing on primary.", False,
    ),
}


class RemapBanner(QFrame):
    """D-09 / UI-SPEC Surface 5 — non-modal remap banner."""

    # Emitted when the banner is dismissed (Esc, × click, or after
    # action-link click completes its animation).
    dismissed = Signal()
    # Emitted when the pick_missing action link is clicked.
    remap_requested = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedHeight(_HEIGHT)
        self.setVisible(False)
        self.setStyleSheet(
            f"RemapBanner {{ background: {theme.BG_TERTIARY}; "
            f"border-left: 3px solid {theme.WARNING}; }}"
        )

        h = QHBoxLayout(self)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(12)

        self._title = QLabel("Monitor layout changed")
        self._title.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {theme.WARNING};"
        )
        h.addWidget(self._title)

        self._body = QLabel("")
        self._body.setStyleSheet(
            f"font-size: 12px; font-weight: 400; color: {theme.TEXT_SECONDARY};"
        )
        self._body.setWordWrap(True)
        h.addWidget(self._body, 1)

        self._action = QPushButton("Choose monitor →")
        self._action.setFlat(True)
        self._action.setCursor(Qt.PointingHandCursor)
        self._action.setStyleSheet(
            f"QPushButton {{ font-size: 13px; font-weight: 600; "
            f"color: {theme.ACCENT}; border: none; padding: 0 8px; }}"
            f"QPushButton:hover {{ text-decoration: underline; }}"
        )
        self._action.clicked.connect(self._on_action_clicked)
        self._action.setVisible(False)
        h.addWidget(self._action)

        self._dismiss = QPushButton("×")
        self._dismiss.setFlat(True)
        self._dismiss.setFixedSize(16, 16)
        self._dismiss.setStyleSheet(
            f"QPushButton {{ color: {theme.TEXT_SECONDARY}; border: none; "
            f"font-size: 16px; }}"
            f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; }}"
        )
        self._dismiss.clicked.connect(self.dismiss)
        h.addWidget(self._dismiss)

        # Slide-in animation — 200 ms OutCubic matches fullscreen_toolbar
        # reveal cadence so the motion vocabulary stays consistent.
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def show_for_case(self, case: str, picked_name: str = ""):
        """Render copy per UI-SPEC Surface 5 case key.

        Unknown case values fall back to ``single_change`` (the most
        conservative "topology changed" copy).
        """
        body, has_action = _COPY.get(case, _COPY["single_change"])
        self._body.setText(body)
        self._action.setVisible(has_action)

        parent = self.parentWidget()
        if parent is not None:
            self.setFixedWidth(parent.width())
            self.move(0, -_HEIGHT)
            self.show()
            self.raise_()
            self._anim.stop()
            self._anim.setStartValue(self.pos())
            self._anim.setEndValue(QPoint(0, 0))
            self._anim.start()

        # Accept focus so Esc + Enter fire keyPressEvent.
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus(Qt.OtherFocusReason)

        logger.info(
            "remap_banner.shown case=%s picked_name=%r",
            case, picked_name,
        )

    def dismiss(self):
        """Slide the banner back up and hide it."""
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(QPoint(0, -_HEIGHT))
        try:
            self._anim.finished.connect(self._after_dismiss)
        except TypeError:
            pass
        self._anim.start()

    def _after_dismiss(self):
        try:
            self._anim.finished.disconnect(self._after_dismiss)
        except (TypeError, RuntimeError):
            pass
        self.hide()
        self.dismissed.emit()

    def _on_action_clicked(self):
        self.remap_requested.emit()
        self.dismiss()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.dismiss()
            return
        if ev.key() == Qt.Key_Return and self._action.isVisible():
            self._on_action_clicked()
            return
        super().keyPressEvent(ev)
