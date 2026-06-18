"""STAB-05 + D-10 — client-side characterization BEFORE Plan 12 extraction.

Plan 12 extracts 5 modules from client/main.py:
    client/app.py, client/main_window.py, client/tab_manager.py,
    client/session_view.py, client/connection_supervisor.py

If any of those extractions breaks the handshake path, this test trips —
that's the contract. Plan 12 does NOT modify this file; zero test-code
delta is allowed across extraction (D-10 / checker BLOCKER #4).

Why the handshake is driven via ClientFSM directly (not ClientProtocol.connect):
    ClientProtocol.connect() spawns a Qt-adjacent thread and runs an
    asyncio event loop inside it. Driving that from a headless test is
    possible but flaky (needs pytest-qt, QApplication instantiation, and
    cross-thread event-loop teardown). Instead this test instantiates a
    *real* ClientProtocol (so imports, attribute set, and fsm wiring are
    all exercised) and drives proto.fsm.send(event) at each stage of the
    observed message exchange. That proves the FSM behavior that Plan 12
    must preserve without depending on Qt.

VALIDATION.md Wave 0: this test is the D-10 client-side safety net for
Plan 12 (mirrors test_server_bootstrap.py for Plans 10-11).
"""
from __future__ import annotations

import asyncio
import ssl

import pytest
import websockets

from common.messages import (
    AuthRequest, AuthResponse, AuthResult,
    ClientHelloMsg, ServerHelloMsg, MsgType, parse_message,
)


def _server_ssl_ctx(cert_pem, key_pem) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_pem), str(key_pem))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


async def _full_handshake_server_handler(websocket):
    """Minimal server handler: AuthRequest → AuthResult(success) → ServerHello.

    Mirrors the protocol sequence a real server/main.py would emit in
    mode='none' + full encoder setup; no SessionRuntime is required here
    because this test's scope is the CLIENT side.
    """
    await websocket.send(
        AuthRequest(challenge="abc", auth_methods=["local"]).to_json()
    )
    _ = await asyncio.wait_for(websocket.recv(), timeout=2.0)   # AuthResponse
    await websocket.send(
        AuthResult(success=True, message="ok").to_json()
    )
    _ = await asyncio.wait_for(websocket.recv(), timeout=2.0)   # ClientHelloMsg
    await websocket.send(
        ServerHelloMsg(
            screen_width=1920, screen_height=1080,
        ).to_json()
    )
    # Hold the socket open a moment so the client's final FSM transition
    # can land before the server disappears.
    await asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_client_bootstraps_and_connects(tls_ca_and_cert, free_port):
    """Boot a real ClientProtocol, drive it against a loopback handshake
    server, and assert the FSM reaches 'streaming' at the end.

    This is the D-10 before/after gate for Plan 12's extraction.
    """
    from client.protocol import ClientProtocol

    srv_ctx = _server_ssl_ctx(
        tls_ca_and_cert["server_cert"], tls_ca_and_cert["server_key"]
    )

    async with websockets.serve(
        _full_handshake_server_handler, "127.0.0.1", free_port, ssl=srv_ctx,
    ):
        proto = ClientProtocol()
        # Fresh ClientProtocol starts in 'disconnected' — pin this contract.
        assert proto.fsm.current_state.id == "disconnected"

        # Drive FSM through the connect request
        proto.fsm.send("connect_requested")
        assert proto.fsm.current_state.id == "handshaking"

        cli_ctx = ssl.create_default_context(
            cafile=str(tls_ca_and_cert["ca_cert"])
        )
        async with websockets.connect(
            f"wss://127.0.0.1:{free_port}", ssl=cli_ctx,
        ) as ws:
            # TLS + WS handshake succeeded
            proto.fsm.send("tls_ok")
            assert proto.fsm.current_state.id == "authenticating"

            # Receive AuthRequest
            auth_req = parse_message(
                await asyncio.wait_for(ws.recv(), timeout=2.0)
            )
            assert auth_req.get("type") == MsgType.AUTH_REQUEST

            # Send AuthResponse
            await ws.send(
                AuthResponse(
                    username="alice", credential="x",
                ).to_json()
            )

            # Receive AuthResult(success=True)
            auth_res = parse_message(
                await asyncio.wait_for(ws.recv(), timeout=2.0)
            )
            assert auth_res.get("success") is True
            proto.fsm.send("auth_ok")
            assert proto.fsm.current_state.id == "capability_exchange"

            # Send ClientHello
            await ws.send(
                ClientHelloMsg(
                    screen_width=1920, screen_height=1080,
                ).to_json()
            )

            # Receive ServerHello
            hello = parse_message(
                await asyncio.wait_for(ws.recv(), timeout=2.0)
            )
            assert hello.get("type") == MsgType.SERVER_HELLO
            proto.fsm.send("hello_received")

        # Streaming reached — this is the D-10 contract Plan 12 must preserve.
        assert proto.fsm.current_state.id == "streaming"


@pytest.mark.asyncio
async def test_client_fsm_reaches_streaming_on_server_hello(
    tls_ca_and_cert, free_port,
):
    """Narrower belt-and-suspenders guard: the FSM transition table
    alone (no network traffic) takes the documented happy-path events
    disconnected → handshaking → authenticating → capability_exchange →
    streaming. Catches the specific failure mode where a Plan 12
    extraction accidentally re-wires proto.fsm to a new instance that
    loses the initial state seed (e.g. by constructing two ClientFSMs).
    """
    from client.protocol import ClientProtocol

    proto = ClientProtocol()
    # Start state: disconnected (ClientFSM's initial state)
    assert proto.fsm.current_state.id == "disconnected"

    for ev in ("connect_requested", "tls_ok", "auth_ok", "hello_received"):
        proto.fsm.send(ev)

    assert proto.fsm.current_state.id == "streaming"


@pytest.mark.asyncio
async def test_client_fsm_handles_tls_failed_path():
    """Failure-path guard: if the TLS handshake fails after connect_requested,
    the FSM transitions handshaking → reconnecting (not streaming). Catches
    Plan 12 extractions that accidentally remove the reconnecting path,
    which would strand the client in 'handshaking' on a TLS hiccup."""
    from client.protocol import ClientProtocol

    proto = ClientProtocol()
    proto.fsm.send("connect_requested")
    assert proto.fsm.current_state.id == "handshaking"
    proto.fsm.send("tls_failed")
    assert proto.fsm.current_state.id == "reconnecting"


@pytest.mark.asyncio
async def test_client_protocol_has_fsm_attribute():
    """Smoke guard: Plan 12's module split must NOT rename or remove
    ClientProtocol.fsm — it is the wire-contract anchor for HealthPing's
    client_state field (Plan 08) and the Plan 11 observability ratchet.

    Also sanity-check that set_insecure_skip_verify exists (SEC-01 dev
    hatch) and that Plan 12 preserves it as public API."""
    from client.protocol import ClientProtocol

    proto = ClientProtocol()
    assert hasattr(proto, "fsm"), (
        "ClientProtocol.fsm is the serialization source for HealthPing — "
        "Plan 12 extraction must NOT remove or rename this attribute."
    )
    assert hasattr(proto, "set_insecure_skip_verify"), (
        "SEC-01 dev escape hatch is public API and must survive extraction."
    )
    # FSM is initialized in 'disconnected' — contract for Plan 08's
    # initial HealthPing emission.
    assert proto.fsm.current_state.id == "disconnected"
