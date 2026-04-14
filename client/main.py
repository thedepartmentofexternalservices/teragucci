#!/usr/bin/env python3
"""
Teraguchi Client — Modern Remote Desktop Client

Features:
- Multiple concurrent connections via tabs
- True fullscreen with auto-hiding toolbar
- Dark theme UI
- H.264/H.265/AV1 + Wacom pen + audio + clipboard
"""

import sys
import logging
import argparse
from dataclasses import asdict

from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QPoint, QSize
from PySide6.QtGui import QAction, QKeySequence, QColor, QIcon, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QStatusBar, QToolBar,
    QDialog, QFormLayout, QSpinBox, QMessageBox, QSizePolicy,
    QDockWidget, QListWidget, QListWidgetItem, QCheckBox,
    QComboBox, QMenu, QFileDialog, QTabWidget, QTabBar,
    QStyledItemDelegate, QStyle, QToolButton,
)

sys.path.insert(0, ".")
from client import theme
from client import icons
from client.session import Session
from client.bookmarks import BookmarkManager
from client.health_display import HealthStatusWidget, HealthData
from client.quality_control import QualityControlPanel
from client.fullscreen_toolbar import FullscreenToolbar, REVEAL_ZONE
from common.messages import QualitySettings

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════
# Connection Dialog
# ════════════════════════════════════════════════════

class ConnectionDialog(QDialog):
    def __init__(self, parent=None, default_host="", default_port=443,
                 default_username="", default_password=""):
        super().__init__(parent)
        self.setWindowTitle("New Connection")
        self.setMinimumWidth(440)
        self.setMaximumWidth(520)

        layout = QFormLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setLabelAlignment(Qt.AlignRight)

        # Connection type selector
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Direct — connect to a specific machine", "direct")
        self.mode_combo.addItem("Broker — auto-assign via connection broker", "broker")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        layout.addRow("Mode:", self.mode_combo)

        self.host_input = QLineEdit(default_host)
        self.host_input.setPlaceholderText("e.g. 192.168.1.100 or hostname")
        self._host_label = QLabel("Host:")
        layout.addRow(self._host_label, self.host_input)

        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(default_port)
        self._port_label = QLabel("Port:")
        layout.addRow(self._port_label, self.port_input)

        self.username_input = QLineEdit(default_username)
        self.username_input.setPlaceholderText("(leave empty if no auth)")
        layout.addRow("Username:", self.username_input)

        self.password_input = QLineEdit(default_password)
        self.password_input.setEchoMode(QLineEdit.Password)
        layout.addRow("Password:", self.password_input)

        self.tls_check = QCheckBox("Encrypt connection")
        self.tls_check.setChecked(True)
        layout.addRow(self.tls_check)

        self.auto_reconnect_check = QCheckBox("Auto-reconnect on disconnect")
        self.auto_reconnect_check.setChecked(True)
        layout.addRow(self.auto_reconnect_check)

        self.save_bookmark_check = QCheckBox("Save as bookmark")
        layout.addRow(self.save_bookmark_check)

        self.bookmark_name_input = QLineEdit()
        self.bookmark_name_input.setPlaceholderText("Bookmark name")
        self.bookmark_name_input.setEnabled(False)
        self.save_bookmark_check.toggled.connect(self.bookmark_name_input.setEnabled)
        layout.addRow("Name:", self.bookmark_name_input)

        # Buttons
        btn_layout = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("connectBtn")
        self.connect_btn.setDefault(True)
        self.cancel_btn = QPushButton("Cancel")
        btn_layout.addStretch()
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.connect_btn)
        layout.addRow(btn_layout)

        self.connect_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

    def _on_mode_changed(self, index):
        is_broker = (index == 1)
        if is_broker:
            self._host_label.setText("Broker:")
            self.host_input.setPlaceholderText("e.g. dxs-broker or 10.10.0.173")
            self.port_input.setValue(8443)
            self.username_input.setPlaceholderText("FreeIPA username")
        else:
            self._host_label.setText("Host:")
            self.host_input.setPlaceholderText("e.g. 192.168.1.100 or hostname")
            self.port_input.setValue(443)
            self.username_input.setPlaceholderText("(leave empty if no auth)")

    @property
    def connection_mode(self):
        # Use currentIndex — more reliable than currentData across PySide6 versions
        return "broker" if self.mode_combo.currentIndex() == 1 else "direct"
    @property
    def host(self): return self.host_input.text().strip()
    @property
    def port(self): return self.port_input.value()
    @property
    def username(self): return self.username_input.text().strip()
    @property
    def password(self): return self.password_input.text()
    @property
    def use_tls(self): return self.tls_check.isChecked()
    @property
    def auto_reconnect(self): return self.auto_reconnect_check.isChecked()
    @property
    def save_bookmark(self): return self.save_bookmark_check.isChecked()
    @property
    def bookmark_name(self): return self.bookmark_name_input.text().strip()


# ════════════════════════════════════════════════════
# Broker Machine Picker
# ════════════════════════════════════════════════════

