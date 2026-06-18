"""
QUIC transport layer for Teraguchi.

Provides a single multiplexed connection that replaces the dual TCP+UDP
architecture with QUIC (RFC 9000):

- Reliable streams for control messages (input, auth, health, clipboard)
- QUIC datagrams (RFC 9221) for low-latency media (video, audio)
- Built-in TLS 1.3 encryption
- 0-RTT connection establishment
- No head-of-line blocking between streams
- Connection migration (Wi-Fi <-> cellular handoff)

Architecture:
    Server: QUICTransportServer — listens on port 443/UDP
    Client: QUICTransportClient — connects to server

Stream IDs:
    Stream 0: Control channel (JSON messages, bidirectional)
    Stream 4: Health/stats channel (bidirectional)
    Datagrams: Video and audio frames (unreliable, low-latency)

Datagram format (same as UDP transport for compatibility):
    [1 byte: channel] [1 byte: flags] [4 bytes: timestamp_ms] [payload...]
    Total header: 6 bytes (lighter than UDP transport's 16-byte header since
    QUIC provides its own sequencing and framing)
"""

import asyncio
import logging
import struct
import time
import ssl
import os
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict

logger = logging.getLogger(__name__)

try:
    from aioquic.asyncio import connect as quic_connect, serve as quic_serve
    from aioquic.asyncio.protocol import QuicConnectionProtocol
    from aioquic.quic.configuration import QuicConfiguration
    from aioquic.quic.events import (
        StreamDataReceived,
        DatagramFrameReceived,
        HandshakeCompleted,
        ConnectionTerminated,
    )
    HAS_QUIC = True
except ImportError:
    HAS_QUIC = False
    logger.info("aioquic not installed — QUIC transport unavailable. Install with: pip install aioquic")

# Channel IDs for datagrams
QUIC_CHANNEL_VIDEO = 0x01
QUIC_CHANNEL_AUDIO = 0x02
QUIC_DATAGRAM_HEADER = 6  # channel(1) + flags(1) + timestamp(4)

# Stream IDs
QUIC_STREAM_CONTROL = 0
QUIC_STREAM_HEALTH = 4

# Flags (same as UDP transport for compatibility)
QUIC_FLAG_KEYFRAME = 0x01


def encode_datagram(channel: int, flags: int, timestamp_ms: int, payload: bytes) -> bytes:
    """Encode a media datagram for QUIC."""
    header = struct.pack("!BBI", channel, flags, timestamp_ms & 0xFFFFFFFF)
    return header + payload


def decode_datagram(data: bytes) -> Optional[tuple]:
    """Decode a QUIC media datagram. Returns (channel, flags, timestamp_ms, payload)."""
    if len(data) < QUIC_DATAGRAM_HEADER:
        return None
    channel, flags, timestamp_ms = struct.unpack("!BBI", data[:QUIC_DATAGRAM_HEADER])
    return channel, flags, timestamp_ms, data[QUIC_DATAGRAM_HEADER:]


@dataclass
class QUICStats:
    """Statistics for a QUIC connection."""
    datagrams_sent: int = 0
    datagrams_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    rtt_ms: float = 0.0
    loss_rate: float = 0.0
    connected_at: float = 0.0

    @property
    def bandwidth_mbps(self) -> float:
        elapsed = time.time() - self.connected_at
        if elapsed <= 0:
            return 0.0
        return (self.bytes_sent * 8) / (elapsed * 1_000_000)


# ============================================================
# Server-side QUIC Transport
# ============================================================

