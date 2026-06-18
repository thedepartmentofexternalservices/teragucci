# Codebase Concerns

**Analysis Date:** 2026-04-18

## Tech Debt

**Legacy auth mode (local JSON user database):**
- Issue: Local-file authentication is a parallel auth path maintained alongside PAM that uses SHA-256 + salt challenge-response — no bcrypt/argon2 and no rate-limiting. Documented as "legacy" throughout but still wired up as a first-class `--auth-mode local`.
- Files: `server/auth.py` (lines 7, 37, 106 — `# ── Local (legacy) authentication`), `server/main.py` (lines 18, 86, 677, 817, 1131), `client/protocol.py` (lines 6, 49)
- Impact: Two divergent auth flows to maintain; macOS falls back to it by default (`install-server-macos.sh` starts the server with `--auth-mode none`). SHA-256 password hashing is fast enough that a leaked `users.json` is recoverable offline.
- Fix approach: Deprecate local mode entirely on macOS (use keychain-backed auth) or upgrade the hash to argon2id via `argon2-cffi`. Add rate limiting to `Authenticator.verify`.

**Two parallel legacy compat paths in broker pool:**
- Issue: `broker/pool.py` accepts old-style `pool: dedicated` + `assigned_user:` per-machine fields and converts them to the new `assignments:` map at load time.
- Files: `broker/pool.py:34-37` (`Legacy fields (ignored...)`), `broker/pool.py:79-87` (`Legacy support: if no assignments...`)
- Impact: Config schema ambiguity — someone editing `broker.yml` can set both formats and see non-deterministic behavior.
- Fix approach: Log a deprecation warning when legacy fields are present; drop them in the next minor version.

**JPEG dirty-rect fallback lives alongside the H.264 pipeline:**
- Issue: A complete second encode path (`JpegFallbackEncoder`, `encode_jpeg_header`, `FrameType.VIDEO_PARTIAL`, dirty-rectangle detection in `screen_capture.py` and `mac_screen_capture.py`) is maintained for environments without FFmpeg, even though FFmpeg is a hard dependency of the installer.
- Files: `common/messages.py:80, 106-113`, `server/video_encoder.py::JpegFallbackEncoder`, `server/screen_capture.py::capture_dirty_regions`, `server/mac_screen_capture.py:605-688`, `server/main.py:299-323` (`_stream_jpeg`)
- Impact: Dead-weight code path; macOS implementation duplicates ~80 lines of dirty-region logic that exists on Linux.
- Fix approach: Make JPEG fallback optional behind a build flag, or delete it now that FFmpeg is required.

**Monolithic `server/main.py` and `server/session_manager.py`:**
- Issue: `server/main.py` is 1,191 lines and contains the `SessionRuntime`, `ClientSession`, WebSocket handler, HTTP `/status` endpoint, TLS context builder, CLI arg parsing, and main event loop all in one module. `server/session_manager.py` is 1,317 lines.
- Files: `server/main.py`, `server/session_manager.py`, `client/main.py` (1,189 lines)
- Impact: Hard to test in isolation; the per-session lifecycle, protocol handling, and streaming loops are tightly coupled.
- Fix approach: Extract `SessionRuntime` into `server/session_runtime.py`, HTTP status handler into `server/http_status.py`, CLI into `server/cli.py`.

**macOS backend shares capture path that assumes Linux semantics:**
- Issue: `SessionRuntime.__init__` still sets/restores `DISPLAY` around capture/injector construction even on macOS where `DISPLAY` is meaningless; same block wires `cursor_tracker` which is gated, but the `DISPLAY` manipulation is not.
- Files: `server/main.py:106-138`
- Impact: Harmless on Darwin but signals that the platform split is not clean — future contributors may assume `DISPLAY` matters on macOS.
- Fix approach: Hoist the `DISPLAY` handling into the Linux branch only.

## Known Bugs

