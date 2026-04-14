"""
Audio player for Teraguchi client.

Receives raw PCM s16le audio from the server and plays via Qt QAudioSink.
Keeps latency low by using a small buffer and dropping old data if behind.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
    from PySide6.QtCore import QIODevice, QByteArray
    QT_AUDIO_AVAILABLE = True
except ImportError:
    QT_AUDIO_AVAILABLE = False
    logger.warning("PySide6.QtMultimedia not available — audio playback disabled")


class AudioPlayer:
    """
    Plays raw PCM s16le audio from the Teraguchi server.

    Uses processedUSecs() for reliable latency tracking on macOS (where
    bytesFree() is unreliable with wireless devices like Apple Vision Pro).
    Drops frames only when far behind, and never mid-chunk to avoid cracks.
    """

    # How much audio to buffer ahead of hardware playback.
    # 400ms works for AVP and other wireless/Bluetooth outputs.
    TARGET_LATENCY_MS = 400
    # Drop incoming frames only when this far ahead — avoids cracks from
    # abrupt PCM discontinuities on wireless devices.
    MAX_BUFFER_MS = 1200

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self._sink: Optional[QAudioSink] = None
        self._io_device: Optional[QIODevice] = None
        self._started = False
        self._frame_count = 0
        self._bytes_written = 0
        # Bytes per millisecond of audio
        self._bytes_per_ms = sample_rate * channels * 2 // 1000

    @property
    def available(self) -> bool:
        return QT_AUDIO_AVAILABLE

    def start(self):
        """Initialize Qt audio output."""
        if not QT_AUDIO_AVAILABLE:
            logger.warning("Audio player not available (no Qt Multimedia)")
            return

        fmt = QAudioFormat()
        fmt.setSampleRate(self.sample_rate)
        fmt.setChannelCount(self.channels)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            logger.warning("No audio output device found")
            return

        logger.info("Audio output device: %s", device.description())

        self._sink = QAudioSink(device, fmt)
        # Small buffer for low latency
        buf_bytes = self._bytes_per_ms * self.TARGET_LATENCY_MS
        self._sink.setBufferSize(buf_bytes)
        self._io_device = self._sink.start()
        self._started = True
        actual_buf = self._sink.bufferSize()
        logger.info("Audio player started (%dHz, %dch, buffer=%d bytes / %dms)",
                     self.sample_rate, self.channels, actual_buf,
                     actual_buf // self._bytes_per_ms)

    def feed(self, codec: int, timestamp_ms: int, data: bytes):
        """Feed a raw PCM audio chunk from the server."""
        if not self._started or not self._io_device or not self._sink:
            return

        self._frame_count += 1

        # processedUSecs() is reliable on macOS (unlike bytesFree()).
        # It returns microseconds of audio already sent to hardware.
        played_ms = self._sink.processedUSecs() // 1000
        written_ms = self._bytes_written // self._bytes_per_ms
        buffered_ms = written_ms - played_ms

        if buffered_ms > self.MAX_BUFFER_MS:
            # Too far ahead — drop this whole chunk cleanly (no mid-chunk cut)
            if self._frame_count % 50 == 0:
                logger.debug("Audio ahead by %dms, dropping frame", buffered_ms)
            return

        written = self._io_device.write(QByteArray(data))
        if written > 0:
            self._bytes_written += written

    def stop(self):
        """Stop audio playback."""
        self._started = False
        self._bytes_written = 0
        if self._sink:
            self._sink.stop()
            self._sink = None
        self._io_device = None
        logger.info("Audio player stopped")