if HAS_QUIC:
    class TeraguchiServerProtocol(QuicConnectionProtocol):
        """QUIC protocol handler for the server side."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.stats = QUICStats(connected_at=time.time())
            # Callbacks set by QUICTransportServer
            self.on_control_message: Optional[Callable] = None
            self.on_datagram: Optional[Callable] = None
            self.on_connected: Optional[Callable] = None
            self.on_disconnected: Optional[Callable] = None
            self._client_id = ""

        def quic_event_received(self, event):
            if isinstance(event, HandshakeCompleted):
                self._client_id = str(id(self))
                logger.info("QUIC client connected: %s", self._client_id)
                if self.on_connected:
                    self.on_connected(self._client_id, self)

            elif isinstance(event, StreamDataReceived):
                if event.stream_id == QUIC_STREAM_CONTROL or event.stream_id == QUIC_STREAM_HEALTH:
                    if self.on_control_message:
                        self.on_control_message(self._client_id, event.data, event.stream_id)

            elif isinstance(event, DatagramFrameReceived):
                self.stats.datagrams_received += 1
                self.stats.bytes_received += len(event.data)
                if self.on_datagram:
                    self.on_datagram(self._client_id, event.data)

            elif isinstance(event, ConnectionTerminated):
                logger.info("QUIC client disconnected: %s (reason: %s)",
                           self._client_id, event.reason_phrase)
                if self.on_disconnected:
                    self.on_disconnected(self._client_id)

        def send_control(self, data: bytes, stream_id: int = QUIC_STREAM_CONTROL):
            """Send a control message on a reliable stream."""
            self._quic.send_stream_data(stream_id, data, end_stream=False)
            self.transmit()

        def send_datagram(self, data: bytes):
            """Send a media datagram (unreliable, low-latency)."""
            self._quic.send_datagram_frame(data)
            self.stats.datagrams_sent += 1
            self.stats.bytes_sent += len(data)
            self.transmit()

        def send_video_frame(self, payload: bytes, timestamp_ms: int,
                             is_keyframe: bool = False):
            """Send a video frame as a QUIC datagram."""
            flags = QUIC_FLAG_KEYFRAME if is_keyframe else 0
            datagram = encode_datagram(QUIC_CHANNEL_VIDEO, flags, timestamp_ms, payload)

            # QUIC datagrams have a max size based on path MTU
            # For large frames, we need to fragment
            max_datagram = self._quic._max_datagram_size if hasattr(self._quic, '_max_datagram_size') else 1200
            if len(datagram) <= max_datagram:
                self.send_datagram(datagram)
            else:
                # Fragment the payload
                max_payload = max_datagram - QUIC_DATAGRAM_HEADER - 4  # 4 bytes for frag header
                total_frags = (len(payload) + max_payload - 1) // max_payload
                for i in range(total_frags):
                    offset = i * max_payload
                    chunk = payload[offset:offset + max_payload]
                    frag_header = struct.pack("!HH", i, total_frags)
                    frag_flags = flags | 0x02  # FLAG_FRAGMENT
                    if i == total_frags - 1:
                        frag_flags |= 0x04  # FLAG_LAST_FRAGMENT
                    frag_datagram = encode_datagram(
                        QUIC_CHANNEL_VIDEO, frag_flags, timestamp_ms,
                        frag_header + chunk
                    )
                    self.send_datagram(frag_datagram)

        def send_audio_frame(self, payload: bytes, timestamp_ms: int):
            """Send an audio frame as a QUIC datagram."""
            datagram = encode_datagram(QUIC_CHANNEL_AUDIO, 0, timestamp_ms, payload)
            self.send_datagram(datagram)


class QUICTransportServer:
    """
    Server-side QUIC transport manager.

    Listens for QUIC connections and provides methods to send
    control messages (reliable) and media datagrams (unreliable).
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 443):
        self.host = host
        self.port = port
        self._clients: Dict[str, 'TeraguchiServerProtocol'] = {}
        self._server = None
        self._running = False

        # Callbacks
        self.on_control_message: Optional[Callable] = None  # (client_id, data, stream_id)
        self.on_client_connected: Optional[Callable] = None  # (client_id)
        self.on_client_disconnected: Optional[Callable] = None  # (client_id)

    def _create_protocol(self, *args, **kwargs):
        """Factory for creating QUIC protocol instances."""
        if not HAS_QUIC:
            raise RuntimeError("aioquic not installed")
        proto = TeraguchiServerProtocol(*args, **kwargs)
        proto.on_connected = self._on_client_connected
        proto.on_disconnected = self._on_client_disconnected
        proto.on_control_message = self._on_control_message
        return proto

    def _on_client_connected(self, client_id: str, protocol):
        self._clients[client_id] = protocol
        if self.on_client_connected:
            self.on_client_connected(client_id)

    def _on_client_disconnected(self, client_id: str):
        self._clients.pop(client_id, None)
        if self.on_client_disconnected:
            self.on_client_disconnected(client_id)

    def _on_control_message(self, client_id: str, data: bytes, stream_id: int):
        if self.on_control_message:
            self.on_control_message(client_id, data, stream_id)

    async def start(self, cert_file: str = None, key_file: str = None):
        """Start the QUIC server."""
        if not HAS_QUIC:
            logger.warning("QUIC not available (aioquic not installed)")
            return False

        config = QuicConfiguration(
            is_client=False,
            max_datagram_frame_size=65536,
        )

        # TLS is mandatory for QUIC
        if cert_file and key_file:
            config.load_cert_chain(cert_file, key_file)
        else:
            # Generate self-signed cert for local use
            cert_path, key_path = _generate_self_signed_cert()
            config.load_cert_chain(cert_path, key_path)

        config.alpn_protocols = ["teraguchi"]

        try:
            self._server = await quic_serve(
                self.host, self.port,
                configuration=config,
                create_protocol=self._create_protocol,
            )
            self._running = True
            logger.info("QUIC transport server on %s:%d", self.host, self.port)
            return True
        except Exception as e:
            logger.error("QUIC server start failed: %s", e)
            return False

    def send_video_to_all(self, data: bytes, timestamp_ms: int,
                          is_keyframe: bool = False):
        """Send a video frame to all connected QUIC clients."""
        for protocol in list(self._clients.values()):
            try:
                protocol.send_video_frame(data, timestamp_ms, is_keyframe)
            except Exception as e:
                logger.debug("QUIC send error: %s", e)

    def send_audio_to_all(self, data: bytes, timestamp_ms: int):
        """Send an audio frame to all connected QUIC clients."""
        for protocol in list(self._clients.values()):
            try:
                protocol.send_audio_frame(data, timestamp_ms)
            except Exception as e:
                logger.debug("QUIC audio send error: %s", e)

    def send_control(self, client_id: str, data: bytes,
                     stream_id: int = QUIC_STREAM_CONTROL):
        """Send a control message to a specific client."""
        protocol = self._clients.get(client_id)
        if protocol:
            protocol.send_control(data, stream_id)

    def send_control_to_all(self, data: bytes,
                            stream_id: int = QUIC_STREAM_CONTROL):
        """Send a control message to all clients."""
        for protocol in list(self._clients.values()):
            try:
                protocol.send_control(data, stream_id)
            except Exception:
                pass

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def is_running(self) -> bool:
        return self._running

    def stop(self):
        """Stop the QUIC server."""
        self._running = False
        if self._server:
            self._server.close()
            self._server = None
        self._clients.clear()


