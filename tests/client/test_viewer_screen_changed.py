"""Phase 3 D-06 + D-19 — viewer screenChanged slot recomputes caches.

Plan 03-04 Task 1 RED→GREEN. :meth:`client.viewer.RemoteViewer._on_screen_changed`
is connected to :attr:`QWindow.screenChanged`; on signal, the slot
recomputes the scaling cache AND re-emits pen proximity if
``_pen_was_in_proximity`` (D-19 × D-06 cross-trigger) so Flame sees a
fresh proximity-enter after the viewer crosses between screens of
different DPR.
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


def test_on_screen_changed_slot_exists_and_callable(qapp):
    """D-06 — viewer exposes an ``_on_screen_changed`` slot callable from tests."""
    from client.viewer import RemoteViewer
    v = RemoteViewer()
    assert hasattr(v, "_on_screen_changed"), (
        "RemoteViewer must define _on_screen_changed for the screenChanged connection"
    )
    assert callable(v._on_screen_changed)


def test_on_screen_changed_recomputes_scaling_cache(qapp, mock_nsscreen, monkeypatch):
    """D-06 — slot re-runs ``_update_scaling`` when the widget migrates screens."""
    from client.viewer import RemoteViewer

    v = RemoteViewer()
    calls = {"n": 0}

    def fake_update_scaling():
        calls["n"] += 1

    monkeypatch.setattr(v, "_update_scaling", fake_update_scaling)
    # Stub _current_screen_dpr so the slot does not need real windowHandle.
    monkeypatch.setattr(v, "_current_screen_dpr", lambda: 1.0)

    before = calls["n"]
    v._on_screen_changed(mock_nsscreen[1])
    assert calls["n"] == before + 1, (
        "_on_screen_changed must call _update_scaling to refresh widget↔server-px "
        f"cache; expected {before + 1} calls, got {calls['n']}"
    )


def test_on_screen_changed_re_emits_pen_proximity(qapp, mock_nsscreen, monkeypatch):
    """D-06 × D-19 — slot re-emits pen_proximity when pen was in proximity (Pitfall 2)."""
    from client.viewer import RemoteViewer

    v = RemoteViewer()
    # Simulate: pen was in proximity before the screen change.
    v._pen_was_in_proximity = True
    v._last_pen_type = "pen"

    # Stub scaling hooks so the slot focuses on the proximity path.
    monkeypatch.setattr(v, "_update_scaling", lambda: None)
    monkeypatch.setattr(v, "_current_screen_dpr", lambda: 1.0)

    received: list[dict] = []
    v.pen_proximity.connect(received.append)

    v._on_screen_changed(mock_nsscreen[1])

    assert len(received) == 1, (
        f"screenChanged with prior-in-proximity must re-emit pen_proximity "
        f"(Pitfall 2 — symmetric to Phase 2 D-19 focusIn re-synth); got {received!r}"
    )
    assert received[0].get("in_proximity") is True
    assert received[0].get("pen_type") == "pen"


def test_on_screen_changed_without_prior_proximity_does_not_emit(qapp, mock_nsscreen, monkeypatch):
    """Pen never in proximity → slot must NOT emit pen_proximity (wire-noise guard)."""
    from client.viewer import RemoteViewer

    v = RemoteViewer()
    # _pen_was_in_proximity defaults to False.
    monkeypatch.setattr(v, "_update_scaling", lambda: None)
    monkeypatch.setattr(v, "_current_screen_dpr", lambda: 1.0)

    received: list[dict] = []
    v.pen_proximity.connect(received.append)

    v._on_screen_changed(mock_nsscreen[1])

    assert received == [], (
        f"screenChanged without prior-in-proximity must NOT emit pen_proximity; "
        f"got {received!r}"
    )
