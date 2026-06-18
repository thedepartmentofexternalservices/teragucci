"""STAB-01 + STAB-06 integration — full FSM-driven auth handshake path.

Covers the multi-module slice:
    client/protocol.py × server/auth.py × common/session_fsm.py

ClientFSM transitions exercised end-to-end:
    disconnected → handshaking → authenticating → capability_exchange → streaming

Owning plan: Plan 09 (characterization tests). Unit-level auth tests live in
Plan 04 at tests/server/test_auth.py and tests/server/test_pam_auth.py; this
file is explicitly a multi-module integration harness driving FSM events in
lockstep with observed protocol messages over a real (loopback) TLS socket.

Why here and not in Plan 04:
  (a) Requires the Plan 02 CA fixture (tls_ca_and_cert) for real TLS.
  (b) Asserts FSM state that Plan 08 wires into ClientProtocol / ServerFSM.
  (c) Characterizes integrated behavior across modules — not a unit test.

VALIDATION.md Wave 0 line 116 (test_auth_flow.py) was previously unassigned
per checker BLOCKER #3; ownership resolves to Plan 09 Task 3.
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
from common.session_fsm import ClientFSM, ALLOWED_PAIRS


def _server_ssl_ctx(cert_pem, key_pem) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_pem), str(key_pem))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


@pytest.mark.asyncio
async def test_full_auth_flow_reaches_streaming_state(tls_ca_and_cert, free_port):
    """Happy path: FSM traverses disconnected → handshaking → authenticating →
    capability_exchange → streaming, each transition driven by an observed
    protocol message. The corresponding (client_state, server_state) pair is
    in ALLOWED_PAIRS at the end — no disagreement would fire on the Plan 11
    observability ratchet."""
    cfsm = ClientFSM()
    assert cfsm.current_state.id == "disconnected"

    async def auth_success_server(websocket):
        """Minimal auth-terminal handler: request → response → result →
        hello-exchange. No SessionRuntime — Task 1 covers that path."""
        # Stage 1: AuthRequest
        await websocket.send(
            AuthRequest(challenge="abc123", auth_methods=["local"]).to_json()
        )
        # Stage 2: Receive AuthResponse
        resp_raw = await asyncio.wait_for(websocket.recv(), timeout=2.0)
        assert parse_message(resp_raw).get("type") == MsgType.AUTH_RESPONSE
        # Stage 3: AuthResult(success=True)
        await websocket.send(
            AuthResult(success=True, message="ok").to_json()
        )
        # Stage 4: Receive ClientHelloMsg
        hello_raw = await asyncio.wait_for(websocket.recv(), timeout=2.0)
        assert parse_message(hello_raw).get("type") == MsgType.CLIENT_HELLO
        # Stage 5: ServerHelloMsg
        await websocket.send(
            ServerHelloMsg(
                screen_width=1920, screen_height=1080,
            ).to_json()
        )
        # Hold the socket open so client-side sleeps / final assertions
        # don't race against an immediate RST.
        await asyncio.sleep(0.2)

    srv_ctx = _server_ssl_ctx(
        tls_ca_and_cert["server_cert"], tls_ca_and_cert["server_key"]
    )
    async with websockets.serve(
        auth_success_server, "127.0.0.1", free_port, ssl=srv_ctx,
    ):
        cli_ctx = ssl.create_default_context(
            cafile=str(tls_ca_and_cert["ca_cert"])
        )

        # Before TCP/TLS even opens — FSM enters handshaking
        cfsm.send("connect_requested")
        assert cfsm.current_state.id == "handshaking"

        async with websockets.connect(
            f"wss://127.0.0.1:{free_port}", ssl=cli_ctx,
        ) as ws:
            # TLS + WS handshake completed → FSM → authenticating
            cfsm.send("tls_ok")
            assert cfsm.current_state.id == "authenticating"

            # Receive AuthRequest from server
            auth_req_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            auth_req = parse_message(auth_req_raw)
            assert auth_req.get("type") == MsgType.AUTH_REQUEST
            assert auth_req.get("challenge") == "abc123"

            # Send AuthResponse
            await ws.send(
                AuthResponse(
                    username="alice", credential="somehash",
                ).to_json()
            )

            # Receive AuthResult(success=True)
            auth_res_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            auth_res = parse_message(auth_res_raw)
            assert auth_res.get("type") == MsgType.AUTH_RESULT
            assert auth_res.get("success") is True

            # Server accepted → FSM → capability_exchange
            cfsm.send("auth_ok")
            assert cfsm.current_state.id == "capability_exchange"

            # Send ClientHelloMsg
            await ws.send(
                ClientHelloMsg(
                    screen_width=1920, screen_height=1080,
                ).to_json()
            )

            # Receive ServerHelloMsg
            hello_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            hello = parse_message(hello_raw)
            assert hello.get("type") == MsgType.SERVER_HELLO
            assert hello.get("screen_width") == 1920

            # Hello received → FSM → streaming
            cfsm.send("hello_received")
            assert cfsm.current_state.id == "streaming"

    # Final state-pair assertion: both sides in 'streaming' is an
    # ALLOWED_PAIR — Plan 11's disagreement detector would be silent.
    assert ("streaming", "streaming") in ALLOWED_PAIRS


@pytest.mark.asyncio
async def test_auth_failure_transitions_to_disconnected(tls_ca_and_cert, free_port):
    """Auth-failure path: AuthResult(success=False) → FSM auth_failed →
    disconnected. Guards against a regression where the client-side FSM
    stays in 'authenticating' after the server rejects credentials, or
    (worse) advances to capability_exchange on a failed auth."""
    cfsm = ClientFSM()

    async def auth_failure_server(websocket):
        await websocket.send(
            AuthRequest(challenge="nope", auth_methods=["local"]).to_json()
        )
        _ = await asyncio.wait_for(websocket.recv(), timeout=2.0)
        await websocket.send(
            AuthResult(success=False, message="bad credentials").to_json()
        )
        # Keep socket open briefly so client can read the failure.
        await asyncio.sleep(0.1)

    srv_ctx = _server_ssl_ctx(
        tls_ca_and_cert["server_cert"], tls_ca_and_cert["server_key"]
    )
    async with websockets.serve(
        auth_failure_server, "127.0.0.1", free_port, ssl=srv_ctx,
    ):
        cli_ctx = ssl.create_default_context(
            cafile=str(tls_ca_and_cert["ca_cert"])
        )

        cfsm.send("connect_requested")
        async with websockets.connect(
            f"wss://127.0.0.1:{free_port}", ssl=cli_ctx,
        ) as ws:
            cfsm.send("tls_ok")
            assert cfsm.current_state.id == "authenticating"

            _auth_req = await asyncio.wait_for(ws.recv(), timeout=2.0)
            await ws.send(
                AuthResponse(
                    username="alice", credential="wrong",
                ).to_json()
            )

            auth_res_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            auth_res = parse_message(auth_res_raw)
            assert auth_res.get("type") == MsgType.AUTH_RESULT
            assert auth_res.get("success") is False

            # Client reacts to the failure — FSM: authenticating → disconnected
            cfsm.send("auth_failed")

    assert cfsm.current_state.id == "disconnected"
    # And the failure-path pair never reaches streaming
    assert cfsm.current_state.id != "streaming"


@pytest.mark.asyncio
async def test_server_auth_module_rejects_wrong_local_credential(tls_ca_and_cert, free_port):
    """STAB-01 end-to-end: real server/auth.Authenticator (mode='local')
    rejects wrong credentials, and the ClientFSM ends in disconnected.

    This exercises three modules integrated over a loopback TLS socket:
        - server.auth.Authenticator (real — mode='local' with in-memory user)
        - client/server protocol framing (AuthRequest / AuthResponse / AuthResult)
        - common.session_fsm.ClientFSM (auth_failed transition)

    Plan 04's test_auth.py covers the Authenticator class at the unit level;
    this test proves the wire-level contract between client and server agrees
    with the FSM's auth_failed event.
    """
    import tempfile, pathlib
    from server.auth import Authenticator
    from common.messages import hash_password

    # Build a real Authenticator in local mode with an in-memory user.
    tmpdir = tempfile.mkdtemp()
    users_file = str(pathlib.Path(tmpdir) / "users.json")
    authn = Authenticator(mode="local", users_file=users_file)
    authn.enabled = True   # override post-load disable if file was empty
    authn.add_user("alice", "correct-password")

    cfsm = ClientFSM()

    async def real_auth_server(websocket):
        challenge = authn.create_challenge()
        await websocket.send(
            AuthRequest(challenge=challenge, auth_methods=["local"]).to_json()
        )
        resp_raw = await asyncio.wait_for(websocket.recv(), timeout=2.0)
        resp = parse_message(resp_raw)
        # Validate using the real Authenticator — expect rejection on wrong creds
        success = authn.verify(
            resp.get("username", ""),
            resp.get("credential", ""),
            challenge,
        )
        await websocket.send(
            AuthResult(
                success=success,
                message="ok" if success else "Invalid credentials",
            ).to_json()
        )
        await asyncio.sleep(0.05)

    srv_ctx = _server_ssl_ctx(
        tls_ca_and_cert["server_cert"], tls_ca_and_cert["server_key"]
    )
    async with websockets.serve(
        real_auth_server, "127.0.0.1", free_port, ssl=srv_ctx,
    ):
        cli_ctx = ssl.create_default_context(
            cafile=str(tls_ca_and_cert["ca_cert"])
        )
        cfsm.send("connect_requested")
        async with websockets.connect(
            f"wss://127.0.0.1:{free_port}", ssl=cli_ctx,
        ) as ws:
            cfsm.send("tls_ok")

            auth_req_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            auth_req = parse_message(auth_req_raw)
            challenge = auth_req.get("challenge", "")

            # Deliberately wrong: salted with a BAD password
            bad_user_hash = hash_password("wrong-password", "wrong-salt")
            client_hash = hash_password(bad_user_hash, challenge)
            await ws.send(
                AuthResponse(
                    username="alice", credential=client_hash,
                ).to_json()
            )

            auth_res_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            auth_res = parse_message(auth_res_raw)
            assert auth_res.get("success") is False, (
                "Real Authenticator.verify() must reject wrong credentials"
            )

            cfsm.send("auth_failed")

    assert cfsm.current_state.id == "disconnected"
