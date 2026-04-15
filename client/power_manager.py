"""
Power management backend for bookmark-level machine control.

Supported backends
------------------
wol       — Wake-on-LAN magic packet (power ON only).
ssh       — SSH commands (poweroff/reboot). Works for Linux and Windows.
teraguchi — In-band power-off/reboot via the live teraguchi session.
            Falls back to SSH when the session is not connected.
none / "" — Power buttons hidden in the UI.

Fallback order for power-off / reboot
--------------------------------------
1. Backend is "teraguchi" + session is live → POWER_ACTION message over WebSocket.
2. Otherwise → SSH to power_ssh_host (or profile.host) with power_ssh_user/key.
3. If no SSH host available → return error string.
"""

import logging
import socket
import subprocess
from typing import Callable, Optional

logger = logging.getLogger(__name__)

POWER_OFF = "power_off"
REBOOT    = "reboot"
POWER_ON  = "power_on"


# ── WoL ──────────────────────────────────────────────────────────────

def _wol_magic_packet(mac: str) -> bytes:
    mac = mac.replace(":", "").replace("-", "").replace(".", "")
    if len(mac) != 12:
        raise ValueError(f"Invalid MAC address: {mac!r}")
    mac_bytes = bytes.fromhex(mac)
    return b"\xff" * 6 + mac_bytes * 16


def send_wol(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    packet = _wol_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(packet, (broadcast, port))
    logger.info("WoL packet sent → %s via %s:%d", mac, broadcast, port)


# ── SSH ──────────────────────────────────────────────────────────────

def _ssh_remote_cmd(action: str, os_type: str) -> list[str]:
    """Return the remote command tokens for the requested power action."""
    os_type = (os_type or "linux").lower()
    if os_type == "windows":
        if action == POWER_OFF:
            return ["powershell", "-Command", "Stop-Computer -Force"]
        if action == REBOOT:
            return ["powershell", "-Command", "Restart-Computer -Force"]
    else:
        if action == POWER_OFF:
            return ["sudo", "systemctl", "poweroff"]
        if action == REBOOT:
            return ["sudo", "systemctl", "reboot"]
    raise ValueError(f"Unknown action {action!r} for OS {os_type!r}")


def ssh_power(action: str, host: str, user: str = "", port: int = 22,
              key_path: str = "", os_type: str = "linux",
              timeout: int = 10) -> None:
    """Run a power command on a remote host via SSH.

    Always connects to port 22 (standard SSH), not the teraguchi streaming port.
    Authentication order: explicit key → SSH agent → system default.
    """
    remote_cmd = _ssh_remote_cmd(action, os_type)
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", f"ConnectTimeout={timeout}",
        "-o", "BatchMode=yes",
        "-p", str(port),
    ]
    if key_path:
        ssh_cmd += ["-i", key_path]
    target = f"{user}@{host}" if user else host
    full_cmd = ssh_cmd + [target] + remote_cmd

    logger.info("SSH power %s → %s:%d", action, target, port)
    result = subprocess.run(full_cmd, capture_output=True, timeout=timeout + 5)
    # rc 255 = SSH connection closed, expected when remote shuts down mid-command
    if result.returncode not in (0, 255):
        stderr = result.stderr.decode(errors="replace")[:300]
        raise RuntimeError(f"SSH rc={result.returncode}: {stderr}")


# ── PowerManager ─────────────────────────────────────────────────────

class PowerManager:
    """
    Executes power actions for a single bookmark profile.

    Parameters
    ----------
    profile : ConnectionProfile
        The bookmark profile that contains power_* fields.
    get_session_fn : callable, optional
        Zero-argument callable that returns the active Session for this
        bookmark, or None if not connected. Required for teraguchi in-band.
    """

    def __init__(self, profile, get_session_fn: Optional[Callable] = None):
        self._p = profile
        self._get_session = get_session_fn

    # ── capability queries ────────────────────────────────────────────

    @property
    def _backend(self) -> str:
        return (getattr(self._p, "power_backend", "") or "").lower()

    def has_power_on(self) -> bool:
        return bool(getattr(self._p, "power_wol_mac", ""))

    def has_power_off(self) -> bool:
        if self._backend in ("ssh", "teraguchi"):
            ssh_host = getattr(self._p, "power_ssh_host", "") or getattr(self._p, "host", "")
            session_ok = bool(self._get_session and self._get_session())
            return bool(ssh_host or session_ok)
        return False

    def has_reboot(self) -> bool:
        return self.has_power_off()

    # ── actions ──────────────────────────────────────────────────────

    def power_on(self) -> str:
        """Send a Wake-on-LAN magic packet. Returns a human-readable result."""
        mac = getattr(self._p, "power_wol_mac", "")
        if not mac:
            return "No WoL MAC address configured for this bookmark."
        bcast = getattr(self._p, "power_wol_broadcast", None) or "255.255.255.255"
        try:
            send_wol(mac, bcast)
            return f"Wake-on-LAN sent to {mac}."
        except Exception as exc:
            return f"WoL failed: {exc}"

    def power_off(self) -> str:
        return self._do_off_or_reboot(POWER_OFF)

    def reboot(self) -> str:
        return self._do_off_or_reboot(REBOOT)

    # ── internal ─────────────────────────────────────────────────────

    def _do_off_or_reboot(self, action: str) -> str:
        backend  = self._backend
        os_type  = getattr(self._p, "power_os", "linux") or "linux"
        action_label = "Power off" if action == POWER_OFF else "Reboot"

        # 1. In-band via live teraguchi session
        if backend == "teraguchi" and self._get_session:
            session = self._get_session()
            if session and getattr(session, "is_connected", False):
                try:
                    session.send_power_action(action)
                    return f"{action_label} command sent via active session."
                except Exception as exc:
                    logger.warning("In-band power failed (%s) — trying SSH", exc)

        # 2. SSH — host/user inherited from bookmark profile; only key is an override
        # power_ssh_host / power_ssh_user are model-level overrides for edge cases
        # (e.g. bastion host). In normal use they're empty and we fall back to the
        # bookmark's own host/username.
        ssh_host = getattr(self._p, "power_ssh_host", "") or getattr(self._p, "host", "")
        ssh_user = getattr(self._p, "power_ssh_user", "") or getattr(self._p, "username", "")
        ssh_key  = getattr(self._p, "power_ssh_key", "") or ""
        # Always use standard SSH port 22, not the teraguchi streaming port
        ssh_port = int(getattr(self._p, "power_ssh_port", 0) or 22)

        if not ssh_host:
            return f"{action_label} not available: no SSH host and no active session."

        try:
            ssh_power(action, ssh_host, ssh_user, ssh_port, ssh_key, os_type)
            return f"{action_label} command sent to {ssh_host}."
        except Exception as exc:
            return f"{action_label} via SSH failed: {exc}"
