"""teraguchi doctor — cross-platform readiness / capability probe.

Runs on any machine (server host, client, or both) and prints the resolved
backend for each pipeline layer plus *why the alternatives were rejected*.
This is the "identify platform -> pick working tech -> fall back, visibly"
requirement from `.claude/ARCHITECTURE_DIRECTION.md` (section 3 + 5).

Design goals:
- **Zero heavy imports.** Detection is `shutil.which` (tools) +
  `importlib.util.find_spec` (modules) so this runs on a fresh box *before*
  requirements-*.txt is installed — it's the first thing you run when setting
  up a new test machine.
- **Testable.** The two detection primitives (`_has_tool`, `_has_module`) and
  `detect_platform` are module-level and monkeypatchable; every resolver is a
  pure function of them, so tests force a scenario and assert the fallback.
- **No silent downgrade.** Every layer reports the full candidate list with an
  availability flag + note, not just the winner.

Usage:
    python -m common.doctor                 # both roles, human table
    python -m common.doctor --role server   # host only
    python -m common.doctor --role client   # viewer only
    python -m common.doctor --json          # machine-readable (for smoke_matrix)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import sys
from dataclasses import asdict, dataclass, field
from typing import Optional


# Supported Python range. Policy: use the host's *native* interpreter when it
# falls in this range (keeps installs isolated — no runtime dropped on the host).
# 3.9 is the verified floor (server imports + runs on RHEL9 platform-python3.9);
# 3.12 is recommended. Bump the ceiling as newer interpreters are validated.
SUPPORTED_PY_MIN = (3, 9)
SUPPORTED_PY_MAX_EXCL = (3, 14)
RECOMMENDED_PY = (3, 12)


def _ver(t: tuple) -> str:
    return ".".join(str(x) for x in t)


# ---------------------------------------------------------------------------
# Detection primitives (monkeypatch these in tests)
# ---------------------------------------------------------------------------

def _has_tool(name: str) -> bool:
    """True if an executable is on PATH."""
    return shutil.which(name) is not None


def _has_module(name: str) -> bool:
    """True if a Python module is importable without importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


@dataclass
class Platform:
    os: str          # "linux" | "macos" | "windows" | "other"
    raw: str         # sys.platform
    arch: str        # platform.machine()
    python: str      # "3.12.4"
    display_server: str = ""  # linux only: "x11" | "wayland" | ""


def detect_platform() -> Platform:
    raw = sys.platform
    if raw.startswith("linux"):
        os_name = "linux"
    elif raw == "darwin":
        os_name = "macos"
    elif raw.startswith("win"):
        os_name = "windows"
    else:
        os_name = "other"

    display = ""
    if os_name == "linux":
        import os as _os
        if _os.environ.get("WAYLAND_DISPLAY"):
            display = "wayland"
        elif _os.environ.get("DISPLAY"):
            display = "x11"

    return Platform(
        os=os_name,
        raw=raw,
        arch=platform.machine(),
        python=platform.python_version(),
        display_server=display,
    )


# ---------------------------------------------------------------------------
# Resolution model
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    name: str
    available: bool
    note: str = ""


@dataclass
class Layer:
    """One pipeline layer's decision: the chosen backend + full candidate list.

    ``chosen`` is the first available candidate in priority order, or None if
    the whole chain is unavailable (a real gap on this machine).
    """
    layer: str
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def chosen(self) -> Optional[str]:
        for c in self.candidates:
            if c.available:
                return c.name
        return None

    @property
    def ok(self) -> bool:
        return self.chosen is not None


def _layer(name: str, *candidates: Candidate) -> Layer:
    return Layer(layer=name, candidates=list(candidates))


# ---------------------------------------------------------------------------
# Server (host) layer resolvers
# ---------------------------------------------------------------------------

def resolve_capture(p: Platform) -> Layer:
    if p.os == "linux":
        return _layer(
            "capture",
            Candidate("nvfbc", _has_tool("nvidia-smi"),
                      "NVIDIA zero-copy; needs helper build (server/nvfbc)"),
            Candidate("mss-x11", _has_module("mss") and p.display_server != "wayland",
                      "X11 screen grab fallback"),
            Candidate("pipewire", p.display_server == "wayland",
                      "Wayland portal capture (may force buffer copies)"),
        )
    if p.os == "macos":
        return _layer(
            "capture",
            Candidate("screencapturekit", _has_module("ScreenCaptureKit") or _has_module("Quartz"),
                      "P010 10-bit; fails hard under load -> needs reconnect"),
        )
    if p.os == "windows":
        return _layer(
            "capture",
            Candidate("wgc-dxgi", False, "Windows host not implemented (client-only today)"),
        )
    return _layer("capture", Candidate("none", False, "unsupported OS"))


