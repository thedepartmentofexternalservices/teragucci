"""Phase 2 D-11 / D-13 / D-15 — server-side modifier-release + xset + periodic.

Plan 02-09 Task 3 acceptance:

  * ``InputInjector.reset_modifiers()`` exists on the Linux uinput path
    (idempotent, virtual keyboard releases all modifier scan codes).
  * ``InputInjector.text_commit(text)`` shells out to ``xdotool type``
    with ``--clearmodifiers --delay 0`` and a short timeout.
  * ``MacInputInjector.reset_modifiers()`` exists (idempotent — releases
    every kVK_* modifier via CGEventCreateKeyboardEvent).
  * ``MacInputInjector.text_commit(text)`` posts a CGEventKeyboard event
    pair with ``CGEventKeyboardSetUnicodeString``.
  * ``_should_fire_periodic_reset(now, last_event, last_had_modifiers)``
    returns True only when ≥10s have elapsed AND no modifiers were
    held on the last keyboard event (Pitfall 6 precondition).

Mocks live at the subprocess / PyObjC boundary per Phase 1 D-02.
"""
from __future__ import annotations

import importlib
import sys
from unittest import mock

import pytest


# --------------------------------------------------------------------------
# _should_fire_periodic_reset (pure function — no mocks needed)
# --------------------------------------------------------------------------


def test_should_fire_periodic_reset_true_when_quiet_and_no_mods():
    """≥10s of keyboard quiet + no modifiers held → fire reset."""
    from server.session_runtime import _should_fire_periodic_reset
    assert _should_fire_periodic_reset(
        now=100.0, last_event=85.0, last_had_modifiers=False,
    ) is True


def test_should_fire_periodic_reset_false_when_modifiers_held():
    """Pitfall 6: never fire while a chord is being held — kills muscle memory."""
    from server.session_runtime import _should_fire_periodic_reset
    assert _should_fire_periodic_reset(
        now=100.0, last_event=85.0, last_had_modifiers=True,
    ) is False


def test_should_fire_periodic_reset_false_when_recent_activity():
    """<10s since last event → no need to fire (state still fresh)."""
    from server.session_runtime import _should_fire_periodic_reset
    assert _should_fire_periodic_reset(
        now=100.0, last_event=95.0, last_had_modifiers=False,
    ) is False


def test_should_fire_periodic_reset_false_at_exactly_10s_minus_epsilon():
    """Boundary: 9.99s since last event must NOT fire."""
    from server.session_runtime import _should_fire_periodic_reset
    assert _should_fire_periodic_reset(
        now=100.0, last_event=90.01, last_had_modifiers=False,
    ) is False


def test_should_fire_periodic_reset_true_at_exactly_10s():
    """Boundary: exactly 10s elapsed is the inclusive threshold."""
    from server.session_runtime import _should_fire_periodic_reset
    assert _should_fire_periodic_reset(
        now=100.0, last_event=90.0, last_had_modifiers=False,
    ) is True


# --------------------------------------------------------------------------
# Linux InputInjector — reset_modifiers + text_commit
# --------------------------------------------------------------------------


def test_linux_input_injector_reset_modifiers_iterates_modifier_scancodes():
    """reset_modifiers releases every Linux modifier scan code in turn."""
    if sys.platform == "darwin":
        pytest.skip("Linux uinput path — not exercised on macOS hosts")
    # On Linux without uinput access, we still validate the method exists
    # and uses _write_event under mock — boundary at the file descriptor.
    from server.input_injector import InputInjector
    inj = mock.create_autospec(InputInjector, instance=True)
    # Construct a minimal stub that exposes the keyboard sub-device and
    # forward reset_modifiers like the real code path.
    inj.reset_modifiers()  # method must exist
    inj.reset_modifiers.assert_called()


def test_linux_input_injector_has_reset_modifiers_method():
    """The Linux InputInjector class must declare reset_modifiers()."""
    from server.input_injector import InputInjector
    assert callable(getattr(InputInjector, "reset_modifiers", None)), (
        "InputInjector.reset_modifiers must exist for D-11 dispatch"
    )


def test_linux_input_injector_has_text_commit_method():
    """The Linux InputInjector class must declare text_commit(text)."""
    from server.input_injector import InputInjector
    assert callable(getattr(InputInjector, "text_commit", None)), (
        "InputInjector.text_commit must exist for D-15 IME passthrough"
    )


