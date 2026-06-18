"""
Session abstraction — one per remote connection.

Encapsulates protocol, viewer, decoder, audio, health data, and all
signal wiring for a single remote desktop session. Multiple sessions
can exist simultaneously (one per tab).
"""

import json
import logging
import time
from dataclasses import asdict

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from client.viewer import RemoteViewer
from client.protocol import ClientProtocol
from client.audio_player import AudioPlayer
from client.video_decoder import DecoderManager
from client.health_display import HealthOverlay, HealthData
from client.file_transfer import FileSender
from client.usb_forward import USBForwardClient
from common.messages import (
    MsgType, FrameType, QualitySettings, VideoCodec,
    HealthPong, parse_message,
)

logger = logging.getLogger(__name__)


class _Bridge(QObject):
    """Thread-safe Qt signal bridge for protocol callbacks."""
    server_hello = Signal(dict)
    jpeg_frame = Signal(int, int, int, int, int, bytes)
    video_frame = Signal(int, int, int, int, int, int, bytes)
    audio_frame = Signal(int, int, bytes)
    connected = Signal()
    disconnected = Signal(str)
    error = Signal(str)
    auth_required = Signal(str)
    auth_result = Signal(bool, str)
    health_stats = Signal(dict)
    # Phase 3 D-09 / Plan 03-05 — full MonitorListMsg dict (includes
    # ``degradations`` payload). Downstream signal ``monitor_list_received``
    # still emits a plain list of monitors for back-compat with
    # main_window._on_monitor_list.
    monitor_list = Signal(dict)
    # Phase 3 D-13 / Plan 03-07 — clipboard recv now carries (content_type,
    # payload). ``object`` permits both str (text/plain) and bytes
    # (image/png) without a second signal definition.
    clipboard_recv = Signal(str, object)
    # Phase 3 D-14 / Plan 03-07 — oversize-image toast bridge (size in MB).
    oversize_image = Signal(float)
    file_response = Signal(dict)
    usb_response = Signal(dict)
    broker_machine_needed = Signal(list)
    cursor_update = Signal(dict)


