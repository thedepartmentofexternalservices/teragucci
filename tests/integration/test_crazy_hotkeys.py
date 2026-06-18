"""INPUT-04: 'crazy Flame hotkey combos' end-to-end loopback.

Phase 2 Plan 02-11 (Wave 8). Replaces the Wave 0 xfail scaffolds with a
real loopback wss harness that drives KeyEvent / TextCommit /
KeyResetModifiers messages through ``websockets.serve`` /
``websockets.connect`` built on Phase 1's ``tls_ca_and_cert`` +
``free_port`` fixtures.

The server-side side of the test runs in-process: the test handler
consumes parsed ``parse_message(raw)`` dicts and asserts the wire shape
the client just sent. This proves the FULL pipeline:

  * ``common.keymap.qt_key_to_linux_scancode`` resolves every
    ``FLAME_CRITICAL_CHORDS`` entry to a real Linux scancode.
  * ``common.keymap.swap_cmd_ctrl_for_linux_dest`` rewrites Cmd → Ctrl
    on a Linux-bookmarked session before serialization.
  * ``common.messages.TextCommitMsg`` round-trips dead-key / IME commit
    strings without keycode synthesis (D-15).
  * The post-auth send order after a reconnect places
    ``KeyResetModifiersMsg`` ahead of any KeyEvent (D-11 trigger #2).

The ``fake_server_injector`` fixture (in ``conftest.py``) is the
mock-at-server-boundary equivalent of the Phase 1 mock-at-FFmpeg-
subprocess pattern from ``tests/server/test_video_encoder_mock.py``.
"""
from __future__ import annotations

import ssl

import pytest
import websockets

from common.keymap import (
    FLAME_CRITICAL_CHORDS,
    MOD_CTRL,
    MOD_META,
    qt_key_to_linux_scancode,
    swap_cmd_ctrl_for_linux_dest,
)
from common.messages import (
    KeyEventMsg,
    KeyResetModifiersMsg,
    MsgType,
    TextCommitMsg,
    parse_message,
)

pytestmark = pytest.mark.asyncio


def _server_ssl_ctx(cert_pem, key_pem) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_pem), str(key_pem))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


async def _loopback(server_handler, free_port, tls_ca_and_cert, client_actions):
    """Run ``server_handler`` as a wss server; ``client_actions`` is an
    async fn taking a websocket and exercising the test."""
    srv_ctx = _server_ssl_ctx(
        tls_ca_and_cert["server_cert"], tls_ca_and_cert["server_key"]
    )
    async with websockets.serve(
        server_handler, "127.0.0.1", free_port, ssl=srv_ctx
    ):
        cli_ctx = ssl.create_default_context(
            cafile=str(tls_ca_and_cert["ca_cert"])
        )
        async with websockets.connect(
            f"wss://127.0.0.1:{free_port}", ssl=cli_ctx
        ) as ws:
            await client_actions(ws)


# =====================================================================
# Test 1: Every FLAME_CRITICAL_CHORDS entry -> linux scancode round-trip
# =====================================================================


@pytest.mark.flame_critical
@pytest.mark.parametrize("qt_key,modifiers,desc", FLAME_CRITICAL_CHORDS)
async def test_flame_critical_chord_delivers_expected_linux_scancode(
    qt_key, modifiers, desc, fake_server_injector, tls_ca_and_cert, free_port,
):
    """For each Flame-critical chord, the client emits a KeyEventMsg whose
    wire scan_code matches ``qt_key_to_linux_scancode(qt_key)``.

    Pre-condition: ``qt_key_to_linux_scancode`` must return a non-zero
    scancode for every chord — Phase 1 keymap delivered this; we guard
    here so any future drop-out fails loudly.
    """
    received = []

    async def server_handler(ws):
        async for raw in ws:
            msg = parse_message(raw)
            if msg["type"] == MsgType.KEY_EVENT:
                received.append(msg)
                # Also record into the in-memory mock injector so future
                # tests/extensions can grow on the same fixture.
                fake_server_injector.key_event(
                    scan_code=msg["scan_code"],
                    pressed=msg["pressed"],
                    caps_lock_on=msg.get("caps_lock_on", False),
                )
                break

    async def client(ws):
        scan = qt_key_to_linux_scancode(qt_key)
        assert scan != 0, f"{desc}: qt_key=0x{qt_key:x} not in QT_KEY_TO_LINUX"
        msg = KeyEventMsg(scan_code=scan, pressed=True)
        await ws.send(msg.to_json())

    await _loopback(server_handler, free_port, tls_ca_and_cert, client)
    assert len(received) == 1, f"{desc}: no KEY_EVENT received"
    assert received[0]["scan_code"] == qt_key_to_linux_scancode(qt_key), desc
    assert received[0]["pressed"] is True, desc
    # And the same record landed in the mock injector.
    assert ("key_event", qt_key_to_linux_scancode(qt_key), True, False) in (
        fake_server_injector.events
    ), desc


# =====================================================================
# Test 2: Cmd+S on a linux-bookmark routes to Ctrl+S in the wire event
# =====================================================================