# ============================================================
# Client-side QUIC Transport
# ============================================================

if HAS_QUIC:
    class TeraguchiClientProtocol(QuicConnectionProtocol):
        """QUIC protocol handler for the client side."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.stats = QUICStats(connected_at=time.time())
            self.on_control_message: Optional[Callable] = None
            self.on_video_frame: Optional[Callable] = None
            self.on_audio_frame: Optional[Callable] = None
            self.on_connected: Optional[Callable] = None
            self.on_disconnected: Optional[Callable] = None

        def quic_event_received(self, event):
            if isinstance(event, HandshakeCompleted):
                logger.info("QUIC connected to server")
                if self.on_connected:
                    self.on_connected()

            elif isinstance(event, StreamDataReceived):
                self.stats.bytes_received += len(event.data)
                if self.on_control_message:
                    self.on_control_message(event.data, event.stream_id)

            elif isinstance(event, DatagramFrameReceived):
                self.stats.datagrams_received += 1
                self.stats.bytes_received += len(event.data)
                self._handle_datagram(event.data)

            elif isinstance(event, ConnectionTerminated):
                logger.info("QUIC disconnected: %s", event.reason_phrase)
                if self.on_disconnected:
                    self.on_disconnected(event.reason_phrase)

        def _handle_datagram(self, data: bytes):
            parsed = decode_datagram(data)
            if parsed is None:
                return
            channel, flags, timestamp_ms, payload = parsed

            if channel == QUIC_CHANNEL_VIDEO and self.on_video_frame:
                is_keyframe = bool(flags & QUIC_FLAG_KEYFRAME)
                self.on_video_frame(flags, timestamp_ms, payload)
            elif channel == QUIC_CHANNEL_AUDIO and self.on_audio_frame:
                self.on_audio_frame(timestamp_ms, payload)

        def send_control(self, data: bytes, stream_id: int = QUIC_STREAM_CONTROL):
            self._quic.send_stream_data(stream_id, data, end_stream=False)
            self.transmit()


class QUICTransportClient:
    """
    Client-side QUIC transport.

    Connects to the server's QUIC endpoint and provides callbacks
    for received control messages and media datagrams.
    """

    def __init__(self):
        self._protocol: Optional['TeraguchiClientProtocol'] = None
        self._connection = None
        self._connected = False

        # Callbacks
        self.on_control_message: Optional[Callable] = None  # (data, stream_id)
        self.on_video_frame: Optional[Callable] = None       # (flags, timestamp_ms, data)
        self.on_audio_frame: Optional[Callable] = None       # (timestamp_ms, data)
        self.on_connected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def stats(self) -> Optional[QUICStats]:
        if self._protocol:
            return self._protocol.stats
        return None

    async def connect(self, host: str, port: int, verify_cert: bool = True):
        """Connect to the QUIC server.

        SEC-01: ``verify_cert`` defaults to True (verification ON). The only
        way to disable verification is via the double-gate — the caller must
        explicitly pass ``verify_cert=False`` AND the environment must set
        ``TERAGUCHI_ACCEPT_INSECURE=1``. Both conditions emit an ERROR-level
        ``transport.insecure_mode_active`` log event.
        """
        if not HAS_QUIC:
            raise RuntimeError("aioquic not installed")

        from common.tls_opt_out import insecure_tls_allowed

        config = QuicConfiguration(
            is_client=True,
            max_datagram_frame_size=65536,
        )
        config.alpn_protocols = ["teraguchi"]
        config.verify_mode = ssl.CERT_REQUIRED  # SEC-01: verification ON by default.

        # Double-gated dev escape hatch. ``verify_cert=False`` is treated as
        # the --insecure-skip-verify CLI flag; TERAGUCHI_ACCEPT_INSECURE=1
        # must ALSO be set in the environment.
        if not verify_cert and insecure_tls_allowed(cli_flag=True):
            logger.error(
                "transport.insecure_mode_active site=quic env=TERAGUCHI_ACCEPT_INSECURE")
            config.verify_mode = ssl.CERT_NONE

        def create_protocol(*args, **kwargs):
            protocol = TeraguchiClientProtocol(*args, **kwargs)
            protocol.on_control_message = self._on_control
            protocol.on_video_frame = self.on_video_frame
            protocol.on_audio_frame = self.on_audio_frame
            protocol.on_connected = self._on_connected
            protocol.on_disconnected = self._on_disconnected
            return protocol

        try:
            self._connection = await quic_connect(
                host, port,
                configuration=config,
                create_protocol=create_protocol,
            )
            self._protocol = self._connection._protocol
            logger.info("QUIC connection established to %s:%d", host, port)
            return True
        except Exception as e:
            logger.error("QUIC connect failed: %s", e)
            return False

    def _on_control(self, data: bytes, stream_id: int):
        if self.on_control_message:
            self.on_control_message(data, stream_id)

    def _on_connected(self):
        self._connected = True
        if self.on_connected:
            self.on_connected()

    def _on_disconnected(self, reason: str):
        self._connected = False
        if self.on_disconnected:
            self.on_disconnected(reason)

    def send_control(self, data: bytes, stream_id: int = QUIC_STREAM_CONTROL):
        """Send a control message over a reliable QUIC stream."""
        if self._protocol:
            self._protocol.send_control(data, stream_id)

    async def close(self):
        """Close the QUIC connection."""
        if self._connection:
            self._connection.close()
            self._connection = None
        self._connected = False
        self._protocol = None


# ============================================================
# TLS Certificate Helpers
# ============================================================

def _generate_self_signed_cert() -> tuple:
    """Generate a self-signed certificate for QUIC. Returns (cert_path, key_path)."""
    import tempfile

    config_dir = os.path.expanduser("~/.config/teraguchi")
    os.makedirs(config_dir, exist_ok=True)
    cert_path = os.path.join(config_dir, "quic_cert.pem")
    key_path = os.path.join(config_dir, "quic_key.pem")

    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path

    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        import datetime

        key = ec.generate_private_key(ec.SECP256R1())

        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "teraguchi"),
        ])

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )

        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))

        logger.info("Generated self-signed QUIC certificate: %s", cert_path)
        return cert_path, key_path

    except ImportError:
        # Fallback: use openssl command
        import subprocess
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-days", "3650", "-nodes",
            "-keyout", key_path, "-out", cert_path,
            "-subj", "/CN=teraguchi",
        ], capture_output=True)
        return cert_path, key_path


def quic_available() -> bool:
    """Check if QUIC transport is available."""
    return HAS_QUIC
