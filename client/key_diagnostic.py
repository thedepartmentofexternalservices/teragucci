"""
Built-in keystroke diagnostic dialog for Teraguchi.

Shows the full key translation path in real time:
  Physical key → Qt key code → Wire message → X11 keysym

Accessible from View → Key Diagnostic (F10) in the client.

Plan 02-11 (D-20): adds a second tab "Wacom setup" that surfaces
TCC permission status (Input Monitoring + Wacom-driver Accessibility),
detects the Wacom driver bundle on disk, and offers deep-link buttons
to the right System Settings panes plus a download link to the official
Wacom driver. Goal: turn "why did my pen stop working" into an
in-app question with a click-through answer instead of a log dive.
"""

import logging
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from client.tcc_detect import (
    SYSTEM_SETTINGS_ACCESSIBILITY,
    SYSTEM_SETTINGS_INPUT_MONITORING,
    WACOM_DRIVER_URL,
    list_connected_wacom_devices,
    read_tcc_status,
)

logger = logging.getLogger(__name__)

# Qt key code → X11 keysym (must match server/xtest_injector.py QT_TO_XKEYSYM)
_QT_TO_XKEYSYM = {
    0x01000000: 'Escape', 0x01000001: 'Tab', 0x01000003: 'BackSpace',
    0x01000004: 'Return', 0x01000005: 'KP_Enter', 0x01000006: 'Insert',
    0x01000007: 'Delete', 0x01000008: 'Pause', 0x01000009: 'Print',
    0x01000010: 'Home', 0x01000011: 'End',
    0x01000012: 'Left', 0x01000013: 'Up', 0x01000014: 'Right', 0x01000015: 'Down',
    0x01000016: 'Page_Up', 0x01000017: 'Page_Down',
    0x01000020: 'Shift_L', 0x01000021: 'Control_L', 0x01000022: 'Meta_L',
    0x01000023: 'Alt_L', 0x01000024: 'Caps_Lock', 0x01000025: 'Num_Lock',
    0x01000026: 'Scroll_Lock',
    0x01000030: 'F1', 0x01000031: 'F2', 0x01000032: 'F3', 0x01000033: 'F4',
    0x01000034: 'F5', 0x01000035: 'F6', 0x01000036: 'F7', 0x01000037: 'F8',
    0x01000038: 'F9', 0x01000039: 'F10', 0x0100003a: 'F11', 0x0100003b: 'F12',
    0x01000058: 'Super_L', 0x01000059: 'Super_R',
    0x01000055: 'Menu',
    0x20: 'space',
}

_QT_KEY_NAMES = {
    0x01000000: "Escape", 0x01000001: "Tab", 0x01000002: "Backtab",
    0x01000003: "Backspace", 0x01000004: "Return", 0x01000005: "KP_Enter",
    0x01000006: "Insert", 0x01000007: "Delete", 0x01000008: "Pause",
    0x01000009: "Print",
    0x01000010: "Home", 0x01000011: "End",
    0x01000012: "Left", 0x01000013: "Up", 0x01000014: "Right", 0x01000015: "Down",
    0x01000016: "PageUp", 0x01000017: "PageDown",
    0x01000020: "Shift", 0x01000021: "Control", 0x01000022: "Meta",
    0x01000023: "Alt", 0x01000024: "CapsLock", 0x01000025: "NumLock",
    0x01000026: "ScrollLock",
    0x01000030: "F1", 0x01000031: "F2", 0x01000032: "F3", 0x01000033: "F4",
    0x01000034: "F5", 0x01000035: "F6", 0x01000036: "F7", 0x01000037: "F8",
    0x01000038: "F9", 0x01000039: "F10", 0x0100003a: "F11", 0x0100003b: "F12",
    0x01000058: "Super_L", 0x01000059: "Super_R", 0x01000055: "Menu",
    0x20: "Space",
}


def _key_name(code: int) -> str:
    if code in _QT_KEY_NAMES:
        return _QT_KEY_NAMES[code]
    if 0x20 <= code <= 0x7e:
        return chr(code)
    return f"0x{code:x}"


def _mods_str(mods) -> str:
    parts = []
    if mods & Qt.ShiftModifier:
        parts.append("Shift")
    if mods & Qt.ControlModifier:
        parts.append("Ctrl")
    if mods & Qt.AltModifier:
        parts.append("Alt")
    if mods & Qt.MetaModifier:
        parts.append("Meta")
    return "+".join(parts) if parts else ""


def _x11_target(qt_key: int) -> str:
    if qt_key in _QT_TO_XKEYSYM:
        return _QT_TO_XKEYSYM[qt_key]
    if 0x20 <= qt_key <= 0x7e:
        return f"'{chr(qt_key).lower()}'"
    return "UNMAPPED"


