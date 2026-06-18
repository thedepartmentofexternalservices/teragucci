# Architecture Research

**Domain:** High-performance remote desktop / remote workstation for VFX and Autodesk Flame workflows
**Researched:** 2026-04-18
**Confidence:** HIGH (existing prototype + well-documented reference implementations)

---

## Executive Summary

The existing Teraguchi three-tier architecture (client / optional broker / server) is **fundamentally sound and matches the production patterns used by Sunshine, Parsec, and NICE DCV**. What needs hardening for v1 is:

1. **State-machine explicitness.** The implicit "some state in `ClientProtocol`, some in `Session`, some in `SessionRuntime`, some in `HybridServerTransport`" model is the root cause of the "half-connected" bug class. Introduce an explicit connection-state FSM on each side, shared via serialized state snapshots in health pings.
2. **Pipeline decoupling.** Capture-rate, encode-rate, and network-rate are currently implicitly coupled through a single "FPS" knob on `SessionRuntime._stream_h264`. Production systems decouple these with bounded queues + explicit drop policies + keyframe-on-drop recovery. This is already partially in place (`send_queue maxsize=30`) but lacks the recovery half.
3. **Platform backend symmetry.** The duck-typed backend contracts in `server/platform_backends.py` are working but leaking Linux assumptions (`DISPLAY` manipulation, `pactl` probes, `/proc/uptime`). Promote the informal contract to a `typing.Protocol` ABC and audit each call site.
4. **Transport-layer convergence on QUIC.** The `HybridServerTransport` (TCP control + optional UDP media + experimental QUIC) is a reasonable hedge but is two code paths to maintain. The industry has converged on QUIC for this exact use case (NICE DCV default, Media-over-QUIC shipping at NAB 2026, WebTransport in Safari 26.4). Recommendation: make QUIC the production target for v1 with WebSocket+UDP as the LAN-fallback path, not the other way around.
5. **Failure-mode coverage.** Most failure paths (GPU driver reload, monitor unplug, network migration, wake-from-sleep) are either untested or rely on process restart. Production systems make the pipeline **stateless enough to rebuild on any of these events** within a 2-second window without dropping the client session.

Teraguchi already has the hardest architectural work done: per-user X session spawning, shared protocol library, session persistence across disconnects, HMAC-signed broker tokens. The v1 hardening effort is **not a rewrite** — it's a disciplined refactor of module boundaries, explicit state machines, bounded queues with documented drop semantics, and a failure-mode recovery catalog.

---

## Standard Architecture (Industry Reference)

### Production Remote-Workstation System Model

Based on Sunshine/Moonlight, Parsec, NICE DCV, and PCoIP public architecture docs, production remote-workstation systems converge on the following block diagram:

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              CLIENT                                       │
│ ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌────────────┐ ┌─────────────┐  │
│ │  Input   │ │  Render  │ │  Decoder  │ │   Audio    │ │  Telemetry  │  │
│ │ Capture  │ │  (GPU)   │ │ (HW-accel)│ │   Output   │ │   Overlay   │  │
│ └────┬─────┘ └────▲─────┘ └─────▲─────┘ └──────▲─────┘ └──────▲──────┘  │
│      │            │             │              │               │         │
│ ┌────▼────────────┴─────────────┴──────────────┴───────────────┴──────┐ │
│ │                  Session State Machine (FSM)                         │ │
│ │   {Disconnected → Handshaking → Streaming → Degraded → Reconnecting} │ │
│ └────────────────────────────┬─────────────────────────────────────────┘ │
└──────────────────────────────┼───────────────────────────────────────────┘
                               │ Transport (QUIC/WSS+UDP)
                               │ - Control channel (reliable)
                               │ - Media channel (unreliable+FEC)
                               │ - Input channel (reliable, small)
┌──────────────────────────────┼───────────────────────────────────────────┐
│                              │               SERVER                      │
│ ┌────────────────────────────▼─────────────────────────────────────────┐ │
│ │                    Connection Manager (FSM)                           │ │
│ │   Per-client: {Connecting → Authenticating → Streaming → Draining}   │ │
│ └────┬────────┬──────────────────────────────────────────────┬──────────┘ │
│      │        │                                              │            │
│ ┌────▼──┐ ┌──▼──────┐ ┌─────────────┐ ┌──────────┐ ┌─────▼─────┐       │
│ │ Auth  │ │ Session │ │   Capture   │ │ Encoder  │ │   Input   │       │
│ │ (PAM) │ │ Manager │ │  Pipeline   │─▶ Pipeline │ │ Injector  │       │
│ │       │ │ (per-   │ │ (per-user)  │ │(per-user)│ │(per-user) │       │
│ └───────┘ │  user)  │ └──────┬──────┘ └─────┬────┘ └───────────┘       │
│           └─────────┘        │              │                           │
│                   ┌──────────▼──────┐ ┌─────▼─────┐                     │
│                   │   Clipboard     │ │   Audio   │                     │
│                   │    Cursor       │ │  Capture  │                     │
│                   │    USB/IP       │ │           │                     │
│                   └─────────────────┘ └───────────┘                     │
│                                                                          │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │  Platform Backends (Linux: Xorg/Xvfb + NVENC/VAAPI + uinput)          │ │
│ │                   (macOS: SCK + VideoToolbox + CGEventPost)           │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘

OPTIONAL:
┌──────────────────────────────────────────────────────────────────────────┐
│                          BROKER (optional)                                │
│   Auth → Pool Scheduler → Token Mint (HMAC) → Redirect to server          │
│   Never sees media. Never on critical path. Can be offline for LAN use.  │
└──────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| **Session State Machine (Client)** | Model every reachable state from "not connected" through "streaming" to "reconnecting"; drive reconnect logic from events, not wall-clock polling. | `python-statemachine` library with async actions, or hand-rolled enum+dispatch. Parsec BUD uses explicit state. Sunshine uses similar RTSP state progression. |
| **Session State Machine (Server, per-client)** | Mirror of client FSM. Owns `ClientSession` lifecycle. | Same as client — finite set of states + guarded transitions. |
| **Connection Manager (Server)** | Accept sockets, route to auth, attach to `SessionRuntime`, tear down on disconnect. | asyncio coroutine that owns the `websockets.serve` handler (what `server/main.py` does today). |
| **Session Manager (per-user, Linux)** | Spawn / reattach Xvfb or Xorg; manage user's GUI session lifecycle independent of client connections. | `server/session_manager.py` today. Industry analogue: xrdp sesman, NICE DCV's session daemon. |
| **Capture Pipeline** | Produce raw frames at capture-rate into a bounded queue. Must be decoupled from encode-rate. | NvFBC / XShm / DXGI / SCK / DMA-BUF (Vulkan Video). Sunshine 2026 added Vulkan zero-copy DMA-BUF. |
| **Encoder Pipeline** | Consume raw frames, produce encoded NAL/OBU bytes at encode-rate. Supports keyframe-on-demand. | FFmpeg subprocess (Teraguchi today) or direct library binding (NvEnc SDK, VideoToolbox). Zero-copy is the north star. |
| **Transport** | Move bytes with prioritization (input > control > audio > video). Handle reliability asymmetry per channel. | QUIC (NICE DCV default 2024+), WebRTC (browser-first systems), or custom UDP (Parsec BUD). |
| **Input Injector** | Accept input protocol messages, inject at OS level as reliably as local HW would. | uinput (Linux phys), XTest (Linux virtual), CGEventPost (macOS), SendInput (Windows). |
| **Clipboard / Cursor / USB** | Side-channels that run independently of the video path. | xclip/NSPasteboard/usbip as separate asyncio tasks. |
| **Telemetry** | Structured logs + metrics + health endpoint visible to client overlay. | structlog JSON + OpenTelemetry log bridge in 2025+. Health HTTP on same WS port is idiomatic. |
| **Broker (optional)** | Auth + machine pool assignment + signed-token handoff. NEVER on media path. | Teraguchi's broker matches this pattern exactly. |

### How Each Reference System Structures This

