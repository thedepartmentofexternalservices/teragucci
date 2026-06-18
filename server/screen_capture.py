"""
Screen capture module for Linux with enhanced multi-monitor support.

Uses mss for fast screen capture. Supports:
- Individual monitor capture
- All-monitors (virtual desktop) capture
- Raw BGRA output for H.264/H.265/AV1 encoding pipeline
- JPEG fallback with dirty-rectangle detection
- Monitor enumeration and hot-switching
- Per-monitor resolution and DPI tracking
- Dynamic monitor hotplug detection
- NvFBC capture for NVIDIA GPUs (lowest latency)
"""

import io
import time
import logging
import os
import subprocess
from typing import Optional, List, Tuple

import mss
import numpy as np
from PIL import Image

from common.messages import MonitorInfo

logger = logging.getLogger(__name__)

# NvFBC backend — preferred capture path on NVIDIA hosts. Reads the
# framebuffer directly from the compositor's output via NVIDIA's
# libnvidia-fbc.so.1 (wrapped by a small C helper we spawn), which is
# tear-free and much lower latency than mss/XShmGetImage. The mss
# path stays as the fallback for non-NVIDIA machines and for the
# legacy JPEG / dirty-region paths.
try:
    from .nvfbc import NvFBCBackend, helper_available as _nvfbc_helper_available
    _HAS_NVFBC_BACKEND = True
except Exception as _e:
    _HAS_NVFBC_BACKEND = False
    _nvfbc_helper_available = lambda: False  # noqa: E731
    NvFBCBackend = None  # type: ignore

# XDamage-based capture gating — see ScreenCapture._init_damage_tracker for
# the full story. Imported lazily/optionally so a missing python-xlib or
# absent DAMAGE extension degrades to unsynchronized mss capture instead of
# crashing.
try:
    from Xlib import display as _xdisplay
    from Xlib.ext import damage as _xdamage
    _HAS_XLIB_DAMAGE = True
except Exception:
    _HAS_XLIB_DAMAGE = False

DEFAULT_JPEG_QUALITY = 60
CHANGE_THRESHOLD = 10
BLOCK_SIZE = 32


def detect_nvfbc() -> bool:
    """Check if NVIDIA NvFBC capture is available."""
    try:
        # NvFBC is available through NVIDIA's capture SDK
        # Check for the shared library
        for lib_path in ["/usr/lib/libnvidia-fbc.so.1",
                         "/usr/lib64/libnvidia-fbc.so.1",
                         "/usr/lib/x86_64-linux-gnu/libnvidia-fbc.so.1"]:
            if os.path.exists(lib_path):
                return True
    except Exception:
        pass
    return False