class BrokerMachinePicker(QDialog):
    """Dialog shown after broker auth, letting the user pick a machine."""

    def __init__(self, parent, machines: list):
        super().__init__(parent)
        self.setWindowTitle("Select a Flame")
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)
        self._machines = machines
        self._selected = ""  # "" = auto-assign

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header = QLabel("Pick a Flame workstation")
        header_font = QFont()
        header_font.setPointSize(13)
        header_font.setWeight(QFont.DemiBold)
        header.setFont(header_font)
        layout.addWidget(header)

        hint = QLabel("The broker will connect you to the machine you choose. "
                      "Use <b>Auto-assign</b> to let the broker pick for you.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        layout.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.itemDoubleClicked.connect(self._accept_selection)
        layout.addWidget(self.list_widget, 1)

        # Auto-assign row (empty name)
        auto_item = QListWidgetItem("Auto-assign  —  let the broker pick the best machine")
        auto_item.setData(Qt.UserRole, "")
        self.list_widget.addItem(auto_item)

        def _fmt_machine(m: dict) -> str:
            name = m.get("name", "?")
            gpu = m.get("gpu", "")
            healthy = m.get("healthy", False)
            sessions = m.get("active_sessions", []) or []
            tags = m.get("tags", []) or []
            parts = [name]
            if gpu:
                parts.append(f"— {gpu}")
            status_bits = []
            if not healthy:
                status_bits.append("offline")
            if sessions:
                status_bits.append(f"{len(sessions)} active session{'s' if len(sessions) != 1 else ''}")
            if tags:
                status_bits.append(", ".join(tags))
            if status_bits:
                parts.append(f"  [{' · '.join(status_bits)}]")
            return "  ".join(parts)

        # Sort: healthy first, then by priority, then by name
        sorted_machines = sorted(
            machines,
            key=lambda m: (not m.get("healthy", False),
                           m.get("priority", 10),
                           m.get("name", "")))

        for m in sorted_machines:
            item = QListWidgetItem(_fmt_machine(m))
            item.setData(Qt.UserRole, m.get("name", ""))
            if not m.get("healthy", False):
                item.setForeground(QColor(theme.TEXT_MUTED))
            self.list_widget.addItem(item)

        self.list_widget.setCurrentRow(0)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        connect_btn = QPushButton("Connect")
        connect_btn.setDefault(True)
        connect_btn.clicked.connect(self._accept_selection)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(connect_btn)
        layout.addLayout(btn_row)

    def _accept_selection(self, *args):
        item = self.list_widget.currentItem()
        if item is None:
            self._selected = ""
        else:
            self._selected = item.data(Qt.UserRole) or ""
        self.accept()

    @property
    def selected_machine(self) -> str:
        return self._selected


# ════════════════════════════════════════════════════
# Bookmark Panel
# ════════════════════════════════════════════════════

class BookmarkDelegate(QStyledItemDelegate):
    """Custom delegate for bookmark items — card-style with server icon."""

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 56)

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect.adjusted(4, 2, -4, -2)

        # Selection / hover background
        if option.state & QStyle.State_Selected:
            painter.setBrush(QColor(0, 200, 120, 30))
            painter.setPen(QColor(0, 200, 120, 80))
            painter.drawRoundedRect(rect, 6, 6)
        elif option.state & QStyle.State_MouseOver:
            painter.setBrush(QColor(255, 255, 255, 8))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 6, 6)
        else:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(Qt.NoPen)

        # Server icon
        icon = icons.icon_server(theme.TEXT_SECONDARY)
        icon_rect = rect.adjusted(8, 10, 0, 0)
        icon.paint(painter, icon_rect.x(), icon_rect.y(), 20, 20)

        # Name (bold)
        name = index.data(Qt.UserRole + 1) or "Unnamed"
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        name_font = QFont()
        name_font.setWeight(QFont.DemiBold)
        name_font.setPointSize(12)
        painter.setFont(name_font)
        painter.drawText(rect.adjusted(36, 6, -8, -22), Qt.AlignLeft | Qt.AlignVCenter, name)

        # Host:port (secondary)
        host_text = index.data(Qt.UserRole + 2) or ""
        painter.setPen(QColor(theme.TEXT_SECONDARY))
        sub_font = QFont()
        sub_font.setPointSize(10)
        painter.setFont(sub_font)
        painter.drawText(rect.adjusted(36, 24, -8, -2), Qt.AlignLeft | Qt.AlignVCenter, host_text)

        painter.restore()