def resolve_encode(p: Platform) -> Layer:
    ffmpeg = _has_tool("ffmpeg")
    if p.os == "linux":
        return _layer(
            "encode",
            Candidate("nvenc", ffmpeg and _has_tool("nvidia-smi"), "HW: NVIDIA"),
            Candidate("vaapi", ffmpeg and _has_tool("vainfo"), "HW: Intel/AMD VAAPI"),
            Candidate("software", ffmpeg, "SVT-AV1 / x265 / x264"),
        )
    if p.os == "macos":
        return _layer(
            "encode",
            Candidate("videotoolbox", _has_module("VideoToolbox") or ffmpeg, "HW: Apple VideoToolbox"),
            Candidate("software", ffmpeg, "x265 / x264 (no 4:4:4 on VT)"),
        )
    return _layer(
        "encode",
        Candidate("software", ffmpeg, "software only"),
    )


def resolve_input_inject(p: Platform) -> Layer:
    if p.os == "linux":
        import os as _os
        return _layer(
            "input_inject",
            Candidate("uinput", _os.path.exists("/dev/uinput"), "kernel virtual input"),
            Candidate("xtest", p.display_server == "x11", "X11 XTest fallback"),
        )
    if p.os == "macos":
        return _layer(
            "input_inject",
            Candidate("corehid-pen", _has_module("CoreHID"),
                      "CoreHID HIDVirtualDevice — pen pressure (entitlement-gated)"),
            Candidate("coregraphics", _has_module("Quartz"),
                      "CGEventPost — mouse/kb only, NO pressure"),
        )
    if p.os == "windows":
        return _layer(
            "input_inject",
            Candidate("sendinput-vmulti", False, "Windows host not implemented"),
        )
    return _layer("input_inject", Candidate("none", False, "unsupported OS"))


def resolve_tablet_inject(p: Platform) -> Layer:
    """Virtual tablet (Wacom) injection — the VirtualTablet server interface."""
    if p.os == "linux":
        import os as _os
        return _layer(
            "tablet_inject",
            Candidate("uhid", _os.path.exists("/dev/uhid"), "full HID report descriptor"),
            Candidate("usb-vhci", _has_module("usb_vhci"), "USB passthrough -> native wacom driver binds"),
            Candidate("uinput-abs", _os.path.exists("/dev/uinput"), "absolute-axis pen (no full HID)"),
        )
    if p.os == "macos":
        return _layer(
            "tablet_inject",
            Candidate("corehid", _has_module("CoreHID"),
                      "HIDVirtualDevice + com.apple.developer.hid.virtual.device (pending Apple)"),
        )
    if p.os == "windows":
        return _layer(
            "tablet_inject",
            Candidate("vmulti", False, "TeraVHID/vmulti driver (future)"),
        )
    return _layer("tablet_inject", Candidate("none", False, "unsupported OS"))


# ---------------------------------------------------------------------------
# Client (viewer) layer resolvers
# ---------------------------------------------------------------------------

def resolve_decode(p: Platform) -> Layer:
    return _layer(
        "decode",
        Candidate("pyav-hw", _has_module("av"), "PyAV w/ platform hwaccel"),
        Candidate("ffmpeg-sw", _has_tool("ffmpeg"), "software decode fallback"),
    )


def resolve_display(p: Platform) -> Layer:
    qt = _has_module("PySide6")
    api = {"macos": "Metal", "windows": "D3D", "linux": "GL/Vulkan"}.get(p.os, "GL")
    return _layer(
        "display",
        Candidate("qt-qrhi", qt, f"Qt QRhi ({api}) GPU blit"),
        Candidate("qt-qimage", qt, "QImage software blit"),
    )


def resolve_tablet_source(p: Platform) -> Layer:
    if p.os == "macos":
        return _layer(
            "tablet_source",
            Candidate("iohid-read", _has_module("Quartz") or _has_module("CoreHID"),
                      "read physical Wacom HID (TCC-gated)"),
        )
    if p.os == "windows":
        return _layer(
            "tablet_source",
            Candidate("raw-hid-wintab", False, "raw HID / WinTab (future)"),
        )
    if p.os == "linux":
        return _layer(
            "tablet_source",
            Candidate("evdev", _has_module("evdev"), "read /dev/input event device"),
        )
    return _layer("tablet_source", Candidate("none", False, "unsupported OS"))


