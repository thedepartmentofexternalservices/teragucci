# Phase 2: Input + Color Fidelity — Pattern Map

**Mapped:** 2026-04-18
**Files analyzed:** 24 (15 source + 8 tests + 1 docs seed)
**Analogs found:** 20 / 24 (4 flagged as "no analog" — new territory)

> Orientation note for the planner: Phase 1 is the house style. When CONVENTIONS.md is in tension with Phase 1 artifacts (e.g. it says "no structlog" and "no tests" — both pre-Phase-1), the Phase 1 artifact wins (`common/logging.py`, `tests/**`, `common/session_fsm.py`). This document's excerpts are drawn from post-Phase-1 code and integrate the Phase 1 CI baseline.

---

## File Classification

| New / Modified File | Kind | Role | Data Flow | Closest Analog | Match |
|---------------------|------|------|-----------|----------------|-------|
| `server/mac_video_encoder.py` | NEW | server backend | capture → encode (one-in-one-out) | `server/mac_screen_capture.py` (PyObjC + threading bridge) + `server/video_encoder.py` (encoder contract shape) | **NO DIRECT ANALOG** (see §No Analog) |
| `server/mac_pen_injector.py` | NEW | server backend | wire → input injector | `server/input_injector.py` (`VirtualPenTablet` uinput device) + `server/mac_input_injector.py` (PyObjC framing) | **NO DIRECT ANALOG** (see §No Analog) |
| `server/capability_probe.py` | NEW (RESEARCH §Pattern 1) | server util | one-shot startup probe | `server/video_encoder.py::detect_encoders()` | role-match |
| `tools/wacom_quant_analysis.py` | NEW | offline CLI | file-I/O + transform (RMS compute) | `tools/keydiag.py` (argparse CLI shape) | role-match |
| `tests/smoke/fixtures/ten_bit_ramp.y4m` | NEW | test fixture (binary asset) | file-I/O | `tests/smoke/fixtures/canned_encoded_frames.bin` | exact |
| `tests/smoke/fixtures/ten_bit_ramp_pixels.json` | NEW | test fixture (metadata) | file-I/O | `tests/smoke/fixtures/canned_encoded_frames.bin` + `tests/conftest.py::canned_hevc_keyframes` loader | role-match |
| `tests/smoke/test_ten_bit_pipeline.py` | NEW | smoke test | end-to-end assertion | `tests/smoke/test_latency_benchmark.py` (stage-budget harness) + `tests/smoke/test_synthetic_1h.py` (harness shape) | role-match |
| `tests/common/test_keymap.py` | EXTEND | unit test | pure-function table | `tests/common/test_keymap.py` (self — Phase 1 stub) + `tests/common/test_messages.py` (parametrize pattern) | exact |
| `tests/server/test_mac_video_encoder.py` | NEW | unit test | mock-at-VT-boundary | `tests/server/test_video_encoder_mock.py` (D-02 mock-at-subprocess pattern) | exact |
| `tests/server/test_mac_pen_injector.py` | NEW | unit test | mock-at-IOKit-boundary | `tests/server/test_video_encoder_mock.py` (D-02 pattern, same shape) | role-match |
| `tests/server/test_hw_capability_probe.py` | NEW | unit test | mock-at-subprocess-boundary | `tests/server/test_video_encoder_mock.py` (mocks `subprocess.Popen`) | exact |
| `tests/common/test_messages_phase2.py` | NEW | unit test | JSON round-trip | `tests/common/test_messages.py` (exact round-trip pattern) | exact |
| `tests/integration/test_crazy_hotkeys.py` | NEW | integration test | loopback wss + synthetic input | `tests/integration/test_fsm_state_sync.py` (loopback harness) + `tests/smoke/conftest.py::_synthetic_input_generator` | exact |
| `client/viewer.py` (video-layer refactor D-02) | REFACTOR | client widget | decode → display (Metal upload) | `client/viewer.py` current `paintEvent` at lines 290-307 (QPainter path — this is what's being replaced) | **NO DIRECT ANALOG** — QRhiWidget Metal path is new (see §No Analog) |
| `client/viewer.py` (pen proximity + IME D-19/D-15) | EXTEND | client widget | event handlers | `client/viewer.py::tabletEvent` at lines 315-384 (existing pen dispatch pattern) | exact |
| `client/video_decoder.py` | EXTEND | client decoder | P010 format assertion | `client/video_decoder.py::_init_decoder` at lines 80-115 (existing hw-accel try/except pattern) | exact |
| `client/key_diagnostic.py` | EXTEND | client widget | TCC sqlite read + tab | `client/key_diagnostic.py::KeyDiagnosticDialog.__init__` at lines 88-152 (dialog + log layout pattern) | exact |
| `common/keymap.py` | EXTEND | pure table | table lookup | `common/keymap.py::QT_KEY_TO_LINUX` at lines 10-112 (dict-shape + `qt_key_to_*` function pair) | exact |
| `common/messages.py` | EXTEND | dataclass + wire | protocol dataclass | `common/messages.py::PenEventMsg` at lines 498-514 + `ServerHelloMsg` at lines 471-491 | exact |
| `common/session_fsm.py` (new `PenFSM`) | EXTEND | FSM | state machine | `common/session_fsm.py::ClientFSM` at lines 108-160 (State + Transition idiom) | exact |
| `server/platform_backends.py` | EXTEND | dispatcher | import-time IS_MACOS branching | `server/platform_backends.py` self (lines 25-61, full file is the pattern) | exact |
| `server/video_encoder.py` | EXTEND | encoder registry | ENCODER_DEFS dataclass fields | `server/video_encoder.py::HWEncoder` at lines 37-69 (dataclass + ENCODER_DEFS list) | exact |
| `server/mac_input_injector.py` | REFACTOR | delegation thinning | internal composition | `server/mac_input_injector.py::pen_event` at lines 358-398 (current stub — the thing being thinned) + `server/platform_backends.py` (composition pattern) | exact |
| `docs/release.md` | SEED | docs | markdown | — | no analog (docs dir does not yet exist) |

---

## Pattern Assignments

### `server/mac_video_encoder.py` (NEW — server backend, capture→encode)

**Analogs:** `server/mac_screen_capture.py` for the PyObjC + threading bridge; `server/video_encoder.py` for the encoder public contract (`start(on_encoded_frame)` / `feed_frame(raw)` / `request_keyframe()` / `stop()`).

**Module docstring + deferred PyObjC import pattern** (copy from `server/mac_screen_capture.py` lines 63-101):

```python
# PyObjC imports are deferred to import-time but guarded so that simply
# importing server modules on a non-Mac host does not blow up. The
# dispatcher in video_encoder.py checks ``IS_MACOS`` before pulling this
# module in.
try:
    import objc  # noqa: F401
    from Foundation import NSObject, NSCondition
    import VideoToolbox as VT
    from CoreMedia import (
        CMSampleBufferGetDataBuffer,
        CMBlockBufferCopyDataBytes,
        CMTimeMake,
    )
    from CoreVideo import (
        CVPixelBufferCreate,
        kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange,
    )
    _HAS_VT = True
except Exception as _e:
    _HAS_VT = False
    _import_error = _e
```

**Delegate-thread → asyncio bridge** (copy from `server/mac_screen_capture.py` lines 130-200):

```python
# server/mac_screen_capture.py lines 130-200 — the shape for bridging a
# PyObjC callback (arriving on a framework-owned thread) to the encoder's
# pull-style consumer. VTCompressionSession's output callback plays the
# same role: it fires on VT's internal thread. Latch the latest encoded
# NAL under self._lock; drain on the async sender's call_soon_threadsafe.
class _StreamOutputHandler(NSObject):
    def initWithCapture_(self, capture):
        self = objc.super(_StreamOutputHandler, self).init()
        if self is None:
            return None
        self._capture = capture
        return self
    # SCK calls this on a framework thread; we latch under a lock.
    def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, output_type):
        ...
```

**Public contract to match** (from `server/video_encoder.py` lines 128-180):

```python
class VideoEncoder:
    def __init__(self, width: int, height: int, settings: QualitySettings,
                 available_encoders: Optional[Dict[str, List[HWEncoder]]] = None): ...
    def start(self, on_encoded_frame: Callable): ...
    def feed_frame(self, raw_bgra: bytes): ...     # new Mac path: feeds CVPixelBuffer P010
    def request_keyframe(self): ...                # VTCompressionSessionEncodeFrame w/ force-keyframe dict
    def stop(self): ...
```

**Custom exception convention** (copy from `server/mac_screen_capture.py` line 115):

```python
class MacScreenCaptureError(RuntimeError):
    """Raised when ScreenCaptureKit is unavailable or unauthorized."""

# → Add: class MacVideoEncoderError(RuntimeError)
```

---

### `server/mac_pen_injector.py` (NEW — server backend, wire→input)

**Analogs:** `server/input_injector.py::VirtualPenTablet` (uinput patterns for HID descriptor + report packing); `server/mac_input_injector.py` (PyObjC framing + deferred import + _HAS_CG guard).

**Wacom pressure/tilt constants** (copy from `server/input_injector.py` lines 92-96):

```python
# Maximum values for pen coordinates — used for the USB HID descriptor
# we publish from IOHIDUserDevice. Match Wacom hardware so Flame's
# muscle memory over 8192 pressure levels survives.
PEN_MAX_X = 65535
PEN_MAX_Y = 65535
PEN_MAX_PRESSURE = 8191  # Wacom typically uses 8192 levels (0-8191)
PEN_MAX_TILT = 127       # -127 to 127
```

**Public contract to match** (copy from `server/mac_input_injector.py::pen_event` lines 358-398 — this is the signature `platform_backends.InputInjector` already dispatches to, so the new `mac_pen_injector.pen_event` takes the same args):

```python
def pen_event(self, x: float, y: float, pressure: float,
              tilt_x: float = 0.0, tilt_y: float = 0.0,
              rotation: float = 0.0,
              button: int = 0, pressed: bool = False,
              hovering: bool = False, pen_type: str = "pen"):
    """IOHIDUserDevice-backed pen injection.
    Replaces the mouse-click stub in mac_input_injector.py."""
```

**Deferred PyObjC + IOKit import** (mirror the pattern from `server/mac_input_injector.py` lines 52-91):

```python
try:
    import objc
    IOKit = objc.loadBundle(
        'IOKit', globals(),
        bundle_path='/System/Library/Frameworks/IOKit.framework',
    )
    # Bind IOHIDUserDeviceCreate + IOHIDUserDeviceHandleReport via objc.parseBridgeSupport
    # or objc.loadFunctionList — validated during the D-08 spike.
    _HAS_IOKIT = True
except Exception as _e:
    _HAS_IOKIT = False
    _import_error = _e


class MacPenInjectorError(RuntimeError):
    pass
```

---

### `server/capability_probe.py` (NEW — server startup probe, D-03)

**Analog:** `server/video_encoder.py::detect_encoders()` lines 72-97 — exact same "trial-run a subprocess, parse the output, cache the result" pattern.

```python
# server/video_encoder.py lines 72-97
def detect_encoders() -> Dict[str, List[HWEncoder]]:
    available = {"h264": [], "h265": [], "av1": []}
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        output = proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return available
    for enc_def in ENCODER_DEFS:
        if enc_def.name in output:
            available[enc_def.codec].append(enc_def)
    for codec in available:
        available[codec].sort(key=lambda e: e.priority)
    return available
```

Apply the same "subprocess trial-run → graceful fallback" shape for:
- Linux: `nvidia-smi --query-gpu=gpu_name` + trial-encode 32×32 P010 via `ffmpeg -f rawvideo -pix_fmt p010le -f null -`
- macOS: `VTCopySupportedPropertyDictionary(kVTProfileLevel_HEVC_Main10_AutoLevel)` under a try/except pattern.

Result feeds `ServerHelloMsg` capability flags (see `common/messages.py` extension below).

---

### `tools/wacom_quant_analysis.py` (NEW — offline CLI, D-18)

**Analog:** `tools/keydiag.py` lines 1-80 (shebang + module docstring + argparse usage lines + optional modes).

```python
# tools/keydiag.py lines 1-14
#!/usr/bin/env python3
"""
Teraguchi Keystroke Diagnostic Tool

Shows the full key translation path:
  Physical key → Qt key code → Wire message → X11 keysym → X11 keycode

Run on the Mac client to see exactly what Qt captures and what gets sent
to the remote server. Essential for verifying Flame hotkeys work correctly.

Usage:
    python tools/keydiag.py                   # interactive Qt window
    python tools/keydiag.py --server :10      # server-side X11 keycode dump
"""
import sys
import os
import argparse
```

**RESEARCH-prescribed module layout** (CONTEXT.md D-18; RESEARCH.md §Claude's Discretion #7): single-file, argparse CLI, < 200 lines:

```
python tools/wacom_quant_analysis.py \
    --client-log a.jsonl --server-log b.jsonl --out rms.svg
```

**Note to planner:** `tools/` package is NOT in `pyproject.toml` `[tool.setuptools.packages.find]` include list (confirmed per STRUCTURE.md §Utilities — `broker*` and `tools*` are not shipped). Scripts in `tools/` are operator utilities only and must not be imported by production code.

---

### `tests/smoke/fixtures/ten_bit_ramp.y4m` + `ten_bit_ramp_pixels.json` (NEW — fixture assets)

**Analog:** `tests/smoke/fixtures/canned_encoded_frames.bin` (Phase 1 fixture binary — same directory, gitignored-via-inclusion pattern).

**Loader pattern** (copy from `tests/conftest.py` lines 69-89):

```python
# tests/conftest.py lines 69-89 — the canonical fixture loader shape:
# lives in the root conftest.py, uses pathlib, degrades gracefully if
# the fixture file is missing so collection doesn't fail pre-Wave-0.
@pytest.fixture
def canned_hevc_keyframes():
    path = (pathlib.Path(__file__).parent /
            "smoke" / "fixtures" / "canned_encoded_frames.bin")
    if not path.exists():
        return []
    data = path.read_bytes()
    frames = []
    offset = 0
    while offset < len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        is_keyframe = bool(data[offset + 4])
        payload = data[offset + 5:offset + 5 + size]
        frames.append((payload, is_keyframe))
        offset += 5 + size
    return frames
```

For Phase 2: add `ten_bit_ramp_frames` fixture in `tests/smoke/conftest.py` that loads the `.y4m` header + raw plane bytes, and `ten_bit_reference_pixels` that loads the JSON reference for bottom-2-bits assertions. JSON file schema (chosen here so the planner is not ambiguous):

```json
{
  "width": 3840, "height": 2160, "pix_fmt": "yuv420p10le",
  "checkpoints": {
    "capture":        {"sha256": "...", "bottom_2_bits_nonzero_pct": 100.0},
    "encoder_input":  {"sha256": "...", "bottom_2_bits_nonzero_pct": 100.0},
    "encoder_output": {"ffprobe_pix_fmt": "yuv420p10le", "nal_profile": 2},
    "wire":           {"h265_profile_idc": 2},
    "decoder_output": {"av_frame_format": ["yuv420p10le", "p010le"]},
    "decoder_hwaccel":{"backend_in": ["videotoolbox", "software"]},
    "qt_surface":     {"texture_format": "rgb10a2"},
    "final_blit":     {"sample_top_right_pixel_rgb10": [1023, 512, 0]},
    "display":        {"reference_mode_ok": true, "edr_state_documented": true}
  }
}
```

---

### `tests/smoke/test_ten_bit_pipeline.py` (NEW — 9-checkpoint smoke harness)

**Analog:** `tests/smoke/test_latency_benchmark.py` (stage-budget harness — stages 1-6 mockable, marker-gated slow path for later stages).

**Marker + stage-sequence pattern** (copy from `tests/smoke/test_latency_benchmark.py` lines 30-70):

```python
# tests/smoke/test_latency_benchmark.py lines 30-70
# Stage budgets per RESEARCH §"Synthetic-stage-time accumulator" line 701-710.
STAGE_BUDGETS_MS = {
    "capture": 1.0, "encode": 4.0, "pipeline": 2.0, "transport": 1.0,
    "decode": 3.0, "jitter": 4.0, "paint": 5.0,
}
EXPECTED_TOTAL_MS = sum(STAGE_BUDGETS_MS.values())  # = 20.0
P99_GATE_MS = 30.0
ITERATIONS = 1000

@pytest.mark.latency_bench
@pytest.mark.asyncio
async def test_latency_p99_under_budget():
    samples = []
    for _ in range(ITERATIONS):
        start = time.perf_counter()
        for _stage, ms in STAGE_BUDGETS_MS.items():
            await asyncio.sleep(ms / 1000)
```

For Phase 2 mirror: replace `STAGE_BUDGETS_MS` with `CHECKPOINT_ASSERTIONS = {"capture": ..., "encoder_input": ..., ... "display": ...}`, add `@pytest.mark.ten_bit_smoke` + `@pytest.mark.gpu` (skipped on GHA) for checkpoints 7-9.

---

### `tests/common/test_keymap.py` (EXTEND — D-12 exhaustive table)

**Analog:** `tests/common/test_keymap.py` self (Phase 1 stub — extend, don't replace).

**Parametrize pattern** (copy from self, lines 45-53 + 113-121):

```python
# tests/common/test_keymap.py lines 45-53
@pytest.mark.parametrize("qt_key,expected_scancode", [
    (0x41, 30),   # A → KEY_A
    (0x5A, 44),   # Z → KEY_Z
    (0x51, 16),   # Q → KEY_Q
    (0x50, 25),   # P → KEY_P
])
def test_letter_scancodes_match_evdev_table(qt_key, expected_scancode):
    assert qt_key_to_linux_scancode(qt_key) == expected_scancode
```

**Table-guard pattern** (copy from lines 132-136):

```python
def test_table_contains_full_ascii_letter_range():
    missing = [0x41 + i for i in range(26) if (0x41 + i) not in QT_KEY_TO_LINUX]
    assert missing == [], f"letters missing from table: {missing}"
```

**D-12 extension shape** (per CONTEXT.md: every key × every modifier combo × {Linux scancode, Mac virtual key code} — ~2000 cases). The Phase 1 stub already enforces that modifiers are passed as separate scancodes (no bitmask arg), so the new `qt_key_to_mac_vk(qt_key)` function must follow the exact same signature to let the existing test_letter_scancodes shape port verbatim. Use `pytest.mark.parametrize` with a fixture-factory if needed to keep the test file readable at 2000 cases.

---

### `tests/server/test_mac_video_encoder.py` (NEW — mock-at-VT-boundary)

**Analog:** `tests/server/test_video_encoder_mock.py` — exact structural mirror; Phase 1 D-02 mock-at-subprocess-boundary becomes Phase 2 mock-at-VT-boundary.

**Mock fixture pattern** (copy from `tests/server/test_video_encoder_mock.py` lines 36-62):

```python
# tests/server/test_video_encoder_mock.py lines 36-62 — the reusable
# mock-at-subprocess-boundary pattern. Mirror for VTCompressionSession:
# monkeypatch server.mac_video_encoder.VT.VTCompressionSessionCreate to
# return a MagicMock session, and stub VTCompressionSessionEncodeFrame
# so the "output callback on VT thread" path is driven by the test.
@pytest.fixture
def mock_popen(monkeypatch):
    fake_proc = mock.MagicMock()
    fake_proc.stdin = mock.MagicMock()
    fake_proc.stdout = mock.MagicMock()
    fake_proc.stdout.read.return_value = b""
    fake_proc.poll.return_value = None
    fake_proc.returncode = None
    fake_proc.wait.return_value = 0
    def _fake_popen(*a, **kw):
        return fake_proc
    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    monkeypatch.setattr("server.video_encoder.subprocess.Popen", _fake_popen)
    return fake_proc
```

**Test shape** (copy the 5 test signatures from `test_video_encoder_mock.py` lines 72-207 — `test_encoder_instantiates_without_real_ffmpeg`, `test_encoder_start_invokes_subprocess`, `test_encoder_request_keyframe_is_callable`, `test_encoder_stop_cleans_up_without_raising`, `test_encoder_feed_frame_is_noop_when_stopped`). Replace "ffmpeg / subprocess" with "VT / VTCompressionSessionCreate" one-for-one.

---

### `tests/server/test_mac_pen_injector.py` (NEW — mock-at-IOKit-boundary)

**Analog:** `tests/server/test_video_encoder_mock.py` — same D-02 mock-at-boundary pattern applied to `IOHIDUserDeviceCreate` + `IOHIDUserDeviceHandleReport`. Use the same fixture shape (`monkeypatch.setattr("server.mac_pen_injector.IOHIDUserDeviceCreate", _fake)`) and 5 equivalent tests: `instantiates_without_real_iokit`, `creates_device_on_start`, `handle_report_passes_through_mock`, `stop_releases_device`, `pressure_roundtrip_survives_to_handle_report`.

---

### `tests/server/test_hw_capability_probe.py` (NEW — mocks `subprocess.run` + VT probe)

**Analog:** `tests/server/test_video_encoder_mock.py` fixture shape, but patch `subprocess.run` (not `subprocess.Popen`) for the `nvidia-smi` + trial-encode path:

```python
# Parallel of test_video_encoder_mock.py pattern applied to subprocess.run:
@pytest.fixture
def mock_nvidia_smi(monkeypatch):
    fake = mock.MagicMock()
    fake.returncode = 0
    fake.stdout = "NVIDIA GeForce RTX 4090\n"
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake)
    return fake

def test_probe_returns_supports_main10_on_ada(mock_nvidia_smi):
    from server.capability_probe import probe_nvenc_main10
    caps = probe_nvenc_main10()
    assert caps["main10"] is True
    assert caps["chroma_422"] is False  # Ada is pre-Blackwell
```

---

### `tests/common/test_messages_phase2.py` (NEW — wire round-trip for new dataclasses)

**Analog:** `tests/common/test_messages.py` — exact round-trip pattern.

**Round-trip idiom** (copy from `tests/common/test_messages.py` lines 89-131):

```python
# tests/common/test_messages.py lines 89-112
def test_authrequest_roundtrip():
    msg = AuthRequest(challenge="abc", auth_methods=["pam", "token"], salt="salty")
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.AUTH_REQUEST
    assert parsed["challenge"] == "abc"
    assert parsed["auth_methods"] == ["pam", "token"]
    assert parsed["salt"] == "salty"

def test_clienthello_serverhello_roundtrip():
    client = ClientHelloMsg(screen_width=2560, screen_height=1440, supports_av1=True)
    server = ServerHelloMsg(server_name="teraguchi-srv", requires_auth=True)
    pc = parse_message(client.to_json())
    ps = parse_message(server.to_json())
    assert pc["type"] == MsgType.CLIENT_HELLO
    assert ps["type"] == MsgType.SERVER_HELLO
```

Apply to every new message: `KeyResetModifiersMsg`, `TextCommitMsg`, `PenProximityMsg`, and the extended `KeyEventMsg` (caps_lock/num_lock/scroll_lock bits) and `ServerHelloMsg` (supports_main10 / supports_422 / supports_444 flags).

---

### `tests/integration/test_crazy_hotkeys.py` (NEW — INPUT-04)

**Analog:** `tests/integration/test_fsm_state_sync.py` (loopback wss harness with `tls_ca_and_cert` + `free_port` fixtures) + `tests/smoke/conftest.py::_synthetic_input_generator` (modifier chord pump).

**Loopback harness pattern** (copy from `tests/integration/test_fsm_state_sync.py` lines 36-69):

```python
# tests/integration/test_fsm_state_sync.py lines 36-69
@pytest.mark.asyncio
async def test_health_ping_carries_client_state(tls_ca_and_cert, free_port):
    cfsm = ClientFSM()
    for ev in ("connect_requested", "tls_ok", "auth_ok", "hello_received"):
        cfsm.send(ev)
    received: list[str] = []
    async def server_handler(websocket):
        async for msg in websocket:
            parsed = parse_message(msg)
            if parsed.get("type") == MsgType.HEALTH_PING:
                received.append(parsed.get("client_state", ""))
                break
    srv_ctx = _server_ssl_ctx(
        tls_ca_and_cert["server_cert"], tls_ca_and_cert["server_key"]
    )
    async with websockets.serve(
        server_handler, "127.0.0.1", free_port, ssl=srv_ctx
    ):
        cli_ctx = ssl.create_default_context(
            cafile=str(tls_ca_and_cert["ca_cert"])
        )
        async with websockets.connect(
            f"wss://127.0.0.1:{free_port}", ssl=cli_ctx
        ) as ws:
            ping = HealthPing(sequence=1, client_state=cfsm.current_state.id)
            await ws.send(ping.to_json())
```

**Modifier chord synth** (copy from `tests/smoke/conftest.py` lines 35-58):

```python
# tests/smoke/conftest.py lines 35-58 — the INPUT-04 "crazy Flame hotkeys"
# should reuse the _synthetic_input_generator modifier-chord pump shape:
if t % 60 == 0:
    event = {"type": "key", "modifiers": ["ctrl", "shift"], "key": "P", "down": True}
```

---

### `client/viewer.py` — REFACTOR (video layer → QRhiWidget Metal, D-02)

**Analog:** `client/viewer.py::paintEvent` around lines 290-307 (the `QPainter.drawPixmap` path — the thing being replaced). Keep it for the overlay layer (cursor + health badge); route video through `QRhiWidget`.

**Current video blit to REPLACE** (from `client/viewer.py` lines 290-307):

```python
# client/viewer.py lines 290-307 — the QPainter.drawPixmap path that
# Phase 2 D-02 replaces for the video layer. Keep the QPainter path
# for overlays (cursor shape + health badge).
painter.drawPixmap(
    QRectF(dest_x, dest_y, dest_w, dest_h),
    self._pixmap,
    QRectF(src_x, src_y, src_w, src_h),
)
```

**Existing PySide6 import shape** (from `client/viewer.py` lines 11-16 — preserve this style, add `QRhiWidget`):

```python
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QSize
from PySide6.QtGui import (
    QImage, QPixmap, QPainter, QMouseEvent, QKeyEvent,
    QTabletEvent, QWheelEvent, QResizeEvent, QCursor,
)
from PySide6.QtWidgets import QWidget
# Phase 2 addition: from PySide6.QtRhiWidgets import QRhiWidget (Qt 6.10+)
```

---

### `client/viewer.py` — EXTEND (pen proximity + IME, D-19/D-15/D-11)

**Analog:** `client/viewer.py::tabletEvent` lines 315-384 (existing pen-event dispatch — new proximity synthesis hooks into the same `pen_event` Signal).

**Event-override idiom to mirror** (copy the style from `tabletEvent` lines 315-384 + `_remap_key` lines 466-474):

```python
# client/viewer.py lines 315-384 — the tabletEvent override pattern.
# D-19 additions: focusInEvent and showEvent synthesize a proximity-enter
# message using the same self.pen_event.emit({...}) sink.
def tabletEvent(self, event: QTabletEvent):
    pointer_type = event.pointerType()
    if pointer_type not in (QTabletEvent.PointerType.Pen,
                            QTabletEvent.PointerType.Eraser):
        event.ignore()
        return
    self._pen_active = True
    event.accept()
    pos = event.position()
    nx, ny = self._widget_to_remote(pos.x(), pos.y())
    # ... build pen_data dict ...
    self.pen_event.emit(pen_data)
```

**D-11 focus-out trigger** (new — no existing override in the file; add `focusOutEvent(self, event)` that emits a new `self.reset_modifiers_requested = Signal()` plumbed to `client/protocol.py`).

**D-15 inputMethodEvent** (new — mirror the `tabletEvent` accept+emit pattern but use `event.commitString()` and emit a new `self.text_commit = Signal(str)`).

---

### `client/video_decoder.py` — EXTEND (AVFrame.format assertion, D-01 cp.5)

**Analog:** `client/video_decoder.py::_init_decoder` lines 80-115 (try/except per-backend enumeration).

**Existing per-backend fallback shape** (copy from lines 80-115):

```python
# client/video_decoder.py lines 80-115 — the existing hardware-backend
# try/open/fall-through pattern. Phase 2 extends the opened-context
# path to assert frame.format ∈ {"yuv420p10le", "p010le"} when the
# negotiated codec is HEVC Main10; raise an explicit error if the
# hwaccel silently fell to 8-bit.
def _init_decoder(self):
    candidates = HW_DECODERS.get(self._codec_name, [("h264", None)])
    for av_codec_name, hw_type in candidates:
        try:
            codec = av.codec.Codec(av_codec_name, "r")
            ctx = av.CodecContext.create(codec)
            if hw_type:
                try:
                    ctx.open()
                    self._decoder = ctx
                    self._hw_type = hw_type
                    self._initialized = True
                    logger.info("Video decoder: %s (hw=%s)", av_codec_name, hw_type)
                    return
                except Exception:
                    continue
```

---

### `client/key_diagnostic.py` — EXTEND (new "Wacom setup" tab, D-20)

**Analog:** `client/key_diagnostic.py::KeyDiagnosticDialog.__init__` lines 88-152 (dialog layout pattern).

**Dialog + layout idiom** (copy from lines 88-152):

```python
# client/key_diagnostic.py lines 88-152 — the self-contained dialog
# pattern: QDialog subclass, QVBoxLayout, Menlo font on darwin, dark
# stylesheet, close button. The Phase 2 D-20 "Wacom setup" tab slots in
# as a QTabWidget split of the existing content + a new TCC-status pane.
class KeyDiagnosticDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Teraguchi — Key Diagnostic")
        self.setMinimumSize(680, 480)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)
        layout = QVBoxLayout(self)
        header = QLabel(
            "Press any key or combo to see how Teraguchi translates it.")
        header.setWordWrap(True)
        layout.addWidget(header)
        mono = QFont("Menlo" if sys.platform == "darwin" else "Monospace", 16)
```

**TCC detection implementation** — no analog in the codebase. Read-only `sqlite3.connect("file:...?mode=ro", uri=True)` against `~/Library/Application Support/com.apple.TCC/TCC.db`; query `SELECT client, auth_value FROM access WHERE service = 'kTCCServiceListenEvent'` (Input Monitoring) + `'kTCCServiceAccessibility'`. See CONTEXT.md D-20 for the UX spec (deep-links to `x-apple.systempreferences:` URLs).

---

### `common/keymap.py` — EXTEND (Qt → Mac virtual key code table)

**Analog:** `common/keymap.py` self (Phase 1 table-driven pattern, lines 10-120).

**Table-driven idiom + companion lookup function** (copy from lines 10-120):

```python
# common/keymap.py — the exact shape to mirror for the new Mac table.
QT_KEY_TO_LINUX = {
    0x41: 30,   # Key_A -> KEY_A
    0x42: 48,   # Key_B -> KEY_B
    ...
    0x01000020: 42,   # Key_Shift -> KEY_LEFTSHIFT
    0x01000021: 29,   # Key_Control -> KEY_LEFTCTRL
    ...
}

def qt_key_to_linux_scancode(qt_key: int) -> int:
    """Convert a Qt key enum value to a Linux evdev scan code.
    Returns 0 if no mapping exists."""
    return QT_KEY_TO_LINUX.get(qt_key, 0)

# Phase 2 addition: QT_KEY_TO_MAC_VK = { 0x41: 0x00, ... }  # kVK_ANSI_A, etc.
# + qt_key_to_mac_vk(qt_key: int) -> int with identical contract (0 = unmapped).
```

**Cmd↔Ctrl swap** (D-10) lives in this file too — reference the authoritative table at the call site, never infer per-call. There is no existing swap helper; add `swap_cmd_ctrl_for_linux_dest(qt_key: int, modifiers: int) -> tuple[int, int]` as a pure function.

---

### `common/messages.py` — EXTEND (new message types + extended dataclasses)

**Analog:** `common/messages.py::PenEventMsg` lines 498-514 + `ServerHelloMsg` lines 471-491 (dataclass + `type: str = MsgType.X` + `to_json`).

**Dataclass shape to mirror for new messages** (copy from `PenEventMsg` lines 498-514):

```python
# common/messages.py lines 498-514 — the dataclass + MsgType const + to_json
# pattern. Apply to the three new Phase 2 messages:
#   KeyResetModifiersMsg, TextCommitMsg, PenProximityMsg.
@dataclass
class PenEventMsg:
    """Pen/stylus event with full tablet data."""
    type: str = MsgType.PEN_EVENT
    x: float = 0.0
    y: float = 0.0
    pressure: float = 0.0
    tilt_x: float = 0.0
    tilt_y: float = 0.0
    rotation: float = 0.0
    button: int = 0
    pressed: bool = False
    hovering: bool = False
    pen_type: str = "pen"

    def to_json(self) -> str:
        return json.dumps(asdict(self))
```

**MsgType constant pattern** (copy the lines 120-145 style):

```python
class MsgType:
    # --- Input (Client → Server) ---
    MOUSE_MOVE = "mouse_move"
    ...
    PEN_EVENT = "pen_event"
    # Phase 2 additions:
    KEY_RESET_MODIFIERS = "key_reset_modifiers"
    TEXT_COMMIT = "text_commit"
    PEN_PROXIMITY = "pen_proximity"
```

**ServerHelloMsg extension** (copy from lines 471-491 — add capability flags alongside the existing `available_encoders` dict):

```python
# common/messages.py lines 471-491 — existing ServerHelloMsg. Phase 2
# extends with three capability booleans (D-03) and a negotiated-state
# badge value for the client health overlay.
@dataclass
class ServerHelloMsg:
    type: str = MsgType.SERVER_HELLO
    ...
    supports_yuv444: bool = True
    ...
    # Phase 2 additions (D-03 / D-05):
    supports_main10: bool = False
    supports_422: bool = False
    supports_444: bool = False  # rename-or-keep alongside supports_yuv444; deprecate the old flag
    color_negotiated_state: str = "not_supported"  # "negotiated" | "confirmed" | "degraded" | "not_supported"
```

**KeyEvent lock-state bit extension** (D-14) — the existing `KEY_EVENT` message is currently dict-form on the wire (see `server/mac_input_injector.py::handle_message` lines 418-419); promote to a real `@dataclass KeyEventMsg` with `caps_lock_on: bool = False`, `num_lock_on: bool = False`, `scroll_lock_on: bool = False`.

---

### `common/session_fsm.py` — EXTEND (new `PenFSM`, D-19)

**Analog:** `common/session_fsm.py::ClientFSM` lines 108-160 (declarative `State` + `Transition` idiom).

**StateMachine subclass pattern** (copy from lines 108-160):

```python
# common/session_fsm.py lines 108-160 — the declarative State+transition
# shape to mirror for the Phase 2 PenFSM. Keep it in common/session_fsm.py
# per RESEARCH §Claude's Discretion #6 (pen state is orthogonal to session
# state but lives in the same module for discoverability).
class ClientFSM(StateMachine):
    disconnected = State(initial=True)
    handshaking = State()
    authenticating = State()
    ...
    closed = State(final=True)
    connect_requested = (
        disconnected.to(handshaking)
        | reconnecting.to(handshaking)
    )
    tls_ok = handshaking.to(authenticating)
    ...

# Phase 2 addition:
class PenFSM(StateMachine):
    out_of_proximity = State(initial=True)
    in_proximity = State()
    proximity_enter = (
        out_of_proximity.to(in_proximity)
        | in_proximity.to(in_proximity)  # idempotent per D-19
    )
    proximity_leave = in_proximity.to(out_of_proximity)
```

**Serialization contract** (from lines 30-38): `fsm.current_state.id` returns a short string that serializes verbatim; Phase 2 wire messages should embed `pen_state: "in_proximity" | "out_of_proximity"` on telemetry events only (not on every pen_event — that would waste bandwidth).

---

### `server/platform_backends.py` — EXTEND (InputInjector composes pen + non-pen)

**Analog:** `server/platform_backends.py` self (full file, lines 20-62).

**Import-time IS_MACOS branch** (copy from lines 42-51):

```python
# server/platform_backends.py lines 42-51 — the exact dispatch pattern.
# Phase 2 extends to compose MacInputInjector + MacPenInjector into a
# single facade.
if IS_MACOS:
    from server.mac_input_injector import MacInputInjector as InputInjector  # noqa: F401
    XTestInputInjector = InputInjector  # type: ignore
    logger.info("platform_backends: using MacInputInjector (CoreGraphics)")
else:
    from server.input_injector import InputInjector  # noqa: F401
    from server.xtest_injector import XTestInputInjector  # noqa: F401
```

**Phase 2 composition (new)**: wrap `MacInputInjector` with a facade class that delegates `pen_event` to `MacPenInjector` when `_HAS_IOKIT` and the pen helper initialized cleanly, else falls back to the existing `MacInputInjector.pen_event` mouse-click stub (D-07 failure-path).

---

### `server/video_encoder.py` — EXTEND (ENCODER_DEFS capability flags)

**Analog:** `server/video_encoder.py::HWEncoder` + `ENCODER_DEFS` lines 37-69.

**Dataclass extension shape** (copy from lines 37-69):

```python
# server/video_encoder.py lines 37-69 — the HWEncoder dataclass + ENCODER_DEFS
# list. Phase 2 adds three capability flags — follow the existing bool
# default + priority-ordered list shape verbatim.
@dataclass
class HWEncoder:
    name: str
    codec: str
    backend: str
    supports_444: bool
    supports_lossless: bool
    priority: int
    # Phase 2 additions (CONTEXT.md Claude's Discretion #3 → RESEARCH §NVENC matrix):
    supports_main10: bool = False
    supports_422: bool = False
    # supports_444 already present — Phase 2 keeps it but ENCODER_DEFS must
    # flip to True on Ada (HEVC 4:4:4) and Blackwell (HEVC + AV1 4:4:4).

ENCODER_DEFS = [
    HWEncoder("h264_nvenc",  "h264", "nvenc",
              supports_444=True,  supports_lossless=True,  priority=10),
    HWEncoder("hevc_nvenc",  "h265", "nvenc",
              supports_444=True,  supports_lossless=True,  priority=10,
              supports_main10=True,  # Turing+
              supports_422=False),   # Blackwell-only; runtime probe confirms
    ...
]
```

---

### `server/mac_input_injector.py` — REFACTOR (thin pen delegation, D-09)

**Analog:** `server/mac_input_injector.py::pen_event` lines 358-398 (the thing being thinned — this is the pre-existing downgrade-to-mouse stub that D-09 replaces with a delegation).

**What changes** (from lines 358-398):

```python
# server/mac_input_injector.py lines 358-398 — the CURRENT stub.
# Phase 2 D-09 refactor replaces the stub body with:
#
#   if self._pen_injector is not None:
#       return self._pen_injector.pen_event(x, y, pressure, ...)
#   # Fallback: legacy mouse-click downgrade (documented v1 limitation
#   # per D-07 if the IOHIDUserDevice spike failed).
#   ... existing mouse-event synthesis ...
def pen_event(self, x: float, y: float, pressure: float,
              tilt_x: float = 0.0, tilt_y: float = 0.0,
              rotation: float = 0.0,
              button: int = 0, pressed: bool = False,
              hovering: bool = False, pen_type: str = "pen"):
    if not MacInputInjector._pen_warned and pressure > 0:
        logger.warning(
            "Pen pressure/tilt injection not implemented on macOS — "
            "pen events are being downgraded to mouse clicks "
            "(pressure dropped)."
        )
        MacInputInjector._pen_warned = True
    ...
```

---

### `docs/release.md` — SEED (Wacom runbook + DXS latency number)

**Analog:** None in-tree. `docs/` directory does not exist yet (confirmed `ls docs/` returns "No such file or directory"). Phase 2 creates the directory + seed file.

**Scope (from CONTEXT.md D-16 + D-21)**: release runbook containing (a) the 4-cell Wacom matrix per-release ritual, (b) per-release `tools/wacom_quant_analysis.py` RMS numbers, (c) pre-vs-post Phase-2 DXS latency measurement. File is load-bearing for Phase 6 release process so seed it with template sections now.

---

## Shared Patterns (cross-cutting)

### Deferred PyObjC import with `_HAS_X` flag

**Source:** `server/mac_screen_capture.py` lines 63-101, `server/mac_input_injector.py` lines 52-91.

**Apply to:** `server/mac_video_encoder.py`, `server/mac_pen_injector.py`.

```python
try:
    import objc
    from Foundation import NSObject
    import VideoToolbox as VT  # or: IOKit = objc.loadBundle('IOKit', ...)
    _HAS_VT = True
except Exception as _e:
    _HAS_VT = False
    _import_error = _e

class MacVideoEncoderError(RuntimeError):
    pass

def _check_vt_available():
    if not _HAS_VT:
        raise MacVideoEncoderError(
            f"VideoToolbox / PyObjC not available: {_import_error}. "
            "Run: pip install pyobjc-framework-VideoToolbox "
            "pyobjc-framework-CoreVideo pyobjc-framework-CoreMedia"
        )
```

### structlog event naming (Phase 1 contract)

**Source:** `common/logging.py` lines 1-70 (canonical schema).

**Apply to:** Wacom matrix counters (D-17), capability-probe outcome, IOHIDUserDevice lifecycle, encoder-reconfigure events.

```python
# common/logging.py lines 9-32 — the schema contract:
#   event:   dotted short name, first positional arg
#   phase:   "server" | "client" | "broker"
#   session_id / client_id / stage: session-scoped fields
# Apply to Phase 2 events, for example:
from common.logging import get_logger
log = get_logger("server.mac_video_encoder")
log.info("encode.session.start",
         width=width, height=height,
         profile="HEVC_Main10_AutoLevel",
         low_latency_rc=True)
log.warning("capability.probe.main10_unsupported",
            gpu_name="NVIDIA Turing T4",
            probe_source="ffmpeg_trial")
```

### Mock-at-subprocess-boundary / mock-at-PyObjC-boundary (Phase 1 D-02)

**Source:** `tests/server/test_video_encoder_mock.py` lines 36-62.

**Apply to:** every new `tests/server/test_mac_*.py` file + `tests/server/test_hw_capability_probe.py`. Never let a real `ffmpeg`, `subprocess`, or PyObjC framework call cross the test boundary.

### Dataclass protocol message + JSON round-trip test

**Source:** `common/messages.py` (dataclass patterns) + `tests/common/test_messages.py` (round-trip pattern).

**Apply to:** every new MsgType (`KEY_RESET_MODIFIERS`, `TEXT_COMMIT`, `PEN_PROXIMITY`) + extended `KeyEventMsg` / `ServerHelloMsg`. Every new dataclass ships with a round-trip test in `tests/common/test_messages_phase2.py`.

### Platform dispatch via `server/platform_backends.py` only

**Source:** `server/platform_backends.py` full file + CONVENTIONS.md "Platform Branching".

**Apply to:** the new `MacPenInjector`. Import at the top of `platform_backends.py` behind `if IS_MACOS:`, compose into the `InputInjector` facade. Do NOT scatter `sys.platform == "darwin"` checks into `server/session_runtime.py` or `server/main.py`.

### Fixture graceful-degradation on missing binary asset

**Source:** `tests/conftest.py::canned_hevc_keyframes` lines 69-89.

**Apply to:** the new `tests/smoke/fixtures/ten_bit_ramp.y4m` loader. If the fixture is missing, return a sentinel rather than failing collection — lets Wave 0 develop the test scaffolding before the binary asset is committed.

---

## No Analog Found

Files with no close match in the codebase — the planner should front-load these in Wave 0 or Wave 1 with extra review scrutiny, extra smoke coverage, and a named owner for the spike path.

| File | Why No Analog | Guidance for Planner |
|------|---------------|----------------------|
| `server/mac_video_encoder.py` (VTCompressionSession) | No existing code uses VideoToolbox directly from PyObjC; `server/video_encoder.py` calls FFmpeg as a subprocess, not the VT C API. The async callback → asyncio bridge is similar in shape to `server/mac_screen_capture.py`'s SCK delegate bridge but VT-specific binding idioms (`kVTVideoEncoderSpecification_*`, `CMSampleBufferGetDataBuffer`) are not exercised anywhere in the tree. | Land on a very early wave (suggest Wave 2 per RESEARCH). Borrow the `mac_screen_capture.py` delegate-thread pattern. Mock the VT functions directly in tests. Plan-check must include an explicit "low-latency rate control took effect" smoke check (VT silently ignores unsupported specs). |
| `server/mac_pen_injector.py` (IOHIDUserDevice) | Completely new surface — no IOKit usage anywhere in the tree. `server/input_injector.py::VirtualPenTablet` is the shape-closest reference (HID descriptor + report packing on Linux uinput) but the OS APIs are wholly different. | The 2-day D-07/D-08 pre-phase spike is the gate. Planner MUST sequence the spike first and branch scope on the outcome (D-09 "productionize" vs D-07 "document + ship"). Mock at the `IOHIDUserDeviceCreate` + `IOHIDUserDeviceHandleReport` boundary for unit tests. |
| `client/viewer.py` video-layer refactor (QRhiWidget Metal + P010 YUV→RGB shader) | Current viewer uses `QPainter.drawPixmap` on QImage (8-bit blit — one of the 9 silent downgrade points). `QRhiWidget` is Qt 6.10 new territory; no prior Metal/shader work in the tree. | Largest client-side change in Phase 2 — gets its own plan per CONTEXT.md §Specific Ideas. Accept `QOpenGLWidget` + custom fragment shader as the documented escape hatch if QRhi P010 upload proves painful. Keep QImage path alive for overlay + cursor layers. Plan-check must assert the final-blit shader is BT.709 (not BT.2020 — HDR tone-mapping is out of scope per D-06). |
| `tools/wacom_quant_analysis.py` / `docs/release.md` | New directories / first file in a package not included in the wheel (`tools/*` and `docs/*` are not in `pyproject.toml` packages.find include). | Low risk but keep scope tight: `tools/wacom_quant_analysis.py` is argparse single-file < 200 lines per RESEARCH #7. `docs/release.md` is markdown only — the ritual/runbook text lives here but the structlog counters + RMS numbers it ingests come from the server-side Phase 1 structlog event stream, so no Python wiring beyond ingestion. |

---

## Metadata

**Analog search scope:**
- `tests/common/`, `tests/server/`, `tests/client/`, `tests/integration/`, `tests/smoke/`, `tests/conftest.py`
- `server/*.py` (full server package)
- `client/viewer.py`, `client/video_decoder.py`, `client/key_diagnostic.py`
- `common/messages.py`, `common/keymap.py`, `common/session_fsm.py`, `common/logging.py`
- `tools/keydiag.py`
- Top-level `docs/` (confirmed absent)
- `tests/smoke/fixtures/` (confirmed: only `canned_encoded_frames.bin` exists)

**Files scanned:** ~30 source files, ~15 test files.

**Pattern extraction date:** 2026-04-18.
