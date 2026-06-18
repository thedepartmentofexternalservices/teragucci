"""D-17 emission schema + D-18 RMS contract tests (Plan 02-12 Task 1).

These cover the off-line analysis tool half of the Wacom verification leg:

  * D-17: ``tools.wacom_quant_analysis`` reads ``event="wacom_matrix"``
    structlog JSONL and filters by ``cell``.
  * D-18: RMS error is computed by aligning server samples to client
    timestamps via ``scipy.interpolate.interp1d`` and exit-code semantics
    are correct (``0`` pass, ``1`` fail, ``2`` missing/malformed).

Real-hardware matrix execution (the 4-cell DXS session per
``02-12-PLAN.md`` Task 2) is a manual checkpoint and is NOT covered by
this file -- it cannot run in CI without physical Wacom hardware.
"""
from __future__ import annotations

import json
import math

import numpy as np


def test_rms_on_identical_ramp_is_near_zero():
    from tools.wacom_quant_analysis import compute_rms

    t = np.arange(0, 1000, 10, dtype=np.int64) * 1_000_000  # 10ms steps, ns
    p = np.linspace(0, 1, t.size)
    rms = compute_rms(t, p, t, p)
    assert rms < 1e-6


def test_rms_on_offset_ramp_reflects_offset():
    from tools.wacom_quant_analysis import compute_rms

    t = np.arange(0, 1000, 10, dtype=np.int64) * 1_000_000
    cp = np.linspace(0, 1, t.size)
    sp = cp + 0.05
    rms = compute_rms(t, cp, t, sp)
    assert abs(rms - 0.05) < 1e-6


def test_rms_with_missing_stream_returns_nan():
    from tools.wacom_quant_analysis import compute_rms

    rms = compute_rms(
        np.array([], dtype=np.int64),
        np.array([], dtype=np.float64),
        np.array([1, 2], dtype=np.int64),
        np.array([0.1, 0.2], dtype=np.float64),
    )
    assert math.isnan(rms)


def test_main_exits_2_on_missing_input(tmp_path):
    from tools.wacom_quant_analysis import main

    rc = main([
        "--client-log", str(tmp_path / "missing_c.jsonl"),
        "--server-log", str(tmp_path / "missing_s.jsonl"),
        "--cell", "intuos-pro-L-sonoma",
        "--out-svg", str(tmp_path / "out.svg"),
        "--pass-threshold", "0.01",
    ])
    assert rc == 2


def test_main_exits_0_on_matched_rampy_log(tmp_path):
    """End-to-end: synthetic client+server JSONL with a matched ramp -> exit 0."""
    from tools.wacom_quant_analysis import main

    client_log = tmp_path / "client.jsonl"
    server_log = tmp_path / "server.jsonl"
    with client_log.open("w") as fc, server_log.open("w") as fs:
        base = 1_745_000_000 * 1_000_000_000
        for i, p in enumerate(np.linspace(0, 1, 100)):
            fc.write(json.dumps({
                "event": "wacom_matrix",
                "stage": "pressure_ramp",
                "cell": "intuos-pro-L-sonoma",
                "timestamp_ns": base + i * 10_000_000,
                "client_pressure": float(p),
            }) + "\n")
            fs.write(json.dumps({
                "event": "wacom_matrix",
                "stage": "pressure_ramp",
                "cell": "intuos-pro-L-sonoma",
                "timestamp_ns": base + i * 10_000_000,
                # near-perfect match (well under the 1% threshold)
                "server_hid_pressure_normalized": float(p) + 0.0001,
            }) + "\n")
    rc = main([
        "--client-log", str(client_log),
        "--server-log", str(server_log),
        "--cell", "intuos-pro-L-sonoma",
        "--out-svg", str(tmp_path / "rms.svg"),
        "--pass-threshold", "0.01",
    ])
    assert rc == 0
    assert (tmp_path / "rms.svg").exists()


def test_main_exits_1_on_known_offset(tmp_path):
    """Synthetic 5% offset > 1% threshold -> exit 1."""
    from tools.wacom_quant_analysis import main

    client_log = tmp_path / "client.jsonl"
    server_log = tmp_path / "server.jsonl"
    with client_log.open("w") as fc, server_log.open("w") as fs:
        base = 1_745_000_000 * 1_000_000_000
        for i, p in enumerate(np.linspace(0, 1, 100)):
            fc.write(json.dumps({
                "event": "wacom_matrix",
                "stage": "pressure_ramp",
                "cell": "intuos-pro-L-sequoia",
                "timestamp_ns": base + i * 10_000_000,
                "client_pressure": float(p),
            }) + "\n")
            fs.write(json.dumps({
                "event": "wacom_matrix",
                "stage": "pressure_ramp",
                "cell": "intuos-pro-L-sequoia",
                "timestamp_ns": base + i * 10_000_000,
                # 5% constant offset -> RMS = 0.05 >> 0.01 threshold
                "server_hid_pressure_normalized": float(p) + 0.05,
            }) + "\n")
    rc = main([
        "--client-log", str(client_log),
        "--server-log", str(server_log),
        "--cell", "intuos-pro-L-sequoia",
        "--out-svg", str(tmp_path / "rms.svg"),
        "--pass-threshold", "0.01",
    ])
    assert rc == 1