class BookmarkPanel(QWidget):
    connect_requested = Signal(str)

    def __init__(self, bookmark_mgr: BookmarkManager, parent=None):
        super().__init__(parent)
        self._mgr = bookmark_mgr
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Search bar with icon
        search_row = QHBoxLayout()
        search_row.setSpacing(0)
        self._search = QLineEdit()
        self._search.setPlaceholderText("  Search bookmarks...")
        self._search.textChanged.connect(self._refresh)
        search_row.addWidget(self._search)
        layout.addLayout(search_row)

        self._list = QListWidget()
        self._list.setItemDelegate(BookmarkDelegate(self._list))
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.doubleClicked.connect(self._on_double_click)
        self._list.setMouseTracking(True)
        self._list.setSpacing(1)
        self._list.setStyleSheet(
            f"QListWidget {{ background: {theme.BG_SECONDARY}; border: none; }}"
            f"QListWidget::item {{ border: none; }}"
            f"QListWidget::item:hover {{ background: transparent; }}"
            f"QListWidget::item:selected {{ background: transparent; }}")
        layout.addWidget(self._list)

        # Icon buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        add_btn = QToolButton()
        add_btn.setIcon(icons.icon_connect(theme.ACCENT))
        add_btn.setToolTip("Add Bookmark")
        add_btn.setIconSize(QSize(18, 18))
        add_btn.clicked.connect(self._add_bookmark)
        btn_row.addWidget(add_btn)

        import_btn = QToolButton()
        import_btn.setIcon(icons.icon_import(theme.TEXT_SECONDARY))
        import_btn.setToolTip("Import Bookmarks")
        import_btn.setIconSize(QSize(18, 18))
        import_btn.clicked.connect(self._import_bookmarks)
        btn_row.addWidget(import_btn)

        export_btn = QToolButton()
        export_btn.setIcon(icons.icon_export(theme.TEXT_SECONDARY))
        export_btn.setToolTip("Export Bookmarks")
        export_btn.setIconSize(QSize(18, 18))
        export_btn.clicked.connect(self._export_bookmarks)
        btn_row.addWidget(export_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._refresh()

    def _refresh(self, _query: str = ""):
        self._list.clear()
        query = self._search.text().strip()
        items = self._mgr.search(query) if query else self._mgr.list_all()
        for bid, profile in items:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, bid)
            item.setData(Qt.UserRole + 1, profile.name)
            host_text = f"{profile.host}:{profile.port}"
            if profile.last_connected:
                host_text += f"  ·  {profile.last_connected}"
            item.setData(Qt.UserRole + 2, host_text)
            item.setSizeHint(QSize(0, 56))
            self._list.addItem(item)

    def _on_double_click(self, _index):
        item = self._list.currentItem()
        if item:
            self.connect_requested.emit(item.data(Qt.UserRole))

    def _show_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if not item:
            return
        bid = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction(icons.icon_connect(), "Connect",
                       lambda: self.connect_requested.emit(bid))
        menu.addAction(icons.icon_edit(), "Edit",
                       lambda: self._edit_bookmark(bid))
        menu.addSeparator()
        menu.addAction(icons.icon_trash(), "Delete",
                       lambda: self._delete_bookmark(bid))
        menu.exec(self._list.mapToGlobal(pos))

    def _add_bookmark(self):
        dialog = ConnectionDialog(self)
        dialog.setWindowTitle("Add Bookmark")
        dialog.save_bookmark_check.setChecked(True)
        dialog.save_bookmark_check.setEnabled(False)
        if dialog.exec() == QDialog.Accepted:
            name = dialog.bookmark_name or f"{dialog.host}:{dialog.port}"
            self._mgr.add(name=name, host=dialog.host, port=dialog.port,
                          username=dialog.username, password=dialog.password,
                          use_tls=dialog.use_tls)
            self._refresh()

    def _edit_bookmark(self, bid):
        profile = self._mgr.get(bid)
        if not profile:
            return
        dialog = ConnectionDialog(self)
        dialog.setWindowTitle(f"Edit: {profile.name}")
        dialog.save_bookmark_check.setChecked(True)
        dialog.save_bookmark_check.setEnabled(False)
        dialog.bookmark_name_input.setText(profile.name)
        dialog.host_input.setText(profile.host)
        dialog.port_input.setValue(profile.port)
        dialog.username_input.setText(profile.username)
        dialog.password_input.setText(self._mgr.get_password(bid))
        dialog.tls_check.setChecked(profile.use_tls)
        if dialog.exec() == QDialog.Accepted:
            self._mgr.update(bid, name=dialog.bookmark_name or profile.name,
                             host=dialog.host, port=dialog.port,
                             username=dialog.username, password=dialog.password,
                             use_tls=dialog.use_tls)
            self._refresh()

    def _delete_bookmark(self, bid):
        profile = self._mgr.get(bid)
        if not profile:
            return
        if QMessageBox.question(self, "Delete", f"Delete '{profile.name}'?") == QMessageBox.Yes:
            self._mgr.remove(bid)
            self._refresh()

    def _import_bookmarks(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import", "", "JSON (*.json)")
        if path:
            self._mgr.import_bookmarks(path)
            self._refresh()

    def _export_bookmarks(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export", "bookmarks.json", "JSON (*.json)")
        if path:
            self._mgr.export_bookmarks(path, include_passwords=False)


# ════════════════════════════════════════════════════
# USB Devices Panel
# ════════════════════════════════════════════════════

class USBDevicePanel(QWidget):
    """Shows local USB devices with forward/detach controls."""
    attach_requested = Signal(str)   # bus_id
    detach_requested = Signal(str)   # bus_id
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Device list
        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{
                background: {theme.BG_SECONDARY};
                border: 1px solid {theme.BORDER};
                border-radius: 6px;
            }}
            QListWidget::item {{
                padding: 6px 8px;
                border-bottom: 1px solid {theme.BORDER};
            }}
            QListWidget::item:selected {{
                background: {theme.ACCENT}40;
            }}
        """)
        layout.addWidget(self._list)

        # Buttons
        btn_row = QHBoxLayout()
        self._btn_forward = QPushButton("Forward")
        self._btn_forward.setIcon(icons.icon_upload())
        self._btn_forward.clicked.connect(self._on_forward)
        self._btn_detach = QPushButton("Detach")
        self._btn_detach.setIcon(icons.icon_disconnect())
        self._btn_detach.clicked.connect(self._on_detach)
        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.setIcon(icons.icon_refresh())
        self._btn_refresh.clicked.connect(self.refresh_requested.emit)
        btn_row.addWidget(self._btn_forward)
        btn_row.addWidget(self._btn_detach)
        btn_row.addWidget(self._btn_refresh)
        layout.addWidget(QLabel(
            f"<span style='color:{theme.TEXT_MUTED}; font-size:11px;'>"
            "Wacom tablets &amp; keyboards for direct passthrough</span>"))
        layout.addLayout(btn_row)

        self._devices = []  # list of device dicts
        self._attached = []  # list of attached bus_ids

    def update_devices(self, devices: list, attached: list = None):
        """Update the device list display."""
        self._devices = devices
        self._attached = attached or []
        self._list.clear()
        for dev in devices:
            bus_id = dev.get("bus_id", "")
            name = dev.get("product", "") or f"{dev.get('vendor_id', '')}:{dev.get('product_id', '')}"
            mfr = dev.get("manufacturer", "")
            if mfr:
                name = f"{mfr} {name}"
            status = " [FORWARDED]" if bus_id in self._attached else ""
            item = QListWidgetItem(f"{name}{status}")
            item.setData(Qt.UserRole, bus_id)
            if bus_id in self._attached:
                item.setForeground(QColor(theme.ACCENT))
            self._list.addItem(item)

    def _selected_bus_id(self) -> str:
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else ""

    def _on_forward(self):
        bus_id = self._selected_bus_id()
        if bus_id:
            self.attach_requested.emit(bus_id)

    def _on_detach(self):
        bus_id = self._selected_bus_id()
        if bus_id:
            self.detach_requested.emit(bus_id)


# ════════════════════════════════════════════════════
# Main Window
# ════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    """Tab-based main window with multiple concurrent sessions."""

    def __init__(self, initial_host="", initial_port=443,
                 initial_user="", initial_pass="",
                 initial_mode="direct"):
        super().__init__()
        self.setWindowTitle("Teraguchi")
        self.setMinimumSize(900, 600)

        self._bookmarks = BookmarkManager()
        self._sessions: dict[int, Session] = {}  # tab_index -> Session
        self._fullscreen_state = None  # saved UI state for fullscreen restore

        # ── Tabs (central widget) ──
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.setDocumentMode(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self._tabs)

        # "+" button on tab bar
        self._tabs.setCornerWidget(self._make_new_tab_btn(), Qt.TopRightCorner)

        # ── Docks ──
        self._bookmark_dock = QDockWidget("  Bookmarks", self)
        self._bookmark_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self._bookmark_dock.setFeatures(
            QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self._bookmark_panel = BookmarkPanel(self._bookmarks)
        self._bookmark_panel.connect_requested.connect(self._connect_bookmark)
        self._bookmark_dock.setWidget(self._bookmark_panel)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._bookmark_dock)

        self._quality_dock = QDockWidget("  Quality", self)
        self._quality_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self._quality_dock.setFeatures(
            QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self._quality_panel = QualityControlPanel()
        self._quality_panel.settings_changed.connect(self._on_quality_changed)
        self._quality_panel.mic_mute_toggled.connect(self._toggle_mic)
        self._quality_dock.setWidget(self._quality_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, self._quality_dock)
        self._quality_dock.hide()

        self._usb_dock = QDockWidget("  USB Devices", self)
        self._usb_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self._usb_dock.setFeatures(
            QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self._usb_panel = USBDevicePanel()
        self._usb_panel.attach_requested.connect(self._usb_attach)
        self._usb_panel.detach_requested.connect(self._usb_detach)
        self._usb_panel.refresh_requested.connect(self._usb_refresh)
        self._usb_dock.setWidget(self._usb_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, self._usb_dock)
        self._usb_dock.hide()

        # ── Toolbar ──
        self._setup_toolbar()

        # ── Status Bar ──
        self._health_status = HealthStatusWidget()
        self._status_dot = QLabel()
        self._status_dot.setFixedSize(8, 8)
        self._status_dot.setStyleSheet(
            f"border-radius: 4px; background: {theme.TEXT_MUTED};")
        self._status_label = QLabel("No connections")
        self.statusBar().addWidget(self._status_dot)
        self.statusBar().addWidget(self._status_label)
        self.statusBar().addPermanentWidget(self._health_status)

        # ── Fullscreen toolbar ──
        self._fs_toolbar = FullscreenToolbar()
        self._fs_toolbar.exit_fullscreen.connect(self._toggle_fullscreen)
        self._fs_toolbar.disconnect_requested.connect(self._disconnect_active)
        self._fs_toolbar.settings_requested.connect(
            lambda: self._quality_dock.setVisible(not self._quality_dock.isVisible()))
        self._fs_toolbar.monitor_selector.selection_changed.connect(self._on_monitors_changed)
        self._fs_toolbar.mic_toggle_requested.connect(self._toggle_mic)
        self._fs_toolbar.hide()

        # ── Timers ──
        self._health_timer = QTimer()
        self._health_timer.timeout.connect(self._update_health)
        self._health_timer.start(500)

        # ── Fullscreen mouse tracking ──
        self.setMouseTracking(True)
        self.centralWidget().setMouseTracking(True)

        # Auto-connect if host given
        if initial_host:
            self._new_session_and_connect(
                initial_host, initial_port, initial_user, initial_pass,
                mode=initial_mode)

    def _make_new_tab_btn(self):
        btn = QToolButton()
        btn.setIcon(icons.icon_connect(theme.ACCENT))
        btn.setIconSize(QSize(16, 16))
        btn.setFixedSize(28, 28)
        btn.setToolTip("New Connection (Ctrl+N)")
        btn.clicked.connect(self._show_connect_dialog)
        return btn

    # ── Toolbar & Menus ─────────────────────────

    def _setup_toolbar(self):
        # ── Menu Bar ──
        mb = self.menuBar()

        # File menu
        file_menu = mb.addMenu("&File")
        file_menu.addAction(self._action(
            "New Connection", "Ctrl+N", self._show_connect_dialog, icons.icon_connect()))
        file_menu.addAction(self._action(
            "Send File...", "Ctrl+Shift+S", self._send_file_dialog, icons.icon_upload()))
        file_menu.addSeparator()
        file_menu.addAction(self._action(
            "Import Bookmarks...", "", lambda: self._bookmark_panel._import_bookmarks(),
            icons.icon_import()))
        file_menu.addAction(self._action(
            "Export Bookmarks...", "", lambda: self._bookmark_panel._export_bookmarks(),
            icons.icon_export()))
        file_menu.addSeparator()
        file_menu.addAction(self._action("Quit", "Ctrl+Q", self.close))

        # Connection menu
        conn_menu = mb.addMenu("&Connection")
        conn_menu.addAction(self._action(
            "Disconnect", "Ctrl+D", self._disconnect_active, icons.icon_disconnect()))
        conn_menu.addAction(self._action(
            "Refresh Frame", "F5",
            lambda: self._active_session and self._active_session.request_full_frame(),
            icons.icon_refresh()))
        conn_menu.addSeparator()
        conn_menu.addAction(self._action(
            "Mute/Unmute Mic", "M", self._toggle_mic, icons.icon_mic()))

        # View menu
        view_menu = mb.addMenu("&View")
        bm_action = self._bookmark_dock.toggleViewAction()
        bm_action.setIcon(icons.icon_bookmark())
        view_menu.addAction(bm_action)
        qc_action = self._quality_dock.toggleViewAction()
        qc_action.setIcon(icons.icon_settings())
        view_menu.addAction(qc_action)
        usb_action = self._usb_dock.toggleViewAction()
        usb_action.setIcon(icons.icon_usb())
        view_menu.addAction(usb_action)
        view_menu.addSeparator()
        view_menu.addAction(self._action(
            "Fullscreen", "F11", self._toggle_fullscreen, icons.icon_fullscreen()))
        view_menu.addAction(self._action(
            "Health Overlay", "F9", self._toggle_health, icons.icon_health()))
        view_menu.addSeparator()
        view_menu.addAction(self._action(
            "Key Diagnostic", "F10", self._show_key_diagnostic, icons.icon_keyboard()))

        # Help menu
        help_menu = mb.addMenu("&Help")
        help_menu.addAction(self._action(
            "About Teraguchi", "", self._show_about))

        # ── Toolbar ──
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.addToolBar(tb)

        tb.addAction(self._action(
            "New Connection", "Ctrl+N", self._show_connect_dialog, icons.icon_connect(theme.ACCENT)))
        tb.addAction(self._action(
            "Disconnect", "Ctrl+D", self._disconnect_active, icons.icon_disconnect()))
        tb.addSeparator()
        tb.addAction(self._action(
            "Refresh", "F5",
            lambda: self._active_session and self._active_session.request_full_frame(),
            icons.icon_refresh()))
        tb.addAction(self._action(
            "Fullscreen", "F11", self._toggle_fullscreen, icons.icon_fullscreen()))
        tb.addAction(self._action(
            "Health", "F9", self._toggle_health, icons.icon_health()))
        tb.addAction(self._action(
            "Key Diagnostic", "F10", self._show_key_diagnostic, icons.icon_keyboard()))
        tb.addSeparator()

        from client.monitor_selector import MonitorSelector
        self._monitor_selector = MonitorSelector()
        self._monitor_selector.selection_changed.connect(self._on_monitors_changed)
        tb.addWidget(self._monitor_selector)

        tb.addSeparator()

        # Mic toggle — icon changes based on state
        self._mic_action = self._action(
            "Mute Mic", "M", self._toggle_mic, icons.icon_mic())
        self._mic_action.setCheckable(True)
        self._mic_action.setToolTip("Microphone: active (click to mute)")
        tb.addAction(self._mic_action)

        tb.addSeparator()
        tb.addAction(self._action(
            "Settings", "", lambda: self._quality_dock.setVisible(
                not self._quality_dock.isVisible()), icons.icon_settings()))
        tb.addAction(self._action(
            "USB Devices", "", lambda: self._usb_dock.setVisible(
                not self._usb_dock.isVisible()), icons.icon_usb()))

    def _action(self, text, shortcut, slot, icon=None):
        a = QAction(text, self)
        if icon:
            a.setIcon(icon)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(slot)
        return a

    def _show_about(self):
        QMessageBox.about(
            self, "About Teraguchi",
            "<h3>Teraguchi</h3>"
            "<p>Remote desktop for Flame workstations.</p>"
            "<p>GPU-accelerated H.264/H.265 streaming with "
            "full keyboard, mouse, and Wacom pen support.</p>"
            f"<p style='color:{theme.TEXT_MUTED}'>© 2025 DXS / 1986 Studios</p>")

    # ── Properties ───────────────────────────────

    @property
    def _active_session(self) -> Session | None:
        idx = self._tabs.currentIndex()
        return self._sessions.get(idx)

    # ── Session / Tab Management ─────────────────

    def _new_session_and_connect(self, host, port, username, password,
                                  use_tls=True, auto_reconnect=True,
                                  bookmark_id="", mode="direct"):
        session = Session(self)
        idx = self._tabs.addTab(session.viewer, session.display_name)
        self._sessions[idx] = session
        self._tabs.setCurrentIndex(idx)

        # Wire session signals
        session.status_changed.connect(lambda s, i=idx: self._on_session_status(i, s))
        session.title_changed.connect(lambda t, i=idx: self._tabs.setTabText(i, t))
        session.auth_failed.connect(lambda msg: QMessageBox.warning(self, "Auth Failed", msg))
        session.monitor_list_received.connect(self._on_monitor_list)
        session.file_transfer_finished.connect(self._on_file_transfer_done)
        session.usb_devices_updated.connect(self._on_usb_devices_updated)
        session.broker_machine_needed.connect(
            lambda machines, s=session: self._on_broker_machine_needed(s, machines))
        session.mic_state_changed.connect(self._on_mic_state_changed)

        if mode == "broker":
            logger.info("Connecting via broker to %s:%d", host, port)
            session.connect_broker(host, port, username, password,
                                   use_tls=use_tls, auto_reconnect=auto_reconnect)
        else:
            session.connect(host, port, username, password,
                            use_tls=use_tls, auto_reconnect=auto_reconnect,
                            bookmark_id=bookmark_id)

        # Send initial quality
        session.apply_quality(self._quality_panel.settings)

        label = "broker" if mode == "broker" else host
        self._status_label.setText(f"Connecting to {label}:{port}...")

    def _close_tab(self, idx):
        session = self._sessions.pop(idx, None)
        if session:
            session.cleanup()
        self._tabs.removeTab(idx)

        # Re-key sessions after removal
        new_sessions = {}
        for i in range(self._tabs.count()):
            # Find the session whose viewer matches this tab
            viewer = self._tabs.widget(i)
            for old_idx, s in list(self._sessions.items()):
                if s.viewer is viewer:
                    new_sessions[i] = s
                    del self._sessions[old_idx]
                    break
        self._sessions.update(new_sessions)

        if self._tabs.count() == 0:
            self._status_label.setText("No connections")
            self._health_status.data = HealthData()

    def _on_tab_changed(self, idx):
        session = self._sessions.get(idx)
        if session:
            self._health_status.data = session.health
            self._status_label.setText(session.display_name)
            if self.isFullScreen():
                self._fs_toolbar.set_connection_label(session.display_name)
            # Sync mic UI to newly selected session
            self._on_mic_state_changed(
                session.mic_available and not session.mic_muted)

    def _on_session_status(self, idx, status):
        session = self._sessions.get(idx)
        if not session:
            return

        # Update tab icon/color hint
        colors = {"connected": theme.SUCCESS, "connecting": theme.WARNING,
                  "disconnected": theme.TEXT_MUTED, "error": theme.DANGER}
        color = colors.get(status, theme.TEXT_MUTED)

        if idx == self._tabs.currentIndex():
            self._status_dot.setStyleSheet(
                f"border-radius: 4px; background: {color};")
            if status == "connected":
                self._status_label.setText(f"Connected: {session.display_name}")
                if session._bookmark_id:
                    self._bookmarks.mark_connected(session._bookmark_id)
            elif status == "disconnected":
                self._status_label.setText(f"Disconnected: {session.display_name}")
            elif status == "connecting":
                self._status_label.setText(f"Connecting: {session.display_name}")

    # ── Actions ──────────────────────────────────

    def _show_connect_dialog(self):
        dialog = ConnectionDialog(self)
        if dialog.exec() == QDialog.Accepted:
            bid = ""
            mode = dialog.connection_mode
            if dialog.save_bookmark:
                name = dialog.bookmark_name or f"{dialog.host}:{dialog.port}"
                bid = self._bookmarks.add(
                    name=name, host=dialog.host, port=dialog.port,
                    username=dialog.username, password=dialog.password,
                    use_tls=dialog.use_tls, mode=mode)
                self._bookmark_panel._refresh()

            self._new_session_and_connect(
                dialog.host, dialog.port, dialog.username, dialog.password,
                use_tls=dialog.use_tls, auto_reconnect=dialog.auto_reconnect,
                bookmark_id=bid, mode=mode)

    def _connect_bookmark(self, bookmark_id):
        profile = self._bookmarks.get(bookmark_id)
        if not profile:
            return
        password = self._bookmarks.get_password(bookmark_id)
        mode = getattr(profile, "mode", "direct") or "direct"

        # If no saved password (or user didn't opt to save), re-open the
        # connection dialog pre-filled so they can type credentials.
        if not password:
            dialog = ConnectionDialog(
                self, default_host=profile.host,
                default_port=profile.port,
                default_username=profile.username,
                default_password="")
            dialog.mode_combo.setCurrentIndex(1 if mode == "broker" else 0)
            if dialog.exec() != QDialog.Accepted:
                return
            password = dialog.password
            mode = dialog.connection_mode

        self._quality_panel.load_from_profile(profile)
        self._new_session_and_connect(
            profile.host, profile.port, profile.username, password,
            use_tls=profile.use_tls, auto_reconnect=profile.auto_connect,
            bookmark_id=bookmark_id, mode=mode)

    def _active_bookmark_ids(self) -> set:
        """Return the set of bookmark IDs that currently have a connected session."""
        return {
            s._bookmark_id
            for s in self._sessions.values()
            if s._bookmark_id and s.is_connected
        }

    def _disconnect_by_bookmark_id(self, bookmark_id: str):
        """Disconnect the session associated with a specific bookmark ID."""
        for idx, session in list(self._sessions.items()):
            if session._bookmark_id == bookmark_id:
                if self.isFullScreen():
                    self._toggle_fullscreen()
                self._close_tab(idx)
                return

    def _toggle_mic(self):
        """Mute or unmute the microphone on the active session."""
        s = self._active_session
        if s:
            s.toggle_mic()

    def _on_mic_state_changed(self, active: bool):
        """Update all mic UI when the session mic state changes."""
        s = self._active_session
        available = s.mic_available if s else False
        device = s.mic_device_name if s else ""
        # Main toolbar action
        self._mic_action.setChecked(not active)
        if active:
            self._mic_action.setIcon(icons.icon_mic(theme.ACCENT))
            self._mic_action.setToolTip(
                f"Mic: {device} (click to mute)" if device else "Mic: active (click to mute)")
        else:
            self._mic_action.setIcon(icons.icon_mic_muted())
            self._mic_action.setToolTip("Mic: muted (click to unmute)")
        # Status bar
        mic_text = f"Mic: {device}" if active and device else ("Mic: muted" if not active else "Mic")
        self.statusBar().showMessage(mic_text, 3000)
        # Fullscreen toolbar
        if hasattr(self, "_fs_toolbar"):
            self._fs_toolbar.update_mic_state(active, device)
        # Quality panel
        self._quality_panel.update_mic_status(available, not active, device)

    def _disconnect_active(self):
        """Disconnect the active session and close its tab.

        session.disconnect() by itself only tears down the protocol layer —
        it leaves the tab open with a frozen last frame and a still-running
        decoder/audio pipeline, which looks to the user like nothing
        happened. Closing the tab runs the full cleanup path.
        """
        idx = self._tabs.currentIndex()
        if idx < 0 or idx not in self._sessions:
            return
        # If we're in fullscreen, drop back to windowed first so the user
        # isn't stranded on a blank fullscreen viewer after the tab closes.
        if self.isFullScreen():
            self._toggle_fullscreen()
        self._close_tab(idx)

    def _send_file_dialog(self):
        s = self._active_session
        if not s or not s.is_connected:
            self.statusBar().showMessage("Not connected", 3000)
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Send Files to Remote")
        if paths:
            ids = s.send_files(paths)
            count = len([i for i in ids if i])
            self.statusBar().showMessage(f"Sending {count} file(s)...", 5000)

    def _on_file_transfer_done(self, transfer_id: str, success: bool, message: str):
        if success:
            self.statusBar().showMessage(f"File sent: {message}", 5000)
        else:
            self.statusBar().showMessage(f"File transfer failed: {message}", 5000)

    def _on_broker_machine_needed(self, session, machines: list):
        """Broker has authenticated and is waiting for the user to pick a machine."""
        logger.info("Broker needs machine selection — %d available", len(machines))
        dialog = BrokerMachinePicker(self, machines)
        if dialog.exec() == QDialog.Accepted:
            choice = dialog.selected_machine
            logger.info("User selected broker machine: %r", choice or "(auto)")
            session.select_broker_machine(choice)
        else:
            # User cancelled — tear down the session. The async handshake's
            # future wait will be cancelled when the loop stops.
            logger.info("User cancelled broker machine selection")
            session.disconnect()

    def _usb_attach(self, bus_id: str):
        s = self._active_session
        if s and s.is_connected:
            s.usb_attach(bus_id)
            self.statusBar().showMessage(f"Forwarding USB device {bus_id}...", 3000)

    def _usb_detach(self, bus_id: str):
        s = self._active_session
        if s and s.is_connected:
            s.usb_detach(bus_id)
            self.statusBar().showMessage(f"Detaching USB device {bus_id}...", 3000)

    def _usb_refresh(self):
        s = self._active_session
        if s and s.is_connected:
            s.usb_refresh()

    def _on_usb_devices_updated(self, msg: dict):
        devices = msg.get("devices", [])
        attached = msg.get("attached", [])
        self._usb_panel.update_devices(devices, attached)

    def _on_quality_changed(self, settings):
        s = self._active_session
        if s:
            s.apply_quality(settings)

    def _on_monitors_changed(self, selected_regions: list):
        """Handle monitor checkbox changes from the selector.

        selected_regions: list of monitor dicts to show (empty = all).
        When a subset is selected, switch server to all-monitors capture
        and crop client-side.
        """
        s = self._active_session
        if not s:
            return

        if selected_regions:
            # Subset selected — server must capture everything, client crops
            s.select_monitor(0)  # 0 = all monitors / virtual desktop
            s.viewer.set_monitor_regions(selected_regions)
        else:
            # All monitors — show full virtual desktop, no crop
            s.select_monitor(0)
            s.viewer.set_monitor_regions([])

    def _on_monitor_list(self, monitors):
        self._monitor_selector.update_monitors(monitors)
        if self.isFullScreen():
            self._fs_toolbar.update_monitors(monitors)

    def _toggle_health(self):
        s = self._active_session
        if s:
            s.overlay.toggle()

    def _show_key_diagnostic(self):
        from client.key_diagnostic import KeyDiagnosticDialog
        diag = KeyDiagnosticDialog(self)
        diag.show()

    # ── Fullscreen ───────────────────────────────

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self):
        # Save state
        self._fullscreen_state = {
            "toolbar": not self.findChild(QToolBar).isHidden(),
            "menubar": not self.menuBar().isHidden(),
            "statusbar": not self.statusBar().isHidden(),
            "bookmarks": self._bookmark_dock.isVisible(),
            "quality": self._quality_dock.isVisible(),
            "usb": self._usb_dock.isVisible(),
            "tabbar": self._tabs.tabBar().isVisible(),
        }
        # Hide everything
        self.findChild(QToolBar).hide()
        self.menuBar().hide()
        self.statusBar().hide()
        self._bookmark_dock.hide()
        self._quality_dock.hide()
        self._usb_dock.hide()
        self._tabs.tabBar().hide()

        self.showFullScreen()

        # Position fullscreen toolbar
        screen = self.screen()
        if screen:
            self._fs_toolbar.position_on_screen(screen.geometry())
            s = self._active_session
            if s:
                self._fs_toolbar.set_connection_label(s.display_name)

        # Install event filter for mouse edge detection
        self.installEventFilter(self)

    def _exit_fullscreen(self):
        self.showNormal()
        self._fs_toolbar.hide()
        self.removeEventFilter(self)

        # Restore state
        st = self._fullscreen_state or {}
        if st.get("toolbar", True):
            self.findChild(QToolBar).show()
        if st.get("menubar", True):
            self.menuBar().show()
        if st.get("statusbar", True):
            self.statusBar().show()
        if st.get("bookmarks", True):
            self._bookmark_dock.show()
        if st.get("quality", False):
            self._quality_dock.show()
        if st.get("usb", False):
            self._usb_dock.show()
        self._tabs.tabBar().setVisible(st.get("tabbar", True))
        self._fullscreen_state = None

    def eventFilter(self, obj, event):
        """Detect mouse at top edge for fullscreen toolbar reveal."""
        if self.isFullScreen() and event.type() == QEvent.MouseMove:
            if event.globalPosition().y() <= REVEAL_ZONE:
                self._fs_toolbar.reveal()
        return super().eventFilter(obj, event)

    def mouseMoveEvent(self, event):
        """Also handle mouse moves on the main window itself."""
        if self.isFullScreen():
            if event.globalPosition().y() <= REVEAL_ZONE:
                self._fs_toolbar.reveal()
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self._exit_fullscreen()
            return
        super().keyPressEvent(event)

    # ── Health Updates ───────────────────────────

    def _update_health(self):
        self._health_status.update_display()
        s = self._active_session
        if s:
            s.overlay.update()
            if self.isFullScreen():
                self._fs_toolbar.update_health(s.health)

    # ── Resize ───────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        s = self._active_session
        if s:
            s.overlay.setGeometry(s.viewer.geometry())
        # Debounced resize request
        if not hasattr(self, '_resize_timer'):
            self._resize_timer = QTimer()
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self._send_resize)
        self._resize_timer.start(500)

    def _send_resize(self):
        s = self._active_session
        if s and s.is_connected:
            s.send_resize(s.viewer.width(), s.viewer.height())

    def closeEvent(self, event):
        for session in self._sessions.values():
            session.cleanup()
        event.accept()


# ════════════════════════════════════════════════════
# Entry Point
# ════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Teraguchi Remote Desktop Client")
    parser.add_argument("--host", default="", help="Server hostname or IP")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--username", "-u", default="")
    parser.add_argument("--password", "-p", default="")
    parser.add_argument("--broker", action="store_true",
                        help="Connect via broker instead of direct")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # macOS: don't swap Control/Meta so physical Control = Control_L on Linux
    if sys.platform == "darwin":
        QApplication.setAttribute(Qt.AA_MacDontSwapCtrlAndMeta, True)

    app = QApplication(sys.argv)
    app.setApplicationName("Teraguchi")
    app.setApplicationDisplayName("Teraguchi")
    app.setOrganizationName("Teraguchi")
    app.setDesktopFileName("teraguchi")
    app.setStyle("Fusion")

    # macOS: override process name so dock/menu bar shows "Teraguchi"
    import platform
    if platform.system() == "Darwin":
        try:
            from Foundation import NSBundle  # type: ignore
            bundle = NSBundle.mainBundle()
            info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
            if info:
                info["CFBundleName"] = "Teraguchi"
                info["CFBundleDisplayName"] = "Teraguchi"
        except ImportError:
            pass
    app.setStyleSheet(theme.generate_stylesheet())

    window = MainWindow(
        initial_host=args.host, initial_port=args.port,
        initial_user=args.username, initial_pass=args.password,
        initial_mode="broker" if args.broker else "direct")
    window.resize(1440, 900)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