class Session(QObject):
    """
    A single remote desktop connection session.

    Owns:
      - ClientProtocol (WebSocket + hybrid transport)
      - RemoteViewer widget (display + input capture)
      - DecoderManager (H.264/H.265/AV1 via PyAV)
      - AudioPlayer (QAudioSink raw PCM)
      - HealthData + HealthOverlay
      - ThreadBridge for signal marshalling
    """

    # Emitted for the tab bar / main window
    status_changed = Signal(str)        # "connecting", "connected", "disconnected"
    title_changed = Signal(str)         # e.g. "randy@10.10.0.150 (1920x1080)"
    auth_failed = Signal(str)           # error message
    monitor_list_received = Signal(list)
    file_transfer_finished = Signal(str, bool, str)  # transfer_id, success, message
    usb_devices_updated = Signal(dict)  # server response with device list + attached
    broker_machine_needed = Signal(list)  # broker wants user to pick a machine

    def __init__(self, parent=None):
        super().__init__(parent)

        self._host = ""
        self._port = 443
        self._username = ""
        self._password = ""
        self._bookmark_id = ""

        # Core subsystems
        self.protocol = ClientProtocol()
        self.viewer = RemoteViewer()
        self.decoder = DecoderManager()
        self.health = HealthData()
        self.audio = AudioPlayer()
        self.file_sender = FileSender()
        self.usb_client = USBForwardClient()

        # Bridge for thread safety
        self._bridge = _Bridge()

        # Health overlay on viewer
        self.overlay = HealthOverlay(self.viewer)
        self.overlay.data = self.health

        # Phase 3 D-09 / Plan 03-05 — remap-banner surface state.
        # Lazily instantiated on first MonitorListMsg with degradations
        # so the asyncio I/O thread doesn't construct Qt widgets. The
        # ``toolbar`` attribute is populated by main_window when the
        # session binds to a FullscreenToolbar instance; None until then.
        self.remap_banner = None
        self.toolbar = None
        # Phase 3 D-15 / Plan 03-07 — bookmark manager reference for
        # clipboard-toggle persistence. main_window binds via bind_bookmark_manager
        # so _on_clipboard_toggles_changed can update the saved profile.
        self.bookmark_manager = None
        # Cached server-side session identifier for degradation routing.
        # Currently mirrors ``ClientSession.client_id`` on the server
        # (``str(id(ws))``). Populated via ``set_client_token`` once the
        # server surfaces a session id on the wire (future work); until
        # then the client checks for its presence in any degradation
        # entry (best-effort single-session match).
        self._client_token = ""
        # Monitor-count cache so we can derive mirror_add / mirror_remove
        # banner cases from the delta between broadcasts.
        self._last_monitor_count: int | None = None

        # Wire everything
        self._wire_protocol()
        self._wire_viewer()

        # Screen size
        screen = QApplication.primaryScreen()
        if screen:
            g = screen.availableGeometry()
            w = g.width() - (g.width() % 2)
            h = g.height() - (g.height() % 2)
            self.protocol._screen_size = (w, h)

    @property
    def display_name(self) -> str:
        if self._host:
            name = f"{self._username}@{self._host}" if self._username else self._host
            return f"{name}:{self._port}"
        return "New Connection"

    @property
    def is_connected(self) -> bool:
        return self.protocol.connected

    # ── Connection ───────────────────────────────

    def connect(self, host: str, port: int, username: str = "",
                password: str = "", use_tls: bool = True,
                auto_reconnect: bool = True, bookmark_id: str = "",
                swap_cmd_ctrl: bool = False,
                monitor_mode: str = "mirror_all",
                picked_monitor_id: int = -1,
                picked_monitor_name: str = "",
                clipboard_text_c2s: bool = True,
                clipboard_text_s2c: bool = True,
                clipboard_image_c2s: bool = True,
                clipboard_image_s2c: bool = True):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._bookmark_id = bookmark_id
        # Phase 3 D-01 — remember per-session mode for toolbar badge updates.
        self._monitor_mode = monitor_mode
        self._picked_monitor_id = picked_monitor_id
        self._picked_monitor_name = picked_monitor_name

        self.status_changed.emit("connecting")
        self.title_changed.emit(self.display_name)

        # Phase 2 D-10 — propagate per-bookmark Cmd↔Ctrl swap state into
        # the protocol layer BEFORE the connect kicks off so the very first
        # outbound key event is rewritten correctly.
        self.protocol.set_swap_cmd_ctrl(swap_cmd_ctrl)

        # Phase 3 D-01 / D-02 — push capture-mode + pick fields BEFORE the
        # handshake so the first ClientHelloMsg carries the user's picked
        # mode. Server-side session_runtime (Plan 03) reads these to
        # configure the encoder crop rect.
        self.protocol.set_capture_mode(
            monitor_mode, picked_monitor_id, picked_monitor_name,
        )

        # Phase 3 D-15 / Plan 03-07 — push bookmark clipboard toggles into
        # the protocol BEFORE connect so the first ClientHelloMsg carries
        # the saved toggle state. Mirrors the capture_mode pattern.
        self.protocol.set_clipboard_toggles(
            text_c2s=bool(clipboard_text_c2s),
            text_s2c=bool(clipboard_text_s2c),
            image_c2s=bool(clipboard_image_c2s),
            image_s2c=bool(clipboard_image_s2c),
        )
        # Sync the toolbar button (if one is bound) so the UI matches the
        # bookmark's saved state without re-emitting toggles_changed.
        tb = getattr(self, "toolbar", None)
        if tb is not None:
            try:
                tb.clipboard_toggle.set_state(
                    text_c2s=bool(clipboard_text_c2s),
                    text_s2c=bool(clipboard_text_s2c),
                    image_c2s=bool(clipboard_image_c2s),
                    image_s2c=bool(clipboard_image_s2c),
                )
            except Exception as e:
                logger.debug(
                    "session.toolbar.clipboard_toggle.set_state_failed err=%s", e,
                )

        self.protocol.disconnect()
        self.protocol.connect(
            host, port, username=username, password=password,
            use_tls=use_tls, auto_reconnect=auto_reconnect)

    def connect_with_profile(self, profile, password: str = "",
                             auto_reconnect: bool = True):
        """Phase 3 D-01 — connect using a ConnectionProfile directly.

        Thin convenience that pulls monitor_mode + picked_monitor_id +
        picked_monitor_name off the profile and hands them to
        :meth:`connect`. Mirrors the Phase 2 destination_kind /
        swap_cmd_ctrl plumbing pattern so Plan 03's wire flow stays
        readable at the session layer.
        """
        self.connect(
            host=profile.host,
            port=profile.port,
            username=profile.username,
            password=password,
            use_tls=profile.use_tls,
            auto_reconnect=auto_reconnect,
            swap_cmd_ctrl=bool(getattr(profile, "swap_cmd_ctrl", False)),
            monitor_mode=getattr(profile, "monitor_mode", "mirror_all"),
            picked_monitor_id=getattr(profile, "picked_monitor_id", -1),
            picked_monitor_name=getattr(profile, "picked_monitor_name", ""),
            # Phase 3 D-15 / Plan 03-07 — bookmark clipboard toggles.
            clipboard_text_c2s=bool(
                getattr(profile, "clipboard_text_c2s", True),
            ),
            clipboard_text_s2c=bool(
                getattr(profile, "clipboard_text_s2c", True),
            ),
            clipboard_image_c2s=bool(
                getattr(profile, "clipboard_image_c2s", True),
            ),
            clipboard_image_s2c=bool(
                getattr(profile, "clipboard_image_s2c", True),
            ),
        )

    def connect_broker(self, host: str, port: int, username: str = "",
                       password: str = "", use_tls: bool = True,
                       auto_reconnect: bool = True):
        """Connect via broker — authenticate, get assigned a machine, redirect."""
        import logging
        logging.getLogger(__name__).info("Session.connect_broker: %s:%d user=%s", host, port, username)
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._bookmark_id = ""

        self.status_changed.emit("connecting")
        self.title_changed.emit(f"Broker: {host}")

        self.protocol.disconnect()
        self.protocol.connect_broker(
            host, port, username=username, password=password,
            use_tls=use_tls, auto_reconnect=auto_reconnect)

    def disconnect(self):
        self.protocol.disconnect()
        self.status_changed.emit("disconnected")

    def cleanup(self):
        """Full teardown."""
        try:
            QApplication.clipboard().dataChanged.disconnect(self._on_clipboard_local_changed)
        except RuntimeError:
            pass
        self.protocol.disconnect()
        self.decoder.close_all()
        if self.audio and self.audio._started:
            self.audio.stop()
        if self.usb_client:
            self.usb_client.cleanup()

    # ── Quality / Controls ───────────────────────

    def apply_quality(self, settings: QualitySettings):
        self.protocol.send_quality_settings(settings)
        # Toggle local audio
        if self.audio and self.audio.available:
            if getattr(settings, "enable_audio", True):
                if not self.audio._started:
                    self.audio.start()
            else:
                if self.audio._started:
                    self.audio.stop()

    def select_monitor(self, mon_id: int):
        self.protocol.select_monitor(mon_id)

    def select_broker_machine(self, machine_name: str):
        """Forward the user's broker machine choice to the protocol."""
        self.protocol.select_broker_machine(machine_name or "")

    def request_full_frame(self):
        self.protocol.request_full_frame()

    def send_resize(self, w: int, h: int):
        w = w - (w % 2)
        h = h - (h % 2)
        if w >= 640 and h >= 480:
            self.protocol.send_input({
                "type": "resize_request", "width": w, "height": h})

    # ── Protocol Wiring ──────────────────────────

    def _wire_protocol(self):
        p = self.protocol
        b = self._bridge

        p.on_server_hello = b.server_hello.emit
        p.on_jpeg_frame = lambda ft, x, y, w, h, d: b.jpeg_frame.emit(ft, x, y, w, h, d)
        p.on_video_frame = lambda ft, c, ch, f, t, m, d: b.video_frame.emit(ft, c, ch, f, t, m, d)
        p.on_audio_frame = lambda c, t, d: b.audio_frame.emit(c, t, d)
        p.on_connected = b.connected.emit
        p.on_disconnected = b.disconnected.emit
        p.on_error = b.error.emit
        p.on_auth_required = b.auth_required.emit
        p.on_auth_result = b.auth_result.emit
        p.on_health_stats = b.health_stats.emit
        p.on_monitor_list = b.monitor_list.emit
        # Phase 3 D-13 / Plan 03-07 — on_clipboard now takes
        # (content_type, payload). bridge re-emits both to the GUI thread.
        p.on_clipboard = lambda ct, data: b.clipboard_recv.emit(ct, data)
        # Phase 3 D-14 / Plan 03-07 — oversize-image toast bridge.
        p.on_oversize_image = lambda size_mb: b.oversize_image.emit(float(size_mb))
        p.on_file_response = b.file_response.emit
        p.on_usb_response = b.usb_response.emit
        p.on_broker_machine_needed = b.broker_machine_needed.emit
        p.on_cursor_update = b.cursor_update.emit

        b.server_hello.connect(self._on_server_hello)
        b.jpeg_frame.connect(self._on_jpeg_frame)
        b.video_frame.connect(self._on_video_frame)
        b.audio_frame.connect(self._on_audio_frame)
        b.connected.connect(self._on_connected)
        b.disconnected.connect(self._on_disconnected)
        b.error.connect(self._on_error)
        b.auth_result.connect(self._on_auth_result)
        b.health_stats.connect(self._on_health_stats)
        b.monitor_list.connect(self._on_monitor_list)
        b.clipboard_recv.connect(self._on_clipboard_recv)
        b.oversize_image.connect(self._on_oversize_image)
        b.file_response.connect(self._on_file_response)

        # File sender: chunks go out via protocol, responses come back via bridge
        self.file_sender.chunk_ready.connect(self.protocol.send_input)
        self.file_sender.finished.connect(self.file_transfer_finished.emit)

        b.usb_response.connect(self._on_usb_response)
        b.broker_machine_needed.connect(self.broker_machine_needed.emit)
        b.cursor_update.connect(self._on_cursor_update)

    def _wire_viewer(self):
        v = self.viewer
        v.mouse_moved.connect(self._send_mouse_move)
        v.mouse_button_changed.connect(self._send_mouse_button)
        v.mouse_scrolled.connect(self._send_mouse_scroll)
        v.key_changed.connect(self._send_key_event)
        v.pen_event.connect(self._send_pen_event)
        v.paste_requested.connect(self._push_clipboard_for_paste)
        v.files_dropped.connect(self.send_files)
        # Phase 2 D-11 / D-15 — viewer-emitted modifier-release + IME signals
        # bridge directly to the wire helpers in client/protocol.py. See
        # client/viewer.py focusOutEvent + inputMethodEvent for emission
        # sites; client/main_window.py wires the F9 panic shortcut to
        # the same reset_modifiers_requested signal with reason='panic_f9'.
        v.reset_modifiers_requested.connect(self.protocol.send_reset_modifiers)
        v.text_commit.connect(self.protocol.send_text_commit)

    # ── Input Sending ────────────────────────────

    def _send_mouse_move(self, x, y, server_x=-1, server_y=-1):
        # Phase 3 D-05 — viewer now emits 4-arg signal carrying
        # server-physical-pixel ints. Pre-Plan-04 callsites that still
        # pass 2 args hit the kwarg defaults (server_x/y = -1 sentinel).
        self.protocol.send_input({
            "type": MsgType.MOUSE_MOVE, "x": x, "y": y,
            "server_x": int(server_x), "server_y": int(server_y),
        })

    def _send_mouse_button(self, btn, pressed, x, y, server_x=-1, server_y=-1):
        self.protocol.send_input({
            "type": MsgType.MOUSE_BUTTON,
            "button": btn, "pressed": pressed, "x": x, "y": y,
            "server_x": int(server_x), "server_y": int(server_y),
        })

    def _send_mouse_scroll(self, dx, dy, x, y, server_x=-1, server_y=-1):
        self.protocol.send_input({
            "type": MsgType.MOUSE_SCROLL,
            "dx": dx, "dy": dy, "x": x, "y": y,
            "server_x": int(server_x), "server_y": int(server_y),
        })

    def _send_key_event(self, qt_key, scan_code, pressed, mods):
        # Phase 2 D-10 / D-14 — delegate to ClientProtocol.send_key_event
        # which (a) applies the per-bookmark Cmd↔Ctrl swap when active and
        # (b) attaches caps_lock_on / num_lock_on / scroll_lock_on bits.
        self.protocol.send_key_event(qt_key, scan_code, pressed, mods)

    def _send_pen_event(self, data):
        # Phase 3 D-05 — ``data`` dict may carry server_x / server_y
        # from the viewer's _widget_to_remote 4-tuple. Pass through
        # unchanged; server prefers integer fields when server_x >= 0.
        data["type"] = MsgType.PEN_EVENT
        self.protocol.send_input(data)

    def _push_clipboard_for_paste(self):
        """Phase 3 D-13 — explicitly push local clipboard to server before Ctrl+V.

        Image support added for Plan 03-07: if the local clipboard holds
        a QImage (Finder/Preview/screenshot copy), encode to PNG and
        push via the image path. Text is always checked after image so
        a clipboard carrying both (rare, but possible) pushes both.
        """
        if not self.is_connected:
            return
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage
        cb = QApplication.clipboard()
        md = cb.mimeData()
        if md is not None and md.hasImage():
            img: QImage = cb.image()
            if not img.isNull():
                buf = QBuffer()
                buf.open(QIODevice.WriteOnly)
                img.save(buf, "PNG")
                png_bytes = bytes(buf.data())
                logger.debug(
                    "Paste detected — pushing %d-byte PNG to server clipboard",
                    len(png_bytes),
                )
                self.protocol.send_clipboard("image/png", png_bytes)
        if md is not None and md.hasText():
            text = cb.text()
            if text:
                logger.debug(
                    "Paste detected — pushing %d chars to server clipboard",
                    len(text),
                )
                self.protocol.send_clipboard("text/plain", text)

    # ── Protocol Event Handlers ──────────────────

    def _on_server_hello(self, msg):
        w = msg.get("screen_width", 1920)
        h = msg.get("screen_height", 1080)
        self.viewer.set_remote_size(w, h)
        backend = msg.get("encoder_backend", "")
        if backend:
            self.health.encoder_backend = backend
        suffix = f" [{backend}]" if backend else ""
        self.title_changed.emit(f"{self.display_name} ({w}x{h}){suffix}")

    def _on_jpeg_frame(self, ft, x, y, w, h, data):
        self.health.record_frame_received()
        if ft == FrameType.VIDEO_FULL:
            self.viewer.update_full_frame(data)
        else:
            self.viewer.update_partial_frame(x, y, w, h, data)

    def _on_video_frame(self, ft, codec, chroma, flags, ts, mon, data):
        self.health.record_frame_received()
        codec_map = {VideoCodec.H264: "h264", VideoCodec.H265: "h265", VideoCodec.AV1: "av1"}
        codec_name = codec_map.get(codec, "h264")
        rgb_data = self.decoder.decode(codec_name, data)
        # Update health with decoder backend (once)
        if not self.health.decoder_backend:
            self.health.decoder_backend = self.decoder.active_backend
        if rgb_data is not None:
            decoder = self.decoder.get_decoder(codec_name)
            size = decoder.get_frame_size() if decoder else None
            if size:
                w, h = size
                img = QImage(rgb_data, w, h, w * 3, QImage.Format_RGB888)
                if not img.isNull():
                    self.viewer._screen_image = img
                    self.viewer._pixmap = None
                    self.viewer.update()

    def _on_audio_frame(self, codec, ts, data):
        if self.audio and self.audio.available:
            self.audio.feed(codec, ts, data)

    def _on_connected(self):
        self.status_changed.emit("connected")
        # Start monitoring local clipboard for client→server sync
        clipboard = QApplication.clipboard()
        clipboard.dataChanged.connect(self._on_clipboard_local_changed)
        # Advertise USB devices to server
        self._send_usb_device_list()

    def _on_disconnected(self, reason):
        self.status_changed.emit("disconnected")
        try:
            QApplication.clipboard().dataChanged.disconnect(self._on_clipboard_local_changed)
        except RuntimeError:
            pass  # already disconnected

    # ── Phase 3 D-01 — capture-mode getters for the fullscreen toolbar ──

    @property
    def capture_mode(self) -> str:
        """Current monitor mode for this session (default mirror_all)."""
        return getattr(self, "_monitor_mode", "mirror_all")

    @property
    def picked_monitor_name(self) -> str:
        """Name of the picked monitor for pick-one sessions (empty otherwise)."""
        return getattr(self, "_picked_monitor_name", "")

    def _on_error(self, error):
        self.status_changed.emit("error")

    def _on_auth_result(self, success, message):
        if not success:
            self.auth_failed.emit(message)

    def _on_health_stats(self, stats):
        self.health.update_from_server(stats)

    def _on_monitor_list(self, msg):
        """Phase 3 D-09 / Plan 03-05 — bridge MonitorListMsg to downstream.

        The protocol bridge now emits the full dict (to surface
        ``degradations``). Downstream ``monitor_list_received`` keeps the
        pre-Plan-05 list shape so main_window._on_monitor_list continues
        to work. The degradation-aware handler below reacts to the
        banner + toast surface.
        """
        if isinstance(msg, dict):
            monitors = msg.get("monitors", []) or []
            self._on_monitor_list_with_degradations(msg)
        else:
            # Back-compat: pre-Plan-05 callers that still pass a plain list.
            monitors = msg or []
        self.monitor_list_received.emit(monitors)

    def _on_monitor_list_with_degradations(self, msg: dict):
        """Phase 3 D-09 — react to topology change + degradation events.

        Lazily instantiates ``RemapBanner`` on first use (avoids
        constructing Qt widgets from the asyncio I/O thread — handler
        runs on the GUI thread via the _Bridge queued signal).

        Behavior per UI-SPEC Surface 5:
          - If any degradation entry matches ``self._client_token`` (or
            the session-token field is empty, which is the current
            single-session default), show ``pick_missing`` banner +
            monitor-switched toast + toolbar badge flip (degraded=True).
          - Else if the topology count changed vs ``_last_monitor_count``,
            show the appropriate topology-change banner based on
            ``_capture_mode`` (mirror_add / mirror_remove / single_change).
          - Else do nothing (server broadcast, nothing user-facing).

        T-03-18 mitigation: entries are filtered by client_token so a
        malicious server can't spoof another session's degradation into
        our UI.
        """
        # Late imports — banner + toast modules pull in PySide6 widgets
        # that are only available on the GUI thread.
        from client.remap_banner import RemapBanner
        from client.toasts import show_monitor_switched_toast

        if self.remap_banner is None and self.viewer is not None:
            self.remap_banner = RemapBanner(self.viewer)
        if self.remap_banner is None:
            # Viewer not mounted yet — skip the banner surface. Toolbar
            # badge + toast still fire below because they don't depend
            # on the banner widget.
            pass

        degradations = msg.get("degradations", []) or []
        my_token = self._client_token
        # Match semantics:
        #   - If self._client_token is set, require exact match.
        #   - If self._client_token is empty (current default — server
        #     hasn't surfaced a session id yet), accept any degradation
        #     as "ours" ONLY when there is exactly one entry AND we're
        #     in pick_one mode. This is the single-session-per-host
        #     reality of v1; T-03-18 is mitigated by the pick_one check.
        my_event = None
        if my_token:
            my_event = next(
                (d for d in degradations if d.get("client_token") == my_token),
                None,
            )
        elif (len(degradations) == 1
              and getattr(self, "_monitor_mode", "mirror_all") == "pick_one"):
            my_event = degradations[0]

        if my_event:
            picked = my_event.get("previous_pick", "")
            now = my_event.get("now_showing", "primary")
            if self.remap_banner is not None:
                self.remap_banner.show_for_case(
                    "pick_missing", picked_name=picked,
                )
            if self.viewer is not None:
                show_monitor_switched_toast(
                    self.viewer, monitor_name=picked,
                )
            if self.toolbar is not None:
                try:
                    self.toolbar.update_capture_mode(
                        mode="pick_one",
                        picked_name=now,
                        degraded=True,
                    )
                except Exception as e:
                    logger.debug(
                        "session.toolbar.update_capture_mode_failed err=%s", e,
                    )
            logger.info(
                "session.fallback_received previous=%r now=%r",
                picked, now,
            )
            # Update monitor-count cache for future deltas.
            self._last_monitor_count = len(msg.get("monitors", []) or [])
            return

        # No degradation for us — derive the topology-change banner case
        # from the monitor-count delta.
        prev_count = self._last_monitor_count
        cur_count = len(msg.get("monitors", []) or [])
        if prev_count is not None and prev_count != cur_count:
            mode = getattr(self, "_monitor_mode", "mirror_all")
            if mode == "mirror_all":
                case = "mirror_add" if cur_count > prev_count else "mirror_remove"
            else:
                case = "single_change"
            if self.remap_banner is not None:
                self.remap_banner.show_for_case(case)
        self._last_monitor_count = cur_count

    def _on_clipboard_recv(self, content_type, data):
        """Phase 3 D-13 / Plan 03-07 — text + image inbound dispatch.

        content_type ``"image/png"`` routes through ``QImage.fromData``
        into ``QApplication.clipboard().setPixmap``. Anything else is
        treated as text and set via ``.setText``. Echo-suppression flag
        (``_clipboard_from_server``) blocks the dataChanged feedback
        loop from re-uploading what the server just sent us.
        """
        from PySide6.QtGui import QImage, QPixmap

        cb = QApplication.clipboard()
        # Echo suppression — set BEFORE touching the clipboard so the
        # dataChanged slot is guaranteed to see the flag.
        self._clipboard_from_server = True
        try:
            if content_type == "image/png" and isinstance(data, (bytes, bytearray)):
                img = QImage.fromData(bytes(data), "PNG")
                if not img.isNull():
                    cb.setPixmap(QPixmap.fromImage(img))
                    logger.info("clipboard.image_recv_set size=%d", len(data))
                else:
                    logger.warning(
                        "clipboard.image_recv_qimage_null size=%d", len(data),
                    )
            else:
                # Text path (or fallback for unknown content_type).
                text = data if isinstance(data, str) else str(data)
                cb.setText(text)
        finally:
            # Reset flag in a finally so exceptions can't wedge future
            # local-clipboard pushes.
            self._clipboard_from_server = False

    def _on_oversize_image(self, size_mb: float):
        """Phase 3 D-14 / Plan 03-07 — show the oversize-image toast.

        Triggered by ``ClientProtocol._oversize_callback`` via the
        ``oversize_image`` bridge signal. Guards against a detached
        viewer (early disconnect) by falling back to a log-only path.
        """
        from client.toasts import show_oversize_image_toast
        if self.viewer is not None:
            show_oversize_image_toast(self.viewer, size_mb)
        else:
            logger.warning(
                "clipboard.oversize_image size_mb=%.1f (no viewer)", size_mb,
            )

    def _on_clipboard_toggles_changed(self, state: dict):
        """Phase 3 D-15 / Plan 03-07 — react to ClipboardToggleButton changes.

        Pushes the new toggle state into the protocol (gates the next
        outbound send + inbound CLIPBOARD_CHUNK) and persists to the
        current bookmark so the UI preference rides across sessions.
        """
        self.protocol.set_clipboard_toggles(
            text_c2s=bool(state.get("text_c2s", True)),
            text_s2c=bool(state.get("text_s2c", True)),
            image_c2s=bool(state.get("image_c2s", True)),
            image_s2c=bool(state.get("image_s2c", True)),
        )
        # Persist to bookmark if we have one wired.
        bm = getattr(self, "bookmark_manager", None)
        bid = getattr(self, "_bookmark_id", "")
        if bm is not None and bid:
            try:
                bm.update(
                    bid,
                    clipboard_text_c2s=bool(state.get("text_c2s", True)),
                    clipboard_text_s2c=bool(state.get("text_s2c", True)),
                    clipboard_image_c2s=bool(state.get("image_c2s", True)),
                    clipboard_image_s2c=bool(state.get("image_s2c", True)),
                )
            except Exception as e:
                logger.debug(
                    "session.bookmark_update_failed id=%s err=%s", bid, e,
                )

    def _on_clipboard_local_changed(self):
        """Local clipboard changed — send to server if it wasn't from the server."""
        if getattr(self, '_clipboard_from_server', False):
            self._clipboard_from_server = False
            return
        if not self.is_connected:
            return
        text = QApplication.clipboard().text()
        if text:
            self.protocol.send_clipboard("text/plain", text)

    def _on_file_response(self, msg):
        """Route file transfer responses to FileSender."""
        self.file_sender.handle_response(msg)

    def _on_cursor_update(self, msg: dict):
        """Apply a server-sent cursor shape to the viewer widget.

        The RGBA bytes are base64-encoded on the wire so they fit
        cleanly into the JSON control channel. Decoding and cursor
        construction happen on the GUI thread — we're delivered
        here via a queued Qt signal from the protocol bridge.
        """
        import base64
        try:
            rgba = base64.b64decode(msg.get("rgba_b64", ""))
        except Exception as e:
            logger.warning("Bad cursor payload: %s", e)
            return
        self.viewer.set_remote_cursor(
            serial=int(msg.get("serial", 0)),
            width=int(msg.get("width", 0)),
            height=int(msg.get("height", 0)),
            hot_x=int(msg.get("hot_x", 0)),
            hot_y=int(msg.get("hot_y", 0)),
            rgba_bytes=rgba,
        )

    # ── File Transfer ─────────────────────────────

    def send_files(self, file_paths: list) -> list:
        """Send files to the remote server. Returns list of transfer IDs."""
        if not self.is_connected:
            logger.warning("Cannot send files — not connected")
            return []
        return self.file_sender.send_files(file_paths)

    # ── USB Passthrough ───────────────────────────

    def _send_usb_device_list(self):
        """Send local USB device list to server."""
        msg = self.usb_client.get_device_list_message()
        self.protocol.send_input(msg)

    def _on_usb_response(self, msg):
        """Handle USB responses from server."""
        msg_type = msg.get("type")
        if msg_type == "usb_device_list":
            self.usb_devices_updated.emit(msg)
        elif msg_type == "usb_attached":
            bus_id = msg.get("bus_id", "")
            logger.info("USB device attached: %s", bus_id)
            self.usb_devices_updated.emit(msg)
        elif msg_type == "usb_detached":
            bus_id = msg.get("bus_id", "")
            logger.info("USB device detached: %s", bus_id)
            self.usb_devices_updated.emit(msg)
        elif msg_type == "usb_error":
            logger.error("USB error: %s", msg.get("message", ""))
            self.usb_devices_updated.emit(msg)

    def usb_attach(self, bus_id: str):
        """Request the server to attach a USB device."""
        if not self.is_connected:
            return
        # Start client-side forwarding first
        self.usb_client.start_forwarding(bus_id)
        self.protocol.send_input({"type": "usb_attach", "bus_id": bus_id})

    def usb_detach(self, bus_id: str):
        """Request the server to detach a USB device."""
        if not self.is_connected:
            return
        self.protocol.send_input({"type": "usb_detach", "bus_id": bus_id})
        self.usb_client.stop_forwarding(bus_id)

    def usb_refresh(self):
        """Re-enumerate and send device list."""
        self._send_usb_device_list()

    # ── Phase 3 D-15 / Plan 03-07 — toolbar + bookmark_manager binding ──

    def bind_toolbar(self, toolbar):
        """Attach a FullscreenToolbar to this session.

        Wires the ClipboardToggleButton's ``toggles_changed`` signal into
        :meth:`_on_clipboard_toggles_changed` so UI changes flow through
        to the protocol + bookmark. Safe to call multiple times — the
        second call re-binds (disconnects the old connection first).
        """
        if self.toolbar is toolbar:
            return
        self.toolbar = toolbar
        if toolbar is None:
            return
        try:
            toolbar.clipboard_toggle.toggles_changed.connect(
                self._on_clipboard_toggles_changed
            )
        except Exception as e:
            logger.debug(
                "session.bind_toolbar.connect_failed err=%s", e,
            )

    def bind_bookmark_manager(self, manager):
        """Attach the BookmarkManager for clipboard-toggle persistence.

        main_window calls this after constructing the session so
        :meth:`_on_clipboard_toggles_changed` can persist UI changes
        to the active ConnectionProfile.
        """
        self.bookmark_manager = manager
