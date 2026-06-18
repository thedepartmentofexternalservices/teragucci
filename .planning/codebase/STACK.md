# Technology Stack

**Analysis Date:** 2026-04-18

## Languages

**Primary:**
- Python 3.10+ — All three components (server, client, broker) plus shared `common/` protocol library. Declared in `pyproject.toml` (`requires-python = ">=3.10"`).

**Secondary:**
- C — NvFBC capture helper (`server/nvfbc/nvfbc_capture.c`, ~15 KB). Compiled per-host via `server/nvfbc/Makefile` during `install-server.sh`. Spawned as a subprocess by `server/nvfbc/nvfbc_backend.py` so NVIDIA GLX state stays out of the Python process.
- Bash — Installers: `install-server.sh` (Linux), `install-server-macos.sh`, `install-client.sh`, `server/setup_uinput.sh`, top-level launcher `teraguchi`.
- PowerShell — Windows client installer `install-client.ps1`.
- HTML/JavaScript/CSS — Broker admin UI (`broker/static/index.html`, ~17 KB self-contained single-file app served by `broker/admin.py`).

## Runtime

**Environment:**
- CPython 3.10, 3.11, 3.12, or 3.13 (`install-server-macos.sh` probes for each Homebrew Python in order).
- asyncio event loop drives every long-running component (`server/main.py`, `broker/main.py`, `client/protocol.py`, all transports in `common/`).
- Threading is used alongside asyncio for capture + encode pipelines (`server/screen_capture.py`, `server/video_encoder.py`, `server/mac_screen_capture.py` frame buffer under `threading.Lock`).

**Package Manager:**
- `pip` with per-role requirements files (`requirements-server.txt`, `requirements-client.txt`, `requirements-broker.txt`, `requirements-dev.txt`).
- No lockfile. Version pins are minimum-only (`websockets>=12.0`, `PySide6>=6.6`, etc.).
- Virtual environments created by installers at:
  - Linux server: `/opt/teraguchi/.venv`
  - macOS server: `~/Library/Application Support/Teraguchi/.venv`
  - Client (all OS): `<repo>/.venv`

## Frameworks

