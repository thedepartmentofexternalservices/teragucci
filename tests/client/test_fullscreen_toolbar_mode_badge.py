"""Phase 3 D-01 / D-03 / D-09 — fullscreen-toolbar mode badge.

Plan 03-02 Task 2 implements ``_ModeBadge`` as a QFrame in
``client/fullscreen_toolbar.py`` plus an ``update_capture_mode(mode,
picked_name, degraded)`` method on ``FullscreenToolbar`` that forwards to
it. Session calls the method on connect + disconnect + degraded-fallback
events.

UI contract: Surface 2 in ``03-UI-SPEC.md``. Badge template is verbatim:
  - "Mode: single"
  - "Mode: mirror"
  - "Mode: pick: {monitor_name}"
  - "Mode: pick → primary" (degraded; WARNING-colored underline)

Tooltip copy (Surface 2):
  - "Monitor mode is fixed for this session. Disconnect and reconnect
    to change."
  - Degraded: "{monitor_name} disappeared. Showing primary monitor
    instead."
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


def test_mode_badge_renders_live_session_template(qapp):
    """D-01 — badge text matches Surface 2 verbatim copy per mode."""
    from client.fullscreen_toolbar import FullscreenToolbar
    tb = FullscreenToolbar()

    tb.update_capture_mode("single")
    assert tb.mode_badge._label.text() == "Mode: single"
    # Intrinsic visibility (isHidden() is the inverse — True only when
    # an explicit hide() was applied; unaffected by parent window state).
    assert tb.mode_badge.isHidden() is False

    tb.update_capture_mode("mirror_all")
    assert tb.mode_badge._label.text() == "Mode: mirror"

    tb.update_capture_mode("pick_one", picked_name="DP-1")
    assert tb.mode_badge._label.text() == "Mode: pick: DP-1"


def test_mode_badge_tooltip_matches_grayed_copy(qapp):
    """D-03 / Surface 2 — tooltip renders the fixed-for-session copy."""
    from client.fullscreen_toolbar import FullscreenToolbar
    tb = FullscreenToolbar()
    tb.update_capture_mode("mirror_all")
    tip = tb.mode_badge.toolTip()
    assert tip == (
        "Monitor mode is fixed for this session. "
        "Disconnect and reconnect to change."
    )


def test_mode_badge_degraded_state_uses_warning_underline(qapp):
    """D-09 — degraded badge shows WARNING underline + 'pick → primary'."""
    from client.fullscreen_toolbar import FullscreenToolbar
    tb = FullscreenToolbar()
    tb.update_capture_mode("pick_one", picked_name="DP-1", degraded=True)
    assert tb.mode_badge._label.text() == "Mode: pick → primary"
    assert tb.mode_badge._degraded is True
    # Tooltip per Surface 2 degraded row
    tip = tb.mode_badge.toolTip()
    assert tip == "DP-1 disappeared. Showing primary monitor instead."


def test_mode_badge_hides_when_cleared(qapp):
    """Clearing the mode (empty string) hides the badge — used on disconnect."""
    from client.fullscreen_toolbar import FullscreenToolbar
    tb = FullscreenToolbar()
    tb.update_capture_mode("single")
    # Intrinsic visibility — parent may be hidden but widget is not
    # explicitly set-hidden while an active mode is advertised.
    assert tb.mode_badge.isHidden() is False
    tb.update_capture_mode("")
    assert tb.mode_badge.isHidden() is True


def test_protocol_set_capture_mode_plumbs_to_client_hello(qapp):
    """D-02 — set_capture_mode() mutates the fields baked into ClientHelloMsg.

    The setter stores the three values on the protocol; the ClientHelloMsg
    construction site reads them when assembling the hello payload. We
    assert the internal state + that the constructor consumes them.
    """
    from client.protocol import ClientProtocol
    from common.messages import ClientHelloMsg
    p = ClientProtocol()
    p.set_capture_mode("pick_one", 1, "DP-1")
    assert p._capture_mode == "pick_one"
    assert p._picked_monitor_id == 1
    assert p._picked_monitor_name == "DP-1"
    # Emulate the hello-construction path with the stored values.
    hello = ClientHelloMsg(
        capture_mode=p._capture_mode,
        picked_monitor_id=p._picked_monitor_id,
        picked_monitor_name=p._picked_monitor_name,
    )
    import json
    payload = json.loads(hello.to_json())
    assert payload["capture_mode"] == "pick_one"
    assert payload["picked_monitor_id"] == 1
    assert payload["picked_monitor_name"] == "DP-1"


def test_protocol_set_capture_mode_rejects_invalid_mode(qapp):
    """T-03-07 — whitelist check rejects unknown modes on the client side."""
    from client.protocol import ClientProtocol
    p = ClientProtocol()
    p.set_capture_mode("rce", 99, "bogus")
    assert p._capture_mode == "mirror_all"


def test_session_connect_pushes_capture_mode_before_handshake(qapp, monkeypatch):
    """D-01 / D-02 — session.connect() calls protocol.set_capture_mode
    with the profile's mode BEFORE the websocket connect fires.

    We monkeypatch protocol.connect (starts a thread) and record the
    call ordering via a list. set_capture_mode must appear before
    protocol.connect in the recorded order.
    """
    from client.session import Session
    s = Session()

    calls: list[str] = []

    def _record_sccm(mode, pid, pname):
        calls.append(f"set_capture_mode({mode},{pid},{pname!r})")

    def _record_connect(*a, **kw):
        calls.append("protocol.connect")

    def _record_disconnect():
        calls.append("protocol.disconnect")

    monkeypatch.setattr(s.protocol, "set_capture_mode", _record_sccm)
    monkeypatch.setattr(s.protocol, "connect", _record_connect)
    monkeypatch.setattr(s.protocol, "disconnect", _record_disconnect)
    # Swap setter is Phase 2 path; stub it so it doesn't alter order.
    monkeypatch.setattr(s.protocol, "set_swap_cmd_ctrl", lambda *a, **k: None)

    from common.messages import ConnectionProfile
    profile = ConnectionProfile(
        host="rocky", port=443, username="randy",
        monitor_mode="pick_one",
        picked_monitor_id=2,
        picked_monitor_name="DP-1",
    )
    s.connect_with_profile(profile, password="hunter2")

    # set_capture_mode appears before protocol.connect in the recorded order.
    idx_sccm = next(i for i, c in enumerate(calls) if c.startswith("set_capture_mode"))
    idx_conn = calls.index("protocol.connect")
    assert idx_sccm < idx_conn, f"set_capture_mode must precede connect; calls={calls!r}"
    # And the values on the wire match the profile.
    assert "pick_one" in calls[idx_sccm]
    assert "DP-1" in calls[idx_sccm]