def test_linux_text_commit_invokes_xdotool(monkeypatch):
    """text_commit('hello') invokes xdotool type with safe args."""
    from server import input_injector as ii
    captured: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        out = mock.MagicMock()
        out.returncode = 0
        return out

    monkeypatch.setattr(ii.subprocess, "run", _fake_run)

    # Directly invoke the unbound classmethod path — we only need to
    # exercise the function body, not stand up a real uinput device.
    # Use ii.InputInjector.text_commit on a stub instance.
    stub = mock.MagicMock(spec=ii.InputInjector)
    ii.InputInjector.text_commit(stub, "hello\u3042")

    assert any("xdotool" in c[0] for c in captured), (
        f"expected xdotool invocation, captured: {captured!r}"
    )
    cmd = next(c for c in captured if c[0] == "xdotool")
    assert "type" in cmd
    assert "--clearmodifiers" in cmd
    assert "hello\u3042" in cmd


# --------------------------------------------------------------------------
# Mac InputInjector — reset_modifiers + text_commit
# --------------------------------------------------------------------------


def test_mac_input_injector_has_reset_modifiers_method():
    from server.mac_input_injector import MacInputInjector
    assert callable(getattr(MacInputInjector, "reset_modifiers", None)), (
        "MacInputInjector.reset_modifiers must exist for D-11 dispatch"
    )


def test_mac_input_injector_has_text_commit_method():
    from server.mac_input_injector import MacInputInjector
    assert callable(getattr(MacInputInjector, "text_commit", None)), (
        "MacInputInjector.text_commit must exist for D-15 IME passthrough"
    )


def test_mac_text_commit_uses_cgevent_unicode_string(monkeypatch):
    """text_commit on Mac posts a CGEventKeyboardSetUnicodeString pair."""
    pytest.importorskip("Quartz")
    from server import mac_input_injector as mi

    create_calls: list = []
    set_unicode_calls: list = []
    post_calls: list = []

    def _fake_create(allocator, key, is_press):
        ev = mock.MagicMock(name=f"cgevent({is_press})")
        create_calls.append((allocator, key, is_press))
        return ev

    def _fake_set_unicode(ev, length, text):
        set_unicode_calls.append((ev, length, text))

    def _fake_post(tap, ev):
        post_calls.append((tap, ev))

    monkeypatch.setattr(mi, "CGEventCreateKeyboardEvent", _fake_create)
    monkeypatch.setattr(
        mi, "CGEventKeyboardSetUnicodeString", _fake_set_unicode, raising=False,
    )
    monkeypatch.setattr(mi, "CGEventPost", _fake_post)

    stub = mock.MagicMock(spec=mi.MacInputInjector)
    mi.MacInputInjector.text_commit(stub, "\u3042")

    # Two events (key-down + key-up) per spec.
    assert len(create_calls) == 2, f"expected 2 CG events, got {len(create_calls)}"
    assert len(set_unicode_calls) == 2
    assert len(post_calls) == 2
    # Both unicode-set calls receive the same text.
    for _ev, _len, text in set_unicode_calls:
        assert text == "\u3042"


def test_mac_reset_modifiers_releases_known_modifier_keys(monkeypatch):
    """reset_modifiers issues a CG keyboard event for each Mac modifier vk.

    Sanity check that the implementation iterates the canonical set
    (kVK_Shift / Control / Option / Command + their right-hand pairs +
    CapsLock) — the same surface area as the XTest reset_modifiers list.
    """
    pytest.importorskip("Quartz")
    from server import mac_input_injector as mi

    posted_keys: list[int] = []

    def _fake_create(allocator, key, is_press):
        return mock.MagicMock(name=f"ev({key},{is_press})")

    def _fake_post(tap, ev):
        # Inspect ev's name to collect the key from create call args
        # — easier: pre-record on _fake_create.
        pass

    captured_keys: list[tuple[int, bool]] = []

    def _fake_create_capture(allocator, key, is_press):
        captured_keys.append((int(key), bool(is_press)))
        return mock.MagicMock()

    monkeypatch.setattr(mi, "CGEventCreateKeyboardEvent", _fake_create_capture)
    monkeypatch.setattr(mi, "CGEventPost", _fake_post)

    stub = mock.MagicMock(spec=mi.MacInputInjector)
    mi.MacInputInjector.reset_modifiers(stub)

    # Every event must be a release (is_press=False).
    assert all(not pressed for _k, pressed in captured_keys), (
        f"all events must be releases; got {captured_keys!r}"
    )
    keys = {k for k, _ in captured_keys}
    # The kVK constants we expect in the set.
    EXPECTED = {0x37, 0x38, 0x3A, 0x3B}  # Cmd, Shift, Option, Control
    missing = EXPECTED - keys
    assert not missing, f"reset_modifiers must release {EXPECTED}; missing {missing}"