**Sunshine + Moonlight** (GameStream-compatible, open-source):
- **Discovery/pairing:** NVHTTP (HTTP/HTTPS on port 47984/47989).
- **Session init:** RTSP for stream negotiation (SDP-like descriptor).
- **Control:** ENet over UDP 47999, encrypted AES-GCM-128. Reliable, ordered, small messages.
- **Video:** RTP over UDP 47998, unencrypted (performance-first choice — Moonlight's legacy). H.264/HEVC/AV1.
- **Audio:** RTP over UDP 48000, Opus.
- **Capture:** Platform backends (NvFBC, DXGI, KMSGrab, WlrDisplay, SCK). 2026 added Vulkan Video encode with zero-copy DMA-BUF.
- **Keyframe recovery:** `request_idr_frame()` forces `AV_PICTURE_TYPE_I` on next frame. Client requests IDR over the control channel when it detects loss.
- **Session persistence:** Process stays up across client disconnects; stream can be resumed.

**Parsec** (proprietary, closed-source):
- **BUD protocol:** Custom UDP over DTLS 1.2. Adds TCP-like reliability with "highly tuned congestion control." 97% NAT traversal success claimed. 7ms added latency on LAN.
- **Single-channel multiplex:** Media + control + input share BUD, with application-level prioritization.
- **Lesson for Teraguchi:** Parsec built their own protocol because WebRTC was "too complex, not low-latency enough" and plain UDP lacked reliability. QUIC did not exist in production form when BUD shipped. In 2026 this case is weaker — QUIC gives 80% of BUD for zero protocol code.

**NICE DCV / Amazon DCV** (commercial, high-end HPC/VFX):
- **Transport:** QUIC (UDP) by default, WebSocket (TCP) fallback if QUIC blocked.
- **Color:** 4:4:4 chroma subsampling, color-accurate, high-latency-tolerant (100ms+ WANs).
- **Capture:** Pixels not geometry — security-by-design vs. VNC-style framebuffer protocols.
- **Session persistence:** Fully independent of client connections; `dcv-session-manager` owns session lifecycle.
- **Lesson for Teraguchi:** QUIC-first with WS fallback is the 2026 production pattern. Teraguchi's existing `QUICTransportServer` is on the right path — promote it from "experimental" to "preferred."

**HP Anyware (PCoIP)** (proprietary, end-of-life 2028):
- **Transport:** UDP primary with TCP fallback. Proprietary FEC and retransmit logic.
- **Color:** 4:4:4 and 10-bit HDR supported.
- **Session persistence:** Classic PCoIP feature — session continues independent of client.
- **Lesson for Teraguchi:** Every feature PCoIP made standard in VFX (4:4:4, 10-bit, session-persistence, color-accurate) is a **table-stakes requirement**, not a nice-to-have.

---

## Teraguchi's Existing Architecture — Agree/Disagree Audit

| Area | Existing Design | Verdict | Rationale |
|------|----------------|---------|-----------|
| Three-tier: client / broker / server | Client, optional broker, server | ✅ **Keep** | Matches every production system. Broker-optional is correct for small studios. |
| Shared `common/` protocol library | Message dataclasses + keymap + transport shared by all three | ✅ **Keep** | Avoids two-implementations-drift bug class. Industry-standard. |
| TLS WebSocket for control + optional UDP for media | Hybrid, with QUIC experimental | ⚠️ **Evolve** | Right idea but two paths to maintain. Promote QUIC to primary, keep WS+UDP as LAN-only fallback. See "Transport Convergence" below. |
| Per-user X11 session isolation via PAM (Linux) | `XSessionManager` spawns Xvfb/Xorg per authenticated user | ✅ **Keep** | This is the **correct** model. xrdp uses the same pattern. Industry standard for multi-user Linux. |
| Single shared session on macOS | SCK captures the logged-in user's desktop | ✅ **Keep for v1** | macOS has no equivalent of "spawn a new GUI session per user." Document as intentional; not a bug. |
| `SessionRuntime` persists across disconnects | Runtime stays alive, clients dict changes | ✅ **Keep** | PCoIP-style session persistence is table stakes. Don't change. |
| Backends selected by `platform_backends.py` at import time | Duck-typed, OS-dispatched | ⚠️ **Evolve** | Right idea but contracts are informal. Promote to `typing.Protocol` ABC and remove Linux assumptions leaking through (see CONCERNS.md "Platform Parity Gaps"). |
| FFmpeg subprocess for encode | Stdin pipe feeds raw BGRA | ⚠️ **Evolve later** | Works today. Subprocess isolates encoder crashes (good). But stride-strip copy per frame is a waste (see CONCERNS performance). v1 OK, v2 move to direct NvEnc SDK / VideoToolbox API. |
| PyAV for decode | Hardware decode via CUDA/VT/VAAPI | ✅ **Keep** | Industry-standard Python FFmpeg wrapper. |
| HMAC-SHA256 broker tokens | 60s TTL, one-time-use | ✅ **Keep** | Correct pattern. Minor hardening (AEAD wrap) is optional, not required for v1. |
| JPEG dirty-rect fallback alongside H.264 | Two parallel encode paths | ❌ **Delete** | FFmpeg is a hard installer dep already. Dead-weight code path (CONCERNS.md). Remove in v1. |
| `send_queue maxsize=30` per client | 1-second backlog cap | ⚠️ **Evolve** | Bound is right; recovery is wrong. On drop must request IDR; currently doesn't. Bug fix, not redesign. |
| Local-auth (JSON user db) alongside PAM | Legacy path | ❌ **Delete or gate** | Weak hash (SHA-256, no bcrypt), no rate limit. Deprecate or gate behind `--dev-mode` for v1. |

---

## Recommended Architecture for v1

### High-Level Block Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      CLIENT (PySide6, macOS)                              │
│                                                                           │
│  ┌─────────────┐        ┌──────────────────────────────────┐             │
│  │  MainWindow │        │         SessionFSM (NEW)          │             │
│  │  + Tabs +   │◀──────▶│  State: Disconnected/Handshaking/ │             │
│  │  Bookmarks  │ signals│  Streaming/Degraded/Reconnecting  │             │
│  └──────┬──────┘        └──────────────┬────────────────────┘             │
│         │                              │                                  │
│  ┌──────▼──────┐                ┌──────▼─────────┐                       │
│  │RemoteViewer │                │  ClientProtocol│                       │
│  │ (input cap, │◀──────────────▶│ (IO thread,    │                       │
│  │  render)    │     bridge     │  WS/QUIC/UDP)  │                       │
│  └──────┬──────┘                └──────┬─────────┘                       │
│         │                              │                                  │
│  ┌──────▼──────┐  ┌──────────┐  ┌──────▼────┐  ┌────────────┐           │
│  │DecoderMgr   │  │AudioSink │  │JitterBuf  │  │  Health    │           │
│  │(PyAV HW)    │  │(QAudio)  │  │(UDP recv) │  │  Overlay   │           │
│  └─────────────┘  └──────────┘  └───────────┘  └────────────┘           │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
                                  │  QUIC (primary) / WSS+UDP (fallback)
                                  │  ── Channels (stream multiplexed):
                                  │     ┌─ input (reliable, priority 0)
                                  │     ├─ control (reliable, priority 1)
                                  │     ├─ audio (unreliable+FEC, priority 2)
                                  │     └─ video (unreliable+FEC, priority 3)
                                  │
┌─────────────────────────────────▼────────────────────────────────────────┐
│                      SERVER (Rocky Linux / macOS)                         │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │               ConnectionAcceptor (asyncio)                       │    │
│  │  - QUIC endpoint (aioquic)                                       │    │
│  │  - WSS endpoint (websockets) -- LAN fallback                     │    │
│  └────────────────────┬────────────────────────────────────────────┘    │
│                       │                                                   │
│  ┌────────────────────▼────────────────────────────────────────────┐    │
│  │               ClientSessionFSM (NEW, one per WS)                  │    │
│  │  States: Connecting → Authenticating → Capabilities → Streaming  │    │
│  │          → Draining → Disconnected                                │    │
│  │  Owns: send queues, health state, transport handle                │    │
│  └────────────────────┬────────────────────────────────────────────┘    │
│                       │                                                   │
│                       ▼                                                   │
│  ┌───────────────────────────────────────────────────────────────┐      │
│  │         SessionRuntime (one per authenticated user)             │      │
│  │                                                                 │      │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │      │
│  │  │  Capture     │──▶│   Encoder   │──▶│ Broadcaster  │         │      │
│  │  │  Pipeline    │  │   Pipeline   │  │  (per-client)│         │      │
│  │  │  (bounded Q) │  │  (bounded Q) │  │   queues     │         │      │
│  │  └──────▲───────┘  └──────────────┘  └──────────────┘         │      │
│  │         │                                                      │      │
│  │  ┌──────┴───────┐  ┌──────────────┐  ┌──────────────┐         │      │
│  │  │   Input      │  │  Clipboard   │  │ Audio Capture│         │      │
│  │  │   Injector   │  │  Cursor      │  │              │         │      │
│  │  │              │  │  USB/IP      │  │              │         │      │
│  │  └──────────────┘  └──────────────┘  └──────────────┘         │      │
│  └──────────────────────────┬────────────────────────────────────┘      │
│                             │                                             │
│  ┌──────────────────────────▼────────────────────────────────────┐      │
│  │        Platform Backends (typing.Protocol ABC, NEW)            │      │
│  │  Linux: screen_capture.py / input_injector.py / clipboard.py    │      │
│  │  macOS: mac_screen_capture.py / mac_input_injector.py / ...     │      │
│  │  (Explicit Protocol typing; no more Linux assumptions leaking)  │      │
│  └────────────────────────────────────────────────────────────────┘      │
│                                                                           │
│  ┌──────────────────────────────────────────────────────────────┐        │
│  │       Telemetry (structlog JSON → optional OTLP export)        │        │
│  │       HTTP /health endpoint on same port (already exists)      │        │
│  └──────────────────────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────────────────────┘

              ┌──────────────────────────────────────────────┐
              │      BROKER (optional, paused for v1)         │
              │  Auth → Pool → HMAC token → redirect          │
              │  Stays in tree; no v1 hardening.              │
              └──────────────────────────────────────────────┘
```

### What Changes from Today's Code

**New modules:**

```
common/
└── session_fsm.py              # NEW — shared FSM definitions and transition table
                                 #   Used by client and server; states serialize to
                                 #   wire on health ping/pong so both sides agree.

server/
├── session_runtime.py          # NEW (extracted from server/main.py)
├── client_session.py           # NEW (extracted from server/main.py)
├── connection_acceptor.py      # NEW (extracted from server/main.py)
├── pipelines/
│   ├── __init__.py
│   ├── capture.py              # NEW — wraps screen_capture.py with bounded queue
│   ├── encoder.py              # NEW — wraps VideoEncoder with bounded queue
│   └── broadcaster.py          # NEW — per-client send queues + drop policy
├── backends.py                 # NEW — typing.Protocol ABC definitions
└── telemetry.py                # NEW — structlog + /metrics endpoint

client/
├── session_fsm.py              # NEW — client-side FSM driver (wraps common FSM)
└── connection_supervisor.py    # NEW — owns reconnect policy, hides from Session
```

**Existing modules that get smaller:**
- `server/main.py` → CLI + wiring only (currently 1,191 lines, target <200)
- `server/session_manager.py` → Xvfb/Xorg spawn only (currently 1,317 lines, target <600 by extracting helpers)
- `client/protocol.py` → wire protocol only; reconnect logic moves to `connection_supervisor.py`
- `client/session.py` → tab/UI glue; FSM moves to `session_fsm.py`

**Existing modules unchanged:**
- `common/messages.py` (plus new additions for state-sync)
- `common/hybrid_transport.py` / `common/udp_transport.py` / `common/quic_transport.py`
- `common/jitter_buffer.py`
- `common/keymap.py`
- All platform-backend concrete classes (they just implement the new Protocol)

---

## Architectural Patterns

### Pattern 1: Bounded Queue with Explicit Drop Policy

**What:** Every pipeline stage is a `asyncio.Queue(maxsize=N)` with documented semantics for what happens on full.

**When to use:** Everywhere data flows between stages at different rates — capture→encoder, encoder→broadcaster, broadcaster→per-client send.

**Trade-offs:**
- Pros: Natural backpressure. Clear semantics. Easy to reason about under load.
- Cons: Drop policy must be explicit and correct or you ship stream corruption.

**Teraguchi-specific drop-policy rules:**

| Queue | Size | On Full | Recovery |
|-------|------|---------|----------|
| Capture → Encoder | 2 frames | Drop oldest (capture always wins) | No recovery needed — encoder sees skip, produces P-frame normally |
| Encoder → Broadcaster | 3 frames | Drop oldest | Same — broadcaster sees skip |
| Broadcaster → per-client send | **4 frames** (down from 30) | Drop oldest **AND request next IDR from encoder** | Client sees gap for ~1 frame; IDR arrives within 100ms; stream recovers |
| Input (client→server) | 64 events | **Block producer** (input must never drop) | N/A |

**Current bug (CONCERNS.md):** `send_queue maxsize=30` drops frames but never requests IDR. Next P-frame reference is broken; stream stalls visually until GOP boundary (2s). Fix is 5 lines of code in `_enqueue_frame` / `_on_encoded_frame`.

**Example:**

```python
# server/pipelines/broadcaster.py
class PerClientBroadcaster:
    def __init__(self, client_id: str, encoder_ref: VideoEncoder):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=4)
        self._encoder = encoder_ref
        self._drops_since_keyframe = 0

    async def enqueue(self, frame: EncodedFrame) -> None:
        if frame.is_keyframe:
            self._drops_since_keyframe = 0
            # Clear the queue — new IDR supersedes stale P-frames
            while not self.queue.empty():
                self.queue.get_nowait()
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Drop OLDEST, not newest — favor freshness
            self.queue.get_nowait()
            self.queue.put_nowait(frame)
            self._drops_since_keyframe += 1
            # RECOVERY: request IDR on first drop of the streak
            if self._drops_since_keyframe == 1:
                self._encoder.request_keyframe()
            HEALTH.record_frame_dropped(self.client_id)
