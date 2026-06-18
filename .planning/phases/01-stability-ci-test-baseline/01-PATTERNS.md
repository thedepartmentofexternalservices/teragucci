# Phase 1: Stability + CI + Test Baseline — Pattern Map

**Mapped:** 2026-04-18
**Files analyzed:** 38 (9 source modifications + 17 net-new source modules + 8 net-new test scaffolds + 4 net-new CI/infra files)
**Analogs found:** 34 / 38 (4 net-new with no direct analog — FSM, diagnostic bundle, errors hierarchy, CI workflows)

Absolute paths are used throughout because the planner runs from a fresh cwd.

---

## File Classification

### Source modifications (existing files)

| File | Role | Data Flow | Closest Analog | Match Quality |
|------|------|-----------|----------------|---------------|
| `server/main.py` (queue fix at :639, then split) | controller (asyncio entrypoint) | request-response + event-driven | self (characterization) | in-place |
| `client/main.py` (decomposition) | controller (Qt entrypoint) | event-driven | self (characterization) | in-place |
| `client/protocol.py` (strip `CERT_NONE` at :295-296, :364-365) | service (transport) | streaming | same method, post-patch | exact |
| `common/quic_transport.py` (strip `CERT_NONE` at :413-414) | service (transport) | streaming | `client/protocol.py` post-patch | exact |
| `broker/pool.py` (strip `ssl=False` at :152-153) | service (health probe) | request-response | `client/protocol.py` post-patch | role-match |
| `common/messages.py` (extend `HealthPing`/`HealthPong` at :298-318) | model (dataclass) | CRUD (serialize) | existing `HealthPing`/`HealthPong` lines 298-318 | exact |
| `server/health.py` (extend `HealthMonitor` deques) | service (telemetry) | event-driven | self: existing `_encode_times`/`_capture_times`/`_input_latencies` at :43-45 | exact |
| `pyproject.toml` (add tool sections) | config | N/A | self: existing `[project]` + `[project.scripts]` | exact |
| `requirements-dev.txt` (add deps) | config | N/A | self: existing `-r requirements-server.txt` layering | exact |

### Net-new source modules

| File | Role | Data Flow | Closest Analog | Match Quality |
|------|------|-----------|----------------|---------------|
| `common/logging.py` | utility | transform (processor chain) | `common/messages.py` module header + `server/health.py` class shape | role-match |
| `common/session_fsm.py` | model (state machine) | event-driven | `common/hybrid_transport.py` `TransportState` @dataclass + `TransportMsg` constants namespace | role-match (no existing FSM) |
| `common/errors.py` | model (exception hierarchy) | N/A | `server/mac_screen_capture.py` `MacScreenCaptureError(RuntimeError)` pattern | role-match |
| `common/diagnostic_bundle.py` | utility (file I/O) | file-I/O | `client/bookmarks.py` (JSON + path helpers + platform dirs) + `server/video_encoder.py` subprocess (`ffmpeg -version`) | partial |
| `server/stream_loop.py` | service (async loop) | streaming | `server/main.py::_stream_h264` / `_stream_jpeg` at :284-323 | exact (extraction) |
| `server/health_loop.py` | service (async loop) | request-response | `server/main.py::_health_ping_loop` at :325-339 | exact (extraction) |
| `server/monitor_hotplug.py` | service (async loop) | event-driven | `server/main.py::_monitor_hotplug_loop` at :341-354 | exact (extraction) |
| `server/encoder_lifecycle.py` | service (subprocess mgmt) | event-driven | `server/main.py::_restart_encoder` at :506-516 + `server/video_encoder.py` | exact (extraction) |
| `server/session_runtime.py` | controller (session mgr) | event-driven | `server/main.py::SessionRuntime` at :78-617 | exact (extraction) |
| `server/client_session.py` | controller (per-client state) | request-response | `server/main.py::ClientSession` at :619-666 | exact (extraction) |
| `server/__main__.py` | entrypoint | N/A | `broker/main.py::main` at :270-373 | role-match |
| `client/app.py` | controller (Qt bootstrap) | N/A | `client/main.py::main` at :1138-1185 | exact (extraction) |
| `client/main_window.py` | component (Qt) | event-driven | `client/main.py::MainWindow` at :549-1131 | exact (extraction) |
| `client/tab_manager.py` | component (Qt) | event-driven | `client/main.py::MainWindow._tabs` wiring at :564-573 | role-match |
| `client/session_view.py` | component (Qt) | event-driven | `client/fullscreen_toolbar.py` (self-contained `QWidget` module) | role-match |
| `client/connection_supervisor.py` | service (reconnect policy) | event-driven | `client/protocol.py::_run_loop` reconnect block at :224-253 | role-match (extraction + FSM drive) |
| `client/__main__.py` | entrypoint | N/A | `server/__main__.py` (new) / `broker/main.py::if __name__` at :373-374 | role-match |

### Net-new test files (no analog — zero tests exist today)

