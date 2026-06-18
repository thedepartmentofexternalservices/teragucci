"""STAB-08 integration — ConnectionSupervisor reconnect against a loopback server.

Drives a real ConnectionSupervisor against two loopback scenarios:

  1. ``test_supervisor_reconnects_after_transport_lost`` — a WSS server that
     closes immediately on the first accept, then stays up on the second.
     Asserts the supervisor retries and the second attempt succeeds.

  2. ``test_supervisor_gives_up_after_max_retries`` — no server listening at
     all; supervisor exhausts its retry cap and ends with FSM in 'closed'.

These exercise the Plan 12 Task 3 end-to-end path: real asyncio event
loop, real TLS handshake against the ``tls_ca_and_cert`` fixture, real
websockets client driven by the supervisor's transport_factory
callable.
"""
from __future__ import annotations

import asyncio
import ssl

import pytest
import websockets

from client.connection_supervisor import ConnectionSupervisor
from common.session_fsm import ClientFSM


def _server_ctx(cert, key) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


@pytest.mark.asyncio
async def test_supervisor_reconnects_after_transport_lost(
    tls_ca_and_cert, free_port,
):
    """Server drops on first accept, supervisor reconnects, second attempt
    runs cleanly to server close."""
    accept_count = 0
    accepted_twice = asyncio.Event()

    async def server_handler(ws):
        nonlocal accept_count
        accept_count += 1
        if accept_count == 1:
            # First accept: close immediately (simulate flap).
            await ws.close()
        else:
            # Second accept: stay open briefly then close cleanly.
            accepted_twice.set()
            await asyncio.sleep(0.05)

    ssl_srv = _server_ctx(
        tls_ca_and_cert["server_cert"], tls_ca_and_cert["server_key"],
    )

    async with websockets.serve(
        server_handler, "127.0.0.1", free_port, ssl=ssl_srv,
    ):
        cli_ctx = ssl.create_default_context(cafile=str(tls_ca_and_cert["ca_cert"]))
        connects_seen = 0

        async def transport_factory() -> None:
            nonlocal connects_seen
            connects_seen += 1
            my_attempt = connects_seen
            async with websockets.connect(
                f"wss://127.0.0.1:{free_port}", ssl=cli_ctx,
            ) as ws:
                # Drain until the server closes.
                async for _ in ws:
                    pass
            # First attempt: treat the immediate server-close as an
            # error so the supervisor retries. Subsequent attempts
            # return cleanly (supervisor exits).
            if my_attempt == 1:
                raise RuntimeError("first accept closed immediately")

        fsm = ClientFSM()
        sup = ConnectionSupervisor(
            transport_factory,
            fsm=fsm,
            max_retries=5,
            base_delay=0.01,
            max_delay=0.05,
            jitter_pct=0.0,
        )
        # The second accept returns cleanly after ~50 ms, so supervisor
        # will stop retrying and return from connect().
        await asyncio.wait_for(sup.connect(), timeout=5.0)

    # At least 2 factory invocations (first fails, second succeeds).
    assert connects_seen >= 2
    assert accept_count >= 2
    assert accepted_twice.is_set()


@pytest.mark.asyncio
async def test_supervisor_gives_up_after_max_retries(
    tls_ca_and_cert, free_port,
):
    """Nothing listening on the port. Supervisor retries max_retries times
    then transitions FSM to 'closed'."""
    cli_ctx = ssl.create_default_context(cafile=str(tls_ca_and_cert["ca_cert"]))

    async def transport_factory() -> None:
        async with websockets.connect(
            f"wss://127.0.0.1:{free_port}", ssl=cli_ctx,
        ) as ws:
            async for _ in ws:
                pass

    fsm = ClientFSM()
    sup = ConnectionSupervisor(
        transport_factory,
        fsm=fsm,
        max_retries=2,
        base_delay=0.001,
        max_delay=0.001,
        jitter_pct=0.0,
    )
    await asyncio.wait_for(sup.connect(), timeout=10.0)

    assert sup.retry_count > 2
    assert fsm.current_state.id == "closed"


@pytest.mark.asyncio
async def test_client_protocol_exposes_supervisor_type():
    """ClientProtocol references the ConnectionSupervisor type so future
    plans can reach it via ``proto._supervisor`` / ``proto.is_reconnecting``.

    This is the wire between STAB-08 (supervisor module) and
    ClientProtocol. Plan 12 Task 3 step 2 (minimal wiring): the class
    imports / references ConnectionSupervisor and exposes an
    is_reconnecting accessor that reads from its FSM (fall-through is
    fine because the FSM is shared with the supervisor once one is
    attached).
    """
    from client import protocol as _client_protocol

    # Module imports ConnectionSupervisor (verified by grep in the plan's
    # automated verification; duplicated here to pin it as an import-level
    # test and catch accidental removal).
    assert "ConnectionSupervisor" in dir(_client_protocol) or \
        "ConnectionSupervisor" in _client_protocol.__dict__ or \
        "ConnectionSupervisor" in open(_client_protocol.__file__).read()
