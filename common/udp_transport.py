"""
UDP media transport with DTLS encryption.

Handles high-throughput, low-latency video and audio streaming over UDP.
Designed to drop stale frames rather than stall (unlike TCP).

Architecture:
- Server sends video/audio frames as UDP datagrams
- Each datagram is self-contained or part of a fragmented frame
- Lost packets are simply skipped (no retransmission)
- DTLS provides encryption without TCP overhead
- Automatic MTU discovery and fragmentation
- Sequence numbers for ordering and loss detection
- Timestamp-based jitter buffer on the client side

Packet format:
  [2 bytes: magic 0xTG]
  [1 byte:  channel]       0x01=video, 0x02=audio
  [1 byte:  flags]         bit0=keyframe, bit1=fragment, bit2=last_fragment
  [4 bytes: sequence]      monotonic per-channel sequence number (big-endian)
  [4 bytes: timestamp_ms]  capture timestamp (big-endian)
  [2 bytes: fragment_id]   which fragment of a multi-packet frame (big-endian)
  [2 bytes: fragment_total] total fragments in this frame (big-endian)
  [payload...]

Total header: 16 bytes.  Leaves ~1484 bytes for payload per packet (MTU 1500).
"""

import asyncio
import hashlib
import logging
import os
import socket
import ssl
import struct
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Constants
UDP_MAGIC = 0x5447           # 'TG' for Teraguchi
UDP_HEADER_SIZE = 16
# Measured 2026-07: the Oslo→London VPN tunnel drops UDP payloads >1250 B
# (path MTU ~1278). 1400 caused ~35% silent video-fragment loss — every
# full-size fragment vanished while small ones passed. 1200 leaves margin
# for varying tunnel overhead. TODO: per-path MTU probe during negotiation.
DEFAULT_MTU = 1200           # Conservative MTU leaving room for IP/UDP/DTLS headers
MAX_PACKET_SIZE = 65507      # Max UDP payload

# Channel IDs
CHANNEL_VIDEO = 0x01
CHANNEL_AUDIO = 0x02

# Flags
FLAG_KEYFRAME = 0x01
FLAG_FRAGMENT = 0x02
FLAG_LAST_FRAGMENT = 0x04
FLAG_FEC = 0x08              # XOR parity packet (task #17)

# FEC group size: one XOR parity packet per K data fragments recovers any
# single lost fragment in the group (the dominant WAN loss pattern is
# isolated single-packet drops). Overhead = 1/K = 12.5%.
FEC_GROUP = 8


def encode_udp_header(channel: int, flags: int, sequence: int,
                      timestamp_ms: int, frag_id: int = 0,
                      frag_total: int = 1) -> bytes:
    """Encode a 16-byte UDP header: magic(2)+channel(1)+flags(1)+seq(4)+ts(4)+frag_id(2)+frag_total(2)."""
    return struct.pack("!HBBII HH",
                       UDP_MAGIC, channel, flags,
                       sequence & 0xFFFFFFFF,
                       timestamp_ms & 0xFFFFFFFF,
                       frag_id, frag_total)


def decode_udp_header(data: bytes) -> Optional[tuple]:
    """
    Returns (channel, flags, sequence, timestamp_ms, frag_id, frag_total, payload)
    or None if invalid.
    """
    if len(data) < UDP_HEADER_SIZE:
        return None
    magic, channel, flags, seq, ts, frag_id, frag_total = struct.unpack(
        "!HBBII HH", data[:UDP_HEADER_SIZE])
    if magic != UDP_MAGIC:
        return None
    return channel, flags, seq, ts, frag_id, frag_total, data[UDP_HEADER_SIZE:]


# ============================================================
# Frame Fragmenter (for frames larger than MTU)
# ============================================================

