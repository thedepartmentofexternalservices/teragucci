"""Phase 3 D-02 / D-09 / D-12 — Linux screen-capture hot-plug detection.

Plan 03-03 Task 1. The shallow count+WxH signature is upgraded to a full
``(id, width, height, x, y)`` tuple so monitor reorder and reposition
also trigger the change signal (mirrors the D-11 Mac upgrade).
"""
import pytest


def _fake_monitors_v1():
    return [
        {"left": 0, "top": 0, "width": 5120, "height": 1600},
        {"left": 0, "top": 0, "width": 2560, "height": 1600},
        {"left": 2560, "top": 0, "width": 2560, "height": 1600},
    ]


def _fake_monitors_repositioned():
    # Same sizes, but the secondary monitor moved from x=2560 to x=3000.
    return [
        {"left": 0, "top": 0, "width": 5120, "height": 1600},
        {"left": 0, "top": 0, "width": 2560, "height": 1600},
        {"left": 3000, "top": 0, "width": 2560, "height": 1600},
    ]


def _fake_monitors_count_added():
    return [
        {"left": 0, "top": 0, "width": 7680, "height": 1600},
        {"left": 0, "top": 0, "width": 2560, "height": 1600},
        {"left": 2560, "top": 0, "width": 2560, "height": 1600},
        {"left": 5120, "top": 0, "width": 2560, "height": 1600},
    ]


@pytest.fixture
def capture(monkeypatch):
    """Minimal ScreenCapture for hot-plug tests — mocks mss at the boundary."""

    class _FakeSct:
        def __init__(self, monitors):
            self.monitors = monitors

        def close(self):
            pass

        def grab(self, monitor):
            raise AssertionError("grab not expected in hot-plug tests")

    import mss
    state = {"monitors": _fake_monitors_v1()}

    def _mss_factory():
        return _FakeSct(list(state["monitors"]))

    monkeypatch.setattr(mss, "mss", _mss_factory)

    import server.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "detect_nvfbc", lambda: False)
    monkeypatch.setattr(sc_mod, "detect_monitors_xrandr", lambda: [])
    monkeypatch.setattr(sc_mod, "_HAS_NVFBC_BACKEND", False)
    monkeypatch.setattr(sc_mod, "_HAS_XLIB_DAMAGE", False)

    cap = sc_mod.ScreenCapture(monitor_index=0)
    # Expose the mutable monitor slot so tests can swap topologies.
    cap.__mss_state = state  # type: ignore[attr-defined]
    return cap


def test_detect_hotplug_catches_count_change(capture):
    """D-12 — monitor added → detect_hotplug returns True."""
    capture.__mss_state["monitors"] = _fake_monitors_count_added()
    assert capture.detect_hotplug() is True


def test_detect_hotplug_catches_position_change(capture):
    """D-09 — same WxH but monitor repositioned → detect_hotplug catches the move.

    The legacy shallow signature (count + WxH only) missed this. Full
    tuple signature + xrandr-query triangulation catches reposition.
    """
    capture.__mss_state["monitors"] = _fake_monitors_repositioned()
    assert capture.detect_hotplug() is True


def test_detect_hotplug_stable_when_nothing_changes(capture):
    """D-09 — unchanged monitor layout → detect_hotplug returns False."""
    # Same topology as init; hot-plug sentinel should not fire.
    assert capture.detect_hotplug() is False


def test_detect_hotplug_full_tuple_signature_includes_id_and_position(capture, monkeypatch):
    """D-09 — signature is (id, width, height, x, y) per monitor, not just (w, h).

    Verifies the implementation inspects the positional axes by seeding
    list_monitors with a position that would be silently equal under the
    old (w, h)-only signature but differs under the full tuple.
    """
    # Baseline: run detect_hotplug once with a no-op change so the internal
    # cached signature is refreshed.
    assert capture.detect_hotplug() is False
    # Now flip only the x position on the second physical monitor.
    capture.__mss_state["monitors"] = _fake_monitors_repositioned()
    assert capture.detect_hotplug() is True


