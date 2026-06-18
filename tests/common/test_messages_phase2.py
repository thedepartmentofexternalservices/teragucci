"""Wave 0 - Phase 2 common/messages.py extensions wire round-trip scaffold.

Mirrors the tests/common/test_messages.py round-trip idiom for the 5 new
message/dataclass surfaces Wave 1 (02-02) lands:

  - KeyResetModifiersMsg  (D-11 release-all-modifiers triggers)
  - TextCommitMsg         (D-15 IME commit string passthrough)
  - PenProximityMsg       (D-19 proximity-event recovery)
  - Extended KeyEventMsg  (D-14 caps/num/scroll-lock bits)
  - Extended ServerHelloMsg (D-03 color_caps dataclass for
                             supports_main10/422/444 + negotiated_state)

Wave 0 role: skeleton only. Each test imports the target symbol inside
the test body so the 7 xfail entries are always collected, matching the
Task 2 acceptance count of exactly 7. Real assertions ship with Wave 1.
"""
from __future__ import annotations

import pytest


def test_key_reset_modifiers_roundtrip():
    """D-11 — release-all-modifiers wire format.

    Client dispatches this on four triggers: focusOut, reconnect, server-
    periodic safety, F9 panic. Reason field is log-only (T-02-04 disposition
    — server always dispatches the same idempotent reset regardless).
    """
    from common.messages import KeyResetModifiersMsg, MsgType, parse_message
    msg = KeyResetModifiersMsg(reason="focus_out")
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.KEY_RESET_MODIFIERS
    assert parsed["reason"] == "focus_out"


def test_text_commit_roundtrip():
    """D-15 — IME / dead-key commit string passthrough.

    Layout-independent Unicode commit; server injects via xdotool type
    (Linux) or CGEventKeyboardSetUnicodeString (macOS) — NOT as synthesized
    keycodes which would mangle dead-key composition.
    """
    from common.messages import MsgType, TextCommitMsg, parse_message
    # Japanese hiragana — exercises the Unicode passthrough contract
    msg = TextCommitMsg(text="あ")
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.TEXT_COMMIT
    assert parsed["text"] == "あ"


def test_pen_proximity_roundtrip():
    """D-19 — proximity-event recovery on focusIn / showEvent.

    Idempotent on the server side (PenFSM tolerates duplicate enters).
    """
    from common.messages import MsgType, PenProximityMsg, parse_message
    msg = PenProximityMsg(in_proximity=True, pen_type="eraser")
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.PEN_PROXIMITY
    assert parsed["in_proximity"] is True
    assert parsed["pen_type"] == "eraser"


def test_key_event_extended_with_caps_num_scroll_lock_bits():
    """D-14 — Caps / Num / Scroll Lock state bits on every KeyEvent.

    Client sends lock-state with every keystroke; server auto-corrects
    its virtual display's lock state on mismatch. Zero round-trip cost;
    state re-converges on the next keystroke. Phase 2 promotes KEY_EVENT
    from a raw dict to a ``@dataclass`` with the three lock-state bits
    as new fields (default False = Phase 1 wire compat).
    """
    from common.messages import KeyEventMsg, MsgType, parse_message
    msg = KeyEventMsg(
        scan_code=30, pressed=True,
        caps_lock_on=True, num_lock_on=False, scroll_lock_on=False,
    )
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.KEY_EVENT
    assert parsed["scan_code"] == 30
    assert parsed["pressed"] is True
    assert parsed["caps_lock_on"] is True
    assert parsed["num_lock_on"] is False
    assert parsed["scroll_lock_on"] is False


def test_server_hello_extended_with_color_caps():
    """D-03 — ServerHelloMsg advertises a nested ServerColorCaps block.

    Server capability-probe writes main10 / chroma_422 / chroma_444 +
    a negotiated_state badge. ``asdict`` recurses into the nested
    dataclass so the wire format is ``color_caps: {main10, chroma_422,
    chroma_444, advertised_pix_fmt, negotiated_state}``.

    Per threat T-02-06 (info-disclosure), ServerHelloMsg is sent POST-auth,
    so the capability fingerprint is not emitted on pre-auth endpoints.
    """
    from common.messages import (
        MsgType, ServerColorCaps, ServerHelloMsg, parse_message,
    )
    caps = ServerColorCaps(
        main10=True, chroma_422=False, chroma_444=False,
        negotiated_state="confirmed",
    )
    hello = ServerHelloMsg(server_name="teraguchi-srv", color_caps=caps)
    parsed = parse_message(hello.to_json())
    assert parsed["type"] == MsgType.SERVER_HELLO
    assert parsed["server_name"] == "teraguchi-srv"
    assert parsed["color_caps"]["main10"] is True
    assert parsed["color_caps"]["chroma_422"] is False
    assert parsed["color_caps"]["chroma_444"] is False
    assert parsed["color_caps"]["negotiated_state"] == "confirmed"


