"""Root test fixtures.

Shared across unit + integration + smoke layers. Fixtures specific to
one scope should live in tests/<scope>/conftest.py.
"""
import pathlib

import pytest


class FakeWebSocket:
    """Stub websocket — records sent bytes, never blocks."""

    def __init__(self):
        self.sent: list = []
        self.closed = False
        # ClientSession reads `ws.remote_address` in __init__
        self.remote_address = ("127.0.0.1", 0)

    async def send(self, data):
        if self.closed:
            raise RuntimeError("send on closed ws")
        self.sent.append(data)

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_ws():
    return FakeWebSocket()


class FakeEncoder:
    """Stand-in for server.video_encoder.VideoEncoder — tracks request_keyframe."""

    def __init__(self, frames=None):
        self._frames = frames or []
        self._cursor = 0
        self.keyframe_requests = 0
        self._on_frame = None

    def start(self, on_encoded_frame):
        self._on_frame = on_encoded_frame

    def feed_frame(self, raw_bgra):
        if self._cursor < len(self._frames) and self._on_frame is not None:
            data, is_kf = self._frames[self._cursor]
            self._cursor += 1
            self._on_frame(data, is_kf)

    def request_keyframe(self):
        self.keyframe_requests += 1
        # Advance cursor to next canned keyframe so next feed_frame emits one
        for i in range(self._cursor, len(self._frames)):
            if self._frames[i][1]:  # is_keyframe
                self._cursor = i
                return

    def stop(self):
        pass


@pytest.fixture
def fake_encoder(canned_hevc_keyframes):
    return FakeEncoder(canned_hevc_keyframes)


@pytest.fixture
def canned_hevc_keyframes():
    """Load length-prefixed canned encoded frames.

    Layout on disk: repeating [uint32 big-endian size][uint8 is_keyframe][size bytes payload].
    """
    path = (pathlib.Path(__file__).parent /
            "smoke" / "fixtures" / "canned_encoded_frames.bin")
    if not path.exists():
        # Graceful fallback so collection doesn't fail pre-wave-0
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


@pytest.fixture(scope="module")
def ten_bit_ramp_bytes():
    """Phase 2 D-01 - raw bytes of the 10-bit P010 ramp fixture.

    Loaded module-scope to avoid repeated 7.4 MB reads across the 9-checkpoint
    harness. Mirrors the canned_hevc_keyframes graceful-degradation pattern:
    returns None if the binary is missing so tests can skip without failing
    collection pre-Wave-0.
    """
    path = (pathlib.Path(__file__).parent /
            "smoke" / "fixtures" / "10bit_ramp.p010.bin")
    if not path.exists():
        return None
    return path.read_bytes()


# ═══════════════════════════════════════════════════════════════════════
# Phase 3 fixtures — display + multi-monitor + clipboard
#
# Plan 03-01 Wave 0 scaffolding. Consumed by the 19 new skeleton test
# files landing in Plan 03-01 Task 2 + the implementation tests that
# land in Plans 02-06. Kept at the root `tests/conftest.py` level so
# every nested scope (common / server / client / integration) inherits
# them without re-authoring.
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_nsscreen():
    """Fake NSScreen / QScreen list for D-06 per-screen DPR tests.

    Returns a list of fake screen objects exposing ``devicePixelRatio()``
    and ``name()`` like Qt's :class:`QScreen`. The two-screen layout
    mirrors the canonical D-08 mixed-DPI spike topology: a Retina
    built-in display (DPR 2.0) plus an external 4K monitor (DPR 1.0).
    """
    from types import SimpleNamespace
    screens = [
        SimpleNamespace(
            devicePixelRatio=lambda: 2.0,
            name=lambda: "Built-in Retina Display",
        ),
        SimpleNamespace(
            devicePixelRatio=lambda: 1.0,
            name=lambda: "DELL U2723QE",
        ),
    ]
    return screens


@pytest.fixture
def fake_mss_monitor_list():
    """Fake ``mss.mss().monitors`` output for D-02 / D-09 hot-plug tests.

    Per the mss convention the first entry is the full virtual desktop;
    subsequent entries are individual physical monitors. This layout
    matches the DXS flame-lab topology (2 × 2560×1600 at 5120×1600).
    """
    return [
        {"left": 0, "top": 0, "width": 5120, "height": 1600},    # virtual desktop
        {"left": 0, "top": 0, "width": 2560, "height": 1600},    # primary
        {"left": 2560, "top": 0, "width": 2560, "height": 1600}, # secondary
    ]


@pytest.fixture
def fixture_png():
    """Generate a tiny valid 1×1 PNG for clipboard-image tests (D-16).

    Returns raw bytes of a deterministic 1×1 black-with-alpha RGBA pixel
    PNG — the 8-byte magic signature + IHDR + IDAT + IEND — valid
    through PIL.Image.verify() so Plan 03-06's defense-in-depth path
    (magic-byte + PIL.verify) accepts it. Threat T-03-04 disposition:
    accept — no PII risk in a single black pixel.

    Plan 03-06 fix (Rule 1): the Wave 0 scaffold payload had a bad
    IDAT checksum which passed the magic-byte check but failed
    ``PIL.Image.verify``. Replaced with a byte-identical-length 1×1
    RGBA PNG whose CRCs round-trip cleanly.
    """
    import base64
    return base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlE"
        b"QVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg=="
    )