| File | Role | Data Flow | Closest Analog | Match Quality |
|------|------|-----------|----------------|---------------|
| `tests/conftest.py` | test (fixtures) | N/A | None (net-new) | no analog — use pytest docs |
| `tests/common/test_messages.py` | test (unit) | CRUD | `common/messages.py` dataclass/JSON round-trip shape | target-read |
| `tests/common/test_keymap.py` | test (unit, table-driven) | transform | `common/keymap.py::qt_key_to_linux_scancode` | target-read |
| `tests/common/test_jitter_buffer.py` | test (unit) | event-driven | `common/jitter_buffer.py::JitterBuffer` | target-read |
| `tests/common/test_hybrid_transport.py` | test (unit) | event-driven | `common/hybrid_transport.py::TransportState` | target-read |
| `tests/common/test_session_fsm.py` | test (unit) | event-driven | `common/session_fsm.py` (new) | target-read |
| `tests/common/test_logging.py` | test (unit) | transform | `common/logging.py` (new) | target-read |
| `tests/server/test_auth.py` | test (unit) | CRUD | `server/auth.py::Authenticator` | target-read |
| `tests/server/test_pam_auth.py` | test (unit, mock) | CRUD | `server/pam_auth.py` | target-read |
| `tests/server/test_pipelines.py` | test (unit) | streaming | `server/main.py::ClientSession.send_queue` (post-fix) + `SessionRuntime._stream_*` | target-read |
| `tests/server/test_health_monitor.py` | test (unit) | event-driven | `server/health.py::HealthMonitor` | target-read |
| `tests/server/test_video_encoder_mock.py` | test (unit, subprocess mock) | subprocess | `server/video_encoder.py::VideoEncoder` | target-read |
| `tests/broker/test_tokens.py` | test (unit) | CRUD (HMAC) | `broker/tokens.py::generate_token`/`verify_token` | target-read |
| `tests/client/test_bookmarks.py` | test (unit) | CRUD | `client/bookmarks.py` | target-read |
| `tests/client/test_connection_supervisor.py` | test (unit) | event-driven | `client/connection_supervisor.py` (new) | target-read |
| `tests/integration/conftest.py` | test (fixture, loopback) | event-driven | None (net-new) | no analog |
| `tests/integration/test_auth_flow.py` | test (integration, loopback) | request-response | `server/auth.py` + `client/protocol.py::_handle_auth` | target-read |
| `tests/integration/test_tls_verify.py` | test (integration, TLS CA) | request-response | `server/main.py::create_tls_context` at :1003-1007 | target-read |
| `tests/integration/test_fsm_state_sync.py` | test (integration) | event-driven | `common/session_fsm.py` + `HealthPing`/`HealthPong` | target-read |
| `tests/integration/test_reconnect.py` | test (integration) | event-driven | `client/connection_supervisor.py` (new) | target-read |
| `tests/integration/test_diag_bundle.py` | test (integration) | file-I/O | `common/diagnostic_bundle.py` (new) | target-read |
| `tests/integration/test_latency_breakdown.py` | test (integration) | streaming | `server/health.py` extended deques | target-read |
| `tests/integration/test_server_bootstrap.py` | test (integration, characterization) | event-driven | `server/main.py::main` + `handle_client` at :685 | target-read |
| `tests/smoke/test_latency_benchmark.py` | test (smoke) | streaming | `server/health.py` + smoke harness | target-read |
| `tests/smoke/test_synthetic_1h.py` | test (smoke, 1h) | streaming | Full loopback fixture | target-read |

### Net-new CI/infra files (no analog — zero workflows today)

| File | Role | Data Flow | Closest Analog | Match Quality |
|------|------|-----------|----------------|---------------|
| `.github/workflows/ci.yml` | CI workflow | N/A | None — net-new; planner anchors to RESEARCH.md §"Code Examples" + `pyproject.toml` tool sections | no analog |
| `.github/workflows/build-artifacts.yml` | CI workflow | N/A | `build_client.py` (wrap in GHA) | role-match |
| `.github/workflows/smoke-nightly.yml` | CI workflow (cron) | N/A | None — net-new | no analog |
| `packaging/teraguchi-server.spec` | packaging (RPM) | N/A | `install-server.sh` dep list + `server/teraguchi-server.service` | role-match |

---

## Pattern Assignments

### `server/main.py` queue fix (STAB-04, at `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:639`)

**Analog:** self (in-place 5-line patch) — `ClientSession.send_queue` and `enqueue` at :639, :657-662.

**Existing construct to preserve**: `asyncio.Queue` + `put_nowait` + `except asyncio.QueueFull` is the idiomatic queue pattern. The fix changes `maxsize=30` to `maxsize=4` and adds IDR-on-drop.

**Before (lines 639, 657-662):**
```python
self.send_queue: asyncio.Queue = asyncio.Queue(maxsize=30)
...
async def enqueue(self, data):
    try:
        self.send_queue.put_nowait(data)
        return True
    except asyncio.QueueFull:
        return False
```

**After pattern (planner writes):** `maxsize=4`, track consecutive drops on the session, on first drop per streak call `runtime.encoder.request_keyframe()` (existing method called at :233 and :543), and emit structlog `broadcaster.drop` / `broadcaster.idr_requested` events. Preserve the `asyncio.QueueFull` exception type.

**Keyframe-request call site already in repo (copy shape):**
```python
# server/main.py:233 — existing pattern for requesting a keyframe on reconnect
if self.encoder:
    self.capture.invalidate()
    self.encoder.request_keyframe()
```

---

### `client/protocol.py` + `common/quic_transport.py` + `broker/pool.py` — SEC-01 `CERT_NONE` removal

**Analog (positive pattern):** `server/main.py::create_tls_context` at `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:1003-1007` — server-side TLS context the right way, cert-chain loaded, TLS floor set.

**Existing server-side pattern (copy the "context with proper verification" shape):**
```python
# server/main.py:1003-1007
def create_tls_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx
```

**Sites to remove (all four):**

1. `/Users/randymcentee/workspace/GitHub/teraguchi/client/protocol.py:291-296` (first WSS endpoint):
   ```python
   ssl_context = None
   if self._use_tls:
       import ssl
       ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
       ssl_context.check_hostname = False
       ssl_context.verify_mode = ssl.CERT_NONE
   ```
2. `/Users/randymcentee/workspace/GitHub/teraguchi/client/protocol.py:360-365` (broker handshake WSS) — same shape.
3. `/Users/randymcentee/workspace/GitHub/teraguchi/common/quic_transport.py:407-414`:
   ```python
   config = QuicConfiguration(is_client=True, max_datagram_frame_size=65536)
   config.alpn_protocols = ["teraguchi"]
   if not verify_cert:
       config.verify_mode = ssl.CERT_NONE
   ```
4. `/Users/randymcentee/workspace/GitHub/teraguchi/broker/pool.py:150-154`:
   ```python
   url = f"https://{machine.host}:{machine.port}/status"
   async with aiohttp.ClientSession(
       connector=aiohttp.TCPConnector(ssl=False)
   ) as session:
   ```

