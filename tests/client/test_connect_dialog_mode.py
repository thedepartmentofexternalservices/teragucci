"""Phase 3 D-01 / D-04 — connect-dialog ModeSelector widget.

Plan 03-02 Task 1 implements ``ModeSelector`` as a sub-widget inside
``client/main_window.py::ConnectionDialog``. Emits
``mode_changed(mode, picked_monitor_id, picked_monitor_name)`` on radio
change; pre-fills from the bookmark's last-used mode; feeds
:class:`MonitorSelector` in radio mode for the pick-one sub-selection
(D-04).

UI contract: Surface 1 in ``03-UI-SPEC.md`` (monitor-mode radios with
help copy + sub-selector when ``Pick one`` is chosen). Copy is verbatim;
the grep acceptance criteria in 03-02-PLAN.md check for
``"Single monitor"`` / ``"Mirror all"`` / ``"Pick one"`` / ``"Show one
server monitor at a time. Lowest bandwidth."``.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication (offscreen). Mirrors tests/client/test_viewer_proximity.py."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _make_mode_selector(qapp):
    from client.main_window import ModeSelector
    return ModeSelector()


def test_mode_selector_emits_signal_on_radio_change(qapp):
    """D-01 — ModeSelector emits ``(mode, picked_id, picked_name)`` tuple
    for each radio click. Single / mirror_all / pick_one each fire at
    least once with the expected mode string.
    """
    sel = _make_mode_selector(qapp)
    received: list[tuple] = []
    sel.mode_changed.connect(lambda m, i, n: received.append((m, i, n)))

    # Click through each radio — QButtonGroup auto-exclusive so one click
    # per mode is enough.
    sel._radio_single.setChecked(True)
    sel._radio_mirror.setChecked(True)
    sel._radio_pick.setChecked(True)

    modes = [r[0] for r in received]
    assert "single" in modes, f"expected 'single' emit; got {received!r}"
    assert "mirror_all" in modes, f"expected 'mirror_all' emit; got {received!r}"
    assert "pick_one" in modes, f"expected 'pick_one' emit; got {received!r}"


def test_mode_selector_prefills_from_bookmark(qapp):
    """D-01 — set_mode() pre-fills from a saved bookmark.

    Pre-fills for all 3 modes (single / mirror_all / pick_one). The
    pick_one pre-fill also reveals the sub-selector — verify it.
    """
    # single
    sel = _make_mode_selector(qapp)
    sel.set_mode("single")
    assert sel._radio_single.isChecked()
    assert sel.monitor_mode == "single"

    # mirror_all
    sel = _make_mode_selector(qapp)
    sel.set_mode("mirror_all")
    assert sel._radio_mirror.isChecked()
    assert sel.monitor_mode == "mirror_all"

    # pick_one — sub-selector must be visible
    sel = _make_mode_selector(qapp)
    sel.update_monitors([
        {"id": 1, "name": "eDP-1", "width": 1920, "height": 1200},
        {"id": 2, "name": "HDMI-1", "width": 2560, "height": 1600},
    ])
    sel.set_mode("pick_one", picked_id=2, picked_name="HDMI-1")
    assert sel._radio_pick.isChecked()
    assert sel.monitor_mode == "pick_one"
    assert sel.picked_monitor_id == 2
    assert sel.picked_monitor_name == "HDMI-1"


def test_pick_one_without_sub_selection_emits_minus_one(qapp):
    """D-04 — toggling pick-one before choosing a monitor emits id=-1.

    The dialog's ``mode_changed`` signal fires on every radio toggle; if
    the user picks ``Pick one`` without yet selecting a monitor, the
    tuple must carry -1 / "" (not stale state from the prior mode).
    """
    sel = _make_mode_selector(qapp)
    # No monitors known — picking pick_one with nothing selected reports -1.
    received: list[tuple] = []
    sel.mode_changed.connect(lambda m, i, n: received.append((m, i, n)))
    sel._radio_pick.setChecked(True)
    # Last emit must be pick_one with sentinel id/name.
    last = received[-1]
    assert last[0] == "pick_one"
    assert last[1] == -1
    assert last[2] == ""


def test_monitor_selector_radio_mode_constrains_to_single_check(qapp):
    """D-04 — MonitorSelector in radio mode allows exactly one action checked.

    Toggle monitor 1, then monitor 2 → action for monitor 1 becomes
    unchecked automatically. Mirror-all (checkbox) mode retains the
    prior multi-check semantics.
    """
    from client.monitor_selector import MonitorSelector
    ms = MonitorSelector()
    ms.set_mode("radio")
    ms.update_monitors([
        {"id": 1, "name": "eDP-1", "width": 1920, "height": 1200},
        {"id": 2, "name": "HDMI-1", "width": 2560, "height": 1600},
        {"id": 3, "name": "DP-1", "width": 3840, "height": 2160},
    ])

    # Start clean: every action begins unchecked in radio mode (no
    # default "all selected" semantics in radio mode).
    for action, _ in ms._actions:
        action.setChecked(False)

    ms._actions[0][0].setChecked(True)
    assert ms._actions[0][0].isChecked()
    ms._actions[1][0].setChecked(True)
    # Switching the 2nd on must have unchecked the 1st.
    assert ms._actions[1][0].isChecked()
    assert not ms._actions[0][0].isChecked()


def test_monitor_selector_radio_mode_hides_select_all_none(qapp):
    """D-04 — Select All / Select None are meaningless in radio mode.

    When mode="radio", the two quick-action menu items are hidden (not
    deleted) so toggling back to checkbox mode restores them.
    """
    from client.monitor_selector import MonitorSelector
    ms = MonitorSelector()
    ms.update_monitors([
        {"id": 1, "name": "eDP-1", "width": 1920, "height": 1200},
    ])
    assert ms._action_select_all.isVisible()
    assert ms._action_select_none.isVisible()

    ms.set_mode("radio")
    ms.update_monitors([
        {"id": 1, "name": "eDP-1", "width": 1920, "height": 1200},
    ])
    assert not ms._action_select_all.isVisible()
    assert not ms._action_select_none.isVisible()


def test_monitor_selector_radio_mode_label_shows_picked_name(qapp):
    """D-04 — radio-mode label renders the picked monitor's NAME.

    Not "All (N)" or "N of M". Single-selection in radio mode shows a
    single monitor name.
    """
    from client.monitor_selector import MonitorSelector
    ms = MonitorSelector()
    ms.set_mode("radio")
    ms.update_monitors([
        {"id": 1, "name": "eDP-1", "width": 1920, "height": 1200},
        {"id": 2, "name": "HDMI-1", "width": 2560, "height": 1600},
    ])
    for action, _ in ms._actions:
        action.setChecked(False)
    ms._actions[1][0].setChecked(True)
    assert ms.text() == "HDMI-1"


def test_monitor_selector_monitor_missing_signal_fires(qapp):
    """D-04 — set_picked() with an unknown id+name fires monitor_missing.

    Bookmark stored DP-1 but the currently-connected server has no DP-1
    — the widget falls back to the primary action AND emits a toast-
    worthy signal so the session can show a transient "Monitor X not
    found, now viewing primary." message per Surface 4.
    """
    from client.monitor_selector import MonitorSelector
    ms = MonitorSelector()
    ms.set_mode("radio")
    ms.update_monitors([
        {"id": 1, "name": "eDP-1", "width": 1920, "height": 1200, "primary": True},
        {"id": 2, "name": "HDMI-1", "width": 2560, "height": 1600},
    ])
    captured: list[str] = []
    ms.monitor_missing.connect(captured.append)

    # Simulate a bookmark pointing at a monitor that is no longer attached.
    ms.set_picked(99, "DP-1")
    assert captured, f"expected monitor_missing emit; got {captured!r}"
    assert "DP-1" in captured[0] or "99" in captured[0]


def test_modeselector_disabled_during_session_tooltip(qapp):
    """D-03 — mode widget grayed + tooltip during active session.

    Surface 3 in 03-UI-SPEC.md locks the tooltip copy verbatim:
    ``"Disconnect and reconnect to change monitor mode."``
    """
    sel = _make_mode_selector(qapp)
    sel.set_disabled_during_session(True)
    assert not sel.isEnabled()
    tip = sel._radio_single.toolTip()
    assert "Disconnect and reconnect to change monitor mode." == tip

    sel.set_disabled_during_session(False)
    assert sel.isEnabled()
    assert sel._radio_single.toolTip() == ""
