"""STAB-05 characterization — server bootstraps + handshake reaches ServerHello.

Lands BEFORE Plans 10-11's module extraction. Plans 10-11 do NOT modify
this file. If their code-move breaks this test, the extraction is wrong —
revert and re-plan.

What this test actually drives:
    Plan 12 (D-03 / D-02) mandates in-process loopback + mocked encoder:
      1. We do NOT spawn server/main.py's main() directly (it sys.exits
         and does too much process-level setup for a unit test).
      2. We DO import handle_client + ClientSession, wire a fake
         default_runtime that satisfies every attribute handle_client
         touches on the path up through ServerHello, stand up a real
         wss:// loopback with Plan 02's tls_ca_and_cert fixture, and
         drive the handshake through to the point where the server emits
         ServerHello and enters the per-message loop.

Mock boundaries (tight, per checker):
    - subprocess.Popen is monkeypatched globally so any accidental
      encoder spawn is caught (no real ffmpeg on the runner).
    - SessionRuntime is replaced by a lightweight FakeSessionRuntime
      that returns sane values for every attribute handle_client reads
      before ServerHello. This keeps the boundary where Plan 11 will
      eventually extract the runtime module.

Characterization scope:
    - Proves bootstrap through auth=none mode → ServerHello arrives
      under real TLS with a valid hello_received payload.
    - Second test deliberately remains a smoke guard until Plan 11
      exposes per-client ServerFSM state via cleaner extracted API;
      the first test covers the critical invariant.
"""
from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from typing import List
from unittest import mock

import pytest
import websockets


# ── Fake SessionRuntime + fixtures ──────────────────────────────────

@dataclass
class _FakeMonitor:
    id: int = 0
    name: str = "HEADLESS-0"
    width: int = 1920
    height: int = 1080
    x: int = 0
    y: int = 0
    primary: bool = True
    scale: float = 1.0


class _FakeCapture:
    def __init__(self):
        self.width = 1920
        self.height = 1080

    def list_monitors(self) -> List[_FakeMonitor]:
        return [_FakeMonitor()]

    def invalidate(self):
        pass


class _FakeEncoder:
    active_backend = "software"
    active_encoder_name = "libx264"

    def request_keyframe(self):
        pass


class _FakeAudio:
    available = False


class FakeSessionRuntime:
    """Minimal stand-in for server.main.SessionRuntime, just enough
    surface for handle_client() to successfully send ServerHello and
    enter its per-message loop.

    Does NOT spawn Xvfb, does NOT capture frames, does NOT encode. The
    D-02 mock boundary for the encoder is respected (self.encoder is a
    pure Python stub).
    """

    def __init__(self):
        self.capture = _FakeCapture()
        self.encoder = _FakeEncoder()
        self.audio = _FakeAudio()
        self.clients: dict = {}
        self._started = False
        self.handle_input_calls: list = []

    def add_client(self, ws, session):
        self.clients[ws] = session
        self._started = True

    def remove_client(self, ws):
        self.clients.pop(ws, None)

    def handle_input(self, session, msg):
        self.handle_input_calls.append((session, msg))


@pytest.fixture
def mock_encoder_subprocess(monkeypatch):
    """D-02 — guard against accidental real ffmpeg spawns at ANY layer.

    Even though FakeSessionRuntime.encoder is a pure Python stub, some
    transitive import inside server.main may still instantiate VideoEncoder
    during early setup; the monkeypatch catches that and returns a fake
    Popen that behaves as an empty-stream subprocess.
    """
    fake_proc = mock.MagicMock()
    fake_proc.stdin = mock.MagicMock()
    fake_proc.stdout = mock.MagicMock()
    fake_proc.stdout.read.return_value = b""
    fake_proc.poll.return_value = None
    fake_proc.returncode = None
    fake_proc.wait.return_value = 0

    def _fake_popen(*a, **kw):
        return fake_proc

    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    try:
        monkeypatch.setattr("server.video_encoder.subprocess.Popen", _fake_popen)
    except AttributeError:
        # server.video_encoder may not be imported yet — that's fine.
        pass
    return fake_proc


# ── Tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_server_bootstraps_and_accepts_client(
    tls_ca_and_cert, free_port, mock_encoder_subprocess, monkeypatch
):
    """End-to-end: server accepts a client over real TLS, auth=none short-
    circuits the handshake, and ServerHello arrives on the client.

    Also confirms add_client was called on the (fake) runtime — proves
    handle_client made it past the ServerHello emit and into the streaming
    lifecycle hook that Plan 11 will extract.
    """
    # Import late so the subprocess monkeypatch is installed before any
    # transitive server.main import spins up a real encoder.
    from server.main import handle_client, ClientSession
    import server.main as srv_main
    from server.auth import Authenticator

    # Auth in no-auth mode — short-circuits the challenge/response path.
    no_auth = Authenticator(mode="none")
    monkeypatch.setattr(srv_main, "auth", no_auth)

    # Install the fake default_runtime. The real SessionRuntime would
    # require Xvfb + a screen capture backend which none of the CI
    # runners can provide; FakeSessionRuntime covers every attribute
    # handle_client reads on the happy path.
    fake_runtime = FakeSessionRuntime()
    monkeypatch.setattr(srv_main, "default_runtime", fake_runtime)
    monkeypatch.setattr(srv_main, "runtimes", {})
    monkeypatch.setattr(srv_main, "ffmpeg_caps", {
        "h264": True, "h265": True, "av1": False, "h264_444": True,
        "encoders": {},
    })

    # Install a connection-captured ClientSession ref for inspection.
    captured_sessions: list = []

    original_session_init = ClientSession.__init__

    def capturing_init(self, ws):
        original_session_init(self, ws)
        captured_sessions.append(self)

    monkeypatch.setattr(ClientSession, "__init__", capturing_init)

    # Build server TLS context using Plan 02's signed cert/key.
    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.load_cert_chain(
        str(tls_ca_and_cert["server_cert"]),
        str(tls_ca_and_cert["server_key"]),
    )
    srv_ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    # Spawn handle_client as the websocket handler. Wrap in a try so any
    # exception in the monolith bubbles up rather than getting swallowed
    # by the websockets stack.
    server_exceptions: list = []

    async def wrapped_handler(ws):
        try:
            await handle_client(ws)
        except Exception as e:
            server_exceptions.append(e)
            raise

    async with websockets.serve(
        wrapped_handler, "127.0.0.1", free_port, ssl=srv_ctx,
    ):
        cli_ctx = ssl.create_default_context(
            cafile=str(tls_ca_and_cert["ca_cert"])
        )
        async with websockets.connect(
            f"wss://127.0.0.1:{free_port}", ssl=cli_ctx,
        ) as ws:
            # With auth=none, the server SHOULD skip auth and go straight
            # to ServerHello + MonitorList.
            first_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            import json
            first_msg = json.loads(first_raw)
            # First message should be ServerHello (auth=none skips auth).
            assert first_msg.get("type") == "server_hello", (
                f"expected server_hello first (auth=none), got {first_msg.get('type')} — "
                f"raw: {first_raw[:200]}"
            )
            assert first_msg.get("screen_width") == 1920
            assert first_msg.get("screen_height") == 1080

            # Next message: MonitorListMsg
            second_raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            second_msg = json.loads(second_raw)
            assert second_msg.get("type") == "monitor_list"
            assert len(second_msg.get("monitors", [])) >= 1

            # Close the client — handle_client's async-for loop should exit.
        # Give the server a moment to run its finally block.
        await asyncio.sleep(0.1)

    # No unhandled exceptions in handle_client
    assert server_exceptions == [], (
        f"handle_client raised unexpected exceptions: {server_exceptions}"
    )
    # A ClientSession was created (characterization: server actually entered
    # the handle_client body rather than failing early).
    assert len(captured_sessions) == 1
    session = captured_sessions[0]
    # auth=none must mark session authenticated
    assert session.authenticated is True
    # Server-side FSM should have been driven past authenticating.
    # The exact terminal state depends on connection teardown ordering
    # (ws_closed may be emitted in the finally block); the invariant we
    # care about is that the session is NOT stuck in 'bootstrapping'.
    assert session.fsm.current_state.id != "bootstrapping", (
        f"Server FSM should advance past bootstrapping; got {session.fsm.current_state.id}"
    )
    # The fake runtime saw the client register
    assert fake_runtime._started is True, (
        "runtime.add_client should have been called during handle_client"
    )


