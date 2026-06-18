"""Phase 3 D-02 / D-09 — capture-mode plumbing on SessionRuntime.

Plan 03-03 Task 1. Exercises the server-side contract:

  * ClientHelloMsg.capture_mode + picked_monitor_id/name arrive in
    ``handle_input(MsgType.CLIENT_HELLO)``; server calls
    ``apply_capture_mode`` which sets ``session.crop_rect`` from the
    current ``capture.list_monitors()``.
  * pick_one with an unknown id falls back to primary + flags
    ``capture_mode_degraded`` (Plan 05 wires the toast on top).

Direct in-process test — no websocket loopback. SessionRuntime surface
is called directly; capture is swapped for a stub that returns the DXS
flame-lab 2×2560×1600 topology.
"""
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from common.messages import MonitorInfo, MsgType


@dataclass
class _FakeMonitor:
    id: int
    name: str
    width: int
    height: int
    x: int
    y: int
    primary: bool
    scale: float = 1.0


def _build_runtime():
    """Build a SessionRuntime bypassing __init__ (no Xvfb, no encoder)."""
    from server.session_runtime import SessionRuntime
    rt = SessionRuntime.__new__(SessionRuntime)
    # Minimal state needed by apply_capture_mode.
    rt.username = "test"
    rt.clients = {}
    import logging
    rt._logger = logging.getLogger("test-runtime")

    # Swap capture for a fake that returns the DXS flame-lab topology.
    capture = MagicMock()
    capture.list_monitors.return_value = [
        MonitorInfo(id=0, name="All Monitors (Virtual Desktop)", width=5120,
                    height=1600, x=0, y=0, primary=False, scale=1.0),
        MonitorInfo(id=1, name="DP-0", width=2560, height=1600, x=0, y=0,
                    primary=True, scale=1.0),
        MonitorInfo(id=2, name="DP-1", width=2560, height=1600, x=2560,
                    y=0, primary=False, scale=1.0),
    ]
    rt.capture = capture
    rt.encoder = None
    return rt


class _FakeSession:
    """Stand-in for ClientSession with just the per-session crop fields."""
    def __init__(self):
        self.capture_mode = "mirror_all"
        self.picked_monitor_id = -1
        self.picked_monitor_name = ""
        self.crop_rect = None
        self.capture_mode_degraded = False


def test_mirror_all_sets_no_crop():
    """D-02 — capture_mode=mirror_all → crop_rect is None (full virtual desktop)."""
    rt = _build_runtime()
    s = _FakeSession()
    ok = rt.apply_capture_mode(s, mode="mirror_all", picked_id=-1,
                                picked_name="")
    assert ok is True
    assert s.crop_rect is None
    assert s.capture_mode == "mirror_all"
    assert s.capture_mode_degraded is False


def test_single_crops_to_primary():
    """D-02 — capture_mode=single → crop_rect is the primary monitor's geometry."""
    rt = _build_runtime()
    s = _FakeSession()
    ok = rt.apply_capture_mode(s, mode="single", picked_id=-1, picked_name="")
    assert ok is True
    # Primary is monitor id=1 at (0, 0, 2560, 1600).
    assert s.crop_rect == (0, 0, 2560, 1600)
    assert s.picked_monitor_id == 1
    assert s.capture_mode_degraded is False


def test_pick_one_crops_to_selected_monitor():
    """D-02 — pick_one with valid id → crop_rect is the picked monitor's geometry."""
    rt = _build_runtime()
    s = _FakeSession()
    ok = rt.apply_capture_mode(s, mode="pick_one", picked_id=2,
                                picked_name="DP-1")
    assert ok is True
    # Monitor id=2 is at (2560, 0, 2560, 1600).
    assert s.crop_rect == (2560, 0, 2560, 1600)
    assert s.picked_monitor_id == 2
    assert s.picked_monitor_name == "DP-1"
    assert s.capture_mode_degraded is False


def test_pick_one_fallback_to_primary_on_invalid_id():
    """D-09 — pick_one with unknown id → fall back to primary + degraded=True."""
    rt = _build_runtime()
    s = _FakeSession()
    ok = rt.apply_capture_mode(s, mode="pick_one", picked_id=999,
                                picked_name="Missing Monitor")
    # Return value reflects degraded status (fell back).
    assert ok is False
    # Fell back to primary (id=1, geometry 0,0,2560,1600).
    assert s.crop_rect == (0, 0, 2560, 1600)
    assert s.picked_monitor_id == 1
    assert s.capture_mode_degraded is True


def test_pick_one_name_fallback_if_id_missing():
    """D-04 — id wins, but name is the fallback when id is -1."""
    rt = _build_runtime()
    s = _FakeSession()
    ok = rt.apply_capture_mode(s, mode="pick_one", picked_id=-1,
                                picked_name="DP-1")
    assert ok is True
    assert s.crop_rect == (2560, 0, 2560, 1600)
    assert s.picked_monitor_name == "DP-1"


def test_invalid_mode_falls_back_to_mirror_all():
    """T-03-09 mitigation — unknown mode string is rejected + reset to mirror_all."""
    rt = _build_runtime()
    s = _FakeSession()
    rt.apply_capture_mode(s, mode="totally_bogus", picked_id=-1, picked_name="")
    assert s.capture_mode == "mirror_all"
    assert s.crop_rect is None
