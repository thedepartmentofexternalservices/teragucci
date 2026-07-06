"""
Hybrid TCP+UDP transport layer.

Coordinates:
- TCP WebSocket (wss://) on port 443 for control messages
- UDP on same port or adjacent port for media streaming
- Automatic fallback to TCP-only when UDP is blocked
- UDP hole-punching via the TCP channel

Connection flow:
1. Client connects via WebSocket (TCP) to server:443
2. Client opens a UDP socket and reports its port via TCP
3. Server sends a UDP probe packet to the client
4. Client confirms UDP receipt via TCP
5. If confirmed: media goes over UDP, control stays on TCP
6. If UDP probe fails: everything stays on TCP (fallback mode)

This is similar to how Teradici and Parsec negotiate their transports.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Default ports
DEFAULT_PORT = 443
DEFAULT_UDP_PORT = 443  # Use same port (server binds both TCP and UDP on 443)


class TransportMode(Enum):
    TCP_ONLY = "tcp_only"       # Fallback: everything over WebSocket
    UDP_MEDIA = "udp_media"     # Normal: control on TCP, media on UDP


# Protocol messages for UDP negotiation (sent over TCP WebSocket)
class TransportMsg:
    # Client tells server its UDP port
    UDP_ANNOUNCE = "udp_announce"
    # Server sends probe to confirm UDP path works
    UDP_PROBE = "udp_probe"
    # Client confirms it received the UDP probe
    UDP_CONFIRMED = "udp_confirmed"
    # Server confirms UDP is active
    UDP_ACTIVE = "udp_active"
    # Fallback: UDP failed, stay on TCP
    UDP_FALLBACK = "udp_fallback"
    # Client reports UDP stats back to server
    UDP_STATS = "udp_stats"


@dataclass
class TransportState:
    """Tracks the current transport state for a connection."""
    mode: TransportMode = TransportMode.TCP_ONLY
    udp_confirmed: bool = False
    udp_probe_sent_at: float = 0.0
    udp_rtt_ms: float = 0.0
    client_udp_port: int = 0
    client_udp_addr: str = ""
    fallback_reason: str = ""

    # Stats
    udp_packets_sent: int = 0
    udp_packets_received: int = 0
    tcp_frames_sent: int = 0
    packet_loss_pct: float = 0.0

    @property
    def using_udp(self) -> bool:
        return self.mode == TransportMode.UDP_MEDIA and self.udp_confirmed


class HybridServerTransport:
    """
    Server-side hybrid transport manager.

    For each connected client, manages the UDP negotiation and decides
    whether to send media over UDP or fall back to TCP.
    """

    # Generous: a busy client event loop (decoding a full-rate TCP stream
    # while negotiating) can delay the confirm by several seconds. Measured
    # 3.9 s on a WAN client under load — 3 s timed out spuriously.
    UDP_PROBE_TIMEOUT = 10.0  # seconds to wait for UDP confirmation
    UDP_PROBE_RETRIES = 3

    def __init__(self, udp_server):
        """
        Args:
            udp_server: UDPMediaServer instance for sending media
        """
        self._udp_server = udp_server
        self._client_states: dict = {}  # client_id -> TransportState
        # NAT hole-punch bookkeeping. Tokens travel over the authenticated
        # WebSocket (UDP_ANNOUNCE); punch datagrams carrying the same token
        # reveal the client's observed (post-NAT) address.
        self._token_to_client: dict = {}   # punch_token -> client_id
        self._early_punches: dict = {}     # punch_token -> observed addr
        # Wire the punch callback (called from the UDP recv thread).
        self._udp_server.on_punch = self.handle_punch

    def _probe_payload(self) -> bytes:
        return json.dumps({
            "type": TransportMsg.UDP_PROBE,
            "timestamp": int(time.time() * 1000),
        }).encode()

    def handle_punch(self, token: str, addr: tuple):
        """Punch datagram arrived (UDP recv thread). Register the client's
        OBSERVED address — the one that actually traverses NAT/VPN — and
        probe it immediately.

        May fire before or after the UDP_ANNOUNCE that carries the token;
        both orders are handled.
        """
        client_id = self._token_to_client.get(token)
        if client_id is None:
            # Punch raced ahead of the announce — stash for later.
            self._early_punches[token] = addr
            return
        state = self.get_state(client_id)
        state.client_udp_addr, state.client_udp_port = addr[0], addr[1]
        self._udp_server.register_client(client_id, addr)
        logger.info("Client %s: punch observed from %s:%d — probing observed addr",
                    client_id, addr[0], addr[1])
        for _ in range(self.UDP_PROBE_RETRIES):
            self._udp_server.send_probe(addr, self._probe_payload())

    def get_state(self, client_id: str) -> TransportState:
        if client_id not in self._client_states:
            self._client_states[client_id] = TransportState()
        return self._client_states[client_id]

    async def handle_transport_message(self, client_id: str, msg: dict,
                                       ws_send: Callable) -> Optional[dict]:
        """
        Handle a transport negotiation message from a client.

        Args:
            client_id: Unique client identifier
            msg: Parsed JSON message
            ws_send: Async callable to send a response over WebSocket

        Returns response dict or None.
        """
        msg_type = msg.get("type")
        state = self.get_state(client_id)

        if msg_type == TransportMsg.UDP_ANNOUNCE:
            # Client is telling us its UDP address
            state.client_udp_port = msg.get("udp_port", 0)
            state.client_udp_addr = msg.get("udp_addr", "")
            punch_token = msg.get("punch_token", "")

            if not state.client_udp_port:
                logger.warning("Client %s announced invalid UDP port", client_id)
                return None

            state.udp_probe_sent_at = time.time()

            # NAT path: remember the token so an incoming punch datagram can
            # be matched to this client. If the punch already arrived (it
            # races the announce), register the observed addr right now.
            if punch_token:
                self._token_to_client[punch_token] = client_id
                early = self._early_punches.pop(punch_token, None)
                if early is not None:
                    self.handle_punch(punch_token, early)

            # LAN path: the announced addr may be directly reachable. Probe
            # it too; only the reachable address will actually deliver, and
            # a later punch overwrites the registration with the observed
            # addr (which also works on a LAN).
            if state.client_udp_addr and not state.using_udp:
                addr = (state.client_udp_addr, state.client_udp_port)
                if client_id not in getattr(self._udp_server, "_clients", {}):
                    self._udp_server.register_client(client_id, addr)
                logger.info("Client %s announced UDP %s:%d, sending probes...",
                            client_id, state.client_udp_addr, state.client_udp_port)
                for _ in range(self.UDP_PROBE_RETRIES):
                    self._udp_server.send_probe(addr, self._probe_payload())
                    await asyncio.sleep(0.1)

            # Start a timeout check
            asyncio.get_event_loop().call_later(
                self.UDP_PROBE_TIMEOUT,
                lambda: self._check_probe_timeout(client_id))

            return {"type": TransportMsg.UDP_PROBE, "status": "probing"}

        elif msg_type == TransportMsg.UDP_CONFIRMED:
            # Client confirmed it received our UDP probe
            if state.udp_probe_sent_at > 0:
                state.udp_rtt_ms = (time.time() - state.udp_probe_sent_at) * 1000
            state.udp_confirmed = True
            state.mode = TransportMode.UDP_MEDIA

            # A confirm may arrive AFTER the probe timeout already
            # unregistered this client — re-register the known-good
            # (observed) address so send_video_frame_to works again.
            if state.client_udp_addr and state.client_udp_port:
                self._udp_server.register_client(
                    client_id, (state.client_udp_addr, state.client_udp_port))

            logger.info("Client %s: UDP confirmed (negotiation took %.1f ms). "
                        "Switching to UDP media.", client_id, state.udp_rtt_ms)

            return {
                "type": TransportMsg.UDP_ACTIVE,
                "udp_rtt_ms": state.udp_rtt_ms,
            }

        elif msg_type == TransportMsg.UDP_STATS:
            # Client reporting packet loss stats
            state.packet_loss_pct = msg.get("packet_loss_pct", 0.0)
            state.udp_packets_received = msg.get("packets_received", 0)
            return None

        return None

    def _check_probe_timeout(self, client_id: str):
        """Called after timeout to check if UDP was confirmed."""
        state = self._client_states.get(client_id)
        if state and not state.udp_confirmed:
            state.mode = TransportMode.TCP_ONLY
            state.fallback_reason = "UDP probe timeout"
            self._udp_server.unregister_client(client_id)
            logger.warning("Client %s: UDP probe timed out, falling back to TCP",
                           client_id)

    def should_use_udp(self, client_id: str) -> bool:
        """Check if media should be sent over UDP for this client."""
        state = self._client_states.get(client_id)
        return state is not None and state.using_udp

    def remove_client(self, client_id: str):
        """Clean up when a client disconnects."""
        self._udp_server.unregister_client(client_id)
        self._client_states.pop(client_id, None)


class HybridClientTransport:
    """
    Client-side hybrid transport manager.

    Opens a UDP socket, announces it to the server, waits for probe
    confirmation, and manages the UDP media receiver.
    """

    def __init__(self, udp_client):
        """
        Args:
            udp_client: UDPMediaClient instance for receiving media
        """
        self._udp_client = udp_client
        self.state = TransportState()
        self._probe_received = False

    def start_udp(self) -> int:
        """
        Start the UDP receiver and return the local port.

        The caller should send a UDP_ANNOUNCE message to the server
        with this port number.
        """
        port = self._udp_client.start(local_port=0)
        self.state.client_udp_port = port

        # Intercept video frames to detect probe packets
        original_handler = self._udp_client.on_video_frame

        def probe_interceptor(flags, timestamp_ms, data):
            # Check if this is a probe packet (JSON content)
            try:
                msg = json.loads(data)
                if msg.get("type") == TransportMsg.UDP_PROBE:
                    self._probe_received = True
                    logger.info("UDP probe received from server")
                    return
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            # Not a probe, pass through to normal handler
            if original_handler:
                original_handler(flags, timestamp_ms, data)

        self._udp_client.on_video_frame = probe_interceptor
        return port

    def check_probe_received(self) -> bool:
        """Check if the UDP probe was received. Call this after a short delay."""
        if self._probe_received:
            self.state.udp_confirmed = True
            self.state.mode = TransportMode.UDP_MEDIA
            return True
        return False

    def get_announce_message(self, local_addr: str = "") -> dict:
        """Build the UDP_ANNOUNCE message to send to server."""
        return {
            "type": TransportMsg.UDP_ANNOUNCE,
            "udp_port": self.state.client_udp_port,
            "udp_addr": local_addr,
        }

    def get_confirm_message(self) -> dict:
        return {"type": TransportMsg.UDP_CONFIRMED}

    def get_stats_message(self) -> dict:
        stats = self._udp_client.stats
        return {
            "type": TransportMsg.UDP_STATS,
            "packet_loss_pct": stats.get("packet_loss_rate", 0.0),
            "packets_received": stats.get("packets_received", 0),
            "frames_completed": stats.get("frames_completed", 0),
            "frames_dropped": stats.get("frames_dropped", 0),
        }

    def stop(self):
        self._udp_client.stop()
