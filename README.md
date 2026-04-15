# Teraguchi

Open-source remote desktop built for creative professionals. GPU-accelerated H.264/H.265/AV1 video with YUV 4:4:4 chroma, full Wacom pen pressure, USB device passthrough, file transfer, clipboard sync, and per-user session isolation. Designed to replace Teradici PCoIP, HP Anywhere, Parsec, and HP RGS on Linux workstations.

## Why Teraguchi

Commercial remote desktop tools cost thousands per seat, lock you into proprietary protocols, and treat creative workflows as an afterthought. Teraguchi was built for VFX and post-production environments where color accuracy, pen pressure, and low-latency input are non-negotiable.

- **YUV 4:4:4** chroma means no color subsampling — text stays sharp, color pickers stay accurate
- **Full Wacom support** — 8192 pressure levels, tilt, rotation, express keys via USB passthrough
- **GPU encoding** — NVENC, VAAPI, AMF hardware acceleration with software fallback
- **Per-user sessions** — PAM authentication spawns isolated X sessions (like PCoIP)
- **Runs on commodity hardware** — no Teradici cards, no dongles, no license servers

## Features

### Video
- H.264, H.265, AV1 encoding with YUV 4:2:0, 4:2:2, or 4:4:4 chroma
- GPU-accelerated encoding via NVENC (NVIDIA), VAAPI (Intel/AMD), AMF (AMD)
- **NvFBC zero-copy capture** on NVIDIA GPUs — tear-free, sub-millisecond framebuffer grab running as a sandboxed helper process (`nvfbc_capture`)
- Lossless mode for pixel-perfect accuracy
- JPEG dirty-rectangle fallback for environments without FFmpeg
- Adaptive quality slider: sharpness vs temporal stability

### Cursor
- **Local cursor rendering** — the server polls the X cursor shape via XFixes and ships only *shape* updates to the client, which then draws the cursor locally at native refresh rate. Cursor motion never round-trips through the video encoder, so pointer lag is effectively zero regardless of network quality. This is how PCoIP, RDP, VNC, and Parsec all handle cursors.
- Tracks Flame's dynamic cursor swaps (arrow → crosshair → move → text-insert → wait) and applies them as native `QCursor` shapes with correct hotspots
- Late-joining clients receive the current cursor shape immediately on connect — no stale placeholder

### Input
- Full mouse and keyboard with Qt-to-Linux keycode mapping
- macOS key remapping — Command and Control both map to Ctrl on Linux
- macOS Ctrl+click is caught before the OS rewrites it to RightButton, so Flame hotkeys like Ctrl+Shift+click work as expected
- Wacom pen/stylus: 8192 pressure levels, tilt X/Y, rotation, barrel button, eraser, hover
- Virtual uinput tablet device (recognized by Flame, GIMP, Krita, Nuke, etc.)
- XTest injection for virtual displays, uinput for physical

### USB Passthrough
- Forward local USB devices to the remote server via Linux USB/IP
- Wacom tablets, keyboards, and other HID devices appear as native hardware
- Device enumeration on macOS (system_profiler), Windows (usbipd), Linux (lsusb)
- Attach/detach from the USB Devices panel in the client toolbar
- Server-side kernel modules: `vhci-hcd`, `usbip-core`

### File Transfer
- Drag-and-drop files onto the viewer to send to the remote desktop
- File > Send File picker (Ctrl+Shift+S)
- Chunked transfer over WebSocket with SHA-256 checksum verification
- Files land in `~/Desktop` on the remote machine
- 2 GB max file size, 256 KB chunks

### Clipboard
- Bidirectional text clipboard sync via xclip
- Explicit clipboard push on Ctrl+V / Cmd+V for reliable paste
- Server polls X11 clipboard for server-to-client changes

### Audio
- System audio capture via PulseAudio/PipeWire monitor source
- PCM streaming with configurable bitrate (32-320 kbps)
- Toggle on/off from quality settings

### Microphone
- Client microphone streamed to the server in real time (client → server)
- Captured via `QAudioSource` — PCM s16le, 48 kHz mono, 20 ms chunks
- Server pipeline: `pacat` → `module-null-sink` (`teraguchi_mic_sink`) → `module-virtual-source` (`teraguchi_mic`)
- **`teraguchi_mic` appears as a real input device** in GNOME Sound Settings, Zoom, OBS, etc. — not as a monitor source
- Starts automatically on connect, mute/unmute from quality panel or toolbar
- Tested on PipeWire 0.3.48 (Ubuntu 22.04)

