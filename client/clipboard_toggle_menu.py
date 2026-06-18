"""Phase 3 D-15 / UI-SPEC C-07 — Clipboard direction toggle button.

4-checkbox QMenu controlling per-direction text + image clipboard flow.
Per-bookmark + in-session override pattern (mirrors Phase 2 D-10
Cmd<->Ctrl swap pattern). Default-ON for all 4 directions per D-15.
Rows 3 + 4 are nested under rows 1 + 2 — disabling row 1 disables
row 3 without state loss.

UI-SPEC Surface 7 verbatim copy is load-bearing for the acceptance
grep battery in the plan. Any change here must be mirrored in
03-UI-SPEC.md.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QToolButton, QMenu, QWidgetAction, QCheckBox, QLabel, QWidget, QVBoxLayout,
)

from client import theme
from client.icons import icon_clipboard

logger = logging.getLogger(__name__)


class ClipboardToggleButton(QToolButton):
    """UI-SPEC C-07 / Surface 7 — per-direction clipboard toggle.

    4 checkboxes:
      Row 1: text + image c2s ("Copy on this Mac -> paste on server")
      Row 3: image c2s (nested under row 1)
      Row 2: text + image s2c ("Copy on server -> paste on this Mac")
      Row 4: image s2c (nested under row 2)

    Row 3 is disabled (grayed) when row 1 is off; row 4 is disabled
    when row 2 is off. Disabling nesting preserves the child's checked
    state so re-enabling row 1 restores the prior image c2s state.
    """

    toggles_changed = Signal(dict)  # {"text_c2s", "text_s2c", "image_c2s", "image_s2c"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setIcon(icon_clipboard())
        self.setPopupMode(QToolButton.InstantPopup)
        self.setCheckable(True)  # so :checked QSS rule applies when popup open / any-off
        self.setAccessibleName("Clipboard direction")
        self.setAccessibleDescription(
            "Toggle which direction clipboard content flows between "
            "this Mac and the remote server."
        )
        self.setToolTip("Clipboard direction toggles")

        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._menu.aboutToShow.connect(lambda: self.setChecked(True))
        self._menu.aboutToHide.connect(self._refresh_checked_state)

        # Header
        self._add_header("Clipboard direction")

        # Row 1: c2s text (parent of row 3's image c2s)
        self._cb_text_c2s = self._add_checkbox(
            "Copy on this Mac → paste on server",
            "Text and images you copy here become available on the remote machine.",
        )
        # Row 3 (indented): image c2s nested under row 1
        self._cb_image_c2s = self._add_checkbox(
            "Include images (client → server)",
            None, indent=True,
        )

        # Row 2: s2c text (parent of row 4's image s2c)
        self._cb_text_s2c = self._add_checkbox(
            "Copy on server → paste on this Mac",
            "Text and images copied on the remote machine become available locally.",
        )
        # Row 4 (indented): image s2c nested under row 2
        self._cb_image_s2c = self._add_checkbox(
            "Include images (server → client)",
            None, indent=True,
        )

        # Footer
        self._add_footer(
            "Changes apply to the next copy. Saved per bookmark."
        )

        # Default state: all four ON (D-16 secure defaults)
        for cb in (self._cb_text_c2s, self._cb_text_s2c,
                   self._cb_image_c2s, self._cb_image_s2c):
            cb.setChecked(True)

        # Wire nested-disable logic
        self._cb_text_c2s.toggled.connect(self._refresh_nested_states)
        self._cb_text_s2c.toggled.connect(self._refresh_nested_states)
        for cb in (self._cb_text_c2s, self._cb_text_s2c,
                   self._cb_image_c2s, self._cb_image_s2c):
            cb.toggled.connect(lambda _checked: self._emit_change())

        self._refresh_nested_states()
        self._refresh_button_tooltip()

    # ---- internals ----

    def _add_header(self, text: str):
        wa = QWidgetAction(self._menu)
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {theme.TEXT_PRIMARY}; "
            f"padding: 8px 16px;"
        )
        wa.setDefaultWidget(lbl)
        self._menu.addAction(wa)
        self._menu.addSeparator()

    def _add_checkbox(self, text: str, secondary: str | None,
                      indent: bool = False) -> QCheckBox:
        wa = QWidgetAction(self._menu)
        container = QWidget()
        v = QVBoxLayout(container)
        left = 32 if indent else 16
        v.setContentsMargins(left, 8, 16, 8)
        v.setSpacing(2)
        cb = QCheckBox(text)
        cb.setStyleSheet(f"font-size: 13px; color: {theme.TEXT_PRIMARY};")
        v.addWidget(cb)
        if secondary:
            sec = QLabel(secondary)
            sec.setStyleSheet(f"font-size: 12px; color: {theme.TEXT_SECONDARY};")
            sec.setWordWrap(True)
            v.addWidget(sec)
        wa.setDefaultWidget(container)
        self._menu.addAction(wa)
        return cb

    def _add_footer(self, text: str):
        self._menu.addSeparator()
        wa = QWidgetAction(self._menu)
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 10px; color: {theme.TEXT_MUTED}; "
            f"font-style: italic; padding: 8px 16px;"
        )
        wa.setDefaultWidget(lbl)
        self._menu.addAction(wa)

    def _refresh_nested_states(self):
        """Row 3/4 enabled iff parent row 1/2 is on. Preserves checked state."""
        text_c2s_on = self._cb_text_c2s.isChecked()
        text_s2c_on = self._cb_text_s2c.isChecked()
        self._cb_image_c2s.setEnabled(text_c2s_on)
        self._cb_image_c2s.setToolTip(
            "" if text_c2s_on
            else 'Turn on "Copy on this Mac → paste on server" first.'
        )
        self._cb_image_s2c.setEnabled(text_s2c_on)
        self._cb_image_s2c.setToolTip(
            "" if text_s2c_on
            else 'Turn on "Copy on server → paste on this Mac" first.'
        )

    def _emit_change(self):
        self._refresh_button_tooltip()
        self.toggles_changed.emit({
            "text_c2s": self._cb_text_c2s.isChecked(),
            "text_s2c": self._cb_text_s2c.isChecked(),
            "image_c2s": self._cb_image_c2s.isChecked(),
            "image_s2c": self._cb_image_s2c.isChecked(),
        })

    def _refresh_checked_state(self):
        """Button :checked when any toggle off OR popup open."""
        any_off = not all((
            self._cb_text_c2s.isChecked(), self._cb_text_s2c.isChecked(),
            self._cb_image_c2s.isChecked(), self._cb_image_s2c.isChecked(),
        ))
        self.setChecked(any_off)

    def _refresh_button_tooltip(self):
        t1 = self._cb_text_c2s.isChecked()
        t2 = self._cb_text_s2c.isChecked()
        i1 = self._cb_image_c2s.isChecked()
        i2 = self._cb_image_s2c.isChecked()
        if all((t1, t2, i1, i2)):
            self.setToolTip("Clipboard: all directions on")
        elif not any((t1, t2, i1, i2)):
            self.setToolTip("Clipboard: disabled")
        else:
            self.setToolTip(
                f"Clipboard: text-c2s {'on' if t1 else 'off'}, "
                f"text-s2c {'on' if t2 else 'off'}, "
                f"image-c2s {'on' if i1 else 'off'}, "
                f"image-s2c {'on' if i2 else 'off'}"
            )

    # ---- public accessors ----

    def set_state(self, text_c2s: bool, text_s2c: bool,
                  image_c2s: bool, image_s2c: bool):
        """Pre-fill from bookmark without emitting signals."""
        for cb, val in (
            (self._cb_text_c2s, text_c2s),
            (self._cb_text_s2c, text_s2c),
            (self._cb_image_c2s, image_c2s),
            (self._cb_image_s2c, image_s2c),
        ):
            cb.blockSignals(True)
            cb.setChecked(val)
            cb.blockSignals(False)
        self._refresh_nested_states()
        self._refresh_checked_state()
        self._refresh_button_tooltip()

    def current_state(self) -> dict:
        """Snapshot of current toggle state."""
        return {
            "text_c2s": self._cb_text_c2s.isChecked(),
            "text_s2c": self._cb_text_s2c.isChecked(),
            "image_c2s": self._cb_image_c2s.isChecked(),
            "image_s2c": self._cb_image_s2c.isChecked(),
        }
