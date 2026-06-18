#!/usr/bin/env python3
# Source: SMPTE RP 2111-inspired 10-bit horizontal ramp. BT.709 video range 64..940.
# P010 stores 10-bit in top 10 bits of 16-bit.
"""Phase 2 D-01 10-bit P010 ramp fixture generator (Wave 0 baseline).

Emits a horizontal luma ramp + neutral-chroma frame as the byte-equality
reference for the 9-checkpoint harness (VIDEO-02). Container layout uses
the plan-mandated 2.5 bytes per 10-bit sample: for every pair (s1, s2)
we write 5 bytes = [u16_le (s1<<6)][u16_le (s2<<6)][0x00 padding].

At 1920x1080 the luma plane totals 5_184_000 bytes (1920*1080/2 * 5) and
the 4:2:0 interleaved UV plane totals 2_592_000 bytes, giving 7_776_000
bytes total. Downstream Wave 2/3 encoder/decoder paths replace this with
real-encoder output validated against the reference ffprobe JSON.
"""
import argparse
import struct
import sys
from pathlib import Path

# BT.709 video-range luma endpoints, 10-bit code values.
Y_BLACK_10 = 64
Y_WHITE_10 = 940
UV_NEUTRAL_10 = 512
P010_SHIFT = 6  # 10-bit stored in TOP 10 bits of a 16-bit container.


def _pack_pair(s1: int, s2: int) -> bytes:
    """Pack two 10-bit samples into a 5-byte P010-with-padding cell."""
    return struct.pack("<HHB", s1 << P010_SHIFT, s2 << P010_SHIFT, 0)


def _luma_row(width: int) -> bytes:
    """One row of Y samples as 2.5-bytes-per-sample cells."""
    span = Y_WHITE_10 - Y_BLACK_10
    denom = max(width - 1, 1)
    out = bytearray()
    for x in range(0, width, 2):
        s1 = Y_BLACK_10 + (span * x) // denom
        nx = x + 1 if x + 1 < width else x
        s2 = Y_BLACK_10 + (span * nx) // denom
        out += _pack_pair(s1, s2)
    return bytes(out)


def _chroma_row(chroma_width: int) -> bytes:
    """One row of interleaved UV samples at neutral 512.

    chroma_width is the number of (U, V) pairs per row.
    """
    u = UV_NEUTRAL_10
    v = UV_NEUTRAL_10
    # Each (U, V) pair uses the same 2.5-byte pack cell.
    return _pack_pair(u, v) * chroma_width


def generate_ramp(width: int, height: int) -> bytes:
    if width <= 0 or height <= 0:
        raise SystemExit(f"invalid dimensions: {width}x{height}")
    if width % 2 or height % 2:
        raise SystemExit("width and height must be even for 4:2:0 subsampling")
    y_row = _luma_row(width)
    y_plane = y_row * height
    chroma_row = _chroma_row(width // 2)
    chroma_plane = chroma_row * (height // 2)
    return y_plane + chroma_plane


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Generate 10-bit P010 ramp fixture")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    data = generate_ramp(args.width, args.height)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(data)
    print(
        f"wrote {len(data)} bytes -> {args.out} "
        f"({args.width}x{args.height} P010 ramp)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
