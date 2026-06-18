"""Phase 3 D-09 / Plan 03-05 — RemapBanner (UI-SPEC Surface 5).

Non-modal topology-change banner that appears at the top of the viewer
when the server broadcasts a MonitorListMsg reporting that the monitor
layout changed. 4 cases per UI-SPEC:
  - pick_missing  (pick_one + bookmarked monitor vanished)
  - mirror_add    (mirror_all + monitor added)
  - mirror_remove (mirror_all + monitor removed)
  - single_change (single-monitor session, any change)

Only the pick_missing case renders the "Choose monitor →" action link.
Banner is sticky (no auto-timeout); Esc dismisses; Enter activates the
action link (when visible).
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def banner_parent(qapp):
    from PySide6.QtWidgets import QWidget
    parent = QWidget()
    parent.resize(1200, 800)
    yield parent
    parent.deleteLater()


def test_remap_banner_mounts_inside_viewer_at_48_px_tall(qapp, banner_parent):
    """UI-SPEC Surface 5 — banner is 48 px tall, parented to viewer."""
    from client.remap_banner import RemapBanner
    banner = RemapBanner(banner_parent)
    assert banner.parentWidget() is banner_parent
    assert banner.height() == 48


def test_remap_banner_pick_missing_renders_action_link(qapp, banner_parent):
    """UI-SPEC Surface 5 — pick_missing case renders title + verbatim
    body + action link.

    Uses isHidden() (not isVisible()) per the Plan 02 Rule 1 convention —
    Qt's effective visibility cascades from the parent chain which is
    False in headless tests even when the widget was never hidden
    explicitly. isHidden() reflects the explicit setVisible(False) call.
    """
    from client.remap_banner import RemapBanner
    banner = RemapBanner(banner_parent)
    banner.show_for_case("pick_missing", picked_name="DP-1")
    assert banner._title.text() == "Monitor layout changed"
    assert banner._body.text() == (
        "Showing primary monitor. Click to choose a different one."
    )
    # Action link only visible in pick_missing case. isHidden() is False
    # because show_for_case() called setVisible(True) on it.
    assert banner._action.isHidden() is False
    assert banner._action.text() == "Choose monitor →"


def test_remap_banner_mirror_add_body(qapp, banner_parent):
    """UI-SPEC Surface 5 — mirror_add body verbatim, no action link."""
    from client.remap_banner import RemapBanner
    banner = RemapBanner(banner_parent)
    banner.show_for_case("mirror_add")
    assert banner._body.text() == "A new monitor is now visible."
    # Action link hidden — setVisible(False) was called.
    assert banner._action.isHidden() is True


def test_remap_banner_mirror_remove_body(qapp, banner_parent):
    """UI-SPEC Surface 5 — mirror_remove body verbatim, no action link."""
    from client.remap_banner import RemapBanner
    banner = RemapBanner(banner_parent)
    banner.show_for_case("mirror_remove")
    assert banner._body.text() == (
        "A monitor was removed. Continuing with the rest."
    )
    assert banner._action.isHidden() is True


def test_remap_banner_single_change_body(qapp, banner_parent):
    """UI-SPEC Surface 5 — single_change body verbatim, no action link."""
    from client.remap_banner import RemapBanner
    banner = RemapBanner(banner_parent)
    banner.show_for_case("single_change")
    assert banner._body.text() == (
        "Monitor layout changed. Continuing on primary."
    )
    assert banner._action.isHidden() is True


def test_remap_banner_sticky_no_auto_timeout(qapp, banner_parent):
    """UI-SPEC Surface 5 — banner has NO auto-timeout. We verify this
    by showing the banner and confirming no QTimer is running on it
    that would dismiss it."""
    from client.remap_banner import RemapBanner
    banner = RemapBanner(banner_parent)
    banner.show_for_case("pick_missing", picked_name="DP-1")
    # No QTimer child with singleShot+active firing auto-dismiss.
    # The only animation-related attribute is _anim (QPropertyAnimation).
    # If an auto-dismiss timer existed, it'd be a QTimer on self.
    from PySide6.QtCore import QTimer
    for child in banner.findChildren(QTimer):
        if child.isActive() and child.isSingleShot():
            # Fail loudly if any active singleshot timer exists
            assert False, (
                "UI-SPEC Surface 5 forbids auto-dismiss QTimer on RemapBanner"
            )


def test_remap_banner_action_emits_remap_requested(qapp, banner_parent):
    """UI-SPEC Surface 5 — clicking the action link emits remap_requested."""
    from client.remap_banner import RemapBanner
    banner = RemapBanner(banner_parent)
    banner.show_for_case("pick_missing", picked_name="DP-1")
    fired = []
    banner.remap_requested.connect(lambda: fired.append(True))
    banner._action.click()
    assert fired == [True]


def test_remap_banner_no_inline_hex_colors():
    """UI-SPEC color contract — zero inline hex colors; only theme.*"""
    import pathlib
    src = pathlib.Path(
        __file__,
    ).resolve().parents[2] / "client" / "remap_banner.py"
    text = src.read_text()
    # Strip comment lines before checking (docstrings + comments may
    # legitimately reference hex values for UI-SPEC cross-referencing).
    # Also allow 3-digit rgb(...) per theme.py pattern.
    import re
    # Match a 6-digit hex literal used as a style color in code (not comments).
    code_lines = [
        line for line in text.splitlines()
        if not line.lstrip().startswith("#") and '"""' not in line
    ]
    joined = "\n".join(code_lines)
    hex_matches = re.findall(r'"#[0-9a-fA-F]{6}"', joined)
    assert hex_matches == [], (
        f"UI-SPEC forbids inline hex colors in remap_banner.py; "
        f"found: {hex_matches}"
    )
