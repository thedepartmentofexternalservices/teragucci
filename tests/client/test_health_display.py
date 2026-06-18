"""D-03 — client health overlay color-capability badge.

Tests the `render_color_badge(color_caps)` helper which renders the
'10-bit: confirmed | negotiated | degraded | not_supported' string from
the ServerHelloMsg.color_caps payload.

Kept isolated from the QPainter rendering path — the helper is a pure
function so it can be unit-tested without a QApplication.
"""
from __future__ import annotations

from dataclasses import dataclass

from client.health_display import render_color_badge


@dataclass
class _Caps:
    negotiated_state: str


def test_render_color_badge_reflects_confirmed_state():
    assert render_color_badge(_Caps(negotiated_state="confirmed")) == "10-bit: confirmed"


def test_render_color_badge_reflects_degraded_state():
    assert render_color_badge(_Caps(negotiated_state="degraded")) == "10-bit: degraded"


def test_render_color_badge_reflects_not_supported_state():
    assert render_color_badge(_Caps(negotiated_state="not_supported")) == "10-bit: not_supported"


def test_render_color_badge_accepts_dict_payload():
    """Client may receive color_caps as a parsed dict (pre-dataclass merge)."""
    assert render_color_badge({"negotiated_state": "confirmed"}) == "10-bit: confirmed"


def test_render_color_badge_defaults_to_not_supported_when_missing():
    """Empty/None caps — stay conservative."""
    assert render_color_badge({}) == "10-bit: not_supported"
