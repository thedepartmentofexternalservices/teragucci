"""STAB-06 / D-13 — ClientFSM + ServerFSM + disagreement pair table.

Covers the declarative session FSMs defined in :mod:`common.session_fsm`.

Required exports (from plan 01-07 must_haves):

* ``test_client_initial_state``
* ``test_server_initial_state``
* ``test_full_happy_path_transition``
* ``test_invalid_transition_raises``
* ``test_allowed_pairs_table``
* ``test_state_pair_disagreement_detected``

Plus the broader coverage defined in 01-07-PLAN.md <action>.
"""
from __future__ import annotations

import pytest
from statemachine.exceptions import TransitionNotAllowed

from common.session_fsm import (
    ALLOWED_PAIRS,
    CLIENT_STATES,
    SERVER_STATES,
    ClientFSM,
    ServerFSM,
    is_state_pair_allowed,
)


# ─── State-tuple contract ─────────────────────────────────────────────
def test_client_states_count():
    assert len(CLIENT_STATES) == 8
    assert "disconnected" in CLIENT_STATES
    assert "streaming" in CLIENT_STATES
    assert "closed" in CLIENT_STATES


def test_server_states_count():
    assert len(SERVER_STATES) == 7
    assert "bootstrapping" in SERVER_STATES
    assert "streaming" in SERVER_STATES
    assert "closed" in SERVER_STATES


def test_client_states_are_tuple_of_str():
    assert isinstance(CLIENT_STATES, tuple)
    for s in CLIENT_STATES:
        assert isinstance(s, str)


def test_server_states_are_tuple_of_str():
    assert isinstance(SERVER_STATES, tuple)
    for s in SERVER_STATES:
        assert isinstance(s, str)


def test_allowed_pairs_is_frozenset():
    assert isinstance(ALLOWED_PAIRS, frozenset)


def test_allowed_pairs_count():
    # Exactly 9 pairs per RESEARCH §"Disagreement detection (STAB-06)".
    assert len(ALLOWED_PAIRS) == 9


def test_allowed_pairs_table():
    """Frontmatter must_haves export — the exact 9 pair table is locked."""
    expected = frozenset({
        ("handshaking", "bootstrapping"),
        ("authenticating", "authenticating"),
        ("capability_exchange", "capability_exchange"),
        ("streaming", "streaming"),
        ("streaming", "reconfiguring"),
        ("degraded", "streaming"),
        ("reconnecting", "draining"),
        ("reconnecting", "closed"),
        ("closed", "closed"),
    })
    assert ALLOWED_PAIRS == expected


# ─── ClientFSM ────────────────────────────────────────────────────────
def test_client_initial_state():
    """Frontmatter must_haves export."""
    fsm = ClientFSM()
    assert fsm.current_state.id == "disconnected"


def test_client_happy_path():
    fsm = ClientFSM()
    fsm.send("connect_requested")
    assert fsm.current_state.id == "handshaking"
    fsm.send("tls_ok")
    assert fsm.current_state.id == "authenticating"
    fsm.send("auth_ok")
    assert fsm.current_state.id == "capability_exchange"
    fsm.send("hello_received")
    assert fsm.current_state.id == "streaming"


def test_full_happy_path_transition():
    """Frontmatter must_haves export — both FSMs reach ``streaming``.

    Covers the full handshake for the client (disconnected → streaming)
    AND for the server (bootstrapping → streaming). Names locked by the
    plan frontmatter.
    """
    client = ClientFSM()
    for ev in ("connect_requested", "tls_ok", "auth_ok", "hello_received"):
        client.send(ev)
    assert client.current_state.id == "streaming"

    server = ServerFSM()
    for ev in ("tls_ok", "auth_ok", "client_hello"):
        server.send(ev)
    assert server.current_state.id == "streaming"


def test_client_degraded_round_trip():
    fsm = ClientFSM()
    for ev in ("connect_requested", "tls_ok", "auth_ok", "hello_received"):
        fsm.send(ev)
    assert fsm.current_state.id == "streaming"
    fsm.send("health_degraded")
    assert fsm.current_state.id == "degraded"
    fsm.send("health_restored")
    assert fsm.current_state.id == "streaming"


def test_client_transport_lost_from_multiple_states():
    """``transport_lost`` has multiple origin states — all route to ``reconnecting``."""
    for starting_events in [
        ("connect_requested",),                                        # handshaking
        ("connect_requested", "tls_ok"),                               # authenticating
        ("connect_requested", "tls_ok", "auth_ok", "hello_received"),  # streaming
    ]:
        fsm = ClientFSM()
        for ev in starting_events:
            fsm.send(ev)
        fsm.send("transport_lost")
        assert fsm.current_state.id == "reconnecting"


def test_client_max_retries_terminates():
    fsm = ClientFSM()
    fsm.send("connect_requested")
    fsm.send("transport_lost")
    assert fsm.current_state.id == "reconnecting"
    fsm.send("max_retries")
    assert fsm.current_state.id == "closed"
    # closed is final — any further event raises.
    with pytest.raises(TransitionNotAllowed):
        fsm.send("connect_requested")


def test_invalid_transition_raises():
    """Frontmatter must_haves export — invalid transitions MUST raise (Pitfall 1)."""
    fsm = ClientFSM()
    # disconnected has no ``tls_ok`` path.
    with pytest.raises(TransitionNotAllowed):
        fsm.send("tls_ok")


def test_client_invalid_transition_from_streaming():
    fsm = ClientFSM()
    for ev in ("connect_requested", "tls_ok", "auth_ok", "hello_received"):
        fsm.send(ev)
    assert fsm.current_state.id == "streaming"
    # Already past the handshake — ``connect_requested`` is not valid from streaming.
    with pytest.raises(TransitionNotAllowed):
        fsm.send("connect_requested")