```

### Pattern 2: Explicit Finite State Machine Per Session Side

**What:** Both client and server model the connection as a FSM with a closed set of states and guarded transitions. State is serialized into health pings so both sides can detect disagreement.

**When to use:** Any long-lived connection where reconnect, encoder reconfig, or topology change can happen mid-stream. I.e. every remote desktop ever.

**Trade-offs:**
- Pros: Eliminates the "half-connected" class of bugs. Makes reconnect logic explicit. Test-friendly.
- Cons: Upfront design work. Must keep client/server FSMs in sync over protocol versions.

**State table:**

| Client State | Server (per-client) State | Transition Trigger | Entry Action |
|--------------|---------------------------|-------------------|--------------|
| `Disconnected` | (none — not yet connected) | User clicks Connect | Reset all buffers, clear old video codec state |
| `Handshaking` | `Connecting` | TLS handshake OK | Start auth timer (30s) |
| `Authenticating` | `Authenticating` | AuthRequest received | Prompt user or use saved credential |
| `CapabilityExchange` | `CapabilityExchange` | Auth success | Negotiate codecs, monitors, UDP/QUIC |
| `Streaming` | `Streaming` | ServerHello + first frame received | Start render loop, show viewer widget |
| `Degraded` | `Streaming` (server unaware) | Health: RTT > threshold or frame drops > threshold | Show degraded UI, lower quality automatically |
| `Reconnecting` | `Draining` | WS/QUIC closed unexpectedly | Retry with exponential backoff, show "Reconnecting" overlay |
| `Disconnected` | `Disconnected` | User quit or max retries | Free all resources |

**Rule:** No code path outside `session_fsm.py` may directly mutate the state variable. All transitions go through `fsm.transition(Event)` which enforces the allowed-transitions matrix.

**Tool choice:** Either `python-statemachine` (v3.0+, native async support, used in production elsewhere) or a hand-rolled `Enum` + dispatch table. Hand-rolled is preferred for Teraguchi — the state set is small (~7 states), the async story avoids library bugs, and it's testable without library mocking.

**Example (hand-rolled sketch):**

```python
# common/session_fsm.py
from enum import Enum, auto
from dataclasses import dataclass

class SessionState(Enum):
    DISCONNECTED = auto()
    HANDSHAKING = auto()
    AUTHENTICATING = auto()
    CAPABILITY_EXCHANGE = auto()
    STREAMING = auto()
    DEGRADED = auto()
    RECONNECTING = auto()

@dataclass(frozen=True)
class Transition:
    from_state: SessionState
    event: str
    to_state: SessionState

# Closed set — if it's not here, it's not allowed
TRANSITIONS = {
    (SessionState.DISCONNECTED, "connect_requested"): SessionState.HANDSHAKING,
    (SessionState.HANDSHAKING, "tls_ok"): SessionState.AUTHENTICATING,
    (SessionState.AUTHENTICATING, "auth_ok"): SessionState.CAPABILITY_EXCHANGE,
    (SessionState.CAPABILITY_EXCHANGE, "server_hello"): SessionState.STREAMING,
    (SessionState.STREAMING, "health_degraded"): SessionState.DEGRADED,
    (SessionState.DEGRADED, "health_restored"): SessionState.STREAMING,
    (SessionState.STREAMING, "transport_lost"): SessionState.RECONNECTING,
    (SessionState.DEGRADED, "transport_lost"): SessionState.RECONNECTING,
    (SessionState.RECONNECTING, "tls_ok"): SessionState.AUTHENTICATING,
    (SessionState.RECONNECTING, "max_retries_exceeded"): SessionState.DISCONNECTED,
    # ... etc
}

class SessionFSM:
    def __init__(self):
        self.state = SessionState.DISCONNECTED
        self._listeners: list[Callable[[SessionState, SessionState], None]] = []

    def dispatch(self, event: str) -> SessionState:
        key = (self.state, event)
        if key not in TRANSITIONS:
            # Invalid transition — log LOUDLY, don't silently drop
            logger.error("Invalid FSM transition: %s + %s", self.state, event)
            raise InvalidTransition(self.state, event)
        prev, self.state = self.state, TRANSITIONS[key]
        for listener in self._listeners:
            listener(prev, self.state)
        return self.state
```

### Pattern 3: QUIC-Primary Transport with WS+UDP Fallback

**What:** Promote `QUICTransportServer` from experimental to the production default. Keep WS+UDP as the LAN-only fallback for environments where QUIC is blocked (rare) or the client is on a LAN where TCP+UDP hole-punching is already free.

**When to use:** WAN, Tailscale overlay, any path with NAT/firewall between client and server. Which is almost always.

**Why now (vs. Parsec BUD in 2018):**

1. **QUIC got connection migration.** WiFi→cellular or Tailscale re-routing (changing network interfaces) does not drop the connection. QUIC's connection ID decouples session identity from IP:port. Parsec had to build this manually into BUD. ([Internet Society](https://pulse.internetsociety.org/blog/how-quic-helps-you-seamlessly-connect-to-different-networks))
2. **QUIC ships in every OS.** HTTP/3 rollout means QUIC goes through every middlebox. UDP 443 is now as unblocked as TCP 443.
3. **Media-over-QUIC (MoQ) ships at NAB 2026.** ~1s latency demos, OBS plugins in early access, Safari 26.4 WebTransport support. The ecosystem is arriving. ([Fastly](https://www.fastly.com/blog/media-over-quic-can-streaming-finally-have-both-scale-and-low-latency), [antmedia](https://antmedia.io/webrtc-vs-moq-media-over-quic-ant-media-server/))
4. **NICE DCV's default is QUIC since 2022.** ([AWS Gametech blog](https://aws.amazon.com/blogs/gametech/stream-remote-environment-nice-dcv-quic-udp-4k-monitor-60-fps/))
5. **Teraguchi already has `aioquic` integrated.** The code exists; it's marked experimental; the hardening work is the same-sized chunk as hardening the custom UDP path.

**Trade-offs:**

- Pros: One protocol, one code path. Connection migration free. TLS 1.3 baked in. Stream prioritization built into the protocol. Datagrams for unreliable media + streams for reliable control in one connection.
- Cons: `aioquic` is Python-only and less battle-tested than C implementations (msquic, quiche). Python GIL is a soft ceiling on throughput — but the bottleneck for Teraguchi is encoder/capture, not transport.
- Pros (specific to Teraguchi's FFmpeg-subprocess pipeline): QUIC datagrams are packet-oriented, perfect for passing MTU-sized NAL-unit fragments without app-layer fragmentation logic.

**Channel mapping:**

| Channel | QUIC Stream or Datagram? | Reliability | Priority |
|---------|--------------------------|-------------|----------|
| Input (client→server) | Reliable bidi stream | Yes | Highest |
| Control (both ways — auth, hello, health, clipboard, quality) | Reliable bidi stream | Yes | High |
| Video | Unreliable datagram, FEC for key frames | No (but FEC) | Normal |
| Audio | Unreliable datagram, FEC | No (but FEC) | High (small packets, audible artifacts) |
| File transfer | Reliable unidirectional stream | Yes | Low |

**WS+UDP fallback:** If QUIC handshake fails after 3s on first connect, fall back to WSS + UDP media on the same port (existing hybrid transport). LAN deployments can also opt out of QUIC via config if they want the lower CPU overhead.

### Pattern 4: Platform Backends as `typing.Protocol` ABCs

**What:** Promote the existing duck-typed backend contracts in `server/platform_backends.py` to formal `typing.Protocol` classes. Run `mypy --strict` over the server package.

**When to use:** When you have multiple implementations of the same interface that must stay in sync (Linux vs macOS vs future Windows).

**Trade-offs:**
- Pros: Typechecker catches drift at develop-time. New developers learn the contract from the ABC, not from reading both backends. Future Windows backend gets a spec.
- Cons: Python `Protocol` is structural, not nominal — runtime behavior unchanged. Pure typing discipline.

**Example:**

```python
# server/backends.py
from typing import Protocol, runtime_checkable
import numpy as np