# ═══════════════════════════════════════════════════════════════════════
# Plan 03-05 — hot-plug auto-fallback iteration harvests degradations
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def fake_monitor_runtime():
    """Minimal SessionRuntime-shaped fake exposing apply_capture_mode +
    list_monitors stub. Lets us drive MonitorHotplug.run()'s iteration
    body without spinning up an X server / mss / encoder.
    """
    import types
    from common.messages import MonitorInfo

    class _FakeCapture:
        def __init__(self):
            self._hotplug_pending = True
            self._monitors_now = [
                MonitorInfo(id=1, name="DP-1", width=2560, height=1440,
                            x=0, y=0, primary=True),
            ]

        def detect_hotplug(self) -> bool:
            return True

        def list_monitors(self):
            return list(self._monitors_now)

    class _FakeEncoderLifecycle:
        def __init__(self):
            self.restart_calls = 0

        def restart(self):
            self.restart_calls += 1

    class _FakeClientSession:
        def __init__(self, cid, mode, picked_id, picked_name,
                     authenticated=True):
            self.client_id = cid
            self.authenticated = authenticated
            self.capture_mode = mode
            self.picked_monitor_id = picked_id
            self.picked_monitor_name = picked_name
            self.crop_rect = None
            self.capture_mode_degraded = False
            self.enqueued: list = []

        async def enqueue(self, msg):
            self.enqueued.append(msg)

    runtime = types.SimpleNamespace(
        capture=_FakeCapture(),
        encoder=object(),
        encoder_lifecycle=_FakeEncoderLifecycle(),
        clients={},
    )

    # apply_capture_mode: if session's picked_monitor_id is not in the
    # capture's current monitors, flip degraded + fall back to primary.
    def apply_capture_mode(session, mode, picked_id, picked_name):
        current = runtime.capture.list_monitors()
        if mode == "mirror_all":
            session.capture_mode = mode
            session.capture_mode_degraded = False
            return True
        match = next((m for m in current if m.id == picked_id), None)
        if match is None:
            # Fall back to primary
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
    runtime._FakeClientSession = _FakeClientSession
    return runtime


def test_hotplug_iteration_harvests_degradation_for_vanished_pick_one(
    fake_monitor_runtime,
):
    """Plan 03-05 / D-09 — when a pick_one session's monitor vanishes,
    MonitorHotplug.run() one-iteration body MUST call apply_capture_mode
    which flips capture_mode_degraded=True, and the broadcast
    MonitorListMsg MUST carry a degradations entry for that client.
    """
    import asyncio
    import json
    from server.monitor_hotplug import MonitorHotplug

    runtime = fake_monitor_runtime
    cs = runtime._FakeClientSession(
        cid="cid-A", mode="pick_one", picked_id=2, picked_name="DP-2",
    )
    # Use a plain dict value (key shape doesn't matter; we iterate .items())
    runtime.clients[object()] = cs

    hotplug = MonitorHotplug(runtime)

    # Drive one iteration manually — asyncio.sleep(1.0) inside run() is
    # bypassed by flipping _running=True, awaiting once, then _running=False.
    async def _one_iteration():
        hotplug._running = True
        task = asyncio.ensure_future(hotplug.run())
        # Give it enough time to complete one pass (the 1s sleep keeps it
        # bounded; bump to 1.2s for safety).
        await asyncio.sleep(1.2)
        hotplug._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_one_iteration())

    # The session got its apply_capture_mode call and degraded to primary.
    assert cs.capture_mode_degraded is True
    assert cs.picked_monitor_id == 1
    assert cs.picked_monitor_name == "DP-1"

    # The broadcast MonitorListMsg carries the degradation entry.
    assert len(cs.enqueued) == 1
    broadcast = json.loads(cs.enqueued[0])
    assert broadcast["type"] == "monitor_list"
    assert len(broadcast.get("degradations", [])) == 1
    event = broadcast["degradations"][0]
    assert event["previous_pick"] == "DP-2"
    assert event["now_showing"] == "DP-1"
    assert event["client_token"] == "cid-A"


def test_hotplug_iteration_no_degradation_for_mirror_all(
    fake_monitor_runtime,
):
    """mirror_all sessions never appear in the degradations list even
    when the topology changed — only pick_one / single auto-fallback
    does (D-09 scope)."""
    import asyncio
    import json
    from server.monitor_hotplug import MonitorHotplug

    runtime = fake_monitor_runtime
    cs = runtime._FakeClientSession(
        cid="cid-B", mode="mirror_all", picked_id=-1, picked_name="",
    )
    runtime.clients[object()] = cs
    hotplug = MonitorHotplug(runtime)

    async def _one_iteration():
        hotplug._running = True
        task = asyncio.ensure_future(hotplug.run())
        await asyncio.sleep(1.2)
        hotplug._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_one_iteration())

    assert len(cs.enqueued) == 1
    broadcast = json.loads(cs.enqueued[0])
    assert broadcast["degradations"] == []
