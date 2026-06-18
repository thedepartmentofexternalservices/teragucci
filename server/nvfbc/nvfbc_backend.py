"""
NvFBC capture backend for teraguchi.

Spawns the compiled ``nvfbc_capture`` helper (see ``nvfbc_capture.c``)
as a child process and streams raw BGRA frames from its stdout into a
latest-frame buffer that the rest of screen_capture.py can read on
demand.

Why a subprocess instead of ctypes
----------------------------------
NvFBC's Linux library internally creates a GLX context and owns it for
the lifetime of the capture session. If we linked NvFBC directly into
the teraguchi Python process we'd drag GLX/OpenGL state into a process
that already has its own mixed threading + PAM + DBus + ffmpeg
subprocess model, and any crash in the NVIDIA driver would take the
whole server with it. Running NvFBC in a dedicated child gives us:

* A clean crash domain — helper dies, Python restarts it
* No GLX state pollution in the Python process
* The helper can run as root for direct-capture / push-model access
  while the Python server is free to drop privileges later
* Trivial wire format (16-byte header + raw BGRA) keeps the Python
  side ~100 lines of code

Protocol (see nvfbc_capture.c for the canonical definition)::

    [4] magic = 'TGFR'
    [4] width  (uint32, little-endian)
    [4] height (uint32, little-endian)
    [4] size   (uint32, little-endian)  -- bytes of payload that follow
    [size] BGRA pixel data

The reader thread drains the pipe continuously; ``capture_raw_bgra()``
returns whatever the most recent complete frame was under a lock.
Old frames are silently dropped, which is what we want — teraguchi
always wants the freshest frame, never a backlog.
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Header layout — must match nvfbc_capture.c exactly.
_HEADER_FMT = "<4sIII"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_MAGIC = b"TGFR"

# The helper binary lives next to this module when installed by
# install-server.sh. In dev checkouts it's in server/nvfbc/ too, built
# via ``make``.
_HELPER_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "nvfbc_capture")


def helper_available() -> bool:
    """True if the compiled helper binary exists and is executable."""
    return os.path.isfile(_HELPER_BIN) and os.access(_HELPER_BIN, os.X_OK)


class NvFBCBackend:
    """Spawn nvfbc_capture and expose latest-frame access.

    Not thread-safe against concurrent close(); the rest of the server
    creates and destroys these synchronously from the main loop.
    """

    def __init__(self,
                 display: Optional[str] = None,
                 fps: int = 60,
                 with_cursor: bool = False,
                 push_model: bool = True,
                 direct_capture: bool = False,
                 want_10bit: bool = False,
                 startup_timeout: float = 5.0):
        """Spawn the nvfbc_capture helper.

        Phase 2 VIDEO-09: ``want_10bit=True`` requests a YUV420P10LE
        capture surface. The helper compiles the 10-bit path under
        ``#ifdef NVFBC_BUFFER_FORMAT_YUV420P10LE`` so older NvFBC SDKs
        silently fall through to BGRA + emit a warning to stderr; the
        Python side logs the warning but otherwise treats the helper as
        successful (the parent ``ScreenCapture`` carries the degraded
        capability state separately).
        """
        if not helper_available():
            raise RuntimeError(
                f"nvfbc_capture helper not found at {_HELPER_BIN} — "
                "run `make` in server/nvfbc/ or reinstall the server")

        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Latest-frame storage. _latest is a bytes object that is
        # ATOMICALLY REPLACED by the reader thread on each new frame;
        # readers hold a reference + dimensions under _lock.
        self._lock = threading.Lock()
        self._latest: Optional[bytes] = None
        self._width: int = 0
        self._height: int = 0
        self._frame_count: int = 0

        args = [
            _HELPER_BIN,
            "--fps", str(fps),
            "--with-cursor", "1" if with_cursor else "0",
            "--push", "1" if push_model else "0",
            "--direct-capture", "1" if direct_capture else "0",
            "--want-10bit", "1" if want_10bit else "0",
        ]
        if display:
            args.extend(["--display", display])
        # Stash for the parent ScreenCapture to surface the actual
        # surface format we asked for (the runtime SDK guard may have
        # overridden it back to BGRA, which is reported via stderr).
        self._want_10bit = bool(want_10bit)

        env = os.environ.copy()
        if display:
            env["DISPLAY"] = display

        logger.info("NvFBC backend: spawning %s", " ".join(args))
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )

        # Kick off the stderr pump so helper diagnostic lines end up
        # in our logger, not buried in a pipe buffer.
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr, name="nvfbc-stderr", daemon=True)
        self._stderr_thread.start()

        # Kick off the frame reader.
        self._reader = threading.Thread(
            target=self._reader_loop, name="nvfbc-reader", daemon=True)
        self._reader.start()

        # Block until we have a first frame or timeout. We need width
        # and height before the rest of the capture stack will know
        # what to do with us.
        if not self._wait_for_first_frame(startup_timeout):
            self.close()
            raise RuntimeError(
                "NvFBC helper did not produce a frame within "
                f"{startup_timeout:.1f}s — check stderr log for the reason")

        logger.info("NvFBC backend ready: %dx%d", self._width, self._height)

    # ── Properties the rest of screen_capture.py uses ─────────────

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── Frame access ──────────────────────────────────────────────

    def capture_raw_bgra(self) -> bytes:
        """Return the most recent BGRA frame as bytes.

        Never blocks. If no new frame has arrived since the last call,
        returns the previous frame (which is what the teraguchi
        encoder wants — it needs *something* every tick to keep the
        codec alive).

        Raises RuntimeError if the helper has died.
        """
        with self._lock:
            frame = self._latest
        if frame is None:
            if not self.alive:
                raise RuntimeError("nvfbc_capture helper exited unexpectedly")
            # Reader thread hasn't received a frame yet — spin briefly.
            # This should essentially never hit after __init__ returns.
            return b""
        return frame

    def capture_raw_frame(self):
        """Return the most recent frame as a numpy BGRA array."""
        import numpy as np  # local import keeps module import cheap
        data = self.capture_raw_bgra()
        if not data:
            return np.zeros((self._height, self._width, 4), dtype=np.uint8)
        arr = np.frombuffer(data, dtype=np.uint8)
        return arr.reshape((self._height, self._width, 4))

    # ── Reader thread ─────────────────────────────────────────────

    def _wait_for_first_frame(self, timeout: float) -> bool:
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.alive:
                return False
            with self._lock:
                if self._latest is not None:
                    return True
            time.sleep(0.02)
        return False

    def _read_exact(self, fd, n: int) -> Optional[bytes]:
        """Read exactly n bytes or return None on EOF/error."""
        buf = bytearray()
        while len(buf) < n:
            if self._stop.is_set():
                return None
            try:
                chunk = fd.read(n - len(buf))
            except Exception as e:
                logger.warning("NvFBC read error: %s", e)
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _reader_loop(self):
        assert self._proc is not None and self._proc.stdout is not None
        fd = self._proc.stdout
        while not self._stop.is_set():
            header = self._read_exact(fd, _HEADER_SIZE)
            if header is None:
                break
            magic, w, h, sz = struct.unpack(_HEADER_FMT, header)
            if magic != _MAGIC:
                logger.error("NvFBC framing lost: bad magic %r — bailing",
                             magic)
                break
            # Phase 2 WR-07: derive the size cap from the frame geometry
            # rather than a hardcoded 256 MB constant. The previous cap
            # was fine for 8K BGRA but YUV420P10LE @ 8K is ~200 MB
            # (sneaks under) and 16K oversampled-capture would silently
            # break the reader by hitting the hardcoded limit.
            #
            # Cap = w * h * 8 bytes-per-pixel * 2 safety_margin. Picks
            # the worst case (4:4:4 16-bit per channel = 8 BPP) so any
            # legitimate format passes; 2x safety so a single
            # bit-flipped header that doesn't fully blow the format
            # check still gets caught before we ask the OS for tens of
            # gigs.
            #
            # Absolute floor of 4 MB so we still reject obviously-bogus
            # tiny-frame + huge-size combos when w/h are unset (first
            # frame, ``self._width == 0``).
            if w > 0 and h > 0:
                derived_cap = max(4 * 1024 * 1024, w * h * 8 * 2)
            else:
                # No geometry yet — fall back to the legacy absolute
                # cap so the first frame still has SOMETHING to gate on.
                derived_cap = 256 * 1024 * 1024
            if sz == 0 or sz > derived_cap:
                logger.error(
                    "NvFBC rejected absurd frame size %d "
                    "(derived_cap=%d, w=%d, h=%d) — bailing reader",
                    sz, derived_cap, w, h,
                )
                break
            payload = self._read_exact(fd, sz)
            if payload is None:
                break
            with self._lock:
                # Safety net: the helper pins NvFBC's frameSize to the
                # initial screen dimensions, so width/height should
                # never change once the first frame arrives. If they
                # ever do, the downstream ffmpeg rawvideo encoder
                # (locked to fixed -video_size at construction) will
                # misinterpret the bytes as sheared rows, producing
                # the "jigsaw puzzle" corruption we chased for a day.
                # Drop the frame loudly instead of passing it on.
                if self._width and (w != self._width or h != self._height):
                    logger.error(
                        "NvFBC dimension drift detected: %dx%d -> %dx%d "
                        "(dropping frame; encoder would jigsaw)",
                        self._width, self._height, w, h)
                    continue
                self._latest = payload
                self._width = w
                self._height = h
                self._frame_count += 1
        logger.info("NvFBC reader loop exited (frames=%d)",
                    self._frame_count)

    def _pump_stderr(self):
        assert self._proc is not None and self._proc.stderr is not None
        for raw in iter(self._proc.stderr.readline, b""):
            if self._stop.is_set():
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            # Helper tags all its lines with "[nvfbc] " already.
            logger.info("%s", line)

    # ── Lifecycle ─────────────────────────────────────────────────

    def close(self):
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=1.0)
                except Exception:
                    pass
            except Exception:
                pass
            self._proc = None
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=1.0)
            self._reader = None