@runtime_checkable
class ScreenCaptureBackend(Protocol):
    width: int
    height: int

    def capture_raw_bgra(self) -> np.ndarray: ...
    def list_monitors(self) -> list[MonitorInfo]: ...
    def detect_hotplug(self) -> bool: ...
    def switch_monitor(self, monitor_id: int) -> None: ...
    def invalidate(self) -> None: ...
    def reinit(self, width: int, height: int) -> None: ...
    def close(self) -> None: ...

@runtime_checkable
class InputInjectorBackend(Protocol):
    def handle_message(self, msg: dict) -> None: ...
    def reset_modifiers(self) -> None: ...

@runtime_checkable
class ClipboardBackend(Protocol):
    available: bool

    def get_clipboard(self) -> str | None: ...
    def set_clipboard(self, text: str) -> None: ...
    def start_monitoring(self, callback: Callable[[str], None]) -> None: ...
    def stop(self) -> None: ...
```

### Pattern 5: Connection Supervisor (Client-Side Reconnect Logic)

**What:** Dedicated object that owns reconnect decisions. `Session` delegates transport lifecycle to `ConnectionSupervisor`, which drives the client-side FSM.

**When to use:** When reconnect logic is non-trivial (it always is — exponential backoff, jitter, max retries, user-facing "reconnecting" UI, session-token reuse).

**Trade-offs:**
- Pros: Testable in isolation. Single source of truth for "should we retry?". Makes the client FSM trivial.
- Cons: One more object. But it's justified because the alternative is sprinkling reconnect logic across `Session`, `ClientProtocol`, and `MainWindow`.

---

## Data Flow (Explicit)

### Video Path (Capture → Encode → Transmit → Decode → Display)

```
1. [Capture thread, server]
   ScreenCapture.capture_raw_bgra() → np.ndarray(H, W, 4)
   ↓ put_nowait into capture_queue (maxsize=2, drop-oldest)

2. [Encoder thread, server]
   frame = capture_queue.get() (or drop/skip)
   VideoEncoder.feed_frame(frame) → FFmpeg stdin
   ↓ FFmpeg produces NAL units on stdout
   NALParser.read() → (data: bytes, is_keyframe: bool)
   ↓ encode_video_header() adds 10-byte header
   ↓ put into per-client broadcaster queues (maxsize=4, drop-oldest + request-IDR-on-drop)

3. [Send loop, per client, server]
   frame_bytes = broadcaster_queue.get()
   transport.send_media(frame_bytes, channel=VIDEO)
   ↓ QUIC datagram or UDP (1400-byte fragmentation) or TCP WS

4. [Receive loop, client]
   datagram = transport.recv()
   ↓ jitter_buffer.insert(datagram, sequence_number)
   [periodic] reassembled_frame = jitter_buffer.pop_ready()
   ↓ decode_video_header() strips header
   DecoderManager.decode(payload) → QImage

5. [Qt render, client]
   RemoteViewer.set_frame(qimage)
   QWidget.update() → paintEvent → blit

Target end-to-end latency budget (LAN, Mac→Rocky NVIDIA):
  Capture (NvFBC zero-copy):       1ms
  Encode (NVENC ultra-low-latency): 4ms
  Pipeline hops + queue wait:       2ms
  Transport (LAN RTT/2):            1ms
  Decode (VideoToolbox HW):         3ms
  Jitter buffer (minimal):          4ms
  Qt paint + compositor:            5ms
  ─────────────────────────────────────
  TOTAL:                           20ms  ← sub-20ms goal is achievable

  WAN (Tailscale) adds RTT/2 (~10-30ms) and needs larger jitter buffer (~15-25ms).
  Budget: 50-70ms WAN is realistic; 40ms aspirational.
```

**Evidence for the budget:**
- Parsec claims 7ms added on LAN. ([Parsec medium](https://medium.com/parsec/a-networking-protocol-built-for-the-lowest-latency-interactive-game-streaming-1fd5a03a6007))
- Research paper: 17-25ms MTP for 4K on high-end workstation over wired Ethernet. ([dl.acm.org paper on ultra-low-latency coding](https://dl.acm.org/doi/10.1145/3512342))
- Cloud VR target: <20ms MTP. ([GSMA Cloud VR whitepaper](https://www.gsma.com/solutions-and-impact/technologies/networks/gsma_resources/cloud-ar-vr-whitepaper-2/))
- NICE DCV claims handling 100ms+ WAN latency gracefully — not the same as minimizing it, but a floor for WAN expectations.

### Input Path (reliable, must never drop)

```
1. [Qt event loop, client]
   RemoteViewer.mouseMoveEvent(QMouseEvent) → normalized (x,y)
   ↓ emits Qt signal

2. [Session signal slot, client]
   ClientProtocol.send_mouse_move(x, y)
   ↓ serialized to JSON {"type": "mouse_move", "x": ..., "y": ...}
   ↓ put on INPUT channel (reliable, priority 0)

3. [Transport, client→server]
   QUIC bidi stream, or TCP WebSocket frame
   GUARANTEE: ordered, no drops

4. [Receive loop, server]
   ClientSession._recv_loop() → parse_message(raw)
   ↓ SessionRuntime.handle_input(msg)

5. [Injector, server]
   XTestInputInjector.handle_message(msg) OR MacInputInjector.handle_message(msg)
   ↓ XTestFakeMotionEvent or CGEventPost
   ↓ underlying X server or macOS event loop
```

### Control Path (auth, capability exchange, clipboard, quality, health)

Same channel as input — reliable. Isolated per-message; no state between messages except what the FSMs track.

### Clipboard / Cursor / USB — Separate Async Tasks

Each runs in its own asyncio task, independent of video pipeline. A stuck cursor poll on macOS does not stall capture.

---

## State Machine: Session Lifecycle (Block Diagram)

```
CLIENT:

  [App start]
       │
       ▼
  ┌──────────────┐
  │ DISCONNECTED │◀───────────────────────────┐
  └──────┬───────┘                             │
         │ User clicks Connect                 │
         ▼                                     │
  ┌──────────────┐                             │
  │ HANDSHAKING  │──── QUIC/TLS fails ─────────┤
  └──────┬───────┘                             │
         │ TLS handshake OK                    │
         ▼                                     │
  ┌───────────────┐                            │
  │AUTHENTICATING │─── AuthResult.failed ──────┤
  └──────┬────────┘   or timeout               │
         │ AuthResult.ok                       │
         ▼                                     │
  ┌───────────────────┐                        │
  │CAPABILITY_EXCHANGE│── protocol mismatch ───┤
  └──────┬────────────┘                        │
         │ ServerHello received                │
         ▼                                     │
  ┌──────────────┐  health_degraded  ┌────────┴──────┐
  │  STREAMING   │──────────────────▶│   DEGRADED    │
  └──────┬───────┘◀──health_restored─└────────┬──────┘
         │                                    │
         │ transport_lost  ┌──────────────────┘
         │                 │ transport_lost
         ▼                 ▼
  ┌─────────────────────────────┐
  │       RECONNECTING          │── max_retries ─▶ DISCONNECTED
  └─────────┬───────────────────┘
            │ tls_ok + auth_ok
            ▼
         [resume at CAPABILITY_EXCHANGE or STREAMING depending on server-side
          SessionRuntime persistence]

SERVER (per client):

  ┌────────────┐
  │ CONNECTING │
  └──────┬─────┘
         │ WS/QUIC established, TLS done
         ▼
  ┌───────────────┐
  │AUTHENTICATING │── fails ──▶ send AuthResult(success=False) → close WS
  └──────┬────────┘
         │ PAM/token success
         ▼
  ┌───────────────────┐
  │CAPABILITY_EXCHANGE│── client bye ─▶ DRAINING
  └──────┬────────────┘
         │ ClientHello received + echoed ServerHello
         ▼
  ┌──────────────┐
  │  STREAMING   │── socket error / timeout ──▶ DRAINING
  └──────┬───────┘
         │ graceful close
         ▼
  ┌──────────────┐
  │   DRAINING   │── drain complete ──▶ [remove from runtime.clients]
  └──────────────┘    [SessionRuntime stays alive]
```

**Session persistence rule:** `SessionRuntime` lifetime ≠ any `ClientSession` lifetime. Runtime persists across disconnects. Reconnect that arrives with a valid re-auth re-attaches to the existing runtime — stream resumes instantly from next keyframe.

---

## Transport Layer Design (Detailed)

### Recommendation: QUIC primary, WS+UDP fallback

See Pattern 3 above. Primary rationale: NICE DCV (the PCoIP-class production incumbent) defaults to QUIC; Media-over-QUIC ships in 2026; Teraguchi already has `aioquic` integrated.

### Jitter Buffer Sizing

Teraguchi already has `common/jitter_buffer.py` — needs tuning validation, not redesign.

**Target:**
- LAN: 4ms fixed, or dynamic with 95th-percentile one-way jitter over 250ms window.
- WAN (Tailscale): 15-25ms dynamic. Measure jitter over 500ms window, set buffer = 1.0-1.5× the 95th-percentile.

**Source:** [BlogGeek.me WebRTC jitter buffer guide](https://bloggeek.me/webrtcglossary/jitter-buffer/), [PulseGeek low-latency buffer analysis](https://pulsegeek.com/articles/optimal-buffer-size-for-low-latency-streaming-explained/).

### FEC Strategy

**Keyframes (IDR):** Always send FEC repair packets. An IDR is the whole stream's correctness anchor. Losing one stalls until next IDR.

**P-frames:** No FEC. Loss recovers via next IDR on request. IDRs requested on drop-detected (see Pattern 1).

**Audio:** Always FEC (voice/audio packets are small, cheap to protect, audible artifacts from loss).

**Recommendation:** Use Reed-Solomon or XOR parity packets. Lightweight; integrates with existing UDP transport at the header level.

### Adaptive Bitrate

Simple rules-based adaptive bitrate is sufficient for v1:

```
if rtt_ms > 100 or loss_pct > 2%:
    step_down_quality()  # reduce target bitrate 25%, lower fps from 60→30 if needed
