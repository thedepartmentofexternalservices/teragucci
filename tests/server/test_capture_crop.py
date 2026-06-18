"""Phase 3 D-02 — server-side BGRA crop for per-session capture-mode.

Plan 03 Task 1 implementation tests. The 03-03 plan ships `capture_raw_bgra_with_crop`
on `server.screen_capture.ScreenCapture` plus a NumPy-based BGRA crop path.
P010 raw-crop seam is DEFERRED to Phase 3.5 — v1 ships BGRA-only crop and
preserves 10-bit fidelity via the encoder's existing BGRA→P010 pipeline.
"""
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture
def mock_capture(monkeypatch):
    """Build a ScreenCapture instance without touching mss / NvFBC / xrandr.

    Patches the expensive init paths (mss.mss(), NvFBC detection, xrandr
    query) so the constructor runs in milliseconds and produces a capture
    object with a deterministic width/height we can drive the crop math
    against.
    """
    # Patch mss.mss() to return a fake object with a monitor list matching
    # the DXS flame-lab 2×2560×1600 topology (5120×1600 virtual desktop).
    fake_monitors = [
        {"left": 0, "top": 0, "width": 5120, "height": 1600},
        {"left": 0, "top": 0, "width": 2560, "height": 1600},
        {"left": 2560, "top": 0, "width": 2560, "height": 1600},
    ]

    class _FakeSct:
        def __init__(self):
            self.monitors = fake_monitors

        def close(self):
            pass

        def grab(self, monitor):
            raise AssertionError("real grab should not be called in unit tests")

    import mss
    monkeypatch.setattr(mss, "mss", lambda: _FakeSct())

    import server.screen_capture as sc_mod
    monkeypatch.setattr(sc_mod, "detect_nvfbc", lambda: False)
    monkeypatch.setattr(sc_mod, "detect_monitors_xrandr", lambda: [])

    # Force the NvFBC branch off so __init__ takes the mss fallback path.
    monkeypatch.setattr(sc_mod, "_HAS_NVFBC_BACKEND", False)
    monkeypatch.setattr(sc_mod, "_HAS_XLIB_DAMAGE", False)

    cap = sc_mod.ScreenCapture(monitor_index=0)
    # Pin width/height to the virtual-desktop size so the crop math is
    # deterministic.
    cap.width = 5120
    cap.height = 1600
    return cap


def _fake_full_frame(cap, fill_value: int = 0x42) -> bytes:
    """Return a BGRA buffer of the right size, filled with a known byte."""
    return bytes([fill_value]) * (cap.width * cap.height * 4)


def test_crop_none_is_byte_equal_to_full_capture(mock_capture, monkeypatch):
    """D-02 — crop=None → mirror_all: pass through identical bytes.

    This is the Phase 2 9-checkpoint 10-bit fixture anti-regression: the
    mirror_all path MUST be byte-equal to capture_raw_bgra() so the
    Phase 2 ten_bit_smoke fixture keeps passing.
    """
    fake = _fake_full_frame(mock_capture)
    monkeypatch.setattr(mock_capture, "capture_raw_bgra", lambda: fake)

    out = mock_capture.capture_raw_bgra_with_crop(crop=None)
    assert out is fake or out == fake, "mirror_all path must be byte-equal"


def test_crop_returns_expected_dimensions(mock_capture, monkeypatch):
    """D-02 — crop=(0,0,640,480) returns exactly 640*480*4 = 1228800 bytes."""
    # Build a frame where each pixel has a unique RGBA fingerprint so we
    # can verify the crop picked the right region.
    arr = np.arange(
        mock_capture.width * mock_capture.height * 4, dtype=np.uint8,
    ).reshape(mock_capture.height, mock_capture.width, 4)
    fake = arr.tobytes()
    monkeypatch.setattr(mock_capture, "capture_raw_bgra", lambda: fake)

    cropped = mock_capture.capture_raw_bgra_with_crop(crop=(0, 0, 640, 480))
    assert len(cropped) == 640 * 480 * 4

    # Verify the bytes match a manual NumPy crop.
    expected = arr[0:480, 0:640].tobytes()
    assert cropped == expected


