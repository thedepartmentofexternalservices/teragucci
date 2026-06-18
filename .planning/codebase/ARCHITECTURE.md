# Architecture

**Analysis Date:** 2026-04-18

## Pattern Overview

**Overall:** Three-tier client/broker/server remote-desktop system with a shared protocol library (`common/`) and per-platform backends behind a dispatch module on the server side.

Teraguchi is an open-source Teradici PCoIP / HP Anyware replacement for creative workflows. A single code tree produces four deployable entities:

1. **Server** (`server/`) — runs on the host being remoted (Linux or macOS). Captures screen, encodes video, injects input, syncs clipboard/audio, and speaks WebSocket to clients.
2. **Broker** (`broker/`) — optional PAM/LDAP-gated front door (Linux only). Authenticates users, consults FreeIPA for group membership, and redirects each client to a healthy server in a machine pool with a signed HMAC token. The broker does **not** relay media.
3. **Client** (`client/`) — PySide6 GUI (Mac / Windows / Linux). Renders the remote framebuffer, captures input (including full Wacom pen pressure via `QTabletEvent`), and optionally forwards USB devices to the server over USB/IP.
4. **Common library** (`common/`) — protocol types, message dataclasses, Qt→Linux keymap, and the `HybridServerTransport` / `HybridClientTransport` / `UDPMediaServer` / `QUICTransportServer` implementations shared by both ends.

**Key Characteristics:**
- Hybrid transport: TLS WebSocket (control + JSON) paired with an optional UDP media channel on the same port, plus an experimental QUIC path (`common/quic_transport.py`).
- Per-user X11 session isolation on Linux via PAM (each authenticated user gets an Xvfb or real Xorg display with NVIDIA support), single shared session on macOS.
- Platform backends are selected at import time by `server/platform_backends.py`. The session runtime itself is platform-agnostic.
- Sessions persist across client disconnects (PCoIP-style): dropping a client just removes it from the `SessionRuntime.clients` dict; the capture/encode pipeline keeps running.
- Binary video/audio frames use compact fixed-size headers; everything else is JSON over the same WebSocket.

## Layers

**Transport layer (shared):**
- Purpose: Move bytes. Owns TLS WebSocket, UDP media datagrams, QUIC datagrams, and jitter buffering.
- Location: `common/hybrid_transport.py`, `common/udp_transport.py`, `common/quic_transport.py`, `common/jitter_buffer.py`
- Contains: `HybridServerTransport`, `HybridClientTransport`, `UDPMediaServer`, `UDPMediaClient`, `QUICTransportServer`, `QUICTransportClient`, `JitterBuffer`, `BandwidthEstimator`, UDP 16-byte packet header (`encode_udp_header` / `decode_udp_header`).
- Depends on: Python `websockets`, optional `aioquic`, `ssl` for TLS/DTLS.
- Used by: Both `server/main.py` and `client/protocol.py`.

**Protocol layer (shared):**
- Purpose: Define every on-the-wire message + binary frame header.
- Location: `common/messages.py`, `common/keymap.py`
- Contains: `MsgType` string constants, `FrameType`/`VideoCodec`/`ChromaSubsampling`/`AudioCodec` IntEnums, dataclasses (`QualitySettings`, `AuthRequest`, `AuthResponse`, `AuthResult`, `HealthPing`, `HealthPong`, `ClipboardMsg`, `MonitorInfo`, `MonitorListMsg`, `ClientHelloMsg`, `ServerHelloMsg`), helpers (`encode_video_header`, `decode_video_header`, `encode_audio_header`, `encode_jpeg_header`, `hash_password`, `generate_challenge`, `parse_message`), and the `qt_key_to_linux_scancode()` lookup table.
- Depends on: Standard library only (`json`, `struct`, `hashlib`, `secrets`, `dataclasses`).
- Used by: Server, broker, and client.

