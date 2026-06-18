"""
Input injection module for macOS.

Uses CoreGraphics event posting to inject mouse, keyboard, scroll, and
(best-effort) tablet events into the system event stream. Equivalent to
server/input_injector.py on Linux, which uses uinput.

Design notes
------------
* **TCC permission required**: the calling process must be granted
  "Accessibility" (and "Input Monitoring" for keyboard key-up events
  on some macOS releases). The first call to CGEventPost triggers a
  permission prompt; until granted, events silently fail. This module
  logs a warning if it can't detect permission at init time.

* **Unified event API**: unlike Linux (where the injector owns three
  separate uinput devices — mouse, keyboard, pen), macOS exposes a
  single event stream. We keep the ``InputInjector`` class's public
  interface the same as Linux so the session manager doesn't care
  which platform it's running on.

* **Keyboard scan codes**: the client currently sends Linux-native
  scan codes (from ``linux/input-event-codes.h``). macOS uses a
  completely different virtual keycode space (kVK_*). We translate
  Linux codes → macOS virtual keycodes via a static map in
  ``_LINUX_TO_MAC_KEYCODE``. Unknown codes are dropped with a debug
  log — Phase 1 covers the US-ANSI layout and the common Flame
  modifiers; extended symbols, F13+, and other locales get added as
  the test matrix grows.

* **Pen/tablet**: macOS has ``kCGEventTabletPointer`` /
  ``kCGEventTabletProximity`` events, but synthetic injection of
  those events into the global stream does NOT feed into NSEvent's
  ``-pressure`` / ``-tilt`` accessors for most apps the way Wacom's
  own kext does. For Phase 1 we downgrade pen events to ordinary
  mouse events (tip-down = left-click-drag, eraser = right-click-drag)
  and log a warning. Full tablet support requires a signed
  ``IOHIDUserDevice`` helper, which is future work.

* **Coordinates**: CG uses a top-left-origin coordinate system, same
  as Linux. The client sends normalized 0.0–1.0 coordinates that we
  multiply by the actual screen size. Multi-monitor: we currently
  clip to the primary display's bounds; multi-display pen routing is
  TODO and tracked alongside the server-side display switch logic.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Deferred PyObjC imports — guarded so the module can at least be
# imported on Linux for static analysis.
try:
    from Quartz import (
        CGEventCreateMouseEvent,
        CGEventCreateKeyboardEvent,
        CGEventCreateScrollWheelEvent,
        CGEventKeyboardSetUnicodeString,
        CGEventPost,
        CGEventSetType,
        CGEventSetIntegerValueField,
        CGPointMake,
        CGMainDisplayID,
        CGDisplayPixelsWide,
        CGDisplayPixelsHigh,
        kCGHIDEventTap,
        kCGEventMouseMoved,
        kCGEventLeftMouseDown,
        kCGEventLeftMouseUp,
        kCGEventLeftMouseDragged,
        kCGEventRightMouseDown,
        kCGEventRightMouseUp,
        kCGEventRightMouseDragged,
        kCGEventOtherMouseDown,
        kCGEventOtherMouseUp,
        kCGEventOtherMouseDragged,
        kCGMouseButtonLeft,
        kCGMouseButtonRight,
        kCGMouseButtonCenter,
        kCGScrollEventUnitPixel,
        kCGScrollEventUnitLine,
    )
    # AXIsProcessTrusted lives in HIServices / ApplicationServices.
    try:
        from ApplicationServices import AXIsProcessTrusted
    except Exception:
        AXIsProcessTrusted = None  # type: ignore
    _HAS_CG = True
except Exception as _e:
    _HAS_CG = False
    _import_error = _e


class MacInputInjectorError(RuntimeError):
    pass


def _check_cg_available():
    if not _HAS_CG:
        raise MacInputInjectorError(
            f"CoreGraphics / PyObjC not available: {_import_error}. "
            "Run: pip install pyobjc-framework-Quartz "
            "pyobjc-framework-ApplicationServices"
        )


# ----------------------------------------------------------------------
# Linux evdev scan code → macOS virtual keycode map
# ----------------------------------------------------------------------
#
# Linux codes from /usr/include/linux/input-event-codes.h (KEY_*).
# macOS codes from <HIToolbox/Events.h> (kVK_*).
#
# Only the US-ANSI layout + common modifiers are covered here. Extend
# as needed when we have more test coverage.
_LINUX_TO_MAC_KEYCODE = {
    # Letters (KEY_A = 30 .. KEY_Z = 44 in Linux, non-alphabetical layout)
    30: 0x00,   # a -> kVK_ANSI_A
    48: 0x0B,   # b -> kVK_ANSI_B
    46: 0x08,   # c -> kVK_ANSI_C
    32: 0x02,   # d -> kVK_ANSI_D
    18: 0x0E,   # e -> kVK_ANSI_E
    33: 0x03,   # f -> kVK_ANSI_F
    34: 0x05,   # g -> kVK_ANSI_G
    35: 0x04,   # h -> kVK_ANSI_H
    23: 0x22,   # i -> kVK_ANSI_I
    36: 0x26,   # j -> kVK_ANSI_J
    37: 0x28,   # k -> kVK_ANSI_K
    38: 0x25,   # l -> kVK_ANSI_L
    50: 0x2E,   # m -> kVK_ANSI_M
    49: 0x2D,   # n -> kVK_ANSI_N
    24: 0x1F,   # o -> kVK_ANSI_O
    25: 0x23,   # p -> kVK_ANSI_P
    16: 0x0C,   # q -> kVK_ANSI_Q
    19: 0x0F,   # r -> kVK_ANSI_R
    31: 0x01,   # s -> kVK_ANSI_S
    20: 0x11,   # t -> kVK_ANSI_T
    22: 0x20,   # u -> kVK_ANSI_U
    47: 0x09,   # v -> kVK_ANSI_V
    17: 0x0D,   # w -> kVK_ANSI_W
    45: 0x07,   # x -> kVK_ANSI_X
    21: 0x10,   # y -> kVK_ANSI_Y
    44: 0x06,   # z -> kVK_ANSI_Z

    # Digits
    2: 0x12,    # 1
    3: 0x13,    # 2
    4: 0x14,    # 3
    5: 0x15,    # 4
    6: 0x17,    # 5
    7: 0x16,    # 6
    8: 0x1A,    # 7
    9: 0x1C,    # 8
    10: 0x19,   # 9
    11: 0x1D,   # 0

    # Whitespace / punctuation
    28: 0x24,   # return -> kVK_Return
    1:  0x35,   # escape -> kVK_Escape
    14: 0x33,   # backspace -> kVK_Delete
    15: 0x30,   # tab -> kVK_Tab
    57: 0x31,   # space -> kVK_Space
    12: 0x1B,   # minus/- -> kVK_ANSI_Minus
    13: 0x18,   # equal/= -> kVK_ANSI_Equal
    26: 0x21,   # leftbrace/[ -> kVK_ANSI_LeftBracket
    27: 0x1E,   # rightbrace/] -> kVK_ANSI_RightBracket
    43: 0x2A,   # backslash -> kVK_ANSI_Backslash
    39: 0x29,   # semicolon -> kVK_ANSI_Semicolon
    40: 0x27,   # apostrophe -> kVK_ANSI_Quote
    41: 0x32,   # grave -> kVK_ANSI_Grave
    51: 0x2B,   # comma -> kVK_ANSI_Comma
    52: 0x2F,   # period -> kVK_ANSI_Period
    53: 0x2C,   # slash -> kVK_ANSI_Slash

    # Modifiers
    42: 0x38,   # left shift -> kVK_Shift
    54: 0x3C,   # right shift -> kVK_RightShift
    29: 0x3B,   # left ctrl -> kVK_Control
    97: 0x3E,   # right ctrl -> kVK_RightControl
    56: 0x3A,   # left alt -> kVK_Option
    100: 0x3D,  # right alt -> kVK_RightOption
    125: 0x37,  # left meta (super) -> kVK_Command
    126: 0x36,  # right meta -> kVK_RightCommand
    58: 0x39,   # capslock -> kVK_CapsLock

    # Function keys
    59: 0x7A,   # F1
    60: 0x78,   # F2
    61: 0x63,   # F3
    62: 0x76,   # F4
    63: 0x60,   # F5
    64: 0x61,   # F6
    65: 0x62,   # F7
    66: 0x64,   # F8
    67: 0x65,   # F9
    68: 0x6D,   # F10
    87: 0x67,   # F11
    88: 0x6F,   # F12

    # Arrow keys
    103: 0x7E,  # up -> kVK_UpArrow
    108: 0x7D,  # down -> kVK_DownArrow
    105: 0x7B,  # left -> kVK_LeftArrow
    106: 0x7C,  # right -> kVK_RightArrow

    # Edit keys
    110: 0x72,  # insert -> kVK_Help (mac has no insert; Help is closest)
    111: 0x75,  # delete -> kVK_ForwardDelete
    102: 0x73,  # home
    107: 0x77,  # end
    104: 0x74,  # pageup
    109: 0x79,  # pagedown
}


class MacInputInjector:
    """CoreGraphics-backed input injector for macOS.

    Matches the Linux ``InputInjector`` interface used by
    session_manager: ``handle_message(dict)`` plus ``close()``. The
    sub-device objects (``mouse``, ``keyboard``, ``pen``) are also
    exposed as attributes for callers that want direct access.
    """

    def __init__(self, screen_width: int = 0, screen_height: int = 0):
        _check_cg_available()

        if screen_width <= 0 or screen_height <= 0:
            main = CGMainDisplayID()
            screen_width = int(CGDisplayPixelsWide(main))
            screen_height = int(CGDisplayPixelsHigh(main))

        self.screen_width = screen_width
        self.screen_height = screen_height
        self._last_x = 0.0
        self._last_y = 0.0
        self._buttons_down = set()

        # Sub-device shims so code that was written against the Linux
        # injector can still reach into .mouse / .keyboard / .pen.
        self.mouse = self
        self.keyboard = self
        self.pen = self

        if AXIsProcessTrusted is not None:
            try:
                trusted = bool(AXIsProcessTrusted())
                if not trusted:
                    logger.warning(
                        "Accessibility permission not granted — input "
                        "injection will silently fail until you allow "
                        "this process in System Settings → Privacy & "
                        "Security → Accessibility."
                    )
            except Exception:
                pass

        logger.info(
            "MacInputInjector ready (%dx%d screen)",
            screen_width, screen_height,
        )

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _to_pixels(self, x: float, y: float):
        px = max(0.0, min(1.0, x)) * (self.screen_width - 1)
        py = max(0.0, min(1.0, y)) * (self.screen_height - 1)
        return px, py

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def move_abs(self, x: float, y: float):
        px, py = self._to_pixels(x, y)
        self._last_x, self._last_y = px, py
        # If a button is held, CG wants the "Dragged" variant so the
        # dragged-from app keeps tracking the move.
        if kCGMouseButtonLeft in self._buttons_down:
            ev_type = kCGEventLeftMouseDragged
            btn = kCGMouseButtonLeft
        elif kCGMouseButtonRight in self._buttons_down:
            ev_type = kCGEventRightMouseDragged
            btn = kCGMouseButtonRight
        elif kCGMouseButtonCenter in self._buttons_down:
            ev_type = kCGEventOtherMouseDragged
            btn = kCGMouseButtonCenter
        else:
            ev_type = kCGEventMouseMoved
            btn = kCGMouseButtonLeft
        event = CGEventCreateMouseEvent(
            None, ev_type, CGPointMake(px, py), btn
        )
        CGEventPost(kCGHIDEventTap, event)

    def button(self, button: int, pressed: bool):
        """Mouse button press/release.

        ``button`` is the client's 1=left, 2=middle, 3=right convention
        (same as Linux injector).
        """
        px, py = self._last_x, self._last_y
        if button == 1:
            cg_btn = kCGMouseButtonLeft
            down_t = kCGEventLeftMouseDown
            up_t = kCGEventLeftMouseUp
        elif button == 3:
            cg_btn = kCGMouseButtonRight
            down_t = kCGEventRightMouseDown
            up_t = kCGEventRightMouseUp
        else:
            cg_btn = kCGMouseButtonCenter
            down_t = kCGEventOtherMouseDown
            up_t = kCGEventOtherMouseUp

        ev_type = down_t if pressed else up_t
        event = CGEventCreateMouseEvent(
            None, ev_type, CGPointMake(px, py), cg_btn
        )
        CGEventPost(kCGHIDEventTap, event)
        if pressed:
            self._buttons_down.add(cg_btn)
        else:
            self._buttons_down.discard(cg_btn)

    def scroll(self, dx: int, dy: int):
        # CG scroll wheel event: units=pixel, wheelCount=2 (y, x).
        # dy positive = scroll up (same as Linux convention).
        try:
            event = CGEventCreateScrollWheelEvent(
                None, kCGScrollEventUnitPixel, 2, int(dy), int(dx)
            )
            CGEventPost(kCGHIDEventTap, event)
        except Exception as e:
            logger.debug("Scroll event failed: %s", e)

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def key_event(self, scan_code: int, pressed: bool):
        mac_code = _LINUX_TO_MAC_KEYCODE.get(scan_code)
        if mac_code is None:
            logger.debug("Unmapped Linux scan code %d — dropped", scan_code)
            return
        try:
            event = CGEventCreateKeyboardEvent(None, mac_code, bool(pressed))
            CGEventPost(kCGHIDEventTap, event)
        except Exception as e:
            logger.debug("Key event failed: %s", e)

    # ------------------------------------------------------------------
    # Phase 2 D-11 / D-15 — modifier release + IME commit string
    # ------------------------------------------------------------------

    # Mac virtual key codes for every modifier we care about. Mirrors the
    # Linux InputInjector._MODIFIER_SCAN_CODES surface area so the two
    # platform paths satisfy the same "release every modifier" contract.
    # Source: Carbon HIToolbox/Events.h
    _MAC_MODIFIER_KEYS = (
        0x37,  # kVK_Command (Cmd)
        0x36,  # kVK_RightCommand
        0x38,  # kVK_Shift
        0x3C,  # kVK_RightShift
        0x3A,  # kVK_Option (Alt)
        0x3D,  # kVK_RightOption
        0x3B,  # kVK_Control
        0x3E,  # kVK_RightControl
        0x3F,  # kVK_Function (fn)
        # CapsLock release is a documented no-op on Mac CGEventPost
        # (Caps state is a system toggle, not a held modifier) but we
        # include it for parity. Caps state syncing happens via the
        # KeyEventMsg lock-bit channel (D-14), not this path.
        0x39,  # kVK_CapsLock
    )

    def reset_modifiers(self) -> None:
        """Phase 2 D-11 — release every modifier key on Mac.

        Idempotent. Called from server-side dispatch in response to
        KEY_RESET_MODIFIERS wire messages. Per threat T-02-04 the client-
        provided reason is informational only — the action is the same
        regardless of trigger.
        """
        for vk in self._MAC_MODIFIER_KEYS:
            try:
                ev = CGEventCreateKeyboardEvent(None, vk, False)
                CGEventPost(kCGHIDEventTap, ev)
            except Exception as e:
                logger.debug("reset_modifiers: release vk=0x%x failed: %s", vk, e)
        logger.info("input.reset_modifiers (mac)")

    def text_commit(self, text: str) -> None:
        """Phase 2 D-15 — IME / dead-key commit passthrough on macOS.

        Posts a key-down + key-up event pair both carrying the Unicode
        commit string via ``CGEventKeyboardSetUnicodeString``. The
        virtual key code is 0 (kVK_ANSI_A as a stand-in) which most
        focused apps ignore in favor of the unicode payload — same
        pattern as Karabiner Elements / Hammerspoon.

        Empty strings are ignored so callers can pump unconditionally.
        """
        if not text:
            return
        try:
            for is_press in (True, False):
                ev = CGEventCreateKeyboardEvent(None, 0, is_press)
                CGEventKeyboardSetUnicodeString(ev, len(text), text)
                CGEventPost(kCGHIDEventTap, ev)
        except Exception as e:
            logger.debug("text_commit failed: %s", e)

    # ------------------------------------------------------------------
    # Pen / tablet (downgraded to mouse for Phase 1)
    # ------------------------------------------------------------------

    _pen_warned = False

    def pen_event(self, x: float, y: float, pressure: float,
                  tilt_x: float = 0.0, tilt_y: float = 0.0,
                  rotation: float = 0.0,
                  button: int = 0, pressed: bool = False,
                  hovering: bool = False, pen_type: str = "pen"):
        """Best-effort pen injection.

        Phase 2 INPUT-08 re-scope (D-07 FAIL branch):
        ``docs/release.md`` records the IOHIDUserDevice spike outcome as
        FAIL — Mac-server pen pressure is a v1 known-limitation. Full
        tablet pressure/tilt injection on macOS requires a signed
        IOHIDUserDevice helper (or a HIDDriverKit system extension);
        neither is in v1 scope. Until that lands, we downgrade to:
          - Pen hover (no pressure) → mouse move
          - Pen tip down → left-mouse-down+drag
          - Eraser tip down → right-mouse-down+drag
        Pressure and tilt are dropped on the floor. Client-side tools
        that require real pressure will fall back to their binary-mode
        brush behavior. **Flame artists should run on the Rocky Linux
        server** (production path per ``PROJECT.md``) for full pressure.
        """
        if not MacInputInjector._pen_warned and pressure > 0:
            logger.warning(
                "Pen pressure/tilt injection not implemented on macOS — "
                "pen events are being downgraded to mouse clicks "
                "(pressure dropped). See docs/release.md "
                "'Phase 2 IOHIDUserDevice spike outcome'."
            )
            MacInputInjector._pen_warned = True

        # Always update cursor position while the pen is in range.
        if hovering or pressed:
            self.move_abs(x, y)

        # Map tip state to a mouse button. Eraser → right button.
        is_eraser = pen_type == "eraser"
        mouse_button = 3 if is_eraser else 1
        was_down = (kCGMouseButtonRight if is_eraser else kCGMouseButtonLeft) \
            in self._buttons_down

        if pressed and not was_down:
            self.button(mouse_button, True)
        elif not pressed and was_down:
            self.button(mouse_button, False)

    # ------------------------------------------------------------------
    # session_manager dispatch
    # ------------------------------------------------------------------

    def handle_message(self, msg: dict):
        """Dispatch a parsed input message."""
        msg_type = msg.get("type")

        if msg_type == "mouse_move":
            self.move_abs(msg["x"], msg["y"])

        elif msg_type == "mouse_button":
            self.move_abs(msg["x"], msg["y"])
            self.button(msg["button"], msg["pressed"])

        elif msg_type == "mouse_scroll":
            self.move_abs(msg["x"], msg["y"])
            self.scroll(msg.get("dx", 0), msg.get("dy", 0))

        elif msg_type == "key_event":
            self.key_event(msg["scan_code"], msg["pressed"])

        elif msg_type == "pen_event":
            self.pen_event(
                x=msg["x"],
                y=msg["y"],
                pressure=msg.get("pressure", 0.0),
                tilt_x=msg.get("tilt_x", 0.0),
                tilt_y=msg.get("tilt_y", 0.0),
                rotation=msg.get("rotation", 0.0),
                button=msg.get("button", 0),
                pressed=msg.get("pressed", False),
                hovering=msg.get("hovering", False),
                pen_type=msg.get("pen_type", "pen"),
            )

    def close(self):
        # Release any stuck buttons so we don't leave the host in an
        # odd state (left-click held) if the session dies mid-drag.
        for btn in list(self._buttons_down):
            try:
                if btn == kCGMouseButtonLeft:
                    self.button(1, False)
                elif btn == kCGMouseButtonRight:
                    self.button(3, False)
                else:
                    self.button(2, False)
            except Exception:
                pass
        self._buttons_down.clear()
        logger.info("MacInputInjector shut down")
