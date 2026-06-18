"""
Server-side connection health monitor.

Tracks:
- Round-trip latency (via ping/pong)
- Actual vs target FPS
- Bandwidth usage
- Dropped frames (encoder backpressure)
- Encode/capture timing
"""

import logging
import time
import collections
from dataclasses import dataclass
from typing import Optional

from common.messages import HealthStats

logger = logging.getLogger(__name__)


class HealthMonitor:
    """
    Tracks connection and streaming health metrics.
    Periodically produces HealthStats for client display.
    """

    def __init__(self, target_fps: float = 30.0):
        self.target_fps = target_fps

        # RTT tracking
        self._rtt_samples: collections.deque = collections.deque(maxlen=100)
        self._pending_pings: dict = {}  # sequence -> timestamp_ms
        self._ping_sequence = 0

        # Frame tracking
        self._frames_sent = 0
        self._frames_dropped = 0
        self._frame_times: collections.deque = collections.deque(maxlen=120)

        # Timing
        self._encode_times: collections.deque = collections.deque(maxlen=120)
        self._capture_times: collections.deque = collections.deque(maxlen=120)
        self._input_latencies: collections.deque = collections.deque(maxlen=120)
        # OBS-02 (Plan 01-14) — per-stage latency beyond capture/encode. The
        # client-reported stages (decode, display) will be submitted via the
        # Phase 4 smoke harness once HealthPong carries them; transmit is
        # server-side and can be populated today.
        self._transmit_times: collections.deque = collections.deque(maxlen=120)
        self._decode_times: collections.deque = collections.deque(maxlen=120)
        self._display_times: collections.deque = collections.deque(maxlen=120)

        # OBS-03 (Plan 01-14) — keyframe telemetry. keyframe_requested is
        # incremented by ClientSession.enqueue on the first drop of a streak
        # (STAB-04 IDR-on-drop path). keyframe_emitted is incremented by
        # SessionRuntime._on_encoded_frame when the encoder reports
        # is_keyframe=True. Both counters surface in HealthStats for the
        # client overlay.
        self.keyframe_requested = 0
        self.keyframe_emitted = 0

        # Optional reference to the active VideoEncoder so get_stats()
        # can pull the encoder's own rolling average without main.py
        # having to push samples. Set externally after construction.
        self.encoder_ref = None

        # Bandwidth
        self._bytes_sent = 0
        self._bandwidth_window_start = time.time()
        self._bandwidth_bytes_window = 0

        # Current encoder state (set externally)
        self.current_codec = "h264"
        self.current_chroma = "yuv444"
        self.current_resolution = "1920x1080"
        self.clients_connected = 0

    def next_ping_sequence(self) -> int:
        """Get next ping sequence number and record send time."""
        self._ping_sequence += 1
        self._pending_pings[self._ping_sequence] = time.time() * 1000
        return self._ping_sequence

    def record_pong(self, sequence: int, ping_timestamp_ms: int):
        """Record a pong response and calculate RTT."""
        send_time = self._pending_pings.pop(sequence, None)
        if send_time is not None:
            rtt = time.time() * 1000 - send_time
            self._rtt_samples.append(rtt)
        # Clean old pending pings (>10s old)
        now = time.time() * 1000
        stale = [s for s, t in self._pending_pings.items() if now - t > 10000]
        for s in stale:
            self._pending_pings.pop(s, None)

    def record_frame_sent(self, byte_count: int):
        """Record that a frame was sent."""
        self._frames_sent += 1
        self._frame_times.append(time.time())
        self._bytes_sent += byte_count
        self._bandwidth_bytes_window += byte_count

    def record_frame_dropped(self):
        """Record that a frame was dropped (backpressure/slow client)."""
        self._frames_dropped += 1

    def record_encode_time(self, ms: float):
        self._encode_times.append(ms)

    def record_capture_time(self, ms: float):
        self._capture_times.append(ms)

    def record_input_latency(self, ms: float):
        self._input_latencies.append(ms)

    # OBS-02 — new per-stage recorders (Plan 01-14). Same append-to-deque
    # pattern as the existing three. Intentionally simple — aggregation lives
    # in get_stats() via _avg_deque.
    def record_transmit_time(self, ms: float):
        self._transmit_times.append(ms)

    def record_decode_time(self, ms: float):
        self._decode_times.append(ms)

    def record_display_time(self, ms: float):
        self._display_times.append(ms)

    # OBS-03 — keyframe telemetry counters (Plan 01-14). Plain int bumps; no
    # locking needed because all call sites run on the main asyncio event loop.
    def record_keyframe_requested(self):
        self.keyframe_requested += 1

    def record_keyframe_emitted(self):
        self.keyframe_emitted += 1

    @property
    def avg_rtt_ms(self) -> float:
        if not self._rtt_samples:
            return 0.0
        return sum(self._rtt_samples) / len(self._rtt_samples)

    @property
    def actual_fps(self) -> float:
        if len(self._frame_times) < 2:
            return 0.0
        elapsed = self._frame_times[-1] - self._frame_times[0]
        if elapsed <= 0:
            return 0.0
        return (len(self._frame_times) - 1) / elapsed

    @property
    def bandwidth_mbps(self) -> float:
        now = time.time()
        elapsed = now - self._bandwidth_window_start
        if elapsed <= 0:
            return 0.0
        mbps = (self._bandwidth_bytes_window * 8) / (elapsed * 1_000_000)
        # Reset window every 5 seconds
        if elapsed > 5.0:
            self._bandwidth_window_start = now
            self._bandwidth_bytes_window = 0
        return mbps

    def _avg_deque(self, d: collections.deque) -> float:
        if not d:
            return 0.0
        return sum(d) / len(d)

    def get_stats(self) -> HealthStats:
        """Build current health statistics."""
        # Prefer the encoder's own rolling average if we have a reference.
        # Note: this is the ffmpeg stdin-pipe write time, which is near-zero
        # unless ffmpeg is stalling under backpressure — it's a pipeline
        # health indicator more than a true encode-latency number.
        if self.encoder_ref is not None:
            encode_ms = self.encoder_ref.avg_encode_time_ms
        else:
            encode_ms = self._avg_deque(self._encode_times)

        return HealthStats(
            rtt_ms=round(self.avg_rtt_ms, 1),
            fps_actual=round(self.actual_fps, 1),
            fps_target=self.target_fps,
            bandwidth_mbps=round(self.bandwidth_mbps, 2),
            frames_sent=self._frames_sent,
            frames_dropped=self._frames_dropped,
            # Fork: encode_ms is sourced live from the encoder when an
            # encoder_ref is wired (else falls back to the rolling deque).
            encode_time_ms=round(encode_ms, 1),
            capture_time_ms=round(self._avg_deque(self._capture_times), 1),
            input_latency_ms=round(self._avg_deque(self._input_latencies), 1),
            # OBS-02 — Plan 01-14
            transmit_time_ms=round(self._avg_deque(self._transmit_times), 1),
            decode_time_ms=round(self._avg_deque(self._decode_times), 1),
            display_time_ms=round(self._avg_deque(self._display_times), 1),
            # OBS-03 — Plan 01-14
            keyframe_requested=self.keyframe_requested,
            keyframe_emitted=self.keyframe_emitted,
            codec=self.current_codec,
            chroma=self.current_chroma,
            resolution=self.current_resolution,
            clients_connected=self.clients_connected,
        )
