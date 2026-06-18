# External Integrations

**Analysis Date:** 2026-04-18

## APIs & External Services

Teraguchi is an on-premises system. There are no cloud SaaS integrations, no webhooks to third parties, and no outbound API calls to external providers. Every integration is with local OS services, local network peers, or the FreeIPA directory.

**Identity Provider:**
- **FreeIPA / LDAP** — Primary authorization source for the broker.
  - Module: `broker/freeipa.py` (`FreeIPAClient`)
  - Default servers: `ldap://dxs-salt.the-dxs.com`, `ldap://dxs-salty.the-dxs.com` (bidirectional replicas)
  - Default base DN: `dc=the-dxs,dc=com`
  - Primary bind: GSSAPI / Kerberos host keytab (`sasl_interactive_bind_s`).
  - Admin UI bind: LDAP simple bind at `uid=<user>,cn=users,cn=accounts,<base_dn>` (`broker/admin.py::_ldap_bind_auth()`).
  - Fallback when `python-ldap` is missing: `subprocess.run(["id", "-Gn", username])` on FreeIPA-joined hosts (`broker/freeipa.py::_get_groups_fallback()`).
  - Group cache TTL: 60 s (`broker/main.py::group_cache`, `broker/admin.py::AUTH_CACHE_TTL = 300`).
  - Required groups: `teraguchi-users` (access), `teraguchi-admins` (admin UI + share busy machines).

**Authentication (system-level):**
- **Linux PAM** — Authenticates interactive users on both the server and the broker.
  - Module: `server/pam_auth.py` (`PAMAuthenticator`), imported by `server/auth.py` and `broker/main.py`.
  - PAM service: `login` (default).
  - Must run as root. `server/main.py` enforces `os.geteuid() != 0` → exit when `--auth-mode pam`.
  - Forced off on macOS (`server/main.py` line ~1070: `if IS_MACOS and args.auth_mode == "pam": args.auth_mode = "none"`).

- **Local JSON user database** — Legacy fallback auth.
  - File: `~/.config/teraguchi/users.json`
  - Challenge-response (SHA-256 of `password:challenge`).
  - Module: `server/auth.py::Authenticator(mode="local")`.

**NVIDIA GPU Integration:**
- `libnvidia-fbc.so.1` — NvFBC zero-copy framebuffer capture, loaded out-of-process by the compiled `nvfbc_capture` C helper (`server/nvfbc/nvfbc_capture.c`, backend `server/nvfbc/nvfbc_backend.py`). Kept in a sandboxed subprocess so a driver crash doesn't kill the server.
- `nvidia-smi` — Probed by `install-server.sh` to verify driver presence.
- NVENC via FFmpeg (`h264_nvenc`, `hevc_nvenc`, `av1_nvenc`) — detected by `server/video_encoder.py::detect_encoders()`.
- CUDA decode via PyAV (`client/video_decoder.py::HW_DECODERS["h264"][0] == ("h264", "cuda")`).

**Intel / AMD GPU Integration:**
- VAAPI via FFmpeg (`h264_vaapi`, `hevc_vaapi`, `av1_vaapi`) — server encode.
- AMF via FFmpeg (`h264_amf`, `hevc_amf`) — server encode (AMD on Windows/Linux).
- VAAPI / DXVA2 / D3D11VA via PyAV — client decode.

**Apple Integration (macOS server):**
- **ScreenCaptureKit** (`server/mac_screen_capture.py`) — Frame source. Requires macOS 12.3+ and "Screen & System Audio Recording" TCC.
- **CoreGraphics** (`server/mac_input_injector.py`) — Input via `CGEventPost`. Requires "Accessibility" and "Input Monitoring" TCC.
- **NSPasteboard** (`server/mac_clipboard.py`) — Clipboard, polled via `changeCount` (no push API).
- **AVFoundation / CoreAudio / CoreMedia** — Audio capture and `CMSampleBuffer` handling.
- **VideoToolbox** — FFmpeg hardware encode (`h264_videotoolbox`, `hevc_videotoolbox`, `av1_videotoolbox`).

## Data Storage

**Databases:**
- None. No SQL, no ORM, no KV store.

