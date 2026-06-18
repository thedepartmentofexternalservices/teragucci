"""SEC-01 integration tests — real TLS cert verification.

No verification opt-out is used anywhere in these tests. A trusted CA
fixture is built at test-time, a server cert is signed under it, and
the client either trusts that CA (positive case) or a different one
(negative case).

Five tests:
  - test_trusted_cert_accepted: cert signed by trusted CA → connects
  - test_untrusted_cert_rejected: cert signed by attacker CA → rejected
  - test_insecure_flag_requires_env_var: double-gate semantics
  - test_insecure_flag_emits_error_log: ERROR log event on insecure mode
  - test_no_cert_none_in_client_broker_common: grep-level regression guard
"""
import asyncio
import logging
import pathlib
import ssl

import pytest
import websockets

from common.tls_opt_out import build_client_ssl_context, insecure_tls_allowed


async def _echo_server(websocket):
    async for msg in websocket:
        await websocket.send(msg)


def _server_ssl_context(cert_pem, key_pem):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_pem), str(key_pem))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


@pytest.mark.asyncio
async def test_trusted_cert_accepted(tls_ca_and_cert, free_port):
    """Client trusting the test CA successfully connects to a server
    presenting a cert signed by that CA (real verification exercised)."""
    srv_ctx = _server_ssl_context(
        tls_ca_and_cert["server_cert"], tls_ca_and_cert["server_key"])
    async with websockets.serve(
            _echo_server, "127.0.0.1", free_port, ssl=srv_ctx):
        client_ctx = build_client_ssl_context(
            insecure_cli_flag=False,
            ca_bundle=str(tls_ca_and_cert["ca_cert"]),
            site_label="test_trusted",
        )
        # check_hostname=True by default. Cert's SAN is 127.0.0.1, matches.
        async with websockets.connect(
                f"wss://127.0.0.1:{free_port}", ssl=client_ctx) as ws:
            await ws.send("hello")
            reply = await asyncio.wait_for(ws.recv(), timeout=2.0)
            assert reply == "hello"


@pytest.mark.asyncio
async def test_untrusted_cert_rejected(
        untrusted_ca_cert, tls_ca_and_cert, free_port):
    """Server presents a cert signed by the 'attacker' CA. Client trusts
    only the 'test' CA → connection MUST be rejected with
    ssl.SSLCertVerificationError (or equivalent SSLError)."""
    srv_ctx = _server_ssl_context(
        untrusted_ca_cert["server_cert"], untrusted_ca_cert["server_key"])
    async with websockets.serve(
            _echo_server, "127.0.0.1", free_port, ssl=srv_ctx):
        client_ctx = build_client_ssl_context(
            insecure_cli_flag=False,
            ca_bundle=str(tls_ca_and_cert["ca_cert"]),  # trusts OTHER CA
            site_label="test_untrusted",
        )
        with pytest.raises((ssl.SSLCertVerificationError, ssl.SSLError)):
            async with websockets.connect(
                    f"wss://127.0.0.1:{free_port}", ssl=client_ctx):
                pass


def test_insecure_flag_requires_env_var(monkeypatch):
    """SEC-01 double-gate: single gate is intentionally insufficient."""
    monkeypatch.delenv("TERAGUCHI_ACCEPT_INSECURE", raising=False)
    assert insecure_tls_allowed(cli_flag=True) is False
    monkeypatch.setenv("TERAGUCHI_ACCEPT_INSECURE", "yes")  # wrong value
    assert insecure_tls_allowed(cli_flag=True) is False
    monkeypatch.setenv("TERAGUCHI_ACCEPT_INSECURE", "1")
    assert insecure_tls_allowed(cli_flag=True) is True
    # env set but flag off — belt-and-suspenders:
    assert insecure_tls_allowed(cli_flag=False) is False


def test_insecure_flag_emits_error_log(monkeypatch, caplog):
    """When the double-gate is active, log an ERROR-level event per call."""
    monkeypatch.setenv("TERAGUCHI_ACCEPT_INSECURE", "1")
    with caplog.at_level(logging.ERROR, logger="common.tls_opt_out"):
        ctx = build_client_ssl_context(
            insecure_cli_flag=True, site_label="test_log")
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False
    assert any(
        "transport.insecure_mode_active" in rec.message
        for rec in caplog.records
    ), f"expected ERROR log, got: {[r.message for r in caplog.records]}"


def test_no_cert_none_in_client_broker_common():
    """Grep-level guard: no unconditional CERT_NONE left outside gated paths."""
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    suspects = []
    for sub in ("client", "common", "broker"):
        for p in (root / sub).rglob("*.py"):
            if "tls_opt_out.py" in str(p):
                # the single gated implementation is intentional
                continue
            try:
                text = p.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            # If the file uses CERT_NONE, it MUST also import/refer to
            # insecure_tls_allowed — otherwise the usage is ungated.
            if "CERT_NONE" in text and "insecure_tls_allowed" not in text:
                suspects.append(str(p))
    assert not suspects, f"CERT_NONE found outside gated path: {suspects}"
