#!/usr/bin/env python3
"""
Teraguchi Server - Linux Remote Desktop Server v4

PCoIP / HP Anyware-like remote desktop with:
- PAM authentication (Linux system users, LDAP, FreeIPA)
- Per-user X sessions via Xvfb (sessions persist across disconnections)
- H.264/H.265/AV1 video encoding with GPU acceleration
- YUV 4:4:4 chroma support
- Wacom pen/tablet pressure injection via uinput
- Audio streaming via PulseAudio/PipeWire
- Multi-monitor support
- Adaptive quality control
- Hybrid TCP+UDP+QUIC transport

Auth modes:
  --auth-mode pam    Authenticate via PAM (PCoIP-like, per-user sessions)
  --auth-mode local  Legacy JSON user database, single shared display
  --auth-mode none   No authentication, single shared display

Usage:
    sudo python -m server.main --auth-mode pam
    python -m server.main --auth-mode none --verbose
"""

import asyncio
import argparse
import json
import logging
import os
import signal
import ssl
import sys
from dataclasses import asdict
from typing import Dict, Optional

import websockets
from websockets.server import WebSocketServerProtocol

sys.path.insert(0, ".")
from common.messages import (
    AuthRequest, AuthResult, MonitorListMsg, MsgType,
    QualitySettings, ServerHelloMsg, parse_message,
)
from common.logging import configure as _configure_logging
from server.platform_backends import IS_MACOS
from server.video_encoder import check_ffmpeg_available, detect_encoders
from server.auth import Authenticator
from server.bootstrap import (
    build_color_caps,
    check_system_dependencies,
    create_tls_context,
)
from server.status_endpoint import make_status_handler
# D-11 / Plan 01-11 Task 1: ClientSession moved to its own module. Re-export
# here so existing callers (``from server.main import ClientSession``) keep
# working unchanged — tests/integration/test_server_bootstrap.py relies on
# that import path.
from server.client_session import ClientSession
# D-11 / Plan 01-11 Task 2: SessionRuntime moved to its own module. Re-export
# here so existing callers (``from server.main import SessionRuntime``) keep
# working unchanged — tests/integration/test_server_bootstrap.py + all tests
# that reference the monolith import path depend on this.
from server.session_runtime import SessionRuntime

logger = logging.getLogger("teraguchi.server")


# ═══════════════════════════════════════════════════════════════
# Server
# ═══════════════════════════════════════════════════════════════
# Note: SessionRuntime (Plan 01-11 Task 2) was extracted to
# server/session_runtime.py, and ClientSession (Plan 01-11 Task 1) to
# server/client_session.py. Both are re-exported at the top of this module
# so the ``from server.main import SessionRuntime|ClientSession`` import
# paths still work for existing callers (tests + any external integrations).

# Global state
auth: Authenticator = None
session_mgr = None   # SessionManager (PAM mode only)
runtimes: Dict[str, SessionRuntime] = {}   # username -> SessionRuntime
default_runtime: Optional[SessionRuntime] = None  # Legacy mode
quality_settings: QualitySettings = QualitySettings()
ffmpeg_caps: dict = {}
available_encoders: dict = {}
running = True
server_args = None  # Parsed CLI args


