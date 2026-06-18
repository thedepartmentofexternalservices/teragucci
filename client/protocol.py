"""
Client-side WebSocket protocol handler v2.

Manages the connection to the Teraguchi server with:
- PAM authentication (send credentials directly for system auth)
- Legacy challenge-response authentication
- Hybrid TCP+UDP transport (UDP for video/audio, TCP for control)
- TLS support (wss://) — recommended for PAM mode
- Auto-reconnect with exponential backoff
- Jitter buffer for smooth UDP playback
"""

import hashlib
import json
import logging
import socket
import threading
import asyncio
import time
from typing import Optional, Callable

import websockets

from common.messages import (
    MsgType, ClientHelloMsg, FrameType,
    decode_video_header, decode_jpeg_header, decode_audio_header,
    VIDEO_HEADER_SIZE, JPEG_HEADER_SIZE, AUDIO_HEADER_SIZE,
    HealthPing, HealthPong, QualitySettings,
    AuthResponse, parse_message,
    # Phase 2 D-11 / D-15 wire messages — see common/messages.py.
    KeyResetModifiersMsg, TextCommitMsg,
    # Phase 3 D-05 — input messages carrying server_x / server_y.
    MouseMoveMsg, MouseButtonMsg, MouseScrollMsg, PenEventMsg,
    # Phase 3 D-17 — chunked clipboard wire envelope (Plan 03-06/03-07).
    ClipboardChunkMsg,
)
from common.keymap import swap_cmd_ctrl_for_linux_dest
from common.session_fsm import ClientFSM
from client.connection_supervisor import ConnectionSupervisor


# ---------------------------------------------------------------------------
# Phase 2 D-14 — Mac-first lock-state probe.
#
# Every outbound KeyEventMsg carries caps_lock_on / num_lock_on /
# scroll_lock_on bits so the server can auto-correct its virtual display's
# lock state on mismatch (zero round-trip cost; converges on the next
# keystroke). Mac probe uses NSEvent.modifierFlags() — the canonical
# Carbon/Cocoa source. Linux client is out of scope for v1 per PROJECT.md
# but we keep the interface symmetrical so the server-side handler is
# platform-agnostic.
# ---------------------------------------------------------------------------

def _probe_lock_state() -> tuple[bool, bool, bool]:
    """Return (caps_lock_on, num_lock_on, scroll_lock_on).

    All three default to False on probe failure — the server's auto-
    correct path treats False as "lock off" and will release the lock
    if it was held. That's the safer direction: stuck-lock release on a
    probe miss is recoverable; phantom-lock-on is not.
    """
    try:
        from AppKit import NSEvent  # type: ignore[import-not-found]
        mods = int(NSEvent.modifierFlags())
        # NSEventModifierFlagCapsLock = 1<<16 = 0x10000
        # NSEventModifierFlagNumericPad = 1<<21 = 0x200000 (numpad-origin
        # event, NOT NumLock state — macOS does not expose NumLock state
        # via NSEvent because Mac keyboards have no NumLock indicator).
        caps_lock = bool(mods & 0x10000)
        num_lock = bool(mods & 0x200000)
        # Scroll Lock not exposed by NSEvent at all; default False.
        scroll_lock = False
        return caps_lock, num_lock, scroll_lock
    except Exception:
        return False, False, False


class _BrokerRedirect(Exception):
    """Raised when direct-mode auth detects a broker and needs to redirect."""
    pass
from common.udp_transport import UDPMediaClient, CHANNEL_VIDEO, CHANNEL_AUDIO, FLAG_KEYFRAME
from common.hybrid_transport import HybridClientTransport, TransportMsg
from common.jitter_buffer import JitterBuffer

logger = logging.getLogger(__name__)


# Phase 3 WR-02 — upper bound on _dropped_seqs cardinality. Prevents
# soft-DoS memory growth from attacker-controlled peers spamming
# chunk-0 ClipboardChunkMsg with random sequence_ids. On overflow we
# pop() an arbitrary entry — CPython set pop() drains in hash order,
# not insertion order, so this is NOT strict FIFO, but the security
# invariant is "bounded size" not ordering. 4096 entries ~= 32 KiB at
# 8 bytes/int — well below any realistic chunk lifetime window.
_DROPPED_SEQS_MAX = 4096


# ---------------------------------------------------------------------------
# Phase 3 D-13 / D-14 / D-17 — clipboard transport constants (Plan 03-07).
#
# Mirror of the server-side constants in ``server/clipboard.py``. Kept in
# sync via the Plan 06/07 acceptance grep battery. Shared-constants module
# is deferred to v1.1 — both ends live in the same repo so the drift
# surface is reviewable at every commit boundary.
# ---------------------------------------------------------------------------
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_MAX_BYTES = 64 * 1024 * 1024   # D-14 oversize cap
CHUNK_BYTES = 1024 * 1024          # D-17 per-chunk target


