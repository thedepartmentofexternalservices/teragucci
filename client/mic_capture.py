"""
Microphone capture for Teraguchi client.

Captures local microphone audio (PCM s16le, 48 kHz, mono) via Qt QAudioSource
and provides 20 ms chunks for streaming to the server as MIC binary frames.
"""

import logging
from typing import Optional, Callable

logger = logging.getLogger(__name__)

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
    from PySide6.QtCore import QIODevice, QTimer
    QT_AUDIO_AVAILABLE = True
except ImportError:
    QT_AUDIO_AVAILABLE = False
    logger.warning("PySide6.QtMultimedia not available — mic capture disabled")


class MicCapture:
    """
    Captures microphone audio and delivers raw PCM chunks via callback.

    Uses QAudioSource (Qt Multimedia) — same stack as AudioPlayer — so no
    extra dependencies are needed.  Chunks are 20 ms of mono s16le at 48 kHz
    (960 samples = 1 920 bytes), which aligns with Opus frame sizes.

    Usage::

        mic = MicCapture()
        mic.on_mic_frame = lambda data: protocol.send_binary(mic_header + data)
        mic.start()
        ...
        mic.stop()
    """

    SAMPLE_RATE = 48_000
    CHANNELS = 1          # Mono — sufficient for voice, half the bandwidth
    CHUNK_MS = 20         # 20 ms → 960 samples → 1 920 bytes per chunk

    def __init__(self):
        self._source: Optional["QAudioSource"] = None
        self._io: Optional["QIODevice"] = None
        self._timer: Optional["QTimer"] = None
        self._started = False
        self._bytes_per_chunk = (
            self.SAMPLE_RATE * self.CHANNELS * 2 * self.CHUNK_MS // 1000
        )
        self.on_mic_frame: Optional[Callable[[bytes], None]] = None

    # ── Public API ───────────────────────────────

    @property
    def available(self) -> bool:
        if not QT_AUDIO_AVAILABLE:
            return False
        return not QMediaDevices.defaultAudioInput().isNull()

    def start(self):
        if not QT_AUDIO_AVAILABLE or self._started:
            return

        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            logger.warning("MicCapture: no microphone found")
            return

        fmt = QAudioFormat()
        fmt.setSampleRate(self.SAMPLE_RATE)
        fmt.setChannelCount(self.CHANNELS)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

        if not device.isFormatSupported(fmt):
            logger.warning("MicCapture: s16le 48 kHz mono not supported by %s",
                           device.description())
            return

        logger.info("MicCapture: using device '%s'", device.description())
        self._source = QAudioSource(device, fmt)
        # Buffer = 4 chunks so we never starve the read timer
        self._source.setBufferSize(self._bytes_per_chunk * 4)
        self._io = self._source.start()
        self._started = True

        self._timer = QTimer()
        self._timer.setInterval(self.CHUNK_MS)
        self._timer.timeout.connect(self._read_chunk)
        self._timer.start()

        logger.info("MicCapture: started (%d Hz, %d ch, %d ms chunks)",
                    self.SAMPLE_RATE, self.CHANNELS, self.CHUNK_MS)

    def stop(self):
        if not self._started:
            return
        if self._timer:
            self._timer.stop()
            self._timer = None
        if self._source:
            self._source.stop()
            self._source = None
        self._io = None
        self._started = False
        logger.info("MicCapture: stopped")

    # ── Internal ─────────────────────────────────

    def _read_chunk(self):
        if not self._io or not self.on_mic_frame:
            return
        available = self._io.bytesAvailable()
        if available <= 0:
            return
        # Read exactly one chunk; if more is buffered it will be picked up next tick
        to_read = min(available, self._bytes_per_chunk)
        raw = self._io.read(to_read)
        if raw and len(raw) > 0:
            self.on_mic_frame(bytes(raw))