**xrandr resize crashes the NVIDIA X server:**
- Symptoms: The entire server process segfaults when a client sends `MsgType.RESIZE_REQUEST` on a GPU display. The code path is intentionally stubbed out with a logger message.
- Files: `server/main.py:548-552` (explicit `# TODO: investigate safe resize path for GPU displays`)
- Trigger: Any client that sends `resize_request` while the session is backed by real Xorg + NVIDIA (as opposed to Xvfb). Xvfb resize used to work.
- Workaround: Disabled entirely for now — client cannot dynamically match the server display to window size. Client has to live with whatever geometry the session was created at (`--width`/`--height` CLI flags, default 1920x1200).

**macOS sysdep check was false-positive (recent fix):**
- Symptoms: On macOS the server used to log `Missing system dependency: pactl`, `Missing clipboard tool`, and `/dev/uinput not found` warnings even though the Mac backends don't use any of these.
- Files: `server/main.py:980-1000` — fixed in commit `08fd492` by early-returning from `check_system_dependencies` when `IS_MACOS`.
- Trigger: Running `server.main` on Darwin.
- Workaround: Already fixed on current `dev`. See "Platform Parity Gaps" below for similar Linux-assumptions that likely still leak through.

**Pen pressure/tilt silently dropped on macOS:**
- Symptoms: Clients with Wacom tablets will see pressure/tilt events ignored by the Mac server; only binary press/release is forwarded. A one-time warning is logged.
- Files: `server/mac_input_injector.py:358-398` (`pen_event`, `_pen_warned`), `server/mac_input_injector.py:376-382`
- Trigger: Any pen event with non-zero pressure sent to a macOS server.
- Workaround: Fall back to mouse mode in the client; accept that pressure is unavailable until an IOHIDUserDevice helper is shipped.

**`--no-auth` / `--auth-mode none` forces all users to `anonymous`:**
- Symptoms: When auth is disabled, all concurrent clients share a single session runtime named `anonymous`. No per-client isolation.
- Files: `server/main.py:770-771` (`session.username = "anonymous"`), `server/main.py:817-818`
- Trigger: Running the server with `--auth-mode none` (the default on macOS).
- Workaround: Use PAM mode on Linux. On macOS there is no alternative — SCK captures the logged-in user's desktop and there is only one session.

**Authentication timeout uses 30s fixed window, no retry limit:**
- Symptoms: A client that sits idle between the server's `AuthRequest` and its own `AuthResponse` for 30+ seconds gets a `asyncio.TimeoutError` and disconnects, but there is no per-IP backoff or attempt counter.
- Files: `server/main.py:706, 749`, `broker/main.py:96, 161`
- Trigger: Brute-force attacker can reconnect and retry indefinitely.
- Workaround: Depend on firewall rate-limiting (`ufw`, `firewall-cmd`) — not enforced by the app.

## Security Considerations

**TLS verification disabled on every client connection:**
- Risk: MITM between client and server (or between client and broker) is undetected — the client accepts any TLS certificate.
- Files: `client/protocol.py:291-296` (direct server connection), `client/protocol.py:360-365` (broker connection), `common/quic_transport.py:402-414` (`verify_cert=False` default), `broker/pool.py:153` (`aiohttp.TCPConnector(ssl=False)` for broker-to-server health probes)
- Current mitigation: Uses TLS for encryption, but certificate trust is deliberately bypassed (`check_hostname = False`, `verify_mode = ssl.CERT_NONE`). All broker health probes go over `https://` with SSL verification disabled.
- Recommendations: Pin the broker-issued server cert by SHA-256 fingerprint, delivered in `BROKER_ASSIGN`. Ship a DXS-internal CA and verify against it. At minimum, make `verify_cert` opt-in via CLI flag rather than always-off.

**Bookmark password "encryption" is XOR with a derived key:**
- Risk: Saved passwords in `bookmarks.json` are obfuscated with XOR using a key derived from hostname + CPU arch + home path + `/etc/machine-id`. Not cryptographic.
- Files: `client/bookmarks.py:37-85` (`_get_machine_key`, `_encrypt_password`, `_decrypt_password`)
- Current mitigation: Docstring explicitly calls it out: "This is NOT high-security encryption — it just prevents casual reading of saved passwords" (`client/bookmarks.py:44-45`).
- Recommendations: On macOS, use Keychain via `keyring` or `Security.framework`. On Windows, use DPAPI. On Linux, use `libsecret` / GNOME Keyring. `keyring` package covers all three.

