# Codebase Structure

**Analysis Date:** 2026-04-18

## Directory Layout

```
teraguchi/
├── README.md                       # User-facing docs + architecture diagram
├── pyproject.toml                  # Package metadata + console_scripts
├── requirements-server.txt         # Linux server deps
├── requirements-client.txt         # Client deps (all platforms)
├── requirements-broker.txt         # Broker deps (Linux)
├── requirements-dev.txt            # Dev/test deps
├── teraguchi                       # Bash shim: activates .venv, runs client
├── build_client.py                 # PyInstaller wrapper for standalone client
├── install-server.sh               # Linux server installer (systemd + uinput)
├── install-server-macos.sh         # macOS server installer (LaunchAgent)
├── install-client.sh               # Cross-platform client installer
├── install-client.ps1              # Windows PowerShell client installer
│
├── client/                         # PySide6 GUI — runs on Mac/Win/Linux
│   ├── __init__.py
│   ├── main.py                     # QApplication, MainWindow, tab UI, entry point
│   ├── session.py                  # Session per-tab (protocol + viewer + decoder)
│   ├── protocol.py                 # ClientProtocol — WebSocket + UDP + jitter buffer
│   ├── viewer.py                   # RemoteViewer QWidget — paint + input capture
│   ├── video_decoder.py            # PyAV H.264/H.265/AV1 decoder manager
│   ├── audio_player.py             # QAudioSink-based audio playback
│   ├── bookmarks.py                # Bookmark storage (encrypted credentials)
│   ├── quality_control.py          # Quality slider + settings panel
│   ├── health_display.py           # Health overlay + status-bar widget
│   ├── fullscreen_toolbar.py       # Auto-hiding toolbar for fullscreen mode
│   ├── monitor_selector.py         # Multi-monitor checkbox picker
│   ├── file_transfer.py            # FileSender — 256 KB chunked sender w/ SHA-256
│   ├── usb_forward.py              # USBForwardClient — device enum + attach
│   ├── key_diagnostic.py           # F10 key-event debug dialog
│   ├── icons.py                    # Inline SVG icons (Lucide-style)
│   └── theme.py                    # Dark stylesheet + color constants
│
├── server/                         # Server — runs on the remoted machine
│   ├── __init__.py
│   ├── main.py                     # Entry point: SessionRuntime, ClientSession, handle_client
│   ├── platform_backends.py        # OS dispatch for capture/input/clipboard
│   ├── session_manager.py          # XSessionManager — per-user Xvfb/Xorg spawn (Linux)
│   ├── auth.py                     # Authenticator (local JSON + PAM dispatcher)
│   ├── pam_auth.py                 # PAMAuthenticator (python-pam wrapper)
│   ├── health.py                   # HealthMonitor (RTT, FPS, bandwidth stats)
│   │
│   ├── # Linux backends
│   ├── screen_capture.py           # mss + XDamage + NvFBC dispatcher
│   ├── input_injector.py           # uinput mouse/kbd/pen virtual devices
│   ├── xtest_injector.py           # XTest injection for Xvfb virtual displays
│   ├── clipboard.py                # xclip/xsel wrapper
│   ├── cursor_tracker.py           # XFixes cursor-shape poller
│   ├── audio_capture.py            # PulseAudio/PipeWire monitor-source capture
│   ├── usb_passthrough.py          # USBForwardingManager via usbip/vhci-hcd
│   │
│   ├── # macOS backends
│   ├── mac_screen_capture.py       # MacScreenCapture — ScreenCaptureKit (SCK)
│   ├── mac_input_injector.py       # MacInputInjector — CoreGraphics CGEventPost
│   ├── mac_clipboard.py            # MacClipboardSync — NSPasteboard polling
│   │
│   ├── # Cross-platform
│   ├── video_encoder.py            # VideoEncoder + JpegFallbackEncoder — FFmpeg subprocess
│   ├── file_transfer.py            # FileReceiver — checksummed chunk reassembly
│   │
│   ├── # Ops artifacts
│   ├── teraguchi-server.service    # systemd unit
│   ├── xorg-teraguchi.conf         # Xorg config for headless NVIDIA display
│   ├── setup_uinput.sh             # uinput permissions bootstrap
│   │
│   └── nvfbc/                      # NVIDIA NvFBC zero-copy capture helper
│       ├── nvfbc_capture.c         # C helper: dlopens libnvidia-fbc, streams BGRA
│       ├── NvFBC.h                 # Vendored NVIDIA header (MIT)
│       ├── Makefile                # Builds nvfbc_capture binary
│       ├── nvfbc_backend.py        # Python subprocess wrapper
│       └── __init__.py
│
├── broker/                         # Connection broker — auth + routing (Linux)
│   ├── __init__.py
│   ├── main.py                     # Entry point: handle_client, run_broker
│   ├── pool.py                     # Machine + MachinePool + health probe loop
│   ├── freeipa.py                  # FreeIPAClient — LDAP/GSSAPI group lookup
│   ├── tokens.py                   # HMAC-SHA256 signed auth token gen/verify
│   ├── admin.py                    # AdminServer — HTTP admin API + LDAP auth
│   ├── broker.yml.example          # Example machine/assignments config
│   ├── teraguchi-broker.service    # systemd unit
│   └── static/
│       └── index.html              # Single-page admin UI
│
├── common/                         # Shared protocol + transport library
│   ├── __init__.py
│   ├── messages.py                 # MsgType, FrameType, dataclasses, header codec
│   ├── keymap.py                   # Qt key enum → Linux evdev scancode map
│   ├── hybrid_transport.py         # HybridServerTransport / HybridClientTransport
│   ├── udp_transport.py            # UDPMediaServer / UDPMediaClient + DTLS
│   ├── quic_transport.py           # aioquic-based QUIC transport (experimental)
│   └── jitter_buffer.py            # Adaptive jitter buffer for UDP reception
│
├── Teraguchi.app/                  # macOS app-bundle wrapper (dev mode)
│   └── Contents/
│       ├── Info.plist              # CFBundleIdentifier = com.teraguchi.client
│       └── MacOS/
│           └── Teraguchi           # Bash launcher → .venv + python -m client.main
│
├── tools/
│   └── keydiag.py                  # Standalone key-event diagnostic script
│
└── .planning/codebase/             # GSD codebase analysis docs (this directory)
```

