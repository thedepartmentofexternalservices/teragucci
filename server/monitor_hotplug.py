"""Monitor hot-plug loop — extracted from SessionRuntime.

D-11 extraction (Plan 01-10 Task 2). Every 1 second (Phase 3 D-11
cadence), poll the ``ScreenCapture.detect_hotplug()`` sentinel or the
push-delegate-set ``_hotplug_pending`` flag; if a change is detected,
re-apply per-session capture_mode (harvesting any auto-fallback
events), broadcast the refreshed monitor list + degradations payload
to every authenticated client, and restart the encoder so the new
framebuffer geometry takes effect.

Plan 03-05 additions:
- The per-session ``apply_capture_mode`` iteration runs BEFORE the
  broadcast so the MonitorListMsg carries the fallback events with the
  new topology (D-09).
- ``MonitorListMsg.degradations`` payload lists per-client fallback
  events ``{"client_token", "previous_pick", "now_showing"}``.
- Telemetry: ``monitor_hotplug.broadcast`` + ``monitor_hotplug.fallback``
  events fire per iteration (D-18 latency budget tracking, counts only
  per T-03-13 no-payload-bytes rule).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Optional

from common.messages import MonitorListMsg

if TYPE_CHECKING:
    # D-11 / Plan 01-11 Task 2: SessionRuntime moved to server/session_runtime.py
    from server.session_runtime import SessionRuntime


logger = logging.getLogger("teraguchi.server.monitor_hotplug")


class MonitorHotplug:
    """Polls the capture backend for monitor hot-plug events. One
    instance per SessionRuntime."""

    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def run(self) -> None:
        # Phase 3 D-11 — cadence tightened from 5s to 1s so the push-
        # complement check (MacScreenCapture._hotplug_pending on macOS)
        # has a fast safety net. The 1s poll still covers the case where
        # the NSWorkspace notification is missed (sleep/wake, runloop
        # suspension). Linux keeps the same poll cadence; the cost is
        # one extra mss.mss() probe per 1s on a topology that never
        # changes, which is negligible.
        while self._running:
            await asyncio.sleep(1.0)
            runtime = self._runtime
            if not runtime.capture:
                continue
            pending = getattr(runtime.capture, "_hotplug_pending", False)
            if not (pending or runtime.capture.detect_hotplug()):
                continue
            if pending:
                # Clear the push flag; the delegate will re-set it on
                # the next display configuration change.
                try:
                    runtime.capture._hotplug_pending = False
                except Exception:
                    pass

            monitors_data = [asdict(m) for m in runtime.capture.list_monitors()]

            # Plan 03-05 / D-09 — re-apply per-session capture_mode and
            # harvest degradations BEFORE the broadcast so the
            # MonitorListMsg carries the fallback events with the new
            # geometry. Wrap client iteration in list(...) to tolerate
            # dict mutation per PATTERNS L361.
            degradations: list = []
            apply_capture_mode = getattr(
                runtime, "apply_capture_mode", None,
            )
            if apply_capture_mode is not None:
                for ws, cs in list(runtime.clients.items()):
                    if not getattr(cs, "authenticated", False):
                        continue
                    mode = getattr(cs, "capture_mode", "mirror_all")
                    if mode == "mirror_all":
                        continue
                    previous_pick = getattr(cs, "picked_monitor_name", "") or ""
                    try:
                        ok = apply_capture_mode(
                            cs,
                            mode,
                            getattr(cs, "picked_monitor_id", -1),
                            previous_pick,
                        )
                    except Exception as e:
                        logger.debug(
                            "hotplug.reapply_capture_mode_failed err=%s", e,
                        )
                        continue
                    if not ok and previous_pick:
                        # Fallback fired — emit a degradation event for
                        # this client. ClientSession.client_id is the
                        # canonical session identifier (str(id(ws))).
                        now_showing = getattr(
                            cs, "picked_monitor_name", "primary",
                        ) or "primary"
                        token = getattr(cs, "client_id", "") or ""
                        degradations.append({
                            "client_token": token,
                            "previous_pick": previous_pick,
                            "now_showing": now_showing,
                        })

            msg_json = MonitorListMsg(
                monitors=monitors_data,
                degradations=degradations,
            ).to_json()

            for ws, cs in list(runtime.clients.items()):
                if getattr(cs, "authenticated", False):
                    try:
                        await cs.enqueue(msg_json)
                    except Exception:
                        pass

            if runtime.encoder:
                runtime.encoder_lifecycle.restart()

            # D-18 telemetry — counts only (T-03-13 information-disclosure
            # mitigation: no payload bytes, no monitor names).
            logger.info(
                "monitor_hotplug.broadcast monitor_count=%d degradation_count=%d",
                len(monitors_data), len(degradations),
            )
            if degradations:
                logger.info(
                    "monitor_hotplug.fallback events=%d", len(degradations),
                )

    def start(self) -> None:
        self._running = True
        self._task = asyncio.ensure_future(self.run())

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
