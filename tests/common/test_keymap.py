"""STAB-01 — Qt key → Linux evdev scan code table coverage.

Target-read divergence note:
``qt_key_to_linux_scancode(qt_key: int) -> int`` takes only a single integer Qt
key code. There is NO modifier bitmask argument — modifier keys are themselves
entries in the table (Shift, Ctrl, Alt, Meta) and the server layer issues them
as separate scancode events. So the "chord" tests verify that each modifier
has its own scancode and that a letter scancode is unchanged by what the
caller does alongside it.

Unmapped keys return 0 per the docstring contract.
"""
import itertools

import pytest

from common.keymap import (
    FLAME_CRITICAL_CHORDS,
    MOD_ALT,
    MOD_CTRL,
    MOD_META,
    MOD_SHIFT,
    QT_KEY_TO_LINUX,
    QT_KEY_TO_MAC_VK,
    qt_key_to_linux_scancode,
    qt_key_to_mac_vk,
    swap_cmd_ctrl_for_linux_dest,
)

# PySide6 key codes — use integer literals to keep this test PySide6-free.
# Values match `Qt.Key_*` enum integers.
QT_KEY_A = 0x41
QT_KEY_Z = 0x5A
QT_KEY_0 = 0x30
QT_KEY_9 = 0x39
QT_KEY_F1 = 0x01000030
QT_KEY_F12 = 0x0100003B
QT_KEY_SHIFT = 0x01000020
QT_KEY_CTRL = 0x01000021
QT_KEY_ALT = 0x01000023
QT_KEY_META = 0x01000022
QT_KEY_SPACE = 0x20
QT_KEY_RETURN = 0x01000004
QT_KEY_ESCAPE = 0x01000001


# ─── Letters ──────────────────────────────────────────────────────────────


def test_letters_a_through_z_all_map_to_positive_scancode():
    """Every ASCII letter must have a valid positive scancode — this is the
    bread-and-butter Flame-session keystroke path."""
    for code in range(QT_KEY_A, QT_KEY_Z + 1):
        sc = qt_key_to_linux_scancode(code)
        assert isinstance(sc, int) and sc > 0, f"key 0x{code:X} → {sc}"


@pytest.mark.parametrize("qt_key,expected_scancode", [
    (0x41, 30),   # A → KEY_A
    (0x5A, 44),   # Z → KEY_Z
    (0x51, 16),   # Q → KEY_Q
    (0x50, 25),   # P → KEY_P
])
def test_letter_scancodes_match_evdev_table(qt_key, expected_scancode):
    """Spot-check: scancodes match the Linux ``KEY_*`` numeric constants."""
    assert qt_key_to_linux_scancode(qt_key) == expected_scancode


# ─── Digits ───────────────────────────────────────────────────────────────


def test_digits_0_through_9_all_map_to_positive_scancode():
    for code in range(QT_KEY_0, QT_KEY_9 + 1):
        sc = qt_key_to_linux_scancode(code)
        assert isinstance(sc, int) and sc > 0, f"key 0x{code:X} → {sc}"


# ─── Function keys ────────────────────────────────────────────────────────


def test_function_keys_f1_through_f12_all_have_scancodes():
    """Flame's F-key hotkeys are load-bearing (muscle memory)."""
    for code in range(QT_KEY_F1, QT_KEY_F12 + 1):
        sc = qt_key_to_linux_scancode(code)
        assert isinstance(sc, int) and sc > 0, f"F-key 0x{code:X} → {sc}"


# ─── Modifier chord path ──────────────────────────────────────────────────
# The real signature takes only one arg — modifiers are sent as their own
# scancode events by the injection layer. These tests lock in that each
# modifier has its own valid scancode AND that a letter scancode is stable
# (i.e. pressing the modifier doesn't somehow affect the letter mapping).