async def handle_client(websocket: WebSocketServerProtocol):
    """Handle a single client connection lifecycle."""
    addr = websocket.remote_address
    session = ClientSession(websocket)
    logger.info("Client connected: %s", addr)
    runtime = None

    try:
        # STAB-06 / Plan 01-08 — FSM enters `authenticating` as soon as the
        # websocket is open (TLS + WS handshake already passed). Guarded so
        # an unexpected state (e.g. reconnect) doesn't crash the session.
        try:
            session.fsm.send("tls_ok")
        except Exception:
            pass

        # ── Authentication ───────────────────────────────────
        if auth.enabled:
            if auth.mode == "pam":
                # PAM mode: request username + password directly
                # Also accept broker tokens (method="token")
                auth_req = AuthRequest(
                    challenge="",
                    auth_methods=["pam", "token"],
                )
                req_dict = json.loads(auth_req.to_json())
                req_dict["auth_mode"] = "pam"
                await websocket.send(json.dumps(req_dict))

                raw = await asyncio.wait_for(websocket.recv(), timeout=30)
                msg = parse_message(raw)
                if msg.get("type") != MsgType.AUTH_RESPONSE:
                    await websocket.send(
                        AuthResult(success=False, message="Expected auth response").to_json())
                    return

                method = msg.get("method", "pam")
                username = msg.get("username", "")

                if method == "token" and broker_secret:
                    # Broker token authentication
                    token = msg.get("credential", "")
                    verified_user = auth.verify_token(token, broker_secret)
                    success = verified_user is not None
                    if success:
                        username = verified_user
                else:
                    # Standard PAM authentication
                    password = msg.get("credential", "")
                    success = auth.verify_pam(username, password)

                await websocket.send(
                    AuthResult(success=success,
                               message="OK" if success else "Invalid credentials").to_json())
                if not success:
                    return

                session.authenticated = True
                session.username = username
                session.client_screen_width = msg.get("screen_width", 0)
                session.client_screen_height = msg.get("screen_height", 0)
                logger.info("Client screen: %dx%d", session.client_screen_width, session.client_screen_height)
                # STAB-06 / Plan 01-08 — auth succeeded → capability_exchange.
                try:
                    session.fsm.send("auth_ok")
                except Exception:
                    pass

            else:
                # Local mode: challenge-response
                challenge = auth.create_challenge()
                session.challenge = challenge
                auth_req = AuthRequest(challenge=challenge)
                req_dict = json.loads(auth_req.to_json())
                req_dict["auth_mode"] = "local"
                await websocket.send(json.dumps(req_dict))

                raw = await asyncio.wait_for(websocket.recv(), timeout=30)
                msg = parse_message(raw)
                if msg.get("type") != MsgType.AUTH_RESPONSE:
                    await websocket.send(
                        AuthResult(success=False, message="Expected auth response").to_json())
                    return

                success = auth.verify(msg.get("username", ""),
                                      msg.get("credential", ""), challenge)
                await websocket.send(
                    AuthResult(success=success,
                               message="OK" if success else "Invalid credentials").to_json())
                if not success:
                    return

                session.authenticated = True
                session.username = msg.get("username", "unknown")
                session.client_screen_width = msg.get("screen_width", 0)
                session.client_screen_height = msg.get("screen_height", 0)
                logger.info("Client screen: %dx%d", session.client_screen_width, session.client_screen_height)
                # STAB-06 / Plan 01-08 — local-mode auth succeeded.
                try:
                    session.fsm.send("auth_ok")
                except Exception:
                    pass
        else:
            session.authenticated = True
            session.username = "anonymous"
            # STAB-06 / Plan 01-08 — no-auth mode short-circuits through
            # authenticating into capability_exchange.
            try:
                session.fsm.send("auth_ok")
            except Exception:
                pass

        # ── Get or create session runtime ────────────────────
        if auth.mode == "pam" and session_mgr is not None:
            # PAM mode: per-user X session
            user_info = auth.get_user_info(session.username)
            if not user_info:
                await websocket.send(
                    AuthResult(success=False,
                               message=f"System user '{session.username}' not found").to_json())
                return

            from server.session_manager import SessionManager
            # Use client screen size if available, fall back to server args
            client_w = session.client_screen_width or (server_args.width if hasattr(server_args, 'width') else 0)
            client_h = session.client_screen_height or (server_args.height if hasattr(server_args, 'height') else 0)
            user_session = session_mgr.create_session(
                session.username, user_info["uid"], user_info["gid"], user_info["home"],
                width=client_w, height=client_h)

            # Get or create runtime for this user
            if session.username not in runtimes:
                runtime = SessionRuntime(
                    display=user_session.display,
                    username=session.username,
                    quality=quality_settings,
                    ffmpeg_caps=ffmpeg_caps,
                    available_encoders=available_encoders,
                    no_audio=server_args.no_audio,
                    no_clipboard=server_args.no_clipboard,
                    sw_only=server_args.sw_only,
                    monitor_index=server_args.monitor,
                    jpeg_quality=server_args.quality,
                    uid=user_info["uid"],
                    gid=user_info["gid"],
                    home_dir=user_info["home"],
                    pen_tablet=user_session.pen_tablet,
                )
                runtime.set_event_loop(asyncio.get_event_loop())
                runtimes[session.username] = runtime
            else:
                runtime = runtimes[session.username]

            logger.info("User %s → session %s", session.username, user_session.display)

        else:
            # Legacy mode: single shared runtime
            runtime = default_runtime

        if runtime is None:
            logger.error("No runtime available")
            return

        session.runtime = runtime

        # ── Send server hello ────────────────────────────────
        monitors = [asdict(m) for m in runtime.capture.list_monitors()]
        encoder_backend = runtime.encoder.active_backend if runtime.encoder else ""

        # Phase 2 D-03: run the hardware capability probe and advertise the
        # ServerColorCaps payload so the client renders the '10-bit: <state>'
        # badge. Gated POST-auth (see T-02-10) because this callsite fires
        # only after the PAM handshake above succeeds.
        color_caps = build_color_caps()

        hello_kwargs = dict(
            screen_width=runtime.capture.width,
            screen_height=runtime.capture.height,
            monitors=monitors,
            supports_h264=ffmpeg_caps.get("h264", False),
            supports_h265=ffmpeg_caps.get("h265", False),
            supports_av1=ffmpeg_caps.get("av1", False),
            supports_yuv444=ffmpeg_caps.get("h264_444", False),
            supports_audio=runtime.audio is not None and runtime.audio.available,
            supports_pen=True,
            requires_auth=auth.enabled,
            encoder_backend=encoder_backend,
            available_encoders=ffmpeg_caps.get("encoders", {}),
        )
        # color_caps is only a valid kwarg once plan 02-02 extends
        # ServerHelloMsg. Pass it defensively so bootstrap works in the
        # pre-merge worktree AND after 02-02 lands.
        import dataclasses as _dc
        if "color_caps" in {f.name for f in _dc.fields(ServerHelloMsg)}:
            hello_kwargs["color_caps"] = color_caps

        hello = ServerHelloMsg(**hello_kwargs)
        await websocket.send(hello.to_json())

        mon_msg = MonitorListMsg(monitors=monitors)
        await websocket.send(mon_msg.to_json())

        # Register client
        runtime.add_client(websocket, session)
        session.start_sender()

        # ── Process messages ─────────────────────────────────
        async for message in websocket:
            if isinstance(message, str):
                try:
                    msg = parse_message(message)
                    runtime.handle_input(session, msg)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from %s", addr)
                except Exception as e:
                    logger.error("Error from %s: %s", addr, e)

    except asyncio.TimeoutError:
        logger.warning("Client %s: auth timeout", addr)
    except websockets.exceptions.ConnectionClosed:
        logger.info("Client disconnected: %s", addr)
    except Exception as e:
        logger.error("Client error %s: %s", addr, e)
    finally:
        # STAB-06 / Plan 01-08 — drive FSM to draining/closed on disconnect.
        try:
            session.fsm.send("ws_closed")
        except Exception:
            pass
        session.stop()
        if runtime:
            runtime.remove_client(websocket)
        logger.info("Client removed: %s (%s)", addr, session.username)


