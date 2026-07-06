"""Minimal cross-machine connection probe.

Opens a WebSocket to a running Teraguchi server and reads the first protocol
message (the server's auth request / challenge, sent immediately on connect).
Receiving it proves the full path works end to end across machines: TCP over
the VPN -> WebSocket upgrade -> server responds in-protocol.

This is a *transport/handshake* smoke, not a full video test — it deliberately
does not complete auth or decode frames. Exit 0 on a valid first message.

Usage:
    python scripts/connect_probe.py ws://192.168.200.175:8443
"""
import asyncio
import json
import sys

import websockets


async def probe(url: str) -> int:
    try:
        async with websockets.connect(url, open_timeout=8, max_size=None) as ws:
            print(f"[ok] WebSocket connected: {url}")
            raw = await asyncio.wait_for(ws.recv(), timeout=8)
            try:
                msg = json.loads(raw)
                mtype = msg.get("type", "?")
            except (ValueError, TypeError):
                mtype = f"<non-json {len(raw)} bytes>"
                msg = raw
            print(f"[ok] first server message: type={mtype}")
            print(f"     payload: {str(msg)[:300]}")
            return 0
    except Exception as e:
        print(f"[fail] {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8443"
    raise SystemExit(asyncio.run(probe(url)))