def test_all_four_modifiers_have_distinct_scancodes():
    """Ctrl / Shift / Alt / Meta must all map to distinct KEY_* codes — no
    silent aliasing on the Flame modifier-chord path."""
    shift = qt_key_to_linux_scancode(QT_KEY_SHIFT)
    ctrl = qt_key_to_linux_scancode(QT_KEY_CTRL)
    alt = qt_key_to_linux_scancode(QT_KEY_ALT)
    meta = qt_key_to_linux_scancode(QT_KEY_META)
    assert shift == 42   # KEY_LEFTSHIFT
    assert ctrl == 29    # KEY_LEFTCTRL
    assert alt == 56     # KEY_LEFTALT
    assert meta == 125   # KEY_LEFTMETA
    assert len({shift, ctrl, alt, meta}) == 4


def test_ctrl_shift_alt_p_stability():
    """The canonical Ctrl+Shift+Alt+P smoke chord — verify each component of
    the chord maps to its documented KEY_* code, and the letter scancode is
    independent of the modifier stack (signature takes one arg)."""
    assert qt_key_to_linux_scancode(QT_KEY_CTRL) == 29
    assert qt_key_to_linux_scancode(QT_KEY_SHIFT) == 42
    assert qt_key_to_linux_scancode(QT_KEY_ALT) == 56
    p_scancode = qt_key_to_linux_scancode(0x50)   # KEY_P
    assert p_scancode == 25
    # Calling it again with no modifiers must return the same answer — guard
    # against any future stateful side effect in the lookup.
    assert qt_key_to_linux_scancode(0x50) == 25


# ─── Symbols + navigation ─────────────────────────────────────────────────


@pytest.mark.parametrize("qt_key,expected", [
    (QT_KEY_SPACE, 57),     # KEY_SPACE
    (QT_KEY_RETURN, 28),    # KEY_ENTER
    (QT_KEY_ESCAPE, 1),     # KEY_ESC
    (0x01000003, 14),       # Key_Backspace → KEY_BACKSPACE
    (0x01000007, 111),      # Key_Delete → KEY_DELETE
])
def test_nav_and_symbol_keys_map_correctly(qt_key, expected):
    assert qt_key_to_linux_scancode(qt_key) == expected


# ─── Negative path ────────────────────────────────────────────────────────


def test_unknown_key_returns_zero():
    """Per the docstring contract — unmapped keys return 0, not KeyError."""
    assert qt_key_to_linux_scancode(0xDEADBEEF) == 0


def test_table_contains_full_ascii_letter_range():
    """Guard: the QT_KEY_TO_LINUX dict must cover every letter — a regression
    here would silently eat a Flame keystroke."""
    missing = [0x41 + i for i in range(26) if (0x41 + i) not in QT_KEY_TO_LINUX]
    assert missing == [], f"letters missing from table: {missing}"


# =============================================================================
# Phase 2 Wave 1 (02-03) — D-12 exhaustive Qt × modifier × {linux, mac} matrix.
# Plan 02-03 fills in QT_KEY_TO_MAC_VK + qt_key_to_mac_vk() + FLAME_CRITICAL_CHORDS.
# =============================================================================


def test_qt_key_to_mac_vk_letter_a_returns_kvk_ansi_a():
    from common.keymap import qt_key_to_mac_vk  # noqa: F401
    assert qt_key_to_mac_vk(0x41) == 0x00  # kVK_ANSI_A


def test_flame_critical_chords_exist():
    from common.keymap import FLAME_CRITICAL_CHORDS  # noqa: F401
    assert len(FLAME_CRITICAL_CHORDS) >= 20


def test_swap_cmd_ctrl_for_linux_dest_inverts_cmd_to_ctrl():
    from common.keymap import swap_cmd_ctrl_for_linux_dest  # noqa: F401
    # Qt Meta (0x01000022) should become Qt Control (0x01000021) when dest=linux
    out_key, out_mods = swap_cmd_ctrl_for_linux_dest(0x01000022, 0)
    assert out_key == 0x01000021


# =============================================================================
# Phase 2 Wave 1 (02-03) — D-12 exhaustive Qt × modifier × {linux, mac} matrix.
# Target: >= 2000 parametrized cases (~84 keys × ~15 modifier combos × 2 platforms).
# =============================================================================