**Core:**
- `websockets>=12.0` — TCP control channel for all three components (`server/main.py`, `broker/main.py`, `client/protocol.py`). Server uses the `process_request` hook to serve the broker admin HTTP UI on the same port as the WebSocket listener (`broker/admin.py`).
- `PySide6>=6.6` — Qt6 GUI for the client (`client/main.py`, `client/viewer.py`). Uses `QtWidgets`, `QtGui`, `QtCore`, and `QtMultimedia`.
- `aiohttp>=3.9` — Broker health probing of servers (`broker/pool.py` HTTPS GET to each machine's `/status` endpoint every 15 s).
- `asyncio` — Core concurrency model for all network code.

**Testing:**
- `pytest>=7.0` — Declared in `requirements-dev.txt`. No committed test suite detected in the repo.

**Build/Dev:**
- `pyinstaller>=6.0` — Standalone client bundle. Driven by `build_client.py`; produces `dist/Teraguchi/` (and `dist/Teraguchi.app` on macOS, bundle ID `com.teraguchi.client`).
- `setuptools>=68.0` + `wheel` — PEP 517 build backend declared in `pyproject.toml`.

## Key Dependencies

**Server (Linux) — `requirements-server.txt`:**
- `mss>=9.0` — Fallback screen capture (`server/screen_capture.py`). Used when NvFBC is unavailable.
- `numpy>=1.24` — Raw pixel buffer handling in the encoder pipeline.
- `Pillow>=10.0` — JPEG dirty-rect fallback encoder.
- `python-pam>=2.0` — PAM authentication (`server/pam_auth.py`, `broker/main.py`). Server must run as root for PAM.
- `python-xlib>=0.33` — XTest input injection for Xvfb/Xorg sessions (`server/xtest_injector.py`) and XDamage/XFixes hooks (`server/screen_capture.py`, `server/cursor_tracker.py`).
- `aioquic>=1.0` — Optional QUIC transport (`common/quic_transport.py`).
- `cryptography>=41.0` — Backs `aioquic` TLS 1.3.

**Server (macOS) — installed by `install-server-macos.sh`:**
- `pyobjc-core`
- `pyobjc-framework-Cocoa` — NSPasteboard (`server/mac_clipboard.py`)
- `pyobjc-framework-Quartz` — CoreGraphics event posting (`server/mac_input_injector.py`)
- `pyobjc-framework-ScreenCaptureKit` — SCK frame capture (`server/mac_screen_capture.py`)
- `pyobjc-framework-AVFoundation` — audio capture on macOS
- `pyobjc-framework-CoreMedia` — `CMSampleBuffer` handling
- `pyobjc-framework-CoreAudio`
- `pyobjc-framework-ApplicationServices` — TCC permission probing
- `python-xlib` / `python-pam` / `uinput` are explicitly skipped on macOS.

**Client — `requirements-client.txt`:**
- `av>=12.0` (PyAV) — H.264/H.265/AV1 decode (`client/video_decoder.py`). Hardware-accel order: CUDA, VAAPI, DXVA2/D3D11VA, VideoToolbox, software.
- `numpy>=1.24` — Required by `av.VideoFrame.to_ndarray()`; without it the viewer renders black. Commented explicitly in `requirements-client.txt`.
- `aioquic>=1.0` — Optional QUIC transport.
- `PySide6.QtMultimedia` — Audio playback via `QAudioSink` (`client/audio_player.py`).

**Broker — `requirements-broker.txt`:**
- `PyYAML>=6.0` — `broker/broker.yml` + assignments file parsing (`broker/main.py`, `broker/admin.py`).
- `python-pam>=2.0` — PAM authentication of broker clients (`broker/main.py` imports `server.pam_auth`).
- `python-ldap>=3.4` — **Commented out** in `requirements-broker.txt` and loaded conditionally in `broker/freeipa.py` (falls back to `id -Gn` on FreeIPA-joined hosts if missing).

**Infrastructure / System:**
- FFmpeg — Video encode (server) + decode (PyAV on client). Required libraries: `libx264`, `libx265`, `libsvtav1`, `libopus`, plus GPU encoders `h264_nvenc` / `hevc_nvenc` / `av1_nvenc` / `h264_vaapi` / `h264_amf` / `h264_videotoolbox`. Detected at runtime by `server/video_encoder.py::detect_encoders()`.
- PulseAudio / PipeWire (`pactl`) — System audio monitor source on Linux (`server/audio_capture.py`).
- xclip / xsel — Linux clipboard sync (`server/clipboard.py`).
- Linux kernel modules: `uinput` (input injection), `vhci-hcd` + `usbip-core` (USB/IP passthrough).
- Linux USB/IP userspace tools — `linux-tools-common` on Debian, `usbip` on Fedora.
- Homebrew (`/opt/homebrew/bin`) — Expected on macOS for Python + FFmpeg discovery in `install-server-macos.sh`.

## Configuration

**Environment:**
- `DISPLAY` — Set per-session by `server/session_manager.py` when spawning Xvfb/Xorg; propagated into capture/encoder subprocesses. `server/main.py` temporarily overrides `DISPLAY` around `SessionRuntime.__init__`.
- `PULSE_RUNTIME_PATH`, `XDG_RUNTIME_DIR`, `HOME`, `USER` — Rebuilt per-user by `server/audio_capture.py::_make_pulse_env()` so dropped-privileges audio capture reaches the user's PulseAudio socket.
- No `.env` files detected. `.gitignore` does include `.env`.

**Server config files (Linux):**
- `/etc/teraguchi/broker.yml` — Machine pool + assignments (example: `broker/broker.yml.example`).
- `/etc/teraguchi/broker.secret` — HMAC-SHA256 signing secret for broker-issued tokens (auto-generated ephemeral secret if missing).
- `/etc/teraguchi/tls/broker.crt` + `broker.key` — TLS materials referenced from `broker/teraguchi-broker.service`.
- `/var/log/teraguchi/assignments.yml` — Writable assignment state (separated from read-only config).
- `/etc/udev/rules.d/99-teraguchi-uinput.rules` — Generated by `install-server.sh` so the `uinput` group gets `0660` access to `/dev/uinput`.
- `/etc/modules-load.d/teraguchi.conf` — Persists `uinput`, `usbip-core`, `vhci-hcd` across reboots.
- `server/xorg-teraguchi.conf` — Headless Xorg config with NVIDIA `ConnectedMonitor "DFP-0"` + `CustomEDID` (matches PCoIP / NICE DCV Flame setup).

**Client config (per OS):**
- macOS: `~/Library/Application Support/Teraguchi/` — bookmarks, encrypted credentials (`client/bookmarks.py::_get_config_dir()`).
- Windows: `%APPDATA%/Teraguchi/`
- Linux: `~/.config/teraguchi/`
- Bookmark credentials are XOR-encrypted with a machine-derived key (hostname + CPU arch + home path + `/etc/machine-id` when available) — see `client/bookmarks.py::_get_machine_key()`. This is obfuscation, not cryptographic protection.

**Auth config (server local mode):**
- `~/.config/teraguchi/users.json` (`server/auth.py` `DEFAULT_USERS_FILE`).

**Build:**
- `pyproject.toml` — Package metadata, optional-dep groups (`server`, `client`), entry points `teraguchi-server`, `teraguchi-client`.
- `build_client.py` — PyInstaller driver (`--windowed --onedir --collect-all PySide6`, macOS bundle identifier `com.teraguchi.client`).
- `server/nvfbc/Makefile` — Builds the `nvfbc_capture` helper against `libnvidia-fbc.so.1`.

## Platform Requirements

**Development:**
- Python 3.10+ installed via Homebrew on macOS or distro package manager on Linux.
- FFmpeg with `libx264`, `libx265`, `libsvtav1`, `libopus` for server development; PyAV-linked FFmpeg for client.
- GCC / `cc` for compiling the NvFBC helper on NVIDIA hosts.
- Write access to `/dev/uinput` (Linux server) via the auto-created `uinput` group.

**Production — Linux Server:**
- Rocky / RHEL 9, Fedora, Ubuntu / Debian, Arch, openSUSE (handled by `install-server.sh`).
- X11 (Xorg with NVIDIA driver for GPU sessions, Xvfb for CPU/virtual displays).
- PAM stack configured for the desired auth source (local, LDAP, or FreeIPA via SSSD).
- `/dev/uinput` reachable, `uinput` / `usbip-core` / `vhci-hcd` kernel modules loaded (persisted via `/etc/modules-load.d/teraguchi.conf`).
- Deployed under `/opt/teraguchi` with `teraguchi-server.service` (systemd unit in `server/teraguchi-server.service`). Default command line opens ports 443 (TCP WebSocket) and 444 (QUIC/UDP).
- Optional NVIDIA `libnvidia-fbc.so.1` for zero-copy capture (auto-detected; falls back to mss).

**Production — macOS Server:**
- macOS 12.3+ (ScreenCaptureKit requirement), Apple Silicon or Intel.
- Runs as a **LaunchAgent** (`com.dxs.teraguchi.server`) in the logged-in user's GUI session (NOT a LaunchDaemon — SCK / CoreGraphics need the GUI session context).
- Installed to `~/Library/Application Support/Teraguchi/`, logs in `~/Library/Logs/Teraguchi/server.{log,err.log}`.
- TCC permissions required before first run: **Screen & System Audio Recording**, **Accessibility**, **Input Monitoring**.
- No PAM, no per-user sessions — always runs in `--auth-mode none` or `local`; `server/main.py` force-downgrades PAM to `none` on Darwin (`if IS_MACOS and args.auth_mode == "pam"`).

**Production — Broker:**
- Rocky 9 / RHEL on `dxs-ansible` or similar control-plane host.
- FreeIPA-joined host OR `python-ldap` available to bind to `ldap://dxs-salt.the-dxs.com` / `ldap://dxs-salty.the-dxs.com`.
- systemd unit `broker/teraguchi-broker.service` with `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`, `ReadWritePaths=/var/log/teraguchi`, `PrivateTmp`.

**Production — Client:**
- macOS 10.14+ (Darwin), Windows 10+, or any X11 Linux with PySide6 support.
- Python 3.10+ OR the standalone PyInstaller bundle (`dist/Teraguchi/`, `dist/Teraguchi.app`, or `.exe`).
- GPU-accelerated decode is best-effort: CUDA / VAAPI / DXVA2 / VideoToolbox → software.
- Wacom tablet required for pen pressure; mouse works without one.

---

*Stack analysis: 2026-04-18*
