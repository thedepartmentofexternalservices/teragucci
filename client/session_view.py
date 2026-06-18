"""SessionView — per-tab widget wrapping client.session.Session.

Per RESEARCH Open Q #4 (D-12): ``client/session.py`` is NOT split — this
module WRAPS an existing ``Session`` instance. MainWindow used to insert
``session.viewer`` directly as a tab page; SessionView is a compositional
seam that centralises per-tab widget ownership without moving any
business logic out of ``Session``.

This widget is deliberately thin. It:
  * Holds a reference to the wrapped Session
  * Exposes ``.viewer`` / ``.overlay`` / ``.display_name`` pass-throughs so
    MainWindow can continue reading them without knowing the Session is
    wrapped
  * Adds the Session's existing viewer as a child via QVBoxLayout, which
    lets MainWindow ``addTab(session_view, label)`` instead of
    ``addTab(session.viewer, label)``. The FSM + protocol wiring live on
    Session as before.

Do NOT add decoder/audio/health-overlay handling here. Those remain on
Session.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QVBoxLayout, QWidget

from client.session import Session

logger = logging.getLogger(__name__)


class SessionView(QWidget):
    """Per-tab QWidget that contains exactly one Session's viewer.

    Kept compositional: SessionView IS-A QWidget and HAS-A Session.
    """

    def __init__(self, session: Session, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._session = session

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Reparent Session's viewer widget into this container. The
        # Session object continues to own decoder/audio/overlay
        # lifecycles — this widget just provides the tab surface.
        layout.addWidget(session.viewer)

    # ── Public accessors ───────────────────────────────────────────

    @property
    def session(self) -> Session:
        return self._session

    @property
    def viewer(self):
        """The underlying RemoteViewer — exposed so MainWindow's
        legacy ``session_view.viewer`` / ``.overlay`` lookups work."""
        return self._session.viewer

    @property
    def overlay(self):
        return self._session.overlay

    @property
    def display_name(self) -> str:
        return self._session.display_name

    @property
    def health(self):
        return self._session.health

    @property
    def is_connected(self) -> bool:
        return self._session.is_connected