**File-backed state:**
- `/etc/teraguchi/broker.yml` — YAML config (machine pool, static assignments).
- `/var/log/teraguchi/assignments.yml` — Writable assignments state, persisted by `broker/admin.py::_save_assignments()` when admins change mappings via the web UI.
- `/etc/teraguchi/broker.secret` — Shared HMAC-SHA256 signing secret for broker-issued tokens (`broker/tokens.py`).
- `~/.config/teraguchi/users.json` — Local auth user DB (server legacy mode).
- `~/Library/Application Support/Teraguchi/bookmarks.json` (macOS) / `%APPDATA%/Teraguchi/` (Windows) / `~/.config/teraguchi/` (Linux) — Client bookmarks with XOR-encrypted passwords (`client/bookmarks.py`).
- `/var/log/teraguchi/server.log` — Server log (Linux systemd).
- `~/Library/Logs/Teraguchi/server.{log,err.log}` — Server log (macOS LaunchAgent).

**File Storage:**
- File transfer lands incoming files in `~/Desktop` on the remote machine (`server/file_transfer.py`, 2 GB max, 256 KB chunks, SHA-256 verified).

**Caching:**
- In-process group-membership cache in `broker/main.py` (60 s TTL).
- In-process auth cache in `broker/admin.py` (300 s TTL, keyed by `sha256(user:pass)`).
- Per-user jitter buffer in `common/jitter_buffer.py` for UDP frame reordering.

## Authentication & Identity

**Flow — Direct client-to-server:**
1. Client opens `wss://host:port/`.
2. Server sends `AUTH_REQUEST` with `auth_methods=["pam"|"local"|"token"]` plus `challenge` and `salt`.
3. Client replies with `AUTH_RESPONSE` containing `username` + `credential` (plain password for PAM over TLS; SHA-256 challenge-hash for local).
4. Server verifies via `Authenticator` and replies with `AUTH_RESULT`.

**Flow — Brokered:**
1. Client connects to broker at `wss://broker:8443`.
2. Broker sends `AUTH_REQUEST` with `auth_mode="pam"`; client sends `AUTH_RESPONSE` with password.
3. Broker calls `PAMAuthenticator.authenticate()`. On success, broker queries FreeIPA (`FreeIPAClient.get_user_groups_cached()`) and checks `teraguchi-users` membership.
4. Broker sends `BROKER_HELLO` with the list of machines the user is allowed to see (admins see all, regular users filtered by `MachinePool.user_can_access()`).
5. Client picks a machine (or broker auto-assigns the least-loaded healthy host via `MachinePool.assign()`).
6. Broker generates an HMAC-SHA256 token (`broker/tokens.py::generate_token()`, 60 s TTL) with payload `username:machine:issued:expires` and sends `BROKER_ASSIGN` with host, port, and token.
7. Client disconnects from broker and opens a new `wss://` session directly to the assigned server, presenting the token instead of a password.
8. Server verifies the token using the shared secret file (`--broker-secret /etc/teraguchi/broker.secret`).

**Token format** (`broker/tokens.py`):
```
username:machine:issued_at:expires_at:hmac_sha256_hex
```

**Credential storage:**
- Broker → server: HMAC-SHA256 signed tokens with a shared secret file.
- Client bookmarks: XOR with a machine-derived key (hostname + CPU arch + home dir + `/etc/machine-id`). `client/bookmarks.py` explicitly notes this is obfuscation, not real encryption.

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry, no Rollbar, no Bugsnag).

**Logs:**
- Python `logging` module in every component.
- Format: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`.
- Logger names: `teraguchi.server`, `teraguchi.broker`, `teraguchi.client` (and dotted children per module).
- Linux server logs: `/var/log/teraguchi/server.log` + journald (via `teraguchi-server.service`).
- Broker logs: journald (`SyslogIdentifier=teraguchi-broker`).
- macOS server logs: `~/Library/Logs/Teraguchi/server.{log,err.log}`.

**Metrics / Health:**
- Server exposes `/status` HTTPS endpoint (served via the same port as the WebSocket listener by the `process_request` hook in `server/main.py`). Returns `{active_sessions, load_avg, uptime_s}` as JSON.
- Broker probes every server every 15 s via `aiohttp.ClientSession` → `https://host:port/status` (`broker/pool.py::_probe_one()`). Unhealthy after 3 consecutive failures; healthy again after 2 consecutive successes.
- Server pushes `HealthStats` messages (RTT, FPS actual/target, bandwidth, dropped frames, encode/capture timing, input latency) to each client. Client renders them in the F9 overlay (`client/health_display.py`).