**After pattern (planner writes — same client-side construction, verification on by default):**
```python
ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
if ca_bundle:
    ssl_context.load_verify_locations(cafile=ca_bundle)
# Opt-in dev escape hatch per RESEARCH Open Question #3:
if os.environ.get("TERAGUCHI_ACCEPT_INSECURE") == "1" and args.insecure_skip_verify:
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    logger.error("transport.insecure_mode_active", ...)   # structlog ERROR on every use
```

For `broker/pool.py`, swap to `aiohttp.TCPConnector(ssl=ssl_context)` using the same builder.

---

### `common/messages.py` HealthPing/HealthPong extension (STAB-06, at `/Users/randymcentee/workspace/GitHub/teraguchi/common/messages.py:298-318`)

**Analog:** self — the existing dataclasses.

**Imports pattern (preserve):**
```python
# common/messages.py:29-36
import json, struct, time, hashlib, secrets
from enum import IntEnum
from dataclasses import dataclass, asdict, field
from typing import Optional, List
```

**Core pattern to extend (lines 298-318):**
```python
@dataclass
class HealthPing:
    type: str = MsgType.HEALTH_PING
    timestamp_ms: int = 0
    sequence: int = 0
    # ADD:
    client_state: str = ""     # STAB-06 — one of the 8 client FSM states

    def to_json(self) -> str:
        self.timestamp_ms = int(time.time() * 1000)
        return json.dumps(asdict(self))


@dataclass
class HealthPong:
    type: str = MsgType.HEALTH_PONG
    ping_timestamp_ms: int = 0
    sequence: int = 0
    server_timestamp_ms: int = 0
    # ADD:
    server_state: str = ""     # STAB-06 — one of the 7 server FSM states

    def to_json(self) -> str:
        self.server_timestamp_ms = int(time.time() * 1000)
        return json.dumps(asdict(self))
```

**Also add (per RESEARCH §"Error taxonomy"):** `MsgType.ERROR = "error"` in the `MsgType` class around line 150, and new `@dataclass ProtocolErrorMsg` following the `AuthResult` dataclass shape at :273-281.

---

### `server/health.py` extension (OBS-02, OBS-03)

**Analog:** self — `HealthMonitor` at `/Users/randymcentee/workspace/GitHub/teraguchi/server/health.py:23-145`.

**Existing deque pattern (copy for new stages):**
```python
# server/health.py:43-45 — existing per-stage timing deques
self._encode_times: collections.deque = collections.deque(maxlen=120)
self._capture_times: collections.deque = collections.deque(maxlen=120)
self._input_latencies: collections.deque = collections.deque(maxlen=120)
```

**Add parallel deques for OBS-02:**
```python
self._transmit_times: collections.deque = collections.deque(maxlen=120)
self._decode_times: collections.deque = collections.deque(maxlen=120)    # client-reported via HealthPong
self._display_times: collections.deque = collections.deque(maxlen=120)   # client-reported
```

**Existing recorder pattern (copy for each new stage):**
```python
# server/health.py:87-94
def record_encode_time(self, ms: float):
    self._encode_times.append(ms)

def record_capture_time(self, ms: float):
    self._capture_times.append(ms)

def record_input_latency(self, ms: float):
    self._input_latencies.append(ms)
```

**Existing `get_stats()` pattern (copy — `HealthStats` dataclass extension mirrors):**
```python
# server/health.py:129-145
def get_stats(self) -> HealthStats:
    return HealthStats(
        rtt_ms=round(self.avg_rtt_ms, 1),
        ...
        encode_time_ms=round(self._avg_deque(self._encode_times), 1),
        capture_time_ms=round(self._avg_deque(self._capture_times), 1),
        input_latency_ms=round(self._avg_deque(self._input_latencies), 1),
        ...
    )
```

**OBS-03 keyframe counters — add as plain int counters, not deques:**
```python
self.keyframe_requested = 0
self.keyframe_emitted = 0

def record_keyframe_requested(self):
    self.keyframe_requested += 1

def record_keyframe_emitted(self):
    self.keyframe_emitted += 1
```

Mirror `HealthStats` fields in `common/messages.py:322-340` — add `transmit_time_ms`, `decode_time_ms`, `display_time_ms`, `keyframe_requested`, `keyframe_emitted`.

---

### `common/logging.py` (NEW — OBS-01)

**Role:** utility — structlog processor chain + context binding.

**Closest analog for module shape:** `/Users/randymcentee/workspace/GitHub/teraguchi/common/messages.py:1-40` (module-level docstring + import block + top-level constants).

**Analog excerpt — module header style to match:**
```python
# common/messages.py:1-36 — copy docstring-first + stdlib/third-party/firstparty import ordering
"""
Teraguchi Protocol Message Definitions - v3
...
"""

import json
import struct
import time
...
from dataclasses import dataclass, asdict, field
from typing import Optional, List
```

**Existing logging pattern it replaces (logger-at-top module convention, preserve):**
```python
# every module today, e.g. server/health.py:12-13, client/bookmarks.py:20-21
import logging
logger = logging.getLogger(__name__)
```

**Planner writes the structlog configure function per RESEARCH §"structlog schema":**
```python
def configure(tier: str) -> None:
    """Configure structlog for a given tier ('server' | 'client' | 'broker').

    Required fields on every event: ts, level, tier, event.
    Binds `tier` into contextvars so every log carries it.
    """
    import structlog
    from structlog.contextvars import bind_contextvars, merge_contextvars

    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            _redact_secrets,                    # strips password/token/secret keys
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    bind_contextvars(tier=tier)
```

**Entry-point hook (copy the `logging.basicConfig` call sites that `common/logging.configure()` replaces):**
- `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:1059-1062`
- `/Users/randymcentee/workspace/GitHub/teraguchi/client/main.py:1149-1151`
- `/Users/randymcentee/workspace/GitHub/teraguchi/broker/main.py:301-304`

