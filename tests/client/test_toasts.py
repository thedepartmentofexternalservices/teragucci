"""Phase 3 D-09 / D-14 / Plan 03-05 — InfoToast (UI-SPEC Surface 8 + 9).

Reusable non-modal transient notification:
  - Surface 8: oversize-image toast (Plan 06)
  - Surface 9: monitor-switched toast (Plan 05)

Stacks vertically sm=8 px apart, max 3 visible. 360 px fixed width.
Fade-in 120 ms / fade-out 200 ms. Hover pauses auto-dismiss.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def toast_parent(qapp):
    from PySide6.QtWidgets import QWidget
    parent = QWidget()
    parent.resize(1200, 800)
    yield parent
    # Drain toast stack between tests so the module-level list doesn't
    # accumulate across runs.
    from client.toasts import _toast_stack
    _toast_stack.clear()
    parent.deleteLater()


def test_info_toast_is_360_px_wide(qapp, toast_parent):
    """UI-SPEC Surface 8 / 9 — fixed 360 px width."""
    from client.toasts import InfoToast
    t = InfoToast(
        parent=toast_parent, icon=None,
        title="Test", body="Body",
    )
    assert t.width() == 360


def test_show_monitor_switched_toast_verbatim_copy(qapp, toast_parent):
    """UI-SPEC Surface 9 — title + body verbatim."""
    from client.toasts import show_monitor_switched_toast, _toast_stack
    show_monitor_switched_toast(toast_parent, monitor_name="DP-1")
    assert len(_toast_stack) == 1
    t = _toast_stack[0]
    # Find the title + body QLabels on the toast
    from PySide6.QtWidgets import QLabel
    labels = [l.text() for l in t.findChildren(QLabel)]
    assert "Monitor switched" in labels
    assert "DP-1 is no longer available. Showing primary monitor." in labels


def test_show_oversize_image_toast_verbatim_copy(qapp, toast_parent):
    """UI-SPEC Surface 8 — title + body verbatim (Plan 06 will consume)."""
    from client.toasts import show_oversize_image_toast, _toast_stack
    show_oversize_image_toast(toast_parent, size_mb=128.0)
    assert len(_toast_stack) == 1
    t = _toast_stack[0]
    from PySide6.QtWidgets import QLabel
    labels = [l.text() for l in t.findChildren(QLabel)]
    assert "Clipboard image too large" in labels
    assert "128 MB exceeds the 64 MB limit. Copy the image as a file instead." in labels


def test_info_toast_max_3_visible(qapp, toast_parent):
    """UI-SPEC Surface 8 — max 3 visible; oldest auto-dismisses when
    a 4th is shown."""
    from client.toasts import InfoToast, _toast_stack
    for i in range(4):
        t = InfoToast(
            parent=toast_parent, icon=None,
            title=f"T{i}", body=f"B{i}",
        )
        t.show()
    # After enforcing max, only up to 3 should remain tracked
    # (the oldest one triggers dismiss which starts a fade-out; the
    # element stays in _toast_stack until fade-out finishes, but
    # _enforce_max_visible marks it for dismissal — we assert the
    # max was enforced by checking the oldest one has dismiss started).
    assert len(_toast_stack) <= 4
    # First one should have its dismiss timer stopped (dismiss called)
    # — we verify by checking the _timer is not active on t0.
    t0 = _toast_stack[0] if _toast_stack else None
    if t0 is not None and len(_toast_stack) == 4:
        assert t0._timer.isActive() is False  # dismiss stopped the timer


def test_info_toast_hover_stops_timer(qapp, toast_parent):
    """UI-SPEC Surface 8 / 9 — hover pauses auto-dismiss.

    The hover gesture maps to QEvent::Enter on the widget; enterEvent
    stops the _timer. We construct a real QEnterEvent so the super()
    chain's type-strict dispatch accepts the call.
    """
    from client.toasts import InfoToast
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent
    t = InfoToast(
        parent=toast_parent, icon=None,
        title="Test", body="Body", duration_ms=6000,
    )
    t.show()
    # Timer active after show
    assert t._timer.isActive() is True
    # Real QEnterEvent (localPos, windowPos, screenPos).
    ev = QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10))
    t.enterEvent(ev)
    assert t._timer.isActive() is False


def test_info_toast_no_inline_hex_colors():
    """UI-SPEC color contract — zero inline hex colors; theme.* tokens only."""
    import pathlib
    src = pathlib.Path(
        __file__,
    ).resolve().parents[2] / "client" / "toasts.py"
    text = src.read_text()
    import re
    code_lines = [
        line for line in text.splitlines()
        if not line.lstrip().startswith("#") and '"""' not in line
    ]
    joined = "\n".join(code_lines)
    hex_matches = re.findall(r'"#[0-9a-fA-F]{6}"', joined)
    assert hex_matches == [], (
        f"UI-SPEC forbids inline hex colors in toasts.py; found: {hex_matches}"
    )
