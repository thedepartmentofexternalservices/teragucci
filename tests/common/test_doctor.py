"""Tests for common/doctor.py — the readiness / capability probe.

These lock the *fallback logic*: given a controlled machine state (which
tools/modules exist, which OS), assert that each pipeline layer resolves to
the correct backend and degrades to the right fallback. Detection is faked by
monkeypatching the two primitives (`_has_tool`, `_has_module`) + `Platform`,
so the assertions are independent of the machine actually running the tests.
"""
import pytest

from common import doctor
from common.doctor import Platform


def _plat(os_name, display="", arch="x86_64"):
    return Platform(os=os_name, raw=os_name, arch=arch, python="3.12.0",
                    display_server=display)


@pytest.fixture
def fake_env(monkeypatch):
    """Returns a helper that installs a fake (tools, modules, /dev nodes) world."""
    state = {"tools": set(), "modules": set(), "paths": set()}

    monkeypatch.setattr(doctor, "_has_tool", lambda n: n in state["tools"])
    monkeypatch.setattr(doctor, "_has_module", lambda n: n in state["modules"])
    # input/tablet resolvers probe /dev nodes via os.path.exists
    import os as _os
    monkeypatch.setattr(_os.path, "exists", lambda p: p in state["paths"])
    return state


# ---------------------------------------------------------------------------
# Encode fallback chain: NVENC -> VAAPI -> software
# ---------------------------------------------------------------------------

def test_encode_prefers_nvenc_when_gpu_present(fake_env):
    fake_env["tools"] |= {"ffmpeg", "nvidia-smi"}
    layer = doctor.resolve_encode(_plat("linux"))
    assert layer.chosen == "nvenc"


def test_encode_falls_back_to_vaapi_without_nvidia(fake_env):
    fake_env["tools"] |= {"ffmpeg", "vainfo"}
    layer = doctor.resolve_encode(_plat("linux"))
    assert layer.chosen == "vaapi"


def test_encode_falls_back_to_software_without_hw(fake_env):
    fake_env["tools"] |= {"ffmpeg"}
    layer = doctor.resolve_encode(_plat("linux"))
    assert layer.chosen == "software"


def test_encode_has_no_backend_without_ffmpeg(fake_env):
    layer = doctor.resolve_encode(_plat("linux"))
    assert layer.chosen is None
    assert not layer.ok


# ---------------------------------------------------------------------------
# Transport fallback chain: QUIC -> hybrid-udp-tcp -> tcp-only
# ---------------------------------------------------------------------------

def test_transport_prefers_quic(fake_env):
    fake_env["modules"] |= {"aioquic", "websockets"}
    layer = doctor.resolve_transport(_plat("linux"))
    assert layer.chosen == "quic"


def test_transport_falls_back_when_quic_blocked(fake_env):
    # aioquic missing (or UDP blocked) -> hybrid UDP+TCP over websockets
    fake_env["modules"] |= {"websockets"}
    layer = doctor.resolve_transport(_plat("linux"))
    assert layer.chosen == "hybrid-udp-tcp"


def test_transport_no_backend_without_websockets(fake_env):
    layer = doctor.resolve_transport(_plat("linux"))
    assert layer.chosen is None


# ---------------------------------------------------------------------------
# Capture is OS-specific
# ---------------------------------------------------------------------------

def test_capture_macos_uses_screencapturekit(fake_env):
    fake_env["modules"] |= {"Quartz"}
    layer = doctor.resolve_capture(_plat("macos"))
    assert layer.chosen == "screencapturekit"


def test_capture_linux_prefers_nvfbc(fake_env):
    fake_env["tools"] |= {"nvidia-smi"}
    layer = doctor.resolve_capture(_plat("linux", display="x11"))
    assert layer.chosen == "nvfbc"


def test_capture_linux_x11_falls_back_to_mss(fake_env):
    fake_env["modules"] |= {"mss"}
    layer = doctor.resolve_capture(_plat("linux", display="x11"))
    assert layer.chosen == "mss-x11"


def test_capture_linux_wayland_uses_pipewire(fake_env):
    # mss present but Wayland session -> mss-x11 is unavailable, pipewire wins
    fake_env["modules"] |= {"mss"}
    layer = doctor.resolve_capture(_plat("linux", display="wayland"))
    assert layer.chosen == "pipewire"


def test_capture_windows_host_is_unavailable(fake_env):
    layer = doctor.resolve_capture(_plat("windows"))
    assert not layer.ok  # client-only today


# ---------------------------------------------------------------------------
# Input inject: macOS pen vs mouse-only
# ---------------------------------------------------------------------------