## Directory Purposes

**`client/`:**
- Purpose: Everything the end user runs on their laptop/workstation to view the remote machine.
- Contains: Qt GUI, WebSocket client, PyAV decoder, audio playback, viewer widget, input capture, bookmarks, quality UI, health overlay, file sender, USB forwarder.
- Key files: `client/main.py` (MainWindow + entry point), `client/session.py` (Session per-tab), `client/protocol.py` (ClientProtocol), `client/viewer.py` (RemoteViewer).

**`server/`:**
- Purpose: Everything that runs on the machine being remoted. Captures the screen, encodes video, injects input, syncs clipboard, streams audio.
- Contains: Entry point, session runtime, per-platform backends (Linux vs macOS), video encoder, X session manager, auth, health monitor, file receiver, USB passthrough.
- Key files: `server/main.py` (SessionRuntime, ClientSession, handle_client), `server/platform_backends.py` (OS dispatch), `server/session_manager.py` (Linux per-user X11 sessions).

**`server/nvfbc/`:**
- Purpose: Isolated NVIDIA NvFBC zero-copy capture helper. Compiled C binary spawned as a sandboxed subprocess so GLX state and NVIDIA-driver crashes don't take the server process down.
- Contains: `nvfbc_capture.c` source, vendored `NvFBC.h`, Makefile, Python subprocess wrapper.
- Key files: `server/nvfbc/nvfbc_capture.c` (C source), `server/nvfbc/nvfbc_backend.py` (Python side).
- Note: Binary `nvfbc_capture` is built per-host by `install-server.sh` and is gitignored.

**`broker/`:**
- Purpose: Connection broker. Clients hit the broker first, which PAM-auths, checks FreeIPA group membership, assigns a machine from the pool, and hands back a signed HMAC token that the real server accepts instead of a password.
- Contains: WebSocket handler, machine pool + health probes, FreeIPA client, token gen/verify, embedded HTTP admin UI.
- Key files: `broker/main.py` (entry), `broker/pool.py` (MachinePool), `broker/tokens.py` (HMAC tokens), `broker/admin.py` (admin API).

