"""STAB-01 — hybrid_transport TransportState + TransportMsg wire constants.

Target-read divergence notes:
- ``TransportMsg`` is a plain class of string constants (not an Enum). Keep it
  that way — these strings travel on the wire and any Enum conversion would
  break protocol compatibility.
- ``TransportState.using_udp`` is a property that is True ONLY when both
  ``mode == UDP_MEDIA`` AND ``udp_confirmed``. Guard against regressing either
  half of the conjunction.
"""
from common.hybrid_transport import TransportMode, TransportMsg, TransportState


def test_initial_mode_is_tcp_only():
    ts = TransportState()
    assert ts.mode == TransportMode.TCP_ONLY
    assert ts.udp_confirmed is False
    assert ts.using_udp is False


def test_using_udp_requires_both_mode_and_confirmed():
    """Guard: both halves of the conjunction must be True for UDP to be active."""
    ts = TransportState(mode=TransportMode.UDP_MEDIA, udp_confirmed=False)
    assert ts.using_udp is False, "mode alone is not enough"

    ts.udp_confirmed = True
    assert ts.using_udp is True, "mode + confirmed → active"

    ts.mode = TransportMode.TCP_ONLY
    assert ts.using_udp is False, "confirmed alone without UDP_MEDIA is not active"


def test_transport_msg_constants_are_stable_strings():
    """These six strings travel on the wire — anyone converting them to an Enum
    or renaming them would break every currently-deployed client.
    """
    assert TransportMsg.UDP_ANNOUNCE == "udp_announce"
    assert TransportMsg.UDP_PROBE == "udp_probe"
    assert TransportMsg.UDP_CONFIRMED == "udp_confirmed"
    assert TransportMsg.UDP_ACTIVE == "udp_active"
    assert TransportMsg.UDP_FALLBACK == "udp_fallback"
    assert TransportMsg.UDP_STATS == "udp_stats"


def test_transport_mode_is_enum_with_two_modes():
    """Sanity: only two modes are defined — no 'QUIC_ONLY' sneaking in until
    Phase 5's transport work lands explicitly."""
    values = {m.value for m in TransportMode}
    assert values == {"tcp_only", "udp_media"}


def test_default_state_defaults():
    """Ensure defaults — any change here affects every new connection."""
    ts = TransportState()
    assert ts.udp_probe_sent_at == 0.0
    assert ts.udp_rtt_ms == 0.0
    assert ts.client_udp_port == 0
    assert ts.client_udp_addr == ""
    assert ts.fallback_reason == ""
    assert ts.udp_packets_sent == 0
    assert ts.udp_packets_received == 0
    assert ts.tcp_frames_sent == 0
    assert ts.packet_loss_pct == 0.0
