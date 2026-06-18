"""
Clipboard synchronization for the server.

Monitors the X11 clipboard for changes and can set clipboard contents
when receiving data from the client.

Phase 3 (Plan 03-06) extensions:

  * CRLF preservation — ``get_clipboard`` uses ``text=False`` subprocess
    with explicit UTF-8 decode so the originating newline encoding
    survives verbatim (D-16 / CLIP-01 — the old text-mode-True flag
    silently converted CRLF → LF via universal-newlines).
  * PNG image path — ``get_clipboard_image`` / ``set_clipboard_image``
    route raw PNG bytes through ``xclip -t image/png`` with magic-byte
    + 64 MB defense-in-depth validation (D-13 / D-14 / D-16).
  * Unified on_change callback — ``start_monitoring(on_change)`` emits
    ``on_change(content_type, payload)`` so callers can distinguish text
    vs image events. ``_poll_loop`` queries xclip TARGETS to pick the
    right path each tick, preserving the single-thread poll pattern.
"""

import hashlib
import io
import logging
import subprocess
import threading
import time
from typing import Callable, Optional, Union

logger = logging.getLogger(__name__)


# Phase 3 D-16 / D-14 — PNG magic-byte + 64 MB cap defense-in-depth.
#
# PNG_MAGIC is the canonical 8-byte signature every valid PNG starts
# with (RFC 2083 § 12.11). Validation runs BEFORE the subprocess call on
# set and AFTER the subprocess call on get so malformed payloads never
# traverse the xclip / network boundary. PNG_MAX_BYTES caps total
# payload at 64 MB (D-14); pairs with the 2 MB per-chunk cap in
# common/clipboard_chunks.ClipboardChunkAssembler.
PNG_MAGIC: bytes = b"\x89PNG\r\n\x1a\n"
PNG_MAX_BYTES: int = 64 * 1024 * 1024  # D-14 — 64 MB cap


def validate_png_payload(payload: bytes) -> bool:
    """D-16 defense-in-depth PNG validation.

    Returns True iff ``payload`` is a well-formed PNG within the size
    cap. Logs a structlog-style warning on any failure (counts + sizes
    only, never the payload bytes — T-03-32). Both the send side
    (before ``xclip -i``) and the receive side (after assembler
    completes) call this so a malformed payload is rejected at both
    trust boundaries.

    Failure modes (all return False):
      * payload > PNG_MAX_BYTES (T-03-24 DoS via oversize)
      * payload < 8 bytes OR first 8 bytes != PNG_MAGIC (T-03-23 /
        magic-byte tampering)
      * :meth:`PIL.Image.verify` raises (T-03-23 / internal chunk
        corruption)
    """
    if len(payload) > PNG_MAX_BYTES:
        logger.warning(
            "clipboard.image_oversize size=%d cap=%d",
            len(payload), PNG_MAX_BYTES,
        )
        return False
    if len(payload) < 8 or payload[:8] != PNG_MAGIC:
        logger.warning(
            "clipboard.image_bad_magic prefix=%s",
            payload[:8].hex() if payload else "empty",
        )
        return False
    try:
        from PIL import Image
        Image.open(io.BytesIO(payload)).verify()
    except Exception as e:
        logger.warning("clipboard.image_pil_verify_failed error=%s", e)
        return False
    return True


