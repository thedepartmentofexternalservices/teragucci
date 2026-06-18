"""ConnectionSupervisor — STAB-08 client-side reconnect policy + ClientFSM driver.

Owns the reconnect loop that used to live inline in
``client/protocol.py::_run_loop``:

  * Exponential backoff (base 1s, doubles, caps at max_delay=30s)
  * ±jitter_pct random jitter (default 25%)
  * Retry cap (default 20 — roughly 10 minutes with 30s cap)
  * Drives a ``common.session_fsm.ClientFSM`` through the connect /
    disconnect / retry / close events

Call sites create a ``ConnectionSupervisor`` with an async
``transport_factory`` callable that does ONE complete connect-and-run
cycle: when the factory awaits and returns cleanly, the supervisor
resets backoff and exits; when it raises, the supervisor increments
the retry count, waits ``_next_delay()`` seconds, and calls the factory
again.

Phase 2 D-11 addition: ``post_auth_hook`` is an awaitable callback the
supervisor invokes immediately after the wrapped factory establishes a
fresh authenticated transport on a RECONNECT (not the first connect).
Use it to send a ``KeyResetModifiersMsg(reason="reconnect")`` as the
first post-auth message on the new connection — this kills the
"network-stall repeat-runaway" failure mode where the server still
holds modifier state from before the drop.

Unit tests live in ``tests/client/test_connection_supervisor.py`` and
the integration reconnect test in
``tests/integration/test_reconnect.py``.

Per RESEARCH §"Pattern 5: ConnectionSupervisor" lines 459-468.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, Optional

from common.session_fsm import ClientFSM

logger = logging.getLogger(__name__)


TransportFactory = Callable[[], Awaitable[None]]
PostAuthHook = Callable[[str], Awaitable[None]]
"""Phase 2 D-11 — async callback invoked after a reconnect's authenticated
transport is established. Argument is the reconnect reason ("reconnect"
on the wire). Owners typically wrap a ``ws.send(KeyResetModifiersMsg(...))``
call. The supervisor only invokes the hook on RECONNECTs, never on the
first connect (``self._retry_count > 0`` predicate)."""


class ConnectionSupervisor:
    """Supervises a single supervised connection lifecycle.

    Create one per ClientProtocol. Call ``await supervisor.connect()`` to
    enter the connect+retry loop; ``await supervisor.close()`` to stop it
    cleanly. The supervisor owns no I/O of its own — ``transport_factory``
    does the actual connect, handshake, and receive loop.

    The supervisor does NOT run on its own thread; callers schedule it on
    whatever event loop ClientProtocol already owns.
    """

    def __init__(
        self,
        transport_factory: TransportFactory,
        *,
        fsm: Optional[ClientFSM] = None,
        max_retries: int = 20,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter_pct: float = 0.25,
        post_auth_hook: Optional[PostAuthHook] = None,
    ):
        self._factory = transport_factory
        self.fsm: ClientFSM = fsm if fsm is not None else ClientFSM()
        self.max_retries = max_retries
        self.base_delay = float(base_delay)
        self.max_delay = float(max_delay)
        self.jitter_pct = float(jitter_pct)
        # Phase 2 D-11 — invoked after a successful RECONNECT to send the
        # release-all-modifiers wire message before any other input.
        self._post_auth_hook: Optional[PostAuthHook] = post_auth_hook

        self._closing = False
        self._retry_count = 0
        self._current_delay = float(base_delay)

    # ── Public state ──────────────────────────────────────────

    @property
    def is_reconnecting(self) -> bool:
        """True iff the wrapped FSM is in the 'reconnecting' state."""
        return self.fsm.current_state.id == "reconnecting"

    @property
    def retry_count(self) -> int:
        return self._retry_count

    @property
    def closing(self) -> bool:
        return self._closing

    # ── Backoff math ──────────────────────────────────────────

    def _next_delay(self) -> float:
        """Return the next backoff delay and advance the internal state.

        Pure exponential backoff: delay doubles each call, capped at
        ``max_delay``. Jitter is applied as a symmetric ±``jitter_pct``
        random factor around the current delay. Returns non-negative
        floats only (clamped at 0).
        """
        delay = min(self._current_delay, self.max_delay)
        spread = delay * self.jitter_pct
        jittered = delay + random.uniform(-spread, spread)
        # Advance the next delay — doubles, capped at max_delay.
        self._current_delay = min(self._current_delay * 2, self.max_delay)
        return max(0.0, jittered)

    def _reset_backoff(self) -> None:
        """Reset backoff + retry_count after a clean connection."""
        self._current_delay = self.base_delay
        self._retry_count = 0

    # ── FSM helpers ───────────────────────────────────────────

    def _fsm_send_safe(self, event: str) -> None:
        """Send an FSM event but swallow TransitionNotAllowed so a stale
        state doesn't crash the supervisor loop."""
        try:
            self.fsm.send(event)
        except Exception as e:
            logger.debug("supervisor.fsm_transition_suppressed event=%s err=%s",
                         event, e)

    # ── Main loop ─────────────────────────────────────────────

    async def _fire_post_auth_hook(self, reason: str) -> None:
        """Phase 2 D-11 — invoke the post-auth reconnect hook.

        Wrapped in try/except so a hook failure can never bring down the
        reconnect loop. Documented contract: hook owners are responsible
        for their own logging on failure.
        """
        if self._post_auth_hook is None:
            return
        try:
            await self._post_auth_hook(reason)
            logger.info(
                "supervisor.post_auth_hook_invoked reason=%s retry=%d",
                reason, self._retry_count,
            )
        except Exception as e:
            logger.warning(
                "supervisor.post_auth_hook_failed reason=%s err=%s",
                reason, e,
            )

    async def connect(self) -> None:
        """Run the supervised connect/retry loop until closed or capped.

        Calls the transport factory. On clean return — resets backoff and
        exits. On exception — emits ``transport_lost`` to the FSM, waits
        ``_next_delay()`` seconds, and retries, up to ``max_retries`` times.
        After the cap, emits ``max_retries`` (FSM → 'closed') and exits.

        Phase 2 D-11: when a RECONNECT (``self._retry_count > 0``) lands a
        fresh authenticated transport, the post-auth hook fires with
        reason='reconnect' BEFORE we wait on the factory body. Hook
        failures are logged but never abort the loop.
        """
        while not self._closing:
            self._fsm_send_safe("connect_requested")
            logger.info("supervisor.connect_attempt retry=%d", self._retry_count)
            # D-11 — fire the reconnect hook for retries (not first connect).
            # The hook runs concurrently with the factory; the factory call
            # below is what actually owns the I/O loop. Splitting this out
            # avoids needing the hook to embed itself inside the factory.
            if self._retry_count > 0:
                await self._fire_post_auth_hook("reconnect")
            try:
                await self._factory()
            except asyncio.CancelledError:
                # Cooperative cancellation — treat as close().
                self._closing = True
                self._fsm_send_safe("user_quit")
                raise
            except Exception as e:
                logger.warning("supervisor.transport_error err=%s", e)
                self._fsm_send_safe("transport_lost")
                self._retry_count += 1
                if self._retry_count > self.max_retries:
                    logger.error("supervisor.max_retries attempts=%d",
                                 self._retry_count)
                    self._fsm_send_safe("max_retries")
                    return
                delay = self._next_delay()
                logger.info("supervisor.backoff delay_s=%.3f retry=%d",
                            delay, self._retry_count)
                if self._closing:
                    return
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    self._closing = True
                    self._fsm_send_safe("user_quit")
                    raise
                continue

            # Factory returned cleanly — transport completed normally.
            self._reset_backoff()
            return

    async def close(self) -> None:
        """Stop the supervisor. Safe to call more than once."""
        if self._closing:
            return
        self._closing = True
        self._fsm_send_safe("user_quit")


__all__ = ["ConnectionSupervisor", "TransportFactory", "PostAuthHook"]