**`common/`:**
- Purpose: Shared code imported by all three of client/server/broker. Protocol types, transport implementations, Qt→Linux keymap.
- Contains: `messages.py` (MsgType, FrameType, dataclasses, header encode/decode), `keymap.py` (key translation), `hybrid_transport.py` + `udp_transport.py` + `quic_transport.py` (transport layer), `jitter_buffer.py`.
- Key files: `common/messages.py` (the protocol), `common/hybrid_transport.py` (TCP+UDP coordination).
- **How it's shared:** `client/`, `server/`, and `broker/` all sit as sibling top-level packages inside the repo. They each import via `from common.messages import ...` with `sys.path.insert(0, ".")` at the top of each entry point (`client/main.py`, `server/main.py`, `broker/main.py`). `pyproject.toml`'s `[tool.setuptools.packages.find]` includes `"common*"` so the package ships with both installed wheels. `build_client.py` explicitly adds `--add-data common:common` so PyInstaller bundles it. `install-server-macos.sh` rsyncs the whole tree (including `common/`) into `~/Library/Application Support/Teraguchi/`.

**`Teraguchi.app/`:**
- Purpose: macOS `.app` bundle for the client. This is a dev-mode bundle, not a release artifact — its launcher shells out to the repo's `.venv`. Real release bundles come from `build_client.py`.
- Contains: Standard macOS bundle layout: `Contents/Info.plist` (`CFBundleIdentifier=com.teraguchi.client`, `NSHighResolutionCapable=true`), `Contents/MacOS/Teraguchi` (bash launcher).
- Key files: `Teraguchi.app/Contents/MacOS/Teraguchi` (hardcoded path to `/Users/randymcentee/workspace/GitHub/teraguchi`, sources `.venv`, execs `python -m client.main`).
- Note: The hardcoded path means this bundle only works on one machine. Real distributions go through `python build_client.py` → `dist/Teraguchi.app/`.

**`tools/`:**
- Purpose: One-off operator utilities outside the main packages.
- Contains: `keydiag.py` — standalone key-event debugger.

**`.planning/codebase/`:**
- Purpose: GSD (`/gsd-map-codebase`) analysis outputs. Read by `/gsd-plan-phase` and `/gsd-execute-phase`.
- Generated: Yes (by the GSD codebase mapper).
- Committed: Yes.

## Key File Locations

**Entry Points:**
- `server/main.py` — `main()` function, also exposed as `teraguchi-server` console_script in `pyproject.toml`.
- `broker/main.py` — `main()` function.
- `client/main.py` — `main()` function, also exposed as `teraguchi-client` console_script.
- `teraguchi` — bash shim at repo root that activates `.venv` and runs `python -m client.main`.
- `Teraguchi.app/Contents/MacOS/Teraguchi` — bash launcher inside the dev-mode macOS bundle.
- `build_client.py` — PyInstaller wrapper that produces `dist/Teraguchi/` (or `dist/Teraguchi.app/` on macOS).

**Transport layer (cross-component):**
- `common/messages.py` — wire protocol: `MsgType` strings, `FrameType`/`VideoCodec`/`ChromaSubsampling`/`AudioCodec` enums, binary header encoders (`encode_video_header`, `encode_audio_header`, `encode_jpeg_header`), `parse_message()`, and every message dataclass.
- `common/hybrid_transport.py` — TCP+UDP coordination, UDP negotiation state machine (`TransportMsg.UDP_ANNOUNCE`/`UDP_PROBE`/`UDP_CONFIRMED`/`UDP_FALLBACK`), `TransportState`.
- `common/udp_transport.py` — `UDPMediaServer`, `UDPMediaClient`, 16-byte UDP header codec, DTLS.
- `common/quic_transport.py` — experimental QUIC path via `aioquic`, single-connection multiplexed streams + datagrams.
- `common/jitter_buffer.py` — adaptive jitter buffer for UDP media reception on the client.

**Client-side transport:**
- `client/protocol.py` — `ClientProtocol` class. Owns the IO thread, `asyncio` loop, WebSocket, optional UDP/QUIC channels, reconnect logic, broker-redirect handling.
- `client/session.py` — `Session` class. One per tab. Wires `ClientProtocol` callbacks onto Qt signals via `_Bridge` for thread safety.

