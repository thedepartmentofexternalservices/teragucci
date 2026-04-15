"""
Remote desktop adapter registry.

Teraguchi is the master adapter (H.264/NVENC, uinput, PAM, ring buffer).
VNC, RDP, Local are candidate adapters — lower fidelity, useful when the
Teraguchi server is not installed on the target machine.

Usage:
    from ai.adapters import registry
    registry.use("vnc")
    adapter = registry.active
    adapter.connect("192.168.1.10", 5900, "", "password")
    png_b64 = adapter.screenshot()
"""

from .base import BaseAdapter, Capability
from .teraguchi import TeraGuchiAdapter
from .vnc import VNCAdapter
from .rdp import RDPAdapter
from .local import LocalAdapter
from .registry import AdapterRegistry

registry: AdapterRegistry = AdapterRegistry([
    TeraGuchiAdapter,
    VNCAdapter,
    RDPAdapter,
    LocalAdapter,
])

__all__ = [
    "BaseAdapter",
    "Capability",
    "TeraGuchiAdapter",
    "VNCAdapter",
    "RDPAdapter",
    "LocalAdapter",
    "AdapterRegistry",
    "registry",
]