**Platform dispatch layer (server only):**
- Purpose: Select the right backend class for the current OS without polluting `server/main.py` with `if sys.platform` checks.
- Location: `server/platform_backends.py`
- Contains: `IS_MACOS` / `IS_LINUX` flags; re-exported names `ScreenCapture`, `InputInjector`, `XTestInputInjector`, `ClipboardSync`.
- Depends on: `server/screen_capture.py`, `server/input_injector.py`, `server/xtest_injector.py`, `server/clipboard.py` on Linux; `server/mac_screen_capture.py`, `server/mac_input_injector.py`, `server/mac_clipboard.py` on macOS.
- Used by: `server/main.py` (the session runtime imports from this module, not from the concrete backend modules).

**Server session runtime:**
- Purpose: One `SessionRuntime` per user. Owns a capture pipeline, encoder, input injector, audio capture, clipboard, cursor tracker, file receiver, USB manager, and a dict of connected `ClientSession` WebSockets.
- Location: `server/main.py` (`SessionRuntime`, `ClientSession`), `server/session_manager.py` (`XSessionManager` for per-user Xvfb/Xorg spawn on Linux).
- Contains: Streaming loops (`_stream_h264`, `_stream_jpeg`, `_health_ping_loop`, `_monitor_hotplug_loop`), encoded-frame broadcast (`_on_encoded_frame`), input dispatch (`handle_input`), quality reconfiguration (`apply_quality`, `_restart_encoder`).
- Depends on: Platform backends, `VideoEncoder` (`server/video_encoder.py`), `AudioCapture` (`server/audio_capture.py`), `CursorTracker` (`server/cursor_tracker.py`, Linux only), `FileReceiver` (`server/file_transfer.py`), `USBForwardingManager` (`server/usb_passthrough.py`, Linux only), `HealthMonitor` (`server/health.py`), `Authenticator` / `PAMAuthenticator` (`server/auth.py`, `server/pam_auth.py`).
- Used by: `server/main.py`'s top-level `handle_client` coroutine, invoked by the `websockets.serve` handler.

**Client session:**
- Purpose: One `Session` per tab. Encapsulates the protocol, the Qt viewer widget, video/audio decoders, health overlay, file sender, and USB forwarder.
- Location: `client/session.py` (`Session`, `_Bridge`), `client/main.py` (`MainWindow` with tabbed `QTabWidget`).
- Contains: Signal-wired `_Bridge` that marshals protocol callbacks from the WebSocket IO thread onto the Qt main thread; wiring of `RemoteViewer` input signals to `ClientProtocol` sends.
- Depends on: `ClientProtocol` (`client/protocol.py`), `RemoteViewer` (`client/viewer.py`), `DecoderManager` (`client/video_decoder.py`, PyAV), `AudioPlayer` (`client/audio_player.py`, `QAudioSink`), `HealthData` / `HealthOverlay` (`client/health_display.py`), `FileSender` (`client/file_transfer.py`), `USBForwardClient` (`client/usb_forward.py`).
- Used by: `client/main.py` — one `Session` per tab in the main window.

**Broker:**
- Purpose: Authenticate + authorize + redirect. Never sees media.
- Location: `broker/main.py` (entrypoint), `broker/pool.py` (`Machine`, `MachinePool`), `broker/freeipa.py` (`FreeIPAClient` — LDAP / `id` fallback), `broker/tokens.py` (HMAC-SHA256 signed token generation/verification), `broker/admin.py` (`AdminServer` — HTTP admin UI served on the same port via the `websockets` `process_request` hook), `broker/static/index.html` (admin UI).
- Depends on: `common/messages.py` for the `AuthRequest`/`AuthResult`/`MsgType.BROKER_*` message types, `server/pam_auth.py` for PAM verification, `aiohttp` for pool health probes, `PyYAML` for config.
- Used by: Client (via `ClientProtocol.connect_broker`), then clients disconnect from broker and reconnect to the returned `host:port` with the signed token as their auth credential.

## Data Flow

**Connection flow (direct, PAM mode):**