def test_color_caps_dataclass_defaults_false():
    """D-03 — ServerColorCaps() default instance surfaces as 'not_supported'.

    Capability-probe failure path — the server refuses to advertise
    10-bit / 4:2:2 / 4:4:4 when hardware can't honestly deliver, and the
    client health overlay renders the ``negotiated_state`` badge so
    artists see state at a glance (no silent fallback).
    """
    from common.messages import ServerColorCaps
    caps = ServerColorCaps()
    assert caps.main10 is False
    assert caps.chroma_422 is False
    assert caps.chroma_444 is False
    assert caps.advertised_pix_fmt == "p010le"
    assert caps.negotiated_state == "not_supported"


def test_unknown_msgtype_still_rejected_like_phase1():
    """Phase 1 parse_message contract preserved.

    tests/common/test_messages.py::test_parse_message_unknown_type_passes_through
    establishes that parse_message is a thin ``json.loads`` — unknown
    type values flow through as plain dicts with no KeyError. Phase 2
    additions MUST not tighten this contract (upstream dispatcher is
    responsible for unknown-type handling, not the parser).
    """
    from common.messages import parse_message
    result = parse_message('{"type": "phase2_bogus_type_xyz"}')
    assert result["type"] == "phase2_bogus_type_xyz"


# ═══════════════════════════════════════════════════════════════════════
# Phase 3 wire round-trip coverage (Plan 03-01, Task 1)
#
# D-02 — ClientHelloMsg capture-mode negotiation
# D-05 — server_x / server_y physical-pixel integer sentinels on input msgs
# D-13 — ClipboardChunkMsg / SESSION_CONFIGURE MsgType constants
# D-17 — ClipboardChunkMsg dataclass
# ConnectionProfile — 7 new Phase 3 fields with backward-compat defaults
# ═══════════════════════════════════════════════════════════════════════


def test_clipboard_chunk_msg_type_constant():
    """D-17 — MsgType.CLIPBOARD_CHUNK + SESSION_CONFIGURE constants exist.

    Downstream Plans 02-06 import these by name; locking the string
    values now prevents silent renames.
    """
    from common.messages import MsgType
    assert MsgType.CLIPBOARD_CHUNK == "clipboard_chunk"
    assert MsgType.SESSION_CONFIGURE == "session_configure"


def test_input_msgs_carry_server_xy():
    """D-05 — every input message round-trips the server_x/server_y pair.

    Default ``-1`` is the "client did not compute physical px" sentinel;
    server prefers integer fields when ``server_x >= 0`` and falls back
    to the normalized floats otherwise.
    """
    from common.messages import (
        KeyEventMsg,
        MouseButtonMsg,
        MouseMoveMsg,
        MouseScrollMsg,
        MsgType,
        PenEventMsg,
        parse_message,
    )

    # Explicit server-px values round-trip to integer JSON fields.
    move = MouseMoveMsg(x=0.5, y=0.5, server_x=1920, server_y=1080)
    parsed_move = parse_message(move.to_json())
    assert parsed_move["type"] == MsgType.MOUSE_MOVE
    assert parsed_move["server_x"] == 1920
    assert parsed_move["server_y"] == 1080

    button = MouseButtonMsg(x=0.1, y=0.2, button=1, pressed=True,
                            server_x=100, server_y=200)
    parsed_btn = parse_message(button.to_json())
    assert parsed_btn["type"] == MsgType.MOUSE_BUTTON
    assert parsed_btn["server_x"] == 100
    assert parsed_btn["server_y"] == 200
    assert parsed_btn["button"] == 1
    assert parsed_btn["pressed"] is True

    scroll = MouseScrollMsg(x=0.3, y=0.4, dx=0.0, dy=-1.0,
                            server_x=500, server_y=600)
    parsed_scr = parse_message(scroll.to_json())
    assert parsed_scr["type"] == MsgType.MOUSE_SCROLL
    assert parsed_scr["server_x"] == 500
    assert parsed_scr["dy"] == -1.0

    key = KeyEventMsg(scan_code=30, pressed=True, caps_lock_on=True,
                      server_x=42, server_y=99)
    parsed_key = parse_message(key.to_json())
    assert parsed_key["type"] == MsgType.KEY_EVENT
    assert parsed_key["caps_lock_on"] is True
    assert parsed_key["server_x"] == 42
    assert parsed_key["server_y"] == 99

    pen = PenEventMsg(x=0.5, y=0.5, pressure=0.75, server_x=7, server_y=8)
    parsed_pen = parse_message(pen.to_json())
    assert parsed_pen["type"] == MsgType.PEN_EVENT
    assert parsed_pen["pressure"] == 0.75
    assert parsed_pen["server_x"] == 7
    assert parsed_pen["server_y"] == 8

    # Default sentinel is -1 (pre-Phase-3 backward compat).
    for cls in (MouseMoveMsg, MouseButtonMsg, MouseScrollMsg,
                KeyEventMsg, PenEventMsg):
        default_parsed = parse_message(cls().to_json())
        assert default_parsed["server_x"] == -1, f"{cls.__name__} default server_x"
        assert default_parsed["server_y"] == -1, f"{cls.__name__} default server_y"


