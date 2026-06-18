"""Clock-offset estimator (OBS-02 — RESEARCH Open Q #5 planner-open locked).

Uses HealthPing / HealthPong round-trip timestamps to estimate client↔server
clock drift so per-stage latency metrics that span tiers can be normalized
against a shared time base before the client overlay renders them.

Design decisions (locked per RESEARCH Open Q #5 + Plan 01-14 CONTEXT):

* ~40-LOC helper — no NTP, no PTP, no external dependency. EWMA of the
  per-exchange offset samples is enough for an in-session overlay.
* Re-estimate every 60 s of session lifetime. :meth:`should_reestimate`
  returns True when the interval has elapsed (or before the first sample).
* Server-side only for Phase 1 — the protocol surface stays unchanged.
  :class:`common.messages.HealthPing.timestamp_ms` and
  :class:`common.messages.HealthPong.ping_timestamp_ms` +
  ``server_timestamp_ms`` provide the three timestamps needed; the
  client-side smoke harness (Phase 4) will own the client end.

Math
----

Given a ping/pong exchange:

  T1 = ``ping_sent_ms``         (client clock when it sent the ping)
  T2 = ``server_received_ms``   (server clock when it received the ping)
  T3 = ``pong_received_ms``     (client clock when the pong came back)

Assuming symmetric one-way latency::

    one_way_ms = (T3 - T1) / 2
    offset_ms  = T2 - T1 - one_way_ms
               = T2 - (T1 + T3) / 2

A positive ``offset_ms`` means the server clock is AHEAD of the client clock
by that many milliseconds. EWMA damps transient spikes (one slow packet
shouldn't swing the estimator hard).
"""
from __future__ import annotations

import time
from typing import Optional


class ClockOffsetEstimator:
    """Sliding EWMA estimator — one sample per successful ping/pong exchange.

    Plan 01-14 ships the Phase 1 version. Phase 4's smoke harness consumes
    :attr:`current_offset_ms` to align its per-stage latency breakdown.
    """

    def __init__(self, alpha: float = 0.2, reestimate_interval_s: float = 60.0):
        self._alpha = alpha
        self._interval = reestimate_interval_s
        self._offset_ms: Optional[float] = None
        self._last_sample_time: float = 0.0

    @property
    def current_offset_ms(self) -> float:
        """Return current estimated offset (server - client) in ms. 0.0 until seeded."""
        return self._offset_ms if self._offset_ms is not None else 0.0

    def submit_exchange(
        self,
        ping_sent_ms: int,
        server_received_ms: int,
        pong_received_ms: int,
    ) -> None:
        """Append a ping/pong sample and update the EWMA.

        See module docstring for the symmetric-latency math. Called once per
        HealthPong the client receives.
        """
        one_way = (pong_received_ms - ping_sent_ms) / 2.0
        offset = server_received_ms - ping_sent_ms - one_way
        if self._offset_ms is None:
            self._offset_ms = offset
        else:
            self._offset_ms = self._alpha * offset + (1 - self._alpha) * self._offset_ms
        self._last_sample_time = time.time()

    def should_reestimate(self) -> bool:
        """True before the first sample, or whenever the interval has elapsed."""
        if self._last_sample_time == 0:
            return True
        return (time.time() - self._last_sample_time) >= self._interval
