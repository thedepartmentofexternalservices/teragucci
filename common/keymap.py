"""
Key mapping from Qt key names to Linux scan codes.

Qt sends key names (from QKeyEvent.key()), and we need to convert
them to Linux evdev scan codes for uinput injection.
"""

# Mapping from Qt key enum values to Linux evdev scan codes
# Qt key values -> Linux KEY_* codes (from linux/input-event-codes.h)
QT_KEY_TO_LINUX = {
    # Letters
    0x41: 30,   # Key_A -> KEY_A
    0x42: 48,   # Key_B -> KEY_B
    0x43: 46,   # Key_C -> KEY_C
    0x44: 32,   # Key_D -> KEY_D
    0x45: 18,   # Key_E -> KEY_E
    0x46: 33,   # Key_F -> KEY_F
    0x47: 34,   # Key_G -> KEY_G
    0x48: 35,   # Key_H -> KEY_H
    0x49: 23,   # Key_I -> KEY_I
    0x4A: 36,   # Key_J -> KEY_J
    0x4B: 37,   # Key_K -> KEY_K
    0x4C: 38,   # Key_L -> KEY_L
    0x4D: 50,   # Key_M -> KEY_M
    0x4E: 49,   # Key_N -> KEY_N
    0x4F: 24,   # Key_O -> KEY_O
    0x50: 25,   # Key_P -> KEY_P
    0x51: 16,   # Key_Q -> KEY_Q
    0x52: 19,   # Key_R -> KEY_R
    0x53: 31,   # Key_S -> KEY_S
    0x54: 20,   # Key_T -> KEY_T
    0x55: 22,   # Key_U -> KEY_U
    0x56: 47,   # Key_V -> KEY_V
    0x57: 17,   # Key_W -> KEY_W
    0x58: 45,   # Key_X -> KEY_X
    0x59: 21,   # Key_Y -> KEY_Y
    0x5A: 44,   # Key_Z -> KEY_Z

    # Numbers
    0x30: 11,   # Key_0 -> KEY_0
    0x31: 2,    # Key_1 -> KEY_1
    0x32: 3,    # Key_2 -> KEY_2
    0x33: 4,    # Key_3 -> KEY_3
    0x34: 5,    # Key_4 -> KEY_4
    0x35: 6,    # Key_5 -> KEY_5
    0x36: 7,    # Key_6 -> KEY_6
    0x37: 8,    # Key_7 -> KEY_7
    0x38: 9,    # Key_8 -> KEY_8
    0x39: 10,   # Key_9 -> KEY_9

    # Function keys
    0x01000030: 59,   # Key_F1
    0x01000031: 60,   # Key_F2
    0x01000032: 61,   # Key_F3
    0x01000033: 62,   # Key_F4
    0x01000034: 63,   # Key_F5
    0x01000035: 64,   # Key_F6
    0x01000036: 65,   # Key_F7
    0x01000037: 66,   # Key_F8
    0x01000038: 67,   # Key_F9
    0x01000039: 68,   # Key_F10
    0x0100003a: 87,   # Key_F11
    0x0100003b: 88,   # Key_F12

    # Modifiers
    0x01000020: 42,   # Key_Shift -> KEY_LEFTSHIFT
    0x01000021: 29,   # Key_Control -> KEY_LEFTCTRL
    0x01000023: 56,   # Key_Alt -> KEY_LEFTALT
    0x01000022: 125,  # Key_Meta -> KEY_LEFTMETA (Super/Windows)

    # Navigation
    0x01000013: 103,  # Key_Up -> KEY_UP
    0x01000015: 108,  # Key_Down -> KEY_DOWN
    0x01000012: 105,  # Key_Left -> KEY_LEFT
    0x01000014: 106,  # Key_Right -> KEY_RIGHT
    0x01000010: 102,  # Key_Home -> KEY_HOME
    0x01000011: 107,  # Key_End -> KEY_END
    0x01000016: 104,  # Key_PageUp -> KEY_PAGEUP
    0x01000017: 109,  # Key_PageDown -> KEY_PAGEDOWN

    # Editing
    0x01000003: 14,   # Key_Backspace -> KEY_BACKSPACE
    0x01000007: 111,  # Key_Delete -> KEY_DELETE
    0x01000004: 28,   # Key_Return -> KEY_ENTER
    0x01000005: 28,   # Key_Enter -> KEY_ENTER
    0x01000001: 1,    # Key_Escape -> KEY_ESC
    0x01000000: 15,   # Key_Tab -> KEY_TAB
    0x01000006: 110,  # Key_Insert -> KEY_INSERT

    # Symbols
    0x20: 57,         # Key_Space -> KEY_SPACE
    0x2D: 12,         # Key_Minus -> KEY_MINUS
    0x3D: 13,         # Key_Equal -> KEY_EQUAL
    0x5B: 26,         # Key_BracketLeft -> KEY_LEFTBRACE
    0x5D: 27,         # Key_BracketRight -> KEY_RIGHTBRACE
    0x5C: 43,         # Key_Backslash -> KEY_BACKSLASH
    0x3B: 39,         # Key_Semicolon -> KEY_SEMICOLON
    0x27: 40,         # Key_Apostrophe -> KEY_APOSTROPHE
    0x60: 41,         # Key_QuoteLeft (backtick) -> KEY_GRAVE
    0x2C: 51,         # Key_Comma -> KEY_COMMA
    0x2E: 52,         # Key_Period -> KEY_DOT
    0x2F: 53,         # Key_Slash -> KEY_SLASH

    # Lock keys
    0x01000024: 58,   # Key_CapsLock -> KEY_CAPSLOCK
    0x01000025: 69,   # Key_NumLock -> KEY_NUMLOCK
    0x01000026: 70,   # Key_ScrollLock -> KEY_SCROLLLOCK

    # Print/Pause
    0x01000009: 99,   # Key_Print -> KEY_SYSRQ
    0x01000008: 119,  # Key_Pause -> KEY_PAUSE
}


