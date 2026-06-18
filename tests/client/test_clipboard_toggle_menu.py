"""Phase 3 D-15 / C-07 — ClipboardToggleButton QMenu signal emission.

Plan 03-07 Wave 5 — flips the Wave 0 RED skeletons (Plan 03-01 Task 2)
GREEN. Implementation lives in ``client/clipboard_toggle_menu.py``.

UI contract: Surface 7 in ``03-UI-SPEC.md`` (accessible name
``Clipboard direction``; rows 3/4 nested under 1/2 with disabled-state
tooltips).
"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from client.clipboard_toggle_menu import ClipboardToggleButton  # noqa: E402


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_clipboard_toggle_button_emits_dict_on_change(qapp):
    """D-15 — ClipboardToggleButton emits a 4-key dict on any toggle change."""
    btn = ClipboardToggleButton()
    captured = []
    btn.toggles_changed.connect(lambda d: captured.append(d))

    # Toggle row 1 off
    btn._cb_text_c2s.setChecked(False)

    assert len(captured) >= 1
    last = captured[-1]
    assert set(last.keys()) == {"text_c2s", "text_s2c", "image_c2s", "image_s2c"}
    assert last["text_c2s"] is False
    assert last["text_s2c"] is True
    assert last["image_s2c"] is True


def test_clipboard_toggle_defaults_all_on(qapp):
    """D-15 / D-16 — default state has all 4 directions ON (secure default)."""
    btn = ClipboardToggleButton()
    state = btn.current_state()
    assert state == {
        "text_c2s": True,
        "text_s2c": True,
        "image_c2s": True,
        "image_s2c": True,
    }


def test_clipboard_toggle_row_3_disabled_when_row_1_off(qapp):
    """Surface 7 — unchecking row 1 (text c2s) disables row 3 (image c2s),
    preserving row 3's checked state, and the disabled-state tooltip is set."""
    btn = ClipboardToggleButton()
    # Precondition
    assert btn._cb_image_c2s.isEnabled() is True

    btn._cb_text_c2s.setChecked(False)

    assert btn._cb_image_c2s.isEnabled() is False
    # Checked state preserved (not cleared)
    assert btn._cb_image_c2s.isChecked() is True
    # Disabled-state tooltip verbatim per UI-SPEC Surface 7
    assert (
        'Turn on "Copy on this Mac → paste on server" first.'
        in btn._cb_image_c2s.toolTip()
    )

    # Re-enabling row 1 clears the disabled-state tooltip
    btn._cb_text_c2s.setChecked(True)
    assert btn._cb_image_c2s.isEnabled() is True
    assert btn._cb_image_c2s.isChecked() is True
    assert btn._cb_image_c2s.toolTip() == ""


def test_clipboard_toggle_row_4_disabled_when_row_2_off(qapp):
    """Surface 7 — unchecking row 2 (text s2c) disables row 4 (image s2c)
    and shows the nested-disable tooltip string verbatim."""
    btn = ClipboardToggleButton()
    assert btn._cb_image_s2c.isEnabled() is True

    btn._cb_text_s2c.setChecked(False)

    assert btn._cb_image_s2c.isEnabled() is False
    assert btn._cb_image_s2c.isChecked() is True  # state preserved
    assert (
        'Turn on "Copy on server → paste on this Mac" first.'
        in btn._cb_image_s2c.toolTip()
    )


def test_clipboard_toggle_set_state_does_not_emit(qapp):
    """set_state (bookmark pre-fill) must not emit toggles_changed."""
    btn = ClipboardToggleButton()
    captured = []
    btn.toggles_changed.connect(lambda d: captured.append(d))

    btn.set_state(
        text_c2s=False, text_s2c=True,
        image_c2s=True, image_s2c=False,
    )

    assert captured == []
    state = btn.current_state()
    assert state["text_c2s"] is False
    assert state["text_s2c"] is True
    assert state["image_c2s"] is True
    assert state["image_s2c"] is False
    # Row 3 disabled because row 1 is off
    assert btn._cb_image_c2s.isEnabled() is False
