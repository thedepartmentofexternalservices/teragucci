"""
Clipboard synchronization for the macOS server.

Mirrors the interface of ``server/clipboard.py`` (the Linux xclip/xsel
wrapper) so the session manager can treat the two platforms
identically.

NSPasteboard has no push-style change notification API — you poll
``changeCount`` and diff against the last value. Any increment means
something (possibly another app, possibly us) changed the pasteboard.
We remember what we last wrote so we don't echo our own writes back
to the client.

Phase 3 (Plan 03-06) extensions:

  * PNG image path — ``get_clipboard_image`` / ``set_clipboard_image``
    via NSPasteboardTypePNG with the same magic-byte + 64 MB
    defense-in-depth validation as the Linux path (D-13 / D-14 / D-16).
  * Unified ``on_change(content_type, payload)`` callback — matches the
    Linux ClipboardSync contract so the session runtime can route both
    platforms through a single dispatcher.
"""

import hashlib
import logging
import threading
import time
from typing import Callable, Optional, Union

# Reuse the defense-in-depth validator + constants from the Linux
# module so both platforms carry identical PNG validation contracts.
# Importing from server.clipboard keeps a single source of truth for
# PNG_MAGIC / PNG_MAX_BYTES and avoids two copies drifting on future
# security updates (D-16 defense-in-depth — each trust boundary
# revalidates, but with the SAME rules).
from server.clipboard import (
    PNG_MAGIC as _SHARED_PNG_MAGIC,
    PNG_MAX_BYTES as _SHARED_PNG_MAX_BYTES,
    validate_png_payload,
)

# Re-export at the Mac module scope so both platforms carry the same
# constant names inline (the Plan 03-06 acceptance grep expects each
# server-clipboard module to own the literal values for audit clarity).
# These MUST stay byte-identical to the Linux defaults — we only
# duplicate the literal for greppability, not for divergence.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_MAX_BYTES: int = 64 * 1024 * 1024  # D-14 — 64 MB cap (mirrors server.clipboard)
assert PNG_MAGIC == _SHARED_PNG_MAGIC, (
    "PNG_MAGIC drift between server.clipboard and server.mac_clipboard"
)
assert PNG_MAX_BYTES == _SHARED_PNG_MAX_BYTES, (
    "PNG_MAX_BYTES drift between server.clipboard and server.mac_clipboard"
)

logger = logging.getLogger(__name__)

try:
    from AppKit import (
        NSPasteboard,
        NSPasteboardTypeString,
        NSPasteboardTypePNG,  # Phase 3 D-13
    )
    from Foundation import NSData
    _HAS_APPKIT = True
except Exception as _e:
    _HAS_APPKIT = False
    _import_error = _e


