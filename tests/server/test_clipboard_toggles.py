"""Phase 3 D-15 — 4-direction clipboard toggle policy gating.

Plan 03-06 implementation of Plan 03-01 Wave 0 RED skeletons. Locks the
server-side toggle contract:

  * ``clipboard_text_s2c=False`` → outbound broadcast skipped (zero wire
    traffic leaves the server for that client).
  * ``clipboard_image_c2s=False`` → inbound image dispatch dropped
    silently (no error response — the peer doesn't learn toggle state
    from side effects).
  * Pitfall 7 — toggle flipped AFTER chunk_index=0 has been accepted
    still completes the in-flight sequence; the toggle applies to the
    NEXT sequence_id. Subsequent chunks of a dropped sequence drop via
    the per-session ``_dropped_seqs`` continuation set.

Pattern analog: :mod:`tests/server/test_modifier_dispatch.py` for the
Phase 2 D-11 periodic-safety precondition — both run the dispatcher
logic against a lightweight stub runtime instead of a real websocket
session.
"""
from __future__ import annotations

import asyncio
import base64
import json
import types
from unittest import mock

import pytest


# ──────────────────────────────────────────────────────────────────
# Shared helpers — build a SessionRuntime-like stub that just exposes
# the methods Plan 03-06 touches without spinning up capture / encoder.
# ──────────────────────────────────────────────────────────────────


