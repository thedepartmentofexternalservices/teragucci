"""
RDPAdapter -- candidate adapter for Windows machines via RDP.

Strategy: spawn an xfreerdp process in headless mode (Xvfb display),
capture screenshots with xwd/imagemagick, send input via xdotool.

This is deliberately thin -- RDP was designed for human interaction,
not programmatic control. For production use on Windows targets, prefer
installing the Teraguchi server (which uses uinput-equivalent on Windows
via the Win32 SendInput API).

Prerequisites on the agent machine (Linux/macOS):
  - xfreerdp (freerdp2 or freerdp3)
  - Xvfb + xwd + ImageMagick (for screenshot capture on Linux)
  - On macOS: use wfreerdp or Microsoft Remote Desktop + screencapture

Limitations vs TeraGuchiAdapter:
  - No streaming ring buffer
  - Screenshot requires xwd roundtrip (slow, ~200ms per frame)
  - Clipboard via xdotool only (limited)
  - No AT-SPI / accessibility tree

Install:
  Linux: sudo apt install freerdp2-x11 xvfb imagemagick x11-utils xdotool
  macOS: brew install freerdp
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import subprocess
import tempfile
import time
from typing import ClassVar

from .base import BaseAdapter, Capability


class RDPAdapter(BaseAdapter):
    name: ClassVar[str] = "rdp"
    description: ClassVar[str] = (
        "RDP (Remote Desktop Protocol) adapter via xfreerdp. "
        "Targets Windows machines. Requires xfreerdp on agent machine. "
        "Screenshots via xwd/ImageMagick (slow). No streaming ring buffer."
    )
    capabilities: ClassVar[frozenset[Capability]] = frozenset({
        Capability.SCREENSHOT,
        Capability.MOUSE,
        Capability.KEYBOARD,
    })

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._display: str = ":99"
        self._xvfb_proc: subprocess.Popen | None = None
        self._host = ""
        self._username = ""
        self._width = 1920
        self._height = 1080

    # ---- lifecycle -------------------------------------------------------

    def connect(
        self,
        host: str,
        port: int = 3389,
        username: str = "",
        password: str = "",
        width: int = 1920,
        height: int = 1080,
        domain: str = "",
        **kwargs,
    ) -> None:
        self._host = host
        self._username = username
        self._width = width
        self._height = height

        # On Linux: start Xvfb if not already running on our display
        if shutil.which("Xvfb"):
            display_num = 99
            self._display = f":{display_num}"
            self._xvfb_proc = subprocess.Popen(
                ["Xvfb", self._display, "-screen", "0", f"{width}x{height}x24"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)

        cmd = [
            "xfreerdp",
            f"/v:{host}:{port}",
            f"/u:{username}",
            f"/p:{password}",
            f"/w:{width}",
            f"/h:{height}",
            "/cert:ignore",
            "/rfx",
            "+auto-reconnect",
            "-grab-keyboard",
        ]
        if domain:
            cmd.append(f"/d:{domain}")

        env = os.environ.copy()
        env["DISPLAY"] = self._display

        self._proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give RDP session time to authenticate and render first frame
        time.sleep(3.0)
        if self._proc.poll() is not None:
            raise ConnectionError(
                f"xfreerdp exited immediately (code {self._proc.returncode}). "
                "Check credentials, host, and that xfreerdp is installed."
            )

    def disconnect(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._xvfb_proc is not None:
            self._xvfb_proc.terminate()
            self._xvfb_proc = None

    @property
    def connected(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ---- vision ----------------------------------------------------------

    @property
    def screen_size(self) -> tuple[int, int]:
        return self._width, self._height

    def screenshot(self) -> str | None:
        if not self.connected:
            return None
        # Use xwd + convert (ImageMagick) to grab the Xvfb root window
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name

            env = os.environ.copy()
            env["DISPLAY"] = self._display

            # xwd writes X11 window dump, convert turns it into PNG
            xwd = subprocess.run(
                ["xwd", "-root", "-silent"],
                capture_output=True,
                env=env,
                timeout=5,
            )
            if xwd.returncode != 0:
                return None

            convert = subprocess.run(
                ["convert", "xwd:-", "png:-"],
                input=xwd.stdout,
                capture_output=True,
                timeout=5,
            )
            if convert.returncode != 0:
                return None

            return base64.b64encode(convert.stdout).decode()
        except Exception:
            return None

    # ---- mouse -----------------------------------------------------------

    def _xdo(self, *args: str) -> None:
        """Run an xdotool command on the Xvfb display."""
        env = os.environ.copy()
        env["DISPLAY"] = self._display
        subprocess.run(["xdotool", *args], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def click(self, x: float, y: float, button: int = 1, double: bool = False) -> None:
        px, py = int(x * self._width), int(y * self._height)
        self._xdo("mousemove", "--sync", str(px), str(py))
        self._xdo("click", str(button))
        if double:
            time.sleep(0.05)
            self._xdo("click", str(button))

    def move_mouse(self, x: float, y: float) -> None:
        self._xdo("mousemove", "--sync",
                  str(int(x * self._width)), str(int(y * self._height)))

    def scroll(self, x: float, y: float, dx: int = 0, dy: int = 0) -> None:
        self.move_mouse(x, y)
        # xdotool: button 4=scroll up, 5=down, 6=left, 7=right
        btn = 5 if dy > 0 else 4 if dy < 0 else (7 if dx > 0 else 6)
        for _ in range(abs(dy or dx or 1)):
            self._xdo("click", str(btn))

    # ---- keyboard --------------------------------------------------------

    def type_text(self, text: str) -> None:
        self._xdo("type", "--clearmodifiers", text)

    def press_combo(self, combo: str) -> None:
        # xdotool uses X11 keysym names joined with '+'
        self._xdo("key", "--clearmodifiers", combo.lower()
                  .replace("ctrl", "control")
                  .replace("win", "super")
                  .replace("enter", "Return")
                  .replace("escape", "Escape")
                  .replace("delete", "Delete")
                  .replace("backspace", "BackSpace")
                  .replace("pageup", "Prior")
                  .replace("pagedown", "Next"))