def test_input_macos_prefers_corehid_pen(fake_env):
    fake_env["modules"] |= {"CoreHID", "Quartz"}
    layer = doctor.resolve_input_inject(_plat("macos"))
    assert layer.chosen == "corehid-pen"


def test_input_macos_falls_back_to_coregraphics_without_corehid(fake_env):
    # No CoreHID (entitlement not granted / old macOS) -> CGEventPost, no pressure
    fake_env["modules"] |= {"Quartz"}
    layer = doctor.resolve_input_inject(_plat("macos"))
    assert layer.chosen == "coregraphics"


def test_input_linux_prefers_uinput(fake_env):
    fake_env["paths"] |= {"/dev/uinput"}
    layer = doctor.resolve_input_inject(_plat("linux", display="x11"))
    assert layer.chosen == "uinput"


def test_input_linux_falls_back_to_xtest(fake_env):
    layer = doctor.resolve_input_inject(_plat("linux", display="x11"))
    assert layer.chosen == "xtest"


# ---------------------------------------------------------------------------
# Runtime version gate (use native when in range)
# ---------------------------------------------------------------------------

def test_runtime_native_in_range_is_usable(fake_env):
    layer = doctor.resolve_runtime(_plat("linux").__class__(
        os="linux", raw="linux", arch="x86_64", python="3.9.19"))
    assert layer.ok  # native 3.9 on the locked RHEL box is in range -> use it


def test_runtime_recommended_version_in_range(fake_env):
    p = Platform(os="macos", raw="darwin", arch="arm64", python="3.12.4")
    assert doctor.resolve_runtime(p).ok


def test_runtime_too_old_is_blocker(fake_env):
    p = Platform(os="linux", raw="linux", arch="x86_64", python="3.8.10")
    assert not doctor.resolve_runtime(p).ok


def test_runtime_too_new_is_blocker(fake_env):
    p = Platform(os="linux", raw="linux", arch="x86_64", python="3.14.0")
    assert not doctor.resolve_runtime(p).ok


def test_runtime_layer_present_in_report(fake_env, monkeypatch):
    monkeypatch.setattr(doctor, "detect_platform", lambda: _plat("linux", display="x11"))
    report = doctor.build_report("server")
    assert report.layers[0].layer == "runtime"


# ---------------------------------------------------------------------------
# Report assembly + role gating
# ---------------------------------------------------------------------------

def test_server_report_omits_client_layers(fake_env, monkeypatch):
    monkeypatch.setattr(doctor, "detect_platform", lambda: _plat("linux", display="x11"))
    report = doctor.build_report("server")
    names = {ly.layer for ly in report.layers}
    assert "capture" in names and "encode" in names
    assert "decode" not in names and "display" not in names
    assert "transport" in names  # shared


def test_client_report_omits_server_layers(fake_env, monkeypatch):
    monkeypatch.setattr(doctor, "detect_platform", lambda: _plat("macos"))
    report = doctor.build_report("client")
    names = {ly.layer for ly in report.layers}
    assert "decode" in names and "display" in names
    assert "capture" not in names and "encode" not in names


def test_blockers_lists_layers_without_backend(fake_env, monkeypatch):
    # Empty world -> everything blocked
    monkeypatch.setattr(doctor, "detect_platform", lambda: _plat("linux"))
    report = doctor.build_report("both")
    assert report.blockers  # non-empty
    assert "transport" in report.blockers


def test_ready_report_has_no_blockers(fake_env, monkeypatch):
    fake_env["tools"] |= {"ffmpeg", "nvidia-smi"}
    fake_env["modules"] |= {"aioquic", "websockets", "av", "PySide6", "mss", "evdev"}
    fake_env["paths"] |= {"/dev/uinput", "/dev/uhid"}
    monkeypatch.setattr(doctor, "detect_platform", lambda: _plat("linux", display="x11"))
    report = doctor.build_report("server")
    assert not report.blockers, f"unexpected blockers: {report.blockers}"


# ---------------------------------------------------------------------------
# Serialization (smoke_matrix consumes --json)
# ---------------------------------------------------------------------------

def test_report_to_dict_is_json_shaped(fake_env, monkeypatch):
    monkeypatch.setattr(doctor, "detect_platform", lambda: _plat("macos"))
    d = doctor.report_to_dict(doctor.build_report("both"))
    assert set(d) >= {"platform", "role", "ready", "blockers", "layers"}
    assert isinstance(d["layers"], list)
    assert all({"layer", "chosen", "candidates"} <= set(ly) for ly in d["layers"])
