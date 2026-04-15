"""
Microphone injection into PipeWire/PulseAudio for Teraguchi server.

Architecture
------------
  Client PCM  →  WebSocket binary (MIC frame)
              →  server/main.py (binary message handler)
              →  MicInjector.write(pcm)
              →  writer thread → pacat stdin
              →  null-sink  "teraguchi_mic_sink"
              →  null-sink monitor → module-virtual-source
              →  "teraguchi_mic"  ← apps see this as a real microphone

Why virtual-source on top of null-sink
---------------------------------------
GNOME Settings (and most desktop apps) only show proper Source devices,
not monitor sources.  module-virtual-source wraps the null-sink monitor
and presents it as a first-class Source that appears in GNOME Sound
Settings, Zoom, OBS, etc.

Tested on PipeWire 0.3.48 (Ubuntu 22.04).
"""

import logging
import os
import pwd
import queue
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_SINK_NAME   = "teraguchi_mic_sink"    # internal null-sink (pacat target)
_SOURCE_NAME = "teraguchi_mic"         # virtual-source visible in GNOME
_SAMPLE_RATE = 48_000
_CHANNELS    = 1
_FORMAT      = "s16le"


class MicInjector:
    """
    Feeds client microphone PCM into a PipeWire virtual microphone source.

    Creates two PA modules:
      1. module-null-sink  (teraguchi_mic_sink)  — pacat plays into this
      2. module-virtual-source (teraguchi_mic)   — wraps the monitor,
         visible as a real mic in GNOME Settings and all PulseAudio apps

    start/stop are idempotent.  write() is thread-safe and non-blocking.
    """

    def __init__(self, uid: int, gid: int, pa_socket: Optional[str] = None):
        self._uid = uid
        self._gid = gid
        self._pa_server = pa_socket or f"unix:/run/user/{uid}/pulse/native"
        self._sink_module_idx: Optional[str] = None
        self._source_module_idx: Optional[str] = None
        self._pacat: Optional[subprocess.Popen] = None
        self._queue: queue.Queue = queue.Queue(maxsize=20)
        self._thread: Optional[threading.Thread] = None
        self._started = False

    # ── Lifecycle ────────────────────────────────

    def start(self) -> bool:
        """Create PA modules and start pacat.  Returns True on success."""
        if self._started:
            return True
        try:
            self._load_sink()
            self._load_virtual_source()
            self._start_pacat()
            self._started = True  # must be set before thread reads it
            self._thread = threading.Thread(
                target=self._writer_loop, name="mic-injector", daemon=True)
            self._thread.start()
            logger.info("MicInjector: virtual mic '%s' ready (uid=%d)",
                        _SOURCE_NAME, self._uid)
            return True
        except Exception as exc:
            logger.warning("MicInjector: startup failed: %s", exc)
            self._cleanup_resources()
            return False

    def stop(self):
        if not self._started:
            return
        self._started = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._cleanup_resources()
        logger.info("MicInjector: stopped")

    # ── Data ─────────────────────────────────────

    def write(self, pcm_data: bytes):
        """Enqueue a PCM chunk for injection.  Non-blocking — drops if full."""
        if not self._started:
            return
        try:
            self._queue.put_nowait(pcm_data)
        except queue.Full:
            pass

    # ── Internal ─────────────────────────────────

    def _pa_env(self) -> dict:
        return {
            **os.environ,
            "XDG_RUNTIME_DIR":   f"/run/user/{self._uid}",
            "PULSE_RUNTIME_PATH": f"/run/user/{self._uid}/pulse",
            "HOME": f"/home/{self._username()}",
        }

    def _drop_privs(self):
        """preexec_fn: drop root to session user."""
        os.setgid(self._gid)
        os.setuid(self._uid)

    def _pactl(self, *args, timeout: int = 5) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["pactl", "--server", self._pa_server, *args],
            capture_output=True, text=True,
            timeout=timeout,
            env=self._pa_env(),
            preexec_fn=self._drop_privs,
        )

    def _username(self) -> str:
        try:
            return pwd.getpwuid(self._uid).pw_name
        except Exception:
            return "user"

    def _find_module(self, module_type: str, name: str) -> Optional[str]:
        """Return module index if a module with the given name is loaded."""
        result = self._pactl("list", "short", "modules")
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if module_type in line and name in line:
                return line.split()[0]
        return None

    def _load_sink(self):
        """Load module-null-sink as the pacat target (idempotent)."""
        existing = self._find_module("module-null-sink", _SINK_NAME)
        if existing:
            logger.debug("MicInjector: reusing null-sink idx=%s", existing)
            self._sink_module_idx = existing
            return

        result = self._pactl(
            "load-module", "module-null-sink",
            f"sink_name={_SINK_NAME}",
            f"rate={_SAMPLE_RATE}",
            f"channels={_CHANNELS}",
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"load module-null-sink failed: {result.stderr.strip()}")
        self._sink_module_idx = result.stdout.strip()
        logger.debug("MicInjector: null-sink idx=%s", self._sink_module_idx)

    def _load_virtual_source(self):
        """Load module-virtual-source wrapping the null-sink monitor.

        This is what GNOME Sound Settings and desktop apps see as a
        proper microphone — unlike a raw monitor source which is hidden.
        """
        existing = self._find_module("module-virtual-source", _SOURCE_NAME)
        if existing:
            logger.debug("MicInjector: reusing virtual-source idx=%s", existing)
            self._source_module_idx = existing
            return

        result = self._pactl(
            "load-module", "module-virtual-source",
            f"source_name={_SOURCE_NAME}",
            f"master={_SINK_NAME}.monitor",
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"load module-virtual-source failed: {result.stderr.strip()}")
        self._source_module_idx = result.stdout.strip()
        logger.debug("MicInjector: virtual-source idx=%s",
                     self._source_module_idx)

    def _start_pacat(self):
        """Start pacat writing PCM from stdin into the null-sink."""
        self._pacat = subprocess.Popen(
            [
                "pacat", "--playback",
                f"--server={self._pa_server}",
                f"--device={_SINK_NAME}",
                f"--format={_FORMAT}",
                f"--rate={_SAMPLE_RATE}",
                f"--channels={_CHANNELS}",
                "--raw",
                "--latency-msec=50",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self._pa_env(),
            preexec_fn=self._drop_privs,
        )
        logger.debug("MicInjector: pacat pid=%d", self._pacat.pid)

    def _writer_loop(self):
        """Drain the queue and write PCM chunks to pacat stdin."""
        while self._started:
            try:
                chunk = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if chunk is None:
                break
            if not self._pacat or self._pacat.poll() is not None:
                logger.warning("MicInjector: pacat exited — stopping")
                break
            try:
                self._pacat.stdin.write(chunk)
                self._pacat.stdin.flush()
            except (BrokenPipeError, OSError):
                logger.warning("MicInjector: pacat pipe broken")
                break

    def _cleanup_resources(self):
        if self._pacat:
            try:
                if self._pacat.stdin:
                    self._pacat.stdin.close()
                self._pacat.terminate()
                self._pacat.wait(timeout=2.0)
            except Exception:
                pass
            self._pacat = None

        for idx_attr in ("_source_module_idx", "_sink_module_idx"):
            idx = getattr(self, idx_attr, None)
            if idx:
                try:
                    self._pactl("unload-module", idx)
                except Exception:
                    pass
                setattr(self, idx_attr, None)
