"""Teraguchi client main window + its dialog/panel widgets.

Extracted from client/main.py (STAB-05 / D-12). The extraction is
mechanical: MainWindow and the panels it owns (ConnectionDialog,
BrokerMachinePicker, BookmarkPanel, BookmarkDelegate, USBDevicePanel)
move here verbatim, with two surgical rewires:

  1. The QTabWidget is now owned by ``client.tab_manager.TabManager`` —
     MainWindow creates one and drops its ``.widget`` into setCentralWidget.
  2. Each tab page is a ``client.session_view.SessionView`` that wraps
     the existing ``Session`` instance (RESEARCH Open Q #4 — do NOT
     split client/session.py).

All other signal wiring is preserved byte-for-byte. ClientFSM wiring
from Plan 08 (Session owns its ClientProtocol which owns the FSM) is
untouched.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QSize
from PySide6.QtGui import QAction, QKeySequence, QColor, QFont, QShortcut
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QToolBar,
    QDialog, QFormLayout, QSpinBox, QMessageBox,
    QDockWidget, QListWidget, QListWidgetItem, QCheckBox,
    QComboBox, QMenu, QFileDialog,
    QStyledItemDelegate, QStyle, QToolButton,
    QRadioButton, QButtonGroup,
)

from client import theme
from client import icons
from client.session import Session
from client.session_view import SessionView
from client.tab_manager import TabManager
from client.bookmarks import BookmarkManager
from client.health_display import HealthStatusWidget, HealthData
from client.quality_control import QualityControlPanel
from client.fullscreen_toolbar import FullscreenToolbar, REVEAL_ZONE

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════
# Phase 3 D-01 / D-04 — Monitor mode picker
# ════════════════════════════════════════════════════


class ModeSelector(QWidget):
    """Connect-dialog Monitor-mode picker (UI-SPEC Surface 1).

    Three radios (Single monitor / Mirror all / Pick one). When
    ``Pick one`` is checked, the existing :class:`MonitorSelector`
    reveals in ``radio`` mode for the sub-selection (D-04).

    Emits ``mode_changed(mode, picked_monitor_id, picked_monitor_name)``
    on every radio toggle AND on every sub-selector pick — callers
    can treat it as the canonical live-state signal.

    Copy contract (UI-SPEC Surface 1) is verbatim; do not paraphrase
    the help strings below — test coverage greps them.
    """

    # Signal carries (mode, picked_monitor_id, picked_monitor_name)
    mode_changed = Signal(str, int, str)

    def __init__(self, parent=None):
        super().__init__(parent)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        heading = QLabel("Monitor mode")
        heading.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {theme.TEXT_PRIMARY};"
        )
        v.addWidget(heading)

        # Three radios — UI-SPEC Surface 1 verbatim copy.
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._radio_single = QRadioButton("Single monitor")
        self._radio_mirror = QRadioButton("Mirror all")
        self._radio_pick = QRadioButton("Pick one")
        self._help_single = QLabel(
            "Show one server monitor at a time. Lowest bandwidth."
        )
        self._help_mirror = QLabel(
            "Show every server monitor in one window. "
            "Matches client review workflows."
        )
        self._help_pick = QLabel(
            "Choose a specific server monitor. Remembered across sessions."
        )
        help_style = (
            f"font-size: 12px; color: {theme.TEXT_SECONDARY}; "
            f"padding-left: 24px;"
        )
        for helper in (self._help_single, self._help_mirror, self._help_pick):
            helper.setStyleSheet(help_style)
            helper.setWordWrap(True)

        self._group.addButton(self._radio_single)
        v.addWidget(self._radio_single)
        v.addWidget(self._help_single)
        self._group.addButton(self._radio_mirror)
        v.addWidget(self._radio_mirror)
        v.addWidget(self._help_mirror)
        self._group.addButton(self._radio_pick)
        v.addWidget(self._radio_pick)
        v.addWidget(self._help_pick)

        # Pick-one sub-selector — MonitorSelector in radio mode (D-04).
        self._pick_label = QLabel("Which monitor?")
        self._pick_label.setStyleSheet(
            f"font-size: 12px; color: {theme.TEXT_SECONDARY}; "
            f"padding-left: 24px;"
        )
        from client.monitor_selector import MonitorSelector
        self._monitor_picker = MonitorSelector()
        self._monitor_picker.set_mode("radio")
        self._pick_label.setVisible(False)
        self._monitor_picker.setVisible(False)
        v.addWidget(self._pick_label)
        v.addWidget(self._monitor_picker)

        # Wiring
        for r in (self._radio_single, self._radio_mirror, self._radio_pick):
            r.toggled.connect(self._on_radio_toggled)
        self._monitor_picker.selection_changed.connect(self._on_pick_changed)
        self._monitor_picker.monitor_missing.connect(self._on_monitor_missing)

        # Default state
        self._radio_mirror.setChecked(True)

    # ── Pre-fill / update --------------------------------------------

    def set_mode(self, mode: str, picked_id: int = -1,
                 picked_name: str = "") -> None:
        """Pre-fill from a saved bookmark (D-01)."""
        mapping = {
            "single": self._radio_single,
            "mirror_all": self._radio_mirror,
            "pick_one": self._radio_pick,
        }
        r = mapping.get(mode, self._radio_mirror)
        r.setChecked(True)
        is_pick = (r is self._radio_pick)
        self._pick_label.setVisible(is_pick)
        self._monitor_picker.setVisible(is_pick)
        if is_pick and picked_id >= 0:
            self._monitor_picker.set_picked(picked_id, picked_name)

    def update_monitors(self, monitors: list) -> None:
        self._monitor_picker.update_monitors(monitors)

    # ── Accessors ----------------------------------------------------

    @property
    def monitor_mode(self) -> str:
        if self._radio_single.isChecked():
            return "single"
        if self._radio_mirror.isChecked():
            return "mirror_all"
        return "pick_one"

    @property
    def picked_monitor_id(self) -> int:
        sel = self._monitor_picker.selected_ids()
        return sel[0] if sel else -1

    @property
    def picked_monitor_name(self) -> str:
        sel = self._monitor_picker.selected_names()
        return sel[0] if sel else ""

    # ── Internal -----------------------------------------------------

    def _on_radio_toggled(self, checked: bool):
        """Emit mode_changed ONLY on the checked side of a toggle.

        QButtonGroup exclusive mode fires two ``toggled`` signals per
        click — one ``False`` for the deselecting radio and one
        ``True`` for the new one. Filter to the True side so
        mode_changed emits once per user click.
        """
        if not checked:
            return
        is_pick = self._radio_pick.isChecked()
        self._pick_label.setVisible(is_pick)
        self._monitor_picker.setVisible(is_pick)
        self.mode_changed.emit(
            self.monitor_mode,
            self.picked_monitor_id,
            self.picked_monitor_name,
        )

    def _on_pick_changed(self, _ids: list):
        if self._radio_pick.isChecked():
            self.mode_changed.emit(
                "pick_one",
                self.picked_monitor_id,
                self.picked_monitor_name,
            )

    def _on_monitor_missing(self, name: str):
        # Transient surface handled by the session (Surface 4 toast).
        # Log here for diagnostics; the emit itself propagates to the
        # session if it's wired.
        logger.warning("mode_selector.bookmarked_monitor_missing name=%r", name)

    # ── D-03 mid-session lock ---------------------------------------

    def set_disabled_during_session(self, disabled: bool) -> None:
        """Gray out the mode widget during an active session (D-03).

        UI-SPEC Surface 3 locks the tooltip copy verbatim —
        ``Disconnect and reconnect to change monitor mode.`` The
        Qt ForbiddenCursor reinforces "not interactive right now".
        No mid-session switch path exists in v1 (see 03-CONTEXT.md).
        """
        self.setEnabled(not disabled)
        tip = (
            "Disconnect and reconnect to change monitor mode."
            if disabled else ""
        )
        for w in (self._radio_single, self._radio_mirror, self._radio_pick,
                  self._pick_label, self._monitor_picker):
            w.setToolTip(tip)
            if disabled:
                w.setCursor(Qt.ForbiddenCursor)
            else:
                w.unsetCursor()
        self.setAccessibleName(
            "Monitor mode, disabled during active session"
            if disabled else "Monitor mode"
        )


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

        # Phase 2 D-10 / WR-03 — destination kind picker drives the
        # per-bookmark Cmd<->Ctrl swap default. Linux server (the v1
        # Flame production path) gets swap=ON; Mac server gets swap=OFF
        # so Cmd shortcuts pass through unchanged. The user can override
        # the resulting swap state with the checkbox below before
        # accepting the dialog.
        self.destination_kind_combo = QComboBox()
        self.destination_kind_combo.addItem(
            "Linux (Rocky / Flame production)", "linux"
        )
        self.destination_kind_combo.addItem(
            "Mac (macOS server)", "mac"
        )
        self.destination_kind_combo.currentIndexChanged.connect(
            self._on_destination_kind_changed
        )
        layout.addRow("Destination:", self.destination_kind_combo)

        self.swap_cmd_ctrl_check = QCheckBox(
            "Swap Cmd/Ctrl for this server (Mac client → Linux Flame)"
        )
        # Default follows the destination kind (Linux: ON, Mac: OFF) and
        # tracks combobox changes via _on_destination_kind_changed below.
        self.swap_cmd_ctrl_check.setChecked(
            BookmarkManager.default_swap_for_destination("linux")
        )
        layout.addRow(self.swap_cmd_ctrl_check)

        # Phase 3 D-01 / D-04 — Monitor mode picker (per-session, locked
        # at connect per 03-CONTEXT.md). UI-SPEC Surface 1.
        self.mode_selector = ModeSelector()
        layout.addRow(self.mode_selector)

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

    def _on_destination_kind_changed(self, _index):
        """Phase 2 WR-03: keep the swap checkbox in sync with destination
        kind. Linux destinations default swap=ON (Cmd→Ctrl translation
        for Flame on Rocky); Mac destinations default swap=OFF (the Mac
        server interprets Cmd natively). The user can still flip the
        checkbox after the combobox changes if they want a non-default
        binding for an unusual host.
        """
        kind = self.destination_kind
        self.swap_cmd_ctrl_check.setChecked(
            BookmarkManager.default_swap_for_destination(kind)
        )

    @property
    def connection_mode(self):
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
    @property
    def swap_cmd_ctrl(self) -> bool:
        """Phase 2 D-10 — per-bookmark Cmd↔Ctrl swap toggle."""
        return self.swap_cmd_ctrl_check.isChecked()

    @property
    def destination_kind(self) -> str:
        """Phase 2 WR-03 — chosen destination platform ('linux' or 'mac').
        Drives the default swap_cmd_ctrl state via the combobox handler.
        Persisted on the ConnectionProfile so the per-bookmark binding
        survives reload.
        """
        data = self.destination_kind_combo.currentData()
        return data if data in ("linux", "mac") else "linux"

    # ── Phase 3 D-01 — Monitor mode picker accessors ──────────────

    @property
    def monitor_mode(self) -> str:
        """Chosen capture mode: 'single' | 'mirror_all' | 'pick_one'."""
        return self.mode_selector.monitor_mode

    @property
    def picked_monitor_id(self) -> int:
        """Bookmarked/newly-picked monitor id (-1 if not pick_one)."""
        return self.mode_selector.picked_monitor_id

    @property
    def picked_monitor_name(self) -> str:
        """Bookmarked/newly-picked monitor name ("" if not pick_one)."""
        return self.mode_selector.picked_monitor_name


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

        icon = icons.icon_server(theme.TEXT_SECONDARY)
        icon_rect = rect.adjusted(8, 10, 0, 0)
        icon.paint(painter, icon_rect.x(), icon_rect.y(), 20, 20)

        name = index.data(Qt.UserRole + 1) or "Unnamed"
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        name_font = QFont()
        name_font.setWeight(QFont.DemiBold)
        name_font.setPointSize(12)
        painter.setFont(name_font)
        painter.drawText(rect.adjusted(36, 6, -8, -22), Qt.AlignLeft | Qt.AlignVCenter, name)

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
                          use_tls=dialog.use_tls,
                          # Phase 2 WR-03 — persist destination kind so
                          # the swap default is honored on reload.
                          destination_kind=dialog.destination_kind,
                          # Phase 2 D-10 — persist Cmd<->Ctrl swap state.
                          swap_cmd_ctrl=dialog.swap_cmd_ctrl)
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
        # Phase 2 WR-03 — restore destination kind FIRST so its
        # currentIndexChanged handler doesn't clobber the saved
        # swap_cmd_ctrl state we set immediately below.
        kind = getattr(profile, "destination_kind", "linux") or "linux"
        idx = dialog.destination_kind_combo.findData(kind)
        if idx >= 0:
            dialog.destination_kind_combo.setCurrentIndex(idx)
        # Phase 2 D-10 — reflect existing per-bookmark swap state.
        # (Set AFTER the combobox so we override its default-swap callback.)
        dialog.swap_cmd_ctrl_check.setChecked(profile.swap_cmd_ctrl)
        if dialog.exec() == QDialog.Accepted:
            self._mgr.update(bid, name=dialog.bookmark_name or profile.name,
                             host=dialog.host, port=dialog.port,
                             username=dialog.username, password=dialog.password,
                             use_tls=dialog.use_tls,
                             destination_kind=dialog.destination_kind,
                             swap_cmd_ctrl=dialog.swap_cmd_ctrl)
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
    """Tab-based main window with multiple concurrent sessions.

    After Plan 12 / D-12: the central widget is owned by
    ``client.tab_manager.TabManager`` and each tab page is a
    ``client.session_view.SessionView`` that wraps an existing
    ``client.session.Session`` (RESEARCH Open Q #4: wrap — do NOT split).
    """

    def __init__(self, initial_host="", initial_port=443,
                 initial_user="", initial_pass="",
                 initial_mode="direct"):
        super().__init__()
        self.setWindowTitle("Teraguchi")
        self.setMinimumSize(900, 600)

        self._bookmarks = BookmarkManager()
        # tab_index -> SessionView (which owns its Session). The
        # dict-keyed-by-tab-index scheme survives extraction unchanged.
        self._views: dict[int, SessionView] = {}
        self._fullscreen_state = None  # saved UI state for fullscreen restore

        # ── Tabs (central widget) — now owned by TabManager ──
        self._tab_manager = TabManager(self, new_connection_callback=self._show_connect_dialog)
        self._tab_manager.tab_close_requested.connect(self._close_tab)
        self._tab_manager.current_changed.connect(self._on_tab_changed)
        self.setCentralWidget(self._tab_manager.widget)

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

    # ── Toolbar & Menus ─────────────────────────

    def _setup_toolbar(self):
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
        # Phase 2 D-11 (Plan 02-09 Rule 3 deviation): F9 was previously
        # bound to Health Overlay. F9 is reserved as the canonical Flame
        # panic-release-all-modifiers shortcut (FLAME_CRITICAL_CHORDS in
        # common/keymap.py + CONTEXT.md D-11), so Health Overlay moves to
        # Ctrl+Alt+H. Conflict resolution preserves muscle memory: F9 is
        # the keymap-locked panic key.
        view_menu.addAction(self._action(
            "Health Overlay", "Ctrl+Alt+H", self._toggle_health, icons.icon_health()))
        view_menu.addSeparator()
        view_menu.addAction(self._action(
            "Key Diagnostic", "F10", self._show_key_diagnostic, icons.icon_keyboard()))

        # Help menu
        help_menu = mb.addMenu("&Help")
        # Phase 2 D-11 trigger #4 — client-side panic shortcut. F9 is the
        # documented "release all stuck modifiers" key (FLAME_CRITICAL_CHORDS
        # entry 19, CONTEXT.md D-11). Surfaced in the Help menu so artists
        # can find it discoverable. The shortcut itself is registered as a
        # QShortcut on the main window so it fires regardless of which tab
        # has focus.
        help_menu.addAction(self._action(
            "Release stuck modifiers (F9)", "", self._panic_release_modifiers))
        help_menu.addAction(self._action(
            "About Teraguchi", "", self._show_about))

        # Window-scope F9 shortcut so the panic fires no matter which
        # widget currently has focus inside the active tab.
        self._panic_shortcut = QShortcut(QKeySequence("F9"), self)
        self._panic_shortcut.setContext(Qt.ApplicationShortcut)
        self._panic_shortcut.activated.connect(self._panic_release_modifiers)

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
        # Phase 2 D-11: F9 reserved for panic-release-modifiers; toolbar
        # button uses Ctrl+Alt+H to match the menu.
        tb.addAction(self._action(
            "Health", "Ctrl+Alt+H", self._toggle_health, icons.icon_health()))
        tb.addAction(self._action(
            "Key Diagnostic", "F10", self._show_key_diagnostic, icons.icon_keyboard()))
        tb.addSeparator()

        from client.monitor_selector import MonitorSelector
        self._monitor_selector = MonitorSelector()
        self._monitor_selector.selection_changed.connect(self._on_monitors_changed)
        tb.addWidget(self._monitor_selector)

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
    def _active_view(self) -> Optional[SessionView]:
        idx = self._tab_manager.current_index()
        return self._views.get(idx)

    @property
    def _active_session(self) -> Optional[Session]:
        view = self._active_view
        return view.session if view else None

    # ── Session / Tab Management ─────────────────

    def _new_session_and_connect(self, host, port, username, password,
                                  use_tls=True, auto_reconnect=True,
                                  bookmark_id="", mode="direct",
                                  swap_cmd_ctrl: bool = True):
        session = Session(self)
        view = SessionView(session, self)
        idx = self._tab_manager.add_tab(view, session.display_name)
        self._views[idx] = view
        self._tab_manager.set_current_index(idx)

        # Wire session signals — same surface as pre-extraction, routed via idx.
        session.status_changed.connect(lambda s, i=idx: self._on_session_status(i, s))
        session.title_changed.connect(lambda t, i=idx: self._tab_manager.set_tab_text(i, t))
        session.auth_failed.connect(lambda msg: QMessageBox.warning(self, "Auth Failed", msg))
        session.monitor_list_received.connect(self._on_monitor_list)
        session.file_transfer_finished.connect(self._on_file_transfer_done)
        session.usb_devices_updated.connect(self._on_usb_devices_updated)
        session.broker_machine_needed.connect(
            lambda machines, s=session: self._on_broker_machine_needed(s, machines))

        # Phase 2 D-10 — when launching from a bookmark, the saved
        # ConnectionProfile carries the per-server swap_cmd_ctrl preference.
        # Override the default-True for the broker path (broker assigns
        # downstream Linux Flames; default-on is correct).
        if bookmark_id:
            profile = self._bookmarks.get(bookmark_id)
            if profile is not None:
                swap_cmd_ctrl = bool(profile.swap_cmd_ctrl)

        if mode == "broker":
            logger.info("Connecting via broker to %s:%d", host, port)
            session.connect_broker(host, port, username, password,
                                   use_tls=use_tls, auto_reconnect=auto_reconnect)
        else:
            session.connect(host, port, username, password,
                            use_tls=use_tls, auto_reconnect=auto_reconnect,
                            bookmark_id=bookmark_id,
                            swap_cmd_ctrl=swap_cmd_ctrl)

        session.apply_quality(self._quality_panel.settings)

        label = "broker" if mode == "broker" else host
        self._status_label.setText(f"Connecting to {label}:{port}...")

    def _close_tab(self, idx):
        view = self._views.pop(idx, None)
        if view is not None:
            view.session.cleanup()
        self._tab_manager.remove_tab(idx)

        # Re-key views after removal — keep the mapping aligned with the
        # QTabWidget's internal indices.
        new_views: dict[int, SessionView] = {}
        for i in range(self._tab_manager.count()):
            page = self._tab_manager.widget_at(i)
            for old_idx, v in list(self._views.items()):
                if v is page:
                    new_views[i] = v
                    del self._views[old_idx]
                    break
        self._views.update(new_views)

        if self._tab_manager.count() == 0:
            self._status_label.setText("No connections")
            self._health_status.data = HealthData()

    def _on_tab_changed(self, idx):
        view = self._views.get(idx)
        if view:
            session = view.session
            self._health_status.data = session.health
            self._status_label.setText(session.display_name)
            if self.isFullScreen():
                self._fs_toolbar.set_connection_label(session.display_name)

    def _on_session_status(self, idx, status):
        view = self._views.get(idx)
        if not view:
            return
        session = view.session

        colors = {"connected": theme.SUCCESS, "connecting": theme.WARNING,
                  "disconnected": theme.TEXT_MUTED, "error": theme.DANGER}
        color = colors.get(status, theme.TEXT_MUTED)

        if idx == self._tab_manager.current_index():
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
                    use_tls=dialog.use_tls, mode=mode,
                    # Phase 2 D-10 — persist the dialog's swap state.
                    swap_cmd_ctrl=dialog.swap_cmd_ctrl)
                self._bookmark_panel._refresh()

            self._new_session_and_connect(
                dialog.host, dialog.port, dialog.username, dialog.password,
                use_tls=dialog.use_tls, auto_reconnect=dialog.auto_reconnect,
                bookmark_id=bid, mode=mode,
                swap_cmd_ctrl=dialog.swap_cmd_ctrl)

    def _connect_bookmark(self, bookmark_id):
        profile = self._bookmarks.get(bookmark_id)
        if not profile:
            return
        password = self._bookmarks.get_password(bookmark_id)
        mode = getattr(profile, "mode", "direct") or "direct"

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

    def _disconnect_active(self):
        """Disconnect the active session and close its tab.

        session.disconnect() by itself only tears down the protocol layer —
        it leaves the tab open with a frozen last frame and a still-running
        decoder/audio pipeline, which looks to the user like nothing
        happened. Closing the tab runs the full cleanup path.
        """
        idx = self._tab_manager.current_index()
        if idx < 0 or idx not in self._views:
            return
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
            s.select_monitor(0)  # 0 = all monitors / virtual desktop
            s.viewer.set_monitor_regions(selected_regions)
        else:
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

    def _panic_release_modifiers(self):
        """Phase 2 D-11 trigger #4 — F9 client-side panic release-all.

        Forwards a synthetic ``RemoteViewer.reset_modifiers_requested``
        emit to the active session's viewer with reason='panic_f9'.
        ``client/session.py::_wire_viewer`` catches the signal and the
        protocol layer turns it into a ``KeyResetModifiersMsg`` on the
        wire. No-op when no session is active.
        """
        s = self._active_session
        if s is None:
            return
        try:
            s.viewer.reset_modifiers_requested.emit("panic_f9")
            logger.info("main_window.panic_release_modifiers: emitted")
        except Exception as e:
            logger.warning("main_window.panic_release_modifiers_failed: %s", e)

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
        self._fullscreen_state = {
            "toolbar": not self.findChild(QToolBar).isHidden(),
            "menubar": not self.menuBar().isHidden(),
            "statusbar": not self.statusBar().isHidden(),
            "bookmarks": self._bookmark_dock.isVisible(),
            "quality": self._quality_dock.isVisible(),
            "usb": self._usb_dock.isVisible(),
            "tabbar": self._tab_manager.tab_bar().isVisible(),
        }
        self.findChild(QToolBar).hide()
        self.menuBar().hide()
        self.statusBar().hide()
        self._bookmark_dock.hide()
        self._quality_dock.hide()
        self._usb_dock.hide()
        self._tab_manager.tab_bar().hide()

        self.showFullScreen()

        screen = self.screen()
        if screen:
            self._fs_toolbar.position_on_screen(screen.geometry())
            s = self._active_session
            if s:
                self._fs_toolbar.set_connection_label(s.display_name)

        self.installEventFilter(self)

    def _exit_fullscreen(self):
        self.showNormal()
        self._fs_toolbar.hide()
        self.removeEventFilter(self)

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
        self._tab_manager.tab_bar().setVisible(st.get("tabbar", True))
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
        for view in self._views.values():
            view.session.cleanup()
        event.accept()


__all__ = [
    "MainWindow",
    "ConnectionDialog",
    "BrokerMachinePicker",
    "BookmarkPanel",
    "BookmarkDelegate",
    "USBDevicePanel",
]
