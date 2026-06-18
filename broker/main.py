#!/usr/bin/env python3
"""
Teraguchi Connection Broker

Authenticates users via PAM/FreeIPA, checks group membership,
and redirects clients to an available Flame workstation.

The broker does NOT relay media — clients connect directly
to the assigned server after receiving a signed token.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import ssl
import sys
import time
from pathlib import Path
from typing import Optional

import websockets
from websockets.server import WebSocketServerProtocol

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from common.messages import (
    MsgType, AuthRequest, AuthResult, parse_message,
)
from broker.freeipa import FreeIPAClient
from broker.pool import MachinePool
from broker.tokens import generate_token
from broker.admin import AdminServer
from common.logging import configure as _configure_logging

logger = logging.getLogger("teraguchi.broker")

# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

DEFAULT_CONFIG = "/etc/teraguchi/broker.yml"
DEFAULT_SECRET = "/etc/teraguchi/broker.secret"
DEFAULT_ASSIGNMENTS = "/var/log/teraguchi/assignments.yml"


def load_config(path: str) -> dict:
    """Load broker YAML config."""
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


def load_secret(path: str) -> str:
    """Load shared signing secret."""
    with open(path) as f:
        return f.read().strip()


# ═══════════════════════════════════════════════════════════════
# Broker State
# ═══════════════════════════════════════════════════════════════

pool: Optional[MachinePool] = None
ipa: Optional[FreeIPAClient] = None
admin_server: Optional[AdminServer] = None
signing_secret: str = ""
required_group: str = "teraguchi-users"
admin_group: str = "teraguchi-admins"
group_cache: dict = {}
config_path: str = ""


# ═══════════════════════════════════════════════════════════════
# Client Handler
# ═══════════════════════════════════════════════════════════════

async def handle_client(websocket: WebSocketServerProtocol):
    """Handle a broker client connection."""
    addr = websocket.remote_address

    logger.info("Client connected: %s", addr)

    try:
        # ── Step 1: Authenticate via PAM ─────────────────
        auth_req = {
            "type": MsgType.AUTH_REQUEST,
            "auth_methods": ["pam"],
            "auth_mode": "pam",
            "challenge": "",
            "salt": "",
        }
        await websocket.send(json.dumps(auth_req))

        raw = await asyncio.wait_for(websocket.recv(), timeout=30)
        msg = parse_message(raw)

        if msg.get("type") != MsgType.AUTH_RESPONSE:
            await websocket.send(
                AuthResult(success=False, message="Expected auth response").to_json())
            return

        username = msg.get("username", "")
        password = msg.get("credential", "")

        # PAM verify
        try:
            from server.pam_auth import PAMAuthenticator
            pam = PAMAuthenticator()
            success = pam.authenticate(username, password)
        except Exception as e:
            logger.error("PAM auth error: %s", e)
            success = False

        if not success:
            await websocket.send(
                AuthResult(success=False, message="Invalid credentials").to_json())
            logger.warning("Auth failed: %s from %s", username, addr)
            return

        logger.info("Auth success: %s", username)

        # ── Step 2: Check FreeIPA group membership ───────
        groups = ipa.get_user_groups_cached(username, group_cache, ttl=60)
        logger.info("User %s groups: %s", username, groups)

        if required_group and required_group not in groups:
            # Also allow admins
            if admin_group not in groups:
                await websocket.send(
                    AuthResult(success=False,
                               message=f"Not a member of {required_group}").to_json())
                logger.warning("Access denied: %s not in %s", username, required_group)
                return

        # Send auth success
        await websocket.send(
            AuthResult(success=True, message="OK").to_json())

        # ── Step 3: Send broker hello ────────────────────
        all_status = pool.get_status()
        is_admin = admin_group in groups
        # Show only machines the user can access (admins see all)
        if is_admin:
            machines_status = all_status
        else:
            machines_status = [m for m in all_status
                               if pool.user_can_access(username, m["name"])]
        broker_hello = {
            "type": MsgType.BROKER_HELLO,
            "broker_name": "Teraguchi Broker",
            "version": "3.0.0",
            "machines": machines_status,
            "is_admin": is_admin,
        }
        await websocket.send(json.dumps(broker_hello))

        # ── Step 4: Wait for assignment request or auto-assign
        # Client may send broker_status or we auto-assign
        raw = await asyncio.wait_for(websocket.recv(), timeout=30)
        msg = parse_message(raw)

        # Handle explicit machine request or auto-assign
        requested_machine = msg.get("machine_name", "")

        if requested_machine:
            if not pool.user_can_access(username, requested_machine):
                await websocket.send(json.dumps({
                    "type": MsgType.BROKER_ASSIGN,
                    "success": False,
                    "message": f"Machine {requested_machine} not available to you",
                }))
                logger.warning("Access denied: %s requested %s", username, requested_machine)
                return
            machine = pool.machines.get(requested_machine)
            if not machine or not machine.healthy:
                await websocket.send(json.dumps({
                    "type": MsgType.BROKER_ASSIGN,
                    "success": False,
                    "message": f"Machine {requested_machine} unavailable",
                }))
                return
        else:
            machine = pool.assign(username, groups)

        if not machine:
            await websocket.send(json.dumps({
                "type": MsgType.BROKER_ASSIGN,
                "success": False,
                "message": "No machines available",
            }))
            return

        # ── Step 5: Generate token and redirect ──────────
        token = generate_token(username, machine.name, signing_secret)

        assign_msg = {
            "type": MsgType.BROKER_ASSIGN,
            "success": True,
            "host": machine.host,
            "port": machine.port,
            "machine_name": machine.name,
            "gpu": machine.gpu,
            "token": token,
            "use_tls": True,
        }
        await websocket.send(json.dumps(assign_msg))
        logger.info("Assigned %s → %s (%s:%d)",
                    username, machine.name, machine.host, machine.port)

    except asyncio.TimeoutError:
        logger.warning("Client %s: timeout", addr)
    except websockets.exceptions.ConnectionClosed:
        logger.info("Client disconnected: %s", addr)
    except Exception as e:
        logger.error("Broker error for %s: %s", addr, e)


# ═══════════════════════════════════════════════════════════════
# Server
# ═══════════════════════════════════════════════════════════════

def create_tls_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.TLS_SERVER_PROTOCOL
                         if hasattr(ssl, "TLS_SERVER_PROTOCOL")
                         else ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)
    return ctx


async def run_broker(host: str, port: int, admin_port: int,
                     tls_context: Optional[ssl.SSLContext]):
    """Start the broker WebSocket server with embedded admin UI."""
    logger.info("Starting Teraguchi broker on %s:%d", host, port)

    stop = asyncio.Future()

    def signal_handler():
        if not stop.done():
            stop.set_result(True)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    pool.start_health_probes()

    # Admin process_request hook — serves admin UI on the same port
    admin_process_request = None
    if admin_server:
        admin_process_request = admin_server.process_request
        logger.info("Admin UI enabled on same port (https://HOST:%d/)", port)

    async with websockets.serve(
        handle_client, host, port,
        ssl=tls_context,
        max_size=1024 * 1024,
        ping_interval=20,
        ping_timeout=30,
        process_request=admin_process_request,
    ):
        logger.info("Broker ready. Waiting for connections...")
        await stop

    pool.stop()
    logger.info("Broker shutdown complete")


def main():
    parser = argparse.ArgumentParser(description="Teraguchi Connection Broker")
    parser.add_argument("--host", default="0.0.0.0", help="Listen address")
    parser.add_argument("--port", type=int, default=8443, help="Listen port")
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help="Broker config YAML")
    parser.add_argument("--secret", default=DEFAULT_SECRET,
                        help="Shared signing secret file")
    parser.add_argument("--tls-cert", help="TLS certificate")
    parser.add_argument("--tls-key", help="TLS key")
    parser.add_argument("--required-group", default="teraguchi-users",
                        help="FreeIPA group required for access (empty=no check)")
    parser.add_argument("--admin-group", default="teraguchi-admins",
                        help="FreeIPA admin group")
    parser.add_argument("--admin-port", type=int, default=8080,
                        help="Admin UI HTTP port")
    parser.add_argument("--assignments-file", default=DEFAULT_ASSIGNMENTS,
                        help="Writable path for assignment state")
    parser.add_argument("--verbose", "-v", action="store_true")

    # FreeIPA
    parser.add_argument("--ipa-servers",
                        default="ldap://dxs-salt.the-dxs.com,ldap://dxs-salty.the-dxs.com",
                        help="Comma-separated FreeIPA LDAP URIs")
    parser.add_argument("--ipa-base-dn", default="dc=the-dxs,dc=com",
                        help="LDAP base DN")

    args = parser.parse_args()

    # OBS-01: route all logging (stdlib + structlog) through the canonical
    # processor chain. phase="broker" is bound into contextvars so every
    # emit carries it (CONTEXT.md §"Claude's Discretion" line 83).
    _configure_logging(phase="broker", verbose=args.verbose)

    global pool, ipa, admin_server, signing_secret, required_group, admin_group, config_path

    # Load config
    config_path = args.config
    config = {}
    if os.path.exists(config_path):
        config = load_config(config_path)
        logger.info("Loaded config: %s", config_path)
    else:
        logger.warning("No config file at %s — using defaults", config_path)

    # Load signing secret
    if os.path.exists(args.secret):
        signing_secret = load_secret(args.secret)
        logger.info("Loaded signing secret")
    else:
        import secrets as secrets_mod
        signing_secret = secrets_mod.token_hex(32)
        logger.warning("No secret file — generated ephemeral secret (tokens won't survive restart)")

    # Machine pool
    machines = config.get("machines", [])
    if not machines:
        logger.error("No machines configured. Create %s with a 'machines' list.", args.config)
        sys.exit(1)

    # Load assignments: prefer separate file, fall back to config
    assignments_path = args.assignments_file
    assignments = {}
    if os.path.exists(assignments_path):
        import yaml as _yaml
        with open(assignments_path) as f:
            adata = _yaml.safe_load(f) or {}
        assignments = adata.get("assignments", {})
        logger.info("Loaded assignments from %s (%d users)", assignments_path, len(assignments))
    elif config.get("assignments"):
        assignments = config["assignments"]
        logger.info("Loaded assignments from config (%d users)", len(assignments))

    pool = MachinePool(machines, assignments=assignments)

    # FreeIPA
    ipa_servers = args.ipa_servers.split(",")
    ipa = FreeIPAClient(ipa_servers, args.ipa_base_dn)

    required_group = args.required_group
    admin_group = args.admin_group

    # Admin UI
    admin_server = AdminServer(pool, ipa, config_path, admin_group,
                               assignments_path=assignments_path)

    # TLS
    tls_context = None
    if args.tls_cert and args.tls_key:
        tls_context = create_tls_context(args.tls_cert, args.tls_key)
        logger.info("TLS enabled")

    # Run
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_broker(args.host, args.port, args.admin_port, tls_context))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