def test_client_user_quit_from_many_states():
    """``user_quit`` should work from every non-closed state."""
    for starting_events in [
        (),                                                            # disconnected
        ("connect_requested",),                                        # handshaking
        ("connect_requested", "tls_ok"),                               # authenticating
        ("connect_requested", "tls_ok", "auth_ok"),                    # capability_exchange
        ("connect_requested", "tls_ok", "auth_ok", "hello_received"),  # streaming
    ]:
        fsm = ClientFSM()
        for ev in starting_events:
            fsm.send(ev)
        fsm.send("user_quit")
        assert fsm.current_state.id == "closed"


def test_client_reconnect_cycle_returns_to_handshaking():
    fsm = ClientFSM()
    for ev in ("connect_requested", "tls_ok", "auth_ok", "hello_received"):
        fsm.send(ev)
    fsm.send("transport_lost")
    assert fsm.current_state.id == "reconnecting"
    fsm.send("connect_requested")
    assert fsm.current_state.id == "handshaking"


def test_client_auth_failed_returns_to_disconnected():
    fsm = ClientFSM()
    fsm.send("connect_requested")
    fsm.send("tls_ok")
    assert fsm.current_state.id == "authenticating"
    fsm.send("auth_failed")
    assert fsm.current_state.id == "disconnected"


# ─── ServerFSM ────────────────────────────────────────────────────────
def test_server_initial_state():
    """Frontmatter must_haves export."""
    fsm = ServerFSM()
    assert fsm.current_state.id == "bootstrapping"


def test_server_happy_path():
    fsm = ServerFSM()
    fsm.send("tls_ok")
    assert fsm.current_state.id == "authenticating"
    fsm.send("auth_ok")
    assert fsm.current_state.id == "capability_exchange"
    fsm.send("client_hello")
    assert fsm.current_state.id == "streaming"


def test_server_reconfigure_cycle():
    fsm = ServerFSM()
    for ev in ("tls_ok", "auth_ok", "client_hello"):
        fsm.send(ev)
    fsm.send("reconfigure_requested")
    assert fsm.current_state.id == "reconfiguring"
    fsm.send("reconfigure_done")
    assert fsm.current_state.id == "streaming"


def test_server_encoder_crash_reconfigures():
    fsm = ServerFSM()
    for ev in ("tls_ok", "auth_ok", "client_hello"):
        fsm.send(ev)
    fsm.send("encoder_crashed")
    assert fsm.current_state.id == "reconfiguring"


def test_server_draining_path():
    fsm = ServerFSM()
    for ev in ("tls_ok", "auth_ok", "client_hello"):
        fsm.send(ev)
    fsm.send("ws_closed")
    assert fsm.current_state.id == "draining"
    fsm.send("drain_timeout")
    assert fsm.current_state.id == "closed"


def test_server_last_frame_sent_closes():
    fsm = ServerFSM()
    for ev in ("tls_ok", "auth_ok", "client_hello"):
        fsm.send(ev)
    fsm.send("ws_closed")
    assert fsm.current_state.id == "draining"
    fsm.send("last_frame_sent")
    assert fsm.current_state.id == "closed"


def test_server_auth_failed_closes():
    fsm = ServerFSM()
    fsm.send("tls_ok")
    fsm.send("auth_failed")
    assert fsm.current_state.id == "closed"


def test_server_invalid_transition_raises():
    fsm = ServerFSM()
    with pytest.raises(TransitionNotAllowed):
        fsm.send("client_hello")  # wrong origin state


# ─── Pair disagreement detection ──────────────────────────────────────
def test_state_pair_disagreement_detected():
    """Frontmatter must_haves export — non-allowed pairs return False."""
    # Allowed pairs
    assert is_state_pair_allowed("streaming", "streaming") is True
    assert is_state_pair_allowed("streaming", "reconfiguring") is True
    assert is_state_pair_allowed("reconnecting", "draining") is True
    # Disallowed pairs → disagreement
    assert is_state_pair_allowed("streaming", "bootstrapping") is False
    assert is_state_pair_allowed("authenticating", "streaming") is False
    assert is_state_pair_allowed("disconnected", "streaming") is False


def test_allowed_pair_streaming_streaming():
    assert is_state_pair_allowed("streaming", "streaming") is True


def test_allowed_pair_streaming_reconfiguring():
    assert is_state_pair_allowed("streaming", "reconfiguring") is True


def test_allowed_pair_reconnecting_draining():
    assert is_state_pair_allowed("reconnecting", "draining") is True


def test_disallowed_pair_streaming_bootstrapping():
    assert is_state_pair_allowed("streaming", "bootstrapping") is False


def test_disallowed_pair_authenticating_streaming():
    assert is_state_pair_allowed("authenticating", "streaming") is False


def test_unknown_state_returns_false():
    assert is_state_pair_allowed("unknown", "streaming") is False
    assert is_state_pair_allowed("streaming", "unknown") is False
    assert is_state_pair_allowed("", "") is False


# ─── fsm.current_state.id serializes verbatim to the wire ─────────────
def test_client_state_id_matches_tuple_entries():
    """Serialization contract: ``fsm.current_state.id`` must equal one of CLIENT_STATES."""
    fsm = ClientFSM()
    assert fsm.current_state.id in CLIENT_STATES
    for ev in ("connect_requested", "tls_ok", "auth_ok", "hello_received"):
        fsm.send(ev)
        assert fsm.current_state.id in CLIENT_STATES


def test_server_state_id_matches_tuple_entries():
    fsm = ServerFSM()
    assert fsm.current_state.id in SERVER_STATES
    for ev in ("tls_ok", "auth_ok", "client_hello"):
        fsm.send(ev)
        assert fsm.current_state.id in SERVER_STATES
