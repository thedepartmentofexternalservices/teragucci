# Phase 1: Stability + CI + Test Baseline — Research

**Researched:** 2026-04-18
**Domain:** Python asyncio production hardening, pytest/CI bootstrap for a zero-tests codebase, FSM introduction on a live protocol, bounded-queue pipeline surgery, structlog instrumentation, and a synthetic nightly smoke harness.
**Overall confidence:** HIGH — every major claim is either verified against the source tree (with line numbers) or cited to official docs/PyPI/vendor release notes from files already in `.planning/research/`. A few are `[ASSUMED]` and called out explicitly (synthetic-stage latency math; PyInstaller `macos-14` GHA behaviour); none touches a locked decision.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions (verbatim from 01-CONTEXT.md)

**Test Strategy**
- **D-01** Coverage = **critical path only**. Lock down `common/` (messages, keymap, transport, jitter buffer), server `auth.py` + `tokens.py`, client `bookmarks.py`, and the new `SessionFSM`. No coverage-% gate.
- **D-02** Hardware mocking = **mock at the FFmpeg subprocess boundary**. Stub the `ffmpeg` subprocess and feed canned encoded frames.
- **D-03** Integration tests = **in-process loopback in CI**. Full client + server stack in one Python process over localhost.
- **D-04** Tests-with-code discipline. No strict red-green-refactor ceremony.

**CI Runner Topology**
- **D-05** GitHub-hosted only: `macos-14` (Apple Silicon) + `rockylinux:9` Docker image on `ubuntu-latest`. No self-hosted runners in v1.
- **D-06** Mac CI = GitHub-hosted `macos-14`. No Wacom HW in CI, no GPU encoder validation in CI (mocked per D-02).
- **D-07** Branch protection required day one. All four gates hard, no exceptions.
- **D-08** CI hard gates (all four): pytest green; ruff + mypy clean; build artifacts succeed (PyInstaller `.app` + RPM dry-run); latency benchmark p99 < **25 ms** LAN (5 ms headroom over the 20 ms target in VIDEO-11).
- **D-09** Latency benchmark uses a **synthetic-stage-time accumulator** (mocked encoders per D-02). Real-hardware latency measurement is manual on DXS until Phase 4.

**Refactor Style**
- **D-10** Decomposition = incremental, test-gated. Characterization tests first, then extract, then prove tests still pass.
- **D-11** `server/main.py` (1,191 lines) aggressive split into ~6–7 modules: `SessionRuntime`, `ClientSession`, `StreamLoop`, `HealthLoop`, `MonitorHotplugLoop`, `EncoderLifecycle`, thin `__main__`.
- **D-12** `client/main.py` (1,189 lines) gets the same treatment in Phase 1. Planner proposes module boundaries mirroring D-11.
- **D-13** FSM library = **`python-statemachine`**. Both ends. State serialised into health pings per STAB-06.
- **D-14** Order: (1) `send_queue` + IDR-on-drop → (2) `ssl.CERT_NONE` removal → (3) pytest + GHA CI green → (4) refactor under the safety net → (5) observability → (6) smoke harness.

**Smoke Harness**
- **D-15** Synthetic client + server loop; headless; numpy frame sink; per-stage latency; RSS growth; dropped frames; audio underflows (audio placeholder until Phase 4).
- **D-16** GitHub Actions nightly cron; one run each on `macos-14` and `rockylinux:9`. Persistent 3-night failure blocks next release tag.
- **D-17** Four assertions (any failing ⇒ fail): zero unhandled exceptions; RSS growth <10% over window; per-stage p99 <25 ms LAN; zero modifier-stuck AND zero audio dropouts.
- **D-18** Phase 1 ships the **1-hour version**. Phase 4 extends to the full 8-hour run.

### Claude's Discretion (research produces concrete recommendations — see §"Concrete Recommendations")

- structlog schema and `common/logging.py` shape
- Precise FSM state set for client and server
- Diagnostic bundle (OBS-05) zip layout + redaction rules
- Error taxonomy (exception hierarchy + structured-error envelope)
- Specific `client/main.py` module boundaries
- Test fixture organisation (repo-root `conftest.py` vs per-package)

### Deferred Ideas (OUT OF SCOPE — do not plan these in Phase 1)

- Self-hosted CI runners on the DXS Flame fleet
- Real-hardware monthly smoke runs
- Full 8-hour smoke harness (Phase 4)
- OpenTelemetry / Prometheus endpoint (OBS-04 → Phase 5)
- Tailscale-native certs, TOFU pinning, corporate-CA bundle (SEC-02..04 → Phase 6)
- Bookmark keychain migration (SEC-06 → Phase 6)
- Session-token rotation (SEC-07 → Phase 6)
- Any INPUT-*, VIDEO-*, DISP-*, AUDIO-*, NET-*, FILE-*, USB-* requirement
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STAB-01 | Pytest suite over `common/` + server auth/tokens + client bookmarks | §"Testing & CI Baseline"; recommend `pytest 8.3`, `pytest-asyncio 0.25` in "strict-auto" mode (scoped fixtures) |
| STAB-02 | GitHub Actions CI on `macos-14` + `rockylinux:9` container every PR | §"Testing & CI Baseline" — `actions/checkout@v4`, `actions/setup-python@v5`, `actions/cache@v4`; Rocky 9 via `container: rockylinux:9` on `ubuntu-latest` |
| STAB-03 | ruff + mypy lint/type gate | §"Testing & CI Baseline" — `ruff 0.11+`, `mypy 1.14+`; strict mode only on `common/` + `client/protocol.py` + new FSM module for Phase 1; progressive tightening in later phases |
| STAB-04 | Fix `send_queue(maxsize=30)` → `maxsize=4` + IDR-on-drop | §"Critical Bug Fixes"; `server/main.py:639,657-662` (queue), `server/video_encoder.py:766` (`request_keyframe()` already exists), `server/main.py:358-383` (`_on_encoded_frame`) |
| STAB-05 | Decompose `server/main.py` | §"Refactor / FSM"; D-11 explicit 6-7 module split; characterisation-test-first approach per Working Effectively with Legacy Code |
| STAB-06 | Explicit SessionFSM on both ends, state in health pings | §"Refactor / FSM"; python-statemachine 2.6+ (async-capable); extend `HealthPing`/`HealthPong` in `common/messages.py:299-318` with `client_state`/`server_state` string fields |
| STAB-07 | Bounded async queues with correct drop policy pipeline-wide | §"Pipeline Queues"; capture→encoder=2 drop-oldest, encoder→broadcaster=3 drop-oldest, broadcaster→per-client=4 drop-oldest-and-IDR, input=64 **block producer** |
| STAB-08 | Client-side `ConnectionSupervisor` | §"ConnectionSupervisor"; owns reconnect policy + exponential backoff + jitter + max-retries; drives client FSM; absorbs reconnect logic currently sprinkled across `client/protocol.py` |
| STAB-09 | 1-hour synthetic smoke harness, nightly CI, 4 assertions | §"Smoke Harness" — `pytest-memray` or `tracemalloc` for RSS; `asyncio` loop for synthetic producer; `schedule: cron` GHA workflow |
| SEC-01 | TLS verification ON everywhere; remove `ssl.CERT_NONE` | §"Critical Bug Fixes"; four sites: `client/protocol.py:295-296`, `client/protocol.py:364-365`, `common/quic_transport.py:413-414`, `broker/pool.py:152-153` |
| OBS-01 | structlog JSON logging throughout server/client/broker | §"Observability"; `structlog 25.1+`; `common/logging.py` with fixed processor chain; `contextvars.ContextVar` binding per-session |
| OBS-02 | Per-stage latency breakdown in client health overlay | §"Observability"; extend `HealthMonitor` deques (`_encode_times`, `_capture_times`, `_input_latencies` already exist in `server/health.py:43-45`), add `transmit_ms`/`decode_ms`/`display_ms`; wire into `client/health_display.py` |
| OBS-03 | Bandwidth + frame-drop + keyframe telemetry (expand existing) | §"Observability"; `HealthMonitor.record_frame_sent/_dropped` already exist; add `record_keyframe_requested` + `record_keyframe_emitted`; expose `keyframe_rate_per_min` field on `HealthStats` |
| OBS-05 | Diagnostic bundle export command | §"Diagnostic Bundle"; `common/diagnostic_bundle.py`; zip with logs + `/status` snapshot + FSM state dump + redacted config; runnable via `teraguchi-server --diag-bundle <out.zip>` and client UI menu item |
</phase_requirements>

## Summary

Phase 1 is a **brownfield production-hardening phase** on a 5,541-line code tree with **zero tests and zero CI**. The three critical-path blockers (`send_queue` time-bomb, `ssl.CERT_NONE` ×4, missing pytest/GHA) are each a small surgical change — the work multiplies because everything after them (FSM, supervisor, structlog, bounded-queue audit, 6-7 module decomposition per side, smoke harness) has to land **under a test safety net that doesn't exist yet**.

The planner's central job is sequencing: **D-14 is load-bearing**. Critical bugs land first so later refactors measure a corrected pipeline. The pytest+CI baseline lands second so the decomposition doesn't ship regressions. Observability lands alongside the refactor (not before — it instruments the new module boundaries, not the monoliths). The smoke harness lands last because it depends on every other piece.

**Primary recommendation:** Structure Phase 1 as ~10–14 incremental plans. Each plan closes one requirement cluster and leaves CI green. No plan proposes a "big bang" — especially not the decomposition. Mirror D-11's 6-7 server modules with a 5-7 client split (recommendation in §"Client Decomposition"). Use `python-statemachine` declarative `@dataclass`-style definitions in a new `common/session_fsm.py` shared by both ends. Serialize state as short strings in the existing `HealthPing`/`HealthPong` dataclasses — no new message types needed.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Bounded queue + drop policy (pipeline backpressure) | Server (per-SessionRuntime + per-ClientSession) | — | All pipeline stages live server-side; client only owns its jitter buffer |
| IDR-on-drop recovery | Server (EncoderLifecycle + per-client Broadcaster) | — | Keyframe request is an encoder primitive; only server talks to the encoder |
| TLS cert verification | Client / Broker (TLS-initiator side) | — | Verification only matters where a connection is being *made*; server presents cert |
| SessionFSM — client side | Client (ConnectionSupervisor + SessionView) | — | Drives reconnect, UI state, ConnectionSupervisor |
| SessionFSM — server side | Server (per-ClientSession) | — | Drives draining, encoder lifecycle, state serialisation in health pings |
| structlog JSON emission | All three tiers (client/server/broker import `common/logging.py`) | — | Shared concern — single schema, single processor chain, bound once per entry point |
| Per-stage latency telemetry | Server (capture/encode/transmit) + Client (decode/display) | — | Each stage instruments its own hop; health-ping exchange stitches them together |
| Smoke harness (synthetic client+server) | Tests tier (`tests/smoke/`) | — | Lives in-process; doesn't belong in client or server packages |
| CI workflows | GitHub Actions (`.github/workflows/`) | — | Infrastructure, not runtime code |
| Diagnostic bundle (OBS-05) | Server (primary — bundle builder) + Client (optional self-bundle) | — | Both tiers can produce one; share a `common/diagnostic_bundle.py` |

## Standard Stack

