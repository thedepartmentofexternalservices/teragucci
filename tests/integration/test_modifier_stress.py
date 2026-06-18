"""INPUT-02 / INPUT-03 / INPUT-06 — modifier stress integration coverage.

Plan 02-09 Task 4 — covers three failure classes documented by D-11 and
PITFALLS #4 / Pitfall 6:

  1. Focus-stress: client losing focus emits ``reset_modifiers_requested``
     toward the wire (server-side InputInjector.reset_modifiers is the
     idempotent endpoint, exercised in tests/server/test_modifier_dispatch).
  2. Reconnect contract: the FIRST post-auth message after a reconnect
     must be ``KEY_RESET_MODIFIERS(reason='reconnect')``. The full wire
     loopback lands in 02-11 (test_reconnect_first_message_is_key_reset_modifiers);
     this file pins the contract via the supervisor's post-auth hook.
  3. Held-chord invariant: ``_should_fire_periodic_reset`` MUST NOT fire
     while ``last_had_modifiers=True``, OR every Flame paint stroke
     (Ctrl+Shift+drag, Ctrl+Alt+drag) gets its modifiers ripped out.
"""
from __future__ import annotations

import asyncio
import os

import pytest

# Importorskip pattern: the focus-stress portion needs PySide6, the
# held-chord and reconnect portions don't. The qapp fixture handles
# graceful skip if PySide6 isn't installed.

try:
    import PySide6.QtWidgets  # noqa: F401
    HAS_PYSIDE6 = True
except Exception:
    HAS_PYSIDE6 = False


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication (offscreen)."""
    if not HAS_PYSIDE6:
        pytest.skip("PySide6 not available")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


# --------------------------------------------------------------------------
# Focus-stress: focusOutEvent → reset_modifiers_requested('focus_out')
# --------------------------------------------------------------------------


def test_focus_out_triggers_server_reset_modifiers(qapp):
    """INPUT-02 — focus-out emits the wire signal that drives server reset."""
    from client.viewer import RemoteViewer
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent

    v = RemoteViewer()
    received: list[str] = []
    v.reset_modifiers_requested.connect(received.append)

    # Synthesize a real Qt focus-out — the actual code path the supervisor
    # would trigger when the viewer loses focus to the OS.
    v.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))

    assert received == ["focus_out"]


# --------------------------------------------------------------------------
# Reconnect contract: post-auth hook fires KEY_RESET_MODIFIERS(reason=reconnect)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_post_auth_hook_emits_reset_modifiers():
    """INPUT-03 — supervisor reconnect hook fires reason='reconnect'.

    Drives ConnectionSupervisor with a transport_factory that fails once
    (forces a retry), then succeeds. After the retry, the post_auth_hook
    must have fired exactly once with reason='reconnect'.
    """
    from client.connection_supervisor import ConnectionSupervisor

    hook_calls: list[str] = []

    async def _hook(reason: str) -> None:
        hook_calls.append(reason)

    attempts = {"n": 0}

    async def _factory() -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            # First connect fails → triggers retry → reconnect hook fires
            raise RuntimeError("simulated transport drop")
        # Second connect "succeeds" — return cleanly.

    sup = ConnectionSupervisor(
        _factory,
        max_retries=3,
        base_delay=0.001,
        max_delay=0.001,
        jitter_pct=0.0,
        post_auth_hook=_hook,
    )
    await asyncio.wait_for(sup.connect(), timeout=2.0)

    assert hook_calls == ["reconnect"], (
        f"reconnect hook must fire once with reason='reconnect'; "
        f"got {hook_calls!r}"
    )
    # First connect must NOT trigger the hook — only retries do.
    assert attempts["n"] == 2, f"expected exactly 2 transport attempts, got {attempts['n']}"


# --------------------------------------------------------------------------
# Held-chord invariant: periodic safety net MUST NOT break a held chord.
# --------------------------------------------------------------------------


def test_periodic_safety_net_does_not_break_held_chord():
    """Pitfall 6 — verify _should_fire_periodic_reset honors the chord guard.

    Three boundary cases the safety-net loop calls every 2s:
      - quiet + no chord       → fire (TRUE)
      - quiet + chord held     → DO NOT fire (FALSE) — Pitfall 6
      - active + no chord      → DO NOT fire (FALSE)
    """
    from server.session_runtime import _should_fire_periodic_reset

    assert _should_fire_periodic_reset(
        now=100.0, last_event=85.0, last_had_modifiers=False,
    ) is True

    assert _should_fire_periodic_reset(
        now=100.0, last_event=85.0, last_had_modifiers=True,
    ) is False, (
        "MUST NOT fire while a chord is held — would break Flame paint strokes"
    )

    assert _should_fire_periodic_reset(
        now=100.0, last_event=95.0, last_had_modifiers=False,
    ) is False, "MUST NOT fire under 10s of keyboard quiet"


def test_periodic_safety_net_threshold_is_inclusive_at_10s():
    """Boundary check: exactly 10s qualifies as 'quiet'."""
    from server.session_runtime import _should_fire_periodic_reset
    assert _should_fire_periodic_reset(
        now=100.0, last_event=90.0, last_had_modifiers=False,
    ) is True


# --------------------------------------------------------------------------
# Wire-format sanity: KeyResetModifiersMsg + TextCommitMsg JSON round-trip.
# --------------------------------------------------------------------------


def test_key_reset_modifiers_wire_round_trip():
    """KEY_RESET_MODIFIERS message survives JSON round-trip end-to-end."""
    from common.messages import KeyResetModifiersMsg, MsgType, parse_message
    msg = KeyResetModifiersMsg(reason="reconnect")
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.KEY_RESET_MODIFIERS
    assert parsed["reason"] == "reconnect"


def test_text_commit_wire_round_trip():
    """TEXT_COMMIT message survives JSON round-trip with Unicode payload."""
    from common.messages import TextCommitMsg, MsgType, parse_message
    msg = TextCommitMsg(text="\u3042")  # JP hiragana 'a' — D-15 test surface
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.TEXT_COMMIT
    assert parsed["text"] == "\u3042"
