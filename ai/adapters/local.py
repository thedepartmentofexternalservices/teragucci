"""
LocalAdapter -- candidate adapter for the agent's own machine.

Controls the LOCAL display of whichever machine the MCP server is running on.
Useful when the AI agent needs to automate something on the same workstation
as Cursor / Claude Desktop.

Uses:
  - mss for fast screenshot (cross-platform, no deps beyond cffi)
  - pynput for mouse and keyboard (cross-platform)

Install:  uv pip install mss pynput

Limitations:
  - Only controls the machine the MCP server runs on (not remote)
  - No REMOTE_EXEC (would be localhost SSH, pointless)
  - macOS: requires Accessibility permission for pynput keyboard
  - Linux: requires X11 or Wayland (limited Wayland support in pynput)
"""

from __future__ import annotations

import base64
import io
import time
from typing import ClassVar

from .base import BaseAdapter, Capability

try:
    import mss  # type: ignore
    import mss.tools  # type: ignore
    _HAS_MSS = True
except ImportError:
    _HAS_MSS = False

try:
    from pynput import mouse as _mouse_ctl, keyboard as _kbd_ctl  # type: ignore
    _HAS_PYNPUT = True
except ImportError:
    _HAS_PYNPUT = False


class LocalAdapter(BaseAdapter):
    name: ClassVar[str] = "local"
    description: ClassVar[str] = (
        "Local display adapter (mss + pynput). "
        "Controls the machine the MCP server is running on. "
        "No remote connection needed. "
        "Requires: uv pip install mss pynput"
    )
    capabilities: ClassVar[frozenset[Capability]] = frozenset({
        Capability.SCREENSHOT,
        Capability.MOUSE,
        Capability.KEYBOARD,
    })

    def __init__(self) -> None:
        self._connected = False
        self._width = 1920
        self._height = 1080
        self._mouse: "_mouse_ctl.Controller | None" = None
        self._kbd: "_kbd_ctl.Controller | None" = None

    # ---- lifecycle -------------------------------------------------------

    def connect(self, host: str = "localhost", port: int = 0,
                username: str = "", password: str = "", **kwargs) -> None:
        if not _HAS_MSS:
            raise RuntimeError("mss not installed. Run: uv pip install mss pynput")
        if not _HAS_PYNPUT:
            raise RuntimeError("pynput not installed. Run: uv pip install mss pynput")

        with mss.mss() as sct:
            mon = sct.monitors[1]  # primary monitor
            self._width = mon["width"]
            self._height = mon["height"]

        self._mouse = _mouse_ctl.Controller()
        self._kbd = _kbd_ctl.Controller()
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._mouse = None
        self._kbd = None

    @property
    def connected(self) -> bool:
        return self._connected

    # ---- vision ----------------------------------------------------------

    @property
    def screen_size(self) -> tuple[int, int]:
        return self._width, self._height

    def screenshot(self) -> str | None:
        if not self._connected or not _HAS_MSS:
            return None
        try:
            with mss.mss() as sct:
                mon = sct.monitors[1]
                grab = sct.grab(mon)
                from PIL import Image
                img = Image.frombytes("RGB", grab.size, grab.bgra, "raw", "BGRX")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None

    # ---- mouse -----------------------------------------------------------

    def click(self, x: float, y: float, button: int = 1, double: bool = False) -> None:
        if not self._connected or self._mouse is None:
            return
        px, py = int(x * self._width), int(y * self._height)
        self._mouse.position = (px, py)
        btn = {1: _mouse_ctl.Button.left,
               2: _mouse_ctl.Button.middle,
               3: _mouse_ctl.Button.right}.get(button, _mouse_ctl.Button.left)
        count = 2 if double else 1
        for _ in range(count):
            self._mouse.press(btn)
            self._mouse.release(btn)

    def move_mouse(self, x: float, y: float) -> None:
        if self._connected and self._mouse is not None:
            self._mouse.position = (int(x * self._width), int(y * self._height))

    def scroll(self, x: float, y: float, dx: int = 0, dy: int = 0) -> None:
        if not self._connected or self._mouse is None:
            return
        self._mouse.position = (int(x * self._width), int(y * self._height))
        self._mouse.scroll(dx, -dy)  # pynput: positive dy = scroll up

    # ---- keyboard --------------------------------------------------------

    def type_text(self, text: str) -> None:
        if not self._connected or self._kbd is None:
            return
        self._kbd.type(text)

    def press_combo(self, combo: str) -> None:
        if not self._connected or self._kbd is None:
            return
        from pynput.keyboard import Key, KeyCode

        _KEY_MAP = {
            "ctrl": Key.ctrl, "control": Key.ctrl,
            "alt": Key.alt, "shift": Key.shift,
            "super": Key.cmd, "win": Key.cmd, "cmd": Key.cmd,
            "enter": Key.enter, "return": Key.enter,
            "escape": Key.esc, "esc": Key.esc,
            "tab": Key.tab, "backspace": Key.backspace,
            "delete": Key.delete, "space": Key.space,
            "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
            "home": Key.home, "end": Key.end,
            "pageup": Key.page_up, "pagedown": Key.page_down,
            **{f"f{n}": getattr(Key, f"f{n}") for n in range(1, 13)},
        }

        parts = [p.strip().lower() for p in combo.split("+")]
        keys = [_KEY_MAP.get(p, KeyCode.from_char(p)) for p in parts]

        # Press all modifiers, then the main key, then release in reverse
        for k in keys:
            self._kbd.press(k)
        for k in reversed(keys):
            self._kbd.release(k)