def test_crop_at_offset_picks_correct_monitor_region(mock_capture, monkeypatch):
    """D-02 — crop=(2560, 0, 2560, 1600) isolates the secondary monitor slice."""
    # Two-color frame: left half 0xAA (primary), right half 0xBB (secondary).
    w, h = mock_capture.width, mock_capture.height
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :w // 2] = 0xAA
    arr[:, w // 2:] = 0xBB
    fake = arr.tobytes()
    monkeypatch.setattr(mock_capture, "capture_raw_bgra", lambda: fake)

    cropped = mock_capture.capture_raw_bgra_with_crop(crop=(2560, 0, 2560, 1600))
    # Every byte in the cropped region should be 0xBB.
    assert len(cropped) == 2560 * 1600 * 4
    assert set(cropped) == {0xBB}


def test_crop_out_of_bounds_clamps_and_returns_black_pixel(mock_capture, monkeypatch):
    """D-02 — degenerate crop returns the defensive black-pixel fallback per PATTERNS L720-722."""
    fake = _fake_full_frame(mock_capture)
    monkeypatch.setattr(mock_capture, "capture_raw_bgra", lambda: fake)

    # x/y way past the frame edge — after clamping w=h=0; return single black pixel.
    out = mock_capture.capture_raw_bgra_with_crop(crop=(99999, 99999, 100, 100))
    assert out == b"\x00\x00\x00\xff"


def test_crop_partial_overflow_clamps_width(mock_capture, monkeypatch):
    """D-02 — crop larger than frame is clamped to the remaining area."""
    fake = _fake_full_frame(mock_capture)
    monkeypatch.setattr(mock_capture, "capture_raw_bgra", lambda: fake)

    # Offset 5000 leaves only 120 px of the 5120-wide frame.
    out = mock_capture.capture_raw_bgra_with_crop(crop=(5000, 0, 500, 100))
    assert len(out) == 120 * 100 * 4


@pytest.mark.ten_bit_smoke
def test_mirror_all_preserves_10bit_smoke(mock_capture, monkeypatch):
    """D-02 — mirror_all path is byte-equal to capture_raw_bgra.

    Gated to mirror_all only (matches the v1 P010 deferral in the plan).
    Preserves the Phase 2 9-checkpoint 10-bit fixture contract: the
    cropped single/pick_one paths feed BGRA into the encoder's internal
    BGRA→P010 conversion — 10-bit fidelity lives in the encoder, not in a
    raw-P010 capture seam (which does not exist in the v1 codebase).
    """
    # Use a recognizable payload so byte-equality is unambiguous.
    import hashlib
    arr = (np.arange(mock_capture.width * mock_capture.height * 4, dtype=np.uint8)
           .reshape(mock_capture.height, mock_capture.width, 4))
    fake = arr.tobytes()
    monkeypatch.setattr(mock_capture, "capture_raw_bgra", lambda: fake)

    mirror = mock_capture.capture_raw_bgra_with_crop(crop=None)
    assert hashlib.sha256(mirror).digest() == hashlib.sha256(fake).digest()


def test_pick_one_bgra_crop_smoke(mock_capture, monkeypatch):
    """D-02 — BGRA crop path returns a correctly-sized buffer (no 10-bit claim).

    Sibling to the ten_bit_smoke mirror_all test: this one exercises the
    BGRA crop path without claiming P010-specific fidelity. The encoder's
    internal BGRA→P010 conversion preserves 10-bit end-to-end through the
    existing Phase 2 pipeline (no new downgrade points).
    """
    fake = _fake_full_frame(mock_capture, fill_value=0xC7)
    monkeypatch.setattr(mock_capture, "capture_raw_bgra", lambda: fake)

    cropped = mock_capture.capture_raw_bgra_with_crop(crop=(0, 0, 64, 64))
    assert len(cropped) == 64 * 64 * 4
    # Every byte should still be the solid 0xC7 fill.
    assert set(cropped) == {0xC7}