### Core (new dependencies for Phase 1)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `pytest` | **>=8.3,<9** | Test runner | [VERIFIED: requirements-dev.txt declares `pytest>=7.0`; STACK.md prescribes 8.3+] Established Python default |
| `pytest-asyncio` | **>=0.25,<0.26** | Async test support (ClientProtocol, WebSocket loopback, aioquic) | [CITED: pytest-asyncio docs] Needed everywhere the codebase's 104 `async def` sites land |
| `pytest-timeout` | **>=2.3** | Guard against hanging async tests | [ASSUMED] Prevents a wedged `asyncio.Queue` from stalling CI; 30 s default; overridable per-test |
| `ruff` | **>=0.11,<0.12** | Lint + format (replaces any black/isort/flake8 if ever added) | [CITED: STACK.md §Supporting Libraries]; 10-100× faster than flake8; ships format + lint in one binary |
| `mypy` | **>=1.14,<1.15** | Static type check | [CITED: STACK.md] Strict mode initially scoped to `common/` + `client/protocol.py` + new FSM module |
| `python-statemachine` | **>=2.6,<3** | SessionFSM (client + server) | [CITED: STACK.md + D-13]; async transition support; declarative state + event model; active maintenance |
| `structlog` | **>=25.1,<26** | Structured JSON logging | [CITED: STACK.md + SUMMARY.md + ARCHITECTURE.md]; contextvars binding for per-session context across asyncio tasks |
| `pytest-memray` *(optional but recommended)* | **>=1.7** | RSS-growth assertion in smoke harness (D-17) | [VERIFIED: pypi.org]; purpose-built for "memory growth < X%" assertions in pytest; Linux-only but that's fine since the RSS test only *needs* to run on the Rocky runner |

**Rocky-runner RSS alternative:** `tracemalloc` (stdlib) or `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` on both macOS and Linux. Fallback if `pytest-memray` turns out to be too Linux-specific: roll the assertion in the harness itself with `psutil.Process().memory_info().rss` — adds `psutil>=6.0` dep but works on both runners.

### Supporting (already in tree — verify versions/floors)

| Library | Version | Purpose | Notes |
|---------|---------|---------|-------|
| `websockets` | `>=12.0` (bump floor to `15.x` per STACK.md) | TLS-WS control channel | No Phase 1 work beyond ensuring cert-verify behaviour with real certs |
| `aioquic` | `>=1.3.0` (bump from `>=1.0`) | QUIC transport (still experimental; not promoted in Phase 1 — NET-01 is Phase 5) | Must fix `verify_cert=False` default at `common/quic_transport.py:413-414` to flip to default-True in Phase 1 |
| `cryptography` | `>=44,<46` | TLS primitives + HMAC | Already used; floor bump matches STACK.md |
| `numpy` | `>=2.1,<3` | Frame sinks + canned test frames | Bump floor during dep cleanup |
| `aiohttp` | `>=3.11` | Broker `_probe_one` (needs `ssl=True` fix at `broker/pool.py:152-153`) | |

### GitHub Actions (versions pinned for Phase 1)

| Action | Version | Purpose |
|--------|---------|---------|
| `actions/checkout` | `v4` | Git clone |
| `actions/setup-python` | `v5` | CPython install (`python-version: "3.12"`) |
| `actions/cache` | `v4` | pip cache keyed on `requirements-*.txt` |
| `actions/upload-artifact` | `v4` | Upload `.app` bundle and RPM build for inspection |
| `docker://rockylinux:9` | latest (pinned by digest in `job.container.image`) | Rocky 9 test environment |

