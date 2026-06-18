"""Phase 2 D-11 / D-15 — RemoteViewer modifier-release + IME-commit triggers.

Plan 02-09 Task 1 / Task 2 acceptance:

  * ``RemoteViewer.focusOutEvent`` emits ``reset_modifiers_requested("focus_out")``
  * ``RemoteViewer.inputMethodEvent`` with non-empty commit string emits
    ``text_commit(commit)``

Both signals are plumbed through ``client/protocol.py`` (Task 2) to wire
``KeyResetModifiersMsg`` / ``TextCommitMsg`` toward the server.

Lives alongside ``tests/client/test_viewer_qrhi_video_layer.py`` so it
shares the offscreen QApplication fixture pattern (no pytest-qt
dependency — Phase 1 D-02 boundary stays at "no new test deps unless
required").
"""
from __future__ import annotations

import os

import pytest

# Widget-level tests require PySide6 + a Qt platform plugin. Skip cleanly
# on hosts without PySide6 so the file collects on Linux CI without Qt.
pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("PySide6.QtGui")


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication.

    Forces ``QT_QPA_PLATFORM=offscreen`` so widget construction works on
    headless CI and inside the executor sandbox. Reuses any existing
    QApplication (Qt allows only one per process).
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    # Don't quit — leaving the app alive lets other tests in the module
    # share the instance; Python interpreter shutdown reclaims it.


def test_focus_out_emits_reset_modifiers_signal(qapp):
    """Losing window focus → reset_modifiers_requested('focus_out')."""
    from client.viewer import RemoteViewer
    v = RemoteViewer()
    received: list[str] = []
    v.reset_modifiers_requested.connect(received.append)

    # Drive the override directly — Qt's QFocusEvent + qWidgetWindow
    # plumbing is not exercised by the offscreen platform plugin so we
    # test the override deterministically rather than relying on
    # qApp.processEvents().
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent
    ev = QFocusEvent(QEvent.Type.FocusOut)
    v.focusOutEvent(ev)

    assert received == ["focus_out"], (
        f"expected ['focus_out'], got {received!r}"
    )


def test_input_method_event_emits_text_commit(qapp):
    """IME commit string → text_commit(text)."""
    from PySide6.QtGui import QInputMethodEvent
    from client.viewer import RemoteViewer
    v = RemoteViewer()
    received: list[str] = []
    v.text_commit.connect(received.append)

    # Construct an IME event with a non-empty commit string. Hiragana
    # 'a' (U+3042) is a canonical JP IME commit (D-15 covers JP layout).
    ev = QInputMethodEvent("", [])
    ev.setCommitString("\u3042")
    v.inputMethodEvent(ev)

    assert received == ["\u3042"], (
        f"expected ['\\u3042'], got {received!r}"
    )


def test_input_method_event_empty_commit_does_not_emit(qapp):
    """Pre-edit-only IME events (empty commit string) must NOT fire.

    Pre-edit composition is part of the dead-key compose flow; only the
    final commit goes on the wire as a TextCommit. Phase 2 D-15 anti-
    pattern: synthesizing keycodes mangles dead-key composition.
    """
    from PySide6.QtGui import QInputMethodEvent
    from client.viewer import RemoteViewer
    v = RemoteViewer()
    received: list[str] = []
    v.text_commit.connect(received.append)

    ev = QInputMethodEvent("preedit", [])
    # commit string left empty — the default
    v.inputMethodEvent(ev)

    assert received == [], (
        f"empty-commit IME event must not emit text_commit; got {received!r}"
    )
