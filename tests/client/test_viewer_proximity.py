"""Phase 2 D-19 — RemoteViewer pen-proximity re-synth (Plan 02-10 Task 3).

Acceptance (from Plan 02-10 Task 3):

  * ``RemoteViewer.showEvent`` emits a ``pen_proximity`` signal with
    ``in_proximity=True`` (catches Cmd-Tab / lockscreen / restore cycles).
  * ``RemoteViewer.focusInEvent`` emits ``pen_proximity`` with
    ``in_proximity=True`` *iff* the pen was in proximity before the
    focus-out (the ``_pen_was_in_proximity`` bookkeeping flag).

Follows the ``tests/client/test_viewer_modifier_triggers.py`` fixture
shape (02-09) — no pytest-qt dependency; ``QT_QPA_PLATFORM=offscreen``
QApplication fixture keeps CI headless-clean.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("PySide6.QtGui")


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication (offscreen)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_show_event_emits_pen_proximity(qapp):
    """showEvent → pen_proximity with in_proximity=True (D-19)."""
    from PySide6.QtGui import QShowEvent

    from client.viewer import RemoteViewer
    v = RemoteViewer()
    received: list[dict] = []
    v.pen_proximity.connect(received.append)

    ev = QShowEvent()
    v.showEvent(ev)

    assert len(received) == 1, (
        f"showEvent must emit exactly one pen_proximity; got {received!r}"
    )
    assert received[0].get("in_proximity") is True, (
        f"showEvent pen_proximity must have in_proximity=True; got {received!r}"
    )


def test_focus_in_after_prior_proximity_emits_pen_proximity(qapp):
    """focusInEvent emits pen_proximity only if _pen_was_in_proximity was True."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent

    from client.viewer import RemoteViewer

    v = RemoteViewer()
    # Simulate the viewer was previously in proximity (state tracked in
    # tabletEvent; we set it directly for this unit test).
    v._pen_was_in_proximity = True
    received: list[dict] = []
    v.pen_proximity.connect(received.append)

    ev = QFocusEvent(QEvent.Type.FocusIn)
    v.focusInEvent(ev)

    assert len(received) == 1, (
        f"focusInEvent after prior-in-proximity must emit exactly one "
        f"pen_proximity; got {received!r}"
    )
    assert received[0].get("in_proximity") is True


def test_focus_in_without_prior_proximity_does_not_emit(qapp):
    """Pen was never in proximity → focusInEvent must NOT emit pen_proximity.

    Per D-19 the re-synth exists to recover the server FSM after the
    client viewer's pen *was* in-proximity and then lost focus. A
    fresh-focus where the pen never approached the tablet must not
    synthesize a spurious proximity event (the server's PenFSM would
    stay correct either way — idempotency — but the wire traffic is
    wasted bandwidth and pollutes the telemetry).
    """
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent

    from client.viewer import RemoteViewer

    v = RemoteViewer()
    # _pen_was_in_proximity defaults to False (never touched the tablet)
    received: list[dict] = []
    v.pen_proximity.connect(received.append)

    ev = QFocusEvent(QEvent.Type.FocusIn)
    v.focusInEvent(ev)

    assert received == [], (
        f"focusInEvent without prior-in-proximity must NOT emit "
        f"pen_proximity; got {received!r}"
    )