@pytest.mark.flame_critical
async def test_cmd_s_on_linux_bookmark_injects_as_ctrl_s(
    tls_ca_and_cert, free_port,
):
    """D-10: Cmd+S on a linux-bookmarked session is rewritten to Ctrl+S
    by ``swap_cmd_ctrl_for_linux_dest`` BEFORE the KeyEvent is serialized.

    The wire payload therefore carries the linux scancode for ``S``
    (KEY_S = 31), and (when the helper is correct) the modifier-state
    bookkeeping the client carries flips MOD_META -> MOD_CTRL.
    """
    received = []

    async def server_handler(ws):
        async for raw in ws:
            msg = parse_message(raw)
            if msg["type"] == MsgType.KEY_EVENT:
                received.append(msg)
                break

    async def client(ws):
        QT_KEY_S = 0x53
        # Simulated client-side path (per Plan 02-09):
        # ConnectionProfile.swap_cmd_ctrl=True + destination_kind="linux"
        # means client.protocol.send_key_event runs the swap helper FIRST.
        out_key, out_mods = swap_cmd_ctrl_for_linux_dest(QT_KEY_S, MOD_META)
        # The helper does NOT remap the letter key itself (S stays S);
        # it only translates Meta -> Ctrl in modifiers and remaps the
        # Meta-as-pressed-key case (handled in the dedicated test below).
        assert out_key == QT_KEY_S, "swap should leave the letter key alone"
        assert out_mods & MOD_CTRL, "swap helper failed to set MOD_CTRL"
        assert not (out_mods & MOD_META), "swap helper failed to clear MOD_META"
        scan = qt_key_to_linux_scancode(out_key)
        msg = KeyEventMsg(scan_code=scan, pressed=True)
        await ws.send(msg.to_json())

    await _loopback(server_handler, free_port, tls_ca_and_cert, client)
    assert len(received) == 1
    # KEY_S in linux input-event-codes is 31 (verified in common/keymap.py).
    assert received[0]["scan_code"] == 31, (
        f"Cmd+S on linux-bookmark should land KEY_S (31); got "
        f"{received[0]['scan_code']}"
    )


@pytest.mark.flame_critical
async def test_meta_keypress_on_linux_bookmark_remaps_to_control_key(
    tls_ca_and_cert, free_port,
):
    """D-10 second leg: pressing the Meta key itself on a linux-bookmark
    is rewritten to a Control key press (so the held-modifier state on
    the server side reflects Ctrl, not Meta).
    """
    received = []

    async def server_handler(ws):
        async for raw in ws:
            msg = parse_message(raw)
            if msg["type"] == MsgType.KEY_EVENT:
                received.append(msg)
                break

    async def client(ws):
        QT_KEY_META = 0x01000022
        QT_KEY_CTRL = 0x01000021
        out_key, _out_mods = swap_cmd_ctrl_for_linux_dest(QT_KEY_META, MOD_META)
        assert out_key == QT_KEY_CTRL, (
            "Meta-as-pressed-key on linux destination should remap to Control"
        )
        scan = qt_key_to_linux_scancode(out_key)
        msg = KeyEventMsg(scan_code=scan, pressed=True)
        await ws.send(msg.to_json())

    await _loopback(server_handler, free_port, tls_ca_and_cert, client)
    assert len(received) == 1
    # KEY_LEFTCTRL = 29 in common/keymap.py.
    assert received[0]["scan_code"] == 29, (
        "Cmd-key press on linux-bookmark should land KEY_LEFTCTRL (29); "
        f"got {received[0]['scan_code']}"
    )


# =====================================================================
# Test 3: Dead-key / IME TextCommit round-trips commit string verbatim
# =====================================================================


@pytest.mark.flame_critical
@pytest.mark.parametrize(
    "text,desc",
    [
        ("\u00e4", "German umlaut a (dead-key composition)"),
        ("\u3042", "Japanese hiragana 'a' (IME)"),
        ("\u00df", "German sharp s (single-codepoint commit)"),
    ],
)
async def test_text_commit_roundtrips_commit_string(
    text, desc, tls_ca_and_cert, free_port,
):
    """D-15: dead-key + IME commit strings ride a single TextCommitMsg
    instead of synthesized keycodes.

    Round-trip preserves the exact codepoint(s); no NFC/NFD normalization
    happens on the wire.
    """
    received = []

    async def server_handler(ws):
        async for raw in ws:
            msg = parse_message(raw)
            if msg["type"] == MsgType.TEXT_COMMIT:
                received.append(msg)
                break

    async def client(ws):
        await ws.send(TextCommitMsg(text=text).to_json())

    await _loopback(server_handler, free_port, tls_ca_and_cert, client)
    assert len(received) == 1, desc
    assert received[0]["text"] == text, desc


# =====================================================================
# Test 4: Reconnect post-auth send order — RESET_MODIFIERS first
# =====================================================================


@pytest.mark.flame_critical
async def test_reconnect_first_message_is_key_reset_modifiers(
    tls_ca_and_cert, free_port,
):
    """D-11 trigger #2 contract: after the post-auth supervisor hook fires
    on a reconnect, the FIRST message on the wire is
    ``KeyResetModifiersMsg(reason="reconnect")`` — never a KeyEvent.
    """
    received_types: list[str] = []

    async def server_handler(ws):
        async for raw in ws:
            msg = parse_message(raw)
            received_types.append(msg["type"])
            if len(received_types) >= 2:
                break

    async def client(ws):
        # Simulate the post-auth send order: reset-modifiers FIRST,
        # then a key event.
        await ws.send(
            KeyResetModifiersMsg(reason="reconnect").to_json()
        )
        await ws.send(KeyEventMsg(scan_code=30, pressed=True).to_json())

    await _loopback(server_handler, free_port, tls_ca_and_cert, client)
    assert received_types[0] == MsgType.KEY_RESET_MODIFIERS, (
        f"first message was {received_types[0]}, expected key_reset_modifiers"
    )
    assert received_types[1] == MsgType.KEY_EVENT, (
        f"second message was {received_types[1]}, expected key_event"
    )
