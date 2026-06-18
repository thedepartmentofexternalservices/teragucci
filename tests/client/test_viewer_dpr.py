"""Phase 3 D-06 — per-screen devicePixelRatio lookup.

Plan 03-04 Task 1 RED→GREEN. :meth:`client.viewer.RemoteViewer._current_screen_dpr`
looks up the DPR of the QScreen the viewer widget is currently on via
``windowHandle().screen().devicePixelRatio()``, falling back to the
widget's ``devicePixelRatioF()`` when ``windowHandle()`` is None
(pre-show / offscreen cases).

Fixes the PITFALLS #5 "cursor lands a bit left of where I click" class
of bug on mixed-DPI Mac clients (Cintiq Pro 24 + Retina).
"""
from __future__ import annotations

import os

import pytest


pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("PySide6.QtGui")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_current_screen_dpr_uses_widget_screen(qapp, mock_nsscreen, monkeypatch):
    """D-06 — viewer on external 4K (screen[1]) returns 1.0, not primary's 2.0."""
    from client.viewer import RemoteViewer
    from types import SimpleNamespace

    v = RemoteViewer()

    # Simulate the viewer's top-level window living on screen[1] (1.0 DPR
    # external 4K). We stand in for window().windowHandle() by injecting
    # a fake window object with a ``screen()`` method.
    fake_window = SimpleNamespace(screen=lambda: mock_nsscreen[1])
    fake_top = SimpleNamespace(windowHandle=lambda: fake_window)
    monkeypatch.setattr(v, "window", lambda: fake_top)

    dpr = v._current_screen_dpr()
    assert dpr == 1.0, (
        f"expected DPR 1.0 (external 4K screen[1]); got {dpr} — the viewer "
        f"must look up its CURRENT screen's DPR, not the primary's (Pitfall 1)"
    )


def test_current_screen_dpr_picks_retina_when_on_retina(qapp, mock_nsscreen, monkeypatch):
    """Viewer on screen[0] (Retina, DPR 2.0) returns 2.0 — symmetric test."""
    from client.viewer import RemoteViewer
    from types import SimpleNamespace

    v = RemoteViewer()
    fake_window = SimpleNamespace(screen=lambda: mock_nsscreen[0])
    fake_top = SimpleNamespace(windowHandle=lambda: fake_window)
    monkeypatch.setattr(v, "window", lambda: fake_top)

    assert v._current_screen_dpr() == 2.0


def test_current_screen_dpr_falls_back_when_windowhandle_none(qapp, monkeypatch):
    """D-06 — graceful fallback via ``devicePixelRatioF()`` pre-show.

    windowHandle() is None until the native window is created (usually
    after showEvent). The helper must return devicePixelRatioF() rather
    than crashing in that window.
    """
    from client.viewer import RemoteViewer
    from types import SimpleNamespace

    v = RemoteViewer()
    # Force windowHandle() to None + stub devicePixelRatioF to a known value.
    fake_top = SimpleNamespace(windowHandle=lambda: None)
    monkeypatch.setattr(v, "window", lambda: fake_top)
    monkeypatch.setattr(v, "devicePixelRatioF", lambda: 1.5)

    dpr = v._current_screen_dpr()
    assert dpr == 1.5, (
        f"pre-show fallback must use devicePixelRatioF(); got {dpr}"
    )


def test_f12_no_op_without_debug_env(qapp, monkeypatch):
    """Pitfall 8 — F12 must NOT install the overlay handler in release builds.

    Gate is at ``RemoteViewer.__init__`` — if ``TERAGUCHI_DEBUG`` is not
    set to exactly ``"1"``, ``_coord_overlay`` stays None and F12 stays
    a pass-through.
    """
    monkeypatch.delenv("TERAGUCHI_DEBUG", raising=False)
    from client.viewer import RemoteViewer
    v = RemoteViewer()
    assert v._coord_overlay is None, (
        "F12 overlay must not be instantiated in release builds "
        "(TERAGUCHI_DEBUG unset) — Pitfall 8"
    )


def test_f12_overlay_installed_with_debug_env(qapp, monkeypatch):
    """Pitfall 8 — with TERAGUCHI_DEBUG=1, overlay exists but starts hidden."""
    monkeypatch.setenv("TERAGUCHI_DEBUG", "1")
    from client.viewer import RemoteViewer
    v = RemoteViewer()
    assert v._coord_overlay is not None, (
        "F12 overlay must be instantiated when TERAGUCHI_DEBUG=1"
    )
    assert v._coord_overlay.isVisible() is False, (
        "F12 overlay must start hidden; user presses F12 to reveal"
    )
