"""HTTP status endpoint — extracted from server/main.py (Plan 01-11 cleanup).

The broker's MachinePool probes GET /status to check server health. The
websockets library's ``process_request`` hook lets us intercept HTTP
requests before the WebSocket upgrade; this module provides the handler
factory that does exactly that.

Kept as a factory (``make_status_handler(runtimes_getter, default_runtime_getter)``)
so the module-level ``runtimes`` / ``default_runtime`` dicts in
server/main.py stay the single source of truth. server/main.py passes
lambdas that read the current values, so the endpoint sees the latest
state every request without a runtime cycle.
"""
from __future__ import annotations

import json
import logging
import os
import platform as _platform_mod
from typing import Callable

import websockets


logger = logging.getLogger("teraguchi.server.status_endpoint")


def make_status_handler(
    runtimes_getter: Callable[[], dict],
    default_runtime_getter: Callable[[], object],
):
    """Build a ``process_request`` callable for websockets.serve.

    Args:
        runtimes_getter: returns the current ``runtimes: dict[str, SessionRuntime]``
            mapping (PAM mode).
        default_runtime_getter: returns the current ``default_runtime``
            (legacy mode; may be None in PAM mode).
    """
    def handle_http(connection, request):
        """Handle HTTP requests (non-WebSocket) via process_request hook.

        Works with websockets 13+ (process_request receives connection, request).
        """
        if request.path == "/status":
            from websockets.http11 import Response
            runtimes = runtimes_getter()
            default_runtime = default_runtime_getter()

            active_sessions = []
            for username, rt in runtimes.items():
                if rt.client_count > 0:
                    active_sessions.append(username)

            try:
                load_avg = list(os.getloadavg())
            except (OSError, AttributeError):
                load_avg = [0.0, 0.0, 0.0]

            try:
                with open("/proc/uptime") as f:
                    uptime_s = int(float(f.read().split()[0]))
            except Exception:
                uptime_s = 0

            gpu = ""
            for rt in runtimes.values():
                if rt.encoder and rt.encoder.active_backend:
                    gpu = rt.encoder.active_backend
                    break
            if not gpu and default_runtime and default_runtime.encoder:
                gpu = default_runtime.encoder.active_backend or ""

            status = {
                "active_sessions": active_sessions,
                "load_avg": load_avg,
                "uptime_s": uptime_s,
                "gpu": gpu,
                "hostname": _platform_mod.node(),
                "version": "3.0.0",
            }

            body = json.dumps(status).encode()
            return Response(200, "OK", websockets.Headers({
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }), body)

        # Not a status request — proceed with WebSocket handshake
        return None

    return handle_http