## CI/CD & Deployment

**Hosting:**
- On-prem only. Target infra is the DXS fleet (`dxs-flame-01` through `dxs-flame-06`), broker on `dxs-ansible` (10.10.0.x).
- No Docker / Kubernetes / cloud services.

**CI Pipeline:**
- None detected. No `.github/workflows`, `.gitlab-ci.yml`, or similar.

**Deployment:**
- Linux server: `sudo bash install-server.sh` → installs under `/opt/teraguchi`, enables `teraguchi-server.service`.
- macOS server: `bash install-server-macos.sh` → installs under `~/Library/Application Support/Teraguchi`, registers `~/Library/LaunchAgents/com.dxs.teraguchi.server.plist`.
- Broker: manual deploy of `broker/teraguchi-broker.service` (no installer script committed).
- Client: `bash install-client.sh [--build]` on macOS/Linux, `install-client.ps1 [-Build] [-Shortcut]` on Windows. `--build` invokes `build_client.py` → PyInstaller.

## Environment Configuration

**Required env vars (runtime):**
- `DISPLAY` — Set by `server/session_manager.py` per user session on Linux. Inherited by FFmpeg and capture subprocesses.
- `PULSE_RUNTIME_PATH`, `XDG_RUNTIME_DIR` — Rebuilt per user by `server/audio_capture.py::_make_pulse_env()` so dropped-privileges audio capture reaches the correct PulseAudio socket (`/run/user/<uid>/pulse`).
- `PATH` — LaunchAgent plist pins `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin` so Homebrew-installed FFmpeg is found.

**No env files present.** `.env` is in `.gitignore` defensively but no committed examples exist.

**Broker CLI flags → env-independent config:**
- `--config /etc/teraguchi/broker.yml`
- `--secret /etc/teraguchi/broker.secret`
- `--ipa-servers`, `--ipa-base-dn`
- `--required-group teraguchi-users`
- `--admin-group teraguchi-admins`
- `--tls-cert`, `--tls-key`

**Secrets location:**
- `/etc/teraguchi/broker.secret` (root-owned, referenced by both broker and each server via `--broker-secret`).
- `/etc/teraguchi/tls/broker.{crt,key}` (broker TLS).
- Server TLS: `--tls-cert certs/cert.pem --tls-key certs/key.pem` (path is deployment-specific).
- No other files in the repo contain secrets.

## Webhooks & Callbacks