1. Client dials `wss://server:443` → TLS WebSocket opens.
2. Server sends `AuthRequest` with `auth_methods=["pam", "token"]`, `auth_mode="pam"`.
3. Client sends `AuthResponse` with `method="pam"`, `username`, `credential=password`.
4. Server calls `PAMAuthenticator.authenticate()` (must run as root). On success, `PAMAuthenticator.get_user_info()` returns `uid/gid/home/groups`.
5. `XSessionManager` spawns or reattaches a per-user Xorg (NVIDIA-accelerated) or Xvfb display on a free `:NN`, launches GNOME/window manager + PulseAudio as that user.
6. `SessionRuntime` for that user instantiates `ScreenCapture`, `XTestInputInjector` (virtual display ≥ `:10`) or `InputInjector` (physical display), `VideoEncoder`, `AudioCapture`, `ClipboardSync`, `CursorTracker`, `FileReceiver`, `USBForwardingManager`.
7. Server sends `ServerHelloMsg` with screen dimensions, supported codecs/chroma, monitor list, audio support flag.
8. Client negotiates UDP media channel via `HybridClientTransport`: `UDP_ANNOUNCE` → `UDP_PROBE` → `UDP_CONFIRMED` → `UDP_ACTIVE`, or falls back to `UDP_FALLBACK` and stays TCP-only.

**Connection flow (broker mode):**

1. Client calls `ClientProtocol.connect_broker(host, port, username, password)` — opens `wss://broker:8443`.
2. Broker sends `AuthRequest` with `auth_methods=["pam"]`.
3. Client replies with username+password. Broker calls `PAMAuthenticator.authenticate()`.
4. Broker queries FreeIPA via `FreeIPAClient.get_user_groups_cached()`. Rejects if user not in `teraguchi-users` (or `teraguchi-admins`).
5. Broker sends `BROKER_HELLO` with the list of machines the user can access.
6. Client either auto-assigns (broker runs `pool.assign(username, groups)`) or the UI prompts the user (`broker_machine_needed` signal) and sends `BROKER_MACHINE_REQUEST`.
7. Broker generates an HMAC-SHA256 signed token (`generate_token`) with 60-second TTL and sends `BROKER_ASSIGN` with `host`, `port`, `machine_name`, `gpu`, `token`.
8. Client closes the broker WS and dials the assigned server, authenticating with `method="token"`, `credential=<broker token>`. The server verifies the token against its shared `broker-secret` file.

**Video path (server → client):**

1. `SessionRuntime._stream_h264` loop (at `QualitySettings.effective_fps()`) calls `self.capture.capture_raw_bgra()`.
2. Raw BGRA is fed to `VideoEncoder.feed_frame()` (FFmpeg subprocess with `h264_nvenc` / `hevc_nvenc` / `av1_nvenc` / VAAPI / AMF / VideoToolbox / software `libx264`/`libx265`/`libsvtav1`).
3. Encoded NAL/OBU bytes surface via the `_on_encoded_frame(data, is_keyframe)` callback.
4. A 10-byte header is prepended by `encode_video_header(frame_type, codec, chroma, flags, timestamp_ms, monitor_id)`.
5. Frame is broadcast to every `ClientSession` in `self.clients`. If `HybridServerTransport.using_udp(client_id)` the frame goes over UDP (fragmented to 1400-byte MTU); otherwise it's pushed into the TCP WebSocket.
6. On the client, `ClientProtocol` routes binary frames by frame type. Video frames are dispatched to `DecoderManager.decode()` (PyAV with hardware decode via CUDA / VideoToolbox / VAAPI where available).
7. Decoded `QImage` is handed to `RemoteViewer._screen_image`, which repaints via `QWidget.update()`.

**Input path (client → server):**

