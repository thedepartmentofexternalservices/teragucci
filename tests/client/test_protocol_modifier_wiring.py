"""Phase 2 D-10 / D-11 / D-14 / D-15 — client/protocol.py wire layer.

Plan 02-09 Task 2 acceptance:

  * ``ClientProtocol.send_reset_modifiers(reason)`` posts a
    ``KeyResetModifiersMsg`` JSON payload via the active websocket sink.
  * ``ClientProtocol.send_text_commit(text)`` posts a ``TextCommitMsg``
    JSON payload.
  * ``ClientProtocol.set_swap_cmd_ctrl(True)`` flips a per-session flag
    that the key-event sender consults before serializing.
  * Outbound key events carry ``caps_lock_on`` / ``num_lock_on`` /
    ``scroll_lock_on`` bits derived from a Mac-first lock-state probe.

Tests run in-process — no real websocket, no real Qt window. We patch
the ``ClientProtocol`` send sink so we can observe every JSON payload
the protocol layer would have shipped.
"""
from __future__ import annotations

import json

import pytest

from common.messages import MsgType


def _make_protocol_with_sink():
    """Construct a ClientProtocol pinned to an in-process sink.

    Replaces the ``send_input`` method (the one common code path that
    posts JSON dict payloads) with a list-append. ``set_connected`` is
    not needed because send_input checks ``self._connected`` — we just
    poke the flag directly so we don't need a live websocket.
    """
    from client.protocol import ClientProtocol
    proto = ClientProtocol()
    captured: list[dict] = []

    def _capture(msg_dict: dict):
        captured.append(msg_dict)

    proto.send_input = _capture  # type: ignore[assignment]
    return proto, captured


def test_send_reset_modifiers_emits_key_reset_message():
    """ClientProtocol.send_reset_modifiers serializes a KeyResetModifiersMsg."""
    proto, captured = _make_protocol_with_sink()
    proto.send_reset_modifiers("focus_out")
    assert len(captured) == 1
    msg = captured[0]
    assert msg["type"] == MsgType.KEY_RESET_MODIFIERS
    assert msg["reason"] == "focus_out"


@pytest.mark.parametrize("reason", ["focus_out", "reconnect", "panic_f9"])
def test_send_reset_modifiers_round_trips_reason(reason):
    proto, captured = _make_protocol_with_sink()
    proto.send_reset_modifiers(reason)
    assert captured[0]["reason"] == reason


def test_send_text_commit_emits_text_commit_message():
    """ClientProtocol.send_text_commit serializes a TextCommitMsg."""
    proto, captured = _make_protocol_with_sink()
    proto.send_text_commit("\u3042")  # JP hiragana 'a'
    assert len(captured) == 1
    msg = captured[0]
    assert msg["type"] == MsgType.TEXT_COMMIT
    assert msg["text"] == "\u3042"


def test_swap_cmd_ctrl_flag_default_off():
    """Per-session swap flag defaults False — only flip when bookmark says so."""
    proto, _ = _make_protocol_with_sink()
    assert proto.swap_cmd_ctrl is False


def test_set_swap_cmd_ctrl_persists_across_calls():
    proto, _ = _make_protocol_with_sink()
    proto.set_swap_cmd_ctrl(True)
    assert proto.swap_cmd_ctrl is True
    proto.set_swap_cmd_ctrl(False)
    assert proto.swap_cmd_ctrl is False


def test_send_key_event_carries_lock_state_bits():
    """KeyEvent dict gains caps_lock_on / num_lock_on / scroll_lock_on (D-14).

    Even when the underlying probe returns all-False (e.g. test host has
    no NSEvent / no Caps Lock pressed), the bits MUST be present so the
    server's auto-correct path always sees a definitive value.
    """
    proto, captured = _make_protocol_with_sink()
    proto.send_key_event(qt_key=0x53, scan_code=0x53, pressed=True, modifiers=0)
    assert len(captured) == 1
    msg = captured[0]
    assert msg["type"] == MsgType.KEY_EVENT
    assert msg["scan_code"] == 0x53
    assert msg["pressed"] is True
    # D-14 bits — three booleans, all present, all valid bool type.
    for field in ("caps_lock_on", "num_lock_on", "scroll_lock_on"):
        assert field in msg, f"key_event missing {field} (D-14)"
        assert isinstance(msg[field], bool), f"{field} must be bool"


def test_send_key_event_swaps_cmd_for_ctrl_when_swap_enabled():
    """D-10: with swap_cmd_ctrl=True, Cmd-pressed becomes Ctrl on the wire."""
    proto, captured = _make_protocol_with_sink()
    proto.set_swap_cmd_ctrl(True)
    QT_KEY_META = 0x01000022
    QT_KEY_CTRL = 0x01000021
    proto.send_key_event(qt_key=QT_KEY_META, scan_code=QT_KEY_META,
                         pressed=True, modifiers=0)
    assert len(captured) == 1
    msg = captured[0]
    assert msg["scan_code"] == QT_KEY_CTRL, (
        f"swap_cmd_ctrl=True should rewrite Meta(0x{QT_KEY_META:x}) "
        f"to Ctrl(0x{QT_KEY_CTRL:x}); got 0x{msg['scan_code']:x}"
    )


def test_send_key_event_swaps_meta_modifier_bit_when_enabled():
    """D-10: MOD_META in modifiers becomes MOD_CTRL when swap is enabled."""
    proto, captured = _make_protocol_with_sink()
    proto.set_swap_cmd_ctrl(True)
    from common.keymap import MOD_CTRL, MOD_META
    proto.send_key_event(qt_key=0x53, scan_code=0x53, pressed=True,
                         modifiers=MOD_META)
    msg = captured[0]
    assert (msg["modifiers"] & MOD_CTRL) != 0, "MOD_META should map to MOD_CTRL"
    assert (msg["modifiers"] & MOD_META) == 0, "MOD_META should be cleared"


def test_send_key_event_no_swap_when_flag_off():
    """When swap_cmd_ctrl is False, Cmd passes through untouched (Mac-server)."""
    proto, captured = _make_protocol_with_sink()
    proto.set_swap_cmd_ctrl(False)
    QT_KEY_META = 0x01000022
    proto.send_key_event(qt_key=QT_KEY_META, scan_code=QT_KEY_META,
                         pressed=True, modifiers=0)
    msg = captured[0]
    assert msg["scan_code"] == QT_KEY_META, (
        "swap_cmd_ctrl=False must not rewrite the key"
    )


def test_send_key_event_serializes_to_json():
    """Sanity: the captured dict round-trips through JSON without loss."""
    proto, captured = _make_protocol_with_sink()
    proto.send_key_event(qt_key=0x53, scan_code=0x53, pressed=True, modifiers=0)
    text = json.dumps(captured[0])
    parsed = json.loads(text)
    assert parsed["type"] == MsgType.KEY_EVENT