### Sessions
- **PAM mode** — per-user Xvfb/Xorg sessions with GNOME, isolated displays
- **Legacy mode** — single shared display (existing X session)
- NVIDIA GPU sessions use real Xorg with NVIDIA driver for full OpenGL/CUDA
- Sessions persist across disconnects for seamless reconnection

### Connection Management
- Multiple concurrent sessions via tabs
- Bookmark system with encrypted credential storage
- Import/Export bookmarks as JSON
- Auto-reconnect with exponential backoff
- Bookmark panel shows a live **connection status dot** per entry (green = connected, gray = idle)
- **Disconnect from bookmark panel** — double-click or right-click an active bookmark to disconnect without navigating tabs
- **Bookmark panel is hidden by default** — press **B** or use View → Bookmarks to toggle; state persists across restarts
- **Branded disconnect overlay** — when a session disconnects or is connecting, the viewer shows a branded "teraguchi" overlay with status and hostname instead of freezing the last video frame
- **Power management** — each bookmark can configure a power backend (SSH, Wake-on-LAN, teraguchi in-band); a power icon appears per row and right-click exposes Power On / Power Off / Reboot (see [Power Management](#power-management))

### Health Monitoring
- Real-time overlay (F9) — RTT, FPS, bandwidth, dropped frames, encode/capture timing
- Status bar with color-coded connection quality indicator
- Server-side health stats pushed to client

### Security
- TLS (wss://) encrypted WebSocket connections
- PAM authentication against system users (FreeIPA, LDAP, local)
- Challenge-response password hashing
- Local auth mode with `--add-user` for standalone deployments

## Architecture

```
┌─────────────────────────┐         WebSocket (wss://)        ┌─────────────────────────┐
│   Client (macOS/Win)    │  ─ JSON control/input ──────────►  │   Server (Linux)        │
│                         │  ─ Binary mic audio (MIC 0x11) ►  │                         │
│  PySide6 GUI            │  ◄─ Binary video (H264/H265/AV1)  │  Screen Capture         │
│  ├─ RemoteViewer        │  ◄─ Binary audio (AUDIO 0x10) ─   │  ├─ NvFBC helper (NV)   │
│  ├─ QTabletEvent (pen)  │  ◄─ JSON health/clipboard ──────  │  ├─ mss / XShm          │
│  ├─ Tabbed sessions     │  ◄─ JSON cursor shapes ─────────  │  └─ Multi-monitor       │
│  ├─ Bookmark panel      │                                    │                         │
│  │  └─ status dots      │  ─ USB/IP (sideband) ──────────►  │  Cursor Tracker         │
│  ├─ Quality controls    │                                    │  └─ XFixes shape poll   │
│  ├─ USB device panel    │                                    │                         │
│  ├─ Health overlay      │                                    │  Video Encoder (FFmpeg)  │
│  ├─ Local cursor render │                                    │  ├─ h264_nvenc          │
│  ├─ File drag-and-drop  │                                    │  ├─ hevc_nvenc          │
│  ├─ Audio playback      │                                    │  ├─ libx264 / libx265   │
│  └─ Mic capture         │                                    │  └─ YUV 4:4:4 profiles  │
│                         │                                    │                         │
│  Decoders (PyAV)        │                                    │  Mic Injector           │
│  ├─ H.264 / H.265      │                                    │  └─ PA null-sink +      │
│  ├─ AV1                 │                                    │     pacat injection     │
│  └─ HW accel:           │                                    │                         │
│     macOS → VideoToolbox│                                    │  Input Injection         │
│     Linux → CUDA / VAAPI│                                    │  ├─ XTest (Xvfb)        │
│     Win   → D3D11VA     │                                    │  ├─ uinput (physical)   │
│                         │                                    │  └─ Wacom tablet device │
│                         │                                    │                         │
│                         │                                    │  Session Manager (PAM)   │
│                         │                                    │  ├─ Per-user Xvfb/Xorg  │
│                         │                                    │  ├─ GNOME shell          │
│                         │                                    │  └─ PulseAudio per-user  │
│                         │                                    │                         │
│                         │                                    │  USB/IP (vhci-hcd)      │
│                         │                                    │  Clipboard (xclip)      │
│                         │                                    │  File Receiver          │
│                         │                                    │  Auth (PAM / local)     │
└─────────────────────────┘                                    └─────────────────────────┘
```

## Installation

### Server (Linux)

The install script handles everything — system packages, Python venv, uinput permissions, kernel modules, and systemd service:

```bash
sudo bash install-server.sh
```

Options:
```bash
sudo bash install-server.sh --no-service    # skip systemd setup
sudo bash install-server.sh --no-auth       # skip user creation prompt
```

Installs to `/opt/teraguchi` with a `.venv` and creates the `teraguchi-server` systemd service.

#### Manual Setup

```bash
pip install -r requirements-server.txt

# System dependencies (RHEL/Rocky)
sudo dnf install ffmpeg pulseaudio-utils xclip

# System dependencies (Ubuntu/Debian)
sudo apt install ffmpeg pulseaudio-utils xclip

# uinput and USB/IP kernel modules
sudo modprobe uinput vhci-hcd usbip-core

# Start
python -m server.main --port 4443 --codec h264 --chroma yuv444 \
  --tls-cert certs/cert.pem --tls-key certs/key.pem
```

### Client (macOS / Windows / Linux)

```bash
bash install-client.sh           # install dependencies
bash install-client.sh --build   # also build standalone app with PyInstaller
```

Or manually:
```bash
pip install -r requirements-client.txt
python -m client.main
```

Connect directly from CLI:
```bash
python -m client.main --host 10.10.0.12 --port 4443 -u randy -p mypassword
```

### Build Standalone Client

```bash
pip install pyinstaller
python build_client.py
# Output: dist/Teraguchi/
```

## Server Configuration

### CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Listen address |
| `--port` | `443` | Listen port |
| `--fps` | `30` | Target frame rate |
| `--codec` | `h264` | Video codec: `h264`, `h265`, `av1`, `jpeg` |
| `--chroma` | `yuv444` | Chroma subsampling: `yuv420`, `yuv422`, `yuv444` |
| `--lossless` | off | Lossless H.264 encoding |
| `--max-bandwidth` | `50` | Bandwidth cap in Mbps |
| `--monitor` | `1` | Monitor index (`0` = all monitors) |
| `--sw-only` | off | Force software encoding (no GPU) |
| `--tls-cert` | — | TLS certificate PEM file |
| `--tls-key` | — | TLS private key PEM file |
| `--no-audio` | off | Disable audio streaming |
| `--no-clipboard` | off | Disable clipboard sync |
| `--verbose` | off | Debug logging |

### Authentication

| Flag | Description |
|------|-------------|
| `--auth-mode pam` | PAM authentication — per-user X sessions (recommended) |
| `--auth-mode local` | Local user database with challenge-response |
| `--auth-mode none` | No authentication — shared display |
| `--no-auth` | Alias for `--auth-mode none` |
| `--add-user USERNAME` | Create a local auth user (interactive password prompt) |

### Session Options (PAM mode)

| Flag | Default | Description |
|------|---------|-------------|
| `--width` | `1920` | Session display width |
| `--height` | `1080` | Session display height |
| `--dpi` | `96` | Session display DPI |

### Systemd Service

```bash
sudo systemctl enable --now teraguchi-server
sudo systemctl status teraguchi-server
sudo journalctl -u teraguchi-server -f
```

Logs: `/var/log/teraguchi/server.log`

## Client Usage

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Ctrl+N** | New connection |
| **Ctrl+D** | Disconnect |
| **Ctrl+Shift+S** | Send file to remote |
| **F5** | Refresh frame |
| **F9** | Toggle health overlay |
| **F10** | Key diagnostic |
| **F11** | Toggle fullscreen |

### Toolbar Icons

Connect, Disconnect, Refresh, Fullscreen, Health, Key Diagnostic, Monitor Selector, Settings (quality panel), USB Devices

### Quality Slider

The quality bias slider maps to encoder parameters:

| Position | CRF | Preset | FPS | Chroma | Behavior |
|----------|-----|--------|-----|--------|----------|
| 0% Smooth | 28 | ultrafast | Max | YUV 4:2:0 | Prioritize frame rate |
| 50% Balanced | 22 | veryfast | Max | YUV 4:2:2 | Good trade-off |
| 100% Sharp | 15 | medium | Capped 30 | YUV 4:4:4 | Prioritize image quality |

### File Transfer

- Drag files from Finder/Explorer onto the viewer window
- Or use **File > Send File...** (Ctrl+Shift+S)
- Files are checksummed (SHA-256) and transferred in 256 KB chunks
- Destination: `~/Desktop` on the remote machine

### Microphone

The client microphone is streamed to the server automatically when a session connects. No configuration is required.

On the **server**, a PulseAudio virtual sink named `teraguchi_mic` is created. Applications can select it as their input source:

- **Unreal Engine 5** — Settings → Audio → Input Device → `Teraguchi Microphone`
- **OBS / Discord / Zoom** — select `Monitor of Teraguchi Microphone` in audio input settings
- **Command line:** `pactl set-default-source teraguchi_mic.monitor`

The virtual sink persists only while a client is connected; it is removed on disconnect.

> **Requirement:** PulseAudio must be running in the user's session on the server. If no mic audio appears, check `pactl info` on the server to verify the PulseAudio daemon is active.

### Power Management

Each bookmark can optionally configure a **power backend** to control the remote machine directly from the bookmark panel — without needing an active session.

**UI:** When a bookmark has power management configured, a small power icon appears on the right side of its row. Left-clicking the icon or right-clicking the bookmark → **Power** opens a menu with **Power On**, **Power Off**, and **Reboot**. Power Off and Reboot require confirmation. To configure power settings, right-click → **Power Settings...**.

#### Supported backends

| Backend | Power On | Power Off / Reboot | Notes |
|---|---|---|---|
| `ssh` | WoL (if MAC configured) | SSH `systemctl poweroff/reboot` | Works for Linux and Windows |
| `teraguchi` | WoL (if MAC configured) | In-band via live session; falls back to SSH | Best option when teraguchi server is running |
| `wol` | Wake-on-LAN | — | Power-on only; no off/reboot |
| `none` / empty | — | — | Power buttons hidden in UI |

#### Bookmark fields

Power settings are stored as flat fields inside the bookmark JSON (`~/.config/teraguchi/bookmarks.json` on Linux, `~/Library/Application Support/Teraguchi/bookmarks.json` on macOS). They can also be set via **right-click → Power Settings...** in the UI.

| Field | Default | Description |
|---|---|---|
| `power_backend` | `""` | `"ssh"`, `"teraguchi"`, `"wol"`, `"none"`, or `""` (disabled) |
| `power_os` | `"linux"` | `"linux"` or `"windows"` — controls which shutdown command is sent |
| `power_ssh_host` | `""` | SSH host override; empty = use the bookmark's host |
| `power_ssh_user` | `""` | SSH user; empty = use the bookmark's username |
| `power_ssh_key` | `""` | Path to SSH private key; empty = SSH agent or password |
| `power_wol_mac` | `""` | MAC address for Wake-on-LAN, e.g. `"aa:bb:cc:dd:ee:ff"` |
| `power_wol_broadcast` | `"255.255.255.255"` | WoL broadcast address (change for a specific subnet) |

#### Examples

Linux workstation — SSH off/reboot + WoL power-on:

```json
{
  "power_backend": "ssh",
  "power_os": "linux",
  "power_ssh_user": "mihai",
  "power_ssh_key": "~/.ssh/id_rsa",
  "power_wol_mac": "aa:bb:cc:dd:ee:ff"
}
```

Windows workstation — SSH off/reboot via OpenSSH + WoL:

```json
{
  "power_backend": "ssh",
  "power_os": "windows",
  "power_ssh_host": "192.168.1.50",
  "power_ssh_user": "Administrator",
  "power_ssh_key": "~/.ssh/id_rsa",
  "power_wol_mac": "bb:cc:dd:ee:ff:00"
}
```

teraguchi server running — in-band shutdown with SSH fallback + WoL:

```json
{
  "power_backend": "teraguchi",
  "power_os": "linux",
  "power_ssh_user": "mihai",
  "power_ssh_key": "~/.ssh/id_rsa",
  "power_wol_mac": "aa:bb:cc:dd:ee:ff"
}
```

#### Fallback logic

```
Power On:  → send WoL magic packet to power_wol_mac
Power Off: 1. backend is "teraguchi" AND session is connected → POWER_ACTION over WebSocket
           2. SSH to power_ssh_host (or bookmark host) → sudo systemctl poweroff / Stop-Computer
           3. No SSH host configured → show error
Reboot:    same as Power Off but with reboot command
```

> **Server requirement for teraguchi in-band:** the server runs the systemctl command directly, so the `teraguchi` system user (or the session user) needs passwordless sudo for `systemctl poweroff` and `systemctl reboot`.

> **Windows SSH commands:** `Stop-Computer -Force` (off) and `Restart-Computer -Force` (reboot) via PowerShell over OpenSSH. Requires OpenSSH server to be installed and running on the Windows machine.

### USB Device Forwarding

1. Open the **USB Devices** panel (toolbar icon or View menu)
2. Select a device (Wacom tablet, keyboard, etc.)
3. Click **Forward** to pass it through to the server
4. The device appears as native USB hardware on the remote machine
5. Click **Detach** to reclaim it

> **macOS note:** Device enumeration works, but USB/IP forwarding requires [VirtualHere](https://www.virtualhere.com/) or a manual usbip setup. Linux and Windows clients can forward natively.

## Requirements

### Server
- Linux with X11 (RHEL/Rocky, Ubuntu, Debian)
- Python 3.10+
- FFmpeg with libx264 (H.264), libx265 (H.265), libsvtav1 (AV1), libopus
- PulseAudio or PipeWire
- xclip or xsel
- `/dev/uinput` access
- **GPU encoding (optional):** NVIDIA with NVENC, Intel with VAAPI, AMD with VAAPI/AMF

### Client
- macOS, Windows, or Linux
- Python 3.10+ (or standalone PyInstaller build)
- PySide6, PyAV, websockets
- Wacom tablet for pen pressure (mouse works without one)

## Project Structure

```
teraguchi/
├── client/
│   ├── main.py              # Main window, UI, tabs, menus
│   ├── session.py           # Per-connection session (protocol + viewer + decoder)
│   ├── protocol.py          # WebSocket client, message routing
│   ├── viewer.py            # Remote desktop widget, input capture, drag-and-drop
│   ├── video_decoder.py     # H.264/H.265/AV1 decode via PyAV
│   ├── audio_player.py      # Audio playback via QAudioSink (server → client)
│   ├── mic_capture.py       # Microphone capture via QAudioSource (client → server)
│   ├── file_transfer.py     # Chunked file sender
│   ├── usb_forward.py       # USB device enumeration and forwarding
│   ├── bookmarks.py         # Bookmark storage with encrypted credentials
│   ├── quality_control.py   # Quality settings panel
│   ├── health_display.py    # Health overlay and status bar widget
│   ├── icons.py             # Inline SVG icons (Lucide-style)
│   ├── theme.py             # Dark theme colors and stylesheet
│   ├── monitor_selector.py  # Multi-monitor checkbox selector
│   ├── fullscreen_toolbar.py # Auto-hiding toolbar for fullscreen mode
│   └── key_diagnostic.py    # Key event debug dialog
├── server/
│   ├── main.py              # Server entry point, SessionRuntime, WebSocket handler
│   ├── screen_capture.py    # Screen capture dispatch (NvFBC, mss/XShm, multi-monitor)
│   ├── nvfbc/               # Sandboxed NvFBC helper (tear-free NVIDIA framebuffer grab)
│   ├── cursor_tracker.py    # XFixes cursor shape poller for local client rendering
│   ├── video_encoder.py     # FFmpeg H.264/H.265/AV1 encoder with HW accel
│   ├── input_injector.py    # uinput mouse/keyboard/pen injection
│   ├── xtest_injector.py    # XTest injection for Xvfb virtual displays
│   ├── audio_capture.py     # PulseAudio/PipeWire audio capture (server → client)
│   ├── mic_injector.py      # PulseAudio virtual mic injection (client → server)
│   ├── clipboard.py         # Clipboard sync via xclip
│   ├── file_transfer.py     # Chunked file receiver with checksum verification
│   ├── usb_passthrough.py   # USB/IP device attach/detach
│   ├── session_manager.py   # Per-user Xvfb/Xorg session management
│   ├── auth.py              # Local authentication (challenge-response)
│   ├── pam_auth.py          # PAM authentication
│   └── health.py            # Health monitoring and stats
├── common/
│   ├── messages.py          # Protocol definitions (binary frames + JSON messages)
│   ├── keymap.py            # Qt key to Linux scancode mapping
│   ├── hybrid_transport.py  # WebSocket + UDP hybrid transport
│   ├── udp_transport.py     # UDP transport for low-latency frames
│   ├── jitter_buffer.py     # Jitter buffer for UDP frame reordering
│   └── quic_transport.py    # QUIC transport (experimental)
├── install-server.sh        # Server installer (system deps + venv + systemd)
├── install-client.sh        # Client installer (venv + optional PyInstaller build)
├── install-client.ps1       # Client installer (Windows PowerShell)
├── build_client.py          # PyInstaller build script
├── pyproject.toml           # Package metadata
├── requirements-server.txt  # Server Python dependencies
├── requirements-client.txt  # Client Python dependencies
└── requirements-dev.txt     # Development dependencies
```

## Protocol

Teraguchi uses a hybrid WebSocket protocol:

- **Binary frames** for video and audio (low overhead, type-length headers)
- **JSON messages** for control, input, health, clipboard, file transfer, USB

### Binary Frame Format

**Video:**
```
[1B frame_type] [1B codec] [1B chroma] [1B flags] [4B timestamp_ms] [2B monitor_id] [payload]
```

**Audio (server → client):**
```
[1B frame_type=0x10] [1B codec] [2B reserved] [4B timestamp_ms] [payload]
```

**Microphone (client → server):**
```
[1B frame_type=0x11] [1B codec] [2B reserved] [4B timestamp_ms] [payload: PCM s16le]
```

### Message Types

| Category | Messages |
|----------|----------|
| Handshake | `client_hello`, `server_hello` |
| Auth | `auth_request`, `auth_response`, `auth_result` |
| Input | `mouse_move`, `mouse_button`, `mouse_scroll`, `key_event`, `pen_event` |
| Video | `request_full_frame`, `quality_settings`, `select_monitor`, `resize_request` |
| Health | `health_ping`, `health_pong`, `health_stats` |
| Clipboard | `clipboard_send`, `clipboard_recv` |
| File Transfer | `file_offer`, `file_accept`, `file_chunk`, `file_done`, `file_ack`, `file_cancel` |
| USB | `usb_device_list`, `usb_attach`, `usb_detach`, `usb_attached`, `usb_detached`, `usb_error` |
| Monitor | `monitor_list` |
| Cursor | `cursor_update` |

## Comparison

| Feature | Teraguchi | Teradici PCoIP | Parsec | HP RGS |
|---------|-----------|----------------|--------|--------|
| Open Source | **Yes** | No | No | No |
| License Cost | **Free** | ~$300/seat/yr | Freemium | ~$200/seat |
| H.264 YUV 4:4:4 | **Yes** | Yes | Yes | No |
| H.265 / AV1 | **Yes** | H.265 only | H.265 only | No |
| GPU Encoding | **NVENC/VAAPI/AMF** | PCoIP card | NVENC | Software |
| Wacom Pen (8192 levels) | **Yes** | Limited | No | No |
| Pen Tilt + Rotation | **Yes** | No | No | No |
| USB Passthrough | **Yes** | Yes | No | No |
| File Transfer | **Yes** | Yes | No | No |
| Lossless Mode | **Yes** | Yes | No | Yes |
| Per-User Sessions | **Yes (PAM)** | Yes | No | Yes |
| Multi-Monitor | **Yes** | Yes | Yes | Yes |
| Clipboard Sync | **Yes** | Yes | Yes | Yes |
| Audio playback | **Yes** | Yes | Yes | Yes |
| Microphone streaming | **Yes** | Yes | No | No |
| TLS | **Yes** | Yes | Built-in | No |
| Auto-Reconnect | **Yes** | Yes | Yes | No |
| Linux Server | **Yes** | Yes | Yes | Yes |
| macOS Client | **Yes** | Yes | Yes | No |
| Windows Client | **Yes** | Yes | Yes | Yes |

## License

MIT