def test_clipboard_chunk_msg_roundtrip():
    """D-17 — ClipboardChunkMsg wire round-trip.

    Mirrors :class:`KeyResetModifiersMsg` shape. Plan 06 adds the
    assembler in ``common/clipboard_chunks.py``; this test only locks
    the wire contract.
    """
    from common.messages import ClipboardChunkMsg, MsgType, parse_message
    msg = ClipboardChunkMsg(
        sequence_id=42,
        chunk_index=0,
        total_chunks=3,
        content_type="image/png",
        data="abc=",
    )
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.CLIPBOARD_CHUNK
    assert parsed["sequence_id"] == 42
    assert parsed["chunk_index"] == 0
    assert parsed["total_chunks"] == 3
    assert parsed["content_type"] == "image/png"
    assert parsed["data"] == "abc="

    # Default-constructed instance lands safe pre-Phase-3 values.
    default_parsed = parse_message(ClipboardChunkMsg().to_json())
    assert default_parsed["sequence_id"] == 0
    assert default_parsed["total_chunks"] == 1
    assert default_parsed["content_type"] == "text/plain"
    assert default_parsed["data"] == ""


def test_client_hello_capture_mode_extension():
    """D-02 — ClientHelloMsg carries capture_mode + picked monitor fields.

    Defaults (``mirror_all``, ``-1``, ``""``) preserve the pre-Phase-3
    always-full-virtual-desktop server behavior.
    """
    from common.messages import ClientHelloMsg, MsgType, parse_message
    hello = ClientHelloMsg(
        capture_mode="pick_one",
        picked_monitor_id=1,
        picked_monitor_name="DP-1",
    )
    parsed = parse_message(hello.to_json())
    assert parsed["type"] == MsgType.CLIENT_HELLO
    assert parsed["capture_mode"] == "pick_one"
    assert parsed["picked_monitor_id"] == 1
    assert parsed["picked_monitor_name"] == "DP-1"

    # Defaults are the pre-Phase-3-compatible sentinel values.
    default_parsed = parse_message(ClientHelloMsg().to_json())
    assert default_parsed["capture_mode"] == "mirror_all"
    assert default_parsed["picked_monitor_id"] == -1
    assert default_parsed["picked_monitor_name"] == ""


def test_client_hello_clipboard_toggle_extension():
    """D-15 / Plan 03-06 — ClientHelloMsg carries the 4 per-direction clipboard toggles.

    Wire field names match :class:`ConnectionProfile` so the client
    can push its bookmark's toggle state straight through. Defaults are
    all True (D-16 "secure defaults = all directions ON"), preserving
    pre-Phase-3 always-on clipboard behavior for legacy clients.
    """
    from common.messages import ClientHelloMsg, MsgType, parse_message
    hello = ClientHelloMsg(
        clipboard_text_c2s=False,
        clipboard_text_s2c=True,
        clipboard_image_c2s=True,
        clipboard_image_s2c=False,
    )
    parsed = parse_message(hello.to_json())
    assert parsed["type"] == MsgType.CLIENT_HELLO
    assert parsed["clipboard_text_c2s"] is False
    assert parsed["clipboard_text_s2c"] is True
    assert parsed["clipboard_image_c2s"] is True
    assert parsed["clipboard_image_s2c"] is False

    # Defaults are all True (pre-Phase-3 legacy behavior + D-16 security default).
    default_parsed = parse_message(ClientHelloMsg().to_json())
    assert default_parsed["clipboard_text_c2s"] is True
    assert default_parsed["clipboard_text_s2c"] is True
    assert default_parsed["clipboard_image_c2s"] is True
    assert default_parsed["clipboard_image_s2c"] is True