class MacClipboardSync:
    """NSPasteboard-backed clipboard sync.

    Matches ``ClipboardSync`` from server/clipboard.py:
      * ``available`` property
      * ``get_clipboard() -> str``
      * ``set_clipboard(text: str)``
      * ``get_clipboard_image() -> Optional[bytes]``  (Plan 03-06 / D-13)
      * ``set_clipboard_image(png_bytes: bytes)``     (Plan 03-06 / D-13)
      * ``start_monitoring(on_change)`` / ``stop()``
    """

    def __init__(self, display: str = "", poll_interval: float = 0.25):
        # ``display`` is only here to match the Linux constructor
        # signature; it's meaningless on macOS.
        del display
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_content = ""
        # Phase 3 D-13 — echo suppression for the PNG path; sha256 of
        # the last PNG we wrote so our own writes don't fire the
        # callback back at us.
        self._last_image_hash: bytes = b""
        self._last_change_count = -1
        self._on_clipboard_change: Optional[Callable[[str, Union[str, bytes]], None]] = None

        if _HAS_APPKIT:
            self._pb = NSPasteboard.generalPasteboard()
            try:
                self._last_change_count = int(self._pb.changeCount())
            except Exception:
                self._last_change_count = -1
        else:
            self._pb = None
            logger.warning(
                "AppKit not available (%s) — clipboard sync disabled",
                _import_error,
            )

    @property
    def available(self) -> bool:
        return self._pb is not None

    def get_clipboard(self) -> str:
        if self._pb is None:
            return ""
        try:
            s = self._pb.stringForType_(NSPasteboardTypeString)
            return str(s) if s is not None else ""
        except Exception as e:
            logger.debug("NSPasteboard read failed: %s", e)
            return ""

    def set_clipboard(self, text: str):
        if self._pb is None:
            return
        try:
            self._pb.clearContents()
            ok = self._pb.setString_forType_(text, NSPasteboardTypeString)
            if not ok:
                logger.warning("NSPasteboard setString returned False")
                return
            self._last_content = text
            # Bump our cached change count so the poll loop doesn't
            # fire the callback for our own write.
            try:
                self._last_change_count = int(self._pb.changeCount())
            except Exception:
                pass
            logger.debug("Clipboard set: %d chars", len(text))
        except Exception as e:
            logger.error("Failed to set clipboard: %s", e)

    # ── Phase 3 D-13 / D-14 / D-16 — PNG image clipboard ──────────────

    def get_clipboard_image(self) -> Optional[bytes]:
        """Return the current clipboard PNG payload, or None.

        Reads ``NSPasteboardTypePNG`` via ``dataForType_``; PyObjC
        returns an ``NSData`` instance that we convert to Python
        ``bytes``. Validated through :func:`validate_png_payload`
        before return — malformed payloads become None (D-16).
        """
        if self._pb is None or not _HAS_APPKIT:
            return None
        try:
            data = self._pb.dataForType_(NSPasteboardTypePNG)
            if data is None:
                return None
            png = bytes(data)
            if not validate_png_payload(png):
                return None
            return png
        except Exception as e:
            logger.debug("NSPasteboard image read failed: %s", e)
            return None

    def set_clipboard_image(self, png_bytes: bytes) -> None:
        """Phase 3 D-13 / D-14 / D-16 — set the clipboard with a PNG payload.

        Magic-byte + size cap validated BEFORE invoking PyObjC
        (defense-in-depth). Updates ``_last_image_hash`` +
        ``_last_change_count`` for echo suppression so the next poll
        tick doesn't re-broadcast our own write back to the client.
        """
        if self._pb is None or not _HAS_APPKIT:
            return
        if not validate_png_payload(png_bytes):
            return  # logged inside validate_png_payload
        try:
            self._pb.clearContents()
            nsdata = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
            ok = self._pb.setData_forType_(nsdata, NSPasteboardTypePNG)
            if not ok:
                logger.warning("NSPasteboard setData returned False")
                return
            self._last_image_hash = hashlib.sha256(png_bytes).digest()
            try:
                self._last_change_count = int(self._pb.changeCount())
            except Exception:
                pass
            logger.info("clipboard.image_set size=%d", len(png_bytes))
        except Exception as e:
            logger.error("Failed to set clipboard image: %s", e)

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
        if self._pb is None:
            return
        self._on_clipboard_change = on_change
        self._running = True
        self._last_content = self.get_clipboard()
        try:
            self._last_change_count = int(self._pb.changeCount())
        except Exception:
            pass
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="mac-clipboard-monitor",
        )
        self._thread.start()

    def _poll_loop(self):
        while self._running:
            try:
                current_cc = int(self._pb.changeCount())
                if current_cc != self._last_change_count:
                    self._last_change_count = current_cc
                    # Phase 3 D-13 — route image/png when present,
                    # else text/plain. Images win over text when both
                    # types are on the pasteboard (Qt / Preview copies
                    # often include both; we want the image payload).
                    types = []
                    try:
                        t = self._pb.types()
                        if t is not None:
                            types = list(t)
                    except Exception:
                        types = []
                    has_png = NSPasteboardTypePNG in types
                    if has_png:
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
                                            "Clipboard change callback error: %s", e
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
                                        "Clipboard change callback error: %s", e
                                    )
            except Exception as e:
                logger.debug("Clipboard poll error: %s", e)
            time.sleep(self.poll_interval)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
