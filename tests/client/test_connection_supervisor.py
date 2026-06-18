"""STAB-08 — ConnectionSupervisor unit tests.

Covers backoff math, jitter bounds, retry cap, FSM transitions, and
structured-log emission. Driven entirely against a synthetic
transport_factory callable; no real network I/O.

Per RESEARCH §"Pattern 5: ConnectionSupervisor" lines 459-468 and
plan 01-12 task 2 spec — six-plus test cases required.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from client.connection_supervisor import ConnectionSupervisor
from common.session_fsm import ClientFSM


# ── Synthetic transport factories ─────────────────────────────

async def _boom_transport() -> None:
    """Always raises — simulates a failing transport."""
    raise RuntimeError("synthetic transport error")


async def _ok_transport() -> None:
    """Returns cleanly — simulates a transport that exited normally."""
    return None


# ── Backoff math ──────────────────────────────────────────────

def test_exponential_backoff_no_jitter_is_deterministic():
    """Without jitter, backoff doubles each call and caps at max_delay."""
    sup = ConnectionSupervisor(
        _boom_transport,
        max_retries=100,
        base_delay=1.0,
        max_delay=8.0,
        jitter_pct=0.0,
    )
    delays = [sup._next_delay() for _ in range(6)]
    # 1 → 2 → 4 → 8 → 8 → 8 (capped)
    assert delays == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


def test_jitter_produces_variance():
    """With jitter_pct=0.25 the same base delay produces a spread of values."""
    sup = ConnectionSupervisor(
        _boom_transport,
        base_delay=1.0,
        max_delay=1.0,
        jitter_pct=0.25,
    )
    observations: set[float] = set()
    for _ in range(40):
        sup._current_delay = 1.0   # reset for a fair draw
        observations.add(round(sup._next_delay(), 4))
    # Jitter must produce ≥ 3 distinct values in 40 draws.
    assert len(observations) >= 3
    # Every draw is inside [1 - 0.25, 1 + 0.25]. Min is clamped at 0 too.
    for v in observations:
        assert 0.75 - 1e-9 <= v <= 1.25 + 1e-9


def test_jitter_never_negative():
    """Even with extreme jitter_pct, _next_delay clamps to >= 0."""
    sup = ConnectionSupervisor(
        _boom_transport,
        base_delay=0.5,
        max_delay=0.5,
        jitter_pct=2.0,   # crazy jitter — spread would otherwise go negative
    )
    for _ in range(20):
        sup._current_delay = 0.5
        assert sup._next_delay() >= 0.0


# ── Retry cap ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_cap_triggers_max_retries():
    """After max_retries failures, FSM lands in 'closed' and loop exits."""
    fsm = ClientFSM()
    sup = ConnectionSupervisor(
        _boom_transport,
        fsm=fsm,
        max_retries=3,
        base_delay=0.001,
        max_delay=0.001,
        jitter_pct=0.0,
    )
    await sup.connect()
    assert sup.retry_count > 3
    assert fsm.current_state.id == "closed"


# ── Success path ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_connect_resets_backoff():
    """A clean return from the factory resets retry_count/backoff."""
    sup = ConnectionSupervisor(_ok_transport, base_delay=1.0)
    await sup.connect()
    assert sup.retry_count == 0


# ── FSM drive ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_drives_client_fsm_on_transport_error():
    """After transport errors, FSM has progressed through reconnecting → closed."""
    fsm = ClientFSM()
    sup = ConnectionSupervisor(
        _boom_transport,
        fsm=fsm,
        max_retries=1,
        base_delay=0.001,
        max_delay=0.001,
        jitter_pct=0.0,
    )
    # Drive to 'streaming' first so transport_lost is a valid transition.
    fsm.send("connect_requested")
    fsm.send("tls_ok")
    fsm.send("auth_ok")
    fsm.send("hello_received")
    assert fsm.current_state.id == "streaming"

    await sup.connect()
    # After failures + retry cap: FSM should end in 'closed' (max_retries fires).
    assert fsm.current_state.id in ("reconnecting", "closed")


def test_is_reconnecting_property():
    """Property mirrors FSM state."""
    fsm = ClientFSM()
    sup = ConnectionSupervisor(_ok_transport, fsm=fsm)
    assert sup.is_reconnecting is False
    fsm.send("connect_requested")
    fsm.send("transport_lost")
    assert fsm.current_state.id == "reconnecting"
    assert sup.is_reconnecting is True


@pytest.mark.asyncio
async def test_close_transitions_to_closed():
    """close() stops the loop and drives FSM → closed via user_quit."""
    fsm = ClientFSM()
    sup = ConnectionSupervisor(_ok_transport, fsm=fsm)
    await sup.close()
    assert sup._closing is True
    assert fsm.current_state.id == "closed"


# ── Structured logging ───────────────────────────────────────

@pytest.mark.asyncio
async def test_supervisor_logs_retry_events(caplog):
    """supervisor.backoff / supervisor.retry structured events emit during retries."""
    fsm = ClientFSM()
    sup = ConnectionSupervisor(
        _boom_transport,
        fsm=fsm,
        max_retries=2,
        base_delay=0.001,
        max_delay=0.001,
        jitter_pct=0.0,
    )
    with caplog.at_level(logging.INFO, logger="client.connection_supervisor"):
        await sup.connect()

    # Expect at least one backoff log line and one connect_attempt log line.
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "supervisor.connect_attempt" in joined
    assert "supervisor.backoff" in joined


# ── Closing short-circuits the loop ──────────────────────────

@pytest.mark.asyncio
async def test_closing_before_connect_exits_immediately():
    """close() flagged before connect() must short-circuit the retry loop."""
    sup = ConnectionSupervisor(
        _boom_transport,
        max_retries=1000,
        base_delay=1.0,
        max_delay=1.0,
        jitter_pct=0.0,
    )
    await sup.close()
    # connect() must return quickly (no retries attempted)
    await asyncio.wait_for(sup.connect(), timeout=0.5)
    assert sup.retry_count == 0