def qt_key_to_linux_scancode(qt_key: int) -> int:
    """Convert a Qt key enum value to a Linux evdev scan code.

    Returns 0 if no mapping exists.
    """
    return QT_KEY_TO_LINUX.get(qt_key, 0)


# =====================================================================
# Phase 2 D-12: Qt key -> Mac virtual key code (kVK_*)
# Source: Apple Carbon/HIToolbox/Events.h
# Returns 0 for unmapped (same sentinel as qt_key_to_linux_scancode).
# =====================================================================
QT_KEY_TO_MAC_VK = {
    # Letters
    0x41: 0x00, 0x42: 0x0B, 0x43: 0x08, 0x44: 0x02, 0x45: 0x0E,
    0x46: 0x03, 0x47: 0x05, 0x48: 0x04, 0x49: 0x22, 0x4A: 0x26,
    0x4B: 0x28, 0x4C: 0x25, 0x4D: 0x2E, 0x4E: 0x2D, 0x4F: 0x1F,
    0x50: 0x23, 0x51: 0x0C, 0x52: 0x0F, 0x53: 0x01, 0x54: 0x11,
    0x55: 0x20, 0x56: 0x09, 0x57: 0x0D, 0x58: 0x07, 0x59: 0x10, 0x5A: 0x06,
    # Digits 0-9
    0x30: 0x1D, 0x31: 0x12, 0x32: 0x13, 0x33: 0x14, 0x34: 0x15,
    0x35: 0x17, 0x36: 0x16, 0x37: 0x1A, 0x38: 0x1C, 0x39: 0x19,
    # Function keys F1-F12
    0x01000030: 0x7A, 0x01000031: 0x78, 0x01000032: 0x63, 0x01000033: 0x76,
    0x01000034: 0x60, 0x01000035: 0x61, 0x01000036: 0x62, 0x01000037: 0x64,
    0x01000038: 0x65, 0x01000039: 0x6D, 0x0100003A: 0x67, 0x0100003B: 0x6F,
    # Modifiers  (Qt Meta == Mac Cmd; Qt Alt == Mac Option)
    0x01000020: 0x38,  # Key_Shift   -> kVK_Shift
    0x01000021: 0x3B,  # Key_Control -> kVK_Control
    0x01000022: 0x37,  # Key_Meta    -> kVK_Command
    0x01000023: 0x3A,  # Key_Alt     -> kVK_Option
    # Navigation / editing
    0x01000000: 0x30,  # Key_Tab      -> kVK_Tab  (Qt uses 0x01000000 for Tab; see QT_KEY_TO_LINUX)
    0x01000001: 0x35,  # Key_Escape   -> kVK_Escape
    0x01000003: 0x33,  # Key_Backspace -> kVK_Delete
    0x01000004: 0x24,  # Key_Return   -> kVK_Return
    0x01000005: 0x24,  # Key_Enter    -> kVK_Return
    0x01000007: 0x75,  # Key_Delete   -> kVK_ForwardDelete
    0x01000010: 0x73,  # Key_Home
    0x01000011: 0x77,  # Key_End
    0x01000012: 0x7B,  # Key_Left
    0x01000013: 0x7E,  # Key_Up
    0x01000014: 0x7C,  # Key_Right
    0x01000015: 0x7D,  # Key_Down
    0x01000016: 0x74,  # Key_PageUp
    0x01000017: 0x79,  # Key_PageDown
    # Punctuation
    0x20: 0x31,  # Space
    0x27: 0x27,  # Apostrophe -> kVK_ANSI_Quote
    0x2C: 0x2B,  # Comma
    0x2D: 0x1B,  # Minus
    0x2E: 0x2F,  # Period
    0x2F: 0x2C,  # Slash
    0x3B: 0x29,  # Semicolon
    0x3D: 0x18,  # Equal
    0x5B: 0x21,  # BracketLeft
    0x5C: 0x2A,  # Backslash
    0x5D: 0x1E,  # BracketRight
    0x60: 0x32,  # QuoteLeft (backtick) -> kVK_ANSI_Grave
    # Lock keys
    0x01000024: 0x39,  # CapsLock
}