**Broker HMAC token format leaks user-machine binding:**
- Risk: Tokens are `username:machine:issued:expires:sig` with no encryption — a passive observer sees which user got which machine. The token is one-time-use but only for 60 seconds.
- Files: `broker/tokens.py:21-33`, `server/auth.py:179-212` (server-side verification)
- Current mitigation: Delivered only over TLS; 60s TTL; HMAC-SHA256 with `hmac.compare_digest` prevents forgery and timing attacks.
- Recommendations: If username/machine secrecy matters, wrap the token in an AEAD (AES-GCM or ChaCha20-Poly1305). Not strictly necessary since TLS already protects it in transit.

**Broker signing secret auto-generated on every restart if the file is missing:**
- Risk: If `broker.secret` is missing, the broker silently generates an ephemeral `secrets.token_hex(32)` and logs a warning — but all previously issued tokens become invalid on restart and operators may miss the warning.
- Files: `broker/main.py:317-324`
- Current mitigation: Warning log only.
- Recommendations: Fail hard unless an explicit `--allow-ephemeral-secret` flag is passed. Generate and persist the secret on first install (extend `install-server.sh` / add a broker installer).

**`install-server.sh` prompts for a password and passes it through a Python `-c` block:**
- Risk: Password entered during `install-server.sh --setup-auth` is read by `getpass` inside the embedded Python one-liner. The one-liner has the target username interpolated via shell expansion (`'$AUTH_USER'`) — safe for benign usernames, but could break with quotes.
- Files: `install-server.sh:316-332` (the `sudo -u "$REAL_USER" "$VENV_DIR/bin/python" -c "import sys, getpass ..."` block)
- Current mitigation: `getpass.getpass` hides the password; shell variable only interpolates the username.
- Recommendations: Invoke `python -m server.main --add-user` as a subprocess with argv, rather than generating a Python one-liner via shell heredoc. Reject usernames containing quotes/backticks.

**Broker admin UI auth cache keyed by `sha256(username:password)`:**
- Risk: The cache key is a plain SHA-256 of the concatenation; no salt, no slow KDF. If the admin server process is dumped or swapped, the cache entries enable offline password cracking.
- Files: `broker/admin.py:57-70` (`cache_key = hashlib.sha256(f"{username}:{password}".encode()).hexdigest()`)
- Current mitigation: TTL 300s (`AUTH_CACHE_TTL`); cache is in-process only.
- Recommendations: Use a per-process random pepper in the hash, or store only `(username, expiry)` in the cache and re-validate on hit.

**USB/IP passthrough accepts `bus_id` from client without strong validation:**
- Risk: `usbip attach -r <client_host> -b <bus_id>` passes an attacker-supplied `bus_id` as a CLI argument. If `subprocess.run` ever switched to `shell=True` this would be shell injection; today it's constrained to `usbip` command-line parsing.
- Files: `server/usb_passthrough.py:159-186` (`attach_device`), `client/usb_forward.py:250-290`
- Current mitigation: `subprocess.run` uses argv form, not shell form.
- Recommendations: Validate `bus_id` against a regex (`^\d+-\d+(\.\d+)*$`) before invoking usbip.

**LDAP anonymous bind fallback for FreeIPA:**
- Risk: `broker/freeipa.py::_connect` tries SASL/GSSAPI first, then falls back to anonymous simple bind. Anonymous bind against FreeIPA usually cannot read `memberOf`, so the broker silently falls back to `id -Gn <user>` — but anonymous bind has succeeded in the log, which is confusing.
- Files: `broker/freeipa.py:60-73` (anonymous bind as last resort)
- Current mitigation: Group lookup has a `id` fallback that does work on FreeIPA-joined hosts.
- Recommendations: Drop the anonymous-bind fallback; require GSSAPI or a service account.