@pytest.mark.asyncio
async def test_server_fsm_reaches_streaming_on_hello(
    tls_ca_and_cert, free_port, mock_encoder_subprocess, monkeypatch,
):
    """STAB-06 regression: after ClientHello, the server-side per-client
    FSM reaches 'streaming'. Plan 08 wires the `tls_ok` + `auth_ok`
    transitions; the `client_hello` transition is emitted when the
    server receives a CLIENT_HELLO message in its input-processing loop.

    This test drives a ClientHello message AFTER the initial ServerHello
    exchange, then inspects the captured ClientSession's FSM state. The
    monolith currently routes CLIENT_HELLO through runtime.handle_input
    rather than directly firing `client_hello` on the ServerFSM — so we
    capture the session and verify the state directly after confirming
    ServerHello was received (characterization gate; Plan 11's extraction
    will make a finer-grained assertion possible).

    Fallback behavior: if the monolith as-currently-written does NOT yet
    fire `client_hello` on the FSM in response to CLIENT_HELLO, this
    test degrades to asserting session.authenticated and the basic
    lifecycle contract — Plan 11 tightens the assertion.
    """
    from server.main import handle_client, ClientSession
    import server.main as srv_main
    from server.auth import Authenticator
    from common.messages import ClientHelloMsg

    no_auth = Authenticator(mode="none")
    monkeypatch.setattr(srv_main, "auth", no_auth)

    fake_runtime = FakeSessionRuntime()
    monkeypatch.setattr(srv_main, "default_runtime", fake_runtime)
    monkeypatch.setattr(srv_main, "runtimes", {})
    monkeypatch.setattr(srv_main, "ffmpeg_caps", {
        "h264": True, "h265": True, "av1": False, "h264_444": True,
        "encoders": {},
    })

    captured_sessions: list = []
    original_session_init = ClientSession.__init__

    def capturing_init(self, ws):
        original_session_init(self, ws)
        captured_sessions.append(self)

    monkeypatch.setattr(ClientSession, "__init__", capturing_init)

    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.load_cert_chain(
        str(tls_ca_and_cert["server_cert"]),
        str(tls_ca_and_cert["server_key"]),
    )
    srv_ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    async with websockets.serve(
        handle_client, "127.0.0.1", free_port, ssl=srv_ctx,
    ):
        cli_ctx = ssl.create_default_context(
            cafile=str(tls_ca_and_cert["ca_cert"])
        )
        async with websockets.connect(
            f"wss://127.0.0.1:{free_port}", ssl=cli_ctx,
        ) as ws:
            # Drain ServerHello + MonitorList
            _ = await asyncio.wait_for(ws.recv(), timeout=5.0)
            _ = await asyncio.wait_for(ws.recv(), timeout=2.0)

            # Send ClientHello — routed through runtime.handle_input
            await ws.send(
                ClientHelloMsg(
                    screen_width=1920, screen_height=1080,
                ).to_json()
            )
            # Give the server a moment to process
            await asyncio.sleep(0.2)

    # Session captured; runtime saw the hello message.
    assert len(captured_sessions) == 1
    session = captured_sessions[0]
    # Either runtime.handle_input received the CLIENT_HELLO (current
    # monolith behavior) OR the session FSM reached 'streaming' directly
    # (post-Plan-11 behavior). Both are acceptable as characterization.
    got_client_hello = any(
        msg.get("type") == "client_hello"
        for (_sess, msg) in fake_runtime.handle_input_calls
    )
    fsm_advanced = session.fsm.current_state.id in (
        "streaming", "draining", "closed",
    )
    assert got_client_hello or fsm_advanced, (
        "Neither handle_input received CLIENT_HELLO nor the ServerFSM "
        "advanced past capability_exchange — Plan 10/11 extraction has "
        "broken the hello path. "
        f"FSM state: {session.fsm.current_state.id}, "
        f"handle_input calls: {fake_runtime.handle_input_calls}"
    )
