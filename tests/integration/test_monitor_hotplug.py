"""Phase 3 D-09 / D-12 — in-process loopback monitor-hotplug integration.

Plan 03-05 implementation. Exercises the full server→client path by
driving MonitorHotplug.run() against a fake runtime + spying on broadcast
enqueues. The FSM-survival assertion uses a minimal in-memory harness
(no real WebSocket) because the wire contract + degradation payload are
the load-bearing pieces for Plan 03-05; the full websocket loopback is
already covered by tests/integration/test_reconnect.py.

Covers two sub-scenarios:
1. Mid-session hotplug triggers MonitorListMsg broadcast; the broadcast
   carries the new monitor list and FSM stays streaming (no fallback).
2. Pick-one + picked monitor vanishes → server auto-fallback to primary
   + MonitorListMsg.degradations carries the fallback event keyed by the
   session's client_id.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from common.messages import MonitorInfo
from server.monitor_hotplug import MonitorHotplug


def _make_fake_runtime(*, monitors_now, pending=True, encoder=True):
    """Spin up a types.SimpleNamespace runtime sufficient for
    MonitorHotplug.run() to iterate once."""

    class _FakeCapture:
        def __init__(self, mons):
            self._hotplug_pending = pending
            self._mons = list(mons)

        def detect_hotplug(self) -> bool:
            # When pending is True the hot-plug loop takes the fast
            # path via _hotplug_pending and never calls detect_hotplug;
            # keep this stubbed True for belt + suspenders.
            return True

        def list_monitors(self):
            return list(self._mons)

    class _FakeEncoderLifecycle:
        def __init__(self):
            self.restart_calls = 0

        def restart(self):
            self.restart_calls += 1

    runtime = types.SimpleNamespace(
        capture=_FakeCapture(monitors_now),
        encoder=object() if encoder else None,
        encoder_lifecycle=_FakeEncoderLifecycle(),
        clients={},
    )

    def apply_capture_mode(session, mode, picked_id, picked_name):
        current = runtime.capture.list_monitors()
        if mode == "mirror_all":
            session.capture_mode = mode
            session.capture_mode_degraded = False
            return True
        match = next((m for m in current if m.id == picked_id), None)
        if match is None:
            primary = next((m for m in current if m.primary), current[0])
            session.capture_mode = mode
            session.picked_monitor_id = primary.id
            session.picked_monitor_name = primary.name
            session.capture_mode_degraded = True
            return False
        session.capture_mode = mode
        session.picked_monitor_id = match.id
        session.picked_monitor_name = match.name
        session.capture_mode_degraded = False
        return True

    runtime.apply_capture_mode = apply_capture_mode
    return runtime


class _FakeClientSession:
    """Mimics server.client_session.ClientSession for the fields that
    MonitorHotplug.run() reads — no websocket needed."""

    def __init__(self, *, cid, mode, picked_id, picked_name):
        self.client_id = cid
        self.authenticated = True
        self.capture_mode = mode
        self.picked_monitor_id = picked_id
        self.picked_monitor_name = picked_name
        self.crop_rect = None
        self.capture_mode_degraded = False
        self.fsm_state = "streaming"   # stays streaming across hotplug
        self.enqueued: list = []

    async def enqueue(self, msg):
        self.enqueued.append(msg)


async def _drive_one_hotplug_pass(runtime):
    """Run MonitorHotplug.run() long enough for exactly one iteration
    (the 1s asyncio.sleep + the iteration body)."""
    hotplug = MonitorHotplug(runtime)
    hotplug._running = True
    task = asyncio.ensure_future(hotplug.run())
    await asyncio.sleep(1.2)
    hotplug._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return hotplug


@pytest.mark.asyncio
async def test_mid_session_hotplug_session_survives():
    """D-09 — server fires hotplug → client MonitorListMsg received;
    mirror_all session keeps streaming with empty degradations list."""
    monitors_now = [
        MonitorInfo(id=1, name="DP-1", width=2560, height=1440,
                    x=0, y=0, primary=True),
        MonitorInfo(id=2, name="DP-2", width=2560, height=1440,
                    x=2560, y=0, primary=False),
    ]
    runtime = _make_fake_runtime(monitors_now=monitors_now)
    cs = _FakeClientSession(
        cid="sid-mirror", mode="mirror_all", picked_id=-1, picked_name="",
    )
    runtime.clients[object()] = cs

    await _drive_one_hotplug_pass(runtime)

    assert len(cs.enqueued) == 1
    broadcast = json.loads(cs.enqueued[0])
    assert broadcast["type"] == "monitor_list"
    assert len(broadcast["monitors"]) == 2
    assert broadcast["degradations"] == []
    # FSM surrogate stays streaming — no degrade event fired.
    assert cs.fsm_state == "streaming"
    assert cs.capture_mode_degraded is False
    # Encoder got restarted (D-09 keeps the geometry fresh).
    assert runtime.encoder_lifecycle.restart_calls == 1


@pytest.mark.asyncio
async def test_pick_vanish_fallback():
    """D-09 — picked monitor vanishes → server auto-fallback to primary,
    MonitorListMsg.degradations carries the fallback event, session
    continues (no fsm exception)."""
    # Topology after hotplug — monitor id=2 (DP-2) disappeared.
    monitors_now = [
        MonitorInfo(id=1, name="DP-1", width=2560, height=1440,
                    x=0, y=0, primary=True),
    ]
    runtime = _make_fake_runtime(monitors_now=monitors_now)
    cs = _FakeClientSession(
        cid="sid-pick", mode="pick_one", picked_id=2, picked_name="DP-2",
    )
    runtime.clients[object()] = cs

    await _drive_one_hotplug_pass(runtime)

    assert len(cs.enqueued) == 1
    broadcast = json.loads(cs.enqueued[0])
    assert broadcast["type"] == "monitor_list"
    assert len(broadcast["monitors"]) == 1
    degradations = broadcast.get("degradations", [])
    assert len(degradations) == 1
    event = degradations[0]
    assert event["previous_pick"] == "DP-2"
    assert event["now_showing"] == "DP-1"
    assert event["client_token"] == "sid-pick"
    # previous_pick != now_showing (the whole point of the entry)
    assert event["previous_pick"] != event["now_showing"]
    # Session survived: capture_mode stays set + degraded flag flipped
    assert cs.capture_mode == "pick_one"
    assert cs.capture_mode_degraded is True
    assert cs.fsm_state == "streaming"


@pytest.mark.asyncio
async def test_hotplug_no_degradation_when_picked_monitor_still_present():
    """pick_one session whose picked monitor DIDN'T vanish → no
    degradation entry, no degraded flag flip (D-09 scope boundary)."""
    monitors_now = [
        MonitorInfo(id=1, name="DP-1", width=2560, height=1440,
                    x=0, y=0, primary=True),
        MonitorInfo(id=2, name="DP-2", width=2560, height=1440,
                    x=2560, y=0, primary=False),
    ]
    runtime = _make_fake_runtime(monitors_now=monitors_now)
    cs = _FakeClientSession(
        cid="sid-stable", mode="pick_one", picked_id=2, picked_name="DP-2",
    )
    runtime.clients[object()] = cs

    await _drive_one_hotplug_pass(runtime)

    assert len(cs.enqueued) == 1
    broadcast = json.loads(cs.enqueued[0])
    assert broadcast["degradations"] == []
    assert cs.capture_mode_degraded is False
