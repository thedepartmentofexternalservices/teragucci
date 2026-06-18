"""
X Session Manager for Teraguchi server.

Manages per-user X11 sessions, replacing HP Anyware / PCoIP:
- Each authenticated user gets a GPU-accelerated X display (real Xorg with NVIDIA)
- Sessions persist across disconnections for seamless reconnection
- Window manager / desktop launched per user
- Supports multiple concurrent user sessions

The server runs as root and spawns Xorg with NVIDIA's headless display
(ConnectedMonitor + CustomEDID), providing full GPU acceleration for
applications like Autodesk Flame that require NVIDIA GLX/MetaModes.

Falls back to Xvfb on machines without NVIDIA GPU.
"""

import logging
import os
import pwd
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from server.input_injector import VirtualPenTablet

logger = logging.getLogger(__name__)


def _find_input_event_device(name_match: str) -> Optional[str]:
    """Find /dev/input/eventN for a uinput device by its registered name.

    Parses /proc/bus/input/devices which lists every evdev node and its
    name. Needed because uinput assigns event numbers dynamically.
    """
    try:
        with open("/proc/bus/input/devices") as f:
            data = f.read()
    except OSError:
        return None

    current_name = None
    for block in data.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("N: Name="):
                current_name = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("H: Handlers=") and current_name == name_match:
                for handler in line.split("=", 1)[1].split():
                    if handler.startswith("event"):
                        return f"/dev/input/{handler}"
        current_name = None
    return None

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1200
DEFAULT_DEPTH = 24
DEFAULT_DPI = 96

MIN_DISPLAY = 10
MAX_DISPLAY = 99

# Path to our Xorg config for headless GPU display
XORG_CONFIG = str(Path(__file__).parent / "xorg-teraguchi.conf")


def find_free_display() -> int:
    """Find an unused X display number."""
    for n in range(MIN_DISPLAY, MAX_DISPLAY):
        lock_file = f"/tmp/.X{n}-lock"
        socket_path = f"/tmp/.X11-unix/X{n}"
        if not os.path.exists(lock_file) and not os.path.exists(socket_path):
            return n
    raise RuntimeError("No free X display numbers available (checked :{}-:{})".format(
        MIN_DISPLAY, MAX_DISPLAY - 1))


def detect_window_manager() -> List[str]:
    """Detect available window managers, return launch command for best option."""
    candidates = [
        (["gnome-shell", "--x11", "--sm-disable"], "GNOME Classic (gnome-shell --x11)"),
        (["xfce4-session"], "XFCE"),
        (["mate-session"], "MATE"),
        (["openbox-session"], "Openbox"),
        (["fluxbox"], "Fluxbox"),
        (["icewm-session"], "IceWM"),
        (["twm"], "TWM"),
        (["xterm"], "xterm (fallback)"),
    ]
    for cmd, name in candidates:
        try:
            result = subprocess.run(
                ["which", cmd[0]], capture_output=True, timeout=5)
            if result.returncode == 0:
                logger.info("Window manager: %s", name)
                return cmd
        except Exception:
            pass

    logger.warning("No window manager found — sessions will have a bare X display")
    return []


