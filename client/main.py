"""Compatibility shim — the real bootstrap lives in ``client/app.py``.

This module exists so the pyproject console script
``teraguchi-client = "client.main:main"`` keeps working after D-12
(Plan 12) split the 1,189-line monolith into
``client.app`` / ``client.main_window`` / ``client.tab_manager`` /
``client.session_view`` / ``client.connection_supervisor``.

Existing imports of ``client.main.MainWindow`` / ``ConnectionDialog`` /
``BrokerMachinePicker`` / ``BookmarkPanel`` / ``USBDevicePanel`` continue
to resolve via this shim — the names are re-exported from
``client.main_window``.
"""
from __future__ import annotations

from client.app import main
from client.main_window import (
    MainWindow,
    ConnectionDialog,
    BrokerMachinePicker,
    BookmarkPanel,
    BookmarkDelegate,
    USBDevicePanel,
)
from client.tab_manager import TabManager
from client.session_view import SessionView


__all__ = [
    "main",
    "MainWindow",
    "ConnectionDialog",
    "BrokerMachinePicker",
    "BookmarkPanel",
    "BookmarkDelegate",
    "USBDevicePanel",
    "TabManager",
    "SessionView",
]


if __name__ == "__main__":
    main()