1. `RemoteViewer` overrides `mousePressEvent`, `mouseMoveEvent`, `wheelEvent`, `keyPressEvent`, `tabletEvent`, etc. and emits Qt signals with normalized coordinates.
2. `Session._wire_viewer()` connects those signals to `ClientProtocol.send_mouse_move()` / `send_key_event()` / `send_pen_event()` methods.
3. Protocol serializes to JSON and sends over the TCP WebSocket (never UDP — control must be reliable).
4. Server's `ClientSession` receive loop calls `SessionRuntime.handle_input()`.
5. For `KEY_EVENT`: if injector is `XTestInputInjector`, Qt scancodes are used as-is; otherwise `qt_key_to_linux_scancode()` translates to Linux evdev codes, then `InputInjector` writes to `/dev/uinput`.
6. On macOS, `MacInputInjector` translates via `_LINUX_TO_MAC_KEYCODE` and posts `CGEventPost` events (TCC Accessibility permission required).

**Clipboard path (bidirectional):**

- Client → server: `CLIPBOARD_SEND` JSON message → `ClipboardSync.set_clipboard(text)`. On Linux that's `xclip -selection clipboard`; on macOS it's `NSPasteboard.generalPasteboard().setString_forType_()`.
- Server → client: `ClipboardSync.start_monitoring(on_change)` polls X11 `PRIMARY`/`CLIPBOARD` (via `xclip`) or `NSPasteboard.changeCount`. Changes emit `CLIPBOARD_RECV` broadcasts to all authenticated clients.

**Cursor path (Linux only):**

- `CursorTracker` polls XFixes at 30 Hz. When the cursor shape changes (e.g. Flame swapping arrow → crosshair), it emits a `CURSOR_UPDATE` message with the encoded cursor pixels + hotspot.
- Client receives the shape and creates a native `QCursor` via `RemoteViewer.setCursor()`. Cursor motion is entirely client-side — it never round-trips through the encoder.
- Late-joining clients get the latest cached cursor shape pushed immediately via `SessionRuntime.add_client()`.
- macOS: `MacScreenCapture` sets `showsCursor=True` on the `SCStreamConfiguration` so the cursor is baked into the video frame VNC-style.

**File transfer path:**

- Client drags files onto `RemoteViewer` or picks via File menu. `FileSender` opens the file, emits `FILE_OFFER` (size, name, SHA-256), waits for `FILE_ACCEPT`, then streams 256 KB `FILE_CHUNK` messages, ending with `FILE_DONE`.
- Server's `FileReceiver` validates the checksum and drops the file in the user's `~/Desktop` (respecting `uid`/`gid`).

**USB passthrough (Linux server / any client):**

- Client enumerates local devices (`system_profiler SPUSBDataType` on macOS, `usbipd` on Windows, `lsusb` on Linux) and sends `USB_DEVICE_LIST`.
- On attach, the client-side `usbip` binds the device and exports it; server-side `USBForwardingManager` imports via `vhci-hcd`/`usbip-core` so it shows up as native USB hardware.
- macOS server stubs out USB passthrough (`self.usb_manager = None if IS_MACOS else USBForwardingManager()` in `server/main.py`).

## Key Abstractions

**`SessionRuntime` (`server/main.py`):**
- Represents one remote-desktop session. Holds the full capture/encode/inject/audio/clipboard/cursor/file/USB stack for a single X display (Linux) or the host session (macOS).
- Keyed by username in the process-global `runtimes` dict; `default_runtime` covers `auth-mode none` and `auth-mode local` legacy single-display modes.

**`ClientSession` (`server/main.py`):**
- Represents one connected WebSocket. Tracks `authenticated`, `supports_h264`/`supports_h265`/`supports_yuv444`/`supports_audio`, `client_screen_width`/`height`, and an `asyncio.Queue` for outbound frames. Many `ClientSession`s may attach to the same `SessionRuntime`.

**`Session` (`client/session.py`):**
- Represents one tab / one remote connection on the client. Owns protocol + viewer + decoder + audio + health + file sender + USB client.

