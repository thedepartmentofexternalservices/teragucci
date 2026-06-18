"""Phase 3 D-14 + D-09 — InfoToast non-modal transient notification.

UI-SPEC Surface 8 (oversize-image toast — Plan 06 use)
UI-SPEC Surface 9 (monitor-switched toast — Plan 05 use)

Stacks vertically sm=8 px apart, max 3 visible. 360 px fixed width.
Fade-in 120 ms / fade-out 200 ms. Hover pauses auto-dismiss. Click or
Esc dismisses.

Threat T-03-19 mitigation: _MAX_VISIBLE = 3 caps the on-screen stack so
a malicious server emitting 10000 degradation events can't overwhelm
the client UI.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QWidget,
    QGraphicsOpacityEffect,
)

from client import theme

logger = logging.getLogger(__name__)

# Spacing tokens from UI-SPEC (keep in source so the verbatim-copy
# contract lives with the widget).
_MARGIN = 16       # md token
_STACK_GAP = 8     # sm token
_MAX_VISIBLE = 3   # T-03-19 mitigation
_WIDTH = 360       # fixed component dimension
_MIN_HEIGHT = 56   # component dimension
_FADE_IN_MS = 120
_FADE_OUT_MS = 200

# Module-level stack tracker. Not thread-safe, but toasts are only ever
# invoked from the Qt GUI thread via queued signals.
_toast_stack: list["InfoToast"] = []


class InfoToast(QFrame):
    """Reusable info-toast. Anchored bottom-right of ``parent``.

    Args:
        parent: QWidget to parent against (toast anchors bottom-right of
            this widget's geometry).
        icon: Optional QIcon drawn 16 px at the top-left of the body.
        title: Bold 13 px title (TEXT_PRIMARY).
        body: 12 px body copy (TEXT_SECONDARY). Word-wraps within 360 px.
        duration_ms: Auto-dismiss duration in ms (default 6 s). Hover
            stops the timer; leaveEvent resumes.
        border_color: Optional CSS color string override for the 3 px
            left border; defaults to ``theme.INFO``.
    """

    def __init__(
        self,
        parent: QWidget,
        icon: Optional[QIcon],
        title: str,
        body: str,
        duration_ms: int = 6000,
        border_color: Optional[str] = None,
    ):
        super().__init__(parent)
        self.setFixedWidth(_WIDTH)
        self.setMinimumHeight(_MIN_HEIGHT)
        self._duration = duration_ms
        # WR-03: track fade-out pending state so _enforce_max_visible
        # can skip toasts that are already dismissing without relying
        # on the fade-out animation to prune _toast_stack synchronously.
        self._dismiss_started = False

        border = border_color or theme.INFO
        self.setStyleSheet(
            f"InfoToast {{ background: {theme.BG_TERTIARY}; "
            f"border-left: 3px solid {border}; border-radius: 4px; }}"
        )

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 12, 12, 12)
        h.setSpacing(8)

        if icon is not None:
            ic = QLabel()
            ic.setPixmap(icon.pixmap(16, 16))
            h.addWidget(ic, 0, Qt.AlignTop)

        v = QVBoxLayout()
        v.setSpacing(4)
        t = QLabel(title)
        t.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {theme.TEXT_PRIMARY};"
        )
        t.setWordWrap(True)
        v.addWidget(t)
        b = QLabel(body)
        b.setStyleSheet(
            f"font-size: 12px; font-weight: 400; color: {theme.TEXT_SECONDARY};"
        )
        b.setWordWrap(True)
        v.addWidget(b)
        h.addLayout(v)

        # Fade-in/out driven by QGraphicsOpacityEffect on the toast.
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._fade_in = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_in.setDuration(_FADE_IN_MS)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)

        self._fade_out = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_out.setDuration(_FADE_OUT_MS)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.finished.connect(self._on_fade_out_done)

        # Auto-dismiss timer.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    def show(self):
        super().show()
        self._fade_in.start()
        self._timer.start(self._duration)
        _toast_stack.append(self)
        _enforce_max_visible()
        _relayout_stack(self.parentWidget())

    def enterEvent(self, ev):
        self._timer.stop()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._timer.start(self._duration)
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        self.dismiss()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.dismiss()
            return
        super().keyPressEvent(ev)

    def dismiss(self):
        # WR-03: mark as dismissing so _enforce_max_visible can skip this
        # entry while it's still in _toast_stack waiting for fade-out.
        self._dismiss_started = True
        self._timer.stop()
        self._fade_out.start()

    def _on_fade_out_done(self):
        if self in _toast_stack:
            _toast_stack.remove(self)
        _relayout_stack(self.parentWidget())
        self.deleteLater()


def _enforce_max_visible():
    """Dismiss oldest toasts when the active stack exceeds _MAX_VISIBLE.

    T-03-19 mitigation — caps on-screen widget count regardless of how
    many toast-invoking events the server emits.

    WR-03: Count only toasts that haven't begun fade-out yet (the
    ``_dismiss_started`` flag). During burst arrivals (e.g. 5 chained
    monitor-switched events), dismiss enough entries in a single pass
    so the active visible count drops to _MAX_VISIBLE — don't rely on
    fade-out finishing between ``show()`` calls to drain the stack.

    Iteration bounded by ``_toast_stack`` snapshot so a pathological
    ``dismiss()`` implementation can't hang the GUI thread.
    """
    # Snapshot the active (not-yet-dismissing) toasts in arrival order.
    active = [t for t in _toast_stack if not getattr(t, "_dismiss_started", False)]
    overflow = len(active) - _MAX_VISIBLE
    if overflow <= 0:
        return
    # Dismiss the oldest `overflow` active toasts in one pass. Slicing
    # into the snapshot prevents mutation-during-iteration hazards even
    # if dismiss() triggered a synchronous fade-out that mutated
    # _toast_stack (it doesn't currently, but stays defensive).
    for t in active[:overflow]:
        t.dismiss()


def _relayout_stack(parent: Optional[QWidget]):
    """Anchor bottom-right with _MARGIN gap; stack vertically with _STACK_GAP."""
    if parent is None:
        return
    x_right = parent.width() - _WIDTH - _MARGIN
    y_bottom = parent.height() - _MARGIN
    for toast in reversed(_toast_stack):
        h = max(_MIN_HEIGHT, toast.sizeHint().height())
        y_bottom -= h
        toast.move(x_right, y_bottom)
        y_bottom -= _STACK_GAP


def show_oversize_image_toast(parent: QWidget, size_mb: float):
    """UI-SPEC Surface 8 — fired by Plan 06 clipboard image path.

    Copy verbatim: title ``"Clipboard image too large"``, body
    ``"{size_mb} MB exceeds the 64 MB limit. Copy the image as a file
    instead."`` (integer MB formatting per UI-SPEC).
    """
    from client.icons import icon_clipboard
    body = (
        f"{size_mb:.0f} MB exceeds the 64 MB limit. "
        "Copy the image as a file instead."
    )
    toast = InfoToast(
        parent=parent,
        icon=icon_clipboard(theme.INFO),
        title="Clipboard image too large",
        body=body,
        duration_ms=6000,
        border_color=theme.INFO,
    )
    toast.show()


def show_monitor_switched_toast(parent: QWidget, monitor_name: str):
    """UI-SPEC Surface 9 — fired by Plan 05 hot-plug fallback.

    Copy verbatim: title ``"Monitor switched"``, body
    ``"{monitor_name} is no longer available. Showing primary monitor."``
    """
    from client.icons import icon_monitor
    body = (
        f"{monitor_name} is no longer available. Showing primary monitor."
    )
    toast = InfoToast(
        parent=parent,
        icon=icon_monitor(theme.INFO),
        title="Monitor switched",
        body=body,
        duration_ms=6000,
        border_color=theme.INFO,
    )
    toast.show()
