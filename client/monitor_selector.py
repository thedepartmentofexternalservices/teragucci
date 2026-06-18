"""
Multi-monitor selector widget for Teraguchi.

Replaces the single-select QComboBox with a checkable menu.
Users can select any combination of monitors — beats PCoIP's
all-or-one limitation.

Phase 3 D-04 — adds a ``mode`` attribute:
  * ``"checkbox"`` (default) — multi-select, Select All/None visible,
    label shows "All (N)" / "N of M". Mirror-all workflow.
  * ``"radio"``              — single-select, Select All/None hidden,
    label shows picked monitor's NAME. Drives the connect-dialog
    pick-one sub-selector (UI-SPEC Surface 4).

The ``selection_changed`` signal contract is unchanged — radio mode
emits a 1-element list with the picked monitor dict.

Phase 3 also adds ``monitor_missing(name)`` for the UI-SPEC Surface 4
toast path: when a bookmarked ``picked_monitor_id+name`` is not in
the current server monitor list, the selector falls back to primary
AND emits a signal so the session can display a transient
"X not found, now viewing primary." banner.
"""

import logging

from PySide6.QtCore import Signal, QSignalBlocker
from PySide6.QtWidgets import QToolButton, QMenu

logger = logging.getLogger(__name__)


class MonitorSelector(QToolButton):
    """Toolbar button that shows a checkable menu of monitors.

    Emits selection_changed with:
      - list of selected monitor dicts (x, y, width, height, id, name)
      - empty list means "all monitors" (nothing specifically selected)
    """

    selection_changed = Signal(list)  # list of selected monitor dicts
    # Phase 3 D-04 — UI-SPEC Surface 4: fires when a caller asks us to
    # pre-select a bookmarked monitor that isn't in the current list
    # (server hot-plug, monitor off, etc.). Session binds a toast.
    monitor_missing = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._monitors: list = []
        self._actions: list = []  # (QAction, monitor_dict) pairs
        # Phase 3 D-04 — "checkbox" (default, mirror-all) | "radio" (pick-one)
        self._mode: str = "checkbox"

        self.setText("Monitors")
        self.setPopupMode(QToolButton.InstantPopup)
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self.setMinimumWidth(120)

        # Placeholders so set_mode() can flip visibility even before the
        # first update_monitors() populates the real quick-actions.
        self._action_select_all = self._menu.addAction("Select All")
        self._action_select_all.triggered.connect(self._select_all)
        self._action_select_none = self._menu.addAction("Select None")
        self._action_select_none.triggered.connect(self._select_none)

    # Phase 3 D-04 --------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """Switch between checkbox (multi) and radio (single-check) mode.

        ``"radio"`` hides Select All / Select None (meaningless for
        single-select), updates menu title to "Pick one monitor", and
        flips label semantics so ``_update_label`` renders the picked
        monitor's name rather than "All (N)".

        Mode is reversible — calling ``set_mode("checkbox")`` restores
        the original multi-select semantics.
        """
        if mode not in ("checkbox", "radio"):
            logger.warning("monitor_selector.invalid_mode %r → checkbox", mode)
            mode = "checkbox"
        self._mode = mode
        # Hide Select All/None in radio mode — they don't make sense.
        self._action_select_all.setVisible(mode != "radio")
        self._action_select_none.setVisible(mode != "radio")
        # Update menu title (UI-SPEC Surface 4)
        self._menu.setTitle(
            "Pick one monitor" if mode == "radio" else "Monitors"
        )

    @property
    def mode(self) -> str:
        """Current mode: 'checkbox' (multi) or 'radio' (single-check)."""
        return self._mode

    def set_picked(self, monitor_id: int, monitor_name: str) -> None:
        """Pre-select a monitor in radio mode (bookmark pre-fill path).

        Match by ``id`` first (stable across reorder), then by ``name``
        (stable across hot-plug). If neither matches, emit
        ``monitor_missing`` with the requested name and fall back to
        the primary monitor per D-09.
        """
        if self._mode != "radio":
            return
        for action, mon in self._actions:
            if mon.get("id") == monitor_id:
                with QSignalBlocker(action):
                    self._set_radio_checked(action)
                self._update_label()
                return
        for action, mon in self._actions:
            if mon.get("name") == monitor_name:
                with QSignalBlocker(action):
                    self._set_radio_checked(action)
                self._update_label()
                return
        # Not found — fall back to primary + emit the toast signal.
        self.monitor_missing.emit(monitor_name or f"id={monitor_id}")
        for action, mon in self._actions:
            if mon.get("primary"):
                with QSignalBlocker(action):
                    self._set_radio_checked(action)
                self._update_label()
                return

    def selected_ids(self) -> list:
        """Return currently-checked monitor ids."""
        return [m.get("id", 0) for a, m in self._actions if a.isChecked()]

    def selected_names(self) -> list:
        """Return currently-checked monitor names."""
        return [m.get("name", "") for a, m in self._actions if a.isChecked()]

    # Core behavior -------------------------------------------------------

    def update_monitors(self, monitors: list):
        """Update the monitor list from server. Preserves existing selections."""
        old_selected_ids = {m["id"] for _, m in self._actions
                           if _.isChecked()}

        self._menu.clear()
        self._actions.clear()
        self._monitors = monitors

        if not monitors:
            self.setText("No monitors")
            # Re-add the quick-actions (menu was cleared). Keep visibility
            # consistent with current mode.
            self._action_select_all = self._menu.addAction("Select All")
            self._action_select_all.triggered.connect(self._select_all)
            self._action_select_none = self._menu.addAction("Select None")
            self._action_select_none.triggered.connect(self._select_none)
            self._action_select_all.setVisible(self._mode != "radio")
            self._action_select_none.setVisible(self._mode != "radio")
            return

        for mon in monitors:
            mon_id = mon.get("id", 0)
            name = mon.get("name", f"Monitor {mon_id}")
            w = mon.get("width", 0)
            h = mon.get("height", 0)
            label = f"{name} ({w}x{h})"

            action = self._menu.addAction(label)
            action.setCheckable(True)
            if self._mode == "radio":
                # Radio mode: begin unchecked; caller uses set_picked()
                # to mark the bookmarked monitor. The label shows
                # "Monitors" until a pick lands.
                action.setChecked(False)
            else:
                # Restore previous selection, or check all by default
                if old_selected_ids:
                    action.setChecked(mon_id in old_selected_ids)
                else:
                    action.setChecked(True)
            action.toggled.connect(self._on_toggled)
            self._actions.append((action, mon))

        # Separator + quick actions
        self._menu.addSeparator()
        self._action_select_all = self._menu.addAction("Select All")
        self._action_select_all.triggered.connect(self._select_all)
        self._action_select_none = self._menu.addAction("Select None")
        self._action_select_none.triggered.connect(self._select_none)
        # Respect current mode (radio hides both)
        self._action_select_all.setVisible(self._mode != "radio")
        self._action_select_none.setVisible(self._mode != "radio")

        self._update_label()

    def _on_toggled(self, _checked):
        # Radio mode: when an action is toggled ON, uncheck every other
        # action (block signals during the bulk update so we don't re-
        # enter this handler for each uncheck).
        if self._mode == "radio" and _checked:
            sender = self.sender()
            for action, _mon in self._actions:
                if action is not sender and action.isChecked():
                    with QSignalBlocker(action):
                        action.setChecked(False)
        self._update_label()
        self._emit_selection()

    def _set_radio_checked(self, target_action) -> None:
        """Mark one action checked and all others unchecked (radio mode)."""
        for action, _mon in self._actions:
            if action is target_action:
                action.setChecked(True)
            elif action.isChecked():
                with QSignalBlocker(action):
                    action.setChecked(False)

    def _select_all(self):
        for action, _ in self._actions:
            action.blockSignals(True)
            action.setChecked(True)
            action.blockSignals(False)
        self._update_label()
        self._emit_selection()

    def _select_none(self):
        for action, _ in self._actions:
            action.blockSignals(True)
            action.setChecked(False)
            action.blockSignals(False)
        self._update_label()
        self._emit_selection()

    def _update_label(self):
        checked = [m for a, m in self._actions if a.isChecked()]
        total = len(self._actions)
        # Phase 3 D-04 / UI-SPEC Surface 4 — radio-mode label is the
        # picked monitor's NAME (single-selection). Not "All (N)".
        if self._mode == "radio":
            if len(checked) == 1:
                self.setText(checked[0].get("name", "Monitor"))
            else:
                self.setText("Monitors")
            return
        if len(checked) == 0:
            self.setText("No monitors")
        elif len(checked) == total:
            self.setText(f"All ({total})")
        elif len(checked) == 1:
            name = checked[0].get("name", "Monitor")
            self.setText(name)
        else:
            self.setText(f"{len(checked)} of {total}")

    def _emit_selection(self):
        checked = [m for a, m in self._actions if a.isChecked()]
        total = len(self._actions)

        # Radio mode — always emit a 1-element list (or empty if none).
        # Binary compat with checkbox mode: downstream receivers see
        # a list, same as before.
        if self._mode == "radio":
            self.selection_changed.emit(checked[:1])
            return

        if len(checked) == total or len(checked) == 0:
            # All selected or none = show everything (no crop)
            self.selection_changed.emit([])
        else:
            self.selection_changed.emit(checked)

    def get_selected(self) -> list:
        """Return currently selected monitor dicts."""
        checked = [m for a, m in self._actions if a.isChecked()]
        total = len(self._actions)
        if self._mode == "radio":
            return checked[:1]
        if len(checked) == total or len(checked) == 0:
            return []
        return checked