**Server-side transport:**
- `server/main.py` — `handle_client(websocket)` coroutine is the WebSocket entry. `handle_http(path, headers)` handles the `/status` REST probe on the same port.
- Server reuses `common/hybrid_transport.py` + `common/udp_transport.py` + `common/quic_transport.py` as the outbound side.

**Platform-specific backends (server):**

| Concern | Linux (`server/`) | macOS (`server/`) | Dispatcher |
|--------|-------------------|-------------------|------------|
| Screen capture | `screen_capture.py` (mss + XDamage + NvFBC) | `mac_screen_capture.py` (ScreenCaptureKit) | `platform_backends.ScreenCapture` |
| Input injection (physical) | `input_injector.py` (uinput) | `mac_input_injector.py` (CoreGraphics) | `platform_backends.InputInjector` |
| Input injection (virtual display) | `xtest_injector.py` (XTest on Xvfb) | stubbed to `MacInputInjector` | `platform_backends.XTestInputInjector` |
| Clipboard | `clipboard.py` (xclip/xsel) | `mac_clipboard.py` (NSPasteboard) | `platform_backends.ClipboardSync` |
| Cursor shape | `cursor_tracker.py` (XFixes poll) | baked into video (SCK `showsCursor=True`) | N/A |
| Audio capture | `audio_capture.py` (pactl monitor source) | not yet implemented on macOS | N/A |
| USB passthrough | `usb_passthrough.py` (usbip/vhci-hcd) | `self.usb_manager = None` | N/A |
| Per-user GUI session | `session_manager.py` (Xvfb / Xorg+NVIDIA) | single logged-in user only | N/A |
| Zero-copy GPU capture | `nvfbc/` (NVIDIA NvFBC subprocess) | N/A | N/A |

**Platform-specific backends (client):**
- No hard split. PySide6 is the abstraction. `client/viewer.py` uses `QTabletEvent` for Wacom pressure on both macOS and Windows. `client/usb_forward.py` branches internally: `system_profiler SPUSBDataType` on macOS, `usbipd` on Windows, `lsusb` on Linux.

**Configuration:**
- `pyproject.toml` — package metadata, version, console scripts.
- `requirements-*.txt` — one per deployable (server/client/broker/dev).
- `/etc/teraguchi/broker.yml` (example at `broker/broker.yml.example`) — broker machine pool + user assignments.
- `/etc/teraguchi/broker.secret` — HMAC signing key shared between broker and servers.
- `/etc/teraguchi/tls/{broker,server}.{crt,key}` — TLS certs.
- `server/xorg-teraguchi.conf` — Xorg config for NVIDIA headless display (copied by `install-server.sh`).
- `~/Library/LaunchAgents/com.dxs.teraguchi.server.plist` — macOS server auto-start (written by `install-server-macos.sh`).
- `~/.config/teraguchi/users.json` — local-auth user database (created by `server.main --add-user`).

**Install / deployment scripts:**
- `install-server.sh` — Linux: system packages, Python venv at `/opt/teraguchi/.venv`, uinput permissions, kernel modules (`uinput`, `vhci-hcd`, `usbip-core`), systemd service.
- `install-server-macos.sh` — macOS: Homebrew FFmpeg, Python venv at `~/Library/Application Support/Teraguchi/.venv`, PyObjC frameworks (Cocoa, Quartz, ScreenCaptureKit, AVFoundation, CoreMedia, CoreAudio, ApplicationServices), LaunchAgent.
- `install-client.sh` — Mac/Linux client: Python venv, pip install, optional `--build` for PyInstaller.
- `install-client.ps1` — Windows PowerShell equivalent.

## Naming Conventions

**Files:**
- `snake_case.py` throughout (`screen_capture.py`, `mac_input_injector.py`, `hybrid_transport.py`).
- Platform-specific modules prefix with `mac_` (e.g. `mac_clipboard.py`). No `linux_` prefix — Linux is the default and those files get plain names.
- Kebab-case for shell scripts (`install-server.sh`, `install-client.sh`).
- systemd units are `teraguchi-<component>.service` (e.g. `teraguchi-server.service`, `teraguchi-broker.service`).

**Directories:**
- Single-word, lowercase (`client`, `server`, `broker`, `common`, `tools`).
- The macOS app bundle is the only exception: `Teraguchi.app/` follows Apple's `CamelCase.app` convention.

