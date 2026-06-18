#!/usr/bin/env python3
"""Wacom pressure-quantization RMS analysis (Phase 2 D-18).

Ingests two side-by-side structlog JSONL streams (client and server), aligns
server pressure samples to client sample timestamps via linear interpolation,
computes RMS error normalized to max pressure, and produces an SVG plot.

D-17 emission schema (one event per sample, in either stream):

  {"event": "wacom_matrix", "stage": "pressure_ramp",
   "cell": "intuos-pro-L-sonoma", "timestamp_ns": <int>,
   "client_pressure": <float in [0, 1]>, ...}

  {"event": "wacom_matrix", "stage": "pressure_ramp",
   "cell": "intuos-pro-L-sonoma", "timestamp_ns": <int>,
   "server_hid_pressure_normalized": <float in [0, 1]>, ...}

Exit codes:
  0  RMS < threshold (pass)
  1  RMS >= threshold (fail)
  2  missing / malformed inputs (no usable wacom_matrix events)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import List, Tuple

import numpy as np


def _read_wacom_samples(
    path: pathlib.Path, cell: str
) -> Tuple[np.ndarray, np.ndarray, str]:
    """Return (timestamps_ns, pressures, schema_tag) filtered by cell.

    schema_tag is one of: ``"missing"`` (file not found), ``"client"``,
    ``"server"``, or ``"unknown"`` (file present but no matching events).
    """
    if not path.exists():
        return np.array([]), np.array([]), "missing"
    ts: List[int] = []
    pr: List[float] = []
    schema = "unknown"
    with path.open() as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                # T-02-36 mitigation: silently skip malformed lines.
                continue
            if r.get("event") != "wacom_matrix":
                continue
            if r.get("cell") != cell:
                continue
            if "client_pressure" in r:
                schema = "client"
                ts.append(int(r["timestamp_ns"]))
                pr.append(float(r["client_pressure"]))
            elif "server_hid_pressure_normalized" in r:
                schema = "server"
                ts.append(int(r["timestamp_ns"]))
                pr.append(float(r["server_hid_pressure_normalized"]))
    return np.array(ts, dtype=np.int64), np.array(pr, dtype=np.float64), schema


def compute_rms(
    client_t: np.ndarray,
    client_p: np.ndarray,
    server_t: np.ndarray,
    server_p: np.ndarray,
) -> float:
    """Align server samples to client timestamps; RMS of the difference.

    Returns NaN if either stream is empty or the streams do not overlap.
    """
    if client_t.size == 0 or server_t.size == 0:
        return float("nan")
    from scipy.interpolate import interp1d

    ci = np.argsort(client_t)
    client_t, client_p = client_t[ci], client_p[ci]
    si = np.argsort(server_t)
    server_t, server_p = server_t[si], server_p[si]
    t_min = max(client_t[0], server_t[0])
    t_max = min(client_t[-1], server_t[-1])
    if t_max <= t_min:
        return float("nan")
    mask = (client_t >= t_min) & (client_t <= t_max)
    ct = client_t[mask].astype(np.float64)
    cp = client_p[mask]
    if ct.size == 0:
        return float("nan")
    f_srv = interp1d(
        server_t.astype(np.float64),
        server_p,
        kind="linear",
        assume_sorted=True,
        bounds_error=False,
        fill_value="extrapolate",
    )
    sp = f_srv(ct)
    diff = cp - sp
    return float(np.sqrt(np.mean(diff * diff)))


def _render_svg(
    out_path: pathlib.Path,
    cell: str,
    rms: float,
    client_t: np.ndarray,
    client_p: np.ndarray,
    server_t: np.ndarray,
    server_p: np.ndarray,
) -> None:
    """Render a matplotlib SVG: client + server pressure vs time, RMS in title."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 4))
    if client_t.size:
        t0 = client_t[0]
        ax.plot(
            (client_t - t0) / 1e9,
            client_p,
            label="client (QTabletEvent)",
            linewidth=1.0,
        )
    if server_t.size:
        t0s = client_t[0] if client_t.size else server_t[0]
        ax.plot(
            (server_t - t0s) / 1e9,
            server_p,
            label="server (HID report)",
            linewidth=1.0,
        )
    ax.set_xlabel("time (s)")
    ax.set_ylabel("normalized pressure")
    ax.set_title(f"{cell} | RMS = {rms:.4f}")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, format="svg")
    plt.close(fig)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Wacom pressure-quantization RMS analysis (Phase 2 D-18)."
    )
    p.add_argument("--client-log", required=True)
    p.add_argument("--server-log", required=True)
    p.add_argument("--cell", required=True)
    p.add_argument("--out-svg", required=True)
    p.add_argument("--pass-threshold", type=float, default=0.01)
    args = p.parse_args(argv)
    client_t, client_p, c_schema = _read_wacom_samples(
        pathlib.Path(args.client_log), args.cell
    )
    server_t, server_p, s_schema = _read_wacom_samples(
        pathlib.Path(args.server_log), args.cell
    )
    if c_schema == "missing" or s_schema == "missing":
        print(
            f"ERROR: missing log file(s) for cell={args.cell}", file=sys.stderr
        )
        return 2
    if client_t.size == 0 or server_t.size == 0:
        print(
            f"ERROR: no wacom_matrix events for cell={args.cell}",
            file=sys.stderr,
        )
        return 2
    rms = compute_rms(client_t, client_p, server_t, server_p)
    _render_svg(
        pathlib.Path(args.out_svg),
        args.cell,
        rms,
        client_t,
        client_p,
        server_t,
        server_p,
    )
    print(
        f"cell={args.cell} RMS={rms:.4f} "
        f"threshold={args.pass_threshold:.4f}"
    )
    if not (rms == rms):  # NaN check
        return 2
    return 0 if rms < args.pass_threshold else 1


if __name__ == "__main__":
    sys.exit(main())
