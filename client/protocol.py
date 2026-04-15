"""
Client-side WebSocket protocol handler v2.

Manages the connection to the Teraguchi server with:
- PAM authentication (send credentials directly for system auth)
- Legacy challenge-response authentication
- Hybrid TCP+UDP transport (UDP for video/audio, TCP for control)
- TLS support (wss://) — recommended for PAM mode
- Auto-reconnect with exponential backoff
- Jitter buffer for smooth UDP playback
"""

import hashlib
import json
import logging
import socket
import threading
import asyncio
import time
from typing import Optional, Callable

import websockets

from common.messages import (
    MsgType, ClientHelloMsg, FrameType,
    decode_video_header, decode_jpeg_header, decode_audio_header,
    VIDEO_HEADER_SIZE, JPEG_HEADER_SIZE, AUDIO_HEADER_SIZE,
    HealthPing, HealthPong, QualitySettings,
    AuthResponse, parse_message,
)


class _BrokerRedirect(Exception):
    """Raised when direct-mode auth detects a broker and needs to redirect."""
    pass
from common.udp_transport import UDPMediaClient, CHANNEL_VIDEO, CHANNEL_AUDIO, FLAG_KEYFRAME
from common.hybrid_transport import HybridClientTransport, TransportMsg
from common.jitter_buffer import JitterBuffer

logger = logging.getLogger(__name__)