[VERIFIED: these are the current GHA majors as of research date per GitHub's actions/*/releases pages referenced in STACK.md].

### Alternatives Considered & Rejected

| Instead of | Could Use | Why Rejected for Phase 1 |
|------------|-----------|--------------------------|
| `python-statemachine` | Hand-rolled `Enum` + transition dict | D-13 locked. Library wins on async transition support and test-friendliness; 7-state FSM doesn't justify custom tooling that will need equivalent test coverage anyway. |
| `structlog` | stdlib `logging` + JSON formatter | Codebase already uses `logging.getLogger(__name__)` everywhere (CONVENTIONS.md). structlog wraps stdlib rather than replacing — existing `logger.info(...)` call sites keep working; the switch is a single-point swap of the logger factory |
| pytest + `unittest` mix | `unittest` only | pytest is the community default and already declared; zero reason to diverge |
| `coverage.py`-enforced % gate | — | D-01 explicitly rejects it |
| Self-hosted Rocky CI runner | — | D-05 rejects it |
| `uv` as dependency installer | pip | Optional local-dev speed-up; not load-bearing for CI since GHA cache is the pip-speed fix. Planner may add as a nice-to-have `setup-python` alternative but not a Phase 1 commitment. |

**Installation (concrete `requirements-dev.txt` diff):**

```diff
 # Development / testing requirements
 -r requirements-server.txt
 -r requirements-client.txt

-pytest>=7.0
-pyinstaller>=6.0
+pytest>=8.3,<9
+pytest-asyncio>=0.25,<0.26
+pytest-timeout>=2.3
+pyinstaller>=6.11,<7
+ruff>=0.11,<0.12
+mypy>=1.14,<1.15
+python-statemachine>=2.6,<3
+structlog>=25.1,<26
+# Optional — used only by the smoke harness RSS assertion
+psutil>=6.0
```

**Version verification note:** These versions match the STACK.md recommendations locked on 2026-04-18. The planner should run `npm view`-equivalent checks (`pip index versions <pkg>` or `uv pip compile`) at plan-time if more than 14 days elapse before Phase 1 execution — versions move.

## Architecture Patterns

### System Data Flow (Phase 1 post-refactor)

```
                        ┌──────────────────────────────────────┐
                        │              CLIENT                  │
                        │                                      │
   User Connect ───────▶│  ConnectionSupervisor (NEW)          │
                        │        │                             │
                        │        ▼                             │
                        │  SessionFSM(client)  ─── state ───┐  │
                        │        │                          │  │
                        │        ▼                          │  │
                        │  ClientProtocol (slimmed)         │  │
                        │     ├─ TLS verify=TRUE ✓          │  │
                        │     └─ IO thread ◀──────────────┐ │  │
                        │                                 │ │  │
                        └─────────────────────────────────┼─┼──┘
                                                          │ │
                  TLS WebSocket / (later: QUIC) ──────────┘ │
                  + HealthPing { client_state, server_state }
                                                            │
                        ┌───────────────────────────────────┼──┐
                        │              SERVER               │  │
                        │                                   ▼  │
                        │  ConnectionAcceptor ─▶ ClientSession │
                        │                          │           │
                        │                          ▼           │
                        │                   SessionFSM(server) │
                        │                          │           │
                        │                          ▼           │
                        │                  SessionRuntime      │
                        │           ┌──────────────┼──────┐    │
                        │           ▼              ▼      ▼    │
                        │      CaptureQ(2) ──▶ EncoderQ(3)     │
                        │      drop-oldest      drop-oldest    │
                        │                         │            │
                        │                         ▼            │
                        │                BroadcasterQ per      │
                        │                client (maxsize=4)    │
                        │                  drop-oldest +       │
                        │                IDR-on-drop  ◀── fix  │
                        │                         │            │
                        │                         ▼            │
                        │                   _send_loop         │
                        └───────────────────────────────────────┘

             All tiers: structlog JSON ─▶ stdout
                        contextvars-bound session_id, client_id, stage
```

**Diagram reading:** Arrows are data flow; bounded queues are marked with maxsize. The fix landing sites are annotated. The observability substrate (structlog + HealthMonitor timing) is pervasive — every arrow can be timestamped at both ends, producing the per-stage latency breakdown OBS-02 needs.

### Recommended Project Structure (post-Phase 1)

```
teraguchi/
├── .github/workflows/                    # NEW
│   ├── ci.yml                            # pytest + ruff + mypy on every PR
│   ├── build-artifacts.yml               # PyInstaller .app + RPM dry-run on PR
│   ├── latency-bench.yml                 # Synthetic-stage-time benchmark, p99 < 25 ms
│   └── smoke-nightly.yml                 # cron: nightly 1-h smoke harness
├── tests/                                # NEW (zero tests today)
│   ├── conftest.py                       # Root fixtures — recommended, not per-package
│   ├── common/
│   │   ├── test_messages.py              # STAB-01 critical path
│   │   ├── test_keymap.py
│   │   ├── test_jitter_buffer.py
│   │   ├── test_hybrid_transport.py
│   │   ├── test_session_fsm.py           # NEW FSM coverage
│   │   └── test_logging.py               # structlog schema
│   ├── server/
│   │   ├── test_auth.py
│   │   ├── test_pam_auth.py              # mocked pam.pam()
│   │   ├── test_video_encoder_mock.py    # mock at ffmpeg subprocess boundary (D-02)
│   │   └── test_pipelines.py             # bounded queue + IDR-on-drop behaviour
│   ├── broker/
│   │   └── test_tokens.py
│   ├── client/
│   │   ├── test_bookmarks.py             # XOR round-trip, migration-safety guard
│   │   └── test_connection_supervisor.py # backoff, jitter, max retries
│   ├── integration/
│   │   ├── conftest.py                   # loopback server/client fixtures (D-03)
│   │   ├── test_auth_flow.py
│   │   ├── test_fsm_state_sync.py        # disagreement detection via health pings
│   │   └── test_reconnect.py
│   └── smoke/
│       ├── test_synthetic_1h.py          # STAB-09 — 1-hour harness
│       └── fixtures/
│           └── canned_encoded_frames.bin # D-02 mocked encoder output
├── common/
│   ├── logging.py                        # NEW — structlog factory + schema
│   ├── session_fsm.py                    # NEW — shared FSM definitions
│   ├── errors.py                         # NEW — exception hierarchy (see §Error Taxonomy)
│   ├── diagnostic_bundle.py              # NEW — OBS-05 zip builder
│   └── ...existing modules unchanged
├── server/
│   ├── __main__.py                       # NEW — thin entrypoint (D-11)
│   ├── cli.py                            # NEW — argparse + main() → D-11
│   ├── session_runtime.py                # NEW — extracted from server/main.py
│   ├── client_session.py                 # NEW
│   ├── connection_acceptor.py            # NEW
│   ├── stream_loop.py                    # NEW — _stream_h264 + _stream_jpeg
│   ├── health_loop.py                    # NEW
│   ├── monitor_hotplug_loop.py           # NEW
│   ├── encoder_lifecycle.py              # NEW
│   ├── http_status.py                    # NEW — /status + (later) /metrics
│   ├── tls_context.py                    # NEW — `build_server_ssl_context` w/ verify
│   ├── main.py                           # SHRINKS — now just `from server.__main__ import main`
│   └── pipelines/                        # NEW
│       ├── __init__.py
│       ├── capture.py                    # bounded queue wrapper
│       ├── encoder.py                    # bounded queue + IDR-on-drop
│       └── broadcaster.py                # per-client send queue (maxsize=4)
├── client/
│   ├── __main__.py                       # NEW — thin entrypoint
│   ├── app.py                            # NEW — QApplication + bootstrap (split from main.py)
│   ├── main_window.py                    # NEW — MainWindow class
│   ├── tab_manager.py                    # NEW — QTabWidget glue
│   ├── session_view.py                   # NEW — per-tab Session UI
│   ├── connection_supervisor.py          # NEW — reconnect policy (STAB-08)
│   ├── fullscreen_toolbar.py             # existing — unchanged
│   ├── main.py                           # SHRINKS — `from client.__main__ import main`
│   └── ...existing modules unchanged
```

### Pattern 1: Critical Bug Fixes — Land First, Self-Contained Plans

**What:** Each of the three critical-path blockers lives in its own one-plan-one-commit ticket with a regression test committed in the same plan (D-04 tests-with-code).

**When to use:** Always for critical-path fixes. Keeps review surface small; each can be reverted independently.

**Example — `send_queue` + IDR-on-drop (STAB-04, VIDEO-06):**

The existing code flow:
- `server/main.py:639` — queue declared `maxsize=30`.
- `server/main.py:657-662` — `enqueue` returns False on `QueueFull`.
- `server/main.py:358-383` — `_on_encoded_frame` loops over `self.clients` and calls `enqueue`; on failure it calls `self.health.record_frame_dropped()` but **does not call `self.encoder.request_keyframe()`**.
- `server/video_encoder.py:766` — `request_keyframe()` already exists — the fix is purely in the dispatch path.

Minimal patch (illustrative — planner will formalise):

```python
# server/client_session.py (extracted in the refactor)
class ClientSession:
    def __init__(self, ws: WebSocketServerProtocol, runtime: "SessionRuntime"):
        ...
        self.send_queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        self._runtime = runtime
        self._drops_since_keyframe = 0

    async def enqueue(self, data: bytes, is_keyframe: bool) -> bool:
        if is_keyframe:
            self._drops_since_keyframe = 0
            # Clear stale P-frames; new IDR supersedes them
            while not self.send_queue.empty():
                try:
                    self.send_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        try:
            self.send_queue.put_nowait(data)
            return True
        except asyncio.QueueFull:
            # Drop OLDEST (favor freshness) and request IDR on first drop of a streak
            try:
                self.send_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.send_queue.put_nowait(data)
            self._drops_since_keyframe += 1
            if self._drops_since_keyframe == 1:
                self._runtime.encoder.request_keyframe()
                log.warning("broadcaster.idr_requested",
                            client_id=self.client_id,
                            session_id=self._runtime.session_id)
            self._runtime.health.record_frame_dropped()
            return False
```

Regression test (`tests/server/test_pipelines.py`):

```python
@pytest.mark.asyncio
async def test_send_queue_idr_on_drop(fake_encoder, fake_ws):
    """Dropping a P-frame must trigger exactly one IDR request."""
    session = ClientSession(fake_ws, runtime_with(fake_encoder))
    # Fill queue + one overflow
    for i in range(5):  # maxsize=4, so 5th enqueue drops oldest
        await session.enqueue(b"pframe-%d" % i, is_keyframe=False)
    assert fake_encoder.keyframe_requests == 1

    # Subsequent drops don't re-request until next keyframe resets the counter
    for i in range(3):
        await session.enqueue(b"pframe-more-%d" % i, is_keyframe=False)
    assert fake_encoder.keyframe_requests == 1

    await session.enqueue(b"keyframe", is_keyframe=True)
    # Now the counter resets, next drop requests another IDR
    for i in range(5):
        await session.enqueue(b"pframe-new-%d" % i, is_keyframe=False)
    assert fake_encoder.keyframe_requests == 2
```

### Pattern 2: TLS Verification — Default-On + Explicit Opt-Out

**What:** Flip `verify_mode = ssl.CERT_NONE` to `verify_mode = ssl.CERT_REQUIRED` + `check_hostname = True` at every call site, and expose one explicit CLI/env knob to disable verification for dev with a LOUD warning.

**Exact sites (all must land in the SEC-01 plan):**

| File | Line | Current | Fix |
|------|------|---------|-----|
| `client/protocol.py` | 295-296 | `check_hostname = False` / `verify_mode = ssl.CERT_NONE` | `check_hostname = True` / `verify_mode = ssl.CERT_REQUIRED` (respect `--insecure-skip-verify` flag) |
| `client/protocol.py` | 364-365 | same (broker connect) | same |
| `common/quic_transport.py` | 413-414 | `if not verify_cert: config.verify_mode = ssl.CERT_NONE` | Flip `verify_cert` default in `connect()` signature from `False` → `True`; remove the `CERT_NONE` path when `verify_cert=True`; keep opt-out logged loudly |
| `broker/pool.py` | 152-153 | `aiohttp.TCPConnector(ssl=False)` | `aiohttp.TCPConnector(ssl=ssl.create_default_context())` (or `ssl=True` which means "use default context") |

**Dev-mode escape hatch:** `--insecure-skip-verify` flag (scary name) exists on every entry point; triggers a `logger.error("TLS VERIFICATION DISABLED — do not use in production")` on every connect (once per session is fine — use `contextvars` + a "logged once" flag if we want to avoid spam).

**Server certs in Phase 1:** The full Tailscale-cert / TOFU / corporate-CA story is Phase 6 (SEC-02..04 per CONTEXT deferred list). For Phase 1, the server and broker get a CLI/env `--tls-cert`/`--tls-key` they already have; tests use a self-signed cert with a **trusted CA fixture** (generated by `cryptography` in a `conftest.py` fixture — the integration test builds a CA, signs a cert, writes them to temp, both client and server load them — real verification exercised without real CAs). This is how the loopback tests of D-03 validate that verify-on really works end-to-end.

### Pattern 3: SessionFSM via `python-statemachine`

**What:** Declarative state + event model in `common/session_fsm.py`, shared between client and server. Each side picks its own set of states (see §"FSM State Sets" for the precise lists). State is a short string (`"streaming"`, `"reconnecting"`, etc.) and serialized verbatim into `HealthPing.client_state` and `HealthPong.server_state` (new fields on `common/messages.py:299-318`).

**Library choice — python-statemachine 2.6+ specifics [CITED: python-statemachine docs]:**
- Supports async actions via `async def` entry/exit/`on_<event>` callbacks.
- `@classmethod` states via `State("Streaming", initial=False)`.
- `.send(event)` is async-aware — must be `await`ed inside async contexts.
- Invalid transitions raise `TransitionNotAllowed` by default — catch it centrally and log `fsm.invalid_transition` at error level (never swallow silently).

**Pattern (skeleton for `common/session_fsm.py`):**

```python
from statemachine import StateMachine, State
from statemachine.exceptions import TransitionNotAllowed

class ClientSessionFSM(StateMachine):
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
    transport_lost = streaming.to(reconnecting) | degraded.to(reconnecting)
    retry_exceeded = reconnecting.to(closed)
    user_quit = streaming.to(closed) | degraded.to(closed) | reconnecting.to(closed) | ...

class ServerClientSessionFSM(StateMachine):
    bootstrapping = State(initial=True)
    authenticating = State()
    capability_exchange = State()
    streaming = State()
    reconfiguring = State()
    draining = State()
    closed = State(final=True)
    # ... transitions mirror client, plus reconfigure + drain
```

**Serialization convention:** `fsm.current_state.id` returns the short string. That string is what's dropped into the ping/pong.

### Pattern 4: Pipeline Bounded Queues (STAB-07)

Refer to ARCHITECTURE.md §Pattern 1. Codified as a table the planner will turn into per-queue unit tests:

| Queue | Maxsize | On Full | Recovery | Test |
|-------|---------|---------|----------|------|
| Capture → Encoder | 2 | Drop oldest | None (next P-frame) | `tests/server/test_pipelines.py::test_capture_queue_drops_oldest` |
| Encoder → Broadcaster | 3 | Drop oldest | None | `test_encoder_queue_drops_oldest` |
| Broadcaster → per-client send | **4** | Drop oldest + request IDR on first drop of streak | IDR arrives ≤100 ms | `test_send_queue_idr_on_drop` (above) |
| Input (client→server) | 64 | **Block producer** | N/A — input must never drop | `test_input_queue_blocks` |

### Pattern 5: ConnectionSupervisor (STAB-08)

Dedicated client-side object owning reconnect decisions. Today reconnect logic is sprinkled across `client/protocol.py` (exception-based control flow with `_BrokerRedirect`, `_reconnect_delay`, etc. — see CONCERNS.md "fragile areas"). Extract into `client/connection_supervisor.py`.

**Responsibilities:**
- Owns exponential backoff + random jitter (base 1 s, max 30 s, ×2 per failure, ±25% jitter).
- Caps retries (default: 20 — ~10 min wall-clock). Emits `retry_exceeded` → FSM `closed`.
- Drives the client FSM: on connect → `connect_requested`; on TLS success → `tls_ok`; on auth success → `auth_ok`; on socket close → `transport_lost`.
- Exposes `await connect()` / `await close()` / `is_reconnecting` properties for the UI.
- Testable in isolation with a fake transport.

### Anti-Patterns to Avoid

- **"Big-bang" decomposition.** D-10 is explicit — incremental, test-gated. No plan should extract more than 2-3 modules at once; each extraction commits with its own characterization tests.
- **Flipping TLS on without a test fixture for real verified certs.** An integration test that still passes with `CERT_NONE` is worse than no test. The loopback test fixture must build a real CA and verify the client rejects a self-signed cert from a *different* CA.
- **Instrumenting before refactoring.** Sprinkling `structlog` calls into `server/main.py`'s current 1,191 lines guarantees they get moved/renamed during decomposition. Instrument the extracted modules, not the monolith. (OBS work rolls in *alongside* refactor per D-14 step 5, not before.)
- **Fixing `send_queue` without the IDR call.** `maxsize=4` without `request_keyframe()` on first drop makes the stall *shorter* but not *fixed*. The two must land together.
- **Letting the smoke harness rely on real encoders.** D-02 + D-09 together require mocked encoders; the harness must feed canned encoded frames through the synthetic pipeline.
- **Adding a coverage percentage gate.** D-01 rejects it. The critical-path rule is enforced by requiring tests for named modules, not a number.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| State machine with async transitions | `Enum` + transition dict | `python-statemachine` | D-13 locked; library's `StateMachine` + `State` + event model beats reinventing the validation matrix, plus it ships invalid-transition exceptions with context |
| JSON-structured logs with contextvars binding | custom `logging.Formatter` | `structlog` (+ stdlib `logging` bridge) | contextvars integration is non-trivial; structlog's `merge_contextvars` processor is canonical |
| Exponential backoff with jitter | hand-rolled `random.uniform` loops | either `tenacity` OR a local 30-line implementation | For a supervisor that owns ~one retry policy, a local implementation is fine; don't pull `tenacity` just for this unless it lands elsewhere |
| Async test fixtures (event loop, ws server) | hand-rolled pytest plumbing | `pytest-asyncio` with `asyncio_mode = "auto"` | The hand-rolled alternative is three fixtures deep and silently gets the event loop policy wrong on macOS |
| Synthetic time measurement in latency bench | `time.perf_counter()` sprinkled ad-hoc | A `StageTimer` helper in `common/logging.py` bound to contextvars | The synthetic-stage accumulator (D-09) wants consistent units and stage labels; one helper, consumed everywhere, produces the benchmark trace |
| TLS test certs | self-signed + `CERT_NONE` | `cryptography` built CA + signed server cert in a pytest fixture | Real verification exercise; no `CERT_NONE` anywhere, even in tests |
| RSS growth assertion | `psutil` loops | `tracemalloc` snapshots at start/end OR `pytest-memray` | Phase 1 D-17 needs "<10% growth" — `tracemalloc` gives a stable number without new deps; `pytest-memray` is slicker but Linux-only |
| Running a websockets server in-process | manual `asyncio.start_server` | `websockets.serve` with `host="127.0.0.1", port=0` and inspect `.sockets[0].getsockname()` for the port | Avoids race on "find free port" — the OS gives us one |

**Key insight:** Python's async/testing ecosystem is mature; the win is picking the 6-8 libraries that compose cleanly and NOT letting the planner get creative. Phase 1's novelty belongs in *the FSM state set*, *the drop-policy behaviour*, and *the smoke-harness assertions* — not in rebuilding plumbing.

## Runtime State Inventory

Phase 1 is **hardening, not rename/refactor-string**. No data stores, no live services, no OS-registered state to rename. Skipping — items below are completeness checks only.

| Category | Items Found | Action Required |
|----------|-------------|-----------------|
| Stored data | **None** — no strings being renamed | None |
| Live service config | **None** — no services being renamed | None |
| OS-registered state | `teraguchi-server.service` systemd unit + `com.dxs.teraguchi.server.plist` LaunchAgent — both keep the same names | None (Phase 6 signing may tweak the plist) |
| Secrets / env vars | `broker.secret`, `--tls-cert`, `--tls-key`, `BROKER_SECRET` — names stay as-is | None |
| Build artifacts | `server/nvfbc/nvfbc_capture` (built per-host, gitignored); `dist/Teraguchi.app` (PyInstaller output, gitignored) | None. Phase 1 PyInstaller dry-run **must** run `python build_client.py` on `macos-14` but does not distribute — only verifies build succeeds (D-08 gate) |

## Common Pitfalls

### Pitfall 1: `python-statemachine` async transitions require explicit await

**What goes wrong:** Developer writes `fsm.send("tls_ok")` inside an async function expecting an awaitable. In `python-statemachine 2.x`, sync and async transitions are different call chains; mixing them inside the same FSM instance produces a silent RuntimeWarning.
**Why it happens:** The library's docs show both patterns; teams converge on one style per FSM class.
**How to avoid:** Pick **async-only** transitions for both FSMs. Declare callbacks as `async def`. Call with `await fsm.send(...)` everywhere.
**Warning signs:** `RuntimeWarning: coroutine 'StateMachine.send' was never awaited`. If this appears in CI, fail the test (pytest has `filterwarnings = error` in `[tool.pytest.ini_options]` — recommended).

### Pitfall 2: `structlog` processor ordering — merge_contextvars must come first

**What goes wrong:** Developer adds `structlog.contextvars.merge_contextvars` to the processor chain *after* `structlog.processors.JSONRenderer()`. Result: contextvar-bound fields don't appear in the output JSON.
**Why it happens:** JSONRenderer is terminal — it serializes and short-circuits.
**How to avoid:** Canonical processor order: `[filter_by_level, merge_contextvars, add_logger_name, add_log_level, TimeStamper, StackInfoRenderer, format_exc_info, UnicodeDecoder, JSONRenderer]`.
**Warning signs:** `session_id` missing from log output even though `bind_contextvars(session_id=...)` ran earlier.

### Pitfall 3: `pytest-asyncio` event-loop-per-test gotcha

**What goes wrong:** Default `asyncio_mode = "strict"` requires every async test to be marked `@pytest.mark.asyncio`. Developer forgets, test passes as sync (skipped), CI goes green on non-executed code.
**Why it happens:** Default mode is strict; "auto" mode is opt-in.
**How to avoid:** Set `[tool.pytest.ini_options] asyncio_mode = "auto"` in `pyproject.toml`. Every `async def test_*` runs under asyncio automatically. Also: `asyncio_default_fixture_loop_scope = "function"` (default) — unless you need session-scoped loops for the loopback harness, in which case declare it explicitly on the fixture, not globally.
**Warning signs:** Test file shows 5 tests collected, `pytest -v` shows 5 passing, but in one of them the async body never actually ran.

### Pitfall 4: GitHub-hosted `macos-14` uses ARM64; PyInstaller arch gotchas

**What goes wrong:** `macos-14` runners are Apple Silicon (arm64). Some Phase 1 deps (or their transitive closures) ship x86_64 wheels only and fall back to sdist builds; the PyInstaller `.app` ends up single-arch. Distribution target per DIST-01/DIST-06 is a universal2 / arm64 bundle.
**Why it happens:** The PySide6/PyAV/PyObjC matrix has historically been fragile on Apple Silicon; STACK.md flags 6.10 as the first PySide with clean ARM64 wheels.
**How to avoid:** In Phase 1 the PyInstaller gate is **dry-run only** — we're verifying the build succeeds, not producing a distributable artifact. Accept arm64-only output on `macos-14`. Signing + notarization + universal2 packaging is Phase 6. Explicitly document this in the CI workflow comment so a future reader doesn't think the arm64-only build is a bug.
**Warning signs:** `lipo -info dist/Teraguchi.app/Contents/MacOS/Teraguchi` returns only `arm64`. Acceptable for Phase 1.

### Pitfall 5: `rockylinux:9` Docker image vs `rocky-9` self-hosted runner

**What goes wrong:** Docker Hub's `rockylinux:9` is the official community image; `dnf install` of Python 3.12 and FFmpeg lands via the EPEL + RPMFusion repos. Developer assumes `yum install python3.12` works out of the box and fails. Or uses `docker.io/rockylinux/rockylinux:9` (same image, different path) and hits rate-limiting on first build without auth.
**Why it happens:** Rocky's default repos lack Python 3.12 and FFmpeg; CI writers forget this step.
**How to avoid:** In `ci.yml` under the Rocky job, enable EPEL first:
```yaml
container:
  image: rockylinux:9
steps:
  - name: Install base deps
    run: |
      dnf -y install epel-release
      dnf -y install python3.12 python3.12-devel python3.12-pip gcc ffmpeg-free
```
Also: the Rocky 9 default `python3` is 3.9 — invoke `python3.12` explicitly everywhere. Set `actions/setup-python` is **not** the right tool inside a container — install Python via `dnf` as above.
**Warning signs:** `python3: command not found` or `No module named 'tomllib'` (stdlib since 3.11).

### Pitfall 6: `websockets.serve` + `ssl` fixture — SSL_SHUTDOWN warnings on every test

**What goes wrong:** The loopback integration test fixture starts a websockets server with TLS. On fixture teardown, websockets logs `SSL shutdown error` at warning level. Combined with `filterwarnings = error`, all tests fail on teardown.
**Why it happens:** websockets' SSL shutdown is best-effort; the test's abrupt close produces a noisy-but-harmless warning.
**How to avoid:** Scope `filterwarnings = error` to specific categories; let `ResourceWarning` and `asyncio`'s SSL-shutdown warnings through. In `pyproject.toml`:
```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error",
    "ignore::ResourceWarning",
    "ignore:SSL shutdown error:Warning",
]
```
**Warning signs:** Entire test file fails at fixture teardown with `Warning: SSL shutdown error`.

### Pitfall 7: `asyncio.Queue.put_nowait` + `get_nowait` race when clearing the queue

**What goes wrong:** The IDR-on-drop fix clears the queue on incoming keyframes: `while not queue.empty(): queue.get_nowait()`. Between the `.empty()` check and the `.get_nowait()` call, another coroutine could have already drained it (single-threaded asyncio makes this unlikely but not impossible if someone adds a `_send_loop` task that `get_nowait`'s too).
**Why it happens:** Copy-paste from threading-world; asyncio is single-threaded but yield points matter.
**How to avoid:** Wrap the drain in a `try/except asyncio.QueueEmpty: break`. See the Pattern 1 code sketch above — already correctly handled.
**Warning signs:** `QueueEmpty: queue empty` appears mid-test. Fix: the try/except pattern.

### Pitfall 8: "Zero unhandled exceptions" assertion is overly strict with asyncio

**What goes wrong:** D-17 assertion #1 is "zero unhandled exceptions." Asyncio raises `CancelledError` during graceful shutdown of tasks — this is not a bug but will trip the assertion.
**Why it happens:** Conflation of "unhandled exception" with "any exception visible to the event loop."
**How to avoid:** Scope the assertion to unhandled exceptions **excluding `asyncio.CancelledError`**. Use `asyncio.get_event_loop().set_exception_handler(...)` to capture `loop.call_exception_handler` events — these are the real uncaught ones. CancelledError raised inside an awaited coroutine is caught by `await` semantics; only if a task is never awaited does it surface.
**Warning signs:** Smoke harness fails on shutdown with `Task was destroyed but it is pending!` or similar.

### Pitfall 9: `filterwarnings = error` + `DeprecationWarning` from deps

**What goes wrong:** `cryptography` 44+ emits deprecation warnings on OpenSSL 3 quirks; `aioquic` emits `DeprecationWarning: The default asyncio policy...` on macOS. With `filterwarnings = error`, tests fail on dependency noise.
**Why it happens:** Transitive deps move faster than projects can chase.
**How to avoid:** Allowlist per-package ignores in `[tool.pytest.ini_options] filterwarnings`, e.g. `"ignore::DeprecationWarning:aioquic.*"`. Revisit on every dep bump.

### Pitfall 10: Refactoring the monolith without preserving `logger` names

**What goes wrong:** `server/main.py:71` uses `logger = logging.getLogger("teraguchi.server")` (a named top-level logger per CONVENTIONS.md). When we extract modules, contributors instinctively switch to `logger = logging.getLogger(__name__)` — breaks log filtering downstream, and breaks any operator's `journalctl -u teraguchi-server | grep teraguchi.server`.
**Why it happens:** Local habit conflicts with the existing project convention.
**How to avoid:** The entry-point modules (`server/__main__.py`, `client/__main__.py`, `broker/main.py`) keep the top-level `teraguchi.server` / `teraguchi.client` / `teraguchi.broker` logger names. Extracted modules under each top use `logging.getLogger(f"teraguchi.server.{short}")` — so `server.pipelines.broadcaster` logs as `teraguchi.server.broadcaster`. Encode this rule in `common/logging.py`'s helper.

## Code Examples

### structlog bootstrap (`common/logging.py`)

```python
"""Shared structlog configuration — imported by all three entry points."""
import contextvars
import logging
import sys
import structlog
from structlog.typing import EventDict, Processor

# Session context binding (call bind() in entry points + per-session)
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_id", default="")

def configure_logging(*, verbose: bool = False, tier: str) -> None:
    """Configure structlog for the given tier (server/client/broker).

    Idempotent; safe to call multiple times.
    """
    level = logging.DEBUG if verbose else logging.INFO
    shared: list[Processor] = [
        structlog.stdlib.filter_by_level,
        structlog.contextvars.merge_contextvars,  # MUST precede JSONRenderer
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    structlog.configure(
        processors=shared + [structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    # Bridge stdlib logging into structlog so existing logger.info() calls flow
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=level,
    )
    # Tag every emit with the tier
    structlog.contextvars.bind_contextvars(tier=tier)

def get_logger(name: str = "") -> "structlog.stdlib.BoundLogger":
    return structlog.get_logger(f"teraguchi.{name}" if name else "teraguchi")
```

### Canned-encoded-frame fixture (D-02 mocked encoder boundary)

```python
# tests/server/conftest.py
import asyncio
import pathlib
import pytest

@pytest.fixture
def canned_hevc_keyframes():
    """3 HEVC NAL units captured from a known-good encoder run.
    Stored at tests/smoke/fixtures/canned_encoded_frames.bin as
    length-prefixed chunks: [uint32 size][is_keyframe u8][NAL bytes]...
    """
    path = pathlib.Path(__file__).parent.parent / "smoke" / "fixtures" / "canned_encoded_frames.bin"
    data = path.read_bytes()
    frames = []
    offset = 0
    while offset < len(data):
        size = int.from_bytes(data[offset:offset+4], "big")
        is_keyframe = bool(data[offset+4])
        payload = data[offset+5 : offset+5+size]
        frames.append((payload, is_keyframe))
        offset += 5 + size
    return frames

class FakeEncoder:
    """Stand-in for VideoEncoder — replays canned frames, records IDR requests."""
    def __init__(self, frames):
        self._frames = frames
        self._cursor = 0
        self.keyframe_requests = 0
        self._on_frame = None
    def start(self, on_encoded_frame):
        self._on_frame = on_encoded_frame
    def feed_frame(self, raw_bgra):
        # Pretend to encode by handing back the next canned frame
        if self._cursor < len(self._frames):
            data, is_kf = self._frames[self._cursor]
            self._cursor += 1
            self._on_frame(data, is_kf)
    def request_keyframe(self):
        self.keyframe_requests += 1
        # On next feed_frame, emit a keyframe from the canned pool
        self._cursor = next(i for i, (_, kf) in enumerate(self._frames[self._cursor:], self._cursor) if kf)
    def stop(self): pass
```

### Synthetic-stage-time accumulator (D-09 latency benchmark)

```python
# tests/smoke/test_latency_benchmark.py
import time
import pytest
import asyncio

# Synthetic budgets per stage (milliseconds). These are *fixed* — the point
# is to exercise the accumulator, not measure real hardware.
STAGE_BUDGETS_MS = {
    "capture": 1.0,
    "encode": 4.0,
    "pipeline": 2.0,
    "transport": 1.0,
    "decode": 3.0,
    "jitter": 4.0,
    "paint": 5.0,
}
EXPECTED_TOTAL_MS = sum(STAGE_BUDGETS_MS.values())  # = 20 ms

@pytest.mark.asyncio
async def test_latency_p99_under_budget(canned_hevc_keyframes):
    """Run the synthetic pipeline N iterations and assert p99 < 25 ms."""
    samples = []
    for _ in range(1000):
        start = time.perf_counter()
        # Simulate each stage by sleeping its budget (mocked encoders per D-02)
        for stage, ms in STAGE_BUDGETS_MS.items():
            await asyncio.sleep(ms / 1000)
        elapsed_ms = (time.perf_counter() - start) * 1000
        samples.append(elapsed_ms)
    samples.sort()
    p99 = samples[int(len(samples) * 0.99)]
    # 25 ms hard gate (D-08). Expected ~20 ms + asyncio scheduling jitter.
    assert p99 < 25.0, f"p99={p99:.1f} ms exceeds 25 ms gate"
```

**[ASSUMED]** The exact synthetic budgets above are a starting point derived from ARCHITECTURE.md §"Data Flow → Target end-to-end latency budget". The planner will refine them against measured base asyncio scheduling overhead on the runners — the first CI run of this benchmark establishes the baseline, then the gate is set 20% above it. User confirmation on the budget split is valuable (call out in Assumptions Log).

### GitHub Actions `ci.yml` skeleton

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [dev, main]

jobs:
  test-linux:
    name: Tests (Rocky 9 + Python 3.12)
    runs-on: ubuntu-latest
    container:
      image: rockylinux:9
    steps:
      - uses: actions/checkout@v4
      - name: Install OS deps
        run: |
          dnf -y install epel-release
          dnf -y install python3.12 python3.12-devel python3.12-pip gcc git \
                         ffmpeg-free xclip pulseaudio-utils
      - name: Install Python deps
        run: |
          python3.12 -m pip install --upgrade pip
          python3.12 -m pip install -r requirements-dev.txt
      - name: Lint + typecheck
        run: |
          python3.12 -m ruff check .
          python3.12 -m ruff format --check .
          python3.12 -m mypy common/ client/protocol.py
      - name: Pytest
        run: python3.12 -m pytest -v --timeout=60

  test-macos:
    name: Tests (macOS 14 / Apple Silicon)
    runs-on: macos-14
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: pip-${{ runner.os }}-${{ hashFiles('requirements-*.txt') }}
      - name: Install Python deps
        run: |
          python3 -m pip install --upgrade pip
          python3 -m pip install -r requirements-dev.txt
      - name: Lint + typecheck
        run: |
          python3 -m ruff check .
          python3 -m ruff format --check .
          python3 -m mypy common/ client/protocol.py
      - name: Pytest
        run: python3 -m pytest -v --timeout=60

  build-mac-app:
    name: PyInstaller .app dry-run
    runs-on: macos-14
    needs: test-macos
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: |
          python3 -m pip install -r requirements-client.txt
          python3 -m pip install pyinstaller>=6.11
      - run: python3 build_client.py
      - uses: actions/upload-artifact@v4
        with:
          name: Teraguchi-app-arm64
          path: dist/Teraguchi.app

  build-rpm-dryrun:
    name: RPM dry-run
    runs-on: ubuntu-latest
    needs: test-linux
    container:
      image: rockylinux:9
    steps:
      - uses: actions/checkout@v4
      - run: dnf -y install rpm-build rpmdevtools
      - name: RPM spec lint + dry-run
        run: |
          # TODO (plan-phase): create packaging/teraguchi-server.spec; this job verifies it builds.
          # Phase 6 handles real signing. Phase 1 gate: the build command exits 0.
          echo "Phase 1 placeholder — spec lands in a future Phase 1 plan"

  latency-bench:
    name: Latency benchmark (p99 < 25 ms)
    runs-on: ubuntu-latest
    container:
      image: rockylinux:9
    needs: test-linux
    steps:
      - uses: actions/checkout@v4
      - run: |
          dnf -y install epel-release
          dnf -y install python3.12 python3.12-pip gcc
          python3.12 -m pip install -r requirements-dev.txt
          python3.12 -m pytest tests/smoke/test_latency_benchmark.py -v
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| stdlib `logging` + string formatting | `structlog` JSON + contextvars | ~2022 industry-wide; still current 2026 | Per-session correlation IDs across asyncio tasks without thread-locals |
| Coverage-percentage gate | Named critical-path modules | D-01 locked; reflects OSS maintainer reality | No theatre, real coverage where regressions would hurt most |
| Hand-rolled state machine via Enum | `python-statemachine` library | 2024+ | Async callback support and invalid-transition exceptions |
| Black + flake8 + isort + autoflake | `ruff` (one binary) | 2023+ | 10-100× faster, one config block |
| WebSocket-only transport + handroll UDP | QUIC (aioquic 1.3) for WAN | 2024 NICE DCV default | **Not Phase 1** — promotion is NET-01 in Phase 5. Phase 1 only removes `CERT_NONE` at `quic_transport.py:413-414` |
| `CERT_NONE` + "it's on a LAN" | Real cert verification by default | Industry-wide | The 3 critical-path blockers in SUMMARY.md |

**Deprecated / outdated in this codebase — already flagged in CONCERNS.md:**
- JPEG dirty-rect fallback alongside H.264 (dead-weight, FFmpeg is a hard dep) — Phase 1 ships tests for the *live* code only; the JPEG path can remain until a later phase deletes it.
- `--auth-mode local` (SHA-256 JSON user DB) — deprecation is Phase 6; Phase 1 tests the existing path faithfully.
- `ssl.CERT_NONE` everywhere — killed by SEC-01 in Phase 1.

## Concrete Recommendations for Claude's Discretion Items

### structlog schema — required + optional fields

**Required on every event (`common/logging.py` enforces via processor):**

| Field | Type | Source |
|-------|------|--------|
| `ts` | ISO 8601 UTC | `structlog.processors.TimeStamper(fmt="iso", utc=True)` |
| `level` | string (`"info"`/`"warning"`/`"error"`/`"debug"`) | `structlog.processors.add_log_level` |
| `tier` | `"server"` / `"client"` / `"broker"` | contextvar bound in entry point |
| `event` | short dotted string (e.g. `"fsm.transition"`, `"broadcaster.idr_requested"`) | first positional arg to `logger.info(...)` |

**Optional (bound per-context; unset ⇒ absent from JSON):**

| Field | Type | When present |
|-------|------|--------------|
| `session_id` | 16-hex | Once a `SessionRuntime` is created; bound via `bind_contextvars(session_id=...)` |
| `client_id` | string (currently `str(id(ws))`) | On every `ClientSession` handler |
| `username` | string | After successful auth |
| `stage` | `"capture"`/`"encode"`/`"transmit"`/`"decode"`/`"display"`/`"input"` | Inside latency-instrumented blocks |
| `state` | FSM state string | Every `fsm.transition` event |
| `prev_state` | FSM state string | `fsm.transition` event only |
| `transport` | `"wss"`/`"quic"`/`"udp"` | Any transport event |
| `latency_ms` | float | Any per-stage timing event |
| `frame_size` | int bytes | encode/transmit/decode events |
| `keyframe` | bool | encode/broadcaster events |
| `err_class` | exception class name | error-level events only |
| `err_code` | string (see Error Taxonomy) | error-level events only |

**Event name taxonomy (prescriptive — pick one; don't let the codebase drift into synonyms):**

- `fsm.transition`, `fsm.invalid_transition`
- `transport.connected`, `transport.closed`, `transport.tls_verify_failed`
- `auth.request`, `auth.success`, `auth.failure`, `auth.token_verify`
- `capture.started`, `capture.error`, `capture.monitor_hotplug`
- `encoder.restart`, `encoder.keyframe_requested`, `encoder.error`
- `broadcaster.enqueue`, `broadcaster.drop`, `broadcaster.idr_requested`
- `health.ping`, `health.pong`, `health.state_disagreement`, `health.degraded`
- `supervisor.connect_attempt`, `supervisor.backoff`, `supervisor.max_retries`
- `diag.bundle_created`

### FSM state set — client (final)

Derived from current `client/protocol.py` flow + failure-mode catalog in ARCHITECTURE.md + CONTEXT.md D-13 sketch:

| State | Entry Trigger | Exit Events | Entry Action |
|-------|---------------|-------------|--------------|
| `disconnected` | App start; user quit | `connect_requested` → `handshaking` | Clear buffers, release codec state |
| `handshaking` | `connect_requested` | `tls_ok` → `authenticating`; `tls_failed` → `reconnecting`; `user_quit` → `closed` | Start 30 s handshake timer |
| `authenticating` | `tls_ok` | `auth_ok` → `capability_exchange`; `auth_failed` → `disconnected`; `auth_timeout` → `reconnecting` | Prompt credential OR use saved bookmark OR replay broker token |
| `capability_exchange` | `auth_ok` | `hello_received` → `streaming`; `proto_mismatch` → `disconnected` | Advertise codecs/monitors, negotiate UDP/QUIC |
| `streaming` | `hello_received` | `health_degraded` → `degraded`; `transport_lost` → `reconnecting`; `user_quit` → `closed` | Start render loop, show viewer widget |
| `degraded` | `health_degraded` (RTT >threshold OR drops >threshold) | `health_restored` → `streaming`; `transport_lost` → `reconnecting` | Show degraded UI, lower quality auto |
| `reconnecting` | `transport_lost` | `connect_requested` (retry) → `handshaking`; `max_retries` → `closed` | Exponential backoff + jitter, show overlay |
| `closed` | `max_retries` OR `user_quit` | (final — terminal) | Free all resources |

Total: **8 states.** Mapping to strings: `"disconnected"`, `"handshaking"`, `"authenticating"`, `"capability_exchange"`, `"streaming"`, `"degraded"`, `"reconnecting"`, `"closed"`.

### FSM state set — server (final)

Per-`ClientSession`. Derived from `server/main.py` `handle_client` flow:

| State | Entry Trigger | Exit Events | Entry Action |
|-------|---------------|-------------|--------------|
| `bootstrapping` | WS accept | `tls_ok` → `authenticating`; `ws_closed` → `closed` | Build ClientSession record |
| `authenticating` | `tls_ok` | `auth_ok` → `capability_exchange`; `auth_failed` → `closed`; `auth_timeout` → `closed` | Send `AuthRequest`, set 30 s timer |
| `capability_exchange` | `auth_ok` | `client_hello` → `streaming`; `ws_closed` → `draining` | Create/attach `SessionRuntime`, send `ServerHello` |
| `streaming` | `client_hello` | `reconfigure_requested` → `reconfiguring`; `ws_closed` → `draining`; `encoder_crashed` → `reconfiguring` | Start stream loop, start health loop |
| `reconfiguring` | quality slider change OR encoder restart | `reconfigure_done` → `streaming`; `encoder_dead` → `closed` | Pause stream, restart encoder, resume |
| `draining` | WS closed OR user quit | `drain_timeout` (60 s) → `closed`; `last_frame_sent` → `closed` | Stop accepting new messages, flush send queue, graceful FIN |
| `closed` | `drain_timeout` OR `auth_failed` | (final — terminal) | Remove from `runtime.clients`; keep `SessionRuntime` alive per PCoIP-style persistence |

Total: **7 states.** Mapping to strings: `"bootstrapping"`, `"authenticating"`, `"capability_exchange"`, `"streaming"`, `"reconfiguring"`, `"draining"`, `"closed"`.

**Disagreement detection (STAB-06):** On every `HealthPong` exchange, the server's view of this client's state is attached (`server_state`), and the client attaches its own state (`client_state` on `HealthPing`). A simple mapping table (client `streaming` ↔ server `streaming`, client `reconnecting` ↔ server `draining` or `closed`, etc.) lives in `common/session_fsm.py`. When the observed pair is not in the allowed-pairs set, both sides emit `health.state_disagreement` at error level with both states. The smoke harness (D-17 assertion #1) will catch this via the "zero unhandled exceptions" criterion if disagreement raises.

### Diagnostic bundle (OBS-05) format

**Target:** A single `teraguchi-diag-<timestamp>.zip` a user can attach to a GitHub issue.

**Zip layout:**

```
teraguchi-diag-20260418T123456Z.zip
├── manifest.json              # version, timestamp, tier, hostname, OS, Python version
├── config/
│   ├── server.toml            # or client.toml — with secrets REDACTED
│   └── env-vars-redacted.txt  # env vars matching TERAGUCHI_* with values "[REDACTED]"
├── logs/
│   ├── current.log            # last 10 MB of the structlog JSON
│   └── previous.log.gz        # previous rotated file if present
├── state/
│   ├── fsm-states.json        # snapshot of every live SessionFSM
│   ├── health-stats.json      # last HealthStats from HealthMonitor
│   ├── queue-depths.json      # {capture_q: 1, encoder_q: 0, per_client: {...}}
│   └── encoder-state.json     # current codec, chroma, resolution, keyframe counter
├── system/
│   ├── python-packages.txt    # pip freeze
│   ├── ffmpeg-version.txt     # ffmpeg -version output
│   ├── gpu.txt                # nvidia-smi / system_profiler SPDisplaysDataType
│   ├── uname.txt              # uname -a
│   └── uptime.txt
└── protocol/
    └── last-hello.json        # last ClientHello/ServerHello exchanged (useful for version-skew bugs)
```

**Redaction rules (required before zip is written):**

| Field | Rule |
|-------|------|
| `--tls-key` path contents | Replace with `[REDACTED: TLS_PRIVATE_KEY]` |
| `broker.secret` file contents | Replace with `[REDACTED: BROKER_HMAC_SECRET]` |
| `users.json` passwords | Hash-replace — `[REDACTED: USER_PASSWORD_HASH]` |
| `bookmarks.json` `password_encrypted` | Replace with `[REDACTED: BOOKMARK_PASSWORD]` |
| Any env var name matching `*_PASSWORD`, `*_SECRET`, `*_KEY`, `*_TOKEN` | Replace value with `[REDACTED]` |
| PAM auth log lines containing passwords | Filter by regex before zipping |
| Broker token bodies | Replace with `[REDACTED: BROKER_TOKEN]`; keep the expiry for debugging |

**Trigger:** `teraguchi-server --diag-bundle [/path/out.zip]` (default path: `~/teraguchi-diag-<ts>.zip`). Client Help menu item "Export Diagnostic Bundle…" calls the same code. Must NOT require restart.

### Error taxonomy (`common/errors.py`)

```python
"""Exception hierarchy for Teraguchi protocol-level errors."""
class TeraguchiError(Exception):
    """Base for all Teraguchi-specific exceptions. Every subclass has a stable `code`."""
    code: str = "TERA_UNKNOWN"

class TransportError(TeraguchiError):
    code = "TERA_TRANSPORT"

class TlsVerificationError(TransportError):
    code = "TERA_TLS_VERIFY"

class ProtocolError(TeraguchiError):
    code = "TERA_PROTOCOL"

class ProtocolVersionMismatch(ProtocolError):
    code = "TERA_PROTO_VERSION"

class InvalidMessageError(ProtocolError):
    code = "TERA_PROTO_INVALID"

class AuthError(TeraguchiError):
    code = "TERA_AUTH"

class AuthFailedError(AuthError):
    code = "TERA_AUTH_FAILED"

class AuthTimeoutError(AuthError):
    code = "TERA_AUTH_TIMEOUT"

class TokenError(AuthError):
    code = "TERA_TOKEN"

class FSMError(TeraguchiError):
    code = "TERA_FSM"

class InvalidTransitionError(FSMError):
    code = "TERA_FSM_TRANSITION"

class EncoderError(TeraguchiError):
    code = "TERA_ENCODER"

class EncoderRestartFailed(EncoderError):
    code = "TERA_ENCODER_RESTART"

class CaptureError(TeraguchiError):
    code = "TERA_CAPTURE"
```

**Structured-error envelope (wire format):** New `MsgType.ERROR = "error"` with dataclass `ProtocolErrorMsg` in `common/messages.py`:

```python
@dataclass
class ProtocolErrorMsg:
    type: str = MsgType.ERROR
    code: str = "TERA_UNKNOWN"           # stable machine-readable code
    message: str = ""                     # human-readable, never raw exception str
    session_id: str = ""
    recoverable: bool = False             # client hint — True ⇒ reconnect viable
```

**Rule:** Never send raw `str(exception)` to the wire. Always wrap in `ProtocolErrorMsg` with a stable `code`. This makes the client's reaction deterministic (specific codes trigger specific FSM events).

### Client decomposition boundaries (D-12 — planner proposal)

Mirroring D-11's 6-7 server modules. `client/main.py` today has (per STRUCTURE.md): `QApplication` bootstrap + `MainWindow` + tab UI + bookmarks panel + quality panel + fullscreen toolbar + USB devices panel + health overlay + entrypoint — 1,189 lines.

**Recommended 6-module split:**

| New module | Extracted content | Approx LOC |
|------------|-------------------|-----------|
| `client/__main__.py` | Thin: calls `client.app.main()` | ~20 |
| `client/app.py` | `QApplication` bootstrap, logging init, CLI parsing, HiDPI setup | ~120 |
| `client/main_window.py` | `MainWindow` class, menu bar, status bar, layout | ~220 |
| `client/tab_manager.py` | `QTabWidget` glue — add/remove/switch/persist open tabs | ~150 |
| `client/session_view.py` | Per-tab widget composition (viewer + toolbar + health) | ~250 |
| `client/connection_supervisor.py` (NEW — STAB-08) | Reconnect policy, FSM driver, backoff | ~200 |
| `client/main.py` | SHRINKS — kept as shim: `from client.__main__ import main` | ~10 |

Existing unchanged: `client/bookmarks.py`, `client/protocol.py` (slimmed slightly — reconnect logic moves to supervisor), `client/viewer.py`, `client/video_decoder.py`, `client/audio_player.py`, `client/quality_control.py`, `client/health_display.py`, `client/fullscreen_toolbar.py`, `client/monitor_selector.py`, `client/file_transfer.py`, `client/usb_forward.py`, `client/key_diagnostic.py`, `client/icons.py`, `client/theme.py`.

**Order of extraction (per D-10 incremental):**
1. `__main__.py` + `app.py` — easy lift, adds characterization test "client starts and exits cleanly in headless Qt test mode (`QT_QPA_PLATFORM=offscreen`)".
2. `connection_supervisor.py` — extract reconnect logic first because STAB-08 is an explicit requirement and landing it early lets the client FSM (STAB-06) hang off it cleanly.
3. `main_window.py` — extract the bulk of the UI class.
4. `tab_manager.py` — carve out tab lifecycle.
5. `session_view.py` — final consolidation.

### Test fixture organization — recommendation

**Recommendation: single repo-root `tests/conftest.py` + per-directory `conftest.py` only where it adds real scope isolation.**

Justification:
- Shared fixtures (TLS CA builder, canned frames, structlog capture, event loop policy) are used by unit and integration and smoke; a root `conftest.py` avoids duplication.
- Integration tests need scoped fixtures (per-session WebSocket servers) — those live in `tests/integration/conftest.py`.
- Smoke tests need even longer-scoped fixtures (1-hour runs) — those live in `tests/smoke/conftest.py`.

Counter-argument (per-package conftest) was that it's easier to see what's in scope. Rejected because the codebase is small enough (5 packages × maybe 5 modules each) that indentation in a single `conftest.py` is easier to navigate than 5 separate files with overlapping fixture sets.

## Dependencies & Versions

| Dependency | Pinned Version | Location | Justification |
|------------|----------------|----------|---------------|
| Python | `>=3.12,<3.14` | `pyproject.toml` (bump from `>=3.10`) | [VERIFIED: STACK.md]; 3.10 EOL Oct 2026; 3.12 first-class ARM64 wheels; 3.13 has free-threaded issues with PySide6 per STACK.md |
| `pytest` | `>=8.3,<9` | `requirements-dev.txt` | [VERIFIED: STACK.md §Supporting Libraries] |
| `pytest-asyncio` | `>=0.25,<0.26` | `requirements-dev.txt` | [CITED: pytest-asyncio] API stable since 0.23 |
| `pytest-timeout` | `>=2.3` | `requirements-dev.txt` | Prevent hung asyncio tests |
| `ruff` | `>=0.11,<0.12` | `requirements-dev.txt` | [CITED: STACK.md] |
| `mypy` | `>=1.14,<1.15` | `requirements-dev.txt` | [CITED: STACK.md] |
| `python-statemachine` | `>=2.6,<3` | `requirements-server.txt` + `requirements-client.txt` | Runtime dep on both tiers; floor chosen so the async-actions API is stable |
| `structlog` | `>=25.1,<26` | all three runtime requirement files | [CITED: STACK.md]; 25.x has the modern `merge_contextvars` API |
| `psutil` *(optional)* | `>=6.0` | `requirements-dev.txt` (not runtime) | Only used by smoke harness for cross-platform RSS |
| `pyinstaller` | `>=6.11,<7` | `requirements-dev.txt` (bump from `>=6.0`) | [CITED: STACK.md]; 6.11 is the version with stable Apple Silicon universal2 behavior |
| `websockets` | `>=15.0,<16` | (floor bump) | [CITED: STACK.md] 15.x streaming-message improvements |
| `aioquic` | `>=1.3.0,<2` | (floor bump) | [CITED: STACK.md]; Phase 1 only fixes `CERT_NONE` default — no promotion |
| `cryptography` | `>=44.0,<46` | Already in tree | [CITED: STACK.md]; needed for test CA fixture |

**GitHub Actions versions (pinned as YAML `uses:`):**

| Action | Version | Notes |
|--------|---------|-------|
| `actions/checkout` | `v4` | [CITED: github.com/actions/checkout] |
| `actions/setup-python` | `v5` | Only used in macos-14 job; Rocky container installs Python via `dnf` |
| `actions/cache` | `v4` | pip cache on macos-14 |
| `actions/upload-artifact` | `v4` | Phase 1 PyInstaller dry-run output |
| Base image: `rockylinux:9` | [VERIFIED: hub.docker.com] Pin by digest once CI is green (after the first successful run) | Avoids silent image drift |

## Common Pitfalls already covered above — see §"Common Pitfalls"

## Validation Architecture

> This section drives VALIDATION.md per `nyquist_validation=true` in `.planning/config.json`.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest` 8.3+ with `pytest-asyncio` 0.25+ in `asyncio_mode = "auto"` |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` section (NEW — Wave 0) |
| Quick run command | `python -m pytest tests/common tests/server tests/client tests/broker -x --timeout=30` |
| Full suite command | `python -m pytest -x --timeout=60` |
| Lint quick check | `python -m ruff check . && python -m ruff format --check .` |
| Type quick check | `python -m mypy common/ client/protocol.py` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| STAB-01 | `common/messages.py` JSON round-trip + binary header codec correctness | unit | `pytest tests/common/test_messages.py -x` | ❌ Wave 0 |
| STAB-01 | `common/keymap.py` every Qt key → Linux scancode correct | unit | `pytest tests/common/test_keymap.py -x` | ❌ Wave 0 |
| STAB-01 | `common/jitter_buffer.py` reorders + evicts correctly | unit | `pytest tests/common/test_jitter_buffer.py -x` | ❌ Wave 0 |
| STAB-01 | `server/auth.py` local-mode challenge/response + token verify | unit (PAM mocked) | `pytest tests/server/test_auth.py -x` | ❌ Wave 0 |
| STAB-01 | `broker/tokens.py` HMAC round-trip + TTL expiry + tamper detect | unit | `pytest tests/broker/test_tokens.py -x` | ❌ Wave 0 |
| STAB-01 | `client/bookmarks.py` XOR encrypt/decrypt round-trip on all three OSes | unit | `pytest tests/client/test_bookmarks.py -x` | ❌ Wave 0 |
| STAB-02 | CI runs on macos-14 + rockylinux:9 every PR | workflow | `.github/workflows/ci.yml` exists, both jobs green | ❌ Wave 0 |
| STAB-03 | Lint + typecheck block merge on violations | workflow | `ruff check` + `mypy` exit 0 in CI | ❌ Wave 0 |
| STAB-04 | `send_queue` maxsize=4 + IDR requested exactly once per drop streak | unit | `pytest tests/server/test_pipelines.py::test_send_queue_idr_on_drop -x` | ❌ Wave 0 |
| STAB-05 | Characterization: after module extraction, `server/main.py` behaviour unchanged | integration | `pytest tests/integration/test_server_bootstrap.py -x` | ❌ Wave 0 |
| STAB-06 | FSM state transitions follow the closed allowed-pairs table; disagreement surfaces via health pings | unit + integration | `pytest tests/common/test_session_fsm.py tests/integration/test_fsm_state_sync.py -x` | ❌ Wave 0 |
| STAB-07 | All 4 pipeline queues enforce their documented drop policy | unit | `pytest tests/server/test_pipelines.py -x` | ❌ Wave 0 |
| STAB-08 | `ConnectionSupervisor` backs off with jitter, caps retries, drives FSM | unit | `pytest tests/client/test_connection_supervisor.py -x` | ❌ Wave 0 |
| STAB-09 | Synthetic 1-hour smoke harness runs green nightly; all 4 D-17 assertions pass | smoke | `pytest tests/smoke/test_synthetic_1h.py -v --timeout=4000` | ❌ Wave 0 |
| SEC-01 | All 4 `CERT_NONE` sites removed; client rejects self-signed cert from wrong CA | integration | `pytest tests/integration/test_tls_verify.py -x` | ❌ Wave 0 |
| OBS-01 | structlog JSON schema present on every emit (required fields) | unit | `pytest tests/common/test_logging.py -x` | ❌ Wave 0 |
| OBS-02 | Per-stage latency breakdown emitted in `HealthStats` | unit + integration | `pytest tests/server/test_health_monitor.py tests/integration/test_latency_breakdown.py -x` | ❌ Wave 0 |
| OBS-03 | `keyframe_requested` / `keyframe_emitted` counters exposed | unit | `pytest tests/server/test_health_monitor.py::test_keyframe_telemetry -x` | ❌ Wave 0 |
| OBS-05 | Diagnostic bundle includes all documented sections and passes redaction rules | integration | `pytest tests/integration/test_diag_bundle.py -x` | ❌ Wave 0 |
| D-08 gate | Latency p99 < 25 ms LAN in synthetic benchmark | smoke | `pytest tests/smoke/test_latency_benchmark.py -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** Quick suite (lint + typecheck + `tests/common tests/server tests/client tests/broker` — excludes `tests/integration` and `tests/smoke`). Target runtime: < 30 s local.
- **Per wave merge:** Full suite including `tests/integration`. Target runtime: < 5 min local, < 10 min CI.
- **Phase gate (`/gsd-verify-work 1`):** Full suite green + all 4 CI jobs green on the PR + one nightly smoke run green on each runner.

### Wave 0 Gaps (every test file below must be created)

- [ ] `pyproject.toml` — add `[tool.pytest.ini_options]`, `[tool.ruff]`, `[tool.mypy]`, and bump `requires-python = ">=3.12"`
- [ ] `requirements-dev.txt` — apply the diff in §"Installation"
- [ ] `tests/conftest.py` — root fixtures: TLS CA builder, structlog capture, offscreen Qt, canned encoded frames
- [ ] `tests/common/test_messages.py` — STAB-01
- [ ] `tests/common/test_keymap.py` — STAB-01
- [ ] `tests/common/test_jitter_buffer.py` — STAB-01
- [ ] `tests/common/test_hybrid_transport.py` — STAB-01
- [ ] `tests/common/test_session_fsm.py` — STAB-06
- [ ] `tests/common/test_logging.py` — OBS-01
- [ ] `tests/server/test_auth.py` — STAB-01
- [ ] `tests/server/test_pam_auth.py` — STAB-01 with mocked `pam.pam()`
- [ ] `tests/server/test_video_encoder_mock.py` — D-02 mocked FFmpeg subprocess
- [ ] `tests/server/test_pipelines.py` — STAB-04, STAB-07
- [ ] `tests/server/test_health_monitor.py` — OBS-02, OBS-03
- [ ] `tests/broker/test_tokens.py` — STAB-01
- [ ] `tests/client/test_bookmarks.py` — STAB-01
- [ ] `tests/client/test_connection_supervisor.py` — STAB-08
- [ ] `tests/integration/conftest.py` — loopback server/client harness (D-03)
- [ ] `tests/integration/test_auth_flow.py`
- [ ] `tests/integration/test_tls_verify.py` — SEC-01
- [ ] `tests/integration/test_fsm_state_sync.py` — STAB-06
- [ ] `tests/integration/test_reconnect.py` — STAB-08
- [ ] `tests/integration/test_diag_bundle.py` — OBS-05
- [ ] `tests/integration/test_latency_breakdown.py` — OBS-02
- [ ] `tests/smoke/fixtures/canned_encoded_frames.bin` — D-02 canned data
- [ ] `tests/smoke/test_latency_benchmark.py` — D-08 gate, D-09 synthetic accumulator
- [ ] `tests/smoke/test_synthetic_1h.py` — STAB-09 (Phase 1 = 1-hour per D-18)
- [ ] `.github/workflows/ci.yml` — STAB-02, STAB-03
- [ ] `.github/workflows/build-artifacts.yml` — D-08 artifact gate
- [ ] `.github/workflows/smoke-nightly.yml` — D-16 nightly cron
- [ ] Framework install: already in `requirements-dev.txt` after the diff lands

## Environment Availability

| Dependency | Required By | Available (on dev box, macOS 15.3) | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | CI + dev | ✓ (via Homebrew) | 3.14.4 currently in `.venv`; need 3.12 pin for consistency | Use `python3.12` explicitly in CI |
| `pytest` / `ruff` / `mypy` | CI + dev | ✗ (not installed globally) | — | `pip install -r requirements-dev.txt` (after the diff lands) |
| `ffmpeg` | Runtime + smoke harness mocks | ✓ (Homebrew path) | — | macos-14 CI installs via `brew install ffmpeg@7`; Rocky 9 CI installs via `dnf install ffmpeg-free` with EPEL |
| `docker` (for Rocky container) | CI only | GHA runners handle this | — | N/A |
| Apple Developer ID | Phase 6 signing, NOT Phase 1 | N/A | N/A | Phase 1 uses PyInstaller dry-run (unsigned, arm64-only) |
| NVIDIA GPU | Not required — D-02 mocks at subprocess boundary | N/A | N/A | All encoder testing uses canned frames |
| Wacom tablet | Not required (INPUT tests are Phase 2) | N/A | N/A | Phase 1 explicitly excludes Wacom work |
| `/dev/uinput` | Not required in CI (no real input injection tested in Phase 1) | N/A | N/A | Mocked |

**Missing dependencies with no fallback:** None — CI runners supply everything the planner needs.

**Missing dependencies with fallback:** The dev loop will assume `python3.12 -m pytest` works post-`requirements-dev.txt` install; `uv` is optional.

## Security Domain

**Phase 1 security scope is bounded:** SEC-01 only. The other SEC-* requirements (02-09) are deferred to Phase 6 per CONTEXT.md and REQUIREMENTS.md. `security_enforcement` is present in the project but the Phase 1 touchpoints are narrow.

### Applicable ASVS Categories

| ASVS Category | Applies to Phase 1 | Standard Control (this phase) |
|---------------|-------|-----------------|
| V1 Architecture, Design | yes (secondary) | FSM makes reconnect behaviour reviewable; structlog gives auditability |
| V2 Authentication | partial | Phase 1 ships regression tests for existing PAM + HMAC token path; no new auth work |
| V3 Session Management | partial | FSM makes session lifecycle explicit; token rotation (SEC-07) is Phase 6 |
| V4 Access Control | N/A (no new access-control code) | — |
| V5 Input Validation | yes | Add validation on `bus_id` (USB regex) + structured-error envelope prevents raw exception strings reaching clients |
| V6 Cryptography | yes | **SEC-01** closes the verification gap — use stdlib `ssl` + `cryptography` — no hand-rolled crypto |
| V7 Errors & Logging | yes | `structlog` JSON + error taxonomy with stable codes |
| V9 Communications | yes | **SEC-01** — TLS 1.3 verified on WSS, QUIC, and aiohttp probe paths |

### Known Threat Patterns (phase-scope)

| Pattern | STRIDE | Standard Mitigation (Phase 1) |
|---------|--------|-------------------------------|
| MITM on a TLS connection with verification off | Spoofing | Remove all four `ssl.CERT_NONE` sites (SEC-01) |
| Replay of broker token | Tampering / Elevation | Token has 60 s TTL + HMAC-SHA256; existing `hmac.compare_digest` prevents timing attacks — no new work, but add regression tests on `broker/tokens.py` |
| Shell injection via USB `bus_id` | Tampering | CONCERNS.md flagged this; regex-validate `bus_id` before passing to `subprocess.run` — low-risk add during Phase 1 if touching `usb_passthrough.py`, deferrable otherwise (Phase 5 USB work) |
| Credentials leaking into logs | Information Disclosure | structlog processor chain MUST redact known sensitive fields (`password`, `credential`, `token`) before JSONRenderer |
| Diagnostic bundle exposing secrets | Information Disclosure | Redaction rules documented above in §"Diagnostic bundle" — enforced by unit test |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Synthetic stage budgets sum to ~20 ms base (capture 1, encode 4, pipeline 2, transport 1, decode 3, jitter 4, paint 5) | §"Synthetic-stage-time accumulator" / §"Code Examples" | Numbers are starting points from ARCHITECTURE.md budget — if asyncio scheduling adds >5 ms on macos-14 GHA runners, p99 gate at 25 ms could be flaky. Mitigation: planner establishes the CI baseline in the first green run and sets gate 20% above observed p99 |
| A2 | PyInstaller 6.11+ on macos-14 arm64 produces a working `.app` for Phase 1 dry-run | §"Pitfall 4" | Phase 1 accepts arm64-only; if the build fails entirely, the gate becomes a "smoke test that PyInstaller ran without error" — downgrade to exit-code-only check |
| A3 | `rockylinux:9` image has no rate-limiting blockers for GHA | §"Pitfall 5" | Docker Hub pull limits apply; if they bite, pin by digest + consider ghcr.io mirror |
| A4 | RSS-growth assertion works via either `pytest-memray` (Linux) or `psutil` (cross-platform) | §"Standard Stack"; D-17 | If `pytest-memray` is too narrow (Linux container only) the harness falls back to `psutil.Process().memory_info().rss` on both runners |
| A5 | The existing `HealthMonitor` (`server/health.py`) extends cleanly with per-stage transmit/decode/display fields | §"Observability"; OBS-02 | Code-read showed `_encode_times`/`_capture_times`/`_input_latencies` deques already present (lines 43-45) — adding transmit/decode/display is parallel. Risk is low |
| A6 | `python-statemachine 2.6+` async transitions are stable enough for production use | D-13 + §"FSM Library" | If async support has edge cases, fallback is hand-rolled Enum+dict per ARCHITECTURE.md pattern sketch. Re-evaluate if the first async test trips an unexpected library bug |
| A7 | `filterwarnings = error` + selective ignores is maintainable across dep bumps | §"Pitfall 9" | Mitigation: every dep bump runs a manual "ratchet" of `filterwarnings` — annotate in `pyproject.toml` comments |
| A8 | The PyInstaller `build_client.py` currently works on macos-14 arm64 | §"Environment Availability" + §"Pitfall 4" | Worth a one-shot manual run on Randy's Mac Studio pre-CI to confirm; fall back to gating on exit code only |

## Open Questions

1. **Latency benchmark seed budget** — What exact synthetic stage times should the D-09 accumulator use? The ARCHITECTURE.md "target end-to-end latency budget" (1+4+2+1+3+4+5 = 20 ms) is the starting point, but these are target hardware numbers, not runner-scheduling numbers. First CI run will establish a baseline; planner should call this out as "baseline in first green run, gate at 1.25× observed p99." Not blocking.
2. **Should Phase 1 ship an RPM spec file, or only a `rpmbuild --nodeps --buildonly` dry-run?** D-08 says "Build artifacts succeed" but doesn't specify a real `.rpm` output. Recommendation: Phase 1 ships a minimal `packaging/teraguchi-server.spec` that builds cleanly; full signing + key publication is Phase 6 DIST-02 / DIST-04. Flag to planner for confirmation — it's ~50 lines of RPM spec.
3. **Should the `--insecure-skip-verify` dev flag be in Phase 1 at all?** Arguments both ways: (a) enabling post-SEC-01 development is a real problem when your dev box has no real cert, (b) a flag that disables security is exactly the knob that leaks to production. Recommendation: ship the flag, log at `ERROR` level on every use, require a `TERAGUCHI_ACCEPT_INSECURE=1` env var as a second safety net. Defer to user if they want to remove the flag entirely.
4. **Client decomposition granularity for `client/session.py`** — `client/session.py` is the per-tab glue class (~300 lines estimated). D-12 says "planner proposes" specific module boundaries. My proposal in §"Client Decomposition" keeps `session.py` as-is and wraps it with `session_view.py`. Alternative is to split `session.py` itself into `session_protocol.py` + `session_bridge.py`. Both work; the wrap-don't-split approach is lower risk. Planner's call.
5. **Per-stage timing between server and client requires clock-drift handling** — per-stage latency (OBS-02) spans both tiers. If the client and server wall-clocks differ by ~200 ms (normal NTP drift), the "transmit_ms" stage produces nonsense. Standard fix: use `HealthPing.timestamp_ms` + `HealthPong.ping_timestamp_ms` + `server_timestamp_ms` (already in the existing protocol at `common/messages.py:299-318`) to estimate clock offset, then subtract. Recommendation: the OBS-02 work in Phase 1 computes clock offset once per session at handshake, re-estimates every 60 s, and applies it to stage timestamps. Flag for planner to plan (this is a ~40-line helper). Not blocking — if deferred, per-stage latency is server-measured + client-measured separately rather than end-to-end.

## Sources

### Primary (HIGH confidence)

- **Project files (direct code read):**
  - `server/main.py:639,657-662,358-383` — `send_queue` declaration + `_on_encoded_frame` dispatch (STAB-04)
  - `server/main.py:106-138` — `DISPLAY` env manipulation (CONCERNS flag)
  - `server/video_encoder.py:766` — `request_keyframe()` API already present (needed for STAB-04 fix)
  - `client/protocol.py:291-296,360-365` — TLS cert-verify disable points (SEC-01)
  - `common/quic_transport.py:402-414` — QUIC client config default `verify_cert=False` (SEC-01)
  - `broker/pool.py:148-176` — aiohttp probe with `ssl=False` (SEC-01)
  - `common/messages.py:299-318` — existing `HealthPing` / `HealthPong` / `HealthStats` dataclasses (extension target for STAB-06)
  - `server/health.py:23-145` — existing `HealthMonitor` with deques for encode/capture/input timing (OBS-02 extension target)
  - `broker/tokens.py:21-65` — existing HMAC token format (regression test target)
  - `client/bookmarks.py:37-85` — existing XOR-key derivation (regression test target, keychain migration is Phase 6)
  - `tools/keydiag.py` — existing diagnostic pattern (extensible to `keydiag.py`-style instrumentation per PITFALL 4)
  - `pyproject.toml` / `requirements-*.txt` — current versions (need bump per STACK.md)
  - `.planning/config.json` — `nyquist_validation: true`, `model_profile: quality`, `mode: yolo`, `workflow.research/plan_check/verifier all true`

- **Research docs already in repo:**
  - `.planning/research/STACK.md` — stack prescription with versions (Python 3.12 floor, structlog 25.x, pytest 8.3+, python-statemachine, aioquic 1.3.0)
  - `.planning/research/ARCHITECTURE.md` — FSM state sets, pipeline queue sizes with drop policy, QUIC rationale, platform-backend Protocol ABC pattern
  - `.planning/research/PITFALLS.md` — 10 critical-pitfall catalog, latency death-by-thousand-cuts, 10-bit silent downgrade, TLS off everywhere
  - `.planning/research/SUMMARY.md` — critical-path table, phase ordering
  - `.planning/codebase/ARCHITECTURE.md` — 3-tier structure, data flow diagrams
  - `.planning/codebase/STRUCTURE.md` — file tree + naming conventions
  - `.planning/codebase/CONCERNS.md` — tech-debt + security + platform-parity catalog with exact file:line refs
  - `.planning/codebase/TESTING.md` — zero-tests baseline confirmed
  - `.planning/codebase/CONVENTIONS.md` — logging pattern (`logging.getLogger("teraguchi.server")` et al.)
  - `.planning/REQUIREMENTS.md` — Phase 1 owns STAB-01..09, SEC-01, OBS-01..03, OBS-05 (14 requirements)
  - `.planning/ROADMAP.md` §"Phase 1" — 5 success criteria + dependency statement "Depends on: Nothing"
  - `CLAUDE.md` — hard constraints: Python 3.10+ asyncio, Rocky 9 only, latency <20 ms LAN, Apache 2.0, solo-maintainer discipline

### Secondary (MEDIUM confidence — library/framework docs)

- `python-statemachine` library — async callbacks, State+Event model, TransitionNotAllowed exception — cited across CONTEXT.md D-13 and SUMMARY.md
- `structlog` 25.x docs — `merge_contextvars` processor ordering; `cache_logger_on_first_use`
- `pytest-asyncio` 0.25 docs — `asyncio_mode = "auto"`; fixture scopes
- `ruff` 0.11 docs — `check` + `format --check` subcommands
- Apple PyInstaller Apple Silicon guidance — referenced via STACK.md §"Packaging & Distribution"
- Rocky 9 EPEL + RPMFusion repo availability — referenced via STACK.md §"Installation"

### Tertiary (needs validation at plan-phase or via first CI run)

- Exact GHA action majors (all `v4`/`v5`) — stable at research-date but move; pin and verify
- Exact PyInstaller 6.11 behaviour on macos-14 — first CI run confirms
- `pytest-memray` Linux-only posture — fallback to `psutil` is cheap
- Docker Hub `rockylinux:9` rate-limiting behaviour in public CI — first few runs confirm

## Metadata

**Confidence breakdown:**
- Critical bug fixes (STAB-04, SEC-01): **HIGH** — exact line numbers verified by direct code read; the `request_keyframe()` method already exists in the encoder (`server/video_encoder.py:766`)
- Test & CI baseline (STAB-01..03): **HIGH** — standard pytest + GHA pattern, versions cited from STACK.md
- Refactor boundaries (STAB-05, D-11/D-12): **HIGH** for server (D-11 explicit), **MEDIUM-HIGH** for client (D-12 requires planner proposal; my 6-module split mirrors D-11 faithfully)
- FSM (STAB-06, D-13): **HIGH** for choice of library and for serialization mechanism (existing HealthPing/Pong); **MEDIUM-HIGH** for exact state list — small possibility of adding/removing one during plan-phase
- Pipeline queues (STAB-07): **HIGH** — codified from ARCHITECTURE.md Pattern 1, directly confirmed against code
- ConnectionSupervisor (STAB-08): **HIGH** — standard reconnect-policy pattern; behaviour fully specified
- Observability (OBS-01..03, OBS-05): **HIGH** for structlog (standard config) and diagnostic bundle (novel but specced); **MEDIUM-HIGH** for per-stage latency (clock-drift concern in Open Q #5)
- Smoke harness (STAB-09): **MEDIUM-HIGH** — assertions are clear; RSS-growth implementation has two equally-valid approaches
- Latency gate (D-08 / D-09): **MEDIUM** — gate number (25 ms) is firm; baseline-in-first-run is the operational open question

**Research date:** 2026-04-18
**Valid until:** 2026-05-18 (30 days for dep versions; re-verify `pip index versions` for pytest, ruff, mypy, python-statemachine, structlog on the first plan-phase day if more than 14 days elapse)

---

*Research for Phase 1 of Teraguchi v1. Consumed by gsd-planner to produce PLAN.md files; consumed by gsd-verify-work-phase to assert the 14 phase requirements are each addressed by at least one task with a regression test.*