**Backend protocols (informal — no ABC, duck-typed):**
- **Screen capture**: `ScreenCapture(monitor_index, jpeg_quality)` must expose `width`, `height`, `capture_raw_bgra()`, `capture_dirty_regions()`, `list_monitors()`, `detect_hotplug()`, `switch_monitor(id)`, `invalidate()`, `reinit(w, h)`.
- **Input injector**: `InputInjector(screen_width, screen_height, pen_tablet=None)` must expose `handle_message(msg)` and optionally `reset_modifiers()`.
- **Clipboard**: `ClipboardSync(display)` must expose `available`, `get_clipboard()`, `set_clipboard(text)`, `start_monitoring(callback)`, `stop()`.
- Contracts are documented in `server/platform_backends.py`; concrete classes live in `server/{screen_capture,input_injector,xtest_injector,clipboard}.py` (Linux) and `server/mac_{screen_capture,input_injector,clipboard}.py` (macOS).

**`HWEncoder` / `VideoEncoder` (`server/video_encoder.py`):**
- `ENCODER_DEFS` enumerates every FFmpeg encoder (NVENC, VideoToolbox, VAAPI, AMF, software) with `supports_444` / `supports_lossless` / `priority`. `detect_encoders()` runs `ffmpeg -encoders` to filter to what's actually installed. `VideoEncoder` picks the highest-priority available encoder per codec and manages an FFmpeg subprocess pipe.