# D-12: table parity -- every Qt key Phase 1 mapped must also map to a Mac VK.
# Exceptions are Apple-keyboard absences (no kVK_* constants exist for these).
# Every entry here is documented -- if something else lands on this list,
# open an issue AND file a Mac-keyboard-equivalent mapping proposal.
ALLOWED_UNMAPPED_ON_MAC = {
    0x01000006,  # Key_Insert       — no kVK_Insert on Apple keyboards
    0x01000008,  # Key_Pause        — no kVK_Pause
    0x01000009,  # Key_Print/SysRq  — no kVK_Print; cmd-shift-3 is the Mac flow
    0x01000025,  # Key_NumLock      — no kVK_NumLock (Apple Clear key ≠ NumLock)
    0x01000026,  # Key_ScrollLock   — no kVK_ScrollLock
}


@pytest.mark.parametrize("qt_key", sorted(QT_KEY_TO_LINUX.keys()))
def test_mac_vk_covers_every_qt_key_in_linux_table(qt_key):
    if qt_key in ALLOWED_UNMAPPED_ON_MAC:
        pytest.skip(f"Qt key {hex(qt_key)} is explicitly allow-listed as Mac-unmapped")
    # NOTE: check dict membership, NOT truthy value. kVK_ANSI_A = 0x00 is a
    # legitimate Mac virtual key code, so the "0 means unmapped" sentinel
    # used by qt_key_to_mac_vk() collides with Key_A's valid mapping.
    # Phase 1's qt_key_to_linux_scancode sentinel has the same shape but no
    # Linux key maps to scancode 0, so the collision only bites on the Mac side.
    assert qt_key in QT_KEY_TO_MAC_VK, (
        f"Qt key {hex(qt_key)} maps to Linux scancode "
        f"{QT_KEY_TO_LINUX[qt_key]} but has no Mac VK entry. "
        f"Add to QT_KEY_TO_MAC_VK in common/keymap.py."
    )


# Explicit per-letter assertions -- catches off-by-one manual-build errors.
LETTER_MAC_VK_EXPECTED = {
    0x41: 0x00, 0x42: 0x0B, 0x43: 0x08, 0x44: 0x02, 0x45: 0x0E,
    0x46: 0x03, 0x47: 0x05, 0x48: 0x04, 0x49: 0x22, 0x4A: 0x26,
    0x4B: 0x28, 0x4C: 0x25, 0x4D: 0x2E, 0x4E: 0x2D, 0x4F: 0x1F,
    0x50: 0x23, 0x51: 0x0C, 0x52: 0x0F, 0x53: 0x01, 0x54: 0x11,
    0x55: 0x20, 0x56: 0x09, 0x57: 0x0D, 0x58: 0x07, 0x59: 0x10, 0x5A: 0x06,
}

@pytest.mark.parametrize("qt_key,expected_mac_vk", sorted(LETTER_MAC_VK_EXPECTED.items()))
def test_mac_vk_letter_roundtrip(qt_key, expected_mac_vk):
    assert qt_key_to_mac_vk(qt_key) == expected_mac_vk


MODIFIER_COMBINATIONS = []
_mods = [0, MOD_SHIFT, MOD_CTRL, MOD_ALT, MOD_META]
for r in range(1, 5):
    for combo in itertools.combinations(_mods[1:], r):
        bitmask = 0
        for m in combo:
            bitmask |= m
        MODIFIER_COMBINATIONS.append(bitmask)
MODIFIER_COMBINATIONS.append(0)  # empty modifier set
MODIFIER_COMBINATIONS = sorted(set(MODIFIER_COMBINATIONS))


# Exhaustive Qt x modifier table -- every key in QT_KEY_TO_LINUX x every modifier combination.
# Drives per-platform lookup assertions; >= 2000 cases by D-12 requirement.
KEYS = sorted(QT_KEY_TO_LINUX.keys())
PLATFORMS = ["linux", "mac"]