**`.env*` files exist in `.gitignore` but no credential policy is documented:**
- Risk: Installer does not create a `.env` or credentials file; broker secret and TLS keys live at `/etc/teraguchi/*` with no documented file-mode enforcement.
- Files: `.gitignore`, `broker/teraguchi-broker.service:14-17`
- Current mitigation: Systemd unit uses `ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp=yes`.
- Recommendations: Installer should `chmod 600` + `chown root:teraguchi` on `broker.secret` and TLS key; README should mandate this.

## Performance Bottlenecks

**Server sends identical encoded frame per-client via one `run_coroutine_threadsafe` call each:**
- Problem: When N clients are attached to the same `SessionRuntime`, the encoded H.264 NAL unit is scheduled onto the event loop N separate times rather than using a single broadcast.
- Files: `server/main.py:358-383` (`_on_encoded_frame`, `_enqueue_frame`), `server/main.py:385-390` (audio same pattern), `server/main.py:398-414` (clipboard + cursor)
- Cause: Each client has its own `asyncio.Queue` (`send_queue`, maxsize=30 in `ClientSession`) and its own `_send_loop`.
- Improvement path: Acceptable given typical fan-out is 1–2 clients (reconnection), but consider a shared broadcast queue if multi-observer becomes common.

**JPEG dirty-region detection runs NumPy diffs per frame:**
- Problem: `mac_screen_capture.py::capture_dirty_regions` and `screen_capture.py::capture_dirty_regions` compute `np.abs(frame - last_frame)` on every frame — that's a 4K BGRA subtraction (~33 MB operation) per capture at 30 fps.
- Files: `server/screen_capture.py:261-316`, `server/mac_screen_capture.py:605-688`
- Cause: Block-based change detection in Python/NumPy.
- Improvement path: Skip entirely when H.264 path is active (dirty-region is fallback-only). Cache `last_frame.astype(np.int16)` once per session instead of per frame.

**macOS screen capture allocates and strips padding every frame:**
- Problem: SCK delivers BGRA frames with IOSurface page-aligned stride; `mac_screen_capture.py` strips padding row-by-row into a fresh numpy array for every frame, with the encoder expecting tight `width*height*4`.
- Files: `server/mac_screen_capture.py:22-27` (documented), frame-copy loop in the capture output handler
- Cause: FFmpeg rawvideo path requires tight buffers.
- Improvement path: Feed FFmpeg with an explicit stride via `-pix_fmt` + `-s` and a custom `-vf` scaler, or write a ctypes memmove that strips padding in-place.

**Client-side `maxsize=30` send queue drops frames when slow:**
- Problem: When the network momentarily slows, `enqueue` returns False and `_on_encoded_frame` calls `record_frame_dropped()`. The dropped frame is a lost P-frame — the stream will stay broken until the next IDR (GOP = 2×fps = 60 frames = 2 s at 30 fps).
- Files: `server/main.py:378-382, 639` (`send_queue: asyncio.Queue = asyncio.Queue(maxsize=30)`)
- Cause: No keyframe request on drop.
- Improvement path: On drop, call `self.encoder.request_keyframe()` to force the next frame to be an IDR so the client can recover.

**Broker health probe uses a new `aiohttp.ClientSession` per probe:**
- Problem: `_probe_one` constructs a fresh `aiohttp.ClientSession` and TCP connector every 15 s per machine. Wasted SSL handshakes and connection pool setup.
- Files: `broker/pool.py:148-176`
- Cause: No long-lived session.
- Improvement path: Create one `ClientSession` in `MachinePool.__init__` and reuse. Close it in `stop()`.

**NvFBC helper is a subprocess per capture cycle on older code paths:**
- Risk: Older pipelines may spawn the C helper frequently; current architecture should keep it long-lived.
- Files: `server/nvfbc/nvfbc_backend.py`, `server/nvfbc/nvfbc_capture.c`
- Cause: Subprocess IPC for framebuffer pixels.
- Improvement path: Confirm the helper is kept alive for the session lifetime; if not, switch to shared memory for frame transfer.

## Fragile Areas

