"""Phase 3 D-05 / DISP-03 / DISP-05 — widget→server-physical-pixel math.

Plan 03-04 Task 1 RED→GREEN. Implementation lands in ``client/viewer.py``
as :meth:`RemoteViewer._widget_to_remote` returning a 4-tuple
``(rx_norm, ry_norm, server_x_px, server_y_px)``.

The synthetic 4-corner test here is the CI analog of the D-08 DXS
hardware spike (Retina MBP + external 4K driving a 2×2560×1600 Rocky
NVIDIA Xorg server). Real-hardware verification stays in
``docs/release.md`` per Phase 1 D-05 (no self-hosted CI runners).
"""
from __future__ import annotations

import os

import pytest


pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("PySide6.QtGui")


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped offscreen QApplication — consistent with other viewer tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_widget_to_remote_returns_4_tuple_with_server_px(qapp):
    """D-05 — ``_widget_to_remote`` returns ``(rx_norm, ry_norm, server_x, server_y)``.

    The two normalized floats remain (Phase 1/2 wire compat) and the
    two integer fields are the new D-05 physical-pixel coords.
    """
    from client.viewer import RemoteViewer

    v = RemoteViewer()
    v.set_remote_size(1920, 1080)
    # Force scaling cache to a known state: full window = full remote.
    v._scale_x = 1.0
    v._scale_y = 1.0
    v._offset_x = 0
    v._offset_y = 0

    result = v._widget_to_remote(100.0, 50.0)
    assert isinstance(result, tuple), (
        f"_widget_to_remote must return a tuple; got {type(result).__name__}"
    )
    assert len(result) == 4, (
        f"_widget_to_remote must return a 4-tuple "
        f"(rx_norm, ry_norm, server_x, server_y); got length {len(result)}"
    )
    rx, ry, sx, sy = result
    assert isinstance(rx, float)
    assert isinstance(ry, float)
    assert isinstance(sx, int), f"server_x must be int; got {type(sx).__name__}"
    assert isinstance(sy, int), f"server_y must be int; got {type(sy).__name__}"
    # Simple topology: widget px (100, 50) → server px (100, 50).
    assert sx == 100
    assert sy == 50
    # Normalized floats in [0, 1].
    assert 0.0 <= rx <= 1.0
    assert 0.0 <= ry <= 1.0


def test_widget_to_remote_simple_scales_with_dpr(qapp):
    """Remote 3840×2160 desktop + widget scaled 0.5 → midpoint click hits center pixel."""
    from client.viewer import RemoteViewer

    v = RemoteViewer()
    v.set_remote_size(3840, 2160)
    # Widget is showing the remote at 0.5x scale.
    v._scale_x = 0.5
    v._scale_y = 0.5
    v._offset_x = 0
    v._offset_y = 0

    # Widget center (960, 540) with 0.5x scale → server (1920, 1080).
    rx, ry, sx, sy = v._widget_to_remote(960.0, 540.0)
    assert sx == 1920
    assert sy == 1080


def test_dxs_topology_4_corner_clicks_land_within_1_px(qapp, fake_mss_monitor_list):
    """DISP-03 / D-08 synthetic fixture — 4-corner click test on 2×2560×1600 desktop.

    Simulates the Retina MBP + external 4K client clicking each of the 8
    corner pixels (4 per server monitor) and asserts the computed server
    coord lands within 1 px of the expected corner. Real-hardware
    verification is the pre-phase D-08 spike (docs/release.md).
    """
    from client.viewer import RemoteViewer

    v = RemoteViewer()
    # DXS server: 2×(2560×1600) laid out side by side on a 5120×1600 virtual.
    v.set_remote_size(5120, 1600)

    # Composite mode: both monitors shown side by side.
    regions = [
        {"id": 1, "name": "DP-1", "x": 0,    "y": 0, "width": 2560, "height": 1600},
        {"id": 2, "name": "DP-2", "x": 2560, "y": 0, "width": 2560, "height": 1600},
    ]
    v.set_monitor_regions(regions)

    # Widget is showing the 5120×1600 composite at 1:1 scale (test fixture).
    v._scale_x = 1.0
    v._scale_y = 1.0
    v._offset_x = 0
    v._offset_y = 0

    # 4 corners of monitor 1 (in composite widget coords 0..2559, 0..1599):
    corners_mon1 = [
        (0.0, 0.0, 0, 0),                       # NW → server (0, 0)
        (2559.0, 0.0, 2559, 0),                 # NE → server (2559, 0)
        (0.0, 1599.0, 0, 1599),                 # SW → server (0, 1599)
        (2559.0, 1599.0, 2559, 1599),           # SE → server (2559, 1599)
    ]
    # 4 corners of monitor 2 (widget x 2560..5119 → server x 2560..5119):
    corners_mon2 = [
        (2560.0, 0.0, 2560, 0),                 # NW of mon 2 → server (2560, 0)
        (5119.0, 0.0, 5119, 0),                 # NE of mon 2 → server (5119, 0)
        (2560.0, 1599.0, 2560, 1599),           # SW of mon 2 → server (2560, 1599)
        (5119.0, 1599.0, 5119, 1599),           # SE of mon 2 → server (5119, 1599)
    ]

    for wx, wy, expected_sx, expected_sy in corners_mon1 + corners_mon2:
        rx, ry, sx, sy = v._widget_to_remote(wx, wy)
        assert abs(sx - expected_sx) <= 1, (
            f"corner click widget=({wx},{wy}) expected_server=({expected_sx},"
            f"{expected_sy}) got server=({sx},{sy}) — off by {sx - expected_sx} px x"
        )
        assert abs(sy - expected_sy) <= 1, (
            f"corner click widget=({wx},{wy}) expected_server=({expected_sx},"
            f"{expected_sy}) got server=({sx},{sy}) — off by {sy - expected_sy} px y"
        )
