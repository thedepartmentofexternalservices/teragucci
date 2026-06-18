"""Phase 3 DISP-04 — Flame-approved CustomEDID profile emission.

Plan 03-03 Task 2. server.session_manager._generate_edid emits a
Flame-approved monitor-profile name ("Eizo CG279X", 11 ASCII chars,
fits the 13-byte EDID descriptor #2 at offset 72+5..72+18) so Flame's
monitor-config dialog accepts the Xvfb / GPU display profile without
warning. Manufacturer ID block also flips to Eizo's PnP code "ENC".

Tests parse the emitted EDID binary back and assert the monitor-name
descriptor matches the expected profile; also verify the PCoIP fallback
path in find_edid_file stays unchanged.
"""
import os
import tempfile
from pathlib import Path

import pytest


def test_generate_edid_emits_flame_approved_name(tmp_path):
    """DISP-04 — the monitor-name descriptor in the generated EDID
    reports 'Eizo CG279X' (industry-standard grading monitor)."""
    from server.session_manager import _generate_edid

    out = tmp_path / "teraguchi.edid.bin"
    _generate_edid(str(out), 1920, 1200)

    data = out.read_bytes()
    # EDID 1.3 descriptor #2 starts at byte 72; the monitor-name header
    # is 5 bytes (00 00 00 FC 00), then 13 bytes of ASCII padded with 0x0a.
    name_bytes = data[77:90]
    # Accept either the full name or the 13-byte padding.
    assert b"Eizo CG279X" in name_bytes, (
        f"expected 'Eizo CG279X' in EDID name descriptor, got {name_bytes!r}"
    )


def test_generate_edid_manufacturer_id_matches_eizo(tmp_path):
    """DISP-04 — manufacturer ID block matches Eizo's PnP code 'ENC'.

    EDID manufacturer ID is 5-bits-per-char packed into 2 bytes
    (big-endian), letters 1-of-26 with A=1, B=2, ..., Z=26.
    'E' = 5, 'N' = 14, 'C' = 3.
    First 2 bytes: (((5 << 10) | (14 << 5) | 3) & 0xFFFF) = 0x16, 0x83.
    Alternative packing 0x15, 0xC3 is also valid (some tools use
    0-based encoding: A=0, B=1, ..., Z=25). Accept either convention.
    """
    from server.session_manager import _generate_edid

    out = tmp_path / "teraguchi.edid.bin"
    _generate_edid(str(out), 1920, 1200)

    data = out.read_bytes()
    # Manufacturer ID at bytes 8-9.
    mfg_high = data[8]
    mfg_low = data[9]

    # EDID packs as: bit 15 = 0, bits 14-10 = letter1,
    # bits 9-5 = letter2, bits 4-0 = letter3; stored as (letter - 1)
    # per the existing session_manager._generate_edid comment at line
    # 152 ("T=20, G=7, C=3 -> ((20-1)<<10) | ((7-1)<<5) | (3-1)").
    packed = (mfg_high << 8) | mfg_low
    letter1 = ((packed >> 10) & 0x1F) + 1
    letter2 = ((packed >> 5) & 0x1F) + 1
    letter3 = (packed & 0x1F) + 1
    decoded = "".join(chr(ord("A") + (n - 1)) for n in (letter1, letter2, letter3))
    assert decoded == "ENC", (
        f"expected ENC in decoded mfg id, got {decoded!r} "
        f"from bytes {mfg_high:#04x} {mfg_low:#04x}"
    )


def test_generate_edid_checksum_is_valid(tmp_path):
    """DISP-04 — generated EDID is a valid 128-byte block (checksum sums
    to zero mod 256). If the name/manufacturer bytes change, the
    existing checksum logic at line 258 MUST still produce a well-
    formed block or Flame + Xorg will reject the CustomEDID."""
    from server.session_manager import _generate_edid

    out = tmp_path / "teraguchi.edid.bin"
    _generate_edid(str(out), 1920, 1200)

    data = out.read_bytes()
    assert len(data) == 128, "EDID 1.3 block must be exactly 128 bytes"
    assert sum(data) % 256 == 0, (
        f"EDID checksum invalid: sum % 256 = {sum(data) % 256}"
    )


def test_find_edid_file_pcoip_fallback_preserved(monkeypatch):
    """DISP-04 — PCoIP fallback path unchanged: if PCoIP's bundled EDID
    is present, find_edid_file returns it without generating.

    Preserves the Phase 1 behavior so users with PCoIP installed
    continue to get its Flame-tested EDID.
    """
    from server import session_manager

    def _fake_exists(path):
        return path == "/usr/share/pcoip-agent/1024x768.bin"

    monkeypatch.setattr(os.path, "exists", _fake_exists)
    result = session_manager.find_edid_file()
    assert result == "/usr/share/pcoip-agent/1024x768.bin"


def test_find_edid_file_falls_back_to_our_edid(monkeypatch, tmp_path):
    """DISP-04 — without PCoIP, find_edid_file prefers our own shipped
    edid/1920x1200.bin before generating."""
    from server import session_manager

    our_edid_path = (
        Path(session_manager.__file__).parent / "edid" / "1920x1200.bin"
    )

    def _fake_exists(path):
        # PCoIP missing; our bundled EDID present.
        if path == "/usr/share/pcoip-agent/1024x768.bin":
            return False
        if str(path) == str(our_edid_path):
            return True
        return False

    monkeypatch.setattr(os.path, "exists", _fake_exists)
    result = session_manager.find_edid_file()
    assert str(result) == str(our_edid_path)