**Mac clipboard change detection polls every 250 ms:**
- Files: `server/mac_clipboard.py:43, 118-136` (`poll_interval: float = 0.25`)
- Why fragile: NSPasteboard has no notification API. If a user copies and pastes within 250 ms, the poll can miss the interim state. Write echo is suppressed by remembering `_last_change_count`, but that breaks if another process also touches the clipboard between the set and the next poll.
- Safe modification: Keep a ring buffer of the last N `changeCount` values and only suppress our own.
- Test coverage: None.

**`session_manager.py::_find_input_event_device` depends on `/proc/bus/input/devices` format:**
- Files: `server/session_manager.py:34-56`
- Why fragile: Parses the free-form text output of `/proc/bus/input/devices`. Kernel changes the format rarely, but this is string parsing on a uapi surface.
- Safe modification: Parse each block top-to-bottom, handle trailing blank lines.
- Test coverage: None.

**Xorg session spawn has to race uinput device creation, EDID detection, Xauthority setup, and compositor startup:**
- Files: `server/session_manager.py:500-720` (Xorg + Xvfb launch, various cvt/xrandr calls)
- Why fragile: Multi-process orchestration with sleeps and file-existence polling. If `nvidia-xconfig` or `cvt` timing changes, sessions fail to start.
- Safe modification: Capture stderr from every subprocess; add retries with exponential backoff on transient errors.
- Test coverage: None (no CI harness for Xorg sessions).

**H.264 NAL unit boundary detection reads raw ffmpeg pipe:**
- Files: `server/video_encoder.py:596-624` (`_read_h264_output`), similar for `_read_hevc_output`, `_read_ivf_output`
- Why fragile: Scans for `\x00\x00\x00\x01` start codes in a byte buffer. Works, but if ffmpeg ever emits 3-byte start codes (`\x00\x00\x01`), every frame would be misframed.
- Safe modification: Match both 3-byte and 4-byte start codes.
- Test coverage: None.

**`client/protocol.py::_connect_to_server` conflates broker-redirect and direct-connect flows:**
- Files: `client/protocol.py:33-34` (`_BrokerRedirect` exception), `client/protocol.py:285-352`
- Why fragile: The broker-detection path reuses `_connect_to_server` with an exception-based control-flow, which makes reconnection logic hard to reason about.
- Safe modification: Split into `_connect_direct` and `_connect_via_broker` explicitly.
- Test coverage: None.

**macOS SCK setup relies on blocking the main thread with `NSCondition`:**
- Files: `server/mac_screen_capture.py:28-32` (documented design note)
- Why fragile: SCK delivers completion handlers on the main thread; the wrapper uses `NSCondition` to turn async into sync during init/close. If the server is run from an already-main-threaded context (e.g., embedded in a Cocoa app), this could deadlock.
- Safe modification: Keep SCK calls explicitly on a background thread with a dedicated runloop.
- Test coverage: None.

**`DISPLAY` env var manipulation is not thread-safe:**
- Files: `server/main.py:106-138` (`old_display = os.environ.get("DISPLAY"); os.environ["DISPLAY"] = display; ... os.environ["DISPLAY"] = old_display`)
- Why fragile: `os.environ` is process-global. If two sessions initialize concurrently (which PAM mode allows), they race.
- Safe modification: Pass `DISPLAY` via a local dict to subprocesses with `env=` instead of mutating `os.environ`.
- Test coverage: None.

## Platform Parity Gaps (Linux vs macOS Backends)

**USB passthrough is Linux-only:**
- Problem: `server.usb_passthrough.USBForwardingManager` is gated off on Mac by `self.usb_manager = None if IS_MACOS else USBForwardingManager()` in `SessionRuntime.__init__`.
- Files: `server/main.py:200-202`, `server/usb_passthrough.py`
- Blocks: Remote Wacom/USB HID devices cannot be attached to a macOS server. Clients on Mac can still *enumerate* their own devices but not attach (README line 277 notes VirtualHere workaround for Mac-as-client).
- Priority: Medium — Mac server is primarily for dev/testing rather than Flame.