def has_nvidia_gpu() -> bool:
    """Check if NVIDIA GPU and driver are available."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            logger.info("NVIDIA GPU detected: %s", result.stdout.strip().split('\n')[0])
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


def find_edid_file() -> str:
    """Find or generate an EDID file for the fake display."""
    # PCoIP ships EDID files we can use
    pcoip_edid = "/usr/share/pcoip-agent/1024x768.bin"
    if os.path.exists(pcoip_edid):
        logger.info("Using PCoIP EDID: %s", pcoip_edid)
        return pcoip_edid

    # Check for our own EDID
    our_edid = str(Path(__file__).parent / "edid" / "1920x1200.bin")
    if os.path.exists(our_edid):
        return our_edid

    # Generate a minimal EDID if none found
    edid_dir = str(Path(__file__).parent / "edid")
    os.makedirs(edid_dir, exist_ok=True)
    edid_path = os.path.join(edid_dir, "default.bin")
    if not os.path.exists(edid_path):
        _generate_edid(edid_path, 1920, 1200)
    return edid_path


def _generate_edid(path: str, width: int, height: int):
    """Generate a minimal EDID 1.3 binary for a given resolution.

    Phase 3 DISP-04 — the generator now emits an "Eizo CG279X" monitor
    profile (11 ASCII chars, fits the 13-byte EDID descriptor #2) +
    matching Eizo manufacturer ID "ENC". The Eizo CG279X is an
    industry-standard 27" 10-bit grading monitor; Flame's monitor-
    config dialog accepts the descriptor without raising the
    "unrecognized monitor" warning that the previous "TGC"/"Teraguchi"
    combination triggered. The resolution is not load-bearing for Flame
    acceptance — any sensible EDID 1.3 block with a named monitor
    descriptor passes the dialog.

    If a studio wants a different profile, drop a custom .bin in
    server/edid/ and find_edid_file picks it up first. The generator
    stays as a fallback path for users without a shipped EDID binary.
    """
    # Standard EDID 1.3 block (128 bytes)
    # This creates a basic monitor profile that NVIDIA's driver will accept
    edid = bytearray(128)

    # Header
    edid[0:8] = b'\x00\xff\xff\xff\xff\xff\xff\x00'

    # Phase 3 DISP-04 — Eizo manufacturer ID (PnP code "ENC") to match
    # the monitor name descriptor below. Flame parses both fields
    # independently; matching them avoids mismatched-vendor warnings.
    # E=5, N=14, C=3 -> ((5-1)<<10) | ((14-1)<<5) | (3-1) = 0x11A2
    edid[8] = 0x11
    edid[9] = 0xA2

    # Product code
    edid[10] = 0x01
    edid[11] = 0x00

    # Serial number
    edid[12:16] = b'\x01\x00\x00\x00'

    # Week 1, Year 2025 (offset from 1990 = 35)
    edid[16] = 1
    edid[17] = 35

    # EDID version 1.3
    edid[18] = 1
    edid[19] = 3

    # Digital input, 8-bit color
    edid[20] = 0x80

    # Max image size (cm): ~52cm x 32cm for a 24" display
    edid[21] = 52  # horizontal
    edid[22] = 32  # vertical

    # Gamma 2.2 (value = (gamma * 100) - 100 = 120)
    edid[23] = 120

    # Feature support: RGB color, preferred timing in DTD1
    edid[24] = 0x0A

    # Chromaticity (standard sRGB values)
    edid[25:35] = bytes([0xEE, 0x95, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54])

    # Established timings (640x480, 800x600, 1024x768)
    edid[35] = 0x21
    edid[36] = 0x08
    edid[37] = 0x00

    # Standard timings (unused, fill with 0x0101)
    for i in range(38, 54, 2):
        edid[i] = 0x01
        edid[i + 1] = 0x01

    # Detailed Timing Descriptor #1 - preferred mode
    # 1920x1200 @ 60Hz, pixel clock 154.0 MHz
    dtd_offset = 54
    pixel_clock = 15400  # in 10kHz units
    edid[dtd_offset] = pixel_clock & 0xFF
    edid[dtd_offset + 1] = (pixel_clock >> 8) & 0xFF

    # Horizontal: 1920 active, 160 blanking
    h_active = width
    h_blank = 160
    edid[dtd_offset + 2] = h_active & 0xFF
    edid[dtd_offset + 3] = h_blank & 0xFF
    edid[dtd_offset + 4] = ((h_active >> 8) << 4) | (h_blank >> 8)

    # Vertical: 1200 active, 35 blanking
    v_active = height
    v_blank = 35
    edid[dtd_offset + 5] = v_active & 0xFF
    edid[dtd_offset + 6] = v_blank & 0xFF
    edid[dtd_offset + 7] = ((v_active >> 8) << 4) | (v_blank >> 8)

    # Sync offsets/widths
    edid[dtd_offset + 8] = 48   # h_sync_offset
    edid[dtd_offset + 9] = 32   # h_sync_width
    edid[dtd_offset + 10] = 0x36  # v_sync_offset=3, v_sync_width=6
    edid[dtd_offset + 11] = 0x00

    # Image size (mm): 518mm x 324mm
    edid[dtd_offset + 12] = 0x06  # h_size low
    edid[dtd_offset + 13] = 0x44  # v_size low
    edid[dtd_offset + 14] = 0x21  # h_size high | v_size high

    edid[dtd_offset + 15] = 0  # h_border
    edid[dtd_offset + 16] = 0  # v_border
    edid[dtd_offset + 17] = 0x1E  # flags: digital separate sync, +hsync +vsync

    # Descriptor #2: Monitor name
    # Phase 3 DISP-04 — "Eizo CG279X" is industry-standard for VFX
    # grading/finishing; Flame's monitor-config dialog accepts the
    # descriptor without complaint. 11 ASCII chars, fits the 13-byte
    # EDID descriptor.
    name_offset = 72
    edid[name_offset:name_offset + 5] = b'\x00\x00\x00\xFC\x00'
    name = "Eizo CG279X"
    name_bytes = (name.encode('ascii')[:12] + b'\x0a').ljust(13, b'\x20')
    edid[name_offset + 5:name_offset + 18] = name_bytes

    # Descriptor #3: Monitor range limits
    range_offset = 90
    edid[range_offset:range_offset + 5] = b'\x00\x00\x00\xFD\x00'
    edid[range_offset + 5] = 24   # min v_freq
    edid[range_offset + 6] = 120  # max v_freq
    edid[range_offset + 7] = 28   # min h_freq (kHz)
    edid[range_offset + 8] = 160  # max h_freq (kHz)
    edid[range_offset + 9] = 22   # max pixel clock / 10 (220 MHz)
    edid[range_offset + 10] = 0x00  # no GTF

    # Descriptor #4: Dummy (unused)
    edid[108:126] = b'\x00\x00\x00\x10\x00' + b'\x00' * 13

    # Extension flag (0 = no extensions)
    edid[126] = 0

    # Checksum: sum of all 128 bytes must be 0 mod 256
    edid[127] = (256 - (sum(edid[:127]) % 256)) % 256

    with open(path, 'wb') as f:
        f.write(bytes(edid))
    logger.info("Generated EDID file: %s (%dx%d)", path, width, height)


@dataclass
class UserSession:
    """Tracks a running user X session."""
    username: str
    uid: int
    gid: int
    home: str
    display: str          # e.g., ":10"
    display_num: int
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    xorg_proc: Optional[subprocess.Popen] = None  # Real Xorg or Xvfb
    gpu_display: bool = False  # True if using real GPU Xorg
    wm_proc: Optional[subprocess.Popen] = None
    compositor_proc: Optional[subprocess.Popen] = None
    dbus_proc: Optional[subprocess.Popen] = None
    dbus_pid: Optional[int] = None  # PID from dbus-launch (for cleanup)
    dbus_address: str = ""  # D-Bus session bus address from dbus-launch
    # Long-lived helper process that holds the logind fifo_fd open so that
    # the logind session created for gnome-shell stays alive. See
    # logind_session_helper.py for the gory details.
    logind_proc: Optional[subprocess.Popen] = None
    logind_session_id: str = ""
    pulseaudio_proc: Optional[subprocess.Popen] = None
    connected_clients: int = 0
    created_at: float = field(default_factory=time.time)
    xauthority: str = ""
    # uinput pen tablet created before Xorg so that Xorg picks it up via an
    # explicit InputDevice section. Kept here to control its lifetime (closing
    # the fd destroys the device) and to hand it to the input injector.
    pen_tablet: Optional["VirtualPenTablet"] = None
    pen_tablet_event: str = ""  # /dev/input/eventN path

    @property
    def alive(self) -> bool:
        return self.xorg_proc is not None and self.xorg_proc.poll() is None

    @property
    def env(self) -> dict:
        """Environment for processes in this session."""
        e = {
            "DISPLAY": self.display,
            "HOME": self.home,
            "USER": self.username,
            "LOGNAME": self.username,
            "SHELL": pwd.getpwuid(self.uid).pw_shell,
            "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin",
            "XDG_RUNTIME_DIR": f"/run/user/{self.uid}",
            "DBUS_SESSION_BUS_ADDRESS": self.dbus_address,
            "XDG_SESSION_TYPE": "x11",
        }
        if self.xauthority:
            e["XAUTHORITY"] = self.xauthority
        return e


class SessionManager:
    """
    Manages per-user virtual X sessions.

    Like PCoIP / HP Anyware:
    - User authenticates → get or create their X session
    - Session persists when client disconnects
    - User reconnects → same session, same state
    - Each session is an isolated Xvfb display with a window manager
    """

    def __init__(self, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT,
                 depth: int = DEFAULT_DEPTH, dpi: int = DEFAULT_DPI):
        self.width = width
        self.height = height
        self.depth = depth
        self.dpi = dpi
        self._sessions: Dict[str, UserSession] = {}
        self._wm_cmd = detect_window_manager()
        self._has_nvidia = has_nvidia_gpu()
        self._edid_file = find_edid_file() if self._has_nvidia else ""
        if self._has_nvidia:
            logger.info("GPU mode: will launch real Xorg with NVIDIA driver")
        else:
            logger.info("Software mode: will use Xvfb (no NVIDIA GPU detected)")

    def get_session(self, username: str) -> Optional[UserSession]:
        """Get an existing live session, or None."""
        session = self._sessions.get(username)
        if session and session.alive:
            # Check if WM died and restart it
            if session.wm_proc and session.wm_proc.poll() is not None:
                logger.warning("WM died for %s (exit %s), restarting",
                               username, session.wm_proc.returncode)
                self._start_window_manager(session)
            return session
        if session:
            logger.warning("Session for %s died, cleaning up", username)
            self._cleanup_session(session)
            del self._sessions[username]
        return None

    def _disable_x_key_repeat(self, display: str, xauthority: str = "") -> None:
        """Phase 2 D-13 — turn off X server's auto key repeat.

        Teraguchi uses client-driven repeats: the client sends explicit
        N press events when the user holds a key, and a network stall
        causes the repeat sequence to STOP mid-air rather than runaway
        on the server side. Matches PCoIP behavior. Failure is logged
        and tolerated — best-effort only.
        """
        env = {"DISPLAY": display, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        if xauthority:
            env["XAUTHORITY"] = xauthority
        try:
            subprocess.run(
                ["xset", "-display", display, "r", "off"],
                check=False, timeout=2, env=env,
                capture_output=True,
            )
            logger.info(
                "session_manager.xset_repeat_off display=%s", display
            )
        except FileNotFoundError:
            logger.warning(
                "session_manager.xset_not_installed — install xorg-x11-server-utils"
            )
        except Exception as e:
            logger.warning(
                "session_manager.xset_repeat_off_failed display=%s err=%s",
                display, e,
            )

    def create_session(self, username: str, uid: int, gid: int, home: str,
                       width: int = 0, height: int = 0) -> UserSession:
        """Create a new X session for a user, or return existing one."""
        existing = self.get_session(username)
        if existing:
            logger.info("Reusing session for %s on %s", username, existing.display)
            return existing

        w = width or self.width
        h = height or self.height
        display_num = find_free_display()
        display = f":{display_num}"

        logger.info("Creating session for %s on %s (%dx%d)", username, display, w, h)

        # Ensure XDG_RUNTIME_DIR exists
        runtime_dir = f"/run/user/{uid}"
        os.makedirs(runtime_dir, mode=0o700, exist_ok=True)
        os.chown(runtime_dir, uid, gid)

        # Xauthority
        xauth_dir = "/run/teraguchi"
        os.makedirs(xauth_dir, mode=0o755, exist_ok=True)
        xauthority = f"{xauth_dir}/{username}.xauth"

        try:
            # Remove stale xauth
            if os.path.exists(xauthority):
                os.unlink(xauthority)
            subprocess.run(
                ["xauth", "-f", xauthority, "generate", display, ".", "trusted"],
                capture_output=True, timeout=10,
                env={"XAUTHORITY": xauthority, "HOME": home})
            os.chown(xauthority, uid, gid)
        except Exception as e:
            logger.warning("xauth failed: %s (using -ac instead)", e)
            xauthority = ""

        # Launch X server — real Xorg with NVIDIA GPU, or Xvfb fallback
        gpu_display = False
        pen_tablet = None
        pen_tablet_event = ""
        if self._has_nvidia:
            xorg_proc, gpu_display, pen_tablet, pen_tablet_event = self._start_xorg_gpu(
                display, display_num, w, h, xauthority)
        else:
            xorg_proc = None

        if not xorg_proc:
            # Fallback to Xvfb (no GPU, or Xorg failed)
            xorg_proc = self._start_xvfb(display, display_num, w, h, xauthority)
            gpu_display = False

        session = UserSession(
            username=username, uid=uid, gid=gid, home=home,
            display=display, display_num=display_num,
            width=w, height=h,
            xorg_proc=xorg_proc, gpu_display=gpu_display,
            xauthority=xauthority,
            pen_tablet=pen_tablet, pen_tablet_event=pen_tablet_event)

        # Phase 2 D-13 — turn off X auto-key-repeat right after the X
        # server is up. Teraguchi drives repeats from the client; leaving
        # X server-side repeat on causes runaway when a packet stalls.
        # Run BEFORE the WM / D-Bus boot so the very first key event the
        # WM sees already lives in the no-repeat regime.
        self._disable_x_key_repeat(display, xauthority)

        # Start D-Bus session for the user
        self._start_dbus(session)

        # Start PulseAudio for the user (audio capture needs it)
        self._start_pulseaudio(session)

        # Start window manager (gnome-shell needs D-Bus ready)
        if self._wm_cmd:
            if self._wm_cmd[0] == "gnome-shell":
                # Give D-Bus and X server time to fully initialize
                time.sleep(1)
            self._start_window_manager(session)

        # Start X compositor so screen capture reads coherent framebuffers
        # (fixes tearing during video playback). gnome-shell is already a
        # compositor, so skip it in that case.
        if not (self._wm_cmd and self._wm_cmd[0] == "gnome-shell"):
            self._start_compositor(session)

        self._sessions[username] = session
        return session

    def _start_xorg_gpu(self, display: str, display_num: int,
                        width: int, height: int,
                        xauthority: str) -> tuple:
        """Start a real Xorg server with NVIDIA GPU acceleration.

        Returns (Popen, True, pen_tablet, pen_event_path) on success,
        (None, False, None, "") on failure.
        """
        xorg_bin = "/usr/libexec/Xorg"
        if not os.path.exists(xorg_bin):
            xorg_bin = shutil.which("Xorg") or shutil.which("X")
        if not xorg_bin:
            logger.warning("Xorg binary not found, falling back to Xvfb")
            return None, False, None, ""

        # Create a uinput virtual pen tablet BEFORE Xorg starts so that we
        # can wire its event device into the xorg.conf as an explicit
        # InputDevice. With AutoAddDevices=false Xorg will not pick up
        # anything we create later.
        pen_tablet = None
        pen_event_path = ""
        try:
            from server.input_injector import VirtualPenTablet
            pen_tablet = VirtualPenTablet(screen_width=width, screen_height=height)
            # Uinput name is fixed in VirtualPenTablet
            pen_event_path = _find_input_event_device("Teraguchi Virtual Pen Tablet") or ""
            if not pen_event_path:
                logger.warning("Pen tablet created but event device not found; pressure disabled")
            else:
                logger.info("Pen tablet event device: %s", pen_event_path)
        except PermissionError:
            logger.warning("No access to /dev/uinput — pen pressure disabled")
            pen_tablet = None
        except Exception as e:
            logger.warning("Failed to create virtual pen tablet: %s", e)
            pen_tablet = None

        # Write a per-display xorg config with the EDID path filled in
        config_path = f"/tmp/teraguchi-xorg-{display_num}.conf"
        self._write_xorg_config(config_path, width, height, pen_event_path=pen_event_path)

        log_file = f"/var/log/teraguchi-Xorg-{display_num}.log"

        xorg_cmd = [
            xorg_bin, display,
            "-config", config_path,
            "-novtswitch",
            "-noreset",
            "-nolisten", "tcp",
            "-logfile", log_file,
        ]
        if xauthority:
            xorg_cmd.extend(["-auth", xauthority])

        logger.info("Starting Xorg GPU display %s: %s", display, " ".join(xorg_cmd))

        try:
            xorg_proc = subprocess.Popen(
                xorg_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE)

            # Wait for X socket to appear (Xorg takes longer than Xvfb)
            for i in range(100):  # 10 seconds max
                if os.path.exists(f"/tmp/.X11-unix/X{display_num}"):
                    break
                # Check if process died
                if xorg_proc.poll() is not None:
                    stderr = xorg_proc.stderr.read().decode(errors='replace') if xorg_proc.stderr else ""
                    logger.error("Xorg died on startup (exit %d): %s",
                                 xorg_proc.returncode, stderr[:500])
                    # Check the log file for more details
                    try:
                        with open(log_file) as f:
                            log_tail = f.read()[-2000:]
                        logger.error("Xorg log tail:\n%s", log_tail)
                    except Exception:
                        pass
                    if pen_tablet:
                        pen_tablet.close()
                    return None, False, None, ""
                time.sleep(0.1)
            else:
                xorg_proc.kill()
                logger.error("Xorg timed out waiting for socket on %s", display)
                if pen_tablet:
                    pen_tablet.close()
                return None, False, None, ""

            logger.info("Xorg GPU started on %s (pid %d)", display, xorg_proc.pid)

            # Set initial resolution via xrandr
            time.sleep(0.5)  # Give Xorg a moment to fully initialize
            self._set_gpu_resolution(display, width, height, xauthority)

            return xorg_proc, True, pen_tablet, pen_event_path

        except Exception as e:
            logger.error("Failed to start Xorg GPU: %s", e)
            if pen_tablet:
                pen_tablet.close()
            return None, False, None, ""

    def _write_xorg_config(self, config_path: str, width: int, height: int,
                           pen_event_path: str = ""):
        """Write a per-display xorg.conf with EDID and resolution settings.

        If `pen_event_path` is provided, an explicit InputDevice section is
        added so that Xorg picks up our uinput virtual Wacom tablet despite
        AutoAddDevices being disabled.
        """
        edid = self._edid_file

        pen_input_section = ""
        pen_layout_line = ""
        if pen_event_path:
            # Use the xf86-input-wacom driver (not libinput) because Flame
            # and most pro creative apps specifically look for the Wacom
            # XInput driver to enable pressure curves, pen prefs, etc. We
            # create three logical sub-devices (stylus, eraser, cursor)
            # from the same uinput node — same pattern as real Wacom
            # tablets plugged into xorg.
            pen_input_section = f'''
Section "InputDevice"
    Identifier     "TeraguchiPen stylus"
    Driver         "wacom"
    Option         "Device" "{pen_event_path}"
    Option         "Type" "stylus"
    Option         "USB" "on"
EndSection

Section "InputDevice"
    Identifier     "TeraguchiPen eraser"
    Driver         "wacom"
    Option         "Device" "{pen_event_path}"
    Option         "Type" "eraser"
    Option         "USB" "on"
EndSection

Section "InputDevice"
    Identifier     "TeraguchiPen cursor"
    Driver         "wacom"
    Option         "Device" "{pen_event_path}"
    Option         "Type" "cursor"
    Option         "USB" "on"
EndSection
'''
            pen_layout_line = (
                '    InputDevice "TeraguchiPen stylus" "SendCoreEvents"\n'
                '    InputDevice "TeraguchiPen eraser" "SendCoreEvents"\n'
                '    InputDevice "TeraguchiPen cursor" "SendCoreEvents"\n'
            )

        config = f'''# Auto-generated by Teraguchi session manager
# GPU-accelerated headless display with NVIDIA driver

Section "ServerLayout"
    Identifier     "Teraguchi"
    Screen      0  "Screen0"
{pen_layout_line}    Option         "AllowEmptyInitialConfiguration" "true"
EndSection

Section "ServerFlags"
    Option         "DefaultServerLayout" "Teraguchi"
    Option         "AllowMouseOpenFail" "true"
    Option         "AutoAddDevices" "false"
    Option         "AutoEnableDevices" "false"
    Option         "DontVTSwitch" "true"
EndSection

Section "Device"
    Identifier     "Device0"
    Driver         "nvidia"
    Option         "ConnectedMonitor" "DFP-0"
    Option         "CustomEDID" "DFP-0:{edid}"
    Option         "AllowEmptyInitialConfiguration" "true"
    Option         "HardDPMS" "false"
    Option         "Interactive" "false"
    Option         "ModeValidation" "AllowNonEdidModes, NoEdidMaxPClkCheck, NoHorizSyncCheck, NoVertRefreshCheck, NoMaxSizeCheck"
    # ForceFullCompositionPipeline routes all rendering through the NVIDIA
    # driver's internal composition engine, giving us a coherent framebuffer
    # for mss/XGetImage capture and eliminating tear bands during video
    # playback. Cheap on headless/virtual displays.
    Option         "MetaModes" "DFP-0: {width}x{height} +0+0 {{ ForceCompositionPipeline = On, ForceFullCompositionPipeline = On }}"
EndSection

Section "Monitor"
    Identifier     "Monitor0"
    HorizSync      28.0-160.0
    VertRefresh    24.0-120.0
EndSection

Section "Screen"
    Identifier     "Screen0"
    Device         "Device0"
    Monitor        "Monitor0"
    DefaultDepth   24
    Option         "AllowEmptyInitialConfiguration" "true"
    SubSection     "Display"
        Virtual    {max(width, 3840)} {max(height, 2160)}
        Depth      24
    EndSubSection
EndSection

Section "Extensions"
    Option         "GLX" "Enable"
    Option         "RANDR" "Enable"
EndSection
{pen_input_section}'''
        with open(config_path, 'w') as f:
            f.write(config)
        logger.debug("Wrote Xorg config: %s", config_path)

    def _set_gpu_resolution(self, display: str, width: int, height: int,
                            xauthority: str):
        """Set the display resolution via xrandr on GPU display."""
        env = {"DISPLAY": display}
        if xauthority:
            env["XAUTHORITY"] = xauthority

        try:
            # Query available outputs
            result = subprocess.run(
                ["xrandr", "--query"],
                capture_output=True, text=True, timeout=5, env=env)
            logger.debug("xrandr output:\n%s", result.stdout[:1000])

            # Find the connected output name
            output_name = None
            for line in result.stdout.splitlines():
                if " connected" in line:
                    output_name = line.split()[0]
                    break

            if not output_name:
                logger.warning("No connected output found in xrandr")
                return

            mode = f"{width}x{height}"
            result = subprocess.run(
                ["xrandr", "--output", output_name, "--mode", mode],
                capture_output=True, text=True, timeout=5, env=env)
            if result.returncode == 0:
                logger.info("Set GPU display to %s on %s", mode, output_name)
            else:
                # Mode might not exist, try adding it
                logger.info("Mode %s not available, trying to add it", mode)
                cvt = subprocess.run(
                    ["cvt", str(width), str(height), "60"],
                    capture_output=True, text=True, timeout=5)
                for line in cvt.stdout.splitlines():
                    if line.startswith("Modeline"):
                        parts = line.split(None, 2)
                        mode_label = parts[1].strip('"')
                        mode_params = parts[2]
                        subprocess.run(
                            ["xrandr", "--newmode", mode_label] + mode_params.split(),
                            capture_output=True, timeout=5, env=env)
                        subprocess.run(
                            ["xrandr", "--addmode", output_name, mode_label],
                            capture_output=True, timeout=5, env=env)
                        subprocess.run(
                            ["xrandr", "--output", output_name, "--mode", mode_label],
                            capture_output=True, timeout=5, env=env)
                        logger.info("Added and set mode %s", mode_label)
                        break
        except Exception as e:
            logger.warning("Failed to set GPU resolution: %s", e)

    def _start_xvfb(self, display: str, display_num: int,
                    width: int, height: int, xauthority: str) -> subprocess.Popen:
        """Start Xvfb as fallback (no GPU acceleration)."""
        xvfb_cmd = [
            "Xvfb", display,
            "-screen", "0", f"{width}x{height}x{self.depth}",
            "-dpi", str(self.dpi),
            "-ac",
            "+extension", "RANDR",
            "+extension", "GLX",
            "-nolisten", "tcp",
        ]
        if xauthority:
            xvfb_cmd.extend(["-auth", xauthority])

        logger.info("Starting Xvfb on %s (software rendering)", display)
        xvfb_proc = subprocess.Popen(
            xvfb_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE)

        # Wait for socket
        for _ in range(50):
            if os.path.exists(f"/tmp/.X11-unix/X{display_num}"):
                break
            time.sleep(0.1)
        else:
            xvfb_proc.kill()
            raise RuntimeError(f"Xvfb failed to start on {display}")

        logger.info("Xvfb started on %s (pid %d)", display, xvfb_proc.pid)
        return xvfb_proc

    def _demote(self, uid: int, gid: int):
        """preexec_fn to drop privileges to a user."""
        os.setgid(gid)
        os.initgroups(pwd.getpwuid(uid).pw_name, gid)
        os.setuid(uid)

    def _start_dbus(self, session: UserSession):
        """Set up D-Bus for the session.

        Use the user's existing dbus-broker session bus (at
        /run/user/<uid>/bus) and update its environment so D-Bus
        activated services (gnome-terminal, etc.) get DISPLAY set.

        Fall back to dbus-launch if no existing bus is found.
        """
        # Check for existing user session bus (dbus-broker)
        user_bus = f"/run/user/{session.uid}/bus"
        if os.path.exists(user_bus):
            session.dbus_address = f"unix:path={user_bus}"
            logger.info("D-Bus using existing user bus for %s: %s",
                        session.username, session.dbus_address)
            # Update the bus environment so D-Bus activated services
            # (gnome-terminal-server, etc.) inherit our DISPLAY
            self._update_dbus_environment(session)
            return

        # No existing bus — start our own via dbus-launch
        try:
            result = subprocess.run(
                ["dbus-launch", "--sh-syntax"],
                capture_output=True, text=True, timeout=5,
                preexec_fn=lambda: self._demote(session.uid, session.gid),
                env=session.env)
            bus_addr = ""
            bus_pid = None
            for line in result.stdout.splitlines():
                if line.startswith("DBUS_SESSION_BUS_ADDRESS="):
                    bus_addr = line.split("=", 1)[1].strip(" ;'\"")
                elif line.startswith("DBUS_SESSION_BUS_PID="):
                    try:
                        bus_pid = int(line.split("=", 1)[1].strip(" ;'\""))
                    except ValueError:
                        pass
            if bus_addr:
                session.dbus_address = bus_addr
                session.dbus_pid = bus_pid
                logger.info("D-Bus started for %s: %s (pid %s)",
                            session.username, bus_addr, bus_pid)
                # Also update the bus environment for activated services
                self._update_dbus_environment(session)
            else:
                logger.warning("D-Bus launch returned no address for %s",
                               session.username)
        except Exception as e:
            logger.warning("D-Bus failed for %s: %s", session.username, e)

    def _update_dbus_environment(self, session: UserSession):
        """Push DISPLAY and XAUTHORITY into D-Bus so activated services inherit them.

        This is critical — without it, D-Bus service activation (e.g.
        gnome-terminal via StartServiceByName) won't know which X display
        to use and will fail.
        """
        env_vars = {
            "DISPLAY": session.display,
            "XDG_SESSION_TYPE": "x11",
            "GDK_BACKEND": "x11",
        }
        if session.xauthority:
            env_vars["XAUTHORITY"] = session.xauthority

        try:
            # dbus-update-activation-environment pushes vars into dbus-daemon/broker
            # AND systemd --user, so ALL activated services get them
            cmd = ["dbus-update-activation-environment", "--systemd"]
            for k, v in env_vars.items():
                cmd.append(f"{k}={v}")

            # Must pass DISPLAY and DBUS_SESSION_BUS_ADDRESS in the
            # calling environment too, or the tool complains
            run_env = session.env.copy()
            run_env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5,
                preexec_fn=lambda: self._demote(session.uid, session.gid),
                env=run_env)
            if result.returncode == 0:
                logger.info("Updated D-Bus activation environment for %s: DISPLAY=%s",
                            session.username, session.display)
            else:
                logger.warning("dbus-update-activation-environment failed: %s",
                               result.stderr.strip())
        except FileNotFoundError:
            # Fallback: use busctl to set environment on systemd user manager
            try:
                for k, v in env_vars.items():
                    subprocess.run(
                        ["busctl", "--user", "call",
                         "org.freedesktop.systemd1",
                         "/org/freedesktop/systemd1",
                         "org.freedesktop.systemd1.Manager",
                         "SetEnvironment", "as", "1", f"{k}={v}"],
                        capture_output=True, timeout=5,
                        preexec_fn=lambda: self._demote(session.uid, session.gid),
                        env=session.env)
                logger.info("Updated systemd user environment for %s via busctl",
                            session.username)
            except Exception as e2:
                logger.warning("Failed to update D-Bus environment: %s", e2)
        except Exception as e:
            logger.warning("Failed to update D-Bus environment: %s", e)

    def _start_pulseaudio(self, session: UserSession):
        """Start a PulseAudio server for the user's session."""
        try:
            env = session.env.copy()
            env["PULSE_RUNTIME_PATH"] = f"/run/user/{session.uid}/pulse"
            os.makedirs(env["PULSE_RUNTIME_PATH"], mode=0o700, exist_ok=True)
            os.chown(env["PULSE_RUNTIME_PATH"], session.uid, session.gid)

            session.pulseaudio_proc = subprocess.Popen(
                ["pulseaudio", "--start", "--exit-idle-time=-1", "--daemonize=no"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                preexec_fn=lambda: self._demote(session.uid, session.gid),
                env=env)
            logger.info("PulseAudio started for %s", session.username)
        except Exception as e:
            logger.warning("PulseAudio failed for %s: %s", session.username, e)

    def _get_logind_session_id(self, username: str) -> str:
        """Find an existing logind session ID for the user.

        Prefers sessions in 'active' state, falling back to any
        running session. Returns empty string if none found.
        """
        try:
            result = subprocess.run(
                ["loginctl", "list-sessions", "--no-legend"],
                capture_output=True, text=True, timeout=5)
            # Columns: SESSION UID USER SEAT TTY STATE [IDLE SINCE]
            # Rocky 9 loginctl doesn't show STATE in list-sessions by default
            # so we query each candidate session individually.
            candidates = []
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 3 and parts[2] == username:
                    candidates.append(parts[0])

            active = []
            other = []
            for sid in candidates:
                try:
                    show = subprocess.run(
                        ["loginctl", "show-session", sid,
                         "-p", "State", "--value"],
                        capture_output=True, text=True, timeout=5)
                    state = show.stdout.strip()
                    if state == "active":
                        active.append(sid)
                    elif state in ("online", "opening"):
                        other.append(sid)
                except Exception:
                    continue

            if active:
                return active[0]
            if other:
                return other[0]
        except Exception as e:
            logger.debug("Could not query logind sessions: %s", e)
        return ""

    def _start_logind_session(self, session: UserSession) -> str:
        """Create a real logind session for the user and hold it open.

        gnome-shell's ScreenShield initialization calls
        ``getCurrentSessionProxy()`` which queries logind for a session
        owned by the user. Without one, it crashes at start with::

            TypeError: this._userProxy.Display is null

        We can't just call ``busctl CreateSession`` because busctl
        returns and exits immediately, which closes its copy of the
        returned ``fifo_fd``, which causes logind to tear the session
        down. Instead we spawn a tiny Python helper that calls
        ``login1.Manager.CreateSession`` via Gio/GLib and blocks
        forever on ``signal.pause()`` holding the fd. When we kill the
        helper during session cleanup, logind sees EOF on the fifo and
        destroys the session cleanly.

        The helper must be spawned from outside any existing user
        session cgroup, otherwise logind refuses with
        ``SessionBusy: Already running in a session or user slice``.
        teraguchi-server runs in ``system.slice``, so the helper
        inherits that cgroup and CreateSession succeeds. If you ever
        invoke this path from an interactive shell for debugging, wrap
        it in ``systemd-run --scope --slice=system.slice``.

        Returns the logind session id (e.g. ``"c5"``) on success, or
        an empty string on failure.
        """
        helper_path = os.path.join(os.path.dirname(__file__),
                                   "logind_session_helper.py")
        if not os.path.exists(helper_path):
            logger.warning("logind_session_helper.py not found at %s, "
                           "gnome-shell will likely crash at ScreenShield init",
                           helper_path)
            return ""

        # python3-gobject on Rocky 9 is only installed for the system
        # Python 3.9. The teraguchi venv does not have it, and /usr/bin/
        # python3 may be 3.11 on some hosts without gi installed.
        py = "/usr/bin/python3.9"
        if not os.path.exists(py):
            py = "/usr/bin/python3"

        try:
            proc = subprocess.Popen(
                [py, helper_path, str(session.uid), session.display],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # Do NOT preexec_fn=_demote — logind's CreateSession
                # needs to be called as root to register a session for
                # another uid.
                start_new_session=True,
            )
        except Exception as e:
            logger.warning("Failed to spawn logind session helper: %s", e)
            return ""

        # Read one line of stdout to get the session id. If the helper
        # hits an error it writes ERROR to stderr and exits; we detect
        # that by polling.
        try:
            line = proc.stdout.readline().strip()
        except Exception as e:
            logger.warning("Failed to read logind session helper stdout: %s", e)
            line = ""

        if not line or proc.poll() is not None:
            err = ""
            try:
                err = proc.stderr.read().strip()
            except Exception:
                pass
            logger.warning("logind session helper failed for %s: %s",
                           session.username, err or "no output")
            try:
                proc.terminate()
            except Exception:
                pass
            return ""

        session.logind_proc = proc
        session.logind_session_id = line
        logger.info("Created logind session %s for %s (helper pid %d)",
                    line, session.username, proc.pid)
        return line

    def _ensure_linger(self, username: str) -> bool:
        """Enable systemd-logind linger for the user (idempotent).

        Linger keeps ``user@UID.service`` and the user D-Bus bus at
        ``/run/user/<uid>/bus`` running regardless of whether the user
        has an active login session. Without it, D-Bus activation of
        services like ``gnome-terminal-server`` fails whenever no
        interactive session exists — which is exactly our situation
        (the "session" is a headless Xorg spawned by the teraguchi
        server, not a real logind session).
        """
        try:
            check = subprocess.run(
                ["loginctl", "show-user", username,
                 "-p", "Linger", "--value"],
                capture_output=True, text=True, timeout=5)
            if check.returncode == 0 and check.stdout.strip() == "yes":
                return True
        except Exception as e:
            logger.debug("Could not check linger state for %s: %s", username, e)

        try:
            result = subprocess.run(
                ["loginctl", "enable-linger", username],
                capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                logger.info("Enabled logind linger for %s", username)
                # Give user@UID.service / dbus-broker a moment to come up
                time.sleep(1)
                return True
            logger.warning("enable-linger for %s failed: %s",
                           username, result.stderr.strip())
        except Exception as e:
            logger.warning("Could not enable linger for %s: %s", username, e)
        return False

    def _start_window_manager(self, session: UserSession):
        try:
            env = session.env.copy()

            if self._wm_cmd and self._wm_cmd[0] == "gnome-shell":
                # Belt-and-suspenders: linger keeps user@UID.service up
                # even if our logind helper somehow dies. D-Bus activation
                # of gnome-terminal-server needs the user bus to exist.
                self._ensure_linger(session.username)

                # gnome-shell crashes at startup without a real logind
                # session because ScreenShield.init calls
                # getCurrentSessionProxy() and dereferences null on no
                # session. Create (or reuse) a real session before WM
                # spawn. Helper holds the fifo_fd alive for the lifetime
                # of the session.
                if not session.logind_session_id or (
                        session.logind_proc and
                        session.logind_proc.poll() is not None):
                    self._start_logind_session(session)

                if session.logind_session_id:
                    env["XDG_SESSION_ID"] = session.logind_session_id
                    logger.info("Using logind session %s for gnome-shell",
                                session.logind_session_id)
                else:
                    # Fall back to any pre-existing session for the user
                    # (from a real login, ssh, etc.).
                    fallback_id = self._get_logind_session_id(session.username)
                    if fallback_id:
                        env["XDG_SESSION_ID"] = fallback_id
                        logger.info("Using pre-existing logind session %s "
                                    "for gnome-shell", fallback_id)
                    else:
                        logger.warning("No logind session for %s; gnome-shell "
                                       "will likely crash at ScreenShield init",
                                       session.username)

                env["GNOME_SHELL_SESSION_MODE"] = "classic"
                env["XDG_CURRENT_DESKTOP"] = "GNOME-Classic:GNOME"
                env["GDK_BACKEND"] = "x11"
                # Force Mutter to always composite — do NOT unredirect
                # fullscreen windows OR honor _NET_WM_BYPASS_COMPOSITOR.
                # Pro creative apps like Flame set BYPASS_COMPOSITOR on
                # their top-level window, which causes Mutter to stop
                # compositing them. That bypasses the compositor entirely
                # and renders directly to the front buffer, which in turn
                # causes torn frames to be captured by mss/XGetImage.
                # With this flag set, Mutter always composites through
                # its own pipeline, giving us coherent framebuffer reads
                # during video playback.
                env["MUTTER_DEBUG_DISABLE_UNREDIRECT"] = "1"
                # NVIDIA-specific: tell the GLX driver to block Mutter's
                # swap until vblank, so the compositor never presents a
                # half-drawn frame. USLEEP yield avoids busy-waiting.
                # These are no-ops on Mesa, so safe to set unconditionally.
                env["__GL_SYNC_TO_VBLANK"] = "1"
                env["__GL_YIELD"] = "USLEEP"
                if not session.gpu_display:
                    # Force software rendering for Xvfb — the GPU's EGL/GLX
                    # context isn't available on virtual displays
                    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
                    env["__GLX_VENDOR_LIBRARY_NAME"] = "mesa"

            wm_cmd = list(self._wm_cmd)
            if wm_cmd[0] == "gnome-shell":
                wm_cmd.extend(["--display=" + session.display, "--replace"])

            session.wm_proc = subprocess.Popen(
                wm_cmd,
                stdout=subprocess.DEVNULL,
                stderr=open(f"/tmp/teraguchi-wm-{session.username}.log", "w"),
                preexec_fn=lambda: self._demote(session.uid, session.gid),
                env=env)
            logger.info("Window manager started for %s: %s",
                        session.username, self._wm_cmd[0])

            # Start gnome-terminal-server so terminal launches work via D-Bus
            if wm_cmd[0] == "gnome-shell":
                self._start_gnome_terminal_server(session)
        except Exception as e:
            logger.warning("WM failed for %s: %s", session.username, e)

    def _start_compositor(self, session: UserSession):
        """Start an X compositor (xcompmgr) so that screen capture reads
        coherent, non-torn framebuffers during video playback.

        Without a compositor, apps draw directly to the X front buffer and
        mss/XGetImage can read mid-swap, producing horizontal tear bands.
        xcompmgr enables the Composite extension and redirects every window
        to an off-screen pixmap, which it then composites to the root — so
        every read sees a finished frame.

        Flags:
          -n   no client-side shadows/fade (we don't want visual effects,
               just the composite redirect)
        """
        compositor_bin = shutil.which("xcompmgr")
        if not compositor_bin:
            logger.warning("xcompmgr not installed — screen capture may tear "
                           "during video playback. Install xcompmgr to fix.")
            return
        try:
            session.compositor_proc = subprocess.Popen(
                [compositor_bin, "-n"],
                stdout=subprocess.DEVNULL,
                stderr=open(f"/tmp/teraguchi-xcompmgr-{session.username}.log", "w"),
                preexec_fn=lambda: self._demote(session.uid, session.gid),
                env=session.env)
            logger.info("xcompmgr started for %s (pid %d)",
                        session.username, session.compositor_proc.pid)
        except Exception as e:
            logger.warning("xcompmgr failed for %s: %s", session.username, e)

    def _start_gnome_terminal_server(self, session: UserSession):
        """Pre-start gnome-terminal-server so gnome-terminal can connect."""
        try:
            gt_server = shutil.which("gnome-terminal-server")
            if not gt_server:
                gt_server = "/usr/libexec/gnome-terminal-server"
            if not os.path.exists(gt_server):
                logger.debug("gnome-terminal-server not found, skipping")
                return
            subprocess.Popen(
                [gt_server, "--app-id", "org.gnome.Terminal"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=lambda: self._demote(session.uid, session.gid),
                env=session.env)
            logger.info("gnome-terminal-server started for %s", session.username)
        except Exception as e:
            logger.debug("gnome-terminal-server failed: %s", e)

    def resize_display(self, username: str, width: int, height: int) -> bool:
        """Resize an existing session's display via xrandr."""
        session = self.get_session(username)
        if not session or not session.alive:
            return False

        # Clamp to reasonable bounds
        width = max(640, min(width, 3840))
        height = max(480, min(height, 2160))

        if width == session.width and height == session.height:
            return True

        try:
            display = session.display
            env = {**os.environ, "DISPLAY": display}

            # Add the new mode if it doesn't exist
            mode_name = f"{width}x{height}"
            # Generate modeline
            modeline = subprocess.run(
                ["cvt", str(width), str(height)],
                capture_output=True, text=True, timeout=5, env=env)
            if modeline.returncode == 0:
                # Parse modeline output: Modeline "WxH_60.00" ...
                for line in modeline.stdout.strip().split("\n"):
                    if line.startswith("Modeline"):
                        parts = line.split(None, 2)
                        mode_label = parts[1].strip('"')
                        mode_params = parts[2]

                        # Create new mode
                        subprocess.run(
                            ["xrandr", "--newmode", mode_label] + mode_params.split(),
                            capture_output=True, timeout=5, env=env)
                        # Add mode to screen output
                        subprocess.run(
                            ["xrandr", "--addmode", "screen", mode_label],
                            capture_output=True, timeout=5, env=env)
                        # Set the mode
                        result = subprocess.run(
                            ["xrandr", "--output", "screen", "--mode", mode_label],
                            capture_output=True, text=True, timeout=5, env=env)
                        if result.returncode == 0:
                            session.width = width
                            session.height = height
                            logger.info("Resized display %s to %dx%d",
                                        display, width, height)
                            return True
                        else:
                            logger.warning("xrandr set mode failed: %s", result.stderr)

        except Exception as e:
            logger.warning("Display resize failed: %s", e)

        return False

    def destroy_session(self, username: str):
        session = self._sessions.pop(username, None)
        if session:
            self._cleanup_session(session)
            logger.info("Session destroyed: %s", username)

    def _cleanup_session(self, session: UserSession):
        # Close the pen tablet first so the uinput device is destroyed
        # before Xorg shuts down (avoids stale input device errors in log).
        if session.pen_tablet:
            try:
                session.pen_tablet.close()
            except Exception as e:
                logger.debug("pen_tablet close: %s", e)
            session.pen_tablet = None

        x_name = "Xorg" if session.gpu_display else "Xvfb"
        # Order matters: kill the WM and compositor before tearing down
        # the logind session, because gnome-shell tries to talk to logind
        # on exit. Xorg goes last so input devices and GL contexts can
        # unwind cleanly.
        for name, proc in [("xcompmgr", session.compositor_proc),
                           ("WM", session.wm_proc),
                           ("logind-session", session.logind_proc),
                           ("PulseAudio", session.pulseaudio_proc),
                           ("D-Bus", session.dbus_proc),
                           (x_name, session.xorg_proc)]:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                logger.debug("Stopped %s (pid %d) for %s",
                             name, proc.pid, session.username)

        # Kill dbus-launch daemon if we have its PID
        if session.dbus_pid:
            try:
                os.kill(session.dbus_pid, signal.SIGTERM)
                logger.debug("Stopped D-Bus (pid %d) for %s", session.dbus_pid, session.username)
            except OSError:
                pass

        if session.xauthority and os.path.exists(session.xauthority):
            try:
                os.unlink(session.xauthority)
            except OSError:
                pass

        # Clean up temp xorg config
        config_path = f"/tmp/teraguchi-xorg-{session.display_num}.conf"
        if os.path.exists(config_path):
            try:
                os.unlink(config_path)
            except OSError:
                pass

        lock_file = f"/tmp/.X{session.display_num}-lock"
        if os.path.exists(lock_file):
            try:
                os.unlink(lock_file)
            except OSError:
                pass

    def destroy_all(self):
        for username in list(self._sessions.keys()):
            self.destroy_session(username)
        logger.info("All sessions destroyed")

    def list_sessions(self) -> List[dict]:
        return [
            {
                "username": s.username,
                "display": s.display,
                "resolution": f"{s.width}x{s.height}",
                "clients": s.connected_clients,
                "alive": s.alive,
                "uptime_s": int(time.time() - s.created_at),
            }
            for s in self._sessions.values()
        ]