def detect_monitors_xrandr() -> List[dict]:
    """
    Detect monitors using xrandr for richer information.

    Returns list of dicts with name, width, height, x, y, primary, scale, refresh_rate.
    """
    monitors = []
    try:
        result = subprocess.run(
            ["xrandr", "--query"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return monitors

        current_name = ""
        for line in result.stdout.splitlines():
            if " connected" in line:
                parts = line.split()
                current_name = parts[0]
                is_primary = "primary" in line

                # Parse geometry: WxH+X+Y
                for part in parts:
                    if "x" in part and "+" in part:
                        try:
                            geo = part.split("+")
                            dims = geo[0].split("x")
                            w = int(dims[0])
                            h = int(dims[1])
                            x = int(geo[1])
                            y = int(geo[2])
                            monitors.append({
                                "name": current_name,
                                "width": w, "height": h,
                                "x": x, "y": y,
                                "primary": is_primary,
                                "refresh_rate": 0.0,
                                "scale": 1.0,
                            })
                        except (ValueError, IndexError):
                            pass
                        break

            elif current_name and "*" in line:
                # Parse refresh rate from mode line (e.g., "  1920x1080     60.00*+")
                parts = line.strip().split()
                for part in parts:
                    if "*" in part:
                        try:
                            rate = float(part.replace("*", "").replace("+", ""))
                            if monitors:
                                monitors[-1]["refresh_rate"] = rate
                        except ValueError:
                            pass
                        break
                current_name = ""  # Only capture first mode (active)

    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return monitors


class ScreenCapture:
    """Captures the Linux screen with enhanced multi-monitor support."""

    def __init__(self, monitor_index: int = 1,
                 jpeg_quality: int = DEFAULT_JPEG_QUALITY,
                 want_10bit: bool = False):
        """
        Args:
            monitor_index: 0 = all monitors (virtual desktop),
                          1+ = specific monitor
            jpeg_quality: JPEG quality for fallback mode
            want_10bit: Phase 2 VIDEO-09 — request a YUV420P10LE NvFBC
                surface for the Main10 path. mss has no 10-bit capture
                so when we fall back to mss the runtime capability state
                is forced to ``"degraded"`` (the client health overlay
                renders the explicit badge — no silent downgrade per
                D-03).
        """
        self.monitor_index = monitor_index
        self.jpeg_quality = jpeg_quality
        # Phase 2 D-03 / VIDEO-09 state surface — read by the bootstrap
        # ``build_color_caps()`` helper to override negotiated_state
        # when the live capture path can't honor the advertised cap.
        self._want_10bit: bool = bool(want_10bit)
        self._using_mss_fallback: bool = False
        self._sct = mss.mss()
        self._last_frame: Optional[np.ndarray] = None
        self._xrandr_info: List[dict] = []
        self._nvfbc_available = detect_nvfbc()
        self._refresh_monitor_info()
        self._select_monitor(monitor_index)

        # NvFBC backend — preferred raw BGRA capture path on NVIDIA.
        # We try to spin it up opportunistically and fall back to mss
        # if anything fails (no helper binary, GeForce without patched
        # driver, Xvfb without an NVIDIA output, etc.).
        self._nvfbc: Optional[NvFBCBackend] = None
        if (_HAS_NVFBC_BACKEND
                and self._nvfbc_available
                and _nvfbc_helper_available()):
            try:
                self._nvfbc = NvFBCBackend(
                    display=os.environ.get("DISPLAY"),
                    fps=60,
                    # Cursor OFF: we ship the real cursor shape to the
                    # client out-of-band via XFixes (CursorTracker) and
                    # the client draws it locally as a QCursor. That
                    # gives zero-latency cursor motion instead of the
                    # full encode→network→decode round-trip we'd get
                    # if we baked the cursor into the video stream.
                    with_cursor=False,
                    push_model=True,
                    # direct_capture off: known to be unstable under
                    # heavy compositor activity (Flame playback) and
                    # contributed to mid-stream frame layout drift.
                    direct_capture=False,
                    # Phase 2 VIDEO-09: request 10-bit YUV420P10LE
                    # surface when caller asked. Helper SDK guard may
                    # silently fall through to BGRA on older drivers;
                    # runtime_capability_state surfaces that.
                    want_10bit=self._want_10bit,
                )
                # NvFBC captures the whole screen — override width/height
                # so downstream encoders see the real framebuffer size
                # instead of mss's first-monitor view.
                self.width = self._nvfbc.width
                self.height = self._nvfbc.height
            except Exception as e:
                logger.warning("NvFBC backend unavailable (%s) — "
                               "falling back to mss/XShmGetImage", e)
                self._nvfbc = None
                # Phase 2 VIDEO-09 + D-03: mss has no 10-bit path. If
                # the caller wanted Main10 capture and we fell back to
                # mss, the negotiated capability state must drop to
                # "degraded" so the client badge tells the artist
                # honestly.
                self._using_mss_fallback = True
        elif self._want_10bit:
            # No NvFBC even attempted (no driver / no helper) — also a
            # degraded path for the 10-bit advertisement.
            self._using_mss_fallback = True

        # Tearing mitigation for the mss fallback path: gate captures
        # on XDamage events so we only read the framebuffer after the
        # compositor has published a new frame. Not needed when NvFBC
        # is active because NvFBC already reads tear-free frames.
        self._xdisplay = None
        self._damage_obj = None
        self._damage_has_events = False
        if self._nvfbc is None:
            self._init_damage_tracker()

        if self._nvfbc is not None:
            logger.info("Screen capture initialized: %dx%d via NvFBC",
                        self.width, self.height)
        else:
            logger.info("Screen capture initialized: %dx%d via mss "
                        "(monitor %d)",
                        self.width, self.height, monitor_index)

    def _init_damage_tracker(self):
        """Open a python-xlib connection and create a root-window damage object.

        The damage object tells Xorg to send us an XDamageNotify event
        whenever the compositor (Mutter) dirties the root window. We use
        this as a sync point: drain pending events + XSync before each
        capture, so mss.grab() reads a stable framebuffer snapshot
        instead of racing with Mutter's mid-composite writes (which
        produces horizontal tear bands during Flame timeline playback).

        Silently degrades to raw mss capture if python-xlib is missing
        or the X server doesn't advertise DAMAGE. Not all X connections
        work from every thread context; failures here are logged at
        WARNING but not fatal.
        """
        if not _HAS_XLIB_DAMAGE:
            logger.info("python-xlib / DAMAGE ext unavailable; "
                        "capture will not be XDamage-gated")
            return

        display_name = os.environ.get("DISPLAY", ":0")
        try:
            self._xdisplay = _xdisplay.Display(display_name)
            if not self._xdisplay.has_extension("DAMAGE"):
                logger.warning("Xorg %s lacks DAMAGE extension; tearing "
                               "mitigation disabled", display_name)
                self._xdisplay.close()
                self._xdisplay = None
                return

            # DAMAGE requires an explicit version handshake before use.
            self._xdisplay.damage_query_version()

            root = self._xdisplay.screen().root
            # DamageReportBoundingBox = one event per damage region
            # aggregated into a bounding box. Lowest event rate, which
            # is what we want — we don't care where the damage is, only
            # that *some* damage happened since last capture.
            self._damage_obj = root.damage_create(
                _xdamage.DamageReportBoundingBox)
            self._xdisplay.sync()
            logger.info("XDamage capture gate enabled on %s (damage obj id=%d)",
                        display_name, self._damage_obj.id)
        except Exception as e:
            logger.warning("XDamage init failed on %s: %s — "
                           "falling back to unsynced capture",
                           display_name, e)
            if self._xdisplay is not None:
                try:
                    self._xdisplay.close()
                except Exception:
                    pass
            self._xdisplay = None
            self._damage_obj = None

    def _sync_before_capture(self):
        """Drain pending XDamage events and flush the X pipeline.

        Called on the hot path right before every mss.grab(). Does two
        things:

        1. Pulls all pending XDamage events off the wire and issues
           ``DamageSubtract`` to mark them consumed. This prevents the
           event queue from growing unbounded and keeps the signal
           "there is new damage to report" usable.

        2. Calls ``Display.sync()`` (= XSync(False)) which is a
           round-trip to the server. That forces the server to finish
           processing any in-flight requests — crucially including
           anything the compositor queued via the same X connection.

        Note: XSync does NOT synchronize direct-rendered (DRI) OpenGL
        commands, and Mutter on NVIDIA uses direct rendering. What it
        DOES do is guarantee we are not stepping on the X protocol
        queue while the server is draining compositor-side requests
        (damage events, region updates, present pixmap completions,
        etc.). In practice this eliminates most of the mid-composite
        grabs we were seeing — the remaining tear budget is the time
        between the last GL command flush and our XShmGetImage read,
        which is small enough that Mutter's vsync-aligned redraws
        rarely land inside it.

        Cheap: a single round-trip plus any pending damage subtracts.
        Sub-millisecond on a local X connection.
        """
        if not self._xdisplay or not self._damage_obj:
            return
        try:
            # Drain all pending events and mark damage consumed.
            while self._xdisplay.pending_events() > 0:
                evt = self._xdisplay.next_event()
                if isinstance(evt, _xdamage.DamageNotify):
                    self._damage_has_events = True
            # DamageSubtract with None,None resets the accumulated
            # damage region on the server side. This also flushes.
            self._damage_obj.subtract(None, None)
            self._xdisplay.sync()
        except Exception as e:
            # Don't let a transient X error kill the capture loop —
            # just disable the gate for the rest of this session.
            logger.warning("XDamage sync failed, disabling gate: %s", e)
            try:
                self._xdisplay.close()
            except Exception:
                pass
            self._xdisplay = None
            self._damage_obj = None

    def _refresh_monitor_info(self):
        """Refresh monitor information from xrandr."""
        self._xrandr_info = detect_monitors_xrandr()
        if self._xrandr_info:
            logger.info("Detected %d monitors via xrandr", len(self._xrandr_info))

    def _select_monitor(self, index: int):
        """Select which monitor to capture."""
        monitors = self._sct.monitors
        if index >= len(monitors):
            logger.warning(
                "screen_capture.monitor_index_out_of_range requested=%d "
                "available=%d → primary",
                index, len(monitors),
            )
            index = 1  # Fall back to primary
        self.monitor_index = index
        self._monitor = monitors[index]
        self.width = self._monitor["width"]
        self.height = self._monitor["height"]
        self._last_frame = None


    def reinit(self, width: int = 0, height: int = 0):
        """Reinitialize capture after a resolution change."""
        # Re-create mss instance to pick up new screen geometry
        self._sct.close()
        self._sct = mss.mss()
        self._refresh_monitor_info()
        self._select_monitor(self.monitor_index)
        self._last_frame = None
        logger.info("Screen capture reinit: %dx%d", self.width, self.height)

    def switch_monitor(self, index: int):
        """Switch to a different monitor (or 0 for all)."""
        # Refresh monitor list in case displays changed
        self._refresh_monitor_info()
        self._sct = mss.mss()  # Re-create to pick up new monitors
        self._select_monitor(index)
        logger.info("Switched to monitor %d: %dx%d", index, self.width, self.height)

    def list_monitors(self) -> List[MonitorInfo]:
        """Enumerate all available monitors with detailed info."""
        monitors = []
        xrandr_by_idx = {}

        # Map xrandr info to mss monitor indices (best-effort by position)
        for xi, xinfo in enumerate(self._xrandr_info):
            xrandr_by_idx[xi] = xinfo

        for i, mon in enumerate(self._sct.monitors):
            if i == 0:
                name = "All Monitors (Virtual Desktop)"
                primary = False
                refresh = 0.0
                scale = 1.0
            else:
                # Try to match with xrandr info
                xinfo = xrandr_by_idx.get(i - 1, {})
                name = xinfo.get("name", f"Monitor {i}")
                primary = xinfo.get("primary", (i == 1))
                refresh = xinfo.get("refresh_rate", 0.0)
                scale = xinfo.get("scale", 1.0)

            monitors.append(MonitorInfo(
                id=i,
                name=name,
                width=mon["width"],
                height=mon["height"],
                x=mon.get("left", 0),
                y=mon.get("top", 0),
                primary=primary,
                scale=scale,
            ))

        return monitors

    def detect_hotplug(self) -> bool:
        """
        Check if monitor configuration has changed.

        Phase 3 D-09 — full ``(id, width, height, x, y)`` tuple
        signature per monitor catches reorder + same-size swap +
        repositioning that the prior count+WxH-only check missed
        (Pitfall 4 fix; mirrors D-11 Mac upgrade in
        ``mac_screen_capture.py::detect_hotplug``).

        Read-only enumeration per D-10 — zero xrandr SET operations.
        The NVIDIA driver SIGSEGV on HDMI unplug happens specifically
        during xrandr set-operations (new/add/change mode); enumerate-
        only stays safe.

        Returns True if monitors changed (caller should re-enumerate).
        """
        try:
            old_sig = [
                (m.id, m.width, m.height, m.x, m.y)
                for m in self.list_monitors()
            ]
        except Exception:
            old_sig = []
        try:
            new_sct = mss.mss()
            # Swap + refresh so list_monitors() reflects the new topology.
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = new_sct
            self._refresh_monitor_info()
            # WR-01: Re-select monitor so self._monitor references the NEW
            # _sct.monitors dict — otherwise capture_raw_bgra() calls
            # self._sct.grab(self._monitor) with coordinates from the old
            # topology, which can crash on physical monitor removal.
            # _select_monitor clamps out-of-range indices back to primary.
            self._select_monitor(self.monitor_index)
            new_sig = [
                (m.id, m.width, m.height, m.x, m.y)
                for m in self.list_monitors()
            ]
            if old_sig != new_sig:
                logger.info(
                    "Monitor hotplug detected: %s -> %s",
                    old_sig, new_sig,
                )
                return True
        except Exception:
            pass
        return False

    @property
    def screen_size(self) -> tuple:
        return (self.width, self.height)

    @property
    def monitor_count(self) -> int:
        return len(self._sct.monitors) - 1  # Subtract virtual desktop

    @property
    def runtime_capability_state(self) -> str:
        """Phase 2 VIDEO-09 / D-03: live ServerColorCaps.negotiated_state
        contribution from the capture path.

        Truth table:
          want_10bit + NvFBC alive          -> "confirmed"
          want_10bit + mss fallback / no GPU -> "degraded"
          NOT want_10bit                    -> "not_supported"

        Consumed by ``server.bootstrap.build_color_caps`` after the
        live capture is initialized — that helper combines this with
        the encoder probe to decide the final badge value.
        """
        if not self._want_10bit:
            return "not_supported"
        if self._using_mss_fallback or self._nvfbc is None:
            return "degraded"
        return "confirmed"

    # --- Raw BGRA capture (for H.264/H.265/AV1 encoder pipeline) ---

    def capture_raw_bgra(self) -> bytes:
        """
        Capture the screen and return raw BGRA pixel data.

        This is the format expected by FFmpeg rawvideo input.
        Returns width * height * 4 bytes.
        """
        if self._nvfbc is not None:
            try:
                return self._nvfbc.capture_raw_bgra()
            except Exception as e:
                logger.warning("NvFBC capture failed mid-stream (%s) — "
                               "falling back to mss for the rest of "
                               "this session", e)
                try:
                    self._nvfbc.close()
                except Exception:
                    pass
                self._nvfbc = None
                # Phase 2 VIDEO-09: a mid-stream NvFBC failure on the
                # 10-bit path drops us to mss → flip to "degraded".
                if self._want_10bit:
                    self._using_mss_fallback = True
                self._init_damage_tracker()
        self._sync_before_capture()
        sct_img = self._sct.grab(self._monitor)
        return bytes(sct_img.raw)

    # Phase 3 D-02 — server-side GPU crop pre-encode.
    #
    # mirror_all: crop=None → identical bytes to capture_raw_bgra (Phase 2
    # 9-checkpoint 10-bit fixture preserved; no new downgrade points).
    # single / pick_one: crop=(x,y,w,h) feeds a cropped BGRA frame into
    # the encoder; the encoder's existing BGRA→P010 conversion preserves
    # 10-bit fidelity through the unchanged Phase 2 pipeline.
    #
    # P010 raw-crop seam DEFERRED to Phase 3.5 (v1.1) — the v1 codebase
    # has no ``capture_raw_p010`` method on any capture backend
    # (ScreenCapture / MacScreenCapture / NvFBCBackend expose BGRA only).
    # See docs/release.md "Phase 3.5 follow-ups" for the backlog item.
    def capture_raw_bgra_with_crop(
        self, crop: Optional[Tuple[int, int, int, int]] = None,
    ) -> bytes:
        """D-02 — full-virtual-desktop capture + optional BGRA crop.

        Args:
            crop: ``(x, y, w, h)`` in server physical pixels, or None to
                pass through (mirror_all path).

        Returns:
            Cropped BGRA bytes. Degenerate crops (w=0 or h=0 after
            clamping) return a single black pixel ``b"\\x00\\x00\\x00\\xff"``
            rather than crashing the encoder feed.

        The crop is clamped to the captured frame's bounds (defense
        against stale crop_rect after a hot-plug), so pick_one + single
        modes never array-index out-of-bounds on a shrunken virtual
        desktop.
        """
        raw = self.capture_raw_bgra()
        if crop is None:
            return raw
        x, y, w, h = crop
        # Clamp origin to [0, width/height]; clamp size to remaining area.
        x = max(0, min(int(x), self.width))
        y = max(0, min(int(y), self.height))
        w = max(0, min(int(w), self.width - x))
        h = max(0, min(int(h), self.height - y))
        if w == 0 or h == 0:
            # Single black pixel — defensive fallback per PATTERNS L720-722.
            # Empty bytes would crash the encoder feed.
            return b"\x00\x00\x00\xff"
        # raw may be width*height*4 bytes (mss) OR a padded buffer if the
        # backend stride doesn't match width*4. The Phase 1 capture path
        # always produces a tight buffer; still defensive-decode here.
        expected = self.width * self.height * 4
        if len(raw) != expected:
            # Fall back to byte-slice per-row to preserve alignment.
            row_stride = len(raw) // self.height if self.height > 0 else 0
            if row_stride < self.width * 4:
                # Malformed — return black pixel rather than garbage bytes.
                logger.warning(
                    "capture_raw_bgra_with_crop: raw size %d != expected %d "
                    "and row_stride %d < width*4 %d — returning black",
                    len(raw), expected, row_stride, self.width * 4,
                )
                return b"\x00\x00\x00\xff"
            out = bytearray(w * h * 4)
            for row in range(h):
                src_off = (y + row) * row_stride + x * 4
                out[row * w * 4:(row + 1) * w * 4] = raw[src_off:src_off + w * 4]
            return bytes(out)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(
            self.height, self.width, 4,
        )
        return arr[y:y + h, x:x + w].tobytes()

    def capture_raw_frame(self) -> np.ndarray:
        """Capture and return as numpy BGRA array."""
        if self._nvfbc is not None:
            try:
                return self._nvfbc.capture_raw_frame()
            except Exception as e:
                logger.warning("NvFBC capture failed mid-stream (%s) — "
                               "falling back to mss", e)
                try:
                    self._nvfbc.close()
                except Exception:
                    pass
                self._nvfbc = None
                if self._want_10bit:
                    self._using_mss_fallback = True
                self._init_damage_tracker()
        self._sync_before_capture()
        sct_img = self._sct.grab(self._monitor)
        return np.array(sct_img)

    # --- JPEG capture (fallback mode) ---

    def capture_full_frame(self) -> bytes:
        """Capture the entire screen and return JPEG bytes."""
        sct_img = self._sct.grab(self._monitor)
        frame = np.array(sct_img)[:, :, :3]
        frame = frame[:, :, ::-1]  # BGR -> RGB
        self._last_frame = frame.copy()
        return self._encode_jpeg(frame)

    def capture_dirty_regions(self) -> list:
        """
        Capture screen and detect dirty rectangles.

        Returns list of (x, y, w, h, jpeg_bytes) tuples for changed regions.
        """
        sct_img = self._sct.grab(self._monitor)
        frame = np.array(sct_img)[:, :, :3]
        frame = frame[:, :, ::-1]

        if self._last_frame is None:
            self._last_frame = frame.copy()
            jpeg_data = self._encode_jpeg(frame)
            return [(0, 0, self.width, self.height, jpeg_data)]

        diff = np.abs(frame.astype(np.int16) - self._last_frame.astype(np.int16))
        changed = np.max(diff, axis=2) > CHANGE_THRESHOLD

        dirty_regions = self._find_dirty_blocks(changed)
        if not dirty_regions:
            return []

        merged = self._merge_regions(dirty_regions)
        results = []
        for x, y, w, h in merged:
            x2 = min(x + w, self.width)
            y2 = min(y + h, self.height)
            region = frame[y:y2, x:x2]
            jpeg_data = self._encode_jpeg(region)
            results.append((x, y, x2 - x, y2 - y, jpeg_data))

        self._last_frame = frame.copy()
        return results

    def _find_dirty_blocks(self, changed: np.ndarray) -> list:
        blocks = []
        h, w = changed.shape
        for by in range(0, h, BLOCK_SIZE):
            for bx in range(0, w, BLOCK_SIZE):
                block = changed[by:by + BLOCK_SIZE, bx:bx + BLOCK_SIZE]
                if np.any(block):
                    blocks.append((bx, by, BLOCK_SIZE, BLOCK_SIZE))
        return blocks

    def _merge_regions(self, blocks: list) -> list:
        if not blocks:
            return []
        rows = {}
        for x, y, w, h in blocks:
            if y not in rows:
                rows[y] = []
            rows[y].append((x, w))

        merged = []
        for y, spans in rows.items():
            spans.sort()
            current_x, current_w = spans[0]
            for x, w in spans[1:]:
                if x <= current_x + current_w:
                    current_w = max(current_w, x + w - current_x)
                else:
                    merged.append((current_x, y, current_w, BLOCK_SIZE))
                    current_x, current_w = x, w
            merged.append((current_x, y, current_w, BLOCK_SIZE))

        if not merged:
            return merged

        merged.sort(key=lambda r: (r[0], r[1]))
        final = [merged[0]]
        for x, y, w, h in merged[1:]:
            px, py, pw, ph = final[-1]
            if x == px and w == pw and y == py + ph:
                final[-1] = (px, py, pw, ph + h)
            else:
                final.append((x, y, w, h))

        return final

    def _encode_jpeg(self, frame: np.ndarray) -> bytes:
        img = Image.fromarray(frame)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.jpeg_quality, optimize=False)
        return buf.getvalue()

    def invalidate(self):
        """Force next capture to be a full frame."""
        self._last_frame = None

    def close(self):
        if self._nvfbc is not None:
            try:
                self._nvfbc.close()
            except Exception:
                pass
            self._nvfbc = None
        self._sct.close()
        if self._damage_obj is not None:
            try:
                self._damage_obj.destroy()
            except Exception:
                pass
            self._damage_obj = None
        if self._xdisplay is not None:
            try:
                self._xdisplay.close()
            except Exception:
                pass
            self._xdisplay = None
