"""Bootstrap helpers — TLS context + system dependency checks.

Extracted from server/main.py (Plan 01-11 cleanup). Pure helpers with no
module-global dependencies; they can live outside main.py without
affecting the CLI entrypoint or the handle_client dispatch.

Phase 2 D-03: adds `build_color_caps()` which runs the hardware capability
probe at server startup and converts the probe result into a
`ServerColorCaps` dataclass (falling back to a compatible stand-in when
common/messages.py hasn't landed it yet in parallel wave-1 execution).
"""
from __future__ import annotations

import logging
import os
import ssl
import sys
from dataclasses import dataclass
from typing import Any

from server.capability_probe import probe_nvenc_main10, probe_vt_main10
from server.platform_backends import IS_MACOS

# ServerColorCaps lands in parallel plan 02-02. Until that merges into the
# same branch, we fall back to a local dataclass with the same shape so
# server/bootstrap.py is self-contained and importable.
try:
    from common.messages import ServerColorCaps  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - cross-wave merge fallback
    @dataclass
    class ServerColorCaps:  # type: ignore[no-redef]
        """Fallback stub until plan 02-02 lands the canonical dataclass."""

        main10: bool = False
        chroma_422: bool = False
        chroma_444: bool = False
        advertised_pix_fmt: str = "p010le"
        negotiated_state: str = "not_supported"


logger = logging.getLogger("teraguchi.server.bootstrap")


def check_system_dependencies() -> None:
    """Warn about missing system dependencies. Non-fatal — a missing tool
    just disables the corresponding feature (e.g. no ffmpeg → no video
    encoding; no xclip/xsel → no clipboard sync)."""
    import shutil
    if not shutil.which("ffmpeg"):
        logger.warning("Missing system dependency: ffmpeg — Video encoding will not work")

    if IS_MACOS:
        # Audio / clipboard / input are all supplied by native Cocoa
        # frameworks on macOS; none of the Linux CLI deps apply.
        return

    if not shutil.which("pactl"):
        logger.warning("Missing system dependency: pactl (PulseAudio) — Audio capture will not work")

    if not shutil.which("xclip") and not shutil.which("xsel"):
        logger.warning("Missing clipboard tool (xclip or xsel) — clipboard sync disabled")

    if not os.path.exists("/dev/uinput"):
        logger.warning("/dev/uinput not found — input injection may fail. Run: sudo modprobe uinput")


def create_tls_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    """Build the server-side TLS context. TLS 1.2 minimum (see STAB-02)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


# ---------------------------------------------------------------------------
# Phase 2 D-03: hardware capability probe -> ServerColorCaps
# ---------------------------------------------------------------------------


def _probe_for_platform() -> dict[str, Any]:
    """Dispatch to the platform-appropriate capability probe.

    Returns the probe's raw dict shape so callers (build_color_caps) can
    derive negotiated_state. The probe functions are themselves defensive
    about missing tools / PyObjC so this wrapper never raises.
    """
    if sys.platform == "darwin" or IS_MACOS:
        return probe_vt_main10()
    return probe_nvenc_main10()


def _derive_negotiated_state(probe: dict[str, Any]) -> str:
    """Convert probe result into the client-facing 4-state badge value.

    Truth table (D-03):
      NVENC trial main10 pass                   -> "confirmed"
      VT VTIsHardwareDecodeSupported + LL rc OK -> "confirmed"
      VT main10 decode OK but low_latency fails -> "degraded"
      NVENC present but main10 trial fails      -> "degraded"
      No NVENC, no VT (mss fallback path)       -> "not_supported"
    """
    probe_source = probe.get("probe_source", "")
    main10 = bool(probe.get("main10", False))

    if probe_source == "vt_query":
        if main10 and probe.get("low_latency_hevc", False):
            return "confirmed"
        if main10:
            return "degraded"
        return "not_supported"

    # NVENC / ffmpeg_trial path
    if main10:
        return "confirmed"
    if probe.get("gpu_name"):
        # GPU present but Main10 trial failed -> Pascal / pre-Turing.
        return "degraded"
    # No NVIDIA GPU detected and we're not on macOS -> mss software path.
    return "not_supported"


def build_color_caps() -> ServerColorCaps:
    """Run the D-03 capability probe and return a populated ServerColorCaps.

    Called from server/main.py just before the ServerHelloMsg is constructed.
    The result is advertised to the client, which renders the
    '10-bit: <state>' badge via client.health_display.render_color_badge.

    Per T-02-10: this runs POST-auth (main.py gates the send_hello callsite
    on PAM success); GPU model / family is never leaked to pre-auth sessions.
    """
    probe = _probe_for_platform()
    state = _derive_negotiated_state(probe)
    advertised = "p010le" if probe.get("main10") else "yuv420p"
    logger.info(
        "bootstrap.color_caps built state=%s main10=%s chroma_422=%s chroma_444=%s",
        state, probe.get("main10"),
        probe.get("chroma_422"), probe.get("chroma_444"),
    )
    return ServerColorCaps(
        main10=bool(probe.get("main10", False)),
        chroma_422=bool(probe.get("chroma_422", False)),
        chroma_444=bool(probe.get("chroma_444", False)),
        advertised_pix_fmt=advertised,
        negotiated_state=state,
    )