def test_connection_profile_phase3_field_defaults():
    """ConnectionProfile — the 7 Phase 3 fields load safely from legacy JSON.

    Pre-Phase-3 bookmark JSON files omit these fields entirely;
    ``from_dict`` filters unknown keys through ``__dataclass_fields__``
    so missing fields fall back to defaults (threat T-03-01 mitigation).
    """
    from common.messages import ConnectionProfile
    # Minimal pre-Phase-3 bookmark — no Phase 3 fields at all.
    p = ConnectionProfile.from_dict({
        "host": "rocky.local",
        "port": 443,
        "username": "randy",
    })
    assert p.monitor_mode == "mirror_all"
    assert p.picked_monitor_id == -1
    assert p.picked_monitor_name == ""
    assert p.clipboard_text_c2s is True
    assert p.clipboard_text_s2c is True
    assert p.clipboard_image_c2s is True
    assert p.clipboard_image_s2c is True

    # Round-trip preserves explicitly-set values.
    p2 = ConnectionProfile.from_dict({
        "host": "mac.local",
        "port": 443,
        "monitor_mode": "pick_one",
        "picked_monitor_id": 2,
        "picked_monitor_name": "HDMI-A-0",
        "clipboard_text_c2s": False,
        "clipboard_image_s2c": False,
    })
    d = p2.to_dict()
    assert d["monitor_mode"] == "pick_one"
    assert d["picked_monitor_id"] == 2
    assert d["picked_monitor_name"] == "HDMI-A-0"
    assert d["clipboard_text_c2s"] is False
    assert d["clipboard_image_s2c"] is False
    # Untouched directions stay ON (D-16 secure default).
    assert d["clipboard_text_s2c"] is True
    assert d["clipboard_image_c2s"] is True


def test_connection_profile_filters_unknown_keys():
    """Threat T-03-01 — unknown keys are stripped by ``from_dict``.

    Pattern inherited from the Phase 2 D-10 migration block; guards
    against bookmark JSON tampering that injects stray fields.
    """
    from common.messages import ConnectionProfile
    p = ConnectionProfile.from_dict({
        "host": "x.local",
        "port": 443,
        "bogus_key": "ignored",
        "__evil__": [1, 2, 3],
    })
    # Unknown keys never appear on the dataclass instance.
    assert not hasattr(p, "bogus_key")
    assert not hasattr(p, "__evil__")
    # Known defaults still populate.
    assert p.monitor_mode == "mirror_all"


def test_monitor_list_with_degradations():
    """Plan 03-05 / D-09 — MonitorListMsg carries per-client fallback events.

    Each entry in ``degradations`` is a dict of:
      - ``client_token``: server-side session identifier (currently
        ``ClientSession.client_id``)
      - ``previous_pick``: the monitor name the client was bookmarked to
      - ``now_showing``: the fallback monitor (typically the primary)

    Empty ``degradations`` list is the pre-Plan-05 wire-compat default
    (topology changed but no sessions needed fall-back).
    """
    from common.messages import MonitorListMsg, MsgType, parse_message
    m = MonitorListMsg(
        monitors=[{"id": 1, "name": "DP-1"}],
        degradations=[{
            "client_token": "tok1",
            "previous_pick": "DP-2",
            "now_showing": "DP-1",
        }],
    )
    parsed = parse_message(m.to_json())
    assert parsed["type"] == MsgType.MONITOR_LIST
    assert parsed["monitors"] == [{"id": 1, "name": "DP-1"}]
    assert len(parsed["degradations"]) == 1
    assert parsed["degradations"][0]["previous_pick"] == "DP-2"
    assert parsed["degradations"][0]["now_showing"] == "DP-1"
    assert parsed["degradations"][0]["client_token"] == "tok1"

    # Default-constructed instance keeps empty degradations list
    # (pre-Plan-05 wire-compat).
    default_parsed = parse_message(MonitorListMsg().to_json())
    assert default_parsed["degradations"] == []