broker_secret: str = ""  # Shared secret for broker token verification


# HTTP status endpoint lives in server/status_endpoint.py; handle_http is
# constructed here with lambdas that read the current module globals.
handle_http = make_status_handler(
    runtimes_getter=lambda: runtimes,
    default_runtime_getter=lambda: default_runtime,
)


async def run_server(host: str, port: int, tls_context: Optional[ssl.SSLContext]):
    """Start the WebSocket server."""
    global running

    logger.info("Starting Teraguchi server on %s:%d", host, port)
    if tls_context:
        logger.info("TLS enabled")
    if auth.mode == "pam":
        logger.info("PAM authentication — per-user X sessions")
    elif auth.mode == "local":
        logger.info("Local authentication — shared display")
    else:
        logger.info("No authentication — shared display")

    # Signal handling
    stop = asyncio.Future()

    def signal_handler():
        global running
        running = False
        if not stop.done():
            stop.set_result(None)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    async with websockets.serve(
        handle_client, host, port,
        ssl=tls_context,
        max_size=50 * 1024 * 1024,
        ping_interval=20,
        ping_timeout=30,
        process_request=handle_http,
    ):
        logger.info("Server ready. Waiting for connections...")
        await stop

    running = False


def main():
    global auth, session_mgr, default_runtime, quality_settings
    global ffmpeg_caps, available_encoders, server_args, broker_secret

    parser = argparse.ArgumentParser(description="Teraguchi Remote Desktop Server")
    parser.add_argument("--host", default="0.0.0.0", help="Listen address")
    parser.add_argument("--port", type=int, default=443, help="Listen port")
    parser.add_argument("--fps", type=int, default=30, help="Target FPS")
    parser.add_argument("--quality", type=int, default=60, help="JPEG quality (fallback)")
    parser.add_argument("--monitor", type=int, default=1, help="Monitor index (0=all)")
    parser.add_argument("--codec", choices=["h264", "h265", "av1", "jpeg"], default="h264")
    parser.add_argument("--chroma", choices=["yuv420", "yuv422", "yuv444"], default="yuv444")
    parser.add_argument("--lossless", action="store_true")
    parser.add_argument("--max-bandwidth", type=float, default=50.0, help="Max Mbps")
    parser.add_argument("--tls-cert", help="TLS certificate file")
    parser.add_argument("--tls-key", help="TLS key file")
    parser.add_argument("--sw-only", action="store_true", help="Software encoding only")
    parser.add_argument("--verbose", "-v", action="store_true")

    # Auth
    auth_group = parser.add_argument_group("authentication")
    auth_group.add_argument("--auth-mode", choices=["pam", "local", "none"],
                            default="pam",
                            help="pam=Linux users (PCoIP-like), local=JSON db, none=disabled")
    auth_group.add_argument("--no-auth", action="store_true",
                            help="Shortcut for --auth-mode none")
    auth_group.add_argument("--add-user", metavar="USERNAME",
                            help="Add a local user and exit")

    # Session
    sess_group = parser.add_argument_group("session")
    sess_group.add_argument("--width", type=int, default=1920,
                            help="Virtual display width (PAM mode)")
    sess_group.add_argument("--height", type=int, default=1080,
                            help="Virtual display height (PAM mode)")
    sess_group.add_argument("--dpi", type=int, default=96,
                            help="Virtual display DPI (PAM mode)")

    # Broker integration
    parser.add_argument("--broker-secret", default="",
                        help="Path to shared broker signing secret file")

    # Features
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--no-clipboard", action="store_true")

    # OBS-05 / Plan 01-15: diagnostic bundle export. Presence-only flag with
    # optional path — `--diag-bundle` alone writes to
    # ~/teraguchi-diag-<ts>.zip; `--diag-bundle /tmp/x.zip` writes there.
    # The short-circuit MUST run before any auth/root/network setup so
    # bundle export stays usable on a broken install.
    parser.add_argument(
        "--diag-bundle",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="Export a diagnostic bundle zip and exit (OBS-05). "
             "Default path: ~/teraguchi-diag-<timestamp>.zip",
    )

    args = parser.parse_args()
    server_args = args

    # OBS-05 short-circuit — runs BEFORE logging config so a misconfigured
    # log path (e.g. permission-denied file handler) can't prevent bundle
    # export. Also BEFORE PAM/root guard so the bundle is available even
    # without sudo.
    if args.diag_bundle is not None:
        from common.diagnostic_bundle import build_bundle
        path = build_bundle(args.diag_bundle or "", tier="server")
        print(f"Diagnostic bundle: {path}")
        sys.exit(0)

    # OBS-01: route all logging (stdlib + structlog) through the canonical
    # processor chain. phase="server" is bound into contextvars so every
    # emit carries it (CONTEXT.md §"Claude's Discretion" line 83).
    _configure_logging(phase="server", verbose=args.verbose)

    if args.no_auth:
        args.auth_mode = "none"

    # macOS can't do the PAM / Xvfb / per-user session flow — it only
    # has the host's single logged-in user, captured directly by SCK.
    # Force legacy single-display mode with no-auth or local-file auth.
    if IS_MACOS and args.auth_mode == "pam":
        logger.warning("PAM auth mode is Linux-only; forcing --auth-mode none "
                       "on macOS. Use --auth-mode local for username/password.")
        args.auth_mode = "none"

    # Handle --add-user (local mode only)
    if args.add_user:
        import getpass
        a = Authenticator(mode="local")
        password = getpass.getpass(f"Password for '{args.add_user}': ")
        a.add_user(args.add_user, password)
        print(f"User '{args.add_user}' added/updated.")
        return

    # PAM mode requires root
    if args.auth_mode == "pam" and os.geteuid() != 0:
        logger.error("PAM auth mode requires root. Run with sudo or as root.")
        logger.error("  sudo python -m server.main --auth-mode pam")
        logger.error("Or use --auth-mode none for testing without auth.")
        sys.exit(1)

    # Check system dependencies
    check_system_dependencies()

    # Check FFmpeg capabilities
    ffmpeg_caps = check_ffmpeg_available()
    available_encoders = detect_encoders()
    logger.info("FFmpeg: %s", {k: v for k, v in ffmpeg_caps.items() if k != "encoders"})

    hw_backends = ffmpeg_caps.get("hw_backends", [])
    if hw_backends:
        logger.info("GPU encoding: %s", ", ".join(hw_backends))

    # Quality settings
    quality_settings = QualitySettings(
        quality_bias=0.5,
        max_fps=args.fps,
        max_bandwidth_mbps=args.max_bandwidth,
        codec=args.codec,
        chroma=args.chroma,
        force_lossless=args.lossless,
        enable_audio=not args.no_audio,
    )

    # Load broker secret (if configured)
    if args.broker_secret and os.path.exists(args.broker_secret):
        with open(args.broker_secret) as f:
            broker_secret = f.read().strip()
        logger.info("Loaded broker signing secret from %s", args.broker_secret)

    # Initialize auth
    auth = Authenticator(mode=args.auth_mode)

    # Initialize session manager (PAM mode) or default runtime (legacy)
    if args.auth_mode == "pam":
        from server.session_manager import SessionManager
        session_mgr = SessionManager(
            width=args.width, height=args.height, dpi=args.dpi)
        logger.info("Session manager ready (per-user Xvfb sessions)")
        # Runtimes are created on-demand when users authenticate
    else:
        # Legacy mode: single runtime on the host's display.
        display = os.environ.get("DISPLAY", ":0")
        if IS_MACOS:
            logger.info("Legacy mode: capturing host desktop via ScreenCaptureKit")
        else:
            logger.info("Legacy mode: using DISPLAY=%s", display)
        try:
            default_runtime = SessionRuntime(
                display=display,
                username="shared",
                quality=quality_settings,
                ffmpeg_caps=ffmpeg_caps,
                available_encoders=available_encoders,
                no_audio=args.no_audio,
                no_clipboard=args.no_clipboard,
                sw_only=args.sw_only,
                monitor_index=args.monitor,
                jpeg_quality=args.quality,
            )
        except Exception as e:
            logger.error("Failed to initialize: %s", e)
            if IS_MACOS:
                logger.error(
                    "On macOS this usually means Screen Recording permission "
                    "has not been granted. Open System Settings → Privacy & "
                    "Security → Screen & System Audio Recording and enable "
                    "the Python interpreter at %s, then re-run.",
                    sys.executable,
                )
            else:
                logger.error("Make sure DISPLAY is set and accessible.")
            sys.exit(1)

    # TLS
    tls_context = None
    if args.tls_cert and args.tls_key:
        tls_context = create_tls_context(args.tls_cert, args.tls_key)

    # Run
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if default_runtime:
            default_runtime.set_event_loop(loop)
        loop.run_until_complete(run_server(args.host, args.port, tls_context))
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        # Clean up all runtimes
        for rt in runtimes.values():
            rt.stop()
        if default_runtime:
            default_runtime.stop()
        # Destroy all X sessions
        if session_mgr:
            session_mgr.destroy_all()
        logger.info("Server shutdown complete")


if __name__ == "__main__":
    main()