---

### `common/session_fsm.py` (NEW — STAB-06 / D-13)

**Role:** model (state machine).

**No existing FSM in repo.** The closest analog is `common/hybrid_transport.py::TransportState` + `TransportMsg` namespace class — an implicit state record with an enum of events. Use its shape for the event-name namespace.

**Analog — event-name namespace (copy the "bare class with `UPPER_SNAKE_CASE` string constants" pattern):**
```python
# common/hybrid_transport.py:42-54
class TransportMsg:
    UDP_ANNOUNCE = "udp_announce"
    UDP_PROBE = "udp_probe"
    UDP_CONFIRMED = "udp_confirmed"
    UDP_ACTIVE = "udp_active"
    UDP_FALLBACK = "udp_fallback"
    UDP_STATS = "udp_stats"
```

**Analog — state-as-dataclass pattern (copy):**
```python
# common/hybrid_transport.py:57-76
@dataclass
class TransportState:
    mode: TransportMode = TransportMode.TCP_ONLY
    udp_confirmed: bool = False
    ...

    @property
    def using_udp(self) -> bool:
        return self.mode == TransportMode.UDP_MEDIA and self.udp_confirmed
```

**Planner writes** (per RESEARCH §"FSM state set — client (final)" + §"FSM state set — server (final)"):

```python
# common/session_fsm.py
"""Client + server FSMs for Teraguchi session lifecycle.

Uses python-statemachine (D-13). States are serialized to strings for
HealthPing.client_state / HealthPong.server_state.
"""
from statemachine import StateMachine, State

CLIENT_STATES = ("disconnected", "handshaking", "authenticating",
                 "capability_exchange", "streaming", "degraded",
                 "reconnecting", "closed")
SERVER_STATES = ("bootstrapping", "authenticating", "capability_exchange",
                 "streaming", "reconfiguring", "draining", "closed")

# Allowed (client, server) pairs for health.state_disagreement detection
ALLOWED_PAIRS: set[tuple[str, str]] = {
    ("handshaking",        "bootstrapping"),
    ("authenticating",     "authenticating"),
    ("capability_exchange","capability_exchange"),
    ("streaming",          "streaming"),
    ("streaming",          "reconfiguring"),
    ("degraded",           "streaming"),
    ("reconnecting",       "draining"),
    ("reconnecting",       "closed"),
    ("closed",             "closed"),
}

class ClientFSM(StateMachine):
    disconnected = State(initial=True)
    handshaking = State()
    authenticating = State()
    capability_exchange = State()
    streaming = State()
    degraded = State()
    reconnecting = State()
    closed = State(final=True)

    connect_requested = disconnected.to(handshaking) | reconnecting.to(handshaking)
    tls_ok = handshaking.to(authenticating)
    auth_ok = authenticating.to(capability_exchange)
    hello_received = capability_exchange.to(streaming)
    health_degraded = streaming.to(degraded)
    health_restored = degraded.to(streaming)
    transport_lost = (
        handshaking.to(reconnecting) | streaming.to(reconnecting)
        | degraded.to(reconnecting) | authenticating.to(reconnecting)
    )
    max_retries = reconnecting.to(closed)
    user_quit = (disconnected.to(closed) | handshaking.to(closed)
                 | streaming.to(closed) | reconnecting.to(closed))

class ServerFSM(StateMachine):
    bootstrapping = State(initial=True)
    authenticating = State()
    capability_exchange = State()
    streaming = State()
    reconfiguring = State()
    draining = State()
    closed = State(final=True)
    # transitions per RESEARCH table
```

---

### `common/errors.py` (NEW)

**Role:** model (exception hierarchy).

**Closest analog:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/mac_screen_capture.py:115` (`MacScreenCaptureError(RuntimeError)`) — the only custom-exception class in the repo.

**Analog excerpt:**
```python
# server/mac_screen_capture.py:115 (and server/mac_input_injector.py:94)
class MacScreenCaptureError(RuntimeError):
    pass
```

**Break from existing pattern:** existing custom errors inherit from `RuntimeError`. The new hierarchy inherits from `Exception` so that catches remain narrow and each class has a stable `code` class attribute (per RESEARCH §"Error taxonomy"). This is intentional — `TeraguchiError` adds metadata (`code`) that `RuntimeError` doesn't carry.

**Concrete content:** already enumerated in RESEARCH.md lines 978-1024. Planner pastes that hierarchy verbatim.

---

### `common/diagnostic_bundle.py` (NEW — OBS-05)

**Role:** utility (file I/O + redaction).

**Closest analog for path/platform handling:** `/Users/randymcentee/workspace/GitHub/teraguchi/client/bookmarks.py:24-34` (`_get_config_dir()`) — cross-platform Path resolution.

**Analog excerpt — platform-appropriate config dir (copy pattern; bundle output defaults similarly):**
```python
# client/bookmarks.py:24-34
def _get_config_dir() -> Path:
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "Teraguchi"
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home())) / "Teraguchi"
    else:
        base = Path.home() / ".config" / "teraguchi"
    base.mkdir(parents=True, exist_ok=True)
    return base