class KeyDiagnosticTab(QWidget):
    """The existing keystroke-translation pane, now lives inside a tab.

    Captures keyPress/keyRelease and renders the Qt -> X11 translation
    path in a colored log. Identical behavior to the pre-D-20 dialog;
    only the container changed.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # Need StrongFocus so the QTabWidget routes key events here when
        # this tab is active.
        self.setFocusPolicy(Qt.StrongFocus)
        layout = QVBoxLayout(self)

        # Header
        header = QLabel(
            "Press any key or combo to see how Teraguchi translates it.\n"
            "Green = mapped to X11   Red = UNMAPPED (will be dropped)")
        header.setWordWrap(True)
        layout.addWidget(header)

        # Current keystroke display
        mono = QFont("Menlo" if sys.platform == "darwin" else "Monospace", 16)
        self._current = QLabel("Press a key...")
        self._current.setFont(mono)
        self._current.setStyleSheet(
            "background: #12121c; color: #ececf1; padding: 16px; "
            "border-radius: 10px; border: 1px solid #262640;")
        self._current.setMinimumHeight(80)
        layout.addWidget(self._current)

        # Platform info
        dont_swap = getattr(Qt, 'AA_MacDontSwapCtrlAndMeta', None)
        plat_text = f"Platform: {sys.platform}"
        if sys.platform == "darwin" and dont_swap is not None:
            plat_text += "  |  AA_MacDontSwapCtrlAndMeta: ON"
        layout.addWidget(QLabel(plat_text))

        # Event log
        layout.addWidget(QLabel("Event log (most recent at bottom):"))
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont(
            "Menlo" if sys.platform == "darwin" else "Monospace", 11))
        self._log.setStyleSheet(
            "background: #0a0a10; color: #ececf1; "
            "border: 1px solid #262640; border-radius: 8px; padding: 8px;")
        layout.addWidget(self._log)

        # Flame reference
        ref = QLabel(
            "Flame keys to test: Ctrl+Z · Ctrl+Shift+Z · F5–F8 · "
            "` (backtick) · Tab · Space · Esc · Ctrl+C · Delete · Home/End")
        ref.setWordWrap(True)
        ref.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(ref)

        # Clear button (Close lives on the dialog).
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(self._log.clear)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._count = 0

    def keyPressEvent(self, event: QKeyEvent):
        if event.isAutoRepeat():
            return
        self._record(event, pressed=True)
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent):
        if event.isAutoRepeat():
            return
        self._record(event, pressed=False)
        event.accept()

    def _record(self, event: QKeyEvent, pressed: bool):
        qt_key = event.key()
        raw_mods = event.modifiers()
        name = _key_name(qt_key)
        x11 = _x11_target(qt_key)
        mapped = x11 != "UNMAPPED"
        action = "PRESS  " if pressed else "RELEASE"
        mods = _mods_str(raw_mods)

        # Update big display on press
        if pressed:
            combo = f"{mods}+{name}" if mods and qt_key not in (
                Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta) else name
            color = "#00c878" if mapped else "#e5484d"
            self._current.setText(
                f'<span style="color:{color}; font-size:18px">{combo}</span><br>'
                f'<span style="color:#888; font-size:13px">'
                f'Qt: 0x{qt_key:x}  →  X11: {x11}</span>')

        # Log line
        self._count += 1
        color = "#00c878" if mapped else "#e5484d"
        press_color = "#88ccff" if pressed else "#555"
        mod_display = mods if mods else "—"
        self._log.append(
            f'<span style="color:#555">{self._count:4d}</span> '
            f'<span style="color:{press_color}">{action}</span> '
            f'<span style="color:{color}">{name:12s}</span> '
            f'<span style="color:#888">mods=[{mod_display:16s}]  '
            f'qt=0x{qt_key:08x}  →  x11={x11}</span>')


class WacomSetupTab(QWidget):
    """D-20 — surfaces TCC permission state + Wacom driver detection.

    Renders four status rows and three deep-link buttons:
      * Teraguchi Input Monitoring (OK / MISSING) + Open System Settings
      * Wacom driver Accessibility (OK / MISSING / Not installed)
        + Open System Settings + (if not installed) Download Wacom driver
      * Wacom driver bundle on disk
      * Wacom device(s) currently on USB

    The TCC read happens once at construction time (cheap, ~1ms on a
    typical TCC.db). A "Re-check" button re-runs ``read_tcc_status``
    so artists can recheck after toggling a permission in System
    Settings without closing the dialog.

    On non-darwin hosts every status reads as Not installed / MISSING
    and the deep-link buttons are visible-but-inert (the URL handler
    is macOS-only; clicking does nothing on Linux).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._status: dict = {}
        self._refresh()

    def _refresh(self):
        # Wipe any previously rendered rows.
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                # Recursively delete child widgets in nested layouts.
                while sub.count():
                    sub_item = sub.takeAt(0)
                    sw = sub_item.widget()
                    if sw is not None:
                        sw.deleteLater()
                sub.deleteLater()

        self._status = read_tcc_status()
        self._build_rows()

    def _status_label(self, granted: bool) -> str:
        return "OK" if granted else "MISSING"

    def _build_rows(self):
        # Header
        hdr = QLabel(
            "Wacom + macOS permissions setup.\n"
            "If your pen stops working, check these four rows in order."
        )
        hdr.setWordWrap(True)
        hdr.setStyleSheet(
            "background: #12121c; color: #ececf1; padding: 12px; "
            "border-radius: 8px; border: 1px solid #262640;")
        self._layout.addWidget(hdr)

        # Row 1 — Teraguchi Input Monitoring
        row_im = QHBoxLayout()
        row_im.addWidget(QLabel(
            "Teraguchi Input Monitoring: "
            f"{self._status_label(self._status['input_monitoring_granted'])}"
        ))
        btn_im = QPushButton("Open System Settings")
        btn_im.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(SYSTEM_SETTINGS_INPUT_MONITORING))
        )
        row_im.addWidget(btn_im)
        row_im.addStretch()
        self._layout.addLayout(row_im)

        # Row 2 — Wacom driver Accessibility (status depends on driver
        # presence: "Not installed" trumps OK/MISSING when the bundle
        # isn't on disk).
        row_acc = QHBoxLayout()
        if not self._status["wacom_driver_installed"]:
            acc_label = "Not installed"
        else:
            acc_label = self._status_label(
                self._status["accessibility_granted_for_wacom"])
        row_acc.addWidget(QLabel(f"Wacom driver Accessibility: {acc_label}"))
        btn_acc = QPushButton("Open System Settings")
        btn_acc.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(SYSTEM_SETTINGS_ACCESSIBILITY))
        )
        row_acc.addWidget(btn_acc)
        if not self._status["wacom_driver_installed"]:
            btn_dl = QPushButton("Download Wacom driver")
            btn_dl.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(WACOM_DRIVER_URL))
            )
            row_acc.addWidget(btn_dl)
        row_acc.addStretch()
        self._layout.addLayout(row_acc)

        # Row 3 — driver bundle on disk
        installed_label = (
            "installed" if self._status["wacom_driver_installed"]
            else "Not installed"
        )
        self._layout.addWidget(QLabel(f"Wacom driver detected: {installed_label}"))

        # Row 4 — connected device(s)
        devices = list_connected_wacom_devices()
        device_text = devices[0] if devices else "none"
        self._layout.addWidget(QLabel(f"Wacom device connected: {device_text}"))

        # Re-check button
        recheck_row = QHBoxLayout()
        recheck = QPushButton("Re-check permissions")
        recheck.clicked.connect(self._refresh)
        recheck_row.addStretch()
        recheck_row.addWidget(recheck)
        self._layout.addLayout(recheck_row)

        # TCC raw rows debug pane (collapsed by default? -- we just
        # render at the bottom so artists can copy/paste into bug
        # reports if needed). Kept terse to avoid information
        # disclosure beyond what System Settings already shows.
        if self._status["raw_rows"]:
            dbg = QLabel(
                "TCC rows seen ({n}):\n{rows}".format(
                    n=len(self._status["raw_rows"]),
                    rows="\n".join(
                        f"  {svc}: {cli} = {av}"
                        for (svc, cli, av) in self._status["raw_rows"][:10]
                    ),
                )
            )
            dbg.setStyleSheet(
                "color: #888; font-family: Menlo, Monospace; font-size: 10px;")
            self._layout.addWidget(dbg)
        elif not self._status["tcc_db_readable"]:
            note = QLabel(
                "TCC database not readable here (Linux host, or pre-grant "
                "macOS state). Run on macOS once the client is signed; "
                "permissions appear after the first prompt."
            )
            note.setStyleSheet("color: #888; font-size: 11px;")
            note.setWordWrap(True)
            self._layout.addWidget(note)

        self._layout.addStretch()


class KeyDiagnosticDialog(QDialog):
    """Tabbed diagnostic dialog: keystroke translation + Wacom setup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Teraguchi — Key Diagnostic")
        self.setMinimumSize(680, 540)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)

        layout = QVBoxLayout(self)

        # Tab container
        self._tabs = QTabWidget(self)
        self._key_tab = KeyDiagnosticTab(self)
        self._wacom_tab = WacomSetupTab(self)
        self._tabs.addTab(self._key_tab, "Key diagnostic")
        self._tabs.addTab(self._wacom_tab, "Wacom setup")
        layout.addWidget(self._tabs)

        # Close button (bottom)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
