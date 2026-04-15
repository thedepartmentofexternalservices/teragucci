"""
VNCAdapter -- candidate adapter for machines without Teraguchi server.

Uses vncdotool (pip: vncdotool) for connection and input.
Screenshots are captured via the VNC framebuffer update mechanism.

Limitations vs TeraGuchiAdapter:
  - No streaming ring buffer (polling only)
  - RFB framebuffer: raw RGB, no hardware encoding -> higher bandwidth
  - Input via VNC key names, not Qt keycodes
  - No PAM / SSH-backed auth
  - No REMOTE_EXEC / ACCESSIBILITY (no SSH channel)

Install dep:  uv pip install vncdotool
"""

from __future__ import annotations

import base64
import io
import time
from typing import ClassVar

from .base import BaseAdapter, Capability

try:
    from vncdotool import api as _vnc_api  # type: ignore
    _HAS_VNCDOTOOL = True
except ImportError:
    _HAS_VNCDOTOOL = False


class VNCAdapter(BaseAdapter):
    name: ClassVar[str] = "vnc"
    description: ClassVar[str] = (
        "VNC / RFB protocol adapter (vncdotool). "
        "Works with TightVNC, TigerVNC, RealVNC, x11vnc. "
        "No streaming ring buffer -- uses polling. "
        "Requires: uv pip install vncdotool"
    )
    capabilities: ClassVar[frozenset[Capability]] = frozenset({
        Capability.SCREENSHOT,
        Capability.MOUSE,
        Capability.KEYBOARD,
    })

    def __init__(self) -> None:
        self._client = None
        self._host = ""
        self._width = 1920
        self._height = 1080

    # ---- lifecycle -------------------------------------------------------

    def connect(
        self,
        host: str,
        port: int = 5900,
        username: str = "",
        password: str = "",
        **kwargs,
    ) -> None:
        if not _HAS_VNCDOTOOL:
            raise RuntimeError(
                "vncdotool is not installed. Run: uv pip install vncdotool"
            )
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass

        self._host = host
        self._client = _vnc_api.connect(
            host,
            password=password or None,
            port=port,
        )
        # Attempt to read framebuffer size
        try:
            self._width = self._client.protocol.screen.width
            self._height = self._client.protocol.screen.height
        except Exception:
            pass

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    # ---- vision ----------------------------------------------------------

    @property
    def screen_size(self) -> tuple[int, int]:
        return self._width, self._height

    def screenshot(self) -> str | None:
        if self._client is None:
            return None
        try:
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            self._client.captureScreen(tmp_path)
            with open(tmp_path, "rb") as f:
                data = f.read()
            os.unlink(tmp_path)
            return base64.b64encode(data).decode()
        except Exception:
            return None

    # ---- mouse -----------------------------------------------------------

    def click(self, x: float, y: float, button: int = 1, double: bool = False) -> None:
        if self._client is None:
            return
        px = int(x * self._width)
        py = int(y * self._height)
        self._client.mouseMove(px, py)
        self._client.mousePress(button)
        if double:
            time.sleep(0.05)
            self._client.mousePress(button)

    def move_mouse(self, x: float, y: float) -> None:
        if self._client is None:
            return
        self._client.mouseMove(int(x * self._width), int(y * self._height))

    def scroll(self, x: float, y: float, dx: int = 0, dy: int = 0) -> None:
        if self._client is None:
            return
        self._client.mouseMove(int(x * self._width), int(y * self._height))
        # VNC scroll buttons: 4=up, 5=down, 6=left, 7=right
        btn = 5 if dy > 0 else 4 if dy < 0 else (7 if dx > 0 else 6)
        for _ in range(abs(dy or dx or 1)):
            self._client.mousePress(btn)

    # ---- keyboard --------------------------------------------------------

    # VNC key names follow X11 keysym naming
    _COMBO_MAP = {
        "ctrl": "ctrl", "control": "ctrl",
        "alt": "alt", "shift": "shift", "super": "super", "win": "super",
        "enter": "return", "return": "return",
        "escape": "escape", "esc": "escape",
        "tab": "tab", "backspace": "backspace", "delete": "delete",
        "space": "space", "up": "up", "down": "down", "left": "left", "right": "right",
        "home": "home", "end": "end", "pageup": "prior", "pagedown": "next",
        "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
        "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
        "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
    }

    def type_text(self, text: str) -> None:
        if self._client is None:
            return
        self._client.type(text)

    def press_combo(self, combo: str) -> None:
        if self._client is None:
            return
        parts = [p.strip().lower() for p in combo.split("+")]
        keys = [self._COMBO_MAP.get(p, p) for p in parts]
        if len(keys) == 1:
            self._client.keyPress(keys[0])
        else:
            # Hold modifiers, press key, release
            modifiers = keys[:-1]
            key = keys[-1]
            for mod in modifiers:
                self._client.keyDown(mod)
            self._client.keyPress(key)
            for mod in reversed(modifiers):
                self._client.keyUp(mod)
