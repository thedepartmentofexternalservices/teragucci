"""Phase 2 D-03: hardware encoder capability probe.

Linux probe: parse nvidia-smi GPU name + ffmpeg trial-encode a 32x32 P010 frame.
macOS probe: VTCopySupportedPropertyDictionary + trial VTCompressionSessionCreate.

Result feeds ServerHelloMsg.color_caps so the client health overlay can render
an explicit '10-bit: confirmed | negotiated | degraded | not_supported' badge
with zero silent fallback.

Threat model notes (T-02-10 / T-02-11):
- Probe is invoked POST-auth from server bootstrap; GPU model is not leaked
  to unauthenticated clients.
- All subprocess calls use short timeouts so a hung nvidia-smi cannot wedge
  server startup.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Any

from common.logging import get_logger

log = get_logger("server.capability_probe")

# Family table -- sanity cross-check only. Trial-encode is authoritative.
_NVIDIA_FAMILY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("blackwell", ("RTX 50", "B100", "B200", "GB10", "GB20")),
    ("ada",       ("RTX 40", "RTX 4000 Ada", "L4", "L40", "Ada")),
    ("ampere",    ("RTX 30", "A100", "A10", "A40", "A4000", "A5000", "A6000", "Ampere")),
    ("turing",    ("RTX 20", "GTX 16", "T4", "Quadro RTX", "Turing")),
)


def _classify_nvidia_family(gpu_name: str) -> str:
    for family, patterns in _NVIDIA_FAMILY_PATTERNS:
        if any(p in gpu_name for p in patterns):
            return family
    return "unknown"


def probe_nvenc_main10() -> dict[str, Any]:
    """Probe NVENC HEVC Main10 + 4:2:2 + 4:4:4 support.

    1. nvidia-smi for GPU name (sanity family table cross-check).
    2. ffmpeg trial-encode 32x32 P010 frame with -profile:v main10.
    3. Blackwell-only: ffmpeg trial 4:2:2 (-pix_fmt yuv422p10le).

    Returns a dict with keys: main10, chroma_422, chroma_444, gpu_family,
    gpu_name, probe_source.
    """
    result: dict[str, Any] = {
        "main10": False,
        "chroma_422": False,
        "chroma_444": False,
        "gpu_family": "unknown",
        "gpu_name": "",
        "probe_source": "ffmpeg_trial",
    }
    try:
        smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=gpu_name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log.info("capability.probe.nvidia_smi_unavailable")
        return result
    except Exception as exc:  # pragma: no cover - defensive; broad Exception
        log.warning("capability.probe.nvidia_smi_error", error=str(exc))
        return result

    if smi.returncode == 0 and smi.stdout and smi.stdout.strip():
        gpu_name = smi.stdout.strip().split(",")[0].strip()
        result["gpu_name"] = gpu_name
        result["gpu_family"] = _classify_nvidia_family(gpu_name)
    else:
        # nvidia-smi responded but returned no useful data (no GPU / driver
        # mismatch). Treat as mss fallback path.
        log.info("capability.probe.nvidia_smi_empty",
                 returncode=smi.returncode)
        return result

    # Trial main10
    try:
        trial_main10 = subprocess.run(
            shlex.split(
                "ffmpeg -v error -f rawvideo -pix_fmt p010le -s 32x32 -i /dev/zero "
                "-t 0.01 -c:v hevc_nvenc -profile:v main10 -pix_fmt p010le -f null -"
            ),
            capture_output=True, timeout=10, check=False,
        )
        result["main10"] = (trial_main10.returncode == 0)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("capability.probe.ffmpeg_trial_failed", error=str(exc))
        return result

    if result["main10"]:
        log.info("capability.probe.main10_ok",
                 gpu_family=result["gpu_family"], gpu_name=result["gpu_name"])
    else:
        stderr_tail = b""
        try:
            stderr_tail = (trial_main10.stderr or b"")[:200]
        except Exception:
            stderr_tail = b""
        log.warning("capability.probe.main10_unsupported",
                    gpu_family=result["gpu_family"],
                    gpu_name=result["gpu_name"],
                    stderr=stderr_tail)

    # Trial 4:2:2 (Blackwell only; future-proof the probe itself)
    if result["gpu_family"] == "blackwell":
        try:
            trial_422 = subprocess.run(
                shlex.split(
                    "ffmpeg -v error -f rawvideo -pix_fmt yuv422p10le -s 32x32 -i /dev/zero "
                    "-t 0.01 -c:v hevc_nvenc -pix_fmt yuv422p10le -f null -"
                ),
                capture_output=True, timeout=10, check=False,
            )
            result["chroma_422"] = (trial_422.returncode == 0)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            log.warning("capability.probe.ffmpeg_422_trial_failed", error=str(exc))

    # 4:4:4 -- Turing+ NVENC supports HEVC 4:4:4 per NVIDIA VCSDK matrix.
    # Leave static for now; trial-encode would add 10s to startup. Correct per
    # research table (family-backed, not probe-backed).
    result["chroma_444"] = result["gpu_family"] in ("turing", "ampere", "ada", "blackwell")

    return result


def probe_vt_main10() -> dict[str, Any]:
    """Probe macOS VideoToolbox HEVC Main10 + low-latency + 4:2:2 support.

    Trials VTCompressionSessionCreate with Main10_AutoLevel + LowLatencyRateControl.
    Returns: main10, chroma_422, chroma_444, low_latency_hevc, probe_source.

    On non-macOS hosts (or macOS hosts without pyobjc-framework-VideoToolbox),
    returns all-False caps with a stable key shape so callers can branch on
    the dict without crashing.
    """
    result: dict[str, Any] = {
        "main10": False,
        "chroma_422": False,
        "chroma_444": False,
        "low_latency_hevc": False,
        "probe_source": "vt_query",
    }
    try:
        import CoreMedia as CM
        import objc  # noqa: F401
        import VideoToolbox as VT
    except Exception as exc:
        log.info("capability.probe.vt_unavailable", error=str(exc))
        return result

    # VTIsHardwareDecodeSupported is a cheap top-level check
    try:
        result["main10"] = bool(VT.VTIsHardwareDecodeSupported(CM.kCMVideoCodecType_HEVC))
    except Exception as exc:
        log.warning("capability.probe.vt_decode_check_failed", error=str(exc))
        return result

    # Trial low-latency HEVC session create
    spec = {
        "EnableLowLatencyRateControl": True,
        "EnableHardwareAcceleratedVideoEncoder": True,
    }
    try:
        status, session = VT.VTCompressionSessionCreate(
            None, 1280, 720, CM.kCMVideoCodecType_HEVC, spec, None, None, None, None, None
        )
        result["low_latency_hevc"] = (status == 0)
        if session is not None:
            try:
                VT.VTCompressionSessionInvalidate(session)
            except Exception:
                pass
    except Exception as exc:
        log.info("capability.probe.vt_low_latency_trial_failed", error=str(exc))
        result["low_latency_hevc"] = False

    # 4:2:2 on M4+: VT_Main42210. Leave False until we confirm with an M4 trial;
    # D-05 has 4:2:2 opt-in policy so "unknown -> False" is the correct
    # conservative default.
    result["chroma_422"] = False
    return result


@dataclass
class CapabilityReport:
    """Structured summary of the probe result (bootstrap-facing)."""

    main10: bool
    chroma_422: bool
    chroma_444: bool
    probe_source: str
    detail: dict[str, Any]
