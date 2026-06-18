"""Phase 2 D-19 — PenFSM unit tests (Plan 02-10 Task 3).

The PenFSM lives in ``common/session_fsm.py`` alongside ClientFSM / ServerFSM
per RESEARCH §"Claude's Discretion #6" (pen state is orthogonal to session
state but the declarative ``python-statemachine`` idiom is shared, and
keeping it in the same module mirrors the serialization contract).

Idempotency is the load-bearing property per D-19: duplicate
``enter_proximity`` / ``leave_proximity`` events from the client re-synth
path (focusIn + showEvent after Cmd-Tab cycles, lockscreen wakes,
minimize/restore) must be no-ops on the server. Pitfall #3 "proximity
event eaten by lockscreen" is the failure mode these tests lock down.
"""
from __future__ import annotations

from common.session_fsm import PenFSM


def test_penfsm_initial_state_is_out_of_proximity():
    fsm = PenFSM()
    assert fsm.current_state.id == "out_of_proximity"


def test_penfsm_enter_proximity_transitions_to_in_proximity():
    fsm = PenFSM()
    fsm.send("enter_proximity")
    assert fsm.current_state.id == "in_proximity"


def test_penfsm_enter_proximity_is_idempotent():
    """D-19 re-synth contract: duplicate enter from in_proximity is a no-op."""
    fsm = PenFSM()
    fsm.send("enter_proximity")
    fsm.send("enter_proximity")  # must not raise
    assert fsm.current_state.id == "in_proximity"


def test_penfsm_leave_from_out_of_proximity_is_noop():
    """Duplicate leave from already-out is a no-op (idempotent)."""
    fsm = PenFSM()
    fsm.send("leave_proximity")
    assert fsm.current_state.id == "out_of_proximity"


def test_penfsm_enter_then_leave_roundtrips():
    fsm = PenFSM()
    fsm.send("enter_proximity")
    fsm.send("leave_proximity")
    assert fsm.current_state.id == "out_of_proximity"