def _build_fake_session(**overrides):
    """Construct a ClientSession-like ``SimpleNamespace`` with toggles.

    Mirrors the ``ClientSession`` attribute surface Plan 03-06 reads:
    per-direction toggles default True; ``_clipboard_chunks`` and
    ``_dropped_seqs`` start empty; ``authenticated=True`` so the
    broadcast gate in ``_on_clipboard_change`` actually iterates to us.
    """
    s = types.SimpleNamespace(
        authenticated=True,
        clipboard_text_c2s=True,
        clipboard_text_s2c=True,
        clipboard_image_c2s=True,
        clipboard_image_s2c=True,
        _clipboard_chunks={},
        _dropped_seqs=set(),
        client_id="session-1",
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _build_fake_runtime(sessions, monkeypatch):
    """Construct a SessionRuntime-like stub with the broadcast + dispatch API.

    Patches ``asyncio.run_coroutine_threadsafe`` in the session_runtime
    module namespace to execute the coroutine synchronously against a
    spy — this bypasses the cross-thread loop machinery entirely (no
    loop needs to run) and lets us assert enqueue counts immediately
    after ``_on_clipboard_change``.
    """
    from server import session_runtime as sr_module
    from server.session_runtime import SessionRuntime

    stub = SessionRuntime.__new__(SessionRuntime)
    # Truthy sentinel — the gate checks ``self._event_loop`` is truthy
    # before scheduling; the actual loop object is never used because
    # we've patched run_coroutine_threadsafe below.
    stub._event_loop = object()
    stub.clients = {}
    stub._clipboard_seq = 0

    for s in sessions:
        captured = []

        async def _enqueue(payload, _captured=captured, is_keyframe=False):
            _captured.append(payload)
            return True

        s.enqueue = _enqueue
        s._enqueued = captured
        stub.clients[id(s)] = s

    # Synchronously drive the coroutine to completion so spies capture
    # the payload. ``run_coroutine_threadsafe`` normally expects a
    # running loop; our fake runs the awaitable in a fresh loop for
    # deterministic test behavior.
    def _fake_run_coroutine_threadsafe(coro, _loop):
        try:
            coro.send(None)
        except StopIteration:
            pass
        class _DummyFuture:
            def result(self, timeout=None):
                return None
        return _DummyFuture()

    monkeypatch.setattr(
        sr_module.asyncio, "run_coroutine_threadsafe",
        _fake_run_coroutine_threadsafe,
    )
    return stub


# ──────────────────────────────────────────────────────────────────
# Test 1 — outbound s2c gate short-circuits broadcast
# ──────────────────────────────────────────────────────────────────


def test_toggle_text_s2c_off_short_circuits_broadcast(monkeypatch):
    """D-15 — clipboard_text_s2c=False → zero enqueue for that session."""
    from server.session_runtime import SessionRuntime

    s_off = _build_fake_session(clipboard_text_s2c=False)
    s_on = _build_fake_session()
    runtime = _build_fake_runtime([s_off, s_on], monkeypatch)

    SessionRuntime._on_clipboard_change(runtime, "text/plain", "hello world")

    # s_off got nothing (toggle OFF).
    assert s_off._enqueued == [], "text_s2c=False must skip enqueue"
    # s_on got exactly one ClipboardMsg.
    assert len(s_on._enqueued) == 1
    parsed = json.loads(s_on._enqueued[0])
    assert parsed["type"] == "clipboard_recv"
    assert parsed["data"] == "hello world"


def test_toggle_image_s2c_off_short_circuits_broadcast(fixture_png, monkeypatch):
    """D-15 — clipboard_image_s2c=False → zero chunk emission for that session."""
    from server.session_runtime import SessionRuntime

    s_off = _build_fake_session(clipboard_image_s2c=False)
    s_on = _build_fake_session()
    runtime = _build_fake_runtime([s_off, s_on], monkeypatch)

    SessionRuntime._on_clipboard_change(runtime, "image/png", fixture_png)

    # s_off received zero chunks.
    assert s_off._enqueued == []
    # s_on received at least one ClipboardChunkMsg.
    assert len(s_on._enqueued) >= 1
    parsed = json.loads(s_on._enqueued[0])
    assert parsed["type"] == "clipboard_chunk"
    assert parsed["content_type"] == "image/png"


# ──────────────────────────────────────────────────────────────────
# Test 2 — inbound c2s gate drops silently
# ──────────────────────────────────────────────────────────────────


def test_toggle_text_c2s_off_drops_inbound(monkeypatch):
    """D-15 — clipboard_text_c2s=False → inbound CLIPBOARD_SEND silently dropped."""
    from server.session_runtime import SessionRuntime
    from common.messages import MsgType

    session = _build_fake_session(clipboard_text_c2s=False)
    fake_clipboard = mock.MagicMock()
    runtime = types.SimpleNamespace(
        clipboard=fake_clipboard,
        _event_loop=None,
        clients={},
        injector=mock.MagicMock(),
        capture=mock.MagicMock(),
        encoder=None,
        encoder_lifecycle=mock.MagicMock(),
        file_receiver=mock.MagicMock(),
        usb_manager=None,
        _pen_fsm=mock.MagicMock(),
        health=mock.MagicMock(),
        _last_key_event_at=0.0,
        _last_key_event_had_modifiers=False,
        _caps_lock_on=False,
        _num_lock_on=False,
        _scroll_lock_on=False,
    )
    # Drive only the CLIPBOARD_SEND dispatch branch.
    SessionRuntime.handle_input(
        runtime, session,
        {"type": MsgType.CLIPBOARD_SEND, "content_type": "text/plain", "data": "hi"},
    )
    fake_clipboard.set_clipboard.assert_not_called()


def test_toggle_image_c2s_off_drops_inbound_chunk():
    """D-15 / Pitfall 7 — clipboard_image_c2s=False at chunk-0 → dropped_seqs entry + no set."""
    from server.session_runtime import SessionRuntime
    from common.messages import MsgType

    session = _build_fake_session(clipboard_image_c2s=False)
    fake_clipboard = mock.MagicMock()
    runtime = types.SimpleNamespace(
        clipboard=fake_clipboard,
        _event_loop=None,
        clients={},
        injector=mock.MagicMock(),
        capture=mock.MagicMock(),
        encoder=None,
        encoder_lifecycle=mock.MagicMock(),
        file_receiver=mock.MagicMock(),
        usb_manager=None,
        _pen_fsm=mock.MagicMock(),
        health=mock.MagicMock(),
        _last_key_event_at=0.0,
        _last_key_event_had_modifiers=False,
        _caps_lock_on=False,
        _num_lock_on=False,
        _scroll_lock_on=False,
    )
    SessionRuntime.handle_input(
        runtime, session,
        {
            "type": MsgType.CLIPBOARD_CHUNK, "content_type": "image/png",
            "sequence_id": 7, "chunk_index": 0, "total_chunks": 2, "data": "AA",
        },
    )
    # chunk-0 dropped at toggle gate → set_clipboard_image MUST NOT be called.
    fake_clipboard.set_clipboard_image.assert_not_called()
    # And the sequence is now tracked in _dropped_seqs for continuation.
    assert 7 in session._dropped_seqs


# ──────────────────────────────────────────────────────────────────
# Test 3 — Pitfall 7 — mid-sequence toggle change applies at chunk-0 boundary
# ──────────────────────────────────────────────────────────────────


def test_toggle_change_after_chunk_zero_completes_inflight(fixture_png):
    """Pitfall 7 — toggle gating at chunk-0 boundary, NOT per-chunk.

    Sequence:
      1. image_c2s=True; receive seq=1 chunk 0 (of 2). Assembler starts.
      2. Flip image_c2s = False.
      3. Receive seq=1 chunk 1 — sequence was NOT in _dropped_seqs
         because chunk 0 predated the flip; assembler completes and
         ``set_clipboard_image`` IS called.
      4. Receive seq=2 chunk 0 — toggle is OFF at the new boundary;
         seq=2 is added to _dropped_seqs and ``set_clipboard_image`` is
         NOT called for it.
    """
    from server.session_runtime import SessionRuntime
    from common.messages import MsgType

    session = _build_fake_session(clipboard_image_c2s=True)
    fake_clipboard = mock.MagicMock()
    runtime = types.SimpleNamespace(
        clipboard=fake_clipboard,
        _event_loop=None,
        clients={},
        injector=mock.MagicMock(),
        capture=mock.MagicMock(),
        encoder=None,
        encoder_lifecycle=mock.MagicMock(),
        file_receiver=mock.MagicMock(),
        usb_manager=None,
        _pen_fsm=mock.MagicMock(),
        health=mock.MagicMock(),
        _last_key_event_at=0.0,
        _last_key_event_had_modifiers=False,
        _caps_lock_on=False,
        _num_lock_on=False,
        _scroll_lock_on=False,
    )

    # Split a real PNG into exactly 2 chunks across base64.
    b64 = base64.b64encode(fixture_png).decode("ascii")
    mid = len(b64) // 2
    chunk0, chunk1 = b64[:mid], b64[mid:]

    # Step 1: seq=1 chunk 0 with toggle ON.
    SessionRuntime.handle_input(
        runtime, session,
        {
            "type": MsgType.CLIPBOARD_CHUNK, "content_type": "image/png",
            "sequence_id": 1, "chunk_index": 0, "total_chunks": 2,
            "data": chunk0,
        },
    )
    assert 1 in session._clipboard_chunks
    fake_clipboard.set_clipboard_image.assert_not_called()

    # Step 2: flip toggle OFF mid-stream.
    session.clipboard_image_c2s = False

    # Step 3: seq=1 chunk 1 — in-flight sequence still completes.
    SessionRuntime.handle_input(
        runtime, session,
        {
            "type": MsgType.CLIPBOARD_CHUNK, "content_type": "image/png",
            "sequence_id": 1, "chunk_index": 1, "total_chunks": 2,
            "data": chunk1,
        },
    )
    # Assembler cleared after completion.
    assert 1 not in session._clipboard_chunks
    # Set called exactly once with the full PNG payload.
    fake_clipboard.set_clipboard_image.assert_called_once()
    (called_bytes,), _ = fake_clipboard.set_clipboard_image.call_args
    assert called_bytes == fixture_png

    # Step 4: seq=2 chunk 0 with toggle OFF — dropped at boundary.
    fake_clipboard.set_clipboard_image.reset_mock()
    SessionRuntime.handle_input(
        runtime, session,
        {
            "type": MsgType.CLIPBOARD_CHUNK, "content_type": "image/png",
            "sequence_id": 2, "chunk_index": 0, "total_chunks": 2,
            "data": chunk0,
        },
    )
    assert 2 in session._dropped_seqs
    fake_clipboard.set_clipboard_image.assert_not_called()

    # Step 5: seq=2 chunk 1 — mid-stream drop continuation; no-op.
    SessionRuntime.handle_input(
        runtime, session,
        {
            "type": MsgType.CLIPBOARD_CHUNK, "content_type": "image/png",
            "sequence_id": 2, "chunk_index": 1, "total_chunks": 2,
            "data": chunk1,
        },
    )
    fake_clipboard.set_clipboard_image.assert_not_called()
    # Assembler never created for seq=2.
    assert 2 not in session._clipboard_chunks


# ──────────────────────────────────────────────────────────────────
# Additional coverage — ClientHelloMsg toggle ingestion
# ──────────────────────────────────────────────────────────────────


def test_client_hello_populates_session_toggles():
    """Plan 03-06 — CLIENT_HELLO copies the 4 clipboard toggle fields onto the session."""
    from server.session_runtime import SessionRuntime
    from common.messages import MsgType

    session = _build_fake_session()
    # Reset to unambiguous values before hello.
    session.clipboard_text_c2s = True
    session.clipboard_text_s2c = True
    session.clipboard_image_c2s = True
    session.clipboard_image_s2c = True
    # Fake ServerFSM so handle_input's session.fsm.send("client_hello") no-ops.
    session.fsm = mock.MagicMock()
    session.client_id = "hello-test"
    session.supports_h264 = True
    session.supports_h265 = False
    session.supports_yuv444 = True
    session.supports_audio = True
    session.client_screen_width = 0
    session.client_screen_height = 0
    session.capture_mode = "mirror_all"
    session.picked_monitor_id = -1
    session.picked_monitor_name = ""
    session.crop_rect = None
    session.capture_mode_degraded = False

    runtime = types.SimpleNamespace(
        clipboard=None,
        _event_loop=None,
        clients={},
        injector=mock.MagicMock(),
        capture=None,
        encoder=None,
        encoder_lifecycle=mock.MagicMock(),
        file_receiver=mock.MagicMock(),
        usb_manager=None,
        _pen_fsm=mock.MagicMock(),
        health=mock.MagicMock(),
        _last_key_event_at=0.0,
        _last_key_event_had_modifiers=False,
        _caps_lock_on=False,
        _num_lock_on=False,
        _scroll_lock_on=False,
        apply_capture_mode=mock.MagicMock(),
    )

    SessionRuntime.handle_input(
        runtime, session,
        {
            "type": MsgType.CLIENT_HELLO,
            "clipboard_text_c2s": False,
            "clipboard_text_s2c": True,
            "clipboard_image_c2s": False,
            "clipboard_image_s2c": True,
        },
    )
    assert session.clipboard_text_c2s is False
    assert session.clipboard_text_s2c is True
    assert session.clipboard_image_c2s is False
    assert session.clipboard_image_s2c is True