elif rtt_ms < 30 and loss_pct < 0.1% and current_bitrate < user_ceiling:
    step_up_quality()    # increase target bitrate 10%

Hysteresis: require 5 seconds of stable measurement before step-up.
```

Teraguchi already has `HealthMonitor` tracking RTT/FPS/bandwidth. Wire it into `QualitySettings` adjustments. No ML, no heavy congestion control — rule-based is fine at small-studio scale.

### Late-Binding Intra Refresh (Future)

SVT-AV1 low-delay mode supports periodic intra-refresh (a few columns per frame are intra-coded, rather than one big IDR per GOP). This eliminates the IDR-spike bandwidth problem. Worth investigating for v2; overkill for v1.

---

## Configuration Surface

### Recommendation: TOML, three levels, not hot-reload

**Formats:** TOML for everything. Not YAML (too complex for small studios, whitespace sensitivity). Not JSON (no comments). TOML has a Python stdlib parser (`tomllib`) since 3.11.

**Three levels, in priority order:**

1. `--flag` CLI arguments (highest, ephemeral)
2. `~/.config/teraguchi/client.toml` (per-user, persistent)
3. `/etc/teraguchi/server.toml` (system defaults, set by admin)

**No hot-reload for v1.** Reason: remote desktop sessions are long-lived and performance-critical. A config change mid-session is an edge case whose value does not justify the complexity (reloading + rejecting invalid + notifying running sessions). Instead: change config → restart server → sessions reconnect. Restart should be fast (<2s) and clients should reconnect transparently (QUIC migration + session-resume).

**Critical small-studio UX points:**

- Installer generates `/etc/teraguchi/server.toml` with sensible defaults. Admin rarely edits it.
- Client saves bookmarks and per-connection quality in `~/.config/teraguchi/client.toml`.
- CLI `--help` lists every setting with default. No hidden-knob/config-file-only options.
- Validation at startup: fail loud on missing TLS cert paths, fail loud on invalid codec names.

**Anti-pattern (PCoIP over-engineered this):** No admin-GPO / Active-Directory-policy layer. No Kubernetes ConfigMap integration. No YAML templating. This is a 1-10 person studio tool.

---

## Telemetry / Observability

### Recommendation: structlog JSON → stdout; optional OpenTelemetry log bridge

**Logging:** Replace stdlib `logging` string formatting with `structlog`. Emit JSON to stdout. systemd journal captures it on Linux; `~/Library/Logs/Teraguchi/server.log` on macOS.

**Why:**
- Small studios don't run Grafana/Tempo. But someone eventually will, and structured JSON is a low-cost bet.
- `structlog` has 2025-era OpenTelemetry bridges. ([johal.in structlog+OTEL 2026](https://johal.in/structlog-json-logs-middleware-opentelemetry-python-2026/), [Dash0 structlog guide](https://www.dash0.com/guides/python-logging-with-structlog))
- `contextvars` binding gives per-session context across asyncio tasks without thread-local hacks.

**Core log event schema:**

```json
{
  "timestamp": "2026-04-18T12:34:56.789Z",
  "level": "info",
  "logger": "teraguchi.server.session",
  "event": "client.connected",
  "session_id": "abc123",
  "username": "randy.mcentee",
  "client_ip": "100.64.0.5",
  "transport": "quic",
  "codec": "hevc",
  "monitor_count": 2
}
```

**Metrics:**

- FPS, RTT, bandwidth, dropped frames per client — already in `HealthMonitor`.
- Expose on `/metrics` HTTP endpoint in Prometheus text format (already have `/status`, add `/metrics` alongside).
- Broker can scrape these; solo studios can point Prometheus at them for a Grafana dashboard.
- Export as OTLP only if `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Default: off.

**Health endpoint:**

- `/health` — liveness (200 if process is up).
- `/ready` — readiness (200 if can accept sessions).
- `/status` — current server state (exists today; keep).
- `/metrics` — Prometheus (new).

**Critical: debugging a stuck session remotely.**

- Every log line carries `session_id` bound via `contextvars`.
- Client can request `/debug/session/{session_id}` → server returns FSM state, queue sizes, encoder state, last N log lines. Helps artist → studio IT diagnose without ssh access.
- FSM invalid-transition errors always log with full state context — "half-connected" bugs surface immediately.

---

## Upgrade / Deployment

### Recommendation: In-place binary replacement + version-tolerant protocol negotiation

**Shipping a new server version to 10 studio machines:**

1. Publish signed RPM / macOS installer on GitHub Releases (CI driven by git tag).
2. Admin runs `dnf upgrade teraguchi-server` (Rocky) or `brew upgrade teraguchi-server` (macOS). Or Ansible. All supported.
3. Systemd: `systemctl restart teraguchi-server` (Linux) or `launchctl kickstart` (macOS).
4. Server drains existing connections (see Pattern: graceful shutdown). Draining = stop accepting new, let existing run for up to 60s, then force-close. `SessionRuntime` lifetime independent of connections, so Xvfb sessions survive the restart.
5. Clients get `transport_lost` event, enter `RECONNECTING`, reconnect within 1-3s. User sees brief "Reconnecting..." overlay; session resumes.

**Restart-on-idle (alternative, no client disruption):**

```bash
# systemd drop-in
ExecStartPre=/bin/sh -c 'while pgrep -f "teraguchi-session" >/dev/null; do sleep 30; done'
```

Not recommended for v1 — adds complexity and means a session with a connected client never updates until logout. Prefer the drain-and-reconnect pattern.

### Client/Server Version Skew Tolerance

**The critical rule:** Do not force both sides to update in lockstep.

**Mechanism:**

- Protocol version is a `(major, minor)` pair embedded in `ClientHello` and `ServerHello`.
- `major` bump = breaking change. Negotiation fails. Client shows "server too new/old" error.
- `minor` bump = additive change. Both sides negotiate to `min(client_minor, server_minor)`. Newer feature simply not advertised.
- Every new message type is behind a capability flag (e.g., `caps.bulk_file_pull = true`). Missing flag = feature not available; both sides degrade gracefully.

**Example:**

```python
# common/messages.py
PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 3  # increment when adding optional features

@dataclass
class ServerHelloMsg:
    protocol_major: int
    protocol_minor: int
    capabilities: list[str]   # e.g. ["h265", "av1", "bulk_pull", "10bit", ...]
    # ...
```

**Teraguchi today:** Already has `ClientHelloMsg` / `ServerHelloMsg` with capability exchange. Needs (a) explicit protocol version fields, (b) capability flags, (c) documented compatibility matrix.

### Distribution Artifacts

- **Linux server:** Signed RPM for Rocky 9.x. Includes systemd unit, Xorg config, uinput setup, NvFBC build hook. `install-server.sh` exists; wrap it in an RPM spec.
- **Linux client:** Not shipped in v1 (out of scope). Code runs; admins can manual-install via pip.
- **macOS server:** Notarized `.pkg` installer. LaunchAgent template. `install-server-macos.sh` exists; wrap it in productbuild/pkgbuild.
- **macOS client:** Signed + notarized `.app` in a `.dmg` or `.pkg`. `build_client.py` is the PyInstaller starting point. Add fastlane/altool for notarization in CI.
- **Windows:** Not v1.

