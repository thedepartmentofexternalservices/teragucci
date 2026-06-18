"""Plan 03-05 / 03-06 — icon_clipboard landing (UI-SPEC Surface 7 SVG body).

Plan 06 will consume this icon in the clipboard toolbar toggle button;
landing it here keeps icons.py changes co-located with the banner+toast
surface work.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtGui")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_icon_clipboard_returns_qicon(qapp):
    """icon_clipboard() follows the 24×24 / 1.5 px-stroke icon convention
    and returns a valid QIcon (same contract as icon_monitor, icon_keyboard)."""
    from PySide6.QtGui import QIcon
    from client.icons import icon_clipboard
    icon = icon_clipboard()
    assert isinstance(icon, QIcon)
    # Icon has at least one pixmap rendering available at 16 px.
    pm = icon.pixmap(16, 16)
    assert not pm.isNull()


def test_icon_clipboard_accepts_color_argument(qapp):
    """icon_clipboard() accepts an override color (matches theme tokens)."""
    from client.icons import icon_clipboard
    from client import theme
    icon_default = icon_clipboard()
    icon_info = icon_clipboard(theme.INFO)
    # Both return valid icons; the cache key differs so they're distinct.
    assert not icon_default.pixmap(16, 16).isNull()
    assert not icon_info.pixmap(16, 16).isNull()
