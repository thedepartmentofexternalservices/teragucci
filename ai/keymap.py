"""
Qt key code mapping for the Teraguchi AI client.

The server's input injector expects Qt key codes in the `scan_code` field
of key_event messages (same as what the GUI client sends from QKeyEvent).
"""

# Qt modifier bitmasks (as used by the existing GUI client)
QT_MOD_SHIFT   = 0x02000000
QT_MOD_CTRL    = 0x04000000
QT_MOD_ALT     = 0x08000000
QT_MOD_META    = 0x10000000

# Qt special key codes (Qt::Key enum values)
QT_KEY = {
    # Whitespace / editing
    "backspace":    0x01000003,
    "tab":          0x01000001,
    "return":       0x01000004,
    "enter":        0x01000004,
    "escape":       0x01000000,
    "esc":          0x01000000,
    "delete":       0x01000007,
    "del":          0x01000007,
    "insert":       0x01000006,

    # Navigation
    "left":         0x01000012,
    "right":        0x01000014,
    "up":           0x01000013,
    "down":         0x01000015,
    "home":         0x01000010,
    "end":          0x01000011,
    "pageup":       0x01000016,
    "pgup":         0x01000016,
    "pagedown":     0x01000017,
    "pgdown":       0x01000017,

    # Modifier keys (as independent key presses)
    "shift":        0x01000020,
    "ctrl":         0x01000021,
    "control":      0x01000021,
    "alt":          0x01000023,
    "meta":         0x01000022,
    "super":        0x01000022,
    "win":          0x01000022,

    # Function keys
    "f1":           0x01000030,
    "f2":           0x01000031,
    "f3":           0x01000032,
    "f4":           0x01000033,
    "f5":           0x01000034,
    "f6":           0x01000035,
    "f7":           0x01000036,
    "f8":           0x01000037,
    "f9":           0x01000038,
    "f10":          0x01000039,
    "f11":          0x0100003a,
    "f12":          0x0100003b,

    # Misc
    "space":        0x00000020,
    "capslock":     0x01000024,
    "numlock":      0x01000025,
    "scrolllock":   0x01000026,
    "printscreen":  0x01000027,
    "pause":        0x01000008,

    # Punctuation that has shifted variants - treated as literal characters
    # (char_to_qt_key handles shift automatically for these)
}

# Characters that require Shift on a US keyboard layout
_SHIFT_CHARS = set('~!@#$%^&*()_+{}|:"<>?ABCDEFGHIJKLMNOPQRSTUVWXYZ')

# Shifted character → base character mapping
_SHIFT_MAP = {
    '~': '`', '!': '1', '@': '2', '#': '3', '$': '4', '%': '5',
    '^': '6', '&': '7', '*': '8', '(': '9', ')': '0', '_': '-',
    '+': '=', '{': '[', '}': ']', '|': '\\', ':': ';', '"': "'",
    '<': ',', '>': '.', '?': '/',
}


def char_to_qt_key(ch: str) -> tuple:
    """
    Convert a single character to (qt_key_code, needs_shift).

    Returns (None, False) for characters that cannot be typed.
    """
    if ch in _SHIFT_MAP:
        base = _SHIFT_MAP[ch]
        return ord(base.upper()), True
    if ch.isupper():
        return ord(ch), True
    if ch.isalpha():
        return ord(ch.upper()), False
    if ch.isdigit() or ch in '`-=[]\\;\',./':
        return ord(ch), False
    if ch == ' ':
        return 0x20, False
    if ch == '\n' or ch == '\r':
        return 0x01000004, False  # Return
    if ch == '\t':
        return 0x01000001, False  # Tab
    if ch == '\x1b':
        return 0x01000000, False  # Escape
    if ch == '\x08':
        return 0x01000003, False  # Backspace
    return None, False


def parse_combo(combo: str) -> tuple:
    """
    Parse a key combination string into (modifier_qt_keys, main_qt_key).

    Examples:
        "ctrl+c"    → ([0x01000021], ord('C'))
        "alt+F4"    → ([0x01000023], 0x01000033)
        "ctrl+shift+t" → ([0x01000021, 0x01000020], ord('T'))
        "enter"     → ([], 0x01000004)
        "escape"    → ([], 0x01000000)
    """
    parts = [p.strip().lower() for p in combo.split("+")]
    modifier_names = {"ctrl", "control", "shift", "alt", "meta", "super", "win"}

    modifiers = []
    main_name = None

    for part in parts:
        if part in modifier_names:
            modifiers.append(QT_KEY[part])
        else:
            main_name = part

    if main_name is None:
        raise ValueError(f"No main key found in combo: {combo!r}")

    # Main key: check special key table first, then treat as character
    if main_name in QT_KEY:
        main_key = QT_KEY[main_name]
    elif len(main_name) == 1:
        qt_key, needs_shift = char_to_qt_key(main_name)
        if qt_key is None:
            raise ValueError(f"Unknown key: {main_name!r}")
        if needs_shift and QT_KEY["shift"] not in modifiers:
            modifiers.append(QT_KEY["shift"])
        main_key = qt_key
    else:
        raise ValueError(f"Unknown key name: {main_name!r}")

    return modifiers, main_key