def resolve_runtime(p: Platform) -> Layer:
    """Python runtime version gate — shared by both roles.

    ``available`` = the host's native interpreter is within the supported
    range, so we can use it as-is (no runtime install → isolated). Out of
    range is a real blocker: install an isolated in-range Python (bundled,
    not touching the host).
    """
    try:
        cur = tuple(int(x) for x in p.python.split(".")[:2])
    except (ValueError, AttributeError):
        cur = (0, 0)
    in_range = SUPPORTED_PY_MIN <= cur < SUPPORTED_PY_MAX_EXCL
    note = (f"native {p.python}; supported {_ver(SUPPORTED_PY_MIN)}-<"
            f"{_ver(SUPPORTED_PY_MAX_EXCL)}, recommended {_ver(RECOMMENDED_PY)}")
    return _layer("runtime", Candidate(f"python-{p.python}", in_range, note))


def resolve_transport(p: Platform) -> Layer:
    """Shared by both roles."""
    return _layer(
        "transport",
        Candidate("quic", _has_module("aioquic"),
                  "QUIC datagrams+streams, 0-RTT, migration (FEC = gap)"),
        Candidate("hybrid-udp-tcp", _has_module("websockets"),
                  "UDP media + TCP control, auto-fallback"),
        Candidate("tcp-only", _has_module("websockets"),
                  "WebSocket only (lossy-link fallback, HoL risk)"),
    )


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

@dataclass
class Report:
    platform: Platform
    role: str
    layers: list[Layer] = field(default_factory=list)

    @property
    def blockers(self) -> list[str]:
        return [ly.layer for ly in self.layers if not ly.ok]


def build_report(role: str, p: Optional[Platform] = None) -> Report:
    p = p or detect_platform()
    layers: list[Layer] = [resolve_runtime(p)]

    if role in ("server", "both"):
        layers += [
            resolve_capture(p),
            resolve_encode(p),
            resolve_input_inject(p),
            resolve_tablet_inject(p),
        ]
    if role in ("client", "both"):
        layers += [
            resolve_decode(p),
            resolve_display(p),
            resolve_tablet_source(p),
        ]
    # transport is shared; add once
    layers.append(resolve_transport(p))

    return Report(platform=p, role=role, layers=layers)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_GREEN, _YELLOW, _RED, _DIM, _RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def _color(s: str, code: str, use_color: bool) -> str:
    return f"{code}{s}{_RESET}" if use_color else s


def format_report(report: Report, use_color: bool = True) -> str:
    p = report.platform
    lines: list[str] = []
    lines.append("=" * 68)
    lines.append(f"  teraguchi doctor - role: {report.role}")
    lines.append("=" * 68)
    lines.append(f"  OS: {p.os} ({p.raw})   arch: {p.arch}   python: {p.python}"
                 + (f"   display: {p.display_server}" if p.display_server else ""))
    lines.append("-" * 68)

    for ly in report.layers:
        chosen = ly.chosen
        if chosen:
            head = _color(f"  {ly.layer:<15}", "", use_color)
            mark = _color("[OK]", _GREEN, use_color)
            lines.append(f"{head} {mark} {_color(chosen, _GREEN, use_color)}")
        else:
            head = f"  {ly.layer:<15}"
            mark = _color("[!!]", _RED, use_color)
            lines.append(f"{head} {mark} {_color('NO WORKING BACKEND', _RED, use_color)}")
        for c in ly.candidates:
            if c.name == chosen:
                continue
            state = _color("avail", _YELLOW, use_color) if c.available else _color(" -- ", _DIM, use_color)
            note = _color(f"- {c.note}", _DIM, use_color) if c.note else ""
            lines.append(f"      {state} {c.name:<18} {note}")

    lines.append("-" * 68)
    if report.blockers:
        lines.append(_color(f"  BLOCKERS: {', '.join(report.blockers)}", _RED, use_color))
    else:
        lines.append(_color("  READY - every layer has a working backend", _GREEN, use_color))
    lines.append("=" * 68)
    return "\n".join(lines)


def report_to_dict(report: Report) -> dict:
    d = {
        "platform": asdict(report.platform),
        "role": report.role,
        "ready": not report.blockers,
        "blockers": report.blockers,
        "layers": [],
    }
    for ly in report.layers:
        d["layers"].append({
            "layer": ly.layer,
            "chosen": ly.chosen,
            "candidates": [asdict(c) for c in ly.candidates],
        })
    return d


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="teraguchi-doctor",
        description="Cross-platform readiness / capability probe.",
    )
    ap.add_argument("--role", choices=["server", "client", "both"], default="both")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    # Windows legacy consoles default to cp1252 and choke on any non-ASCII in
    # candidate notes. Reconfigure to UTF-8 (3.7+) so output never crashes.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    report = build_report(args.role)

    if args.json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        use_color = (not args.no_color) and sys.stdout.isatty()
        print(format_report(report, use_color=use_color))

    # Non-zero exit if this machine can't fulfil the requested role — lets
    # smoke_matrix.sh / CI gate on readiness.
    return 1 if report.blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
