"""
BaseAdapter -- abstract contract for all remote desktop adapters.

Every adapter exposes the same 13 operations. Adapters that cannot support
an operation (e.g. VNC has no ring buffer) raise NotImplementedError or
return empty results. The master MCP server checks `capabilities` before
calling optional methods.
"""

from __future__ import annotations

import abc
from enum import Enum, auto
from typing import ClassVar


class Capability(Enum):
    SCREENSHOT      = auto()   # single-frame capture
    STREAMING       = auto()   # live H.264/JPEG ring buffer
    CHANGE_DETECT   = auto()   # wait_for_change via pixel diff
    MOUSE           = auto()   # click, move, scroll, drag
    KEYBOARD        = auto()   # type_text, press_combo
    CLIPBOARD       = auto()   # clipboard_get / clipboard_set
    REMOTE_EXEC     = auto()   # run_remote via SSH
    ACCESSIBILITY   = auto()   # AT-SPI / UI element finder


class BaseAdapter(abc.ABC):
    """
    All adapters implement this interface.

    Coordinates for pointer methods are normalized: 0.0 = left/top, 1.0 = right/bottom.
    screenshot() and watch_frames() return base64-encoded PNG strings.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]]

    # ---- lifecycle -------------------------------------------------------

    @abc.abstractmethod
    def connect(self, host: str, port: int, username: str, password: str, **kwargs) -> None:
        """Establish connection to the remote host."""

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Close the connection and release resources."""

    @property
    @abc.abstractmethod
    def connected(self) -> bool:
        """True if the adapter has an active connection."""

    # ---- vision ----------------------------------------------------------

    @property
    @abc.abstractmethod
    def screen_size(self) -> tuple[int, int]:
        """Return (width, height) in pixels of the remote screen."""

    @abc.abstractmethod
    def screenshot(self) -> str | None:
        """
        Return the current remote screen as a base64-encoded PNG string,
        or None if no frame is available yet.
        """

    def watch_frames(
        self,
        duration_ms: int = 2000,
        sample_every_ms: int = 500,
    ) -> list[tuple[int, str]]:
        """
        Sample the screen over *duration_ms* at *sample_every_ms* intervals.
        Returns [(unix_ms, base64_png), ...].
        Default: polling-based (works for any adapter with SCREENSHOT).
        Adapters with STREAMING override this for zero-copy ring-buffer access.
        """
        import time, base64, io
        from PIL import Image

        results: list[tuple[int, str]] = []
        end = time.monotonic() + duration_ms / 1000
        while time.monotonic() < end:
            raw = self.screenshot()
            if raw:
                results.append((int(time.time() * 1000), raw))
            sleep = min(sample_every_ms / 1000, end - time.monotonic())
            if sleep > 0:
                time.sleep(sleep)
        return results

    def wait_for_change(
        self,
        timeout_ms: int = 10_000,
        sensitivity: float = 0.02,
    ) -> tuple[int, str, float] | None:
        """
        Poll until the screen changes by at least *sensitivity* (0-1 fraction of pixels).
        Returns (unix_ms, base64_png, diff_fraction) or None on timeout.
        Default: polling-based (works for any adapter with SCREENSHOT).
        Adapters with CHANGE_DETECT override this with event-driven detection.
        """
        import time, base64, io
        import numpy as np
        from PIL import Image

        def _to_array(b64: str) -> np.ndarray:
            data = base64.b64decode(b64)
            img = Image.open(io.BytesIO(data)).convert("RGB")
            return np.array(img, dtype=np.float32)

        baseline_b64 = self.screenshot()
        if baseline_b64 is None:
            return None
        baseline = _to_array(baseline_b64)

        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            time.sleep(0.2)
            cur_b64 = self.screenshot()
            if cur_b64 is None:
                continue
            cur = _to_array(cur_b64)
            diff = float(np.mean(np.abs(cur - baseline) > 10))
            if diff >= sensitivity:
                return (int(time.time() * 1000), cur_b64, diff)
        return None

    def latest_frame_history(self, count: int = 5) -> list[tuple[int, str]]:
        """
        Return the last *count* frames from a ring buffer, newest last.
        Adapters without STREAMING return a single current frame.
        """
        raw = self.screenshot()
        if raw is None:
            return []
        return [(int(__import__("time").time() * 1000), raw)]

    # ---- mouse -----------------------------------------------------------

    @abc.abstractmethod
    def click(self, x: float, y: float, button: int = 1, double: bool = False) -> None:
        """Click at normalized position. button: 1=left 2=middle 3=right."""

    @abc.abstractmethod
    def move_mouse(self, x: float, y: float) -> None:
        """Move mouse cursor to normalized position without clicking."""

    @abc.abstractmethod
    def scroll(self, x: float, y: float, dx: int = 0, dy: int = 0) -> None:
        """Scroll at normalized position. dy>0 = down, dx>0 = right."""

    def drag(
        self,
        x1: float, y1: float,
        x2: float, y2: float,
        steps: int = 20,
        duration_ms: int = 300,
    ) -> None:
        """
        Click-drag from (x1,y1) to (x2,y2), both normalized.
        Default: move in steps with delays. Override for smoother control.
        """
        import time

        self.click(x1, y1, button=1)
        step_delay = duration_ms / 1000 / max(steps, 1)
        for i in range(1, steps + 1):
            t = i / steps
            self.move_mouse(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
            time.sleep(step_delay)
        self.click(x2, y2, button=1)

    # ---- keyboard --------------------------------------------------------

    @abc.abstractmethod
    def type_text(self, text: str) -> None:
        """Type a string of printable text."""

    @abc.abstractmethod
    def press_combo(self, combo: str) -> None:
        """Press a key combination like 'ctrl+c', 'alt+F4', 'enter'."""

    # ---- info ------------------------------------------------------------

    def adapter_info(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "connected": self.connected,
            "capabilities": [c.name for c in self.capabilities],
        }
