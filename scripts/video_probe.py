"""Headless measured video probe — proves video flows + reports latency.

Reuses the real client protocol (`client.protocol.Client`, which is plain
callbacks + asyncio, no Qt) to connect, authenticate, negotiate transport, and
receive encoded video frames exactly as the GUI client does. Decodes each frame
with PyAV, confirms the chroma format (4:4:4), and reports the numbers a
sysadmin cares about: fps, decode time p50/p99, bitrate, inter-frame jitter,
keyframes, resolution, and a one-way latency estimate.

Usage:
    python scripts/video_probe.py --host 192.168.200.175 --port 8443 --seconds 8
    python scripts/video_probe.py --host ... --json      # machine-readable line

The final line is a single greppable `teraguchi.videoprobe ...` key=value record
so it drops straight into log pipelines / Zabbix.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time


def _pctile(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1))))
    return xs[k]


class Probe:
    def __init__(self, codec_hint: str = "h264"):
        self.lock = threading.Lock()
        self.hello = None
        self.codec_name = codec_hint
        self.frames = 0
        self.keyframes = 0
        self.bytes_total = 0
        self.decode_ms = []
        self.arrivals = []          # client recv times (perf_counter)
        self.skews_ms = []          # client_recv_wall - server_ts (offset+latency)
        self.pixfmts = set()
        self.dims = set()
        self.decode_errors = 0
        self._dec = None
        self._t0_wall = None

    # ---- decoder (direct PyAV so we can read format.name) ----------------
    def _ensure_decoder(self):
        if self._dec is None:
            import av
            # Task #19: real hwaccel when available (mirrors the GUI client's
            # VideoDecoder) so probe decode_ms reflects true client decode.
            self.hw = "software"
            try:
                from av.codec.hwaccel import HWAccel, hwdevices_available
                import sys as _s
                plat = "linux" if _s.platform.startswith("linux") else _s.platform
                order = {"darwin": ["videotoolbox"], "linux": ["cuda", "vaapi"],
                         "win32": ["d3d11va", "dxva2"]}.get(plat, [])
                avail = set(hwdevices_available())
                for name in order:
                    if name not in avail:
                        continue
                    try:
                        hwa = HWAccel(device_type=name, allow_software_fallback=False)
                        self._dec = HWAccel and __import__("av").CodecContext.create(
                            self.codec_name, "r", hwaccel=hwa)
                        self.hw = name
                        break
                    except Exception:
                        continue
            except ImportError:
                pass
            if self._dec is None:
                self._dec = av.CodecContext.create(self.codec_name, "r")
        return self._dec

    def on_hello(self, hello: dict):
        with self.lock:
            self.hello = hello
        # server_hello may carry codec/chroma; adopt the codec for the decoder
        c = (hello or {}).get("codec")
        if c in ("h264", "h265", "av1"):
            self.codec_name = "hevc" if c == "h265" else c

    def on_frame(self, *args):
        # (frame_type, codec, chroma, flags, timestamp_ms, monitor_id, payload)
        recv_perf = time.perf_counter()
        recv_wall_ms = time.time() * 1000.0
        payload = args[-1]
        flags = args[3] if len(args) >= 4 else 0
        server_ts = args[4] if len(args) >= 5 else 0
        if not payload:
            return
        try:
            import av
            dec = self._ensure_decoder()
            t = time.perf_counter()
            packets = dec.parse(payload)
            got = 0
            for pkt in packets:
                for frame in dec.decode(pkt):
                    got += 1
                    with self.lock:
                        self.pixfmts.add(frame.format.name)
                        self.dims.add((frame.width, frame.height))
            dt = (time.perf_counter() - t) * 1000.0
        except Exception:
            with self.lock:
                self.decode_errors += 1
            return

        with self.lock:
            self.frames += 1
            if got:
                self.decode_ms.append(dt)
            self.bytes_total += len(payload)
            self.arrivals.append(recv_perf)
            if server_ts:
                self.skews_ms.append(recv_wall_ms - server_ts)
            # keyframe flag (FLAG_KEYFRAME == 1 in udp_transport)
            if flags & 0x01:
                self.keyframes += 1
            if self._t0_wall is None:
                self._t0_wall = recv_perf

    # ---- reporting -------------------------------------------------------
    def summary(self) -> dict:
        with self.lock:
            span = (self.arrivals[-1] - self.arrivals[0]) if len(self.arrivals) > 1 else 0.0
            fps = (len(self.arrivals) - 1) / span if span > 0 else 0.0
            # inter-frame jitter = stddev of arrival intervals (ms)
            intervals = [ (self.arrivals[i] - self.arrivals[i-1]) * 1000.0
                          for i in range(1, len(self.arrivals)) ]
            jitter = statistics.pstdev(intervals) if len(intervals) > 1 else 0.0
            bitrate_kbps = (self.bytes_total * 8 / 1000.0) / span if span > 0 else 0.0
            # one-way latency estimate: skew includes clock offset; subtract the
            # minimum skew (best-case ~ pure offset) to isolate added latency.
            lat_est = 0.0
            if len(self.skews_ms) > 2:
                base = min(self.skews_ms)
                lat_est = statistics.median([s - base for s in self.skews_ms])
            pixfmt = ",".join(sorted(self.pixfmts)) or "none"
            is_444 = any("444" in f for f in self.pixfmts)
            dims = ";".join(f"{w}x{h}" for (w, h) in sorted(self.dims)) or "none"
            return {
                "frames": self.frames,
                "keyframes": self.keyframes,
                "fps": round(fps, 1),
                "decode_ms_p50": round(_pctile(self.decode_ms, 50), 2),
                "decode_ms_p99": round(_pctile(self.decode_ms, 99), 2),
                "jitter_ms": round(jitter, 2),
                "bitrate_kbps": round(bitrate_kbps, 0),
                "latency_est_ms": round(lat_est, 1),
                "pixfmt": pixfmt,
                "hw_decode": getattr(self, "hw", "software"),
                "chroma_444": is_444,
                "resolution": dims,
                "decode_errors": self.decode_errors,
            }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Teraguchi measured video probe")
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--codec", default="h264", choices=["h264", "h265", "av1"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        import av  # noqa: F401
    except ImportError:
        print("[fail] PyAV not installed (pip install av)", file=sys.stderr)
        return 2

    from client.protocol import ClientProtocol

    probe = Probe(codec_hint="hevc" if args.codec == "h265" else args.codec)
    client = ClientProtocol()
    client.on_server_hello = probe.on_hello
    client.on_video_frame = probe.on_frame

    print(f"[..] connecting to {args.host}:{args.port} ...", file=sys.stderr)
    client.connect(args.host, args.port, username=args.user, password=args.password)

    # wait for hello (up to 10s)
    deadline = time.time() + 10
    while probe.hello is None and time.time() < deadline:
        time.sleep(0.1)
    if probe.hello is None:
        print("[fail] no server_hello within 10s", file=sys.stderr)
        client.disconnect()
        return 1
    print(f"[ok] server_hello: {probe.hello.get('server_name')} "
          f"{probe.hello.get('screen_width')}x{probe.hello.get('screen_height')}",
          file=sys.stderr)

    # nudge a keyframe, then collect
    try:
        client.request_full_frame()
    except Exception:
        pass
    time.sleep(args.seconds)
    # Client-side UDP transport diagnostics (reassembly is where WAN frames
    # die silently — fragments lost/late => whole frame dropped after 150ms).
    # Captured BEFORE disconnect() (which nulls _udp_client).
    udp_stats = {}
    try:
        if client._udp_client is not None:
            udp_stats["udp"] = dict(client._udp_client.stats)
        if client._jitter_buffer is not None:
            udp_stats["jb_depth_ms"] = round(
                getattr(client._jitter_buffer, "_target_depth_ms", -1), 1)
    except Exception:
        pass
    client.disconnect()

    s = probe.summary()
    s.update(udp_stats)
    kv = " ".join(f"{k}={v}" for k, v in s.items())
    if args.json:
        print(json.dumps(s))
    else:
        print("\n=== video probe ===")
        for k, v in s.items():
            print(f"  {k:16} {v}")
    # greppable telemetry record (always to stderr so --json stdout stays clean)
    print(f"teraguchi.videoprobe host={args.host} {kv}", file=sys.stderr)

    ok = s["frames"] > 0 and s["decode_errors"] == 0
    print(("[ok] video flows" if ok else "[fail] no decodable video"), file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