**`Machine` / `MachinePool` (`broker/pool.py`):**
- Dataclass-based model of the broker's view of the fleet. Health is polled via async `aiohttp` against each server's `/status` HTTP endpoint (served on the same WebSocket port via `server/main.py`'s `handle_http` process_request hook).

## Entry Points

**Server CLI:**
- Location: `server/main.py` (function `main()`).
- Triggered by: `python -m server.main`, `teraguchi-server` script from `pyproject.toml`, systemd unit `server/teraguchi-server.service`, or macOS LaunchAgent at `~/Library/LaunchAgents/com.dxs.teraguchi.server.plist`.
- Responsibilities: Parse CLI flags, enforce PAM+root requirement on Linux, force `auth-mode none` on macOS, detect FFmpeg encoders, build `Authenticator` + `XSessionManager` + `default_runtime`, call `run_server()` which starts `websockets.serve(handle_client, host, port, ssl=tls_context, process_request=handle_http)`.

**Broker CLI:**
- Location: `broker/main.py` (function `main()`).
- Triggered by: `python -m broker.main` or systemd unit `broker/teraguchi-broker.service`.
- Responsibilities: Load `/etc/teraguchi/broker.yml` (machines + assignments), load `/etc/teraguchi/broker.secret` (HMAC signing key), build `MachinePool`, `FreeIPAClient`, and `AdminServer`, then `websockets.serve(handle_client, ..., process_request=admin_process_request)` on port 8443.

**Client CLI / GUI:**
- Location: `client/main.py` (`main()` → creates `QApplication` + `MainWindow`).
- Triggered by:
  - `python -m client.main` (also `teraguchi-client` script),
  - the `./teraguchi` bash shim at repo root that activates `.venv` and runs `python -m client.main`,
  - the `Teraguchi.app/Contents/MacOS/Teraguchi` bash launcher inside the macOS app bundle (dev-mode bundle — points at the repo's `.venv`),
  - a PyInstaller bundle produced by `build_client.py` (`dist/Teraguchi/` or `dist/Teraguchi.app/`).
- Responsibilities: Show `MainWindow` with tabbed sessions, bookmark panel, quality panel, fullscreen toolbar, USB devices panel, health overlay.

**Server status HTTP endpoint:**
- Location: `handle_http` in `server/main.py` (around line 880), attached via `websockets.serve(..., process_request=handle_http)`.
- Triggered by: HTTP GET `/status` to the server's WebSocket port.
- Responsibilities: Return JSON `{active_sessions, load_avg, uptime_s, gpu, hostname, version}`. Consumed by the broker's `MachinePool._probe_machine()` health loop.

**Broker admin UI:**
- Location: `AdminServer` in `broker/admin.py`; `broker/static/index.html`.
- Triggered by: HTTP GET `/`, `/api/*`, `/static/*` to broker port 8443.
- Responsibilities: LDAP-basic-auth gated REST API for editing machine assignments, serving the single-page admin UI, returning pool status.

**NvFBC capture helper (Linux NVIDIA only):**
- Location: `server/nvfbc/nvfbc_capture.c` (compiled C binary), `server/nvfbc/nvfbc_backend.py` (Python wrapper).
- Triggered by: `ScreenCapture.__init__()` spawns `./nvfbc_capture` as a child process if `libnvidia-fbc.so.1` is present.
- Responsibilities: `dlopen` NVIDIA's NvFBC, grab framebuffer tear-free, stream `[magic=TGFR][width][height][size][BGRA payload]` frames on stdout. Isolated in its own process so GLX state and NVIDIA-driver crashes can't take the server with them.

## Error Handling

**Strategy:** Let exceptions bubble up inside per-client / per-session async tasks. Log with `logger.warning` / `logger.error`. Never let one client's failure bring down the server — a misbehaving `ClientSession` is dropped from the `runtime.clients` dict and streaming continues for the rest.

**Patterns:**
- Auth failures: server sends `AuthResult(success=False, message=...)` and closes the socket.
- Capture / encode errors: caught in `_stream_h264` / `_stream_jpeg` loops with `logger.error("[%s] H264 capture error: %s", username, e)` and the loop keeps running.
- Platform backend missing: `server/platform_backends.py` falls back to Linux classes for static analysis on unsupported platforms; runtime calls will raise.
- UDP negotiation timeout: falls back to TCP-only via `TransportMode.TCP_ONLY`, logged as `fallback_reason` on the `TransportState`.
- Frame broadcast drops: tracked via `HealthMonitor.record_frame_dropped()` and surfaced in the client health overlay.
- NvFBC helper crash: `NvFBCBackend` reader thread detects EOF and screen_capture drops to `mss`/XShm fallback.
- macOS TCC denial: `MacScreenCapture` raises `MacScreenCaptureError` with instructions pointing at System Settings.

## Cross-Cutting Concerns

**Logging:** Standard `logging` module. Named loggers: `teraguchi.server`, `teraguchi.broker`, plus per-module `logging.getLogger(__name__)`. Format `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"`. Verbose mode via `--verbose` / `-v`. systemd journals on Linux; `~/Library/Logs/Teraguchi/server.{log,err.log}` on macOS.

**Validation:** Dataclasses in `common/messages.py` with typed fields. `parse_message(raw)` returns a `dict` — no schema validator. Missing keys default via `msg.get("field", default)` at every call site.

**Authentication:** Three modes selected by `--auth-mode` (`pam` / `local` / `none`). PAM mode requires root and uses `python-pam` via `server/pam_auth.py`. Local mode is a JSON user db with SHA-256 challenge-response in `server/auth.py`. Broker tokens are verified via `broker/tokens.py`'s `verify_token()`. macOS forces `auth-mode none` because there is no PAM/Xvfb equivalent for per-user GUI sessions.

**TLS:** `wss://` recommended for PAM mode because credentials go in the clear inside the WebSocket otherwise. Both server and broker accept `--tls-cert` + `--tls-key`. QUIC path always negotiates TLS 1.3 via `aioquic`. UDP media channel is encrypted via DTLS (see `common/udp_transport.py`).

**Threading model:** Server uses asyncio for WebSocket I/O; capture/encode run on a mix of the FFmpeg subprocess (via stdin pipe) and blocking threads (`ScreenCapture`, `CursorTracker`, `VideoEncoder.feed_frame`). Cross-thread communication back onto the asyncio loop uses `asyncio.run_coroutine_threadsafe(..., self._event_loop)` (see `SessionRuntime._on_encoded_frame`, `_on_clipboard_change`, `_on_cursor_shape_change`). Client uses a dedicated IO thread inside `ClientProtocol` and a Qt signal `_Bridge` (`client/session.py`) to marshal callbacks onto the GUI thread.

**Session lifecycle:** `SessionRuntime` instances persist once created; `add_client` / `remove_client` only adjust the WebSocket set. On last client disconnect the runtime keeps streaming (capture continues, just nothing is sent) so reconnects resume instantly. Full teardown only happens on server shutdown or explicit PAM logout.

---

*Architecture analysis: 2026-04-18*
