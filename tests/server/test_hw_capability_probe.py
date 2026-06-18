"""D-03 / VIDEO-03 / VIDEO-05 - hw capability probe with subprocess mocked.

Mirrors the tests/server/test_video_encoder_mock.py D-02 mock-at-subprocess
pattern applied to the nvidia-smi + VT trial-encode boundary used by
server/capability_probe.py to determine supports_main10 / supports_422 /
supports_444 flags for the ServerHelloMsg capability handshake.

Wave 1 (02-04) implementation: these 4 tests are now passing (no xfail).

Do NOT:
- Invoke real nvidia-smi, ffmpeg, or VT APIs.
- Depend on a specific GPU being present on the runner.
"""
from __future__ import annotations

from unittest import mock

import pytest

from server.capability_probe import probe_nvenc_main10, probe_vt_main10

# ---------------------------------------------------------------------------
# subprocess.run fixtures for the 4 hardware classes the probe cares about
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_nvidia_smi_ada(monkeypatch):
    """RTX 4090 (Ada): Main10 trial succeeds, no 4:2:2 trial (pre-Blackwell)."""

    def fake_run(cmd, **kw):
        if "nvidia-smi" in cmd[0]:
            r = mock.MagicMock()
            r.returncode = 0
            r.stdout = "NVIDIA GeForce RTX 4090, 570.86\n"
            r.stderr = ""
            return r
        if "ffmpeg" in cmd[0]:
            r = mock.MagicMock()
            r.returncode = 0
            r.stderr = b""
            r.stdout = b""
            return r
        raise AssertionError(f"unexpected cmd {cmd!r}")

    monkeypatch.setattr("subprocess.run", fake_run)


@pytest.fixture
def mock_nvidia_smi_blackwell(monkeypatch):
    """RTX 5080 (Blackwell): Main10 + 4:2:2 trials both succeed."""

    def fake_run(cmd, **kw):
        if "nvidia-smi" in cmd[0]:
            r = mock.MagicMock()
            r.returncode = 0
            r.stdout = "NVIDIA GeForce RTX 5080, 575.10\n"
            r.stderr = ""
            return r
        if "ffmpeg" in cmd[0]:
            r = mock.MagicMock()
            r.returncode = 0
            r.stderr = b""
            r.stdout = b""
            return r
        raise AssertionError(f"unexpected cmd {cmd!r}")

    monkeypatch.setattr("subprocess.run", fake_run)


@pytest.fixture
def mock_no_nvidia(monkeypatch):
    """nvidia-smi missing (mss fallback path → all-False caps)."""

    def fake_run(cmd, **kw):
        raise FileNotFoundError("nvidia-smi not found")

    monkeypatch.setattr("subprocess.run", fake_run)


# ---------------------------------------------------------------------------
# Tests — the Wave 1 acceptance gate
# ---------------------------------------------------------------------------


def test_probe_detects_turing_main10(mock_nvidia_smi_ada):
    """Ada (RTX 40xx) NVENC must report main10=True, chroma_422=False.

    Name kept as _turing_main10 per Wave 0 skeleton — the test is a
    pre-Blackwell-NVENC capability assertion; Ada and Turing both fall
    under the 'supports Main10 but no 4:2:2' bucket.
    """
    caps = probe_nvenc_main10()
    assert caps["main10"] is True
    assert caps["chroma_422"] is False  # Ada is pre-Blackwell
    assert caps["chroma_444"] is True
    assert caps["gpu_family"] == "ada"


def test_probe_detects_blackwell_422(mock_nvidia_smi_blackwell):
    """Blackwell (RTX 50xx) NVENC must report chroma_422=True."""
    caps = probe_nvenc_main10()
    assert caps["main10"] is True
    assert caps["chroma_422"] is True
    assert caps["gpu_family"] == "blackwell"


def test_probe_detects_apple_silicon_main10():
    """probe_vt_main10 returns a shape-stable dict on every host.

    On a host without PyObjC VideoToolbox, probe_vt_main10 returns
    all-False caps without crashing — the actual True/False on real
    Apple Silicon is asserted by the D-01 9-checkpoint smoke when a
    real Mac server runs. Here we only assert the probe is defensive
    and returns the expected keys.
    """
    caps = probe_vt_main10()
    assert set(caps.keys()) >= {
        "main10",
        "chroma_422",
        "chroma_444",
        "low_latency_hevc",
        "probe_source",
    }


def test_probe_marks_mss_fallback_degraded(mock_no_nvidia):
    """No nvidia-smi present (mss fallback) must yield all-False caps.

    bootstrap._build_color_caps translates this into a 'not_supported'
    badge on the client overlay — the D-03 contract.
    """
    caps = probe_nvenc_main10()
    assert caps["main10"] is False
    assert caps["gpu_family"] == "unknown"
    assert caps["gpu_name"] == ""