**No PAM / per-user sessions on macOS:**
- Problem: `server/main.py:1070-1073` force-downgrades `--auth-mode pam` to `--auth-mode none` on Darwin. SCK captures the logged-in GUI user's screen — there is no concept of multiple concurrent user sessions.
- Files: `server/main.py:1070-1073`, `server/session_manager.py` (Linux-only)
- Blocks: A macOS Teraguchi host can only ever serve one user's desktop at a time, regardless of how many clients connect.
- Priority: High if multi-user macOS servers are ever wanted; low otherwise (it's intentional).

**Local cursor rendering absent on macOS:**
- Problem: The XFixes-based cursor shape poller (`server/cursor_tracker.py`) has no macOS analogue. Cursor pixels are baked into the captured frame via SCK `showsCursor=True`.
- Files: `server/main.py:180-194` (`if not IS_MACOS: self.cursor_tracker = CursorTracker(...)`), `server/mac_screen_capture.py:34-39`
- Blocks: Mac clients of a Mac server get VNC-style cursor (visible but laggy). Mac clients of a Linux server still get local-cursor rendering.
- Priority: Medium — cosmetic; affects perceived latency.

**Pen tablet: no IOHIDUserDevice helper, pressure/tilt dropped:**
- Problem: See "Known Bugs" above. `mac_input_injector.pen_event` downgrades pen input to mouse clicks.
- Files: `server/mac_input_injector.py:355-398`
- Blocks: Mac server cannot deliver real tablet events to Flame/Photoshop/etc.
- Priority: High if macOS is ever a Flame host; low otherwise.

**Audio capture on macOS is silently disabled:**
- Problem: `SessionRuntime.__init__` gates audio on `check_audio_available()`, which runs `pactl` — always absent on macOS. No `mac_audio_capture.py` exists despite `pyobjc-framework-AVFoundation` / `CoreAudio` being installed by `install-server-macos.sh`.
- Files: `server/main.py:170-173`, `server/audio_capture.py` (pure PulseAudio), `install-server-macos.sh:147-148` (installs AVFoundation + CoreAudio but nothing consumes them yet)
- Blocks: Mac server sends video but no audio. Clients see audio as unsupported in the ServerHello (`supports_audio=False`).
- Priority: Medium. Fix by adding `server/mac_audio_capture.py` using `AVAudioEngine` tap on the default output device.

**Clipboard image/file support absent on both platforms:**
- Problem: Linux `clipboard.py` and macOS `mac_clipboard.py` both only handle `text/plain` (`NSPasteboardTypeString` / xclip `-selection clipboard`).
- Files: `server/clipboard.py`, `server/mac_clipboard.py:71-79`
- Blocks: Copy/paste of images, rich text, or file references across the tunnel.
- Priority: Low — text covers the common case.

**`check_system_dependencies` had Linux-only checks leaking through (recently fixed):**
- Problem: `pactl`, `xclip`/`xsel`, and `/dev/uinput` were being probed on Darwin until commit `08fd492` (`Gate Linux-only sysdep checks on macOS`). Strong hint that other Linux assumptions exist.
- Files: `server/main.py:980-1000` (fixed)
- Blocks: False-positive warnings in Mac logs.
- Priority: Done, but audit more of `server/main.py` for implicit Linux assumptions (see next).

**Likely remaining Linux-assumptions on macOS:**
- `server/main.py:907-910` reads `/proc/uptime` for the `/status` endpoint — wrapped in a try/except so it degrades to `uptime_s=0` on Mac, but that's a hidden failure.
- `server/auth.py:95-104` imports `pwd` and calls `pwd.getpwnam` for `get_user_info` — works on macOS too but the concept (PAM uid/gid/home → Xvfb display) is Linux-only.
- `server/main.py:341-354` uses `detect_hotplug` which is implemented via xrandr on Linux; not clear what it does on macOS where SCK tracks displays.
- Priority: Medium — audit and isolate each one to a `platform_backends.py`-like facade.

## Incomplete Features

**Broker has no session release / disconnect tracking:**
- Problem: `MachinePool.release` exists but nothing calls it. `active_sessions` is populated by probing `/status` — there is no broker-side record of which client is where once assignment completes.
- Files: `broker/pool.py:265-270` (`release` is defined, never invoked)
- Blocks: Broker cannot enforce a session limit; stale session entries persist until the next probe cycle.

**Admin UI has no assignment live-reload notification back to broker clients:**
- Problem: `AdminServer._handle_set_user` calls `pool.update_assignments` in-process, so running broker sessions see the change, but already-connected clients aren't notified.
- Files: `broker/admin.py:182-203`
- Blocks: User reassigned to a new machine while connected keeps their current session.

**Resize disabled entirely:**
- Problem: See "Known Bugs" above — `RESIZE_REQUEST` is a no-op on the server.
- Files: `server/main.py:548-552`
- Blocks: Mismatched client window ↔ server display geometry.

**AV1 encoding advertised but not preferred:**
- Problem: `VideoCodec.AV1` is wired through, but `ENCODER_DEFS` gives SVT-AV1 priority 100 (software) with no hardware fallback for AMD/Intel (only `av1_nvenc` and `av1_videotoolbox`/`av1_vaapi`). Real-world perf is untested.
- Files: `server/video_encoder.py:49-69` (`ENCODER_DEFS`)
- Blocks: Users selecting AV1 on non-NVIDIA hardware get slow software encoding.

**No signed / notarized macOS app bundle:**
- Problem: `Teraguchi.app/Contents/Info.plist` (committed copy) has `CFBundleVersion 2.0.0` and `CFBundleIdentifier com.teraguchi.client` but no code-signing, no entitlements, no notarization, no Hardened Runtime. The committed `Teraguchi.app/Contents/MacOS/Teraguchi` is a hard-coded shell script pointing at `/Users/randymcentee/workspace/GitHub/teraguchi` (absolute path, dev-only).
- Files: `Teraguchi.app/Contents/Info.plist:1-21`, `Teraguchi.app/Contents/MacOS/Teraguchi:1-6`
- Blocks: Distributing the Mac client to other users requires them to right-click → Open (Gatekeeper bypass). No TCC prompt attribution will point to "Teraguchi" — they point to the Python interpreter.
- Priority: High before any external distribution.

**Client launcher hard-codes dev path:**
- Problem: `Teraguchi.app/Contents/MacOS/Teraguchi` sets `SCRIPT_DIR="/Users/randymcentee/workspace/GitHub/teraguchi"` — only works on Randy's laptop. `install-client.sh:142-154` regenerates it at install time, but the committed file is stale.
- Files: `Teraguchi.app/Contents/MacOS/Teraguchi:2`
- Blocks: A fresh clone of the repo has a broken `Teraguchi.app` until someone runs `install-client.sh`.
- Priority: Low — remove the committed artifact or replace with a relocatable launcher.

## Scaling Limits

**Broker is single-process, in-memory state:**
- Current capacity: 6 machines, tens of users.
- Limit: No HA — if the broker restarts, all issued tokens lose validity (`broker.secret` is the only persistence mechanism), `_group_cache` and `_auth_cache` are wiped, `pool.machines` is rebuilt from config. OK for small team, breaks at org scale.
- Scaling path: Persist signing secret, move auth cache to Redis, replicate assignments between two broker instances with shared storage.

**Server `send_queue` `maxsize=30` caps client bandwidth burst:**
- Current capacity: 30 frames of backlog per client = 1 s at 30 fps.
- Limit: Slow clients exhaust the queue, frames drop, IDR requested only on explicit `request_full_frame`.
- Scaling path: See "Client-side `maxsize=30` send queue drops frames" under Performance.

**JPEG dirty-regions loop has O(h*w / block²) blocks per frame in Python:**
- Current capacity: Fine at 1080p.
- Limit: At 4K, dirty-region detection in Python is the bottleneck (Linux and Mac both).
- Scaling path: Offload to a C extension or use ScreenCaptureKit's built-in damage tracking.

## Dependencies at Risk

**`mss` — framebuffer capture fallback:**
- Risk: Project has a small maintainer base; no NVIDIA NvFBC-equivalent perf.
- Impact: Acceptable as fallback; NvFBC is the preferred path on NVIDIA.
- Migration plan: Replace `mss` with direct XShm + XDamage via `python-xlib`.

**`python-pam`:**
- Risk: Last release ~2021; relies on Linux-PAM ABI.
- Impact: Would break if PAM API changes, but PAM is stable.
- Migration plan: `pam-python` or direct ctypes wrap.

**`python-ldap`:**
- Risk: Hard to install on macOS / some Linux distros without libsasl dev headers. Conditionally imported in `broker/freeipa.py`.
- Impact: Without it, broker falls back to `id -Gn`, requires FreeIPA-joined host.
- Migration plan: Swap to `ldap3` (pure-Python) for portability.

**`aioquic`:**
- Risk: QUIC transport is experimental in this codebase (`common/quic_transport.py`). aioquic is actively maintained but Python QUIC ecosystem is thin.
- Impact: QUIC is optional — WebSocket + UDP is the main path.
- Migration plan: Gate behind a feature flag; document as experimental in README.

**`PyAV` / FFmpeg ABI coupling:**
- Risk: PyAV pins against specific FFmpeg versions. Brew's `ffmpeg` vs a bundled FFmpeg can drift.
- Impact: `client/video_decoder.py` may fail to import frames or hardware-accel silently falls back.
- Migration plan: Pin PyAV to a known-compatible FFmpeg; ship FFmpeg inside PyInstaller bundle.

## Test Coverage Gaps

**No test suite committed at all:**
- What's not tested: Everything. `requirements-dev.txt` declares `pytest>=7.0` but there is no `tests/` directory.
- Files: (absent)
- Risk: All refactors are blind. Changes to protocol (`common/messages.py`), encoder (`server/video_encoder.py`), session lifecycle (`server/session_manager.py`), auth (`server/auth.py`, `broker/tokens.py`) land without regression safety.
- Priority: High.

**Protocol message serialization:**
- What's not tested: `common/messages.py` binary header encode/decode, JSON roundtripping of `AuthRequest`/`AuthResult`/`ServerHelloMsg`, message size limits.
- Files: `common/messages.py`
- Risk: Wire format drift between client and server goes undetected.
- Priority: High. Start here — low-hanging fruit for unit tests.

**Broker token generation/verification:**
- What's not tested: `broker/tokens.py:generate_token` / `verify_token` round-trip, TTL expiration, signature mismatch, malformed tokens.
- Files: `broker/tokens.py`, `server/auth.py:179-212`
- Risk: Token auth is a single function; a bug here is an auth bypass.
- Priority: High.

**Bookmark encryption round-trip:**
- What's not tested: `client/bookmarks.py::_encrypt_password` / `_decrypt_password` on Mac/Win/Linux, bookmark file migration.
- Files: `client/bookmarks.py`
- Risk: Changing the key derivation would silently wipe users' saved passwords.
- Priority: Medium.

**macOS backends:**
- What's not tested: `server/mac_screen_capture.py`, `server/mac_input_injector.py`, `server/mac_clipboard.py` — freshly added in commits `4c8c73b`, `2abf179`, `9616037`.
- Files: all three.
- Risk: PyObjC API changes or TCC permission regressions go unnoticed.
- Priority: High — these are new code.

**Platform-parity contract tests:**
- What's not tested: No tests assert that `ScreenCapture` / `InputInjector` / `ClipboardSync` have the same public interface on Linux and macOS.
- Files: `server/platform_backends.py`
- Risk: The Mac stubs silently drift from the Linux classes, leading to `AttributeError` at session start.
- Priority: Medium. Easy to add — a per-class signature comparison test.

**Session lifecycle:**
- What's not tested: Per-user Xvfb/Xorg spawn in `server/session_manager.py`, cleanup on disconnect, concurrent session isolation.
- Files: `server/session_manager.py` (1,317 lines, zero tests).
- Risk: Session leaks on restart, orphaned Xvfb processes.
- Priority: High. Needs integration test harness (containerized Xvfb).

---

*Concerns audit: 2026-04-18*