def qt_key_to_mac_vk(qt_key: int) -> int:
    """Qt key enum value -> Mac virtual key code (kVK_*). Returns 0 for unmapped."""
    return QT_KEY_TO_MAC_VK.get(qt_key, 0)


# Qt::KeyboardModifier bitmask values (PySide6 parity)
MOD_SHIFT = 0x02000000
MOD_CTRL  = 0x04000000
MOD_ALT   = 0x08000000
MOD_META  = 0x10000000


# =====================================================================
# Wire-format modifier bit positions used by client/viewer.py's
# _qt_modifiers_to_int and consumed by the server's input injectors.
# These are NOT the Qt enum values above -- they are the compact
# bit positions transmitted on the wire (KeyEventMsg.modifiers).
#
# Phase 2 WR-08: previously these were magic numbers buried in
# _qt_modifiers_to_int and a `modifiers & 2` paste-detection check in
# keyPressEvent. Hoisting them here so any future modifier-bit
# reshuffle (D-14 already uses 0x10 for keypad) only changes one place
# and the dependent call sites stay in sync.
# =====================================================================
MODIFIER_BIT_SHIFT  = 0x01
MODIFIER_BIT_CTRL   = 0x02
MODIFIER_BIT_ALT    = 0x04
MODIFIER_BIT_META   = 0x08
MODIFIER_BIT_KEYPAD = 0x10


def swap_cmd_ctrl_for_linux_dest(qt_key: int, qt_modifiers: int) -> tuple[int, int]:
    """Mac-client -> Linux-server Cmd<->Ctrl translation (D-10 default).

    Pure function. Caller decides when to invoke (per-bookmark swap_cmd_ctrl flag
    AND destination is a Linux server).
      - Key_Meta as the pressed key becomes Key_Control
      - MOD_META bit in modifiers becomes MOD_CTRL
      - Ctrl + non-Meta keys pass through
    Returns (translated_qt_key, translated_qt_modifiers).
    """
    QT_KEY_META = 0x01000022
    QT_KEY_CTRL = 0x01000021
    out_key = QT_KEY_CTRL if qt_key == QT_KEY_META else qt_key
    out_mods = qt_modifiers
    if out_mods & MOD_META:
        out_mods = (out_mods & ~MOD_META) | MOD_CTRL
    return out_key, out_mods


# =====================================================================
# Phase 2 D-12: Flame-critical hotkey chord named subset.
# Shape: (qt_key, qt_modifier_bitmask, human_description).
# Downstream: INPUT-04 integration test (tests/integration/test_crazy_hotkeys.py)
# iterates this list to prove every Flame-muscle-memory chord round-trips.
# Do not reorder -- chord indices may be referenced in release notes.
# =====================================================================
FLAME_CRITICAL_CHORDS = [
    (0x20, MOD_SHIFT,                        "Shift+Space -- Flame pan"),
    (0x20, MOD_ALT,                          "Alt+Space -- Flame zoom"),
    (0x5B, 0,                                "[ -- decrease brush"),
    (0x5D, 0,                                "] -- increase brush"),
    (0x57, 0,                                "W -- translate gizmo"),
    (0x45, 0,                                "E -- rotate gizmo"),
    (0x52, 0,                                "R -- scale gizmo"),
    (0x01000012, 0,                          "Left -- prev frame"),
    (0x01000014, 0,                          "Right -- next frame"),
    (0x01000012, MOD_SHIFT,                  "Shift+Left -- prev keyframe"),
    (0x01000014, MOD_SHIFT,                  "Shift+Right -- next keyframe"),
    (0x20, 0,                                "Space -- play/pause"),
    (0x4C, 0,                                "L -- loop"),
    (0x53, MOD_CTRL,                         "Ctrl+S -- save"),
    (0x5A, MOD_CTRL,                         "Ctrl+Z -- undo"),
    (0x5A, MOD_CTRL | MOD_SHIFT,             "Ctrl+Shift+Z -- redo"),
    (0x50, MOD_CTRL | MOD_SHIFT | MOD_ALT,   "Ctrl+Shift+Alt+P -- Flame paint brush"),
    (0x4B, MOD_CTRL | MOD_SHIFT | MOD_ALT,   "Ctrl+Shift+Alt+K -- Flame keyer toggle"),
    (0x01000038, 0,                          "F9 -- release all modifiers (client panic)"),
    (0x01000030, MOD_CTRL,                   "Ctrl+F1 -- context-help"),
    (0x5D, MOD_CTRL,                         "Ctrl+] -- brightness up"),
    (0x5B, MOD_CTRL,                         "Ctrl+[ -- brightness down"),
]