class ClipboardSync:
    """
    Monitors and controls the system clipboard.

    Uses xclip or xsel for clipboard access (widely available on Linux).
    Polls for changes and notifies via callback.
    """

    def __init__(self, display: str = "", poll_interval: float = 0.5):
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_content = ""
        # Phase 3 D-13 — echo suppression for the PNG path. sha256 digest
        # of the last PNG we wrote; keeps our own writes from firing
        # _on_change on the next poll tick.
        self._last_image_hash: bytes = b""
        self._on_clipboard_change: Optional[Callable[[str, Union[str, bytes]], None]] = None
        self._display = display
        self._tool = self._find_clipboard_tool()

    def _get_env(self) -> dict:
        """Build environment with correct DISPLAY for xclip/xsel."""
        import os
        env = os.environ.copy()
        if self._display:
            env["DISPLAY"] = self._display
        return env

    def _find_clipboard_tool(self) -> str:
        """Find available clipboard tool."""
        for tool in ("xclip", "xsel"):
            try:
                subprocess.run([tool, "--version"], capture_output=True, timeout=2)
                logger.info("Using clipboard tool: %s", tool)
                return tool
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        logger.warning("No clipboard tool found (install xclip or xsel)")
        return ""

    @property
    def available(self) -> bool:
        return bool(self._tool)

    def get_clipboard(self) -> str:
        """Get current clipboard text content.

        Phase 3 D-16 / CLIP-01: ``subprocess.run(..., text=False)`` with
        explicit UTF-8 decode so the originating newline encoding
        (CRLF / LF / CR) survives verbatim. The old text-mode-True flag
        engaged Python's universal-newlines translation which silently
        converted CRLF → LF, breaking artists round-tripping Windows-
        origin clipboard text through a Mac client to a Rocky server.
        """
        if not self._tool:
            return ""
        try:
            if self._tool == "xclip":
                cmd = ["xclip", "-selection", "clipboard", "-o"]
            else:
                cmd = ["xsel", "--clipboard", "--output"]
            # D-16 — text=False returns raw bytes; decode explicitly so
            # Python's universal-newlines layer never touches the bytes.
            result = subprocess.run(cmd, capture_output=True, text=False,
                                    timeout=2, env=self._get_env())
            if result.returncode != 0:
                return ""
            return result.stdout.decode("utf-8", errors="replace")
        except (subprocess.TimeoutExpired, Exception):
            return ""

    def set_clipboard(self, text: str):
        """Set clipboard text content."""
        if not self._tool:
            return
        try:
            if self._tool == "xclip":
                cmd = ["xclip", "-selection", "clipboard", "-i"]
            else:
                cmd = ["xsel", "--clipboard", "--input"]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, env=self._get_env())
            proc.communicate(input=text.encode("utf-8"), timeout=2)
            self._last_content = text
            logger.debug("Clipboard set: %d chars", len(text))
        except (subprocess.TimeoutExpired, Exception) as e:
            logger.error("Failed to set clipboard: %s", e)

    # ── Phase 3 D-13 / D-14 / D-16 — PNG image clipboard ──────────────

    def get_clipboard_image(self) -> Optional[bytes]:
        """Return the current clipboard PNG payload, or None.

        Phase 3 D-13 / D-14 / D-16. Uses ``xclip -t image/png -o`` on
        the xclip path; xsel doesn't support image targets so xsel-only
        hosts return None (text-only clipboard). The returned bytes are
        validated through :func:`validate_png_payload` before being
        handed back — malformed payloads become None.
        """
        if self._tool != "xclip":
            return None  # xsel doesn't support image targets
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                capture_output=True, timeout=2, env=self._get_env(),
            )
            if result.returncode != 0 or not result.stdout:
                return None
            png = result.stdout
            if not validate_png_payload(png):
                return None
            return png
        except (subprocess.TimeoutExpired, Exception):
            return None

    def set_clipboard_image(self, png_bytes: bytes) -> None:
        """Phase 3 D-13 / D-14 / D-16 — set the clipboard with a PNG payload.

        Magic-byte + size cap validated BEFORE invoking xclip
        (defense-in-depth: every trust boundary revalidates). Updates
        ``_last_image_hash`` for echo suppression so the next poll tick
        doesn't re-broadcast our own write back to the client.
        """
        if self._tool != "xclip":
            logger.warning(
                "clipboard.image_xclip_unavailable tool=%s", self._tool,
            )
            return
        if not validate_png_payload(png_bytes):
            return  # logged inside validate_png_payload
        try:
            proc = subprocess.Popen(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-i"],
                stdin=subprocess.PIPE, env=self._get_env(),
            )
            proc.communicate(input=png_bytes, timeout=2)
            self._last_image_hash = hashlib.sha256(png_bytes).digest()
            logger.info("clipboard.image_set size=%d", len(png_bytes))
        except (subprocess.TimeoutExpired, Exception) as e:
            logger.error("clipboard.image_set_failed error=%s", e)

    # ── Monitor loop ──────────────────────────────────────────────────

    def start_monitoring(
        self,
        on_change: Callable[[str, Union[str, bytes]], None],
    ) -> None:
        """Start monitoring clipboard for changes.

        Args:
            on_change: Callback ``on_change(content_type, payload)``
                called when clipboard content changes. ``content_type``
                is ``"text/plain"`` (payload is ``str``) or
                ``"image/png"`` (payload is ``bytes``). Plan 03-06
                promoted the single-arg callback from Phase 2 to the
                two-arg form so the image path can share the same
                broadcast hook as text.
        """
        if not self._tool:
            return
        self._on_clipboard_change = on_change
        self._running = True
        self._last_content = self.get_clipboard()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="clipboard-monitor",
        )
        self._thread.start()

    def _poll_loop(self):
        """Poll clipboard for changes (text + image).

        Phase 3 D-13: single poll thread routes via xclip TARGETS query.
        When ``image/png`` is available, image path wins over text (Qt
        screenshot tools copy both in parallel; we want the image).
        """
        while self._running:
            try:
                has_image = False
                if self._tool == "xclip":
                    try:
                        targets = subprocess.run(
                            ["xclip", "-selection", "clipboard",
                             "-t", "TARGETS", "-o"],
                            capture_output=True, timeout=1,
                            env=self._get_env(),
                        )
                        has_image = (
                            targets.returncode == 0
                            and b"image/png" in (targets.stdout or b"")
                        )
                    except (subprocess.TimeoutExpired, Exception):
                        has_image = False

                if has_image:
                    img = self.get_clipboard_image()
                    if img is not None:
                        h = hashlib.sha256(img).digest()
                        if h != self._last_image_hash:
                            self._last_image_hash = h
                            if self._on_clipboard_change:
                                try:
                                    self._on_clipboard_change("image/png", img)
                                except Exception as e:
                                    logger.debug(
                                        "Clipboard change callback error: %s", e,
                                    )
                else:
                    current = self.get_clipboard()
                    if current and current != self._last_content:
                        self._last_content = current
                        if self._on_clipboard_change:
                            try:
                                self._on_clipboard_change("text/plain", current)
                            except Exception as e:
                                logger.debug(
                                    "Clipboard change callback error: %s", e,
                                )
            except Exception as e:
                logger.debug("Clipboard poll error: %s", e)
            time.sleep(self.poll_interval)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
