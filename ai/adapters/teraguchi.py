"""
TeraGuchiAdapter -- master adapter.

Wraps HeadlessClient (WebSocket, H.264/NVENC, PAM, uinput).
This is the highest-fidelity option: real kernel-level input, hardware-
accelerated video, streaming ring buffer, and change detection via pixel diff
on decoded frames -- no polling needed.

Capabilities above all other adapters:
  - STREAMING: 300-frame ring buffer, zero-copy from decode thread
  - CHANGE_DETECT: event-driven, sub-frame latency
  - REMOTE_EXEC: SSH passthrough for shell commands
  - ACCESSIBILITY: AT-SPI tree via SSH + pyatspi
"""

from __future__ import annotations

import os
import subprocess
from typing import ClassVar, Optional

from .base import BaseAdapter, Capability


class TeraGuchiAdapter(BaseAdapter):
    name: ClassVar[str] = "teraguchi"
    description: ClassVar[str] = (
        "Teraguchi native WebSocket protocol. "
        "H.264/H.265/AV1 + NVENC, uinput (kernel-level), PAM auth, "
        "300-frame ring buffer, change detection. "
        "Requires Teraguchi server on target."
    )
    capabilities: ClassVar[frozenset[Capability]] = frozenset({
        Capability.SCREENSHOT,
        Capability.STREAMING,
        Capability.CHANGE_DETECT,
        Capability.MOUSE,
        Capability.KEYBOARD,
        Capability.CLIPBOARD,
        Capability.REMOTE_EXEC,
        Capability.ACCESSIBILITY,
    })

    def __init__(self) -> None:
        from ai.headless_client import HeadlessClient
        self._client: HeadlessClient = HeadlessClient()

    # ---- lifecycle -------------------------------------------------------

    def connect(
        self,
        host: str,
        port: int = 4443,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        timeout: float = 20.0,
    ) -> None:
        if self._client.connected:
            self._client.disconnect()
        self._client.connect(host, port, username, password, use_tls=use_tls, timeout=timeout)

    def disconnect(self) -> None:
        self._client.disconnect()

    @property
    def connected(self) -> bool:
        return self._client.connected

    # ---- vision ----------------------------------------------------------

    @property
    def screen_size(self) -> tuple[int, int]:
        return self._client.screen_size

    def screenshot(self) -> str | None:
        return self._client.screenshot()

    def watch_frames(self, duration_ms: int = 2000, sample_every_ms: int = 500) -> list[tuple[int, str]]:
        return self._client.watch_frames(duration_ms=duration_ms, sample_every_ms=sample_every_ms)

    def wait_for_change(self, timeout_ms: int = 10_000, sensitivity: float = 0.02) -> tuple[int, str, float] | None:
        return self._client.wait_for_change(timeout_ms=timeout_ms, sensitivity=sensitivity)

    def latest_frame_history(self, count: int = 5) -> list[tuple[int, str]]:
        return self._client.latest_frame_history(count=count)

    # ---- mouse -----------------------------------------------------------

    def click(self, x: float, y: float, button: int = 1, double: bool = False) -> None:
        self._client.click(x, y, button=button, double=double)

    def move_mouse(self, x: float, y: float) -> None:
        self._client.move_mouse(x, y)

    def scroll(self, x: float, y: float, dx: int = 0, dy: int = 0) -> None:
        self._client.scroll(x, y, dx=dx, dy=dy)

    def drag(self, x1: float, y1: float, x2: float, y2: float, steps: int = 20, duration_ms: int = 300) -> None:
        self._client.drag(x1, y1, x2, y2, steps=steps, duration_ms=duration_ms)

    # ---- keyboard --------------------------------------------------------

    def type_text(self, text: str) -> None:
        self._client.type_text(text)

    def press_combo(self, combo: str) -> None:
        self._client.press_combo(combo)

    # ---- audio -----------------------------------------------------------

    def audio_info(self) -> dict:
        """Return current audio stream info from the server."""
        return self._client.audio_info()

    def listen_audio(self, duration_ms: int = 3000) -> Optional[bytes]:
        """Record remote audio for duration_ms. Returns WAV bytes or None."""
        return self._client.listen_audio(duration_ms=duration_ms)

    # ---- extras (exposed through master MCP) ----------------------------

    def clipboard_get(self) -> str:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=5",
             f"{self._client._username}@{self._client._host}",
             "DISPLAY=:10 xclip -selection clipboard -o 2>/dev/null || "
             "DISPLAY=:10 xsel --clipboard --output 2>/dev/null || echo ''"],
            capture_output=True, text=True, timeout=8,
        )
        return result.stdout.strip() or "(clipboard is empty)"

    def clipboard_set(self, text: str) -> str:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=5",
             f"{self._client._username}@{self._client._host}",
             f"echo {repr(text)} | DISPLAY=:10 xclip -selection clipboard 2>/dev/null || "
             f"echo {repr(text)} | DISPLAY=:10 xsel --clipboard --input 2>/dev/null && echo ok"],
            capture_output=True, text=True, timeout=8,
        )
        return "ok" if result.returncode == 0 else f"Error: {result.stderr.strip()}"

    def run_remote(self, command: str, timeout_sec: int = 30) -> str:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             "-o", "ConnectTimeout=5",
             f"{self._client._username}@{self._client._host}",
             command],
            capture_output=True, text=True, timeout=timeout_sec,
        )
        out = result.stdout
        if result.stderr:
            out += "\n[stderr]\n" + result.stderr
        return out.strip() or "(no output)"