**Python naming:**
- Classes: `CamelCase` (`SessionRuntime`, `ClientSession`, `MachinePool`, `RemoteViewer`, `HybridServerTransport`).
- Functions / methods: `snake_case` (`capture_raw_bgra`, `_on_encoded_frame`, `handle_input`).
- Constants: `UPPER_SNAKE_CASE` (`UDP_MAGIC`, `VIDEO_HEADER_SIZE`, `CHANNEL_VIDEO`, `ENCODER_DEFS`).
- `MsgType` uses lowercase string values (`"mouse_move"`, `"auth_request"`, `"broker_assign"`) — those strings are the actual JSON `type` field on the wire.

## Where to Add New Code

**New protocol message:**
- Add the `MsgType.XXX` constant in `common/messages.py`.
- Add a `@dataclass` for it in `common/messages.py` if it has structured fields.
- Handle it server-side in `server/main.py` `SessionRuntime.handle_input()` (for client→server) or in one of the `_on_*` broadcast methods (for server→client).
- Handle it client-side in `client/protocol.py` (IO thread) and forward it to the Qt main thread via the `_Bridge` signals in `client/session.py`.

**New client UI feature:**
- Widget code goes in `client/<feature>.py` (new file) or as a method on `MainWindow` in `client/main.py`.
- Theme/colors via `client/theme.py`.
- Icons via `client/icons.py` (inline SVG strings).
- Wire it into `MainWindow.__init__` in `client/main.py` if it's a new panel or toolbar action.

**New server backend (adding a third platform, e.g. Windows):**
- Write new modules: `server/win_screen_capture.py`, `server/win_input_injector.py`, `server/win_clipboard.py`.
- Match the duck-typed contracts documented in `server/platform_backends.py`.
- Add an `IS_WINDOWS = sys.platform == "win32"` branch in `server/platform_backends.py` that re-exports the new classes.
- Add a matching `install-server-windows.ps1` installer.

**New encoder:**
- Add an `HWEncoder(...)` entry to `ENCODER_DEFS` in `server/video_encoder.py` with the correct `codec`, `backend`, `supports_444`, `supports_lossless`, `priority`.
- If it needs special FFmpeg args, extend `VideoEncoder._build_ffmpeg_cmd()`.

**New broker machine or assignment:**
- Edit `/etc/teraguchi/broker.yml` on the broker host. Copy `broker/broker.yml.example` as a starting point. No code change needed; `MachinePool` loads from config on startup and health probes pick it up automatically.

**Utilities:**
- One-off operator scripts go in `tools/` (e.g. `tools/keydiag.py`). Not part of any installed package.

**Tests:**
- No dedicated `tests/` directory yet. Test deps live in `requirements-dev.txt`. When adding, mirror the package structure — `tests/common/`, `tests/server/`, `tests/client/`, `tests/broker/`.

## Special Directories

**`.venv/`:**
- Purpose: Python virtual environment used by the `teraguchi` shim and `Teraguchi.app/Contents/MacOS/Teraguchi` launcher.
- Generated: Yes, by `install-*.sh` or a manual `python -m venv .venv`.
- Committed: No (in `.gitignore`).

**`server/nvfbc/nvfbc_capture`:**
- Purpose: Compiled C binary output of `server/nvfbc/Makefile`.
- Generated: Yes, by `make` inside `server/nvfbc/` (invoked by `install-server.sh` on NVIDIA hosts).
- Committed: No (in `.gitignore`).

**`build/`, `dist/`:**
- Purpose: PyInstaller output. `dist/Teraguchi/` (directory bundle) and on macOS `dist/Teraguchi.app/` (full .app bundle).
- Generated: Yes, by `python build_client.py`.
- Committed: No (in `.gitignore`).

**`Teraguchi.app/`:**
- Purpose: Hand-written dev-mode macOS app bundle that shells out to the repo's `.venv`.
- Generated: No — committed as a convenience for the developer.
- Committed: Yes (though currently untracked in the working copy).
- Note: Different from `dist/Teraguchi.app/`, which is the real PyInstaller output.

**`__pycache__/`, `*.egg-info/`:**
- Purpose: Python import caches and setuptools build metadata.
- Generated: Yes.
- Committed: No (in `.gitignore`).

---

*Structure analysis: 2026-04-18*