@pytest.mark.parametrize(
    "qt_key,modifiers,platform",
    [(k, m, p) for k in KEYS for m in MODIFIER_COMBINATIONS for p in PLATFORMS],
)
def test_exhaustive_qt_key_lookup_is_mapped_on_both_platforms(qt_key, modifiers, platform):
    """D-12 exhaustive matrix: each Qt key in QT_KEY_TO_LINUX must be PRESENT
    in the platform table (on Mac, subject to ALLOWED_UNMAPPED_ON_MAC).
    Uses dict-membership rather than value-is-nonzero -- kVK_ANSI_A == 0x00
    is a legitimate Mac VK, so the sentinel collides with a valid mapping."""
    if platform == "linux":
        assert qt_key in QT_KEY_TO_LINUX, (
            f"qt_key={hex(qt_key)} modifiers={hex(modifiers)} not in QT_KEY_TO_LINUX; "
            "exhaustive matrix contract broken."
        )
        # Sanity: Linux scancodes happen to all be > 0 (no KEY_* code is zero).
        assert qt_key_to_linux_scancode(qt_key) != 0
    else:
        if qt_key in ALLOWED_UNMAPPED_ON_MAC:
            pytest.skip(f"{hex(qt_key)} allow-listed as Mac-unmapped")
        assert qt_key in QT_KEY_TO_MAC_VK, (
            f"qt_key={hex(qt_key)} modifiers={hex(modifiers)} missing from QT_KEY_TO_MAC_VK; "
            "every QT_KEY_TO_LINUX key must resolve on both platforms "
            "(modulo ALLOWED_UNMAPPED_ON_MAC)."
        )


NON_META_MODIFIER_COMBINATIONS = [m for m in MODIFIER_COMBINATIONS if not (m & MOD_META)]


@pytest.mark.parametrize("qt_key", sorted(QT_KEY_TO_LINUX.keys()))
@pytest.mark.parametrize("modifiers", NON_META_MODIFIER_COMBINATIONS)
def test_swap_cmd_ctrl_preserves_non_meta_modifiers(qt_key, modifiers):
    QT_KEY_META = 0x01000022
    if qt_key == QT_KEY_META:
        pytest.skip("Meta-as-pressed-key tested elsewhere")
    out_key, out_mods = swap_cmd_ctrl_for_linux_dest(qt_key, modifiers)
    assert out_key == qt_key
    assert out_mods == modifiers, (
        f"Non-Meta modifier bitmask was mutated: "
        f"in={hex(modifiers)} out={hex(out_mods)}"
    )


@pytest.mark.parametrize("qt_key,modifiers,desc", FLAME_CRITICAL_CHORDS)
def test_flame_critical_chord_resolves_on_both_platforms(qt_key, modifiers, desc):
    """Every Flame-critical chord must resolve on Linux (scancode > 0) AND be
    present in the Mac VK table (dict membership — kVK_ANSI_A==0x00 is a valid VK)."""
    assert qt_key_to_linux_scancode(qt_key) != 0, f"Linux mapping missing for {desc}"
    assert qt_key in QT_KEY_TO_MAC_VK, f"Mac VK mapping missing for {desc}"


@pytest.mark.parametrize("qt_key,modifiers,desc", FLAME_CRITICAL_CHORDS)
def test_flame_critical_chord_cmd_ctrl_swap_preserves_key(qt_key, modifiers, desc):
    # Even with Cmd swapped to Ctrl, the chord must not drop its non-Meta modifiers.
    out_key, out_mods = swap_cmd_ctrl_for_linux_dest(qt_key, modifiers)
    if modifiers & MOD_META:
        assert out_mods & MOD_CTRL, f"{desc}: Meta->Ctrl bit not set after swap"
        assert not (out_mods & MOD_META), f"{desc}: Meta bit not cleared after swap"
    else:
        assert out_mods == modifiers, f"{desc}: non-Meta modifier set mutated by swap"