```

**Analog for subprocess probes (`ffmpeg -version`, `nvidia-smi`, `uname`):** `/Users/randymcentee/workspace/GitHub/teraguchi/server/video_encoder.py` and `/Users/randymcentee/workspace/GitHub/teraguchi/server/screen_capture.py` already call `subprocess.run(..., capture_output=True, text=True, timeout=5)`. Copy that shape.

**Analog for JSON write (copy):**
```python
# client/bookmarks.py:119-125
def _save(self):
    try:
        data = {bid: p.to_dict() for bid, p in self._profiles.items()}
        with open(self._bookmarks_file, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Failed to save bookmarks: %s", e)
```

Zip layout + redaction rules are exhaustively specified in RESEARCH.md lines 932-973 — planner pastes that table verbatim into the module docstring.

---

### `server/stream_loop.py` (NEW — D-11 extraction)

**Role:** service (async loop).

**Analog:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:284-323` — extract `_stream_h264` + `_stream_jpeg` verbatim, inject `runtime: SessionRuntime` as a constructor arg, preserve `self._running` / `self.health.record_*` call sites.

**Exact source to extract (copy, don't rewrite):**
```python
# server/main.py:284-297 — h264 capture loop
async def _stream_h264(self, fps: int):
    interval = 1.0 / fps
    while self._running:
        start = time.time()
        if self.clients and self.encoder:
            try:
                t0 = time.time()
                raw = self.capture.capture_raw_bgra()
                self.health.record_capture_time((time.time() - t0) * 1000)
                self.encoder.feed_frame(raw)
            except Exception as e:
                logger.error("[%s] H264 capture error: %s", self.username, e)
        elapsed = time.time() - start
        await asyncio.sleep(max(interval - elapsed, 0.001))
```

**Characterization test target:** before extraction, `tests/integration/test_server_bootstrap.py` must run the loop with mocked capture/encoder and assert exact frame-per-second behavior matches pre-extraction.

---

### `server/health_loop.py` (NEW — D-11 extraction)

**Role:** service (async loop).

**Analog:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:325-339` — extract `_health_ping_loop` verbatim.

```python
# server/main.py:325-339 — health ping + stats broadcast
async def _health_ping_loop(self):
    while self._running:
        await asyncio.sleep(2.0)
        if not self.clients:
            continue
        seq = self.health.next_ping_sequence()
        ping_json = HealthPing(sequence=seq).to_json()
        stats_json = self.health.get_stats().to_json()
        for ws, cs in list(self.clients.items()):
            if cs.authenticated:
                try:
                    await cs.enqueue(ping_json)
                    await cs.enqueue(stats_json)
                except Exception:
                    pass
```

**Extension in the extracted module:** set `HealthPing.client_state = <current server FSM state>` (per STAB-06).

---

### `server/monitor_hotplug.py` (NEW — D-11 extraction)

**Role:** service (async loop, event-driven).

**Analog:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:341-354` — extract `_monitor_hotplug_loop` verbatim.

```python
# server/main.py:341-354
async def _monitor_hotplug_loop(self):
    while self._running:
        await asyncio.sleep(5.0)
        if self.capture and self.capture.detect_hotplug():
            monitors = [asdict(m) for m in self.capture.list_monitors()]
            msg_json = MonitorListMsg(monitors=monitors).to_json()
            for ws, cs in list(self.clients.items()):
                if cs.authenticated:
                    try:
                        await cs.enqueue(msg_json)
                    except Exception:
                        pass
            if self.encoder:
                self._restart_encoder()
```

---

### `server/encoder_lifecycle.py` (NEW — D-11 extraction)

**Role:** service (subprocess / restart management).

**Analog:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:506-516` (`_restart_encoder`) + `/Users/randymcentee/workspace/GitHub/teraguchi/server/video_encoder.py` (`VideoEncoder` + `JpegFallbackEncoder`).

```python
# server/main.py:506-516 — encoder restart primitive
def _restart_encoder(self):
    if self.encoder:
        old_available = self.encoder._available
        self.encoder.stop()
    else:
        old_available = None
    self.encoder = VideoEncoder(self.capture.width, self.capture.height,
                                 self.quality,
                                 available_encoders=old_available)
    self.encoder.start(self._on_encoded_frame)
    self.health.current_resolution = f"{self.capture.width}x{self.capture.height}"
```

---

### `server/session_runtime.py` (NEW — D-11 extraction)

**Role:** controller (per-user session lifecycle).

**Analog:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:78-617` — the entire `SessionRuntime` class, extracted wholesale.

**Key sub-patterns to preserve inside the extracted class:**

1. **`IS_MACOS` platform branch** (:110-137) — keep the try/finally that swaps `DISPLAY` env.
2. **Encoder vs JPEG fallback selection** (:146-162) — do not reorganize; the `codec in (...) and ffmpeg_caps.get(codec, False)` condition is correct.
3. **`threading.Lock` for clients dict** (:101-102, :220-223, :247-250) — preserve:
   ```python
   self.clients: Dict[WebSocketServerProtocol, "ClientSession"] = {}
   self._lock = threading.Lock()
   ...
   with self._lock:
       self.clients[ws] = session
       self.health.clients_connected = len(self.clients)
   ```
4. **Cross-thread enqueue via event loop** (:238-244, :374-376) — preserve `asyncio.run_coroutine_threadsafe(cs.enqueue(...), self._event_loop)` exactly.
5. **Task start pattern** (:266-271):
   ```python
   self._stream_task = asyncio.ensure_future(self._stream_h264(fps))
   self._health_task = asyncio.ensure_future(self._health_ping_loop())
   self._hotplug_task = asyncio.ensure_future(self._monitor_hotplug_loop())
   ```
   After extraction these become injected loop objects, not methods.

---

### `server/client_session.py` (NEW — D-11 extraction)

**Role:** controller (per-client WebSocket state).

**Analog:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:619-666` — the entire `ClientSession` class, extracted wholesale with the STAB-04 queue fix applied.

**Exact source to extract (apply `maxsize=4` change here):**
```python
# server/main.py:619-666
class ClientSession:
    """Tracks per-client connection state."""

    def __init__(self, ws: WebSocketServerProtocol):
        self.ws = ws
        self.client_id = str(id(ws))
        ...
        self.send_queue: asyncio.Queue = asyncio.Queue(maxsize=30)   # → maxsize=4
        self._send_task: Optional[asyncio.Task] = None

    def start_sender(self):
        self._send_task = asyncio.create_task(self._send_loop())

    async def _send_loop(self):
        try:
            while True:
                data = await self.send_queue.get()
                if data is None:
                    break
                await self.ws.send(data)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.debug("Send error: %s", e)

    async def enqueue(self, data):
        try:
            self.send_queue.put_nowait(data)
            return True
        except asyncio.QueueFull:
            return False      # ← extend: record drop + request IDR + structlog event
```

---

### `server/__main__.py` + `client/__main__.py` (NEW — thin entrypoints)

**Role:** entrypoint.

**Analog:** `/Users/randymcentee/workspace/GitHub/teraguchi/broker/main.py:373-374` — existing `if __name__ == "__main__":` pattern is already near-thin.

```python
# broker/main.py:373-374
if __name__ == "__main__":
    main()
```

**Planner writes (server/__main__.py):**
```python
"""Thin entrypoint: `python -m server` → server.main.main()."""
import sys
sys.path.insert(0, ".")              # preserves existing sys.path idiom; see server/main.py:44
from server.main import main
if __name__ == "__main__":
    main()
```

`pyproject.toml` console scripts (lines 24-26) continue to reference `server.main:main` — unchanged.

---

### `client/app.py` (NEW — Qt bootstrap)

**Role:** controller (Qt bootstrap).

**Analog:** `/Users/randymcentee/workspace/GitHub/teraguchi/client/main.py:1138-1185` — extract `main()` wholesale.

**Exact source to extract:**
```python
# client/main.py:1138-1185
def main():
    parser = argparse.ArgumentParser(description="Teraguchi Remote Desktop Client")
    parser.add_argument("--host", default="", help="Server hostname or IP")
    parser.add_argument("--port", type=int, default=443)
    ...
    logging.basicConfig(...)                              # → replace with common.logging.configure("client")

    if sys.platform == "darwin":
        QApplication.setAttribute(Qt.AA_MacDontSwapCtrlAndMeta, True)

    app = QApplication(sys.argv)
    app.setApplicationName("Teraguchi")
    app.setApplicationDisplayName("Teraguchi")
    app.setOrganizationName("Teraguchi")
    app.setDesktopFileName("teraguchi")
    app.setStyle("Fusion")
    ...
    app.setStyleSheet(theme.generate_stylesheet())

    window = MainWindow(initial_host=args.host, ...)
    window.resize(1440, 900)
    window.show()
    sys.exit(app.exec())
```

**Preserve:** the macOS `NSBundle.mainBundle()` mangling at :1166-1175 — do not drop during extraction.

---

### `client/main_window.py` (NEW — D-12 extraction)

**Role:** component (Qt QMainWindow).

**Analog:** `/Users/randymcentee/workspace/GitHub/teraguchi/client/main.py:549-1131` — the entire `MainWindow` class.

**Extraction-preservation checklist:**
- Constructor params (line 552-554) — keep as-is
- Dock widget pattern (:575-605) — keep as-is
- Fullscreen toolbar wiring (:622-628) — keep
- Tab wiring (:564-573) — extract into `client/tab_manager.py`
- `closeEvent` session cleanup at :1130 — keep

---

### `client/tab_manager.py` (NEW — D-12 extraction)

**Role:** component (QTabWidget glue).

**Analog:** the tab-lifecycle methods on `MainWindow`:
```python
# client/main.py:564-573 + methods _close_tab, _on_tab_changed, _make_new_tab_btn
self._tabs = QTabWidget()
self._tabs.setTabsClosable(True)
self._tabs.setMovable(True)
self._tabs.setDocumentMode(True)
self._tabs.tabCloseRequested.connect(self._close_tab)
self._tabs.currentChanged.connect(self._on_tab_changed)
self.setCentralWidget(self._tabs)
self._tabs.setCornerWidget(self._make_new_tab_btn(), Qt.TopRightCorner)
```

Planner extracts into a `TabManager(QObject)` that owns the `QTabWidget` and emits Qt signals for tab-add / tab-close. `MainWindow` instantiates it and calls `setCentralWidget(tab_manager.widget)`.

---

### `client/session_view.py` (NEW — D-12 new wrapper)

**Role:** component (per-tab widget).

**Analog for "self-contained QWidget in its own module":** `/Users/randymcentee/workspace/GitHub/teraguchi/client/fullscreen_toolbar.py:1-60`.

**Analog excerpt (copy module shape — docstring, imports, top-level constants, class with `Signal` declarations):**
```python
# client/fullscreen_toolbar.py:1-50
"""
Auto-hiding slide-down toolbar for fullscreen mode.
"""
import logging
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QPoint, QEasingCurve, Signal
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import QWidget, QHBoxLayout, ...

from client import theme

logger = logging.getLogger(__name__)

TOOLBAR_HEIGHT = 48

class FullscreenToolbar(QWidget):
    exit_fullscreen = Signal()
    disconnect_requested = Signal()
    ...
    def __init__(self, parent=None):
        super().__init__(parent)
        ...
```

Per RESEARCH §"Client Decomposition" (Open Question #4), `SessionView` **wraps** the existing `client/session.py::Session` — do not split `Session` itself.

---

### `client/connection_supervisor.py` (NEW — STAB-08)

**Role:** service (reconnect policy, FSM driver).

**Analog:** `/Users/randymcentee/workspace/GitHub/teraguchi/client/protocol.py:224-253` — existing reconnect loop in `_run_loop`.

**Existing reconnect pattern (copy + generalize):**
```python
# client/protocol.py:224-253
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
            if self.on_disconnected and not self._closing:
                self.on_disconnected("Connection lost")

            if not self._auto_reconnect or self._closing:
                break

            logger.info("Reconnecting in %.1fs...", self._reconnect_delay)
            time.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 1.5, self._max_reconnect_delay)
    finally:
        ...
```

**Preserve (these are STAB-08 primitives already in the codebase):**
- `self._reconnect_delay = 1.0` initial
- `self._max_reconnect_delay = 30.0` cap
- 1.5× backoff multiplier
- `self._auto_reconnect` opt-out flag

**Add (STAB-08 gaps vs existing code):**
- Jitter on each sleep: `time.sleep(self._reconnect_delay * (0.5 + random.random()))`
- Retry cap (e.g. `max_retries=10` → FSM `max_retries` event → state `closed`)
- Drive `ClientFSM` `transport_lost` / `connect_requested` / `max_retries` transitions (from `common/session_fsm.py`)
- Replace `time.sleep` with `await asyncio.sleep` (async supervisor, not thread-blocking)
- Emit structlog events: `supervisor.connect_attempt`, `supervisor.backoff`, `supervisor.max_retries`

---

### `pyproject.toml` extension

**Analog:** self (lines 1-30).

**Existing structure (preserve):**
```toml
# pyproject.toml:1-30
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "teraguchi"
version = "3.0.0"
requires-python = ">=3.10"          # bump to ">=3.12" per RESEARCH

[project.scripts]
teraguchi-server = "server.main:main"
teraguchi-client = "client.main:main"

[tool.setuptools.packages.find]
include = ["server*", "client*", "common*"]
```

**Add (planner writes):**
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
timeout = 30
filterwarnings = ["error", "ignore::DeprecationWarning:websockets"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "ASYNC"]

[tool.mypy]
python_version = "3.12"
strict_optional = true
files = ["common", "client/protocol.py"]
```

Also: add `"broker*"` to `include` list (currently excluded — PATTERNS note: broker tests cannot install from wheel today; confirm planner wants this).

---

### `requirements-dev.txt` extension

**Analog:** self (lines 1-6). Existing pattern (preserve — the `-r` layering idiom):
```
# requirements-dev.txt:1-6
# Development / testing requirements
-r requirements-server.txt
-r requirements-client.txt

pytest>=7.0
pyinstaller>=6.0
```

**Add (planner writes — pin ranges per RESEARCH §"Dependencies & Versions"):**
```
pytest>=8.3,<9
pytest-asyncio>=0.25,<0.26
pytest-timeout>=2.3
pytest-cov
pytest-xdist
ruff>=0.11,<0.12
mypy>=1.14,<1.15
structlog>=25.1,<26
python-statemachine>=2.6,<3
psutil>=6.0
freezegun
```

`structlog` and `python-statemachine` are **runtime** deps — also add them to `requirements-server.txt` and `requirements-client.txt` (not dev-only). Follow the existing per-role split in those files.

---

## Shared Patterns

### Module-level logger (every new module)

**Source:** ubiquitous pattern — e.g. `/Users/randymcentee/workspace/GitHub/teraguchi/server/health.py:12-13`, `/Users/randymcentee/workspace/GitHub/teraguchi/client/bookmarks.py:20-21`, `/Users/randymcentee/workspace/GitHub/teraguchi/common/jitter_buffer.py:14-16`.

**Apply to:** every net-new `.py` file.

```python
import logging
logger = logging.getLogger(__name__)
```

**Phase 1 transition:** once `common/logging.py` lands, modules that emit structured events switch to `structlog.get_logger(__name__)` — but the stdlib-`logging`-first bootstrap stays so `logging.basicConfig` output is not lost during Phase-1 migration.

---

### Module docstring (every new module)

**Source:** ubiquitous — every existing module opens with a triple-quoted summary. See `/Users/randymcentee/workspace/GitHub/teraguchi/server/auth.py:1-11`, `/Users/randymcentee/workspace/GitHub/teraguchi/client/bookmarks.py:1-6`.

**Apply to:** every net-new `.py` file.

**Template (match existing brevity):**
```python
"""
<one-line summary>.

<optional 2-3 line description of design decisions / cross-module role>
"""
```

---

### Dataclass for protocol / config structs

**Source:** `/Users/randymcentee/workspace/GitHub/teraguchi/common/messages.py` (~20 `@dataclass`es) + `/Users/randymcentee/workspace/GitHub/teraguchi/common/hybrid_transport.py::TransportState` at :57-76.

**Apply to:** any new protocol message (`ProtocolErrorMsg`), any FSM state record, any diagnostic bundle manifest struct.

**Pattern (copy `HealthPing` shape):**
```python
@dataclass
class ProtocolErrorMsg:
    type: str = MsgType.ERROR
    code: str = "TERA_UNKNOWN"
    message: str = ""
    session_id: str = ""
    recoverable: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self))
```

---

### `IS_MACOS` / `IS_LINUX` platform branching

**Source:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/platform_backends.py` (single dispatch module).

**Apply to:** any new server-side module that touches OS-specific features (currently none in Phase 1 new modules; diagnostic bundle uses `platform.system()` in `client/bookmarks.py` style instead since it's client-side).

**Rule from CONVENTIONS.md:** never scatter `if sys.platform == "darwin":` — import `IS_MACOS` / `IS_LINUX` from `server.platform_backends`.

```python
# server/platform_backends.py:144-152 (existing)
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

if IS_MACOS:
    from server.mac_screen_capture import MacScreenCapture as ScreenCapture
else:
    from server.screen_capture import ScreenCapture
```

---

### `asyncio.Queue` with bounded size + `QueueFull` drop semantics

**Source:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:639-662` (post-STAB-04 fix).

**Apply to:** every pipeline queue audited for STAB-07. Per RESEARCH.md, there are 4 pipeline queues — the send queue being fixed here is the primary one, but encoder input queue + audio queue + UDP retransmit queue must follow the same shape.

**Pattern (post-fix):**
```python
self.queue: asyncio.Queue = asyncio.Queue(maxsize=4)     # was 30

async def enqueue(self, data) -> bool:
    try:
        self.queue.put_nowait(data)
        return True
    except asyncio.QueueFull:
        self._drop_count += 1
        if self._drop_count == 1:                        # first of a streak
            self._idr_requester()                        # injected callback
            logger.info("broadcaster.idr_requested", drop_count=self._drop_count)
        return False
```

---

### Async task lifecycle (`asyncio.ensure_future` + `_running` flag)

**Source:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:266-271` + `:286` (the `while self._running:` idiom).

**Apply to:** every extracted loop in `server/stream_loop.py`, `health_loop.py`, `monitor_hotplug.py`, `client/connection_supervisor.py`.

```python
self._running = True
self._task = asyncio.ensure_future(self._loop_body())

async def _loop_body(self):
    while self._running:
        ...
        await asyncio.sleep(interval)
```

---

### Argparse + subgroups + `--verbose` (every new entrypoint)

**Source:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/main.py:1014-1057`, `/Users/randymcentee/workspace/GitHub/teraguchi/client/main.py:1139-1147`, `/Users/randymcentee/workspace/GitHub/teraguchi/broker/main.py:271-297`.

**Apply to:** `server/__main__.py` (inherits via `server.main:main`), `client/__main__.py` (inherits via `client.app:main`).

```python
parser = argparse.ArgumentParser(description="Teraguchi ...")
parser.add_argument("--verbose", "-v", action="store_true")
sub = parser.add_argument_group("authentication")
sub.add_argument("--auth-mode", choices=["pam", "local", "none"], default="pam")
args = parser.parse_args()
```

**New flags to thread through (per Phase 1):**
- `--diag-bundle [PATH]` on `server.main` and `client.app` (OBS-05 trigger)
- `--insecure-skip-verify` on `client.app` (dev escape hatch per RESEARCH Open Question #3; requires `TERAGUCHI_ACCEPT_INSECURE=1` env var)

---

### Deferred import for optional deps

**Source:** `/Users/randymcentee/workspace/GitHub/teraguchi/server/mac_screen_capture.py:67-100`, `/Users/randymcentee/workspace/GitHub/teraguchi/server/pam_auth.py:20-25`.

**Apply to:** `common/session_fsm.py` (import `statemachine` inside `try:` with `_HAS_STATEMACHINE` flag so tests can import the module even if the lib is missing), `common/logging.py` (same for `structlog`), `common/diagnostic_bundle.py` (same for `psutil`).

```python
try:
    from statemachine import StateMachine, State
    _HAS_STATEMACHINE = True
except ImportError:
    _HAS_STATEMACHINE = False
```

---

### Redaction before serialization (security)

**No existing analog** — planner writes per RESEARCH §"Diagnostic bundle" redaction table.

Pattern — a processor function applied before JSON render / zip write:
```python
REDACT_KEYS = {"password", "credential", "token", "secret", "tls_key"}

def _redact(d: dict) -> dict:
    return {k: ("[REDACTED]" if k.lower() in REDACT_KEYS else v) for k, v in d.items()}
```

Apply in:
- `common/logging.py` structlog processor chain (before `JSONRenderer`)
- `common/diagnostic_bundle.py` before zip write
- `common/messages.py::ProtocolErrorMsg` constructor (sanitize `message`)

---

### Shell installer idioms (for `packaging/teraguchi-server.spec` sanity)

**Source:** `/Users/randymcentee/workspace/GitHub/teraguchi/install-server.sh:1-50`.

**Apply to:** any new shell invoked from CI workflows (pre/post-install sections of the RPM spec, nightly smoke harness kickoff).

- `#!/usr/bin/env bash`
- `set -euo pipefail`
- `SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"`
- Color log helpers `info()` / `ok()` / `warn()` / `err()`

---

## No Analog Found

Files with no close match in the codebase (planner anchors to RESEARCH.md instead):

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `common/session_fsm.py` | state machine | event-driven | No existing FSM — closest is `TransportState` @dataclass. Use `python-statemachine` per D-13; planner pastes the RESEARCH tables lines 896-929 verbatim. |
| `common/diagnostic_bundle.py` | file I/O + redaction | file-I/O | No existing zip-writer or redaction code. Planner pastes RESEARCH §"Diagnostic bundle" layout (lines 932-973) as module docstring. |
| `tests/conftest.py` / `tests/integration/conftest.py` | fixtures | N/A | Zero tests today. Anchor to `pytest-asyncio` docs + `cryptography.x509` CA builder. |
| `tests/smoke/test_synthetic_1h.py` | 1-hour harness | streaming | No existing harness. Anchor to RESEARCH §"8-Hour Smoke Harness" + D-17 assertions. |
| `.github/workflows/ci.yml` / `build-artifacts.yml` / `smoke-nightly.yml` | CI | N/A | No `.github/` dir exists. Anchor to RESEARCH §"Code Examples" + GHA docs; pin action versions per RESEARCH table (`actions/checkout@v4`, `actions/setup-python@v5`, `actions/cache@v4`, `actions/upload-artifact@v4`). |
| `packaging/teraguchi-server.spec` | RPM spec | N/A | No `packaging/` dir. Anchor to `install-server.sh` system-dep list (`server/teraguchi-server.service` as the `SystemdInstall:` target) + RESEARCH Open Question #2. |

---

## Metadata

**Analog search scope (absolute paths):**
- `/Users/randymcentee/workspace/GitHub/teraguchi/server/` — 21 files
- `/Users/randymcentee/workspace/GitHub/teraguchi/client/` — 19 files
- `/Users/randymcentee/workspace/GitHub/teraguchi/broker/` — 8 files
- `/Users/randymcentee/workspace/GitHub/teraguchi/common/` — 6 files
- `/Users/randymcentee/workspace/GitHub/teraguchi/pyproject.toml`, `requirements-*.txt`, `install-*.sh`

**Files scanned:** 18 (closest analogs read end-to-end or in targeted ranges)
**Files read in full:** `server/health.py`, `broker/tokens.py`, `client/bookmarks.py`, `common/jitter_buffer.py` (head), `common/hybrid_transport.py` (head), `requirements-*.txt`, `pyproject.toml`
**Files read in targeted ranges:** `server/main.py` (7 non-overlapping ranges), `client/main.py` (3 ranges), `client/protocol.py` (2 ranges), `common/messages.py` (3 ranges), `common/quic_transport.py` (1 range), `broker/pool.py` (1 range), `broker/main.py` (2 ranges), `server/auth.py`, `server/session_manager.py` (head), `server/video_encoder.py` (head), `client/session.py` (head), `client/fullscreen_toolbar.py` (head), `install-server.sh` (head)

**Pattern extraction date:** 2026-04-18
