"""Health ping loop — extracted from server/main.py::SessionRuntime.

D-11 extraction (Plan 01-10). Every 2 seconds, broadcast a HealthPong
stamped with the server-side FSM state (STAB-06 / Plan 01-08 inversion:
server originates the pong, client originates HealthPing) plus the
HealthStats JSON to every authenticated client. Behavior preserved
exactly from the pre-extraction monolith.

Plan 01-14 additions:

* :func:`check_state_pair` — structured ERROR emit when an incoming
  HealthPing carries a ``client_state`` that disagrees with the current
  server state per :func:`common.session_fsm.is_state_pair_allowed`.
  Plan 01-08 wired the data path as a stdlib ``logger.warning`` in
  ``session_runtime.handle_input``; Plan 01-14 promotes it to a structlog
  ERROR event the overlay / dashboard can filter on.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from common.logging import get_logger as _get_structlog
from common.messages import HealthPong
from common.session_fsm import is_state_pair_allowed

if TYPE_CHECKING:
    # D-11 / Plan 01-11 Task 2: SessionRuntime moved to server/session_runtime.py
    from server.session_runtime import SessionRuntime


logger = logging.getLogger("teraguchi.server.health_loop")


def check_state_pair(
    client_state: str,
    server_state: str,
    client_id: str,
) -> None:
    """Emit ``fsm.state_disagreement`` at ERROR if the pair is not allowed.

    Plan 01-14 — structured observability upgrade. Called by
    :meth:`server.session_runtime.SessionRuntime.handle_input` on every
    incoming HealthPing. Empty strings are treated as "not reported by
    this peer" and skipped (backward compat with pre-Plan 01-08 clients).

    The structlog bound logger is fetched per-call (rather than cached at
    module scope) so that tests which swap ``sys.stderr`` + re-configure
    structlog pick up the fresh PrintLoggerFactory binding. In production
    this is a single extra attribute lookup per HealthPing — negligible.

    Args:
        client_state: ``HealthPing.client_state`` — serialized ClientFSM id.
        server_state: This session's ``ServerFSM.current_state.id``.
        client_id: ``ClientSession.client_id`` for downstream filtering.
    """
    if not client_state or not server_state:
        return
    if is_state_pair_allowed(client_state, server_state):
        return
    # Structured ERROR — overlay + dashboard filter on level=error.
    _get_structlog("server.health").error(
        "fsm.state_disagreement",
        client_state=client_state,
        server_state=server_state,
        client_id=client_id,
    )


class HealthLoop:
    """Every 2 s: build HealthPong + HealthStats for every auth'd client.

    The direction-inversion (server emits pong, not ping) landed in Plan
    01-08 so the server — the authoritative FSM — can stamp
    ``server_state`` on every beat. This loop is the hook OBS-03 (Plan
    17) will eventually extend with state-disagreement metrics.
    """

    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def run(self) -> None:
        while self._running:
            await asyncio.sleep(2.0)
            if not self._runtime.clients:
                continue
            seq = self._runtime.health.next_ping_sequence()
            stats_json = self._runtime.health.get_stats().to_json()
            for ws, cs in list(self._runtime.clients.items()):
                if cs.authenticated:
                    try:
                        # STAB-06 / Plan 01-08: server emits HealthPong
                        # stamped with server_state. Client originates
                        # HealthPing. See server/main.py
                        # ::SessionRuntime.handle_input for the inverse
                        # ingress path (HEALTH_PING → record + pair-check).
                        pong = HealthPong(
                            sequence=seq,
                            server_state=cs.fsm.current_state.id,
                        )
                        await cs.enqueue(pong.to_json())
                        await cs.enqueue(stats_json)
                    except Exception:
                        pass

    def start(self) -> None:
        self._running = True
        self._task = asyncio.ensure_future(self.run())

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