**Incoming HTTP endpoints:**
- Server `/status` — JSON health/load endpoint consumed by the broker (`server/main.py::handle_http`, served via the websockets `process_request` hook).
- Broker admin UI (`broker/admin.py`, served via `process_request` on the broker's WebSocket port):
  - `GET /` and `GET /admin` — HTML admin UI (`broker/static/index.html`).
  - `GET /api/machines` — Pool status JSON.
  - `GET /api/assignments` — User→machine map.
  - `GET /api/users` — FreeIPA users in `teraguchi-users` / `teraguchi-admins` via `getent group`.
  - `GET /api/set-user?user=…&machines=…` — Update an assignment (mutates `/var/log/teraguchi/assignments.yml`).
  - `GET /api/delete-user?user=…` — Remove an assignment.
  - `GET /api/ping` — Unauthenticated liveness.
  - `GET /static/*` — Admin UI assets.
  - Basic auth via `Authorization: Basic …`; credentials verified by LDAP simple bind + `teraguchi-admins` group check.

**Outgoing HTTP calls:**
- Broker → Server `/status` every 15 s (TLS, `ssl=False` = certificate verification disabled).
- No other outbound HTTP.

## Network Protocols & Transport

Teraguchi is transport-rich. Every data flow uses one of these channels:

**TLS WebSocket (`wss://`) — primary control + data channel:**
- Library: `websockets>=12.0`
- Server default port: 443 (configurable via `--port`).
- Broker default port: 8443.
- `max_size`: 50 MB (server) / 1 MB (broker).
- Keepalive: `ping_interval=20`, `ping_timeout=30`.
- Used for: handshake, auth, input events, cursor updates, clipboard sync, file transfer, USB control, health pings, and (in TCP-fallback mode) video + audio frames.
- Binary frames carry video (`FrameType.VIDEO_H264/H265/AV1/PARTIAL`) and audio (`FrameType.AUDIO`) with packed binary headers defined in `common/messages.py::encode_video_header` and `encode_audio_header`.
- JSON messages carry control traffic (`MsgType.MOUSE_MOVE`, `CURSOR_UPDATE`, `FILE_CHUNK`, etc.).

**UDP — hybrid media transport:**
- Module: `common/udp_transport.py`.
- 16-byte custom header: `magic(2)=0x5447 "TG" | channel(1) | flags(1) | sequence(4) | timestamp_ms(4) | frag_id(2) | frag_total(2)`.
- Channels: `0x01` video, `0x02` audio.
- Negotiated over the TCP WebSocket: client announces its UDP port (`udp_announce`), server sends a probe (`udp_probe`), client confirms (`udp_confirmed`), server flips to `UDP_MEDIA` mode. On probe failure, both sides stay in `TCP_ONLY` mode.
- MTU: 1400-byte default (`DEFAULT_MTU`), fragmentation supported.
- Lost packets are skipped (no retransmit); jitter buffer on the client reorders by timestamp (`common/jitter_buffer.py`).
- DTLS encryption is referenced in the module header comment but implemented in an experimental form.

**QUIC (RFC 9000 / RFC 9221) — experimental alternative to TCP+UDP:**
- Module: `common/quic_transport.py`.
- Library: `aioquic>=1.0` (optional; module gracefully degrades if missing).
- Default server port: 444 (see `teraguchi-server.service`).
- Stream 0 = bidirectional control channel; Stream 4 = health/stats; datagrams carry video + audio.
- 6-byte datagram header: `channel(1) | flags(1) | timestamp_ms(4)` — lighter than UDP transport because QUIC supplies its own sequencing.
- TLS 1.3, 0-RTT reconnect, connection migration (Wi-Fi ↔ cellular handoff).

**USB/IP — sideband protocol:**
- Module: `server/usb_passthrough.py` (`USBIPServer`) + `client/usb_forward.py`.
- Server: Linux kernel's `vhci-hcd` + `usbip-core` subsystem.
- Client enumeration: `system_profiler` (macOS), `usbipd` (Windows), `lsusb` (Linux).
- Raw USB frames are tunneled inside the main WebSocket via `USB_*` JSON messages; the server pushes them into usbip locally so the device appears as native hardware (Wacom tablets, keyboards, HID).
- Not supported natively on macOS clients — requires VirtualHere.

**Inter-process — same host:**
- Server ↔ NvFBC helper: anonymous pipe, binary protocol `TGFR` + `width | height | size | BGRA...` (`server/nvfbc/nvfbc_backend.py`).
- Server ↔ FFmpeg: stdin/stdout pipes with raw BGRA in, encoded NAL/OBU out (`server/video_encoder.py`).
- Server ↔ Xvfb/Xorg: spawned as child processes with isolated `DISPLAY=:10+`; drained via `xauth` cookies (`server/session_manager.py`).
- Server ↔ `pactl` / `xclip` / `xsel` / `system_profiler` / `usbip` / `lsusb` / `getent`: `subprocess.run` invocations.

**Inter-component — same deployment:**
- Client ↔ Broker: `wss://broker:8443`.
- Client ↔ Server: `wss://server:443` (or `quic://server:444`).
- Broker ↔ Server: only an HTTPS `GET /status` health probe. The broker does not relay media — it hands out signed tokens and gets out of the way.

## Cross-Process Dependencies (graph)

```
Client (PySide6, PyAV)
  │
  ├── wss:443 ──► Server (teraguchi-server.service on Linux, LaunchAgent on macOS)
  │                 │
  │                 ├── FFmpeg subprocess (encode)
  │                 ├── nvfbc_capture helper (NVIDIA only)
  │                 ├── Xvfb / Xorg per-user session
  │                 ├── pactl / xclip / uinput / XTest
  │                 └── usbip / vhci-hcd
  │
  ├── wss:8443 ──► Broker (teraguchi-broker.service)
  │                 │
  │                 ├── PAM (python-pam)  ───► local /etc/shadow + SSSD
  │                 ├── LDAP (python-ldap) ──► FreeIPA (dxs-salt, dxs-salty)
  │                 └── aiohttp /status probe ──► each Server
  │
  └── USB/IP sideband tunneled through the Server wss:443 channel
```

---

*Integration audit: 2026-04-18*