class ClientProtocol:
    """
    Hybrid TCP+UDP client with TLS, auth, auto-reconnect, jitter buffer.

    Supports two auth modes:
    - PAM:   Sends username + password directly (over TLS-encrypted WebSocket)
    - Local: Challenge-response hash (legacy)
    """

    def __init__(self):
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected = False
        self._closing = False
        self._auto_reconnect = True
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0

        # Auth
        self._username = ""
        self._password = ""
        self._user_salt = ""

        # TLS
        self._use_tls = False
        # SEC-01: dev escape hatch. Double-gated with TERAGUCHI_ACCEPT_INSECURE=1.
        # Do not enable in production.
        self._insecure_skip_verify: bool = False
        self._ca_bundle: Optional[str] = None  # optional custom CA bundle path

        # Broker redirect
        self._broker_mode = False
        self._broker_token = ""
        self._redirect_host = ""
        self._redirect_port = 0
        self._preferred_machine = ""  # Remembered selection for reconnects
        self._machine_selection_future: Optional[asyncio.Future] = None

        # UDP transport
        self._udp_client: Optional[UDPMediaClient] = None
        self._hybrid: Optional[HybridClientTransport] = None
        self._jitter_buffer: Optional[JitterBuffer] = None
        self._udp_enabled = True
        self._udp_stats_interval = 5.0

        # Callbacks
        self.on_server_hello: Optional[Callable] = None
        self._screen_size: Optional[tuple] = None
        self.on_video_frame: Optional[Callable] = None
        self.on_jpeg_frame: Optional[Callable] = None
        self.on_audio_frame: Optional[Callable] = None
        self.on_connected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
        self.on_auth_required: Optional[Callable] = None
        self.on_auth_result: Optional[Callable] = None
        self.on_health_ping: Optional[Callable] = None
        self.on_health_stats: Optional[Callable] = None
        self.on_monitor_list: Optional[Callable] = None
        self.on_clipboard: Optional[Callable] = None
        self.on_file_response: Optional[Callable] = None  # file_accept/file_ack/file_cancel
        self.on_usb_response: Optional[Callable] = None  # usb_device_list/attached/detached/error
        self.on_broker_hello: Optional[Callable] = None  # broker_hello with machine list
        self.on_broker_assign: Optional[Callable] = None  # broker_assign with redirect info
        self.on_broker_machine_needed: Optional[Callable] = None  # prompts user to pick a machine
        self.on_cursor_update: Optional[Callable] = None  # cursor shape change

        # STAB-06 / Plan 01-08: per-connection FSM. current_state.id is
        # stamped onto every outbound HealthPing (see _client_health_ping_loop).
        # Transitions are driven from the connect / auth / hello / disconnect
        # lifecycle hooks below. Guarded sends — python-statemachine raises
        # TransitionNotAllowed on out-of-order events and we don't want that
        # to crash the I/O thread.
        self.fsm = ClientFSM()
        self.last_reported_server_state: str = ""
        self._ping_seq: int = 0

        # STAB-08 / Plan 01-12: ConnectionSupervisor reference. Full
        # replacement of ``_run_loop`` with supervisor.connect() is
        # deferred to a follow-up plan because the current reconnect
        # loop is interleaved with QThread event-loop bootstrap. For
        # now the supervisor is constructed lazily so callers can reach
        # it for is_reconnecting queries; the existing _run_loop
        # reconnect policy remains the active driver.
        # TODO(Plan-12 follow-up): replace _run_loop's while-loop with
        # ConnectionSupervisor.connect() and delete the duplicated
        # backoff math on lines 283-284.
        self._supervisor: Optional[ConnectionSupervisor] = None

        # Phase 2 D-10 — per-session Cmd<->Ctrl swap flag. Default OFF;
        # session.py flips it via set_swap_cmd_ctrl(profile.swap_cmd_ctrl)
        # at connect time. When True, send_key_event runs every key event
        # through swap_cmd_ctrl_for_linux_dest() before serializing.
        self._swap_cmd_ctrl: bool = False

        # Phase 3 D-01 / D-02 — capture-mode state baked into the outbound
        # ClientHelloMsg. Defaults preserve pre-Phase-3 behavior (mirror_all
        # = ship full virtual desktop unchanged). ``session.py`` flips via
        # ``set_capture_mode`` before calling ``protocol.connect`` so the
        # first hello carries the user's picked mode.
        self._capture_mode: str = "mirror_all"
        self._picked_monitor_id: int = -1
        self._picked_monitor_name: str = ""

        # Phase 3 D-15 / D-16 — per-direction clipboard toggles (Plan 03-07).
        # Defaults True preserve pre-Phase-3 always-on clipboard behavior
        # and match D-16's "secure defaults = all directions ON". session.py
        # flips via ``set_clipboard_toggles`` on bookmark connect so the
        # first hello carries the user's saved toggle state.
        self._clipboard_text_c2s: bool = True
        self._clipboard_text_s2c: bool = True
        self._clipboard_image_c2s: bool = True
        self._clipboard_image_s2c: bool = True

        # Phase 3 D-17 — per-protocol chunk reassembly state (Plan 03-07).
        # ``_clipboard_chunks`` maps sequence_id -> ClipboardChunkAssembler.
        # ``_dropped_seqs`` tracks sequences whose chunk-0 was gated off
        # so subsequent chunks for the same sequence silently no-op
        # (Pitfall 7 continuation on the client-inbound path).
        # WR-02: bounded to _DROPPED_SEQS_MAX entries to prevent soft DoS
        # from attacker-controlled peers spamming chunk-0 with random
        # sequence_ids over a long-lived connection.
        self._clipboard_chunks: dict = {}
        self._dropped_seqs: set = set()
        # Phase 3 D-17 — monotonic outbound sequence_id allocator.
        self._clipboard_seq: int = 0

        # Phase 3 D-14 — oversize-image toast bridge. session.py assigns a
        # GUI-thread callback taking a single float (size in MB). Absent
        # assignment, the oversize path just logs.
        self.on_oversize_image: Optional[Callable[[float], None]] = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def is_reconnecting(self) -> bool:
        """STAB-08: True while the ClientFSM is in 'reconnecting' state.

        Reads directly from ``self.fsm`` so it stays accurate even when
        the supervisor isn't attached (current behavior — see the TODO
        in __init__).
        """
        return self.fsm.current_state.id == "reconnecting"

    def set_insecure_skip_verify(self, enable: bool, ca_bundle: Optional[str] = None):
        """SEC-01: dev-only escape hatch. Double-gated with TERAGUCHI_ACCEPT_INSECURE=1.

        Setting this flag alone is NOT sufficient to disable TLS verification;
        the operating environment must also export TERAGUCHI_ACCEPT_INSECURE=1.
        Every use emits an ERROR-level log event (transport.insecure_mode_active).

        Do not call this from production code paths.
        """
        self._insecure_skip_verify = bool(enable)
        self._ca_bundle = ca_bundle

    def connect(self, host: str, port: int, username: str = "", password: str = "",
                use_tls: bool = False, auto_reconnect: bool = True):
        if self._thread and self._thread.is_alive():
            self.disconnect()

        self._closing = False
        self._broker_mode = False
        self._broker_token = ""
        self._redirect_host = ""
        self._redirect_port = 0
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._auto_reconnect = auto_reconnect
        self._reconnect_delay = 1.0
        self._host = host
        self._port = port

        self._thread = threading.Thread(
            target=self._run_loop, args=(host, port),
            daemon=True, name="teraguchi-client-io")
        self._thread.start()

    def connect_broker(self, host: str, port: int, username: str = "",
                       password: str = "", use_tls: bool = True,
                       auto_reconnect: bool = True):
        """Connect via broker. Authenticates with broker, gets assigned a machine,
        then redirects to that machine with a signed token."""
        if self._thread and self._thread.is_alive():
            self.disconnect()

        self._closing = False
        self._broker_mode = True
        self._broker_token = ""
        self._redirect_host = ""
        self._redirect_port = 0
        self._preferred_machine = ""
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._auto_reconnect = auto_reconnect
        self._reconnect_delay = 1.0
        self._host = host
        self._port = port

        self._thread = threading.Thread(
            target=self._run_loop, args=(host, port),
            daemon=True, name="teraguchi-broker-io")
        self._thread.start()

    def disconnect(self):
        self._closing = True
        self._auto_reconnect = False
        if self._hybrid:
            self._hybrid.stop()
            self._hybrid = None
        if self._jitter_buffer:
            self._jitter_buffer.stop()
            self._jitter_buffer = None
        self._udp_client = None
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._connected = False

    def send_input(self, msg_dict: dict):
        if not self._connected or not self._ws:
            return
        try:
            json_str = json.dumps(msg_dict)
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._ws.send(json_str), self._loop)
        except Exception as e:
            logger.debug("Send error: %s", e)

    def send_quality_settings(self, settings: QualitySettings):
        self.send_input(json.loads(settings.to_json()))

    def request_full_frame(self):
        self.send_input({"type": MsgType.REQUEST_FULL_FRAME})

    def select_monitor(self, monitor_id: int):
        self.send_input({"type": MsgType.SELECT_MONITOR, "monitor_id": monitor_id})

    def select_broker_machine(self, machine_name: str):
        """Resolve a pending broker machine selection from the GUI thread.

        Pass empty string to request auto-assignment. Safe to call from any thread.
        """
        loop = self._loop
        fut = self._machine_selection_future
        if loop is None or fut is None:
            # Either no selection pending, or handshake already moved on.
            # Still cache the preference for any next handshake pass.
            self._preferred_machine = machine_name or ""
            return

        def _resolve():
            if not fut.done():
                fut.set_result(machine_name or "")

        try:
            loop.call_soon_threadsafe(_resolve)
        except RuntimeError:
            # Loop stopped — ignore
            pass

    def send_clipboard(self, content_type, data=None):
        """Phase 3 D-13 / D-15 / D-17 — content-type-aware clipboard send.

        Dispatch contract:
          - ``send_clipboard("text/plain", text)`` — 1-shot for <=1MB,
            chunked via ``_send_chunked`` for >1MB. Gated on
            ``_clipboard_text_c2s``.
          - ``send_clipboard("image/png", png_bytes)`` — validates PNG
            magic + <=64MB cap (triggers ``on_oversize_image`` on
            overflow per D-14), base64-encodes, and ships chunked.
            Gated on ``_clipboard_image_c2s``.

        Backward-compat: legacy callers that passed a single ``str``
        (pre-Phase-3-07) are routed to the text path.
        """
        # Legacy single-arg (pre-Plan-07) compat: send_clipboard("hello")
        if data is None:
            data = content_type
            content_type = "text/plain"

        is_image = (content_type == "image/png")

        # D-15 outbound c2s gating at send-start (primary gate — per-chunk
        # re-check lives inside _send_chunked for the Pitfall 7 mid-stream
        # race.)
        if is_image:
            if not self._clipboard_image_c2s:
                return
        else:
            if not self._clipboard_text_c2s:
                return

        if is_image:
            if not isinstance(data, (bytes, bytearray)):
                logger.warning(
                    "clipboard.image_send_invalid_type type=%s",
                    type(data).__name__,
                )
                return
            size = len(data)
            if size > PNG_MAX_BYTES:
                logger.warning(
                    "clipboard.image_oversize size=%d cap=%d",
                    size, PNG_MAX_BYTES,
                )
                self._oversize_callback(size / (1024 * 1024))
                return
            if size < 8 or bytes(data[:8]) != PNG_MAGIC:
                logger.warning("clipboard.image_bad_magic size=%d", size)
                return
            import base64
            payload = base64.b64encode(bytes(data)).decode("ascii")
            self._send_chunked("image/png", payload)
            return

        # text/plain path
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        if len(data) <= CHUNK_BYTES:
            self.send_input({
                "type": MsgType.CLIPBOARD_SEND,
                "content_type": "text/plain",
                "data": data,
            })
        else:
            self._send_chunked("text/plain", data)

    def _send_chunked(self, content_type: str, payload: str) -> None:
        """Phase 3 D-17 — emit ClipboardChunkMsg per 1 MB chunk.

        W-6 Pitfall 7 mid-stream cancellation: the c2s toggle state is
        captured at sequence start AND re-checked per-chunk. If the user
        toggles the relevant c2s direction OFF mid-payload (e.g. they
        realize they are screen-sharing and disable client->server image
        paste while a 30 MB PNG is still chunking), the loop breaks
        immediately and the partial sequence is abandoned. The server's
        ``_dropped_seqs`` assembler will drop the orphan chunks at
        CHUNK_TIMEOUT_S; no extra wire signal is needed because
        absence-of-final-chunk + timeout is the contract.
        """
        is_image = (content_type == "image/png")
        # Snapshot toggle state at sequence start (Pitfall 7 boundary).
        c2s_at_start = (
            self._clipboard_image_c2s if is_image else self._clipboard_text_c2s
        )
        if not c2s_at_start:
            return  # double-defence; primary gate is in send_clipboard()

        seq = self._next_clipboard_seq()
        total = max(1, (len(payload) + CHUNK_BYTES - 1) // CHUNK_BYTES)
        for i in range(total):
            # Per-chunk re-check — mid-stream toggle-off cancels remaining.
            current_c2s = (
                self._clipboard_image_c2s if is_image else self._clipboard_text_c2s
            )
            if not current_c2s:
                logger.info(
                    "clipboard.chunk_send_cancelled_mid_stream seq=%d "
                    "sent_chunks=%d total=%d content_type=%s",
                    seq, i, total, content_type,
                )
                break  # abandon sequence; server drops orphans on timeout
            chunk = payload[i * CHUNK_BYTES : (i + 1) * CHUNK_BYTES]
            msg = ClipboardChunkMsg(
                sequence_id=seq,
                chunk_index=i,
                total_chunks=total,
                content_type=content_type,
                data=chunk,
            )
            self.send_input(json.loads(msg.to_json()))

    def _next_clipboard_seq(self) -> int:
        """Phase 3 D-17 — monotonic per-protocol clipboard sequence_id.

        Wraps at 32-bit to keep the wire integer bounded. Server-side
        assembler dedupes on ``sequence_id`` per client so the wrap is
        benign in practice. WR-04: the wrap collision window with
        ``_dropped_seqs`` is kept negligible by the WR-02 bounded-size
        cap on that set (see ``_track_dropped_seq``).
        """
        self._clipboard_seq = (self._clipboard_seq + 1) & 0xFFFFFFFF
        return self._clipboard_seq

    def _track_dropped_seq(self, seq_id: int) -> None:
        """WR-02 — add ``seq_id`` to ``_dropped_seqs`` with bounded growth.

        Pops an arbitrary existing entry when the set reaches
        ``_DROPPED_SEQS_MAX``. This caps memory from attacker spam
        (chunk-0 with random sequence_ids) without needing a scheduler
        for TTL cleanup. See module docstring for ordering notes.
        """
        if len(self._dropped_seqs) >= _DROPPED_SEQS_MAX:
            # set.pop() removes an arbitrary element — good enough to
            # bound the set; NOT strict FIFO. The seq_id we're about
            # to add will survive this eviction.
            self._dropped_seqs.pop()
        self._dropped_seqs.add(seq_id)

    def _oversize_callback(self, size_mb: float) -> None:
        """Phase 3 D-14 — fire the oversize-image toast hook if wired.

        Decoupled from Qt so ``client/protocol.py`` stays UI-free;
        session.py bridges the call into ``show_oversize_image_toast``.
        """
        cb = self.on_oversize_image
        if cb is None:
            return
        try:
            cb(size_mb)
        except Exception as e:
            logger.debug("clipboard.oversize_callback_failed err=%s", e)

    def set_clipboard_toggles(self, text_c2s: bool, text_s2c: bool,
                              image_c2s: bool, image_s2c: bool) -> None:
        """Phase 3 D-15 — update per-direction clipboard toggles.

        Called from session.py on bookmark connect and when the
        ClipboardToggleButton emits a change. Updates take effect
        on the next outbound send / inbound CLIPBOARD_CHUNK boundary
        (per-chunk re-check in ``_send_chunked`` handles the Pitfall 7
        mid-stream race).
        """
        self._clipboard_text_c2s = bool(text_c2s)
        self._clipboard_text_s2c = bool(text_s2c)
        self._clipboard_image_c2s = bool(image_c2s)
        self._clipboard_image_s2c = bool(image_s2c)
        logger.debug(
            "client.protocol.clipboard_toggles t_c2s=%s t_s2c=%s i_c2s=%s i_s2c=%s",
            self._clipboard_text_c2s, self._clipboard_text_s2c,
            self._clipboard_image_c2s, self._clipboard_image_s2c,
        )

    # ------------------------------------------------------------------
    # Phase 2 D-10 / D-11 / D-14 / D-15 — modifier-state plumbing
    # ------------------------------------------------------------------

    @property
    def swap_cmd_ctrl(self) -> bool:
        """Phase 2 D-10 — current per-session Cmd↔Ctrl swap state."""
        return self._swap_cmd_ctrl

    def set_swap_cmd_ctrl(self, enabled: bool) -> None:
        """Flip the per-session Cmd↔Ctrl swap policy.

        Bookmark-driven: ``client/session.py`` reads
        ``ConnectionProfile.swap_cmd_ctrl`` and forwards via this method
        when a tab connects. Survives until next call (or until the
        protocol is recreated).
        """
        self._swap_cmd_ctrl = bool(enabled)
        logger.debug("client.protocol.swap_cmd_ctrl = %s", self._swap_cmd_ctrl)

    def set_capture_mode(self, mode: str, picked_id: int = -1,
                         picked_name: str = "") -> None:
        """Phase 3 D-02 — set the capture-mode fields baked into ClientHelloMsg.

        Server-side ``session_runtime`` (Plan 03) applies the crop rect
        from these values before the encoder spins up. Defaults preserve
        pre-Phase-3 behavior (mirror_all → no crop).

        Whitelist enforcement (threat T-03-07): unknown modes fall back
        to mirror_all with a structlog warning.
        """
        if mode not in ("single", "mirror_all", "pick_one"):
            logger.warning(
                "protocol.invalid_capture_mode mode=%r → mirror_all", mode)
            mode = "mirror_all"
        self._capture_mode = mode
        self._picked_monitor_id = (
            int(picked_id) if picked_id is not None else -1
        )
        self._picked_monitor_name = str(picked_name or "")
        logger.debug(
            "client.protocol.capture_mode=%s picked_id=%s picked_name=%r",
            self._capture_mode, self._picked_monitor_id,
            self._picked_monitor_name,
        )

    def send_reset_modifiers(self, reason: str = "unknown") -> None:
        """Phase 2 D-11 — emit a KeyResetModifiersMsg over the wire.

        ``reason`` is one of: "focus_out", "panic_f9", "reconnect",
        "periodic" (server-side), or any free-form string for diagnostic
        plumbing. The server logs the reason and runs the same idempotent
        InputInjector.reset_modifiers() regardless — per threat T-02-04
        the field is not security-load-bearing.
        """
        msg = KeyResetModifiersMsg(reason=reason)
        # send_input converts the dict back to JSON inside the protocol
        # send loop; pass a dict so the test harness's send_input override
        # sees the structured payload directly.
        self.send_input(json.loads(msg.to_json()))

    def send_text_commit(self, text: str) -> None:
        """Phase 2 D-15 — emit a TextCommitMsg (IME / dead-key passthrough).

        Empty strings are intentionally allowed here — the viewer guards
        the upstream emit, but we don't double-guard so test harnesses
        can drive the path directly.
        """
        msg = TextCommitMsg(text=text)
        self.send_input(json.loads(msg.to_json()))

    def send_key_event(self, qt_key: int, scan_code: int,
                       pressed: bool, modifiers: int) -> None:
        """Phase 2 D-10 / D-14 — KeyEvent emit with swap + lock-state bits.

        Replaces inline ``send_input({"type": MsgType.KEY_EVENT, ...})``
        callers in ``client/session.py``. Two transformations:

          1. D-10: when ``self._swap_cmd_ctrl`` is True, run the (qt_key,
             modifiers) pair through ``swap_cmd_ctrl_for_linux_dest`` so
             Mac Cmd becomes Linux Ctrl. The transformed scan_code is
             what crosses the wire — no "infer at server" anti-pattern.
          2. D-14: every event carries caps_lock_on / num_lock_on /
             scroll_lock_on bits from ``_probe_lock_state()``. Server
             auto-corrects mismatched lock state on the next event.
        """
        out_key = qt_key
        out_scan = scan_code
        out_mods = modifiers
        if self._swap_cmd_ctrl:
            out_key, out_mods = swap_cmd_ctrl_for_linux_dest(qt_key, modifiers)
            # The wire's scan_code field carries the (possibly-swapped) Qt key.
            # XTestInputInjector + qt_key_to_linux_scancode both consume Qt key
            # codes downstream so we mirror the swap into scan_code.
            out_scan = out_key
        caps_on, num_on, scroll_on = _probe_lock_state()
        self.send_input({
            "type": MsgType.KEY_EVENT,
            "key": "",
            "scan_code": out_scan,
            "pressed": pressed,
            "modifiers": out_mods,
            # D-14 — lock-state bits.
            "caps_lock_on": caps_on,
            "num_lock_on": num_on,
            "scroll_lock_on": scroll_on,
            # Phase 3 D-05 — cursor-position-sensitive shortcuts carry
            # server_x / server_y so the server-side key handler can
            # route to the right pixel on mixed-DPI clients. KeyEvent's
            # server_x/y default sentinel is ``-1`` (dataclass default);
            # inline dict shape mirrors that convention so pre-Phase-3
            # servers consume the payload unchanged.
            "server_x": -1,
            "server_y": -1,
        })

    # ------------------------------------------------------------------
    # Phase 3 D-05 — mouse / pen / scroll helpers that carry server_x/y.
    # Session's _send_mouse_* call these (or equivalent inline dicts)
    # so every outbound input message ships the server-physical-pixel
    # ints alongside the legacy normalized floats. See D-05 rationale.
    # ------------------------------------------------------------------

    def send_mouse_move(self, x: float, y: float,
                        server_x: int = -1, server_y: int = -1) -> None:
        """Phase 3 D-05 — MouseMove with server physical-pixel ints."""
        msg = MouseMoveMsg(x=float(x), y=float(y),
                           server_x=int(server_x), server_y=int(server_y))
        self.send_input(json.loads(msg.to_json()))

    def send_mouse_button(self, button: int, pressed: bool,
                          x: float, y: float,
                          server_x: int = -1, server_y: int = -1) -> None:
        """Phase 3 D-05 — MouseButton with server physical-pixel ints."""
        msg = MouseButtonMsg(button=int(button), pressed=bool(pressed),
                             x=float(x), y=float(y),
                             server_x=int(server_x), server_y=int(server_y))
        self.send_input(json.loads(msg.to_json()))

    def send_mouse_scroll(self, dx: float, dy: float,
                          x: float, y: float,
                          server_x: int = -1, server_y: int = -1) -> None:
        """Phase 3 D-05 — MouseScroll with server physical-pixel ints."""
        msg = MouseScrollMsg(dx=float(dx), dy=float(dy),
                             x=float(x), y=float(y),
                             server_x=int(server_x), server_y=int(server_y))
        self.send_input(json.loads(msg.to_json()))

    def send_pen_event(self, data: dict) -> None:
        """Phase 3 D-05 — PenEvent dict carries server_x / server_y.

        The viewer's tabletEvent emits a dict with 'server_x' + 'server_y'
        keys alongside the legacy normalized 'x' / 'y' floats. Build the
        PenEventMsg so missing keys default to -1 sentinel.
        """
        msg = PenEventMsg(
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            server_x=int(data.get("server_x", -1)),
            server_y=int(data.get("server_y", -1)),
            pressure=float(data.get("pressure", 0.0)),
            tilt_x=float(data.get("tilt_x", 0.0)),
            tilt_y=float(data.get("tilt_y", 0.0)),
            rotation=float(data.get("rotation", 0.0)),
            button=int(data.get("button", 0)),
            pressed=bool(data.get("pressed", False)),
            hovering=bool(data.get("hovering", False)),
            pen_type=str(data.get("pen_type", "pen")),
        )
        self.send_input(json.loads(msg.to_json()))

    def _run_loop(self, host: str, port: int):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            while not self._closing:
                try:
                    self._loop.run_until_complete(
                        self._connect_and_receive(host, port))
                except Exception as e:
                    if not self._closing:
                        logger.error("Connection error: %s", e)
                        if self.on_error:
                            self.on_error(str(e))

                self._connected = False
                # STAB-06 / Plan 01-08: any exit from _connect_and_receive
                # without an explicit user-quit means the transport dropped.
                # FSM: streaming / degraded / handshaking / authenticating /
                # capability_exchange → reconnecting.
                try:
                    self.fsm.send("transport_lost")
                except Exception:
                    pass
                if self.on_disconnected and not self._closing:
                    self.on_disconnected("Connection lost")

                if not self._auto_reconnect or self._closing:
                    break

                logger.info("Reconnecting in %.1fs...", self._reconnect_delay)
                time.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 1.5, self._max_reconnect_delay)
        finally:
            self._connected = False
            self._loop.close()
            self._loop = None

    async def _connect_and_receive(self, host: str, port: int):
        if self._broker_mode and not self._broker_token:
            # Phase 1: Connect to broker, authenticate, get machine assignment
            await self._broker_handshake(host, port)
            if self._redirect_host and self._broker_token:
                # Phase 2: Connect to assigned machine with token
                host = self._redirect_host
                port = self._redirect_port
                logger.info("Broker redirect → %s:%d", host, port)
            else:
                return

        try:
            await self._connect_to_server(host, port)
        except Exception:
            # If the broker-assigned target failed (e.g. Flame server offline),
            # drop broker state so the next reconnect re-runs the broker
            # handshake and re-prompts the user to pick a different machine.
            # _run_loop passes the original broker host/port on each retry,
            # so we just need to clear the token + redirect + preferred machine.
            if self._broker_mode and self._broker_token:
                logger.warning(
                    "Broker-assigned target %s:%d failed — returning to broker",
                    host, port)
                self._broker_token = ""
                self._redirect_host = ""
                self._redirect_port = 0
                self._preferred_machine = ""
            raise

    async def _connect_to_server(self, host: str, port: int):
        """Connect to a server (Flame or broker) and handle the session."""
        scheme = "wss" if self._use_tls else "ws"
        uri = f"{scheme}://{host}:{port}"
        logger.info("Connecting to %s", uri)

        # STAB-06 / Plan 01-08: disconnected → handshaking.
        try:
            self.fsm.send("connect_requested")
        except Exception:
            pass

        ssl_context = None
        if self._use_tls:
            from common.tls_opt_out import build_client_ssl_context
            ssl_context = build_client_ssl_context(
                insecure_cli_flag=getattr(self, "_insecure_skip_verify", False),
                ca_bundle=getattr(self, "_ca_bundle", None),
                site_label="server_wss",
            )

        try:
            async with websockets.connect(
                uri, max_size=50 * 1024 * 1024,
                ping_interval=20, ping_timeout=30,
                ssl=ssl_context,
            ) as ws:
                self._ws = ws
                logger.info("Connected to server")
                # STAB-06 / Plan 01-08: handshaking → authenticating (TLS + WS
                # handshake is complete the moment websockets.connect returns).
                try:
                    self.fsm.send("tls_ok")
                except Exception:
                    pass

                first_msg = await ws.recv()
                if isinstance(first_msg, str):
                    msg = parse_message(first_msg)

                    if msg.get("type") == MsgType.AUTH_REQUEST:
                        if self._broker_token:
                            # Authenticate with broker token
                            await self._handle_token_auth(ws, msg)
                        else:
                            await self._handle_auth(ws, msg)

                    elif msg.get("type") == MsgType.SERVER_HELLO:
                        self._handle_server_hello(msg)

                self._connected = True
                self._reconnect_delay = 1.0

                if self.on_connected:
                    self.on_connected()

                hello = ClientHelloMsg(
                    capture_mode=self._capture_mode,
                    picked_monitor_id=self._picked_monitor_id,
                    picked_monitor_name=self._picked_monitor_name,
                    # Phase 3 D-15 / Plan 03-07 — per-direction clipboard
                    # toggles ride the hello so the server mirrors them
                    # onto ClientSession on first packet (secure defaults
                    # land via set_clipboard_toggles before connect).
                    clipboard_text_c2s=self._clipboard_text_c2s,
                    clipboard_text_s2c=self._clipboard_text_s2c,
                    clipboard_image_c2s=self._clipboard_image_c2s,
                    clipboard_image_s2c=self._clipboard_image_s2c,
                )
                if self._screen_size:
                    hello.screen_width = self._screen_size[0]
                    hello.screen_height = self._screen_size[1]
                await ws.send(hello.to_json())

                if self._udp_enabled:
                    await self._negotiate_udp(ws, host)

                if self._hybrid and self._hybrid.state.udp_confirmed:
                    asyncio.ensure_future(self._udp_stats_loop(ws))

                # STAB-06 / Plan 01-08 — client-side HealthPing emitter.
                # Direction inversion: client now originates HealthPing,
                # stamped with self.fsm.current_state.id.
                asyncio.ensure_future(self._client_health_ping_loop(ws))

                async for message in ws:
                    if self._closing:
                        break
                    if isinstance(message, str):
                        self._handle_json(message)
                    elif isinstance(message, bytes):
                        self._handle_binary(message)
        except _BrokerRedirect:
            # Auto-detected broker in direct mode — redirect to assigned Flame
            if self._redirect_host and self._broker_token:
                logger.info("Broker redirect → %s:%d", self._redirect_host, self._redirect_port)
                await self._connect_to_server(self._redirect_host, self._redirect_port)
            else:
                logger.error("Broker redirect failed — no assignment received")

    async def _broker_handshake(self, host: str, port: int):
        """Phase 1: Authenticate with broker and get machine assignment."""
        scheme = "wss" if self._use_tls else "ws"
        uri = f"{scheme}://{host}:{port}"
        logger.info("Connecting to broker at %s", uri)

        ssl_context = None
        if self._use_tls:
            from common.tls_opt_out import build_client_ssl_context
            ssl_context = build_client_ssl_context(
                insecure_cli_flag=getattr(self, "_insecure_skip_verify", False),
                ca_bundle=getattr(self, "_ca_bundle", None),
                site_label="broker_wss",
            )

        async with websockets.connect(
            uri, max_size=1024 * 1024,
            ping_interval=20, ping_timeout=30,
            ssl=ssl_context,
        ) as ws:
            # Broker sends auth_request
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = parse_message(raw)

            if msg.get("type") != MsgType.AUTH_REQUEST:
                logger.error("Broker: expected auth_request, got %s", msg.get("type"))
                return

            # Send PAM credentials
            screen_w = self._screen_size[0] if self._screen_size else 0
            screen_h = self._screen_size[1] if self._screen_size else 0
            auth_resp = AuthResponse(
                method="pam",
                username=self._username,
                credential=self._password,
                screen_width=screen_w,
                screen_height=screen_h,
            )
            await ws.send(auth_resp.to_json())

            # Get auth result
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = parse_message(raw)

            if msg.get("type") == MsgType.AUTH_RESULT:
                if self.on_auth_result:
                    self.on_auth_result(msg.get("success", False),
                                        msg.get("message", ""))
                if not msg.get("success", False):
                    return

            # Get broker hello (machine list)
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = parse_message(raw)

            machines = []
            if msg.get("type") == MsgType.BROKER_HELLO:
                machines = msg.get("machines", []) or []
                if self.on_broker_hello:
                    self.on_broker_hello(msg)

            # Determine which machine to request:
            # 1. If we already have a cached preference (reconnect), reuse it.
            # 2. Else if a machine-needed callback is wired, prompt the user.
            # 3. Else fall back to auto-assign (empty machine_name).
            selected_name = self._preferred_machine
            if not selected_name and self.on_broker_machine_needed:
                # Phase 2 WR-02: get_event_loop() is deprecated on 3.12+
                # and racy in threaded contexts. We're already inside a
                # coroutine (this whole method is awaited), so the running
                # loop is the right reference.
                self._machine_selection_future = asyncio.get_running_loop().create_future()
                try:
                    self.on_broker_machine_needed(machines)
                except Exception as e:
                    logger.error("on_broker_machine_needed raised: %s", e)
                try:
                    selected_name = await asyncio.wait_for(
                        self._machine_selection_future, timeout=120)
                except asyncio.TimeoutError:
                    logger.warning("Machine selection timed out — falling back to auto-assign")
                    selected_name = ""
                finally:
                    self._machine_selection_future = None
                # Cache the choice so reconnects don't re-prompt
                self._preferred_machine = selected_name or ""

            await ws.send(json.dumps({
                "type": MsgType.BROKER_MACHINE_REQUEST,
                "machine_name": selected_name or "",
            }))

            # Get assignment
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = parse_message(raw)

            if msg.get("type") == MsgType.BROKER_ASSIGN:
                if self.on_broker_assign:
                    self.on_broker_assign(msg)

                if msg.get("success", False):
                    self._redirect_host = msg["host"]
                    self._redirect_port = msg["port"]
                    self._broker_token = msg["token"]
                    self._use_tls = msg.get("use_tls", True)
                    logger.info("Broker assigned: %s → %s:%d",
                                msg.get("machine_name", ""),
                                self._redirect_host, self._redirect_port)
                else:
                    logger.error("Broker assignment failed: %s",
                                 msg.get("message", ""))

    async def _handle_token_auth(self, ws, msg: dict):
        """Authenticate with a broker-issued token."""
        screen_w = self._screen_size[0] if self._screen_size else 0
        screen_h = self._screen_size[1] if self._screen_size else 0
        auth_resp = AuthResponse(
            method="token",
            username=self._username,
            credential=self._broker_token,
            screen_width=screen_w,
            screen_height=screen_h,
        )
        await ws.send(auth_resp.to_json())

        result_raw = await ws.recv()
        result = parse_message(result_raw)
        if result.get("type") == MsgType.AUTH_RESULT:
            if self.on_auth_result:
                self.on_auth_result(result.get("success", False),
                                    result.get("message", ""))
            if not result.get("success", False):
                return
            # STAB-06 / Plan 01-08: authenticating → capability_exchange.
            try:
                self.fsm.send("auth_ok")
            except Exception:
                pass

        hello_raw = await ws.recv()
        hello = parse_message(hello_raw)
        self._handle_server_hello(hello)

    async def _handle_auth(self, ws, msg: dict):
        """Handle authentication handshake."""
        auth_mode = msg.get("auth_mode", "local")
        challenge = msg.get("challenge", "")

        if self.on_auth_required:
            self.on_auth_required(challenge)

        if auth_mode == "pam":
            # PAM mode: send credentials directly
            # Security is provided by TLS (wss://) — same as PCoIP/SSH
            screen_w = self._screen_size[0] if self._screen_size else 0
            screen_h = self._screen_size[1] if self._screen_size else 0
            auth_resp = AuthResponse(
                method="pam",
                username=self._username,
                credential=self._password,
                screen_width=screen_w,
                screen_height=screen_h,
            )
            logger.info("Authenticating via PAM as '%s'", self._username)
        else:
            # Local mode: challenge-response hash
            if self._password:
                pwd_hash = hashlib.sha256(
                    f"{self._password}:{challenge}".encode()
                ).hexdigest()
            else:
                pwd_hash = ""

            screen_w = self._screen_size[0] if self._screen_size else 0
            screen_h = self._screen_size[1] if self._screen_size else 0
            auth_resp = AuthResponse(
                method="password",
                username=self._username,
                credential=pwd_hash,
                screen_width=screen_w,
                screen_height=screen_h,
            )

        await ws.send(auth_resp.to_json())

        # Wait for result
        result_raw = await ws.recv()
        result = parse_message(result_raw)
        if result.get("type") == MsgType.AUTH_RESULT:
            if self.on_auth_result:
                self.on_auth_result(result.get("success", False),
                                    result.get("message", ""))
            if not result.get("success", False):
                return
            # STAB-06 / Plan 01-08: authenticating → capability_exchange.
            try:
                self.fsm.send("auth_ok")
            except Exception:
                pass

        # Receive server hello (or broker hello if connected to a broker)
        hello_raw = await ws.recv()
        hello = parse_message(hello_raw)

        if hello.get("type") == MsgType.BROKER_HELLO:
            # Auto-detect broker: connected in direct mode but server is a broker.
            # Handle the full broker redirect inline.
            logger.info("Detected broker (auto-switching from direct mode)")
            machines = hello.get("machines", []) or []
            if self.on_broker_hello:
                self.on_broker_hello(hello)

            # Prompt the user to pick a machine (same flow as broker mode)
            selected_name = self._preferred_machine
            if not selected_name and self.on_broker_machine_needed:
                # Phase 2 WR-02: get_event_loop() is deprecated on 3.12+;
                # we are already in an awaited coroutine.
                self._machine_selection_future = asyncio.get_running_loop().create_future()
                try:
                    self.on_broker_machine_needed(machines)
                except Exception as e:
                    logger.error("on_broker_machine_needed raised: %s", e)
                try:
                    selected_name = await asyncio.wait_for(
                        self._machine_selection_future, timeout=120)
                except asyncio.TimeoutError:
                    logger.warning("Machine selection timed out — falling back to auto-assign")
                    selected_name = ""
                finally:
                    self._machine_selection_future = None
                self._preferred_machine = selected_name or ""

            await ws.send(json.dumps({
                "type": MsgType.BROKER_MACHINE_REQUEST,
                "machine_name": selected_name or "",
            }))

            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            assign = parse_message(raw)

            if assign.get("type") == MsgType.BROKER_ASSIGN and assign.get("success"):
                if self.on_broker_assign:
                    self.on_broker_assign(assign)
                self._redirect_host = assign["host"]
                self._redirect_port = assign["port"]
                self._broker_token = assign["token"]
                self._use_tls = assign.get("use_tls", True)
                self._broker_mode = True
                logger.info("Broker assigned: %s:%d",
                            self._redirect_host, self._redirect_port)
            else:
                logger.error("Broker assignment failed: %s",
                             assign.get("message", ""))
            # Phase 2 WR-04: clear self._ws BEFORE raising the redirect.
            # The async-with context in _connect_to_server is about to
            # exit, taking the websocket with it; if a leftover task
            # (e.g. _client_health_ping_loop) tries to write into the
            # dead ws after the raise but before the recursive
            # _connect_to_server call swaps in a fresh one, we'd blow
            # up with OSError. Setting it to None makes the dead-ws
            # window observable to send_input and friends.
            self._ws = None
            # Signal caller to handle redirect (raise to exit the ws context)
            raise _BrokerRedirect()
        else:
            self._handle_server_hello(hello)

    async def _negotiate_udp(self, ws, server_host: str):
        try:
            self._udp_client = UDPMediaClient()
            self._hybrid = HybridClientTransport(self._udp_client)
            self._jitter_buffer = JitterBuffer(on_frame_ready=self._on_jitter_frame)
            self._jitter_buffer.start()

            self._udp_client.on_video_frame = self._on_udp_video
            self._udp_client.on_audio_frame = self._on_udp_audio

            local_port = self._hybrid.start_udp()

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect((server_host, 1))
                local_addr = s.getsockname()[0]
                s.close()
            except Exception:
                local_addr = ""

            announce = self._hybrid.get_announce_message(local_addr)
            await ws.send(json.dumps(announce))

            await asyncio.sleep(1.0)
            if self._hybrid.check_probe_received():
                confirm = self._hybrid.get_confirm_message()
                await ws.send(json.dumps(confirm))
                logger.info("UDP confirmed — media via UDP")
            else:
                logger.info("UDP unavailable — TCP fallback")

        except Exception as e:
            logger.warning("UDP setup failed: %s", e)
            self._hybrid = None

    async def _udp_stats_loop(self, ws):
        while self._connected and not self._closing and self._hybrid:
            await asyncio.sleep(self._udp_stats_interval)
            if self._hybrid and self._connected:
                try:
                    stats_msg = self._hybrid.get_stats_message()
                    await ws.send(json.dumps(stats_msg))
                except Exception:
                    break

    async def _client_health_ping_loop(self, ws):
        """STAB-06 / Plan 01-08 — client-originated HealthPing emitter.

        Ticks every 2s (matches the pre-Plan-08 server cadence) and sends a
        HealthPing stamped with the current ClientFSM state. The server's
        handle_input branch consumes client_state and runs disagreement
        detection via common.session_fsm.is_state_pair_allowed.
        """
        while self._connected and not self._closing:
            await asyncio.sleep(2.0)
            if not self._connected or self._closing:
                break
            try:
                self._ping_seq += 1
                ping = HealthPing(
                    sequence=self._ping_seq,
                    client_state=self.fsm.current_state.id,
                )
                await ws.send(ping.to_json())
            except Exception:
                break

    def _on_udp_video(self, flags: int, timestamp_ms: int, data: bytes):
        is_keyframe = bool(flags & FLAG_KEYFRAME)
        if self._jitter_buffer:
            self._jitter_buffer.push(
                CHANNEL_VIDEO, flags, timestamp_ms, data, is_keyframe)
        else:
            self._on_jitter_frame(CHANNEL_VIDEO, flags, timestamp_ms, data)

    def _on_udp_audio(self, timestamp_ms: int, data: bytes):
        if self._jitter_buffer:
            self._jitter_buffer.push(CHANNEL_AUDIO, 0, timestamp_ms, data)
        elif self.on_audio_frame:
            self.on_audio_frame(0, timestamp_ms, data)

    def _on_jitter_frame(self, channel: int, flags: int,
                         timestamp_ms: int, data: bytes):
        if channel == CHANNEL_VIDEO:
            if self.on_video_frame:
                # UDP frames don't carry codec/chroma in-band; use FrameType
                # to signal H.264 (the server encodes the current codec)
                from common.messages import FrameType, VideoCodec, ChromaSubsampling
                self.on_video_frame(
                    FrameType.VIDEO_H264, VideoCodec.H264,
                    ChromaSubsampling.YUV444, flags, timestamp_ms, 0, data)
        elif channel == CHANNEL_AUDIO:
            if self.on_audio_frame:
                self.on_audio_frame(0, timestamp_ms, data)

    def _handle_server_hello(self, msg: dict):
        if msg.get("type") == MsgType.SERVER_HELLO:
            # STAB-06 / Plan 01-08: capability_exchange → streaming.
            try:
                self.fsm.send("hello_received")
            except Exception:
                pass
            if self.on_server_hello:
                self.on_server_hello(msg)

    def _handle_json(self, data: str):
        try:
            msg = json.loads(data)
            msg_type = msg.get("type")

            if msg_type == MsgType.SERVER_HELLO:
                if self.on_server_hello:
                    self.on_server_hello(msg)
            elif msg_type == TransportMsg.UDP_ACTIVE:
                logger.info("Server: UDP active (RTT: %.1f ms)",
                            msg.get("udp_rtt_ms", 0))
            elif msg_type == TransportMsg.UDP_FALLBACK:
                logger.warning("Server: UDP fallback - %s", msg.get("reason", ""))
            elif msg_type == MsgType.HEALTH_PONG:
                # STAB-06 / Plan 01-08 — server now originates HealthPong
                # stamped with server_state. Client caches the pair for
                # Plan 01-17 observability + potential UI signalling.
                self.last_reported_server_state = msg.get("server_state", "")
                if self.on_health_ping:
                    self.on_health_ping(msg)
            elif msg_type == MsgType.HEALTH_STATS:
                if self.on_health_stats:
                    self.on_health_stats(msg)
            elif msg_type == MsgType.MONITOR_LIST:
                if self.on_monitor_list:
                    # Phase 3 D-09 / Plan 03-05 — pass the full message
                    # so the session handler can read the ``degradations``
                    # payload alongside the monitor list.
                    self.on_monitor_list(msg)
            elif msg_type == MsgType.CLIPBOARD_RECV:
                # Phase 3 D-13 / Plan 03-07 — 1-shot text path. Image
                # payloads arrive via CLIPBOARD_CHUNK (server chunks
                # every image even <1MB per Plan 06). on_clipboard
                # signature is now (content_type, payload).
                if self.on_clipboard:
                    content_type = msg.get("content_type", "text/plain")
                    self.on_clipboard(content_type, msg.get("data", ""))
            elif msg_type == MsgType.CLIPBOARD_CHUNK:
                self._handle_clipboard_chunk(msg)
            elif msg_type in (MsgType.FILE_ACCEPT, MsgType.FILE_ACK,
                              MsgType.FILE_CANCEL):
                if self.on_file_response:
                    self.on_file_response(msg)
            elif msg_type in (MsgType.USB_DEVICE_LIST, MsgType.USB_ATTACHED,
                              MsgType.USB_DETACHED, MsgType.USB_ERROR):
                if self.on_usb_response:
                    self.on_usb_response(msg)
            elif msg_type == MsgType.BROKER_HELLO:
                if self.on_broker_hello:
                    self.on_broker_hello(msg)
            elif msg_type == MsgType.BROKER_ASSIGN:
                if self.on_broker_assign:
                    self.on_broker_assign(msg)
            elif msg_type == MsgType.CURSOR_UPDATE:
                if self.on_cursor_update:
                    self.on_cursor_update(msg)
        except json.JSONDecodeError:
            pass

    def _handle_clipboard_chunk(self, msg: dict):
        """Phase 3 D-17 / Plan 03-07 — inbound CLIPBOARD_CHUNK handler.

        Uses ``ClipboardChunkAssembler`` (Plan 06) for reassembly. Gates
        s2c at the chunk-0 boundary (Pitfall 7) with ``_dropped_seqs``
        continuation so subsequent chunks for the same sequence no-op.
        D-16 defense-in-depth re-validates PNG magic + size cap on
        completion BEFORE invoking ``on_clipboard``.
        """
        try:
            seq_id = int(msg.get("sequence_id", 0))
            chunk_index = int(msg.get("chunk_index", 0))
            total_chunks = int(msg.get("total_chunks", 1))
        except (TypeError, ValueError):
            logger.warning(
                "clipboard.chunk_bad_fields msg_keys=%s",
                list(msg.keys()),
            )
            return

        content_type = msg.get("content_type", "text/plain")

        # D-15 inbound s2c gating at chunk-0 boundary (Pitfall 7). If
        # the user disables the direction AFTER chunk 0 arrived, the
        # in-flight sequence still completes (payload was authorized
        # when chunk 0 crossed). Subsequent chunks of a dropped
        # sequence hit the _dropped_seqs silent no-op path.
        if chunk_index == 0:
            if content_type == "image/png" and not self._clipboard_image_s2c:
                self._track_dropped_seq(seq_id)
                logger.info(
                    "clipboard.chunk_dropped_toggle seq=%d content_type=%s",
                    seq_id, content_type,
                )
                return
            if content_type == "text/plain" and not self._clipboard_text_s2c:
                self._track_dropped_seq(seq_id)
                logger.info(
                    "clipboard.chunk_dropped_toggle seq=%d content_type=%s",
                    seq_id, content_type,
                )
                return
        if seq_id in self._dropped_seqs:
            # Mid-stream drop continuation — silently no-op.
            return

        asm = self._clipboard_chunks.get(seq_id)
        if asm is None:
            from common.clipboard_chunks import ClipboardChunkAssembler
            try:
                asm = ClipboardChunkAssembler(
                    sequence_id=seq_id,
                    total_chunks=total_chunks,
                    content_type=content_type,
                )
            except ValueError:
                logger.warning(
                    "clipboard.chunk_invalid_total seq=%d total=%s",
                    seq_id, total_chunks,
                )
                return
            self._clipboard_chunks[seq_id] = asm

        full_payload = asm.add(chunk_index, msg.get("data", ""))
        if full_payload is not None:
            del self._clipboard_chunks[seq_id]
            if content_type == "image/png":
                import base64
                try:
                    raw = base64.b64decode(full_payload)
                except Exception:
                    logger.warning(
                        "clipboard.chunk_b64_decode_failed seq=%d", seq_id,
                    )
                    return
                # D-16 defense-in-depth re-validate magic + size cap.
                if len(raw) < 8 or raw[:8] != PNG_MAGIC or len(raw) > PNG_MAX_BYTES:
                    logger.warning(
                        "clipboard.image_recv_invalid seq=%d size=%d",
                        seq_id, len(raw),
                    )
                    return
                if self.on_clipboard:
                    self.on_clipboard("image/png", raw)
                logger.info(
                    "clipboard.chunk_assembled seq=%d content_type=%s size=%d",
                    seq_id, content_type, len(raw),
                )
            else:
                # text/plain — chunks are raw UTF-8 text slices (not
                # base64) matching client outbound _send_chunked. The
                # server currently emits text only via 1-shot
                # CLIPBOARD_RECV, so this branch is exercised by the
                # round-trip tests + any future server that chunks text.
                text = full_payload
                if self.on_clipboard:
                    self.on_clipboard("text/plain", text)
                logger.info(
                    "clipboard.chunk_assembled seq=%d content_type=%s size=%d",
                    seq_id, content_type, len(text),
                )

        # Cheap stale cleanup — bounded by number of concurrent sequences.
        now = time.monotonic()
        stale = [s for s, a in self._clipboard_chunks.items() if a.is_stale(now)]
        for s in stale:
            logger.warning("clipboard.chunk_timeout seq=%d", s)
            del self._clipboard_chunks[s]

    def _handle_binary(self, data: bytes):
        if len(data) < 2:
            return
        frame_type = data[0]

        if frame_type in (FrameType.VIDEO_H264, FrameType.VIDEO_H265,
                          FrameType.VIDEO_AV1):
            if len(data) >= VIDEO_HEADER_SIZE and self.on_video_frame:
                ft, codec, chroma, flags, ts, mon, payload = \
                    decode_video_header(data)
                # Diagnostic: log first frame received + first keyframe
                if not hasattr(self, "_rx_video_count"):
                    self._rx_video_count = 0
                    self._rx_keyframe_count = 0
                self._rx_video_count += 1
                if flags & 0x01:  # KEYFRAME flag
                    self._rx_keyframe_count += 1
                if self._rx_video_count in (1, 30, 300) or \
                   (self._rx_keyframe_count == 1 and flags & 0x01):
                    logger.info("RX video frame #%d (keyframes=%d, codec=%d, "
                                "chroma=%d, flags=%d, payload=%d bytes)",
                                self._rx_video_count, self._rx_keyframe_count,
                                int(codec), int(chroma), flags, len(payload))
                self.on_video_frame(ft, codec, chroma, flags, ts, mon, payload)
        elif frame_type in (FrameType.VIDEO_FULL, FrameType.VIDEO_PARTIAL):
            if len(data) >= JPEG_HEADER_SIZE and self.on_jpeg_frame:
                ft, x, y, w, h, payload = decode_jpeg_header(data)
                self.on_jpeg_frame(ft, x, y, w, h, payload)
        elif frame_type == FrameType.AUDIO:
            if len(data) >= AUDIO_HEADER_SIZE and self.on_audio_frame:
                codec, ts, payload = decode_audio_header(data)
                self.on_audio_frame(codec, ts, payload)