class ClientProtocol:
    """
    Hybrid TCP+UDP client with TLS, auth, auto-reconnect, jitter buffer.

    Supports two auth modes:
    - PAM:   Sends username + password directly (over TLS-encrypted WebSocket)
    - Local: Challenge-response hash (legacy)
    """

    def __init__(self):
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._closing = False
        self._auto_reconnect = True
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0

        # Auth
        self._username = ""
        self._password = ""
        self._user_salt = ""

        # TLS
        self._use_tls = False

        # Broker redirect
        self._broker_mode = False
        self._broker_token = ""
        self._redirect_host = ""
        self._redirect_port = 0
        self._preferred_machine = ""  # Remembered selection for reconnects
        self._machine_selection_future: Optional[asyncio.Future] = None

        # UDP transport
        self._udp_client: Optional[UDPMediaClient] = None
        self._hybrid: Optional[HybridClientTransport] = None
        self._jitter_buffer: Optional[JitterBuffer] = None
        self._udp_enabled = True
        self._udp_stats_interval = 5.0

        # Callbacks
        self.on_server_hello: Optional[Callable] = None
        self._screen_size: Optional[tuple] = None
        self.on_video_frame: Optional[Callable] = None
        self.on_jpeg_frame: Optional[Callable] = None
        self.on_audio_frame: Optional[Callable] = None
        self.on_connected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
        self.on_auth_required: Optional[Callable] = None
        self.on_auth_result: Optional[Callable] = None
        self.on_health_ping: Optional[Callable] = None
        self.on_health_stats: Optional[Callable] = None
        self.on_monitor_list: Optional[Callable] = None
        self.on_clipboard: Optional[Callable] = None
        self.on_file_response: Optional[Callable] = None  # file_accept/file_ack/file_cancel
        self.on_usb_response: Optional[Callable] = None  # usb_device_list/attached/detached/error
        self.on_broker_hello: Optional[Callable] = None  # broker_hello with machine list
        self.on_broker_assign: Optional[Callable] = None  # broker_assign with redirect info
        self.on_broker_machine_needed: Optional[Callable] = None  # prompts user to pick a machine
        self.on_cursor_update: Optional[Callable] = None  # cursor shape change

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, host: str, port: int, username: str = "", password: str = "",
                use_tls: bool = False, auto_reconnect: bool = True):
        if self._thread and self._thread.is_alive():
            self.disconnect()

        self._closing = False
        self._broker_mode = False
        self._broker_token = ""
        self._redirect_host = ""
        self._redirect_port = 0
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._auto_reconnect = auto_reconnect
        self._reconnect_delay = 1.0
        self._host = host
        self._port = port

        self._thread = threading.Thread(
            target=self._run_loop, args=(host, port),
            daemon=True, name="teraguchi-client-io")
        self._thread.start()

    def connect_broker(self, host: str, port: int, username: str = "",
                       password: str = "", use_tls: bool = True,
                       auto_reconnect: bool = True):
        """Connect via broker. Authenticates with broker, gets assigned a machine,
        then redirects to that machine with a signed token."""
        if self._thread and self._thread.is_alive():
            self.disconnect()

        self._closing = False
        self._broker_mode = True
        self._broker_token = ""
        self._redirect_host = ""
        self._redirect_port = 0
        self._preferred_machine = ""
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._auto_reconnect = auto_reconnect
        self._reconnect_delay = 1.0
        self._host = host
        self._port = port

        self._thread = threading.Thread(
            target=self._run_loop, args=(host, port),
            daemon=True, name="teraguchi-broker-io")
        self._thread.start()

    def disconnect(self):
        self._closing = True
        self._auto_reconnect = False
        if self._hybrid:
            self._hybrid.stop()
            self._hybrid = None
        if self._jitter_buffer:
            self._jitter_buffer.stop()
            self._jitter_buffer = None
        self._udp_client = None
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._connected = False

    def send_input(self, msg_dict: dict):
        if not self._connected or not self._ws:
            return
        try:
            json_str = json.dumps(msg_dict)
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._ws.send(json_str), self._loop)
        except Exception as e:
            logger.debug("Send error: %s", e)

    def send_binary(self, data: bytes):
        """Send a binary frame to the server (e.g. microphone audio). Thread-safe."""
        if not self._connected or not self._ws:
            return
        try:
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._ws.send(data), self._loop)
        except Exception as e:
            logger.debug("Send binary error: %s", e)

    def send_quality_settings(self, settings: QualitySettings):
        self.send_input(json.loads(settings.to_json()))

    def request_full_frame(self):
        self.send_input({"type": MsgType.REQUEST_FULL_FRAME})

    def select_monitor(self, monitor_id: int):
        self.send_input({"type": MsgType.SELECT_MONITOR, "monitor_id": monitor_id})

    def select_broker_machine(self, machine_name: str):
        """Resolve a pending broker machine selection from the GUI thread.

        Pass empty string to request auto-assignment. Safe to call from any thread.
        """
        loop = self._loop
        fut = self._machine_selection_future
        if loop is None or fut is None:
            # Either no selection pending, or handshake already moved on.
            # Still cache the preference for any next handshake pass.
            self._preferred_machine = machine_name or ""
            return

        def _resolve():
            if not fut.done():
                fut.set_result(machine_name or "")

        try:
            loop.call_soon_threadsafe(_resolve)
        except RuntimeError:
            # Loop stopped — ignore
            pass

    def send_clipboard(self, text: str):
        self.send_input({"type": MsgType.CLIPBOARD_SEND,
                         "content_type": "text/plain", "data": text})

    def _run_loop(self, host: str, port: int):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            while not self._closing:
                try:
                    self._loop.run_until_complete(
                        self._connect_and_receive(host, port))
                except Exception as e:
                    if not self._closing:
                        logger.error("Connection error: %s", e)
                        if self.on_error:
                            self.on_error(str(e))

                self._connected = False
                if self.on_disconnected and not self._closing:
                    self.on_disconnected("Connection lost")

                if not self._auto_reconnect or self._closing:
                    break

                logger.info("Reconnecting in %.1fs...", self._reconnect_delay)
                time.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 1.5, self._max_reconnect_delay)
        finally:
            self._connected = False
            self._loop.close()
            self._loop = None

    async def _connect_and_receive(self, host: str, port: int):
        if self._broker_mode and not self._broker_token:
            # Phase 1: Connect to broker, authenticate, get machine assignment
            await self._broker_handshake(host, port)
            if self._redirect_host and self._broker_token:
                # Phase 2: Connect to assigned machine with token
                host = self._redirect_host
                port = self._redirect_port
                logger.info("Broker redirect → %s:%d", host, port)
            else:
                return

        try:
            await self._connect_to_server(host, port)
        except Exception:
            # If the broker-assigned target failed (e.g. Flame server offline),
            # drop broker state so the next reconnect re-runs the broker
            # handshake and re-prompts the user to pick a different machine.
            # _run_loop passes the original broker host/port on each retry,
            # so we just need to clear the token + redirect + preferred machine.
            if self._broker_mode and self._broker_token:
                logger.warning(
                    "Broker-assigned target %s:%d failed — returning to broker",
                    host, port)
                self._broker_token = ""
                self._redirect_host = ""
                self._redirect_port = 0
                self._preferred_machine = ""
            raise

    async def _connect_to_server(self, host: str, port: int):
        """Connect to a server (Flame or broker) and handle the session."""
        scheme = "wss" if self._use_tls else "ws"
        uri = f"{scheme}://{host}:{port}"
        logger.info("Connecting to %s", uri)

        ssl_context = None
        if self._use_tls:
            import ssl
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        try:
            async with websockets.connect(
                uri, max_size=50 * 1024 * 1024,
                ping_interval=20, ping_timeout=30,
                ssl=ssl_context,
            ) as ws:
                self._ws = ws
                logger.info("Connected to server")

                first_msg = await ws.recv()
                if isinstance(first_msg, str):
                    msg = parse_message(first_msg)

                    if msg.get("type") == MsgType.AUTH_REQUEST:
                        if self._broker_token:
                            # Authenticate with broker token
                            await self._handle_token_auth(ws, msg)
                        else:
                            await self._handle_auth(ws, msg)

                    elif msg.get("type") == MsgType.SERVER_HELLO:
                        self._handle_server_hello(msg)

                self._connected = True
                self._reconnect_delay = 1.0

                if self.on_connected:
                    self.on_connected()

                hello = ClientHelloMsg()
                if self._screen_size:
                    hello.screen_width = self._screen_size[0]
                    hello.screen_height = self._screen_size[1]
                await ws.send(hello.to_json())

                if self._udp_enabled:
                    await self._negotiate_udp(ws, host)

                if self._hybrid and self._hybrid.state.udp_confirmed:
                    asyncio.ensure_future(self._udp_stats_loop(ws))

                async for message in ws:
                    if self._closing:
                        break
                    if isinstance(message, str):
                        self._handle_json(message)
                    elif isinstance(message, bytes):
                        self._handle_binary(message)
        except _BrokerRedirect:
            # Auto-detected broker in direct mode — redirect to assigned Flame
            if self._redirect_host and self._broker_token:
                logger.info("Broker redirect → %s:%d", self._redirect_host, self._redirect_port)
                await self._connect_to_server(self._redirect_host, self._redirect_port)
            else:
                logger.error("Broker redirect failed — no assignment received")

    async def _broker_handshake(self, host: str, port: int):
        """Phase 1: Authenticate with broker and get machine assignment."""
        scheme = "wss" if self._use_tls else "ws"
        uri = f"{scheme}://{host}:{port}"
        logger.info("Connecting to broker at %s", uri)

        ssl_context = None
        if self._use_tls:
            import ssl
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(
            uri, max_size=1024 * 1024,
            ping_interval=20, ping_timeout=30,
            ssl=ssl_context,
        ) as ws:
            # Broker sends auth_request
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = parse_message(raw)

            if msg.get("type") != MsgType.AUTH_REQUEST:
                logger.error("Broker: expected auth_request, got %s", msg.get("type"))
                return

            # Send PAM credentials
            screen_w = self._screen_size[0] if self._screen_size else 0
            screen_h = self._screen_size[1] if self._screen_size else 0
            auth_resp = AuthResponse(
                method="pam",
                username=self._username,
                credential=self._password,
                screen_width=screen_w,
                screen_height=screen_h,
            )
            await ws.send(auth_resp.to_json())

            # Get auth result
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = parse_message(raw)

            if msg.get("type") == MsgType.AUTH_RESULT:
                if self.on_auth_result:
                    self.on_auth_result(msg.get("success", False),
                                        msg.get("message", ""))
                if not msg.get("success", False):
                    return

            # Get broker hello (machine list)
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = parse_message(raw)

            machines = []
            if msg.get("type") == MsgType.BROKER_HELLO:
                machines = msg.get("machines", []) or []
                if self.on_broker_hello:
                    self.on_broker_hello(msg)

            # Determine which machine to request:
            # 1. If we already have a cached preference (reconnect), reuse it.
            # 2. Else if a machine-needed callback is wired, prompt the user.
            # 3. Else fall back to auto-assign (empty machine_name).
            selected_name = self._preferred_machine
            if not selected_name and self.on_broker_machine_needed:
                self._machine_selection_future = asyncio.get_event_loop().create_future()
                try:
                    self.on_broker_machine_needed(machines)
                except Exception as e:
                    logger.error("on_broker_machine_needed raised: %s", e)
                try:
                    selected_name = await asyncio.wait_for(
                        self._machine_selection_future, timeout=120)
                except asyncio.TimeoutError:
                    logger.warning("Machine selection timed out — falling back to auto-assign")
                    selected_name = ""
                finally:
                    self._machine_selection_future = None
                # Cache the choice so reconnects don't re-prompt
                self._preferred_machine = selected_name or ""

            await ws.send(json.dumps({
                "type": MsgType.BROKER_MACHINE_REQUEST,
                "machine_name": selected_name or "",
            }))

            # Get assignment
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = parse_message(raw)

            if msg.get("type") == MsgType.BROKER_ASSIGN:
                if self.on_broker_assign:
                    self.on_broker_assign(msg)

                if msg.get("success", False):
                    self._redirect_host = msg["host"]
                    self._redirect_port = msg["port"]
                    self._broker_token = msg["token"]
                    self._use_tls = msg.get("use_tls", True)
                    logger.info("Broker assigned: %s → %s:%d",
                                msg.get("machine_name", ""),
                                self._redirect_host, self._redirect_port)
                else:
                    logger.error("Broker assignment failed: %s",
                                 msg.get("message", ""))

    async def _handle_token_auth(self, ws, msg: dict):
        """Authenticate with a broker-issued token."""
        screen_w = self._screen_size[0] if self._screen_size else 0
        screen_h = self._screen_size[1] if self._screen_size else 0
        auth_resp = AuthResponse(
            method="token",
            username=self._username,
            credential=self._broker_token,
            screen_width=screen_w,
            screen_height=screen_h,
        )
        await ws.send(auth_resp.to_json())

        result_raw = await ws.recv()
        result = parse_message(result_raw)
        if result.get("type") == MsgType.AUTH_RESULT:
            if self.on_auth_result:
                self.on_auth_result(result.get("success", False),
                                    result.get("message", ""))
            if not result.get("success", False):
                return

        hello_raw = await ws.recv()
        hello = parse_message(hello_raw)
        self._handle_server_hello(hello)

    async def _handle_auth(self, ws, msg: dict):
        """Handle authentication handshake."""
        auth_mode = msg.get("auth_mode", "local")
        challenge = msg.get("challenge", "")

        if self.on_auth_required:
            self.on_auth_required(challenge)

        if auth_mode == "pam":
            # PAM mode: send credentials directly
            # Security is provided by TLS (wss://) — same as PCoIP/SSH
            screen_w = self._screen_size[0] if self._screen_size else 0
            screen_h = self._screen_size[1] if self._screen_size else 0
            auth_resp = AuthResponse(
                method="pam",
                username=self._username,
                credential=self._password,
                screen_width=screen_w,
                screen_height=screen_h,
            )
            logger.info("Authenticating via PAM as '%s'", self._username)
        else:
            # Local mode: challenge-response hash
            if self._password:
                pwd_hash = hashlib.sha256(
                    f"{self._password}:{challenge}".encode()
                ).hexdigest()
            else:
                pwd_hash = ""

            screen_w = self._screen_size[0] if self._screen_size else 0
            screen_h = self._screen_size[1] if self._screen_size else 0
            auth_resp = AuthResponse(
                method="password",
                username=self._username,
                credential=pwd_hash,
                screen_width=screen_w,
                screen_height=screen_h,
            )

        await ws.send(auth_resp.to_json())

        # Wait for result
        result_raw = await ws.recv()
        result = parse_message(result_raw)
        if result.get("type") == MsgType.AUTH_RESULT:
            if self.on_auth_result:
                self.on_auth_result(result.get("success", False),
                                    result.get("message", ""))
            if not result.get("success", False):
                return

        # Receive server hello (or broker hello if connected to a broker)
        hello_raw = await ws.recv()
        hello = parse_message(hello_raw)

        if hello.get("type") == MsgType.BROKER_HELLO:
            # Auto-detect broker: connected in direct mode but server is a broker.
            # Handle the full broker redirect inline.
            logger.info("Detected broker (auto-switching from direct mode)")
            machines = hello.get("machines", []) or []
            if self.on_broker_hello:
                self.on_broker_hello(hello)

            # Prompt the user to pick a machine (same flow as broker mode)
            selected_name = self._preferred_machine
            if not selected_name and self.on_broker_machine_needed:
                self._machine_selection_future = asyncio.get_event_loop().create_future()
                try:
                    self.on_broker_machine_needed(machines)
                except Exception as e:
                    logger.error("on_broker_machine_needed raised: %s", e)
                try:
                    selected_name = await asyncio.wait_for(
                        self._machine_selection_future, timeout=120)
                except asyncio.TimeoutError:
                    logger.warning("Machine selection timed out — falling back to auto-assign")
                    selected_name = ""
                finally:
                    self._machine_selection_future = None
                self._preferred_machine = selected_name or ""

            await ws.send(json.dumps({
                "type": MsgType.BROKER_MACHINE_REQUEST,
                "machine_name": selected_name or "",
            }))

            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            assign = parse_message(raw)

            if assign.get("type") == MsgType.BROKER_ASSIGN and assign.get("success"):
                if self.on_broker_assign:
                    self.on_broker_assign(assign)
                self._redirect_host = assign["host"]
                self._redirect_port = assign["port"]
                self._broker_token = assign["token"]
                self._use_tls = assign.get("use_tls", True)
                self._broker_mode = True
                logger.info("Broker assigned: %s:%d",
                            self._redirect_host, self._redirect_port)
            else:
                logger.error("Broker assignment failed: %s",
                             assign.get("message", ""))
            # Signal caller to handle redirect (raise to exit the ws context)
            raise _BrokerRedirect()
        else:
            self._handle_server_hello(hello)

    async def _negotiate_udp(self, ws, server_host: str):
        try:
            self._udp_client = UDPMediaClient()
            self._hybrid = HybridClientTransport(self._udp_client)
            self._jitter_buffer = JitterBuffer(on_frame_ready=self._on_jitter_frame)
            self._jitter_buffer.start()

            self._udp_client.on_video_frame = self._on_udp_video
            self._udp_client.on_audio_frame = self._on_udp_audio

            local_port = self._hybrid.start_udp()

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect((server_host, 1))
                local_addr = s.getsockname()[0]
                s.close()
            except Exception:
                local_addr = ""

            announce = self._hybrid.get_announce_message(local_addr)
            await ws.send(json.dumps(announce))

            await asyncio.sleep(1.0)
            if self._hybrid.check_probe_received():
                confirm = self._hybrid.get_confirm_message()
                await ws.send(json.dumps(confirm))
                logger.info("UDP confirmed — media via UDP")
            else:
                logger.info("UDP unavailable — TCP fallback")

        except Exception as e:
            logger.warning("UDP setup failed: %s", e)
            self._hybrid = None

    async def _udp_stats_loop(self, ws):
        while self._connected and not self._closing and self._hybrid:
            await asyncio.sleep(self._udp_stats_interval)
            if self._hybrid and self._connected:
                try:
                    stats_msg = self._hybrid.get_stats_message()
                    await ws.send(json.dumps(stats_msg))
                except Exception:
                    break

    def _on_udp_video(self, flags: int, timestamp_ms: int, data: bytes):
        is_keyframe = bool(flags & FLAG_KEYFRAME)
        if self._jitter_buffer:
            self._jitter_buffer.push(
                CHANNEL_VIDEO, flags, timestamp_ms, data, is_keyframe)
        else:
            self._on_jitter_frame(CHANNEL_VIDEO, flags, timestamp_ms, data)

    def _on_udp_audio(self, timestamp_ms: int, data: bytes):
        if self._jitter_buffer:
            self._jitter_buffer.push(CHANNEL_AUDIO, 0, timestamp_ms, data)
        elif self.on_audio_frame:
            self.on_audio_frame(0, timestamp_ms, data)

    def _on_jitter_frame(self, channel: int, flags: int,
                         timestamp_ms: int, data: bytes):
        if channel == CHANNEL_VIDEO:
            if self.on_video_frame:
                # UDP frames don't carry codec/chroma in-band; use FrameType
                # to signal H.264 (the server encodes the current codec)
                from common.messages import FrameType, VideoCodec, ChromaSubsampling
                self.on_video_frame(
                    FrameType.VIDEO_H264, VideoCodec.H264,
                    ChromaSubsampling.YUV444, flags, timestamp_ms, 0, data)
        elif channel == CHANNEL_AUDIO:
            if self.on_audio_frame:
                self.on_audio_frame(0, timestamp_ms, data)

    def _handle_server_hello(self, msg: dict):
        if msg.get("type") == MsgType.SERVER_HELLO and self.on_server_hello:
            self.on_server_hello(msg)

    def _handle_json(self, data: str):
        try:
            msg = json.loads(data)
            msg_type = msg.get("type")

            if msg_type == MsgType.SERVER_HELLO:
                if self.on_server_hello:
                    self.on_server_hello(msg)
            elif msg_type == TransportMsg.UDP_ACTIVE:
                logger.info("Server: UDP active (RTT: %.1f ms)",
                            msg.get("udp_rtt_ms", 0))
            elif msg_type == TransportMsg.UDP_FALLBACK:
                logger.warning("Server: UDP fallback - %s", msg.get("reason", ""))
            elif msg_type == MsgType.HEALTH_PING:
                pong = HealthPong(
                    ping_timestamp_ms=msg.get("timestamp_ms", 0),
                    sequence=msg.get("sequence", 0))
                self.send_input(json.loads(pong.to_json()))
            elif msg_type == MsgType.HEALTH_STATS:
                if self.on_health_stats:
                    self.on_health_stats(msg)
            elif msg_type == MsgType.MONITOR_LIST:
                if self.on_monitor_list:
                    self.on_monitor_list(msg.get("monitors", []))
            elif msg_type == MsgType.CLIPBOARD_RECV:
                if self.on_clipboard:
                    self.on_clipboard(msg.get("data", ""))
            elif msg_type in (MsgType.FILE_ACCEPT, MsgType.FILE_ACK,
                              MsgType.FILE_CANCEL):
                if self.on_file_response:
                    self.on_file_response(msg)
            elif msg_type in (MsgType.USB_DEVICE_LIST, MsgType.USB_ATTACHED,
                              MsgType.USB_DETACHED, MsgType.USB_ERROR):
                if self.on_usb_response:
                    self.on_usb_response(msg)
            elif msg_type == MsgType.BROKER_HELLO:
                if self.on_broker_hello:
                    self.on_broker_hello(msg)
            elif msg_type == MsgType.BROKER_ASSIGN:
                if self.on_broker_assign:
                    self.on_broker_assign(msg)
            elif msg_type == MsgType.CURSOR_UPDATE:
                if self.on_cursor_update:
                    self.on_cursor_update(msg)
        except json.JSONDecodeError:
            pass

    def _handle_binary(self, data: bytes):
        if len(data) < 2:
            return
        frame_type = data[0]

        if frame_type in (FrameType.VIDEO_H264, FrameType.VIDEO_H265,
                          FrameType.VIDEO_AV1):
            if len(data) >= VIDEO_HEADER_SIZE and self.on_video_frame:
                ft, codec, chroma, flags, ts, mon, payload = \
                    decode_video_header(data)
                # Diagnostic: log first frame received + first keyframe
                if not hasattr(self, "_rx_video_count"):
                    self._rx_video_count = 0
                    self._rx_keyframe_count = 0
                self._rx_video_count += 1
                if flags & 0x01:  # KEYFRAME flag
                    self._rx_keyframe_count += 1
                if self._rx_video_count in (1, 30, 300) or \
                   (self._rx_keyframe_count == 1 and flags & 0x01):
                    logger.info("RX video frame #%d (keyframes=%d, codec=%d, "
                                "chroma=%d, flags=%d, payload=%d bytes)",
                                self._rx_video_count, self._rx_keyframe_count,
                                int(codec), int(chroma), flags, len(payload))
                self.on_video_frame(ft, codec, chroma, flags, ts, mon, payload)
        elif frame_type in (FrameType.VIDEO_FULL, FrameType.VIDEO_PARTIAL):
            if len(data) >= JPEG_HEADER_SIZE and self.on_jpeg_frame:
                ft, x, y, w, h, payload = decode_jpeg_header(data)
                self.on_jpeg_frame(ft, x, y, w, h, payload)
        elif frame_type == FrameType.AUDIO:
            if len(data) >= AUDIO_HEADER_SIZE and self.on_audio_frame:
                codec, ts, payload = decode_audio_header(data)
                self.on_audio_frame(codec, ts, payload)
