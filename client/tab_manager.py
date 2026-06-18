"""TabManager — QTabWidget glue extracted from MainWindow (STAB-05 / D-12).

Owns the central QTabWidget that hosts one SessionView per remote
connection. Kept deliberately thin: the per-tab business logic (session
lookup, status routing) still lives in MainWindow because it needs
access to fields like _status_dot, _health_status, and the bookmarks
manager. TabManager's responsibility is strictly the widget + the
"+"-corner button + tab-close / current-changed signal forwarding.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtWidgets import QTabWidget, QToolButton

from client import icons, theme

logger = logging.getLogger(__name__)


class TabManager(QObject):
    """Owns the central QTabWidget and the "+"-corner new-connection button.

    Emits Qt Signals so the MainWindow can wire up status-bar updates,
    bookmark tracking, etc. without touching widget internals.
    """

    tab_close_requested = Signal(int)
    current_changed = Signal(int)
    new_connection_requested = Signal()

    def __init__(
        self,
        parent: Optional[QObject] = None,
        new_connection_callback: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.setDocumentMode(True)
        self._tabs.tabCloseRequested.connect(self.tab_close_requested.emit)
        self._tabs.currentChanged.connect(self.current_changed.emit)

        # "+" button on the tab bar — shares the icon-asset scheme MainWindow
        # already used.
        self._new_tab_btn = QToolButton()
        self._new_tab_btn.setIcon(icons.icon_connect(theme.ACCENT))
        self._new_tab_btn.setIconSize(QSize(16, 16))
        self._new_tab_btn.setFixedSize(28, 28)
        self._new_tab_btn.setToolTip("New Connection (Ctrl+N)")
        if new_connection_callback is not None:
            self._new_tab_btn.clicked.connect(new_connection_callback)
        else:
            self._new_tab_btn.clicked.connect(self.new_connection_requested.emit)
        self._tabs.setCornerWidget(self._new_tab_btn, Qt.TopRightCorner)

    @property
    def widget(self) -> QTabWidget:
        """The QTabWidget itself — set as MainWindow's central widget."""
        return self._tabs

    # Convenience pass-throughs so callers don't reach into .widget for
    # common operations — keeps the surface stable.

    def count(self) -> int:
        return self._tabs.count()

    def current_index(self) -> int:
        return self._tabs.currentIndex()

    def add_tab(self, page, label: str) -> int:
        return self._tabs.addTab(page, label)

    def remove_tab(self, index: int) -> None:
        self._tabs.removeTab(index)

    def set_current_index(self, index: int) -> None:
        self._tabs.setCurrentIndex(index)

    def set_tab_text(self, index: int, text: str) -> None:
        self._tabs.setTabText(index, text)

    def widget_at(self, index: int):
        return self._tabs.widget(index)

    def tab_bar(self):
        return self._tabs.tabBar()
