"""SessionRuntime — per-user session lifecycle (STAB-05 / D-11 extraction).

Moved wholesale from ``server/main.py`` as Plan 01-11 Task 2. Owns:

  * capture (ScreenCapture) + injector (InputInjector / XTestInputInjector)
  * encoder lifecycle (via EncoderLifecycle from Plan 01-10 Task 2)
  * HealthMonitor (shared metrics)
  * audio / clipboard / cursor-tracker / file-transfer / USB-passthrough
    sub-systems
  * the three async loops — StreamLoop, HealthLoop, MonitorHotplug (all
    extracted in Plan 01-10 Task 1) — kept as sub-objects that back-reference
    this runtime for shared state
  * the per-user ``clients`` dict keyed by websocket, guarded by
    ``threading.Lock`` because both the encoder thread and the asyncio loop
    touch it
  * the cached event loop, used by the encoder callback to schedule
    ``cs.enqueue`` coroutines onto the main asyncio loop via
    ``asyncio.run_coroutine_threadsafe``

Preservation invariants (zero behavior change from the pre-extraction monolith):

  * ``IS_MACOS`` platform branch in ``__init__`` (try/finally DISPLAY swap)
  * ``threading.Lock`` + cross-thread ``asyncio.run_coroutine_threadsafe``
    are untouched
  * Back-compat ``self.encoder`` handle kept in sync by EncoderLifecycle
  * Public API (``add_client``, ``remove_client``, ``handle_input``,
    ``apply_quality``, ``stop``, ``set_event_loop``, ``client_count``,
    ``_on_encoded_frame``, ``_on_audio_frame``) unchanged — handle_client
    + handle_http in server/main.py still reach them via the same attribute
    paths.

``server/main.py`` re-exports ``SessionRuntime`` from this module so the
existing import path (``from server.main import SessionRuntime``) used by
``tests/integration/test_server_bootstrap.py`` continues to resolve.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from websockets.server import WebSocketServerProtocol

from common.messages import (
    AudioCodec,
    ClipboardChunkMsg,
    ClipboardMsg,
    FrameType,
    MsgType,
    QualitySettings,
    VideoCodec,
    VideoFrameFlags,
    encode_audio_header,
    encode_video_header,
)
from common.clipboard_chunks import ClipboardChunkAssembler
from common.session_fsm import PenFSM, is_state_pair_allowed
from common.keymap import qt_key_to_linux_scancode
from server.health_loop import check_state_pair  # Plan 01-14: structured ERROR emit
from server.platform_backends import (
    ClipboardSync,
    InputInjector,
    IS_MACOS,
    ScreenCapture,
    XTestInputInjector,
)
from server.cursor_tracker import CursorTracker
from server.video_encoder import JpegFallbackEncoder, VideoEncoder
from server.audio_capture import AudioCapture, check_audio_available
from server.health import HealthMonitor
from server.file_transfer import FileReceiver
from server.usb_passthrough import USBForwardingManager
from server.stream_loop import StreamLoop
from server.health_loop import HealthLoop
from server.encoder_lifecycle import EncoderLifecycle
from server.monitor_hotplug import MonitorHotplug
from server.pipelines import CaptureQueue, EncoderQueue
# WR-02: bounded-size helper for per-session _dropped_seqs.
from server.client_session import track_dropped_seq

if TYPE_CHECKING:
    from server.client_session import ClientSession


logger = logging.getLogger("teraguchi.server.session_runtime")


# ---------------------------------------------------------------------------
# Phase 2 D-11 — periodic safety-net predicate.
#
# Module-level so tests can import it directly without standing up a full
# SessionRuntime. The "10s elapsed AND no chord held" combo is the
# exact precondition Pitfall 6 calls out: NEVER fire while the artist
# is mid-Ctrl+Shift+drag, OR every long Flame paint stroke would have
# its modifiers ripped out from under it.
#
# Boundary policy: the >=10.0 inclusive threshold matches CONTEXT.md
# D-11; tests in tests/server/test_modifier_dispatch.py pin this.
# ---------------------------------------------------------------------------

PERIODIC_RESET_QUIET_S: float = 10.0


def _should_fire_periodic_reset(now: float, last_event: float,
                                last_had_modifiers: bool) -> bool:
    """Phase 2 D-11 / Pitfall 6 precondition for the periodic safety net.

    Returns True iff:
      * The last keyboard event was at least PERIODIC_RESET_QUIET_S
        seconds ago, AND
      * That last event did NOT have any modifiers held (so we know the
        client isn't mid-chord — a held chord is a common Flame
        muscle-memory pattern that this safety net must not break).

    Pure function; no side effects. Server-side caller in
    SessionRuntime.handle_input updates last_event + last_had_modifiers
    every key event.
    """
    if last_had_modifiers:
        return False
    return (now - last_event) >= PERIODIC_RESET_QUIET_S


class SessionRuntime:
    """
    Runtime state for one user's remote desktop session.

    In PAM mode, each authenticated user gets their own SessionRuntime
    with an isolated Xvfb display, capture pipeline, input injection,
    and video encoder. Multiple clients can share a session (reconnection).

    In legacy mode (local/none auth), there is one global SessionRuntime
    attached to the host's DISPLAY.
    """

    def __init__(self, display: str, username: str, quality: QualitySettings,
                 ffmpeg_caps: dict, available_encoders: dict,
                 no_audio: bool = False, no_clipboard: bool = False,
                 sw_only: bool = False, monitor_index: int = 1,
                 jpeg_quality: int = 60, uid: int = 0, gid: int = 0,
                 home_dir: str = "", pen_tablet=None):
        self.display = display
        self.username = username
        self.quality = quality
        self._uid = uid
        self._gid = gid
        self.clients: Dict[WebSocketServerProtocol, "ClientSession"] = {}
        self._lock = threading.Lock()
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # Set DISPLAY for this session's subsystems
        old_display = os.environ.get("DISPLAY", "")
        os.environ["DISPLAY"] = display

        try:
            self.capture = ScreenCapture(monitor_index=monitor_index,
                                         jpeg_quality=jpeg_quality)
            if IS_MACOS:
                # On macOS there's no Xvfb / uinput — CoreGraphics posts
                # events directly into the logged-in user's event stream.
                self.injector = InputInjector(
                    screen_width=self.capture.width,
                    screen_height=self.capture.height,
                )
            # Use XTest for virtual displays (Xvfb), uinput for physical
            elif display.startswith(":") and int(display[1:]) >= 10:
                try:
                    self.injector = XTestInputInjector(display,
                                                       screen_width=self.capture.width,
                                                       screen_height=self.capture.height,
                                                       pen_tablet=pen_tablet)
                except Exception as xinj_err:
                    logger.warning("XTest unavailable: %s, using uinput", xinj_err)
                    self.injector = InputInjector(screen_width=self.capture.width,
                                                   screen_height=self.capture.height)
            else:
                self.injector = InputInjector(screen_width=self.capture.width,
                                              screen_height=self.capture.height)
        finally:
            if old_display:
                os.environ["DISPLAY"] = old_display
            else:
                os.environ.pop("DISPLAY", None)

        # Video encoder — lifecycle is owned by the EncoderLifecycle helper
        # (D-11 Plan 01-10 Task 2). self.encoder stays as a back-compat
        # attribute (ClientSession.enqueue + tests/server/test_pipelines.py
        # reach it via ``runtime.encoder``) and is kept fresh by
        # EncoderLifecycle.spawn()/restart()/stop().
        self.encoder: Optional[VideoEncoder] = None
        self.jpeg_encoder: Optional[JpegFallbackEncoder] = None
        self.use_h264 = False
        self.ffmpeg_caps = ffmpeg_caps
        self.available_encoders = available_encoders
        self.encoder_lifecycle = EncoderLifecycle(self)
        self.encoder_lifecycle.spawn(sw_only=sw_only, jpeg_quality=jpeg_quality)

        # Health monitor
        self.health = HealthMonitor(target_fps=quality.effective_fps())
        self.health.current_codec = quality.codec
        self.health.current_chroma = quality.chroma
        self.health.current_resolution = f"{self.capture.width}x{self.capture.height}"
        # Give the health monitor a reference to the active encoder so the
        # encode-time stat is pulled live from the encoder's own rolling
        # average instead of sitting at zero. (Fork: macOS health metric.)
        # Re-spawns refresh this via EncoderLifecycle.restart().
        if self.encoder is not None:
            self.health.encoder_ref = self.encoder

        # Audio
        self.audio: Optional[AudioCapture] = None
        if not no_audio and check_audio_available(uid=self._uid, gid=self._gid):
            self.audio = AudioCapture(bitrate_kbps=quality.audio_bitrate_kbps,
                                      uid=self._uid, gid=self._gid)

        # Clipboard
        self.clipboard: Optional[ClipboardSync] = None
        if not no_clipboard:
            self.clipboard = ClipboardSync(display=display)

        # Local-cursor tracker — polls XFixes for cursor shape changes
        # so the client can draw the real Flame cursor locally at zero
        # latency. Falls back gracefully if XFixes is unavailable (we
        # just won't send cursor_update messages and the client will
        # keep using its placeholder cursor).
        self.cursor_tracker: Optional[CursorTracker] = None
        if not IS_MACOS:
            # CursorTracker uses XFixes on Linux; on macOS we bake the
            # cursor into the video frame via SCK's showsCursor=True.
            try:
                self.cursor_tracker = CursorTracker(display_name=display,
                                                    poll_hz=30.0)
            except Exception as e:
                logger.warning("[%s] Cursor tracker unavailable: %s",
                               username, e)

        # File transfer
        ft_home = home_dir or os.path.expanduser("~")
        self.file_receiver = FileReceiver(ft_home, uid=uid, gid=gid)

        # USB passthrough — Linux-only (uses usbip / vhci kernel modules).
        # Stubbed out on macOS; a Mac-native IOKit forwarder is future work.
        self.usb_manager = None if IS_MACOS else USBForwardingManager()

        # Streaming state
        self._streaming = False
        self._running = True

        # Phase 2 D-11 — periodic safety-net state. ``_last_key_event_at``
        # is the monotonic-clock timestamp of the most recent KEY_EVENT;
        # ``_last_key_event_had_modifiers`` records whether the client
        # reported any modifier bit on that event. Together they feed
        # ``_should_fire_periodic_reset`` (module-level) every ~2s. The
        # period itself runs as ``_modifier_periodic_safety_loop`` started
        # from ``_start_streaming``.
        self._last_key_event_at: float = time.monotonic()
        self._last_key_event_had_modifiers: bool = False
        self._modifier_safety_task: Optional[asyncio.Task] = None
        # Phase 2 D-19 — pen-proximity FSM (orthogonal to ClientFSM/ServerFSM).
        # Driven by PEN_PROXIMITY wire messages from the client's focusIn /
        # showEvent re-synth. Idempotent on duplicate enter / leave per
        # the PenFSM contract — see common/session_fsm.py::PenFSM.
        self._pen_fsm = PenFSM()
        # Phase 2 D-14 — server's view of virtual-display lock state.
        # Default False matches a freshly-spawned X session; the wire
        # bits flip these to True/False as needed via
        # ``_sync_lock_state_from_wire``.
        self._caps_lock_on: bool = False
        self._num_lock_on: bool = False
        self._scroll_lock_on: bool = False

        # D-11 / Plan 01-10: stream + health + hotplug loops are now sub-
        # objects. SessionRuntime remains the owner of shared state (capture,
        # encoder, clients, health); the loops only hold the run-flag + task
        # handle and reach shared state via the back-reference.
        self._stream_loop = StreamLoop(self)
        self._health_loop = HealthLoop(self)
        self._hotplug = MonitorHotplug(self)

        # STAB-07 / Plan 01-13 — bounded pipeline queues with drop-OLDEST
        # policy. Instantiated here so the queue classes are reachable for
        # Plan 01-14 telemetry (OBS-03 drop counters via the on_drop hook)
        # and for the future capture-rate decoupling path. Today the
        # synchronous capture → encoder.feed_frame handoff in StreamLoop is
        # fast enough that these queues stay empty; wiring them through
        # StreamLoop would require refactoring the synchronous capture
        # call, which is out of scope for Plan 01-13. The per-client
        # send_queue (maxsize=4, drop-oldest + IDR-on-drop) stays in
        # ClientSession — its drop handling depends on session-local state
        # (_drops_since_keyframe) so it must not be hoisted here.
        self._capture_queue = CaptureQueue(
            maxsize=2,
            on_drop=self.health.record_frame_dropped,
        )
        self._encoder_queue = EncoderQueue(
            maxsize=3,
            on_drop=self.health.record_frame_dropped,
        )

        logger.info("[%s] Session runtime ready on %s (%dx%d)",
                    username, display, self.capture.width, self.capture.height)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._event_loop = loop

    # ── Client management ────────────────────────────────────

    def add_client(self, ws: WebSocketServerProtocol, session: "ClientSession"):
        with self._lock:
            self.clients[ws] = session
            self.health.clients_connected = len(self.clients)
        # Clear stuck modifier keys on new connection
        if hasattr(self.injector, 'reset_modifiers'):
            self.injector.reset_modifiers()
        if not self._streaming:
            self._start_streaming()
        elif self.encoder:
            # Reused session: force an IDR so the new client can start decoding
            # immediately instead of waiting for the next natural GOP boundary
            # (or staying black forever if nvenc doesn't emit one).
            self.capture.invalidate()
            self.encoder.request_keyframe()

        # Push current cursor shape so late-joining clients don't stare
        # at their placeholder until Flame next changes the cursor.
        if self.cursor_tracker is not None and self._event_loop:
            latest = self.cursor_tracker.latest()
            if latest is not None:
                try:
                    asyncio.run_coroutine_threadsafe(
                        session.enqueue(json.dumps(latest)), self._event_loop)
                except Exception:
                    pass

    def remove_client(self, ws: WebSocketServerProtocol):
        with self._lock:
            self.clients.pop(ws, None)
            self.health.clients_connected = len(self.clients)
        # Session persists — don't stop streaming
        # (PCoIP behavior: session stays alive for reconnection)

    @property
    def client_count(self) -> int:
        return len(self.clients)

    # ── Streaming ────────────────────────────────────────────

    def _start_streaming(self):
        if self._streaming:
            return
        self._streaming = True
        fps = self.quality.effective_fps()

        # D-11 / Plan 01-10: stream + health + hotplug loops all live in
        # their own modules. The loops read shared state off this
        # SessionRuntime via back-reference; wiring stays a pure module
        # split with zero behavior change.
        self._stream_loop.start(fps)
        self._health_loop.start()
        self._hotplug.start()

        if self.audio and self.audio.available and self.quality.enable_audio:
            self.audio.start(self._on_audio_frame)

        if self.clipboard and self.clipboard.available:
            self.clipboard.start_monitoring(self._on_clipboard_change)

        if self.cursor_tracker is not None:
            self.cursor_tracker.start(self._on_cursor_shape_change)

        # Phase 2 D-11 — start the periodic safety-net loop. Bound to
        # the asyncio event loop set via set_event_loop(); we schedule
        # the task there so the loop runs alongside the rest of the
        # session's async surface. Skip if no loop is wired (e.g. unit
        # tests construct SessionRuntime without an event loop).
        if self._event_loop is not None and self._modifier_safety_task is None:
            try:
                self._modifier_safety_task = asyncio.run_coroutine_threadsafe(
                    self._spawn_modifier_safety_loop(),
                    self._event_loop,
                ).result(timeout=2.0)
            except Exception as e:
                logger.warning(
                    "[%s] periodic-safety loop failed to start: %s",
                    self.username, e,
                )
                self._modifier_safety_task = None

        logger.info("[%s] Streaming started (%d fps)", self.username, fps)

    async def _spawn_modifier_safety_loop(self) -> asyncio.Task:
        """Schedule the periodic-safety coroutine onto the running loop.

        Returning the Task object via run_coroutine_threadsafe lets the
        synchronous _start_streaming caller hold a handle for cancellation
        in stop().
        """
        return asyncio.create_task(self._modifier_periodic_safety_loop())

    async def _modifier_periodic_safety_loop(self) -> None:
        """Phase 2 D-11 — every ~2s, fire reset_modifiers if Pitfall 6 OK.

        The 2s tick is intentional — far below the 10s quiet threshold
        so we always catch the first tick after the threshold is met
        without imposing any meaningful CPU cost. ``_should_fire_periodic_reset``
        owns the precondition decision (no modifiers held + ≥10s quiet).
        """
        try:
            while self._running and self._streaming:
                await asyncio.sleep(2.0)
                if not (self._running and self._streaming):
                    break
                now = time.monotonic()
                if _should_fire_periodic_reset(
                    now,
                    self._last_key_event_at,
                    self._last_key_event_had_modifiers,
                ):
                    try:
                        if hasattr(self.injector, "reset_modifiers"):
                            self.injector.reset_modifiers()
                        logger.info(
                            "input.periodic_reset_modifiers quiet_s=%.2f",
                            now - self._last_key_event_at,
                        )
                    except Exception as e:
                        logger.debug("periodic reset failed: %s", e)
                    # Bump the timer so we don't immediately re-fire on
                    # the next tick — gives the safety net a clean slate.
                    self._last_key_event_at = now
        except asyncio.CancelledError:
            return

    def _sync_lock_state_from_wire(self, msg: dict) -> None:
        """Phase 2 D-14 — auto-correct virtual display lock state.

        For every KEY_EVENT we receive, compare the wire's caps_lock_on /
        num_lock_on / scroll_lock_on bits to our local cache. On mismatch
        we update the cache and (best-effort) toggle the corresponding
        scan code so the X server's lock state catches up. The toggle
        path is only run on Linux; Mac handles lock state via the OS
        and CGEventCreateKeyboardEvent doesn't expose a lock-toggle.
        """
        # Extract bits with explicit defaults — older clients may not
        # send them, in which case we leave server-side state alone.
        wire_caps = msg.get("caps_lock_on")
        wire_num = msg.get("num_lock_on")
        wire_scroll = msg.get("scroll_lock_on")

        # Linux scan codes for lock keys.
        LOCK_SCANCODES = {
            "caps": 58,    # KEY_CAPSLOCK
            "num": 69,     # KEY_NUMLOCK
            "scroll": 70,  # KEY_SCROLLLOCK
        }

        # Only XTest / uinput injectors expose toggles meaningfully on
        # Linux. The Mac injector path is documented as not toggling
        # locks via this channel.
        if IS_MACOS:
            # Just track wire state for diagnostics; no toggle path.
            if wire_caps is not None:
                self._caps_lock_on = bool(wire_caps)
            if wire_num is not None:
                self._num_lock_on = bool(wire_num)
            if wire_scroll is not None:
                self._scroll_lock_on = bool(wire_scroll)
            return

        for name, wire_bit, cache_attr in (
            ("caps", wire_caps, "_caps_lock_on"),
            ("num", wire_num, "_num_lock_on"),
            ("scroll", wire_scroll, "_scroll_lock_on"),
        ):
            if wire_bit is None:
                continue
            wire_bit = bool(wire_bit)
            current = getattr(self, cache_attr)
            if wire_bit != current:
                # Toggle the lock key — press + release of the lock
                # scan code flips the lock state on most X servers.
                code = LOCK_SCANCODES[name]
                try:
                    if hasattr(self.injector, "keyboard"):
                        self.injector.keyboard.key_event(code, True)
                        self.injector.keyboard.key_event(code, False)
                except Exception:
                    pass
                setattr(self, cache_attr, wire_bit)
                logger.debug(
                    "input.lock_state_toggled lock=%s wire=%s",
                    name, wire_bit,
                )

    # ── Encoder callbacks ────────────────────────────────────

    def _on_encoded_frame(self, frame_data: bytes, is_keyframe: bool):
        # OBS-03 (Plan 01-14) — bump keyframe_emitted counter on every IDR
        # the encoder emits. Pair with ClientSession.enqueue's
        # record_keyframe_requested call for request/emit ratio telemetry.
        if is_keyframe:
            self.health.record_keyframe_emitted()
        timestamp = int(time.time() * 1000) & 0xFFFFFFFF
        codec_name = self.quality.codec.lower()
        if codec_name == "av1":
            codec, frame_type = VideoCodec.AV1, FrameType.VIDEO_AV1
        elif codec_name == "h265":
            codec, frame_type = VideoCodec.H265, FrameType.VIDEO_H265
        else:
            codec, frame_type = VideoCodec.H264, FrameType.VIDEO_H264

        chroma = self.quality.effective_chroma()
        flags = VideoFrameFlags.KEYFRAME if is_keyframe else VideoFrameFlags.NONE
        header = encode_video_header(frame_type, codec, chroma, flags, timestamp)
        tcp_data = header + frame_data

        for ws, cs in list(self.clients.items()):
            if cs.authenticated and self._event_loop:
                asyncio.run_coroutine_threadsafe(
                    self._enqueue_frame(cs, tcp_data, is_keyframe), self._event_loop)

        self.health.record_frame_sent(len(frame_data))

    async def _enqueue_frame(self, cs: "ClientSession", data: bytes, is_keyframe: bool = False):
        if not await cs.enqueue(data, is_keyframe=is_keyframe):
            self.health.record_frame_dropped()

    def _on_audio_frame(self, audio_data: bytes, timestamp_ms: int):
        ts = timestamp_ms & 0xFFFFFFFF
        header = encode_audio_header(AudioCodec.OPUS, ts)
        data = header + audio_data
        for ws, cs in list(self.clients.items()):
            if cs.authenticated and cs.supports_audio and self._event_loop:
                asyncio.run_coroutine_threadsafe(cs.enqueue(data), self._event_loop)

    def _send_to_client(self, session: "ClientSession", msg_dict: dict):
        """Send a JSON response to a specific client (thread-safe)."""
        if self._event_loop and msg_dict:
            msg_json = json.dumps(msg_dict)
            asyncio.run_coroutine_threadsafe(session.enqueue(msg_json), self._event_loop)

    def _on_clipboard_change(self, content_type, payload=None):
        """Phase 3 D-15 / Plan 03-06 — per-direction gating + text/image dispatch.

        Two-arg form ``_on_clipboard_change(content_type, payload)`` — matches
        the Plan 03-06 ClipboardSync / MacClipboardSync ``start_monitoring``
        callback contract. ``content_type`` is ``"text/plain"`` or
        ``"image/png"``; text payload is ``str``, image payload is raw PNG
        ``bytes``.

        Per-client s2c gate: if the client's ``clipboard_text_s2c`` (or
        image variant) is False, skip the enqueue entirely — zero wire
        traffic leaves the server for that direction. Text payloads ride
        a single :class:`ClipboardMsg`; image payloads go through
        :meth:`_enqueue_chunked_clipboard` which splits the base64-
        encoded PNG into 1 MB :class:`ClipboardChunkMsg` frames (D-17).

        Backward-compat: callers that pass a single ``str`` argument
        (pre-Plan-03-06 clipboards + tests that still construct the old
        single-arg callback) are routed to the text path.
        """
        # Backward-compat with single-arg callers (legacy tests /
        # Phase-2-era ClipboardSync stubs that didn't pass content_type).
        if payload is None:
            payload = content_type
            content_type = "text/plain"

        is_image = content_type == "image/png"
        for ws, cs in list(self.clients.items()):
            if not (cs.authenticated and self._event_loop):
                continue
            # Outbound s2c gate — short-circuit BEFORE JSON encode.
            if is_image and not getattr(cs, "clipboard_image_s2c", True):
                continue
            if (not is_image) and not getattr(cs, "clipboard_text_s2c", True):
                continue
            if is_image:
                import base64
                # Base64 encode once; _enqueue_chunked_clipboard splits into 1MB frames.
                data_b64 = base64.b64encode(payload).decode("ascii")
                self._enqueue_chunked_clipboard(cs, content_type, data_b64)
            else:
                # WR-06: large text chunks symmetrically with the client's
                # _send_chunked path — a single-message send of a multi-MB
                # paste would block the per-client send_queue (maxsize=4)
                # and could trigger drop-OLDEST IDR-on-drop noise on video
                # frames. Threshold matches _enqueue_chunked_clipboard's
                # internal CHUNK_BYTES so small text (<1 MB) still rides
                # the zero-overhead CLIPBOARD_RECV envelope.
                CHUNK_BYTES = 1024 * 1024
                if len(payload) <= CHUNK_BYTES:
                    msg_json = ClipboardMsg(
                        type=MsgType.CLIPBOARD_RECV,
                        content_type="text/plain",
                        data=payload,
                    ).to_json()
                    asyncio.run_coroutine_threadsafe(
                        cs.enqueue(msg_json), self._event_loop,
                    )
                else:
                    # Text chunks are raw UTF-8 slices (not base64) — the
                    # client _handle_clipboard_chunk text branch
                    # (client/protocol.py:~1411) reassembles by simple
                    # concatenation. Consistent with client outbound
                    # _send_chunked text path.
                    self._enqueue_chunked_clipboard(cs, content_type, payload)

    def _next_clipboard_seq(self) -> int:
        """Phase 3 D-17 — monotonic per-runtime clipboard sequence_id allocator.

        Wraps at 32-bit to keep the wire integer bounded. Per-client
        inbound assemblers dedupe on ``sequence_id`` so the wrap is
        benign unless a single client receives 2³² clipboard events in
        one session (not a real threat).

        WR-04: the 32-bit wrap could theoretically collide with a stale
        entry in per-session ``_dropped_seqs``. The WR-02 cap on that
        set (_DROPPED_SEQS_MAX=4096 via track_dropped_seq) keeps the
        collision probability negligible in practice.
        """
        if not hasattr(self, "_clipboard_seq"):
            self._clipboard_seq = 0
        self._clipboard_seq = (self._clipboard_seq + 1) & 0xFFFFFFFF
        return self._clipboard_seq

    def _enqueue_chunked_clipboard(self, cs: "ClientSession", content_type: str,
                                    data_b64: str) -> None:
        """D-17 — split base64 payload into 1 MB chunks; emit one
        :class:`ClipboardChunkMsg` per chunk. Each chunk enqueues
        through the existing per-client bounded send_queue so STAB-07
        queue-depth invariants stay intact."""
        CHUNK_BYTES = 1024 * 1024
        total = max(1, (len(data_b64) + CHUNK_BYTES - 1) // CHUNK_BYTES)
        seq = self._next_clipboard_seq()
        for i in range(total):
            chunk = data_b64[i * CHUNK_BYTES : (i + 1) * CHUNK_BYTES]
            msg_json = ClipboardChunkMsg(
                sequence_id=seq,
                chunk_index=i,
                total_chunks=total,
                content_type=content_type,
                data=chunk,
            ).to_json()
            asyncio.run_coroutine_threadsafe(cs.enqueue(msg_json), self._event_loop)
        # Telemetry — size counts only, NEVER payload bytes (T-03-32).
        logger.info(
            "clipboard.outbound_chunked content_type=%s seq=%d total_chunks=%d",
            content_type, seq, total,
        )

    def _on_cursor_shape_change(self, update: dict):
        """Called from the CursorTracker polling thread whenever Flame
        swaps cursor shapes. Broadcast to every authenticated client so
        they can swap their local QCursor with zero latency."""
        if not self._event_loop:
            return
        msg_json = json.dumps(update)
        for ws, cs in list(self.clients.items()):
            if cs.authenticated:
                asyncio.run_coroutine_threadsafe(
                    cs.enqueue(msg_json), self._event_loop)

    # ── Capture-mode plumbing (Phase 3 D-02 / D-09) ──────────

    # Allowed capture modes — T-03-09 STRIDE mitigation: unknown strings
    # are rejected and silently reset to mirror_all (preserves pre-Phase-3
    # always-full-virtual-desktop behavior; structlog warning records the
    # attempt for forensic review).
    _CAPTURE_MODES = ("single", "mirror_all", "pick_one")

    def apply_capture_mode(
        self,
        session: "ClientSession",
        mode: str,
        picked_id: int,
        picked_name: str,
    ) -> bool:
        """Phase 3 D-02 — set the per-session capture crop based on mode.

        Args:
            session: ClientSession-like object with per-session
                ``capture_mode`` / ``picked_monitor_id`` /
                ``picked_monitor_name`` / ``crop_rect`` /
                ``capture_mode_degraded`` attributes.
            mode: One of ``single``, ``mirror_all``, ``pick_one``. Any
                other value is treated as mirror_all (T-03-09 mitigation).
            picked_id: Monitor id from client's ClientHelloMsg, or -1.
                Preferred over ``picked_name`` when both are supplied
                (D-04 belt+suspenders: id wins, name fallback).
            picked_name: Monitor name fallback; used when ``picked_id``
                is -1 or not present in the current monitor list.

        Returns:
            True if the requested mode was honored as-asked;
            False if it was degraded (e.g. pick_one monitor missing →
            fell back to primary). Plan 05 wires the client-side toast
            off the degraded=True signal.

        Capture path: this method sets ``session.crop_rect``; the stream
        loop's ``capture_raw_bgra_with_crop`` wrap reads it on each frame
        via the single shared ``runtime.capture``. v1 ships single-
        session-per-host for cropped paths — if two authenticated
        sessions race to set differing crops, the LATEST
        apply_capture_mode wins (single-session reality means this is a
        no-op in practice). Forensic signal per v1.1 backlog is logged
        via session.multi_session_crop_collision below.
        """
        if mode not in self._CAPTURE_MODES:
            logger.warning(
                "session.invalid_capture_mode mode=%r → mirror_all", mode,
            )
            mode = "mirror_all"
        session.capture_mode = mode
        session.capture_mode_degraded = False

        if mode == "mirror_all":
            # Full-virtual-desktop pass-through; preserves Phase 2
            # 9-checkpoint 10-bit fixture byte-equality.
            session.crop_rect = None
            session.picked_monitor_id = -1
            session.picked_monitor_name = ""
            self._warn_if_multi_session_crop_collision(session)
            logger.info(
                "session.capture_mode_applied mode=%s crop_rect=%s degraded=%s",
                mode, session.crop_rect, session.capture_mode_degraded,
            )
            return True

        if not self.capture:
            # Capture not initialized yet (unit test path). Record the
            # intent; the stream loop will use crop=None until the
            # capture is wired up.
            # WR-05: defense-in-depth — if a future refactor collapses
            # the early `mirror_all` return above into a unified code
            # path, make sure the no-capture branch still clears the
            # pick fields on mirror_all so toolbar-badge /
            # picked_monitor_name consumers never display stale data.
            # Today this is dead code (mirror_all returns at line 710)
            # but it's cheap safety on the path most likely to drift.
            session.crop_rect = None
            if mode == "mirror_all":
                session.picked_monitor_id = -1
                session.picked_monitor_name = ""
            return True

        try:
            monitors = list(self.capture.list_monitors())
        except Exception as e:
            logger.warning(
                "session.list_monitors_failed mode=%s err=%s", mode, e,
            )
            session.crop_rect = None
            return False

        # Skip virtual-desktop entry (id=0 per screen_capture.py L393).
        non_virtual = [m for m in monitors if m.id != 0]
        if not non_virtual:
            logger.warning(
                "session.no_monitors_for_capture_mode mode=%s", mode,
            )
            session.crop_rect = None
            session.capture_mode_degraded = True
            return False

        def _primary() -> Optional[object]:
            for m in non_virtual:
                if getattr(m, "primary", False):
                    return m
            return non_virtual[0]

        target = None
        # D-04 belt+suspenders: id wins, name fallback. Only consult
        # picked_id if it's a real (non-sentinel) value.
        if picked_id is not None and picked_id >= 0:
            for m in non_virtual:
                if m.id == picked_id:
                    target = m
                    break
        if target is None and picked_name:
            for m in non_virtual:
                if m.name == picked_name:
                    target = m
                    break

        if mode == "single":
            # "Single monitor" means one monitor, defaulting to primary
            # if no id/name provided (D-01 semantics).
            if target is None:
                target = _primary()
        elif mode == "pick_one":
            # pick_one with missing id → fall back to primary + flag degraded
            # (D-09 foundation; Plan 05 wires the client-side toast).
            if target is None:
                target = _primary()
                session.capture_mode_degraded = True
                logger.warning(
                    "session.capture_mode_degraded mode=%s requested_id=%d "
                    "requested_name=%r → primary=%s",
                    mode, int(picked_id), picked_name,
                    getattr(target, "name", "<none>"),
                )

        if target is None:
            session.crop_rect = None
            session.capture_mode_degraded = True
            return False

        session.crop_rect = (
            int(target.x), int(target.y),
            int(target.width), int(target.height),
        )
        session.picked_monitor_id = int(target.id)
        session.picked_monitor_name = str(target.name)
        self._warn_if_multi_session_crop_collision(session)
        logger.info(
            "session.capture_mode_applied mode=%s crop_rect=%s degraded=%s",
            mode, session.crop_rect, session.capture_mode_degraded,
        )
        return not session.capture_mode_degraded

    def _warn_if_multi_session_crop_collision(
        self, applying_session: "ClientSession",
    ) -> None:
        """W-5 forensic guard — log when two authenticated sessions
        diverge on ``crop_rect``.

        v1 is single-session-per-host for cropped paths (see
        docs/release.md "Multi-session crop per host (v1.1 follow-up)").
        The single shared ``runtime.capture`` instance reads
        ``client_session.crop_rect`` from the one authenticated session;
        a second concurrent session with a different capture_mode sees
        the first session's crop until v1.1 wires the per-session
        encode wrap. Existing PAM per-user X session isolation on Linux
        prevents cross-tenant capture — this limitation only applies to
        two sessions on the same X display, which the small-studio
        deployment model does not require.

        Emits ``session.multi_session_crop_collision`` at WARNING level
        so the v1.1 forensic signal is machine-greppable.
        """
        try:
            existing_crops = set()
            for ws, cs in getattr(self, "clients", {}).items():
                if cs is applying_session:
                    continue
                if not getattr(cs, "authenticated", False):
                    continue
                rect = getattr(cs, "crop_rect", None)
                existing_crops.add(rect)
            if getattr(applying_session, "crop_rect", None) in existing_crops:
                return  # Same crop — not a collision.
            if not existing_crops:
                return  # Only one authenticated session — no collision.
            logger.warning(
                "session.multi_session_crop_collision "
                "applying=%s existing_crops=%s — v1 single-session-per-host "
                "reality means LATEST apply_capture_mode wins; v1.1 follow-up "
                "adds per-session encode wrap",
                getattr(applying_session, "crop_rect", None),
                list(existing_crops),
            )
        except Exception as e:
            # Never let a forensic-log failure poison the caller.
            logger.debug(
                "session.multi_session_crop_collision_log_failed err=%s", e,
            )

    # ── Quality / encoder management ─────────────────────────

    def apply_quality(self, session: "ClientSession", msg: dict):
        session.quality = QualitySettings(**{k: v for k, v in msg.items()
                                             if k in QualitySettings.__dataclass_fields__})
        self.quality = session.quality

        if self.quality.codec in ("h264", "h265", "av1") and \
           self.ffmpeg_caps.get(self.quality.codec, False):
            if not self.use_h264:
                self.use_h264 = True
                self.encoder_lifecycle.restart()
            elif self.encoder:
                self.encoder.update_settings(self.quality)
        else:
            self.use_h264 = False

        self.health.current_codec = self.quality.codec
        self.health.current_chroma = self.quality.chroma
        self.health.target_fps = self.quality.effective_fps()

        if self.audio:
            if self.quality.enable_audio:
                if not self.audio._running:
                    self.audio.start(self._on_audio_frame)
                self.audio.update_bitrate(self.quality.audio_bitrate_kbps)
            else:
                if self.audio._running:
                    self.audio.stop()

    def _handle_resize(self, width: int, height: int):
        """Handle a resize request from the client."""
        if width == self.capture.width and height == self.capture.height:
            return

        try:
            env = {"DISPLAY": self.display, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}

            # Find the connected output name (DP-0 for GPU, screen for Xvfb)
            query = subprocess.run(
                ["xrandr", "--query"],
                capture_output=True, text=True, timeout=5, env=env)
            output_name = "screen"  # default for Xvfb
            for line in query.stdout.splitlines():
                if " connected" in line:
                    output_name = line.split()[0]
                    break

            mode_name = f"{width}x{height}"

            # Try setting mode directly first
            result = subprocess.run(
                ["xrandr", "--output", output_name, "--mode", mode_name],
                capture_output=True, text=True, timeout=5, env=env)
            if result.returncode == 0:
                self.capture.reinit(width, height)
                self.encoder_lifecycle.restart()
                logger.info("Resized to %dx%d", width, height)
                return

            # Mode doesn't exist — create it
            modeline = subprocess.run(
                ["cvt", str(width), str(height)],
                capture_output=True, text=True, timeout=5, env=env)
            if modeline.returncode == 0:
                for line in modeline.stdout.strip().split("\n"):
                    if line.startswith("Modeline"):
                        parts = line.split(None, 2)
                        mode_label = parts[1].strip('"')
                        mode_params = parts[2]

                        subprocess.run(
                            ["xrandr", "--newmode", mode_label] + mode_params.split(),
                            capture_output=True, timeout=5, env=env)
                        subprocess.run(
                            ["xrandr", "--addmode", output_name, mode_label],
                            capture_output=True, timeout=5, env=env)
                        result = subprocess.run(
                            ["xrandr", "--output", output_name, "--mode", mode_label],
                            capture_output=True, text=True, timeout=5, env=env)
                        if result.returncode == 0:
                            self.capture.reinit(width, height)
                            self.encoder_lifecycle.restart()
                            logger.info("Resized to %dx%d", width, height)
                            return
                        else:
                            logger.warning("Resize failed: %s", result.stderr)
        except Exception as e:
            logger.warning("Resize error: %s", e)

    # ── Input handling ───────────────────────────────────────

    def handle_input(self, session: "ClientSession", msg: dict):
        msg_type = msg.get("type")
        t0 = time.time()
        # Only real user-input events count toward the input-latency metric —
        # housekeeping messages (HEALTH_PONG, CLIENT_HELLO, QUALITY_SETTINGS,
        # etc.) have nothing to do with input lag and would dilute the average
        # to near-zero. (Fork: macOS health metric.)
        is_input_event = msg_type in (
            MsgType.KEY_EVENT,
            MsgType.MOUSE_MOVE,
            MsgType.MOUSE_BUTTON,
            MsgType.MOUSE_SCROLL,
            MsgType.PEN_EVENT,
        )

        if msg_type == MsgType.KEY_EVENT:
            # Phase 2 D-11 — record activity for the periodic safety net
            # BEFORE we dispatch so a stuck dispatcher doesn't poison the
            # last_event timer. last_had_modifiers is derived from the wire
            # modifiers bitmask: any non-zero modifier value means the
            # client thinks at least one chord key is held, and the
            # periodic safety net must NOT fire while that's true.
            self._last_key_event_at = time.monotonic()
            self._last_key_event_had_modifiers = bool(msg.get("modifiers", 0))
            # Phase 2 D-14 — server-side lock-state auto-correct. The wire
            # carries caps_lock_on / num_lock_on / scroll_lock_on bits on
            # every KeyEvent. If the server's view of any lock state
            # disagrees with the client, schedule a corrective release
            # toggle. Best-effort: failures here must not block input.
            try:
                self._sync_lock_state_from_wire(msg)
            except Exception as e:
                logger.debug("input.lock_sync_failed: %s", e)
            if isinstance(self.injector, XTestInputInjector):
                # XTest uses Qt key codes directly
                self.injector.handle_message(msg)
            else:
                qt_key = msg.get("scan_code", 0)
                linux_code = qt_key_to_linux_scancode(qt_key)
                if linux_code == 0:
                    return
                msg["scan_code"] = linux_code
                self.injector.handle_message(msg)

        elif msg_type == MsgType.KEY_RESET_MODIFIERS:
            # Phase 2 D-11 — release every held modifier on the server side.
            # Per threat T-02-04: reason is informational only; the server
            # always performs the same idempotent reset regardless. Empty
            # / unknown reason strings round-trip as "unknown" in logs.
            reason = msg.get("reason", "unknown")
            try:
                if hasattr(self.injector, "reset_modifiers"):
                    self.injector.reset_modifiers()
                logger.info(
                    "input.reset_modifiers reason=%s client_id=%s",
                    reason, session.client_id,
                )
            except Exception as e:
                logger.debug("input.reset_modifiers_failed: %s", e)
            # A reset clears modifier state — record that for the
            # periodic safety net so we don't immediately re-fire.
            self._last_key_event_at = time.monotonic()
            self._last_key_event_had_modifiers = False

        elif msg_type == MsgType.PEN_PROXIMITY:
            # Phase 2 D-19 — pen-proximity re-synth from client focusIn /
            # showEvent. Drives the per-session PenFSM. Both transitions
            # are idempotent (see common/session_fsm.py::PenFSM) so
            # duplicate emissions across rapid focus / show storms (Cmd-
            # Tab cycles, lockscreen wakes, virtual-desktop switches) are
            # safe by construction. PITFALLS #3 mitigation.
            in_prox = bool(msg.get("in_proximity", False))
            try:
                if in_prox:
                    self._pen_fsm.send("enter_proximity")
                else:
                    self._pen_fsm.send("leave_proximity")
                logger.info(
                    "input.pen_proximity in_proximity=%s pen_state=%s "
                    "client_id=%s",
                    in_prox,
                    self._pen_fsm.current_state.id,
                    session.client_id,
                )
            except Exception as e:
                # Idempotent transitions should never raise, but defensive
                # logging matches the pattern used by the modifier-reset
                # path above (D-11) — never let a single message kill the
                # session loop.
                logger.debug("input.pen_proximity_failed: %s", e)

        elif msg_type == MsgType.TEXT_COMMIT:
            # Phase 2 D-15 — IME / dead-key passthrough. Server injects as
            # the desktop user's keystrokes (xdotool on Linux,
            # CGEventKeyboardSetUnicodeString on Mac). Per threat T-02-24
            # this is no more privileged than any focused-window typing.
            text = msg.get("text", "")
            if text and hasattr(self.injector, "text_commit"):
                try:
                    self.injector.text_commit(text)
                    logger.info(
                        "input.text_commit char_count=%d client_id=%s",
                        len(text), session.client_id,
                    )
                except Exception as e:
                    logger.debug("input.text_commit_failed: %s", e)

        elif msg_type in (MsgType.MOUSE_MOVE, MsgType.MOUSE_BUTTON,
                          MsgType.MOUSE_SCROLL, MsgType.PEN_EVENT):
            self.injector.handle_message(msg)

        elif msg_type == MsgType.REQUEST_FULL_FRAME:
            self.capture.invalidate()
            if self.encoder:
                self.encoder.request_keyframe()

        elif msg_type == MsgType.QUALITY_SETTINGS:
            self.apply_quality(session, msg)

        elif msg_type == MsgType.RESIZE_REQUEST:
            # Resize temporarily disabled — xrandr triggers SIGSEGV in
            # NVIDIA X11 libraries, crashing the entire server process.
            # TODO: investigate safe resize path for GPU displays
            logger.debug("Resize request ignored (disabled to prevent SEGV)")

        elif msg_type == MsgType.SELECT_MONITOR:
            self.capture.switch_monitor(msg.get("monitor_id", 1))
            self.encoder_lifecycle.restart()

        elif msg_type == MsgType.HEALTH_PONG:
            self.health.record_pong(msg.get("sequence", 0),
                                    msg.get("ping_timestamp_ms", 0))

        elif msg_type == MsgType.HEALTH_PING:
            # STAB-06 / Plan 01-08 — client originates HealthPing and stamps
            # it with client_state from its ClientFSM. Server reads the
            # state, checks the (client_state, server_state) pair against
            # ALLOWED_PAIRS, and flags disagreements.
            # Plan 01-14 (OBS) upgrades the disagreement emit from a stdlib
            # logger.warning to a structlog ERROR event via check_state_pair
            # so the overlay / dashboard can filter on level=error.
            incoming_client_state = msg.get("client_state", "")
            session.last_reported_client_state = incoming_client_state
            try:
                check_state_pair(
                    incoming_client_state,
                    session.fsm.current_state.id,
                    session.client_id,
                )
            except Exception:
                pass

        elif msg_type == MsgType.CLIPBOARD_SEND:
            if not self.clipboard:
                return
            content_type = msg.get("content_type", "text/plain")
            # Phase 3 D-15 — per-direction c2s gate. Drop silently
            # (no error response) so a misconfigured peer doesn't learn
            # the toggle state from server-side side effects.
            if content_type == "image/png":
                if not getattr(session, "clipboard_image_c2s", True):
                    return
            else:
                if not getattr(session, "clipboard_text_c2s", True):
                    return
            # Single-shot text path. Image payloads arrive via
            # CLIPBOARD_CHUNK even for small PNGs — this branch only
            # handles text.
            if content_type == "text/plain":
                self.clipboard.set_clipboard(msg.get("data", ""))

        elif msg_type == MsgType.CLIPBOARD_CHUNK:
            if not self.clipboard:
                return
            content_type = msg.get("content_type", "text/plain")
            try:
                seq_id = int(msg.get("sequence_id", 0))
                chunk_index = int(msg.get("chunk_index", 0))
                total_chunks = int(msg.get("total_chunks", 1))
            except (TypeError, ValueError):
                logger.warning("clipboard.chunk_bad_fields msg_keys=%s",
                                list(msg.keys()))
                return

            # Phase 3 Pitfall 7 — toggle gating at the chunk-0 boundary,
            # NOT per-chunk. If the client disables the direction AFTER
            # chunk 0 arrived, the in-flight sequence still completes
            # (the payload was already authorized when chunk 0 crossed
            # the boundary). The toggle applies to the NEXT sequence_id.
            # Subsequent chunks of a dropped sequence check
            # ``_dropped_seqs`` and silently no-op.
            dropped_seqs = getattr(session, "_dropped_seqs", None)
            if dropped_seqs is None:
                dropped_seqs = set()
                session._dropped_seqs = dropped_seqs
            if chunk_index == 0:
                if content_type == "image/png" and not getattr(
                    session, "clipboard_image_c2s", True,
                ):
                    track_dropped_seq(dropped_seqs, seq_id)
                    logger.info(
                        "clipboard.chunk_dropped_toggle seq=%d content_type=%s",
                        seq_id, content_type,
                    )
                    return
                if content_type == "text/plain" and not getattr(
                    session, "clipboard_text_c2s", True,
                ):
                    track_dropped_seq(dropped_seqs, seq_id)
                    logger.info(
                        "clipboard.chunk_dropped_toggle seq=%d content_type=%s",
                        seq_id, content_type,
                    )
                    return
            if seq_id in dropped_seqs:
                # Mid-stream drop continuation — chunks arriving after
                # the chunk-0 drop decision no-op silently.
                return

            assemblers = getattr(session, "_clipboard_chunks", None)
            if assemblers is None:
                assemblers = {}
                session._clipboard_chunks = assemblers

            asm = assemblers.get(seq_id)
            if asm is None:
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
                assemblers[seq_id] = asm

            full_b64 = asm.add(chunk_index, msg.get("data", ""))
            if full_b64 is not None:
                import base64
                try:
                    raw = base64.b64decode(full_b64)
                except Exception:
                    logger.warning(
                        "clipboard.chunk_b64_decode_failed seq=%d", seq_id,
                    )
                    del assemblers[seq_id]
                    return
                # D-16 defense-in-depth — re-validate magic byte + size
                # on receive BEFORE touching the system clipboard.
                if content_type == "image/png":
                    # Local import to avoid boot-time coupling.
                    from server.clipboard import validate_png_payload
                    if validate_png_payload(raw):
                        self.clipboard.set_clipboard_image(raw)
                else:
                    self.clipboard.set_clipboard(
                        raw.decode("utf-8", errors="replace"),
                    )
                del assemblers[seq_id]
                logger.info(
                    "clipboard.chunk_assembled seq=%d content_type=%s size=%d",
                    seq_id, content_type, len(raw),
                )

            # T-03-25 periodic stale cleanup — runs on every inbound
            # chunk so an attacker who starts a sequence and abandons
            # it can't pin memory past CHUNK_TIMEOUT_S.
            now = time.monotonic()
            stale = [s for s, a in assemblers.items() if a.is_stale(now)]
            for s in stale:
                logger.warning("clipboard.chunk_timeout seq=%d", s)
                del assemblers[s]

        elif msg_type in (MsgType.FILE_OFFER, MsgType.FILE_CHUNK,
                          MsgType.FILE_DONE, MsgType.FILE_CANCEL):
            response = self.file_receiver.handle_message(msg)
            if response:
                self._send_to_client(session, response)

        elif msg_type in (MsgType.USB_DEVICE_LIST, MsgType.USB_ATTACH,
                          MsgType.USB_DETACH):
            if self.usb_manager is not None:
                response = self.usb_manager.handle_message(msg, session.client_host)
                if response:
                    self._send_to_client(session, response)

        elif msg_type == MsgType.CLIENT_HELLO:
            session.supports_h264 = msg.get("supports_h264", True)
            session.supports_h265 = msg.get("supports_h265", False)
            session.supports_yuv444 = msg.get("supports_yuv444", True)
            session.supports_audio = msg.get("supports_audio", True)
            session.client_screen_width = msg.get("screen_width", 0)
            session.client_screen_height = msg.get("screen_height", 0)
            logger.info("Client screen: %dx%d", session.client_screen_width, session.client_screen_height)
            # Phase 3 D-02 — read capture_mode + picked_monitor_{id,name}
            # off the handshake and apply the per-session crop. Defaults
            # preserve pre-Phase-3 always-full-virtual-desktop behavior.
            try:
                capture_mode = str(msg.get("capture_mode", "mirror_all"))
                picked_id = int(msg.get("picked_monitor_id", -1))
                picked_name = str(msg.get("picked_monitor_name", ""))
                self.apply_capture_mode(
                    session, capture_mode, picked_id, picked_name,
                )
            except Exception as e:
                logger.warning(
                    "session.apply_capture_mode_failed err=%s", e,
                )
            # Phase 3 D-15 / Plan 03-06 — per-direction clipboard toggles.
            # Defaults True preserve pre-Phase-3 legacy clipboard behavior
            # (client hello omitting the fields → all 4 directions ON).
            # Pitfall 7 race: re-reading toggles on every hello is safe
            # because in-flight CLIPBOARD_CHUNK sequences are keyed on
            # session._dropped_seqs and session._clipboard_chunks, not
            # on the toggle value at chunk-N arrival time.
            session.clipboard_text_c2s = bool(
                msg.get("clipboard_text_c2s", True),
            )
            session.clipboard_text_s2c = bool(
                msg.get("clipboard_text_s2c", True),
            )
            session.clipboard_image_c2s = bool(
                msg.get("clipboard_image_c2s", True),
            )
            session.clipboard_image_s2c = bool(
                msg.get("clipboard_image_s2c", True),
            )
            # STAB-06 / Plan 01-08 — capability_exchange → streaming.
            try:
                session.fsm.send("client_hello")
            except Exception:
                pass

        if is_input_event:
            elapsed_ms = (time.time() - t0) * 1000
            self.health.record_input_latency(elapsed_ms)

    # ── Shutdown ─────────────────────────────────────────────

    def stop(self):
        self._running = False
        # Phase 2 D-11 — cancel the periodic safety-net task before tearing
        # down the injector so we don't race a fire-during-teardown.
        if self._modifier_safety_task is not None:
            try:
                self._modifier_safety_task.cancel()
            except Exception:
                pass
            self._modifier_safety_task = None
        # D-11 / Plan 01-10: delegate loop + encoder teardown to the
        # sub-objects. Each sub-loop owns its own task cancellation;
        # encoder_lifecycle.stop() keeps the back-compat self.encoder
        # handle in sync (sets it to None alongside its internal handle).
        self._stream_loop.stop()
        self._health_loop.stop()
        self._hotplug.stop()
        self.encoder_lifecycle.stop()
        if self.audio:
            self.audio.stop()
        if self.clipboard:
            self.clipboard.stop()
        if self.cursor_tracker:
            self.cursor_tracker.stop()
        if self.usb_manager:
            self.usb_manager.cleanup()
        if self.injector:
            self.injector.close()
        if self.capture:
            self.capture.close()
        logger.info("[%s] Session runtime stopped", self.username)