**CI:** GitHub Actions. Matrix build on `macos-14`/`macos-15` runners + `rocky-9` self-hosted runner. Tag-triggered release builds. Sources: [Data-Dive PyInstaller+GitHub Actions](https://data-dive.com/multi-os-deployment-in-cloud-using-pyinstaller-and-github-actions/).

---

## Security Surface (Detailed)

### TLS Cert Management for a Small Studio

**Three deployment patterns:**

1. **Tailscale-native (recommended default).**
   - Tailscale provisions HTTPS certificates via Let's Encrypt for any `*.tailnet-name.ts.net` hostname.
   - `tailscale cert` command on each server generates cert + key.
   - Auto-renewed by Tailscale daemon.
   - Client trusts the Let's Encrypt chain (built-in).
   - Zero admin overhead.
   - Source: [Tailscale TLS certs docs](https://tailscale.com/docs/how-to/set-up-https-certificates), [Tailscale blog](https://tailscale.com/blog/tls-certs).

2. **Self-signed + fingerprint pinning.**
   - Studio IT generates self-signed cert on each server at install time (`install-server.sh --generate-cert`).
   - Client's first connect shows "accept fingerprint?" dialog (TOFU model — SSH-style).
   - Pinned fingerprint stored in bookmarks.
   - Subsequent connects validate against pinned fingerprint.
   - Rotation: user manually re-accepts on cert change.
   - Works offline. No Let's Encrypt dependency.

3. **Corporate CA.**
   - Larger studios with internal CAs provide cert/key.
   - Client has CA cert in system trust store.
   - Standard web PKI validation.

**v1 must support all three.** Tailscale-native is the documented default. Self-signed+pin is the fallback. Corporate CA is the "enterprise use case" stretch goal.

**Current state (CONCERNS.md):** Client TLS verification is **disabled everywhere** (`check_hostname = False`, `verify_mode = ssl.CERT_NONE`). This is the single most urgent security fix. Without it, a MITM on the Tailscale network (or a malicious exit node, or a typo'd hostname resolving to an attacker-controlled box) is invisible.

**v1 must:** Make cert verification ON by default. Add `--trust-fingerprint=SHA256:...` flag for TOFU. Make `--insecure-skip-verify` a loud, scary-named, logged-on-every-connect flag.

### Origin Validation and MITM Protection

- QUIC and TLS 1.3 with proper cert validation = MITM defeated.
- Tailscale's WireGuard layer provides a second encryption envelope for defense-in-depth. Even if TLS was fully broken, attacker needs Tailscale mesh access.

### Replay Attack Resistance on Auth

**Current:** Broker HMAC tokens have 60s TTL + one-time-use. Local-auth and PAM are fresh-session only (no persistent token). Replay is hard.

**v1 hardening:**
- Add a random nonce to `AuthRequest` (server-generated). Client signs nonce with password-derived key or broker token.
- Prevents attacker from replaying an authenticated-session handshake even if they captured it before the TLS layer was fixed.

### Session Token Lifetimes

- Broker token: 60s (current). Keep.
- Per-session cookie (for reconnect): 1 hour, rotated on use. NEW for v1 to support QUIC-migration reconnect without full re-auth.
- Admin UI basic-auth cache: 5min (current). Add per-process pepper (CONCERNS.md recommendation).

### "Direct PAM" Attack Surface vs. Broker-Fronted

**Direct PAM (v1 default):**
- Attack surface: WebSocket endpoint (port 443 typically), TLS 1.3, PAM module. Root-privileged server process.
- Rate limiting: **MISSING today**. Add per-IP and per-username backoff before v1 ship.
- PAM configured correctly = battle-tested attack surface. `pam_unix`, `pam_faillock`, `pam_tally2` all give rate limiting at the PAM layer.
- Recommendation: Configure `/etc/pam.d/teraguchi` to use `pam_faillock` — system-wide brute-force protection without app-layer logic.

**Broker-fronted:**
- Broker sits in front; server accepts only HMAC tokens (and optionally direct-PAM as a backup).
- Attack surface reduced: server's WebSocket endpoint is hit only by clients with a valid broker-signed token.
- Broker does rate-limit auth; compromised broker = compromised all machines (HMAC key).
- v1 keeps broker paused; paths exist in code; direct-PAM is the primary.

---

## Cross-Platform Build Architecture

### Recommendation: GitHub Actions matrix; no hermetic builds; reproducible where it's cheap

**Matrix:**

```yaml
strategy:
  matrix:
    include:
      - target: macos-arm64-client
        runner: macos-15
        artifact: Teraguchi-VERSION-arm64.dmg
      - target: macos-arm64-server
        runner: macos-15
        artifact: teraguchi-server-VERSION-arm64.pkg
      - target: linux-x86_64-server
        runner: rocky-9-x86_64  # self-hosted, DXS lab
        artifact: teraguchi-server-VERSION.rpm
```

**Build steps per artifact:**

1. Checkout + setup Python 3.11 + install deps.
2. Run tests (pytest).
3. PyInstaller for client (`build_client.py`).
4. Codesign (macOS: `codesign -s "Developer ID" --options runtime`).
5. Notarize (macOS: `xcrun notarytool submit`).
6. Staple (macOS: `xcrun stapler staple`).
7. Package (macOS: create-dmg; Linux: `rpmbuild`).
8. Upload artifact.
9. On tag push: attach to GitHub Release.

**No hermetic/reproducible builds for v1.** Reason: Python ecosystem doesn't support reproducible builds well (pip resolver non-determinism, wheel metadata timestamps). Pinning `requirements-*.txt` gets 90% of the reproducibility benefit for 10% of the effort. Revisit for v2.

**Why competitor systems are simpler than Teraguchi here:**

- Sunshine: C++/CMake, single codebase, vcpkg-pinned deps, cross-compile matrix in GitHub Actions. About 200 lines of workflow YAML.
- Parsec: proprietary, private CI. No data.
- NICE DCV: Amazon internal CI. No data.

Teraguchi's Python + Qt + PyObjC + native C helper (`nvfbc_capture`) + FFmpeg subprocess stack is more fragile than C++ to package, but manageable with discipline (lockfile pinning, PyInstaller onedir-bundle mode, explicit `--add-data` for `common/`).

---

## Failure Modes — What Breaks in the Field, and Recovery Pattern

### 1. GPU Driver Reload Mid-Session (Linux NVIDIA)

**What happens:** `nvidia-smi` dies, NvFBC subprocess crashes, Xorg can stall, FFmpeg NVENC encoder errors.

**Recovery pattern:**
- **Isolation:** NvFBC in its own process (already done) — subprocess crash doesn't kill server.
- **Detection:** `NvFBCBackend` reader thread sees EOF on subprocess stdout.
- **Degradation:** Fall back to `mss` or XShm capture (slower but works).
- **Encoder:** Restart FFmpeg subprocess with same params; send IDR on first frame.
- **Client impact:** Brief (~500ms) freeze, then stream resumes. Health overlay shows "GPU hiccup" telemetry.

**Current state:** NvFBC isolation is good. Capture fallback exists. Encoder restart path exists (`_restart_encoder`). What's missing: coordinated recovery — right now a GPU glitch can leave `VideoEncoder` in a half-open state where it won't encode but the streaming loop still calls `feed_frame()`. Need an explicit "recover encoder" state machine on the encoder itself.

**Does NOT survive today:** Full Xorg crash. Requires server process restart. Acceptable for v1 (rare, dramatic event).

### 2. Monitor Unplug / Replug

**What happens:** The Xvfb display doesn't see real monitors; fine. On Xorg+NVIDIA display, hot-plug triggers `detect_hotplug()`. On macOS SCK, the stream's `SCContentFilter` references a display that no longer exists.

**Recovery pattern:**
- **Linux Xvfb/Xorg:** `detect_hotplug` returns True → emit `MONITOR_LIST_UPDATE` to client → client re-renders monitor picker → user re-selects if needed. `ScreenCapture.reinit(w,h)` picks up new geometry.
- **macOS SCK:** `SCStream` delivers error to completion handler → capture loop restarts `SCStream` with new `SCContentFilter` from updated shareable content.
- **Client FSM:** No state change. Capture stutter visible as ~500ms frame stall.

**Current state:** Linux path exists. macOS path not yet implemented (CONCERNS.md: "`detect_hotplug` on macOS unclear"). v1 must implement macOS SCK hotplug handling.

**Test this with:** Unplug a monitor from the Rocky test box during a session; physically plug/unplug a USB-C monitor on the Mac test server.

### 3. Network Path Change (Tailscale Roaming Between Networks)

**What happens:** Client laptop moves from WiFi to cellular (or vice-versa). Tailscale re-establishes the WireGuard tunnel over the new interface. The underlying WebSocket/UDP socket sees a connection reset; QUIC's connection migration handles it transparently.

**Recovery pattern:**
- **With QUIC (recommended):** Connection migration is native. Connection ID survives the IP:port change. Brief ~200ms stutter as packets reroute; FSM never leaves `STREAMING`.
- **With WS+UDP:** TCP WebSocket dies. Client FSM transitions to `RECONNECTING`. Reconnect succeeds over new path. Session resumes (server-side `SessionRuntime` persists). ~1-2s user-visible interruption.
- **Tailscale specifics:** UDP-throughput improvements in Tailscale 1.54+ matter here. Ship a min-version requirement in docs.

**Current state:** Without QUIC, Teraguchi takes option (b) — 1-2s interruption. Workable but not invisible.

**Sources:** [Internet Society on QUIC connection migration](https://pulse.internetsociety.org/blog/how-quic-helps-you-seamlessly-connect-to-different-networks), [arxiv analysis of QUIC migration in the wild](https://arxiv.org/html/2410.06066v1), [Tailscale QUIC throughput blog](https://tailscale.com/blog/quic-udp-throughput).

### 4. Server Process Crash / Restart

**What happens:** Server process exits (panic, OOM, systemd restart).

**Recovery pattern:**
- **User GUI session (Xvfb/Xorg):** Survives if run as separate processes (they are — spawned by `XSessionManager`, not child of server process).
- **Client:** WebSocket closes → FSM → `RECONNECTING` → exponential backoff → reconnect when server is up.
- **SessionRuntime state:** Lost. Client re-auths; new runtime re-attaches to existing Xvfb session (same DISPLAY, same PID). User sees desktop as they left it; first frame after reconnect is an IDR.
- **Apps running inside session:** Unaffected.

**Current state:** Works, per the "SessionRuntime is keyed by username; reconnecting user reattaches" logic. The key insight is that `XSessionManager._find_existing_display(username)` has to check and re-use the existing display. Verify this works on restart, not just on drop.

**Potential bug:** Zombie Xvfb/Xorg processes if `SessionRuntime.shutdown()` doesn't run. `install-server.sh` already has uinput/module cleanup; add xsession cleanup on systemd `ExecStopPost`.

### 5. Client App Crash

**What happens:** PySide6 process dies.

**Recovery pattern:**
- Server sees WebSocket closed. Per-client `ClientSession` enters `DRAINING`, removed from `runtime.clients`. `SessionRuntime` persists.
- User relaunches client. Bookmarks include last connection. One click → reconnect → FSM progresses to `STREAMING`. Desktop state unchanged.

**Current state:** Works. Already tested (this is the common case).

### 6. Audio Device Change

**What happens:**
- **Client (macOS):** User plugs/unplugs headphones, switches AirPods, etc. macOS automatically moves `QAudioSink` to the default output device (mostly seamlessly).
- **Server (Linux):** PulseAudio/PipeWire default sink changes. `AudioCapture` reads the monitor source; if monitor moves, `pactl subscribe` fires an event → `AudioCapture` reinits.

**Recovery pattern:**
- **Client:** `QAudioSink.stateChanged` → reinit if state becomes `StoppedState`. Small audio gap, no session disruption.
- **Server:** Monitor-source reinit with new device. Brief audio silence. Video continues normally.

**Current state:** Client side: probably works (Qt handles it). Server side Linux: PulseAudio monitor source reinit may not exist yet — audit. macOS server: no audio capture exists at all (CONCERNS.md "Audio capture on macOS silently disabled").

**v1 work:**
1. Implement `server/mac_audio_capture.py` (AVAudioEngine tap on default output).
2. Add PulseAudio device-change event subscription on Linux server.
3. Client-side `QAudioSink` state-change listener for reinit.

### 7. Wake from Sleep (Both Ends)

**Client wakes:**
- TCP sockets broken. QUIC connections broken (idle timeout likely exceeded). FSM → `RECONNECTING` → reconnect.
- User sees "Reconnecting..." for 1-3s, then stream resumes.

**Server wakes:**
- Depends on why it slept. A workstation-class server (the typical Teraguchi deployment) should never sleep — document this in installer.
- If it did: client sees transport lost → reconnect after server's network is back.

**Mac server tuned for never-sleep:**
- `pmset -a disablesleep 1` (admin runs this in `install-server-macos.sh`).
- `pmset -a sleep 0 disksleep 0 hibernatemode 0`.
- Document: "Teraguchi server host should not sleep while serving sessions."

**Linux server:** systemd masks sleep when sessions are active via inhibitor locks — integrate.

### Failure-Mode Summary Table

| Failure | Current Survival | Target Survival | Work Required |
|---------|------------------|-----------------|---------------|
| GPU driver reload | Partial (NvFBC isolated) | Full — fall back to mss, reset encoder, IDR | Encoder recovery FSM |
| Monitor hotplug (Linux) | Works | Works | None |
| Monitor hotplug (macOS) | Unclear | SCStream reinit on error | Implement |
| Network path change (WS+UDP) | ~1-2s interruption | Same | None |
| Network path change (QUIC) | Partial (aioquic supports migration) | Transparent, <500ms | Promote QUIC to primary |
| Server process crash | Client reconnects, runtime rebuilt | Same | Verify Xvfb survives |
| Client app crash | Works | Works | None |
| Audio device change (client) | Probably works | Same | Add QAudioSink state listener |
| Audio device change (Linux server) | Unknown | Reinit on pactl event | Implement subscription |
| Audio device change (macOS server) | No audio at all | Full-duplex AVAudioEngine | Implement `mac_audio_capture.py` |
| Wake from sleep (client) | Reconnects | Reconnects | None; document |
| Wake from sleep (server) | Should not sleep | Should not sleep | Installer disables sleep |
| Encoder subprocess crash | Partial (`_restart_encoder` exists) | Clean: drain queue, restart, IDR | Tighten lifecycle |
| TLS cert expiry | Client fails to connect (hard error) | Clear error message | Fix TLS verify defaults (URGENT SEC) |
| PAM account locked | `AuthResult(success=False)` | Same; include reason | Add `faillock` integration |

---

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 1-3 users / 1-3 machines (homelab) | Direct PAM, no broker. Tailscale for reachability. Config: defaults. |
| 5-10 users / 5-10 machines (small studio, Teraguchi's v1 target) | Direct PAM per-server. Optional broker for pool-scheduling if desired; not required. Self-signed-cert + fingerprint pin or Tailscale-native cert. Monitoring: `/metrics` endpoint + cheap Prometheus+Grafana if desired. |
| 10-50 users / 10-30 machines (post-v1 stretch) | Broker mandatory for pool scheduling. FreeIPA or OIDC integration. Metrics federation. Config management via Ansible (Teraguchi already fits this). |
| 50+ users (enterprise, out of scope) | Broker HA (multi-instance with shared state). Session-recording. Policy engine. Not a v1 concern. |

### Scaling Priorities

1. **First bottleneck: single-server encoder throughput.** NVENC on a consumer RTX can do ~8 concurrent encode sessions (2024+). A Flame workstation with one user → not a bottleneck. Multi-user shared Flame server is the constraint (and Flame itself is single-user, so this doesn't matter in practice).
2. **Second bottleneck: broker CPU for auth+pool-probe.** At 30+ machines, broker's `aiohttp.ClientSession` per probe (CONCERNS.md) matters — fix before hitting scale.
3. **Third: log/metric volume.** structlog JSON + async log handler avoids backpressure. Scale-out is Prometheus scrape, not the app's problem.

---

## Anti-Patterns (Domain-Specific)

### Anti-Pattern 1: Single FPS Knob Controls Everything

**What people do:** One config option "target fps" drives capture rate, encoder rate, and send rate.

**Why it's wrong:** When the encoder slows down (frame-complex scene or GPU contention), the capture pipeline keeps producing, the send queue fills, and the oldest frame is dropped — typically a P-frame the client still needs. Stream corrupts until next IDR.

**Do this instead:** Three rates, bounded queues between each, documented drop policy at each queue. See Pattern 1.

### Anti-Pattern 2: Implicit State Scattered Across Objects

**What people do:** `ClientProtocol.connected`, `Session._reconnect_pending`, `MainWindow._tab_active`, `HybridServerTransport.mode` — each tracks a piece of "is this session healthy?" None of them agree when things go wrong.

**Why it's wrong:** Half-connected bugs. Impossible to test. Reconnect logic becomes whack-a-mole.

**Do this instead:** Single FSM per side. Other objects READ the state; only FSM WRITES it. See Pattern 2.

### Anti-Pattern 3: Reliable Transport for Video

**What people do:** TCP WebSocket for video. "It's simpler and mostly fast enough."

**Why it's wrong:** Head-of-line blocking kills latency on any packet loss. One lost MTU means TCP retransmits before any subsequent packet can be delivered to userspace. Unacceptable for interactive video.

**Do this instead:** Unreliable transport (UDP/QUIC datagram) for video + audio. Reliable for input + control. QUIC multiplexes both in one connection.

### Anti-Pattern 4: Lockstep Client/Server Version Dependency

**What people do:** Client and server share one version number. Both must update together.

**Why it's wrong:** Studio IT cannot update all machines at once. Artist with old laptop gets locked out until they update; artist mid-deadline doesn't want to update.

**Do this instead:** Independent versions with semver'd protocol + capability flags. See "Client/Server Version Skew Tolerance."

### Anti-Pattern 5: Security Via Disable-Verification

**What people do:** "TLS is complex, let's just disable cert verification so dev works." Ship that to production.

**Why it's wrong:** Teraguchi today. `verify_mode = ssl.CERT_NONE` everywhere. MITM is trivial.

**Do this instead:** Verify by default. Support Tailscale-native + self-signed-pin + corporate-CA. `--insecure-skip-verify` is a loud flag, logged on every connect, never the default.

### Anti-Pattern 6: Hot-Reload Everything

**What people do:** Watch config file for changes; reload on the fly. Complex reload logic for every setting.

**Why it's wrong:** Long-lived sessions + hot-reload = tested on first frame, untested forever after. Every setting is a state machine to design. Enterprise-level complexity for small-studio value.

**Do this instead:** Restart → reconnect pattern. Drain-and-restart is fast. QUIC migration makes reconnection invisible. See "Configuration Surface."

### Anti-Pattern 7: "Python Can't Do Low-Latency Video" Rewrite Urge

**What people do:** Assume Python is the bottleneck. Rewrite in Rust/Go/C++.

**Why it's wrong:** Teraguchi's hot path is FFmpeg subprocess stdin pipe. FFmpeg is C. PyAV wraps libav. NvFBC is a C helper. Python is orchestration, not the pixel path. Rewriting orchestration doesn't help. Profile first.

**Do this instead:** Keep Python. Move hot inner loops to C extensions only where measurement proves need. See PROJECT.md constraint: "Profile first, optimize inner loops in C extensions only if measurements demand it."

---

## Integration Points

### External Services / Systems

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Tailscale | Hostname-based connection (`wss://host.tailnet.ts.net:443`); no app-layer integration | Tailscale is transparent. Ship docs + assume it's configured. |
| FreeIPA (broker path only) | LDAP/GSSAPI bind for group lookup; `id -Gn` fallback | Broker paused for v1. Keep code path; don't invest in hardening. |
| Let's Encrypt via Tailscale | `tailscale cert` command at install time | Document in `install-server.sh` as optional. |
| systemd (Linux) | Unit file + inhibitor locks | `install-server.sh` already does unit install. Add inhibitor locks for wake-from-sleep. |
| launchd (macOS) | LaunchAgent plist | `install-server-macos.sh` already does this. |
| PAM | `/etc/pam.d/teraguchi` service file | Currently relies on generic `login`. Ship dedicated PAM service file with `faillock` in v1. |
| Autodesk Flame / Nuke / Resolve | None (applications inside the remote session; OS-level integration only) | Teraguchi is OS-level; applications see it as a normal X11/macOS GUI session. |
| USB/IP kernel modules | `usbip-core`, `vhci-hcd` | Linux server only. Already handled by installer. |
| NVIDIA drivers | NvFBC library (`libnvidia-fbc.so.1`), NVENC via FFmpeg | Version-pin in install docs. |

### Internal Module Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `client/` ↔ `common/` | Direct Python imports | ABI: any addition must preserve old message parsing. |
| `server/` ↔ `common/` | Direct Python imports | Same. |
| `server/main.py` ↔ `server/session_runtime.py` (NEW) | Direct imports; acceptor creates runtimes | New split; clean boundary. |
| `session_runtime.py` ↔ backends | Via `typing.Protocol` ABC (NEW) | Enforces contract; mypy-checkable. |
| Capture ↔ Encoder | asyncio.Queue (bounded) | Explicit backpressure. |
| Encoder ↔ Broadcaster | asyncio.Queue | Explicit backpressure. |
| Broadcaster ↔ per-client | asyncio.Queue per client + IDR request channel | Backpressure + recovery. |
| Client `ClientProtocol` IO thread ↔ Qt GUI thread | Qt signals via `_Bridge` (existing) | Keep; works well. |
| Client FSM ↔ UI | Signals emitted on state change | New; UI subscribes to FSM events. |
| Server FSM ↔ transport | Transport invokes FSM.dispatch(event) | New; transport no longer owns state. |

---

## Phase Mapping Hints (for roadmap generation)

Downstream: the roadmap builder should map architectural work to phases roughly as follows.

### Phase 1: Foundation Hardening

- Extract `SessionRuntime` / `ClientSession` / `ConnectionAcceptor` from `server/main.py`.
- Promote platform backends to `typing.Protocol` ABCs in `server/backends.py`.
- Delete JPEG fallback encode path (CONCERNS.md tech debt).
- Deprecate local-auth JSON mode (or gate behind `--dev-mode`).
- Fix TLS cert-verification-disabled default (SECURITY CRITICAL).
- Add `pam_faillock` integration.

### Phase 2: State Machines and Pipeline Decoupling

- Introduce `common/session_fsm.py` with client and server FSM definitions.
- Rewrite `ClientProtocol` reconnect logic as FSM-driven via new `client/connection_supervisor.py`.
- Introduce pipeline queues (`server/pipelines/capture.py`, `encoder.py`, `broadcaster.py`).
- Fix broadcaster drop policy: IDR-on-drop, `maxsize=30` → `maxsize=4`.
- Explicit encoder recovery FSM (GPU crash, stall, subprocess death).

### Phase 3: Transport Hardening

- Promote QUIC path from experimental to production default.
- Add QUIC connection migration testing (WiFi↔cellular, Tailscale re-route).
- Keep WS+UDP as LAN fallback.
- Implement FEC on keyframes + audio.
- Adaptive bitrate rules engine wiring `HealthMonitor` → `QualitySettings`.
- TLS verification default-on + Tailscale-native cert + self-signed-pin TOFU.

### Phase 4: Platform Parity (macOS)

- `server/mac_audio_capture.py` (AVAudioEngine).
- macOS SCK hotplug handling.
- Pen pressure/tilt on macOS (IOHIDUserDevice helper, if scoped in).
- Signed + notarized macOS client .app and server .pkg.

### Phase 5: Observability + Distribution

- `structlog` JSON logging with session_id context.
- `/metrics` Prometheus endpoint.
- `/debug/session/{id}` remote-diagnostics endpoint.
- CI matrix: GitHub Actions for Mac arm64 + Rocky x86_64, tag-triggered signed releases.
- RPM spec wrapping `install-server.sh`.
- Protocol version + capability negotiation with documented compatibility matrix.

### Phase 6: Pilot Deployment

- Deploy to DXS lab (6 Flame workstations).
- Deploy to 3-person studio reference.
- Stress-test failure modes: GPU driver reload, network path change, monitor hotplug.
- Document Tailscale-first deployment guide.

---

## Sources

**Production remote-workstation systems:**

- [LizardByte/Sunshine GitHub](https://github.com/LizardByte/Sunshine) — open-source game-streaming server architecture
- [Sunshine Vulkan Video encode (Phoronix, 2026)](https://www.phoronix.com/news/Sunshine-v2026.413.143228) — zero-copy DMA-BUF pipeline
- [Sunshine DeepWiki — Network Streaming Architecture](https://deepwiki.com/LizardByte/Sunshine/4-core-streaming-architecture)
- [Sunshine DeepWiki — Video Encoding Pipeline](https://deepwiki.com/LizardByte/Sunshine/5.2-video-encoding-pipeline)
- [Moonlight Protocols (Games on Whales)](https://games-on-whales.github.io/wolf/stable/protocols/index.html) — ENet control, RTP video, UDP audio
- [Parsec: A Networking Protocol Built For Low Latency](https://parsec.app/blog/a-networking-protocol-built-for-the-lowest-latency-interactive-game-streaming-1fd5a03a6007) — BUD rationale
- [Parsec BUD UDP protocol overview](https://medium.com/parsec/a-networking-protocol-built-for-the-lowest-latency-interactive-game-streaming-1fd5a03a6007)
- [Amazon DCV (formerly NICE DCV) documentation](https://docs.aws.amazon.com/dcv/latest/adminguide/what-is-dcv.html)
- [Stream 4K at 60 FPS with NICE DCV over QUIC (AWS blog)](https://aws.amazon.com/blogs/gametech/stream-remote-environment-nice-dcv-quic-udp-4k-monitor-60-fps/)
- [HP Anyware / PCoIP EOL announcement discussion (ThinLinc forum)](https://community.thinlinc.com/t/hp-anyware-teradici-end-of-life-central-hub-for-linux-migration-alternatives/1978)
- [Why alternatives to PCoIP (Amulet Hotkey)](https://www.amulethotkey.com/insights/why-you-need-to-consider-alternative-protocols-to-pcoip/)

**Transport / QUIC / MoQ:**

- [NICE DCV QUIC 4K 60FPS (AWS)](https://aws.amazon.com/blogs/gametech/stream-remote-environment-nice-dcv-quic-udp-4k-monitor-60-fps/)
- [QUIC connection migration (Internet Society Pulse)](https://pulse.internetsociety.org/blog/how-quic-helps-you-seamlessly-connect-to-different-networks)
- [Analysis of QUIC connection migration in the wild (arxiv)](https://arxiv.org/html/2410.06066v1)
- [Increasing QUIC/UDP throughput over Tailscale](https://tailscale.com/blog/quic-udp-throughput)
- [Media over QUIC and the future of high-quality streaming (StreamingMedia)](https://www.streamingmedia.com/Articles/Editorial/Featured-Articles/Media-Over-QUIC-and-the-Future-of-High-Quality-Low-Latency-Streaming-167165.aspx)
- [MoQ at NAB 2026 (Fastly)](https://www.fastly.com/blog/media-over-quic-can-streaming-finally-have-both-scale-and-low-latency)
- [WebRTC vs MoQ with Ant Media 2026](https://antmedia.io/webrtc-vs-moq-media-over-quic-ant-media-server/)
- [Tunneling SRT over QUIC (Haivision)](https://haivision.github.io/srt-rfc/draft-sharabayko-srt-over-quic.html)
- [Streaming remote rendering: QUIC vs WebRTC (arxiv 2025)](https://arxiv.org/html/2505.22132v1)

**Latency / encode pipeline:**

- [NVENC low-latency presets (OBS Kb)](https://obsproject.com/kb/advanced-nvenc-options)
- [NVIDIA NvPipe](https://github.com/NVIDIA/NvPipe) — zero-latency compression library
- [Image/Video Coding for Ultra-low Latency (ACM Computing Surveys)](https://dl.acm.org/doi/10.1145/3512342)
- [Cloud AR/VR whitepaper (GSMA)](https://www.gsma.com/solutions-and-impact/technologies/networks/gsma_resources/cloud-ar-vr-whitepaper-2/) — <20ms MTP target

**Jitter buffer / FEC:**

- [WebRTC jitter buffer (BlogGeek.me)](https://bloggeek.me/webrtcglossary/jitter-buffer/)
- [Optimal buffer size for low-latency (PulseGeek)](https://pulsegeek.com/articles/optimal-buffer-size-for-low-latency-streaming-explained/)

**State machines / asyncio patterns:**

- [python-statemachine async docs](https://python-statemachine.readthedocs.io/en/latest/async.html)
- [opcua-asyncio reconnection patterns discussion](https://github.com/FreeOpcUa/opcua-asyncio/issues/1660)
- [Inngest: asyncio primitives and shared state](https://www.inngest.com/blog/no-lost-updates-python-asyncio)

**Observability / logging:**

- [Structured logs with structlog + OTEL 2026](https://johal.in/structlog-json-logs-middleware-opentelemetry-python-2026/)
- [OpenTelemetry asyncio instrumentation (docs)](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/asyncio/asyncio.html)
- [structlog in production (Dash0)](https://www.dash0.com/guides/python-logging-with-structlog)
- [How to structure logs with OpenTelemetry (OneUptime)](https://oneuptime.com/blog/post/2025-01-06-python-structured-logging-opentelemetry/view)

**Security / TLS / Tailscale:**

- [Tailscale TLS certificate setup](https://tailscale.com/docs/how-to/set-up-https-certificates)
- [Tailscale TLS certs for internal services](https://tailscale.com/blog/tls-certs)
- [Tailscale encryption concepts](https://tailscale.com/docs/concepts/tailscale-encryption)

**Build / CI / distribution:**

- [PyInstaller + GitHub Actions multi-OS deployment](https://data-dive.com/multi-os-deployment-in-cloud-using-pyinstaller-and-github-actions/)
- [Pyinstaller-Github-Actions (sayyid5416)](https://github.com/sayyid5416/pyinstaller)
- [PyInstaller notarization issues discussion](https://github.com/pyinstaller/pyinstaller/issues/7937)

**Graceful shutdown / rolling restart:**

- [Kubernetes graceful node shutdown beta](https://kubernetes.io/blog/2021/04/21/graceful-node-shutdown-beta/)
- [Envoy graceful shutdown & hitless upgrades](https://gateway.envoyproxy.io/docs/tasks/operations/graceful-shutdown/)

**Protocol versioning / compatibility:**

- [MS-RDPBCGR versioning and capability negotiation](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/18bdcc20-2ca8-4520-9073-65e52a1c6733)
- [QUIC Versions and Version Negotiation (MSQUIC docs)](https://microsoft.github.io/msquic/msquicdocs/docs/Versions.html)

---

*Architecture research for: high-performance remote workstation (Teraguchi v1 hardening)*
*Researched: 2026-04-18*
