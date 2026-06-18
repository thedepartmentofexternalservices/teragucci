"""STAB-06 — loopback integration: client FSM + server FSM + ping/pong state round-trip.

Plan 01-08 locks the direction:
  - CLIENT sends HealthPing stamped with client_state
  - SERVER sends HealthPong stamped with server_state

These tests exercise the dataclass + FSM wiring end-to-end by standing up a
minimal wss:// loopback server and exchanging a single ping/pong pair. The
fixtures (``tls_ca_and_cert`` + ``free_port``) come from Plan 02's
``tests/integration/conftest.py``.
"""
from __future__ import annotations

import asyncio
import ssl

import pytest
import websockets

from common.messages import HealthPing, HealthPong, MsgType, parse_message
from common.session_fsm import (
    ALLOWED_PAIRS,
    ClientFSM,
    ServerFSM,
    is_state_pair_allowed,
)


def _server_ssl_ctx(cert_pem, key_pem) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_pem), str(key_pem))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


@pytest.mark.asyncio
async def test_health_ping_carries_client_state(tls_ca_and_cert, free_port):
    """CLIENT emits HealthPing with client_state='streaming' — server reads it."""
    cfsm = ClientFSM()
    for ev in ("connect_requested", "tls_ok", "auth_ok", "hello_received"):
        cfsm.send(ev)
    assert cfsm.current_state.id == "streaming"

    received: list[str] = []

    async def server_handler(websocket):
        async for msg in websocket:
            parsed = parse_message(msg)
            if parsed.get("type") == MsgType.HEALTH_PING:
                received.append(parsed.get("client_state", ""))
                break

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
            ping = HealthPing(sequence=1, client_state=cfsm.current_state.id)
            await ws.send(ping.to_json())
            await asyncio.sleep(0.2)

    assert received == ["streaming"]


@pytest.mark.asyncio
async def test_health_pong_carries_server_state(tls_ca_and_cert, free_port):
    """SERVER emits HealthPong with server_state — client reads it."""
    sfsm = ServerFSM()
    for ev in ("tls_ok", "auth_ok", "client_hello"):
        sfsm.send(ev)
    assert sfsm.current_state.id == "streaming"

    async def server_handler(websocket):
        pong = HealthPong(
            ping_timestamp_ms=1700000000000,
            sequence=1,
            server_state=sfsm.current_state.id,
        )
        await websocket.send(pong.to_json())

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
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)

    parsed = parse_message(raw)
    assert parsed["type"] == MsgType.HEALTH_PONG
    assert parsed["server_state"] == "streaming"


def test_state_disagreement_detected():
    """Direct unit-level guard (complements Plan 01-17's log-event integration)."""
    # Both states valid, but the pair isn't allowed.
    assert is_state_pair_allowed("streaming", "bootstrapping") is False
    assert ("streaming", "bootstrapping") not in ALLOWED_PAIRS


def test_allowed_pair_count_matches_table():
    """Belt-and-suspenders — if someone mutates ALLOWED_PAIRS, tests trip."""
    assert len(ALLOWED_PAIRS) == 9