class FrameFragmenter:
    """Splits large frames into MTU-sized UDP packets."""

    def __init__(self, mtu: int = DEFAULT_MTU):
        self.mtu = mtu
        self.payload_size = mtu - UDP_HEADER_SIZE
        self._video_seq = 0
        self._audio_seq = 0

    def fragment_frame(self, channel: int, data: bytes,
                       timestamp_ms: int, is_keyframe: bool = False) -> List[bytes]:
        """
        Fragment a frame into UDP packets.

        Returns a list of complete UDP packets (header + payload) ready to send.
        """
        if channel == CHANNEL_VIDEO:
            self._video_seq += 1
            seq = self._video_seq
        else:
            self._audio_seq += 1
            seq = self._audio_seq

        base_flags = FLAG_KEYFRAME if is_keyframe else 0

        if len(data) <= self.payload_size:
            # Single packet — no fragmentation needed
            header = encode_udp_header(channel, base_flags, seq, timestamp_ms, 0, 1)
            return [header + data]

        # Fragment
        fragments = []
        chunks = []
        total = (len(data) + self.payload_size - 1) // self.payload_size
        for i in range(total):
            offset = i * self.payload_size
            chunk = data[offset:offset + self.payload_size]
            chunks.append(chunk)

            flags = base_flags | FLAG_FRAGMENT
            if i == total - 1:
                flags |= FLAG_LAST_FRAGMENT

            header = encode_udp_header(channel, flags, seq, timestamp_ms, i, total)
            fragments.append(header + chunk)

        # FEC (task #17): one XOR parity per FEC_GROUP data fragments for
        # video. Parity block = XOR over [len:2B][chunk zero-padded]; any
        # single lost fragment in the group is recoverable. frag_id carries
        # the group index; frag_total mirrors the frame's so the receiver
        # can derive the group's span. 12.5% overhead, video only.
        if channel == CHANNEL_VIDEO:
            blk_len = 2 + self.payload_size
            for g in range(0, total, FEC_GROUP):
                group = chunks[g:g + FEC_GROUP]
                # NB: a singleton tail group still gets parity — the XOR of
                # one block is the block itself, i.e. a retransmit copy, and
                # without it the frame's last fragment is unprotected.
                parity = bytearray(blk_len)
                for chunk in group:
                    blk = len(chunk).to_bytes(2, "big") + chunk
                    for j, b in enumerate(blk):
                        parity[j] ^= b
                header = encode_udp_header(
                    channel, base_flags | FLAG_FRAGMENT | FLAG_FEC,
                    seq, timestamp_ms, g // FEC_GROUP, total)
                fragments.append(header + bytes(parity))

        return fragments


# ============================================================
# Frame Reassembler (client-side)
# ============================================================

@dataclass
class PendingFrame:
    """A frame being reassembled from fragments."""
    sequence: int = 0
    timestamp_ms: int = 0
    channel: int = 0
    flags: int = 0
    total_fragments: int = 0
    received_fragments: Dict[int, bytes] = field(default_factory=dict)
    created_at: float = 0.0
    # FEC (task #17): group_index -> XOR parity block ([len:2B]+padded data)
    fec_packets: Dict[int, bytes] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return len(self.received_fragments) == self.total_fragments

    def assemble(self) -> bytes:
        """Assemble all fragments in order."""
        parts = []
        for i in range(self.total_fragments):
            parts.append(self.received_fragments[i])
        return b"".join(parts)


class FrameReassembler:
    """
    Reassembles fragmented UDP frames and handles packet loss.

    Features:
    - Reassembles multi-packet frames
    - Drops incomplete frames after timeout (no retransmission)
    - Skips frames older than the latest received keyframe
    - Tracks packet loss statistics
    """

    def __init__(self, timeout_ms: float = 100.0):
        self.timeout_ms = timeout_ms
        self._pending: Dict[Tuple[int, int], PendingFrame] = {}  # (channel, seq) -> PendingFrame
        self._last_complete_seq: Dict[int, int] = defaultdict(int)  # channel -> last complete seq
        self._packets_received = 0
        self._packets_lost = 0
        self._frames_completed = 0
        self._frames_dropped = 0

    def feed_packet(self, data: bytes) -> Optional[tuple]:
        """
        Feed a raw UDP packet.

        Returns (channel, flags, timestamp_ms, frame_data) if a complete frame
        is ready, or None if still assembling / packet was dropped.
        """
        parsed = decode_udp_header(data)
        if parsed is None:
            return None

        channel, flags, seq, ts, frag_id, frag_total, payload = parsed
        self._packets_received += 1

        # Skip frames at-or-older than what we've already completed. MUST be
        # <= (not <): a trailing FEC parity packet for an already-completed
        # frame would otherwise resurrect it as a ghost PendingFrame that
        # never completes — polluting loss stats and leaking memory.
        if seq <= self._last_complete_seq.get(channel, 0):
            return None

        # Single-packet frame (no fragmentation)
        if frag_total <= 1:
            self._last_complete_seq[channel] = seq
            self._frames_completed += 1
            self._cleanup_old(channel, seq)
            return channel, flags, ts, payload

        # Multi-packet frame: reassemble
        key = (channel, seq)
        if key not in self._pending:
            self._pending[key] = PendingFrame(
                sequence=seq,
                timestamp_ms=ts,
                channel=channel,
                flags=flags,
                total_fragments=frag_total,
                created_at=time.time(),
            )

        frame = self._pending[key]
        if flags & FLAG_FEC:
            # Parity packet: frag_id is the FEC group index.
            frame.fec_packets[frag_id] = payload
        else:
            frame.received_fragments[frag_id] = payload

        # Inherit keyframe flag from any fragment
        if flags & FLAG_KEYFRAME:
            frame.flags |= FLAG_KEYFRAME

        # FEC recovery: if any group is missing exactly one data fragment
        # and its parity arrived, XOR-reconstruct the missing one.
        if frame.fec_packets and not frame.complete:
            self._try_fec_recover(frame)

        if frame.complete:
            del self._pending[key]
            self._last_complete_seq[channel] = seq
            self._frames_completed += 1
            self._cleanup_old(channel, seq)
            return channel, frame.flags, frame.timestamp_ms, frame.assemble()

        return None

    def _try_fec_recover(self, frame: "PendingFrame"):
        """XOR-recover single missing fragments per FEC group (task #17).

        Parity block layout: [len:2B][chunk padded to block size]. XORing
        the parity with every *received* block in the group leaves exactly
        the missing block when one fragment was lost.
        """
        total = frame.total_fragments
        for g, parity in list(frame.fec_packets.items()):
            lo = g * FEC_GROUP
            hi = min(lo + FEC_GROUP, total)
            missing = [i for i in range(lo, hi)
                       if i not in frame.received_fragments]
            if len(missing) != 1:
                continue
            blk = bytearray(parity)
            for i in range(lo, hi):
                if i == missing[0]:
                    continue
                chunk = frame.received_fragments[i]
                other = len(chunk).to_bytes(2, "big") + chunk
                for j, b in enumerate(other):
                    blk[j] ^= b
            length = int.from_bytes(blk[:2], "big")
            if 0 < length <= len(blk) - 2:
                frame.received_fragments[missing[0]] = bytes(blk[2:2 + length])
                self._frames_recovered_fec = getattr(
                    self, "_frames_recovered_fec", 0) + 1
                del frame.fec_packets[g]

    def _cleanup_old(self, channel: int, current_seq: int):
        """Drop pending frames that are older than the current completed frame."""
        stale_keys = [
            k for k in self._pending
            if k[0] == channel and k[1] < current_seq
        ]
        for k in stale_keys:
            frame = self._pending.pop(k)
            lost = frame.total_fragments - len(frame.received_fragments)
            self._packets_lost += lost
            self._frames_dropped += 1

    def cleanup_timed_out(self):
        """Drop frames that have been pending too long."""
        now = time.time()
        timeout_sec = self.timeout_ms / 1000.0
        stale_keys = [
            k for k, f in self._pending.items()
            if now - f.created_at > timeout_sec
        ]
        for k in stale_keys:
            frame = self._pending.pop(k)
            lost = frame.total_fragments - len(frame.received_fragments)
            self._packets_lost += lost
            self._frames_dropped += 1

    @property
    def packet_loss_rate(self) -> float:
        total = self._packets_received + self._packets_lost
        if total == 0:
            return 0.0
        return self._packets_lost / total

    @property
    def stats(self) -> dict:
        return {
            "packets_received": self._packets_received,
            "packets_lost": self._packets_lost,
            "packet_loss_rate": round(self.packet_loss_rate * 100, 2),
            "frames_completed": self._frames_completed,
            "frames_dropped": self._frames_dropped,
            "frames_recovered_fec": getattr(self, "_frames_recovered_fec", 0),
            "pending_frames": len(self._pending),
        }


# ============================================================
# UDP Server Transport
# ============================================================

# NAT hole-punch packet magic. The client sends `PUNCH_MAGIC + token` datagrams
# to the server's UDP port right after UDP_ANNOUNCE; the server's recv loop
# matches the token (which traveled over the authenticated WebSocket) and
# registers the client's *observed* source address. Required whenever the
# client sits behind NAT/VPN (its announced local addr is unreachable).
PUNCH_MAGIC = b"TGPN1"


class UDPMediaServer:
    """
    Server-side UDP transport for media streaming.

    Sends video and audio frames as UDP datagrams to connected clients.
    Each client must first register via the TCP control channel, which
    provides the client's UDP address — and/or via a NAT hole-punch
    datagram, which provides the client's *observed* address (the one
    that actually works through NAT/VPN).
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 443,
                 mtu: int = DEFAULT_MTU):
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._fragmenter = FrameFragmenter(mtu)
        self._clients: Dict[str, tuple] = {}  # client_id -> (host, port)
        self._running = False
        self._send_lock = threading.Lock()
        self._total_bytes_sent = 0
        self._total_packets_sent = 0
        self._frames_sent_udp = 0
        self._send_errors = 0
        self._start_time = 0.0
        self._recv_thread: Optional[threading.Thread] = None
        # Called from the recv thread as on_punch(token: str, addr: tuple)
        # when a hole-punch datagram arrives. Wired by HybridServerTransport.
        self.on_punch: Optional[Callable] = None

    @property
    def bandwidth_mbps(self) -> float:
        elapsed = time.time() - self._start_time
        if elapsed <= 0:
            return 0.0
        return (self._total_bytes_sent * 8) / (elapsed * 1_000_000)

    def start(self):
        """Create and bind the UDP socket + start the punch recv loop."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
        self._sock.bind((self.host, self.port))
        self._sock.settimeout(1.0)
        self._running = True
        self._start_time = time.time()
        # Recv loop: the only client->server UDP traffic is hole-punch
        # datagrams; everything else is ignored. This is what lets us learn
        # the client's observed (post-NAT) address.
        self._recv_thread = threading.Thread(
            target=self._recv_loop, daemon=True, name="udp-media-punch-recv")
        self._recv_thread.start()
        logger.info("UDP media server listening on %s:%d", self.host, self.port)

    def _recv_loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.debug("UDP server recv error")
                break
            if data.startswith(PUNCH_MAGIC) and self.on_punch:
                token = data[len(PUNCH_MAGIC):].decode("ascii", "ignore")
                try:
                    self.on_punch(token, addr)
                except Exception as e:  # never kill the recv loop
                    logger.warning("on_punch handler error: %s", e)

    def send_probe(self, addr: tuple, payload: bytes):
        """Send a probe frame to one explicit address (not the client table).

        Used during negotiation: probes go to the announced addr AND the
        punch-observed addr; whichever is reachable delivers.
        """
        if not self._running:
            return
        packets = self._fragmenter.fragment_frame(
            CHANNEL_VIDEO, payload, int(time.time() * 1000), False)
        with self._send_lock:
            for packet in packets:
                try:
                    self._sock.sendto(packet, addr)
                except OSError as e:
                    logger.debug("UDP probe to %s failed: %s", addr, e)

    def send_video_frame_to(self, client_id: str, data: bytes,
                            timestamp_ms: int, is_keyframe: bool = False) -> bool:
        """Send a video frame to ONE registered client. Returns False if the
        client is not registered (caller should fall back to TCP)."""
        addr = self._clients.get(client_id)
        if not self._running or addr is None:
            return False
        packets = self._fragmenter.fragment_frame(
            CHANNEL_VIDEO, data, timestamp_ms, is_keyframe)
        with self._send_lock:
            for i, packet in enumerate(packets):
                try:
                    self._sock.sendto(packet, addr)
                    self._total_bytes_sent += len(packet)
                    self._total_packets_sent += 1
                except OSError as e:
                    self._send_errors += 1
                    logger.debug("UDP send to %s failed: %s", client_id, e)
                    return False
                # Burst pacing: blasting a large frame (keyframe ≈ 90 pkts)
                # at line rate overruns VPN-tunnel encap queues and the whole
                # tail vanishes. A ~50 µs gap every few packets keeps the
                # burst under queue depth at negligible latency cost
                # (90 pkts ≈ +1.5 ms worst case on keyframes only).
                if i % 4 == 3:
                    time.sleep(0.00005)
            self._frames_sent_udp += 1
        return True

    def register_client(self, client_id: str, addr: tuple):
        """Register a client's UDP address for receiving media."""
        self._clients[client_id] = addr
        logger.info("UDP client registered: %s -> %s:%d", client_id, addr[0], addr[1])

    def unregister_client(self, client_id: str):
        """Remove a client."""
        self._clients.pop(client_id, None)
        logger.info("UDP client unregistered: %s", client_id)

    def send_video_frame(self, data: bytes, timestamp_ms: int,
                         is_keyframe: bool = False):
        """
        Send a video frame to all registered clients.

        The frame is automatically fragmented if it exceeds MTU.
        Packets are sent without waiting for acknowledgement.
        """
        if not self._running or not self._clients:
            return

        packets = self._fragmenter.fragment_frame(
            CHANNEL_VIDEO, data, timestamp_ms, is_keyframe)

        with self._send_lock:
            for packet in packets:
                for client_id, addr in list(self._clients.items()):
                    try:
                        self._sock.sendto(packet, addr)
                        self._total_bytes_sent += len(packet)
                        self._total_packets_sent += 1
                    except OSError as e:
                        logger.debug("UDP send to %s failed: %s", client_id, e)

    def send_audio_frame(self, data: bytes, timestamp_ms: int):
        """Send an audio frame to all registered clients."""
        if not self._running or not self._clients:
            return

        packets = self._fragmenter.fragment_frame(
            CHANNEL_AUDIO, data, timestamp_ms, is_keyframe=False)

        with self._send_lock:
            for packet in packets:
                for client_id, addr in list(self._clients.items()):
                    try:
                        self._sock.sendto(packet, addr)
                        self._total_bytes_sent += len(packet)
                        self._total_packets_sent += 1
                    except OSError as e:
                        logger.debug("UDP audio send failed: %s", e)

    def stop(self):
        """Stop the UDP server."""
        self._running = False
        if self._sock:
            self._sock.close()
            self._sock = None
        self._clients.clear()
        logger.info("UDP media server stopped (sent %d packets, %.1f MB)",
                     self._total_packets_sent,
                     self._total_bytes_sent / (1024 * 1024))


# ============================================================
# UDP Client Transport
# ============================================================

class UDPMediaClient:
    """
    Client-side UDP transport for receiving media.

    Listens for incoming UDP media packets, reassembles fragments,
    and delivers complete frames via callbacks. Runs a receiver
    thread for non-blocking operation.
    """

    def __init__(self, mtu: int = DEFAULT_MTU):
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._reassembler = FrameReassembler(timeout_ms=150.0)
        self._running = False
        self._local_port = 0

        # Callbacks
        self.on_video_frame: Optional[Callable] = None   # (flags, timestamp_ms, data)
        self.on_audio_frame: Optional[Callable] = None   # (timestamp_ms, data)

    @property
    def local_port(self) -> int:
        return self._local_port

    @property
    def stats(self) -> dict:
        return self._reassembler.stats

    def start(self, local_port: int = 0) -> int:
        """
        Start receiving UDP media.

        Args:
            local_port: Port to listen on (0 = auto-assign)

        Returns:
            The actual port being used (report this to server via TCP).
        """
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self._sock.bind(("0.0.0.0", local_port))
        self._sock.settimeout(1.0)

        self._local_port = self._sock.getsockname()[1]
        self._running = True

        self._thread = threading.Thread(
            target=self._receive_loop,
            daemon=True,
            name="udp-media-recv",
        )
        self._thread.start()

        logger.info("UDP media client listening on port %d", self._local_port)
        return self._local_port

    def punch(self, server_addr: tuple, token: str, count: int = 3):
        """Send NAT hole-punch datagrams to the server's UDP port.

        Sent from the SAME socket that listens for media, which (a) opens
        the NAT/VPN pinhole for the server's return traffic and (b) shows
        the server our observed source address. The token ties the punch
        to our authenticated WebSocket session (it was sent in
        UDP_ANNOUNCE over TCP), so the server won't register strangers.
        """
        if self._sock is None:
            return
        pkt = PUNCH_MAGIC + token.encode("ascii")
        for _ in range(count):
            try:
                self._sock.sendto(pkt, server_addr)
            except OSError as e:
                logger.debug("UDP punch failed: %s", e)
                return
            time.sleep(0.05)

    def _receive_loop(self):
        """Receive and process UDP packets."""
        cleanup_interval = 0.1  # Clean up stale fragments every 100ms
        last_cleanup = time.time()

        while self._running:
            try:
                data, addr = self._sock.recvfrom(MAX_PACKET_SIZE)
            except socket.timeout:
                self._reassembler.cleanup_timed_out()
                continue
            except OSError:
                if self._running:
                    logger.debug("UDP recv error")
                break

            result = self._reassembler.feed_packet(data)
            if result is not None:
                channel, flags, timestamp_ms, frame_data = result

                if channel == CHANNEL_VIDEO and self.on_video_frame:
                    self.on_video_frame(flags, timestamp_ms, frame_data)
                elif channel == CHANNEL_AUDIO and self.on_audio_frame:
                    self.on_audio_frame(timestamp_ms, frame_data)

            # Periodic cleanup
            now = time.time()
            if now - last_cleanup > cleanup_interval:
                self._reassembler.cleanup_timed_out()
                last_cleanup = now

    def stop(self):
        """Stop receiving."""
        self._running = False
        if self._sock:
            self._sock.close()
            self._sock = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("UDP media client stopped. Stats: %s", self._reassembler.stats)


# ============================================================
# Bandwidth Estimator
# ============================================================

class BandwidthEstimator:
    """
    Estimates available bandwidth and adjusts sending rate.

    Uses a simple packet-pair technique + loss-based adaptation:
    - If packet loss > 2%: reduce bitrate
    - If packet loss < 0.5% and bandwidth headroom: increase bitrate
    - Smooth changes to avoid oscillation
    """

    def __init__(self, initial_mbps: float = 50.0, min_mbps: float = 1.0,
                 max_mbps: float = 200.0):
        self.current_mbps = initial_mbps
        self.min_mbps = min_mbps
        self.max_mbps = max_mbps
        self._loss_history: list = []
        self._last_adjustment = time.time()
        self._adjustment_interval = 2.0  # seconds

    def report_loss_rate(self, loss_pct: float):
        """Report current packet loss percentage."""
        self._loss_history.append(loss_pct)
        if len(self._loss_history) > 30:
            self._loss_history = self._loss_history[-20:]

    def get_target_bitrate_mbps(self) -> float:
        """Get the recommended target bitrate based on conditions."""
        now = time.time()
        if now - self._last_adjustment < self._adjustment_interval:
            return self.current_mbps

        if not self._loss_history:
            return self.current_mbps

        avg_loss = sum(self._loss_history[-5:]) / len(self._loss_history[-5:])
        self._last_adjustment = now

        if avg_loss > 5.0:
            # Heavy loss: cut bandwidth significantly
            self.current_mbps = max(self.min_mbps, self.current_mbps * 0.6)
            logger.info("Bandwidth: heavy loss (%.1f%%), reducing to %.1f Mbps",
                        avg_loss, self.current_mbps)
        elif avg_loss > 2.0:
            # Moderate loss: reduce gently
            self.current_mbps = max(self.min_mbps, self.current_mbps * 0.85)
            logger.debug("Bandwidth: moderate loss (%.1f%%), reducing to %.1f Mbps",
                         avg_loss, self.current_mbps)
        elif avg_loss < 0.5:
            # Low loss: probe for more bandwidth
            self.current_mbps = min(self.max_mbps, self.current_mbps * 1.05)

        return self.current_mbps

    @property
    def target_bitrate_kbps(self) -> int:
        return int(self.current_mbps * 1000)
