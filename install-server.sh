#!/usr/bin/env bash
#
# Teraguchi Server Installer (Linux)
#
# Installs all system dependencies, Python packages, configures uinput,
# and optionally sets up a systemd service.
#
# Usage:
#   sudo bash install-server.sh
#   sudo bash install-server.sh --no-service    # skip systemd setup
#   sudo bash install-server.sh --no-auth       # skip user creation
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/teraguchi"
VENV_DIR="$INSTALL_DIR/.venv"
SERVICE_NAME="teraguchi-server"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# Parse args
SETUP_SERVICE=true
SETUP_AUTH=true
for arg in "$@"; do
    case "$arg" in
        --no-service) SETUP_SERVICE=false ;;
        --no-auth)    SETUP_AUTH=false ;;
        --help|-h)
            echo "Usage: sudo bash install-server.sh [--no-service] [--no-auth]"
            exit 0
            ;;
    esac
done

# ── Preflight ─────────────────────────────────────────────────────

if [ "$EUID" -ne 0 ]; then
    err "This script must be run as root (sudo)."
    echo "  sudo bash install-server.sh"
    exit 1
fi

REAL_USER="${SUDO_USER:-$USER}"
if [ "$REAL_USER" = "root" ]; then
    warn "Could not detect your normal username."
    read -rp "Enter the username that will run the server: " REAL_USER
fi

info "Installing Teraguchi server for user: $REAL_USER"
echo ""

# ── Detect distro ────────────────────────────────────────────────

detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$ID"
    elif command -v lsb_release &>/dev/null; then
        lsb_release -si | tr '[:upper:]' '[:lower:]'
    else
        echo "unknown"
    fi
}

DISTRO=$(detect_distro)
info "Detected distro: $DISTRO"

# ── Install system packages ──────────────────────────────────────

info "Installing system packages..."

case "$DISTRO" in
    ubuntu|debian|pop|linuxmint|elementary)
        apt-get update -qq
        apt-get install -y -qq \
            python3 python3-pip python3-venv \
            xserver-xorg-core xvfb x11-xserver-utils xauth \
            ffmpeg \
            pulseaudio-utils \
            xclip \
            linux-tools-common \
            libsvtav1enc-dev \
            2>/dev/null
        # VAAPI support (Intel/AMD GPU encoding)
        apt-get install -y -qq \
            vainfo intel-media-va-driver-non-free mesa-va-drivers \
            2>/dev/null || true
        ok "APT packages installed"
        ;;
    fedora)
        dnf install -y -q \
            python3 python3-pip \
            xorg-x11-server-Xvfb xorg-x11-utils \
            ffmpeg-free \
            pulseaudio-utils \
            xclip \
            usbip \
            2>/dev/null
        ok "DNF packages installed"
        ;;
    rhel|rocky|almalinux|centos)
        # Enable EPEL and CRB/PowerTools for ffmpeg
        dnf install -y -q epel-release 2>/dev/null || true
        dnf config-manager --set-enabled crb 2>/dev/null || \
            dnf config-manager --set-enabled powertools 2>/dev/null || true
        dnf install -y -q \
            python3 python3-pip \
            xorg-x11-server-Xorg xorg-x11-server-Xvfb \
            xorg-x11-utils xorg-x11-xauth \
            ffmpeg \
            pulseaudio-utils \
            xclip \
            2>/dev/null
        ok "DNF packages installed"

        # DO NOT install xorg-x11-drv-nvidia. Most DXS Flame hosts get
        # their NVIDIA driver from a DKU (DKMS kernel update) or a
        # manual .run installer, and re-running this installer would
        # pull in the RPM-packaged driver on top of the existing one —
        # RPMFusion akmod + xorg-x11-drv-nvidia-libs + the DKU'd driver
        # stomp on each other's .so files and leave the userspace libs
        # (libnvidia-fbc, libGLX_nvidia, libEGL_nvidia, ...) physically
        # missing. Happened on dxs-flame-02 on 2026-04-10. Leave the
        # driver alone.
        if ! command -v nvidia-smi &>/dev/null; then
            warn "nvidia-smi not found — NVIDIA driver appears to be missing."
            warn "Teraguchi will run on Xvfb (software rendering) which is"
            warn "unusable for Flame. Install the NVIDIA driver out-of-band"
            warn "(DKU, .run installer, or your normal provisioning) BEFORE"
            warn "starting the server."
        else
            ok "NVIDIA driver present: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)"
        fi
        ;;
    arch|manjaro|endeavouros)
        pacman -Sy --noconfirm --needed \
            python python-pip \
            xorg-server-xvfb xorg-xrandr \
            ffmpeg \
            pulseaudio \
            xclip \
            2>/dev/null
        ok "Pacman packages installed"
        ;;
    opensuse*|suse*)
        zypper install -y \
            python3 python3-pip \
            xorg-x11-server-Xvfb xrandr \
            ffmpeg \
            pulseaudio-utils \
            xclip \
            2>/dev/null
        ok "Zypper packages installed"
        ;;
    *)
        warn "Unknown distro '$DISTRO'. Please install manually:"
        warn "  python3, python3-pip, python3-venv, Xvfb, xrandr, ffmpeg, pulseaudio-utils, xclip"
        ;;
esac

# ── Verify FFmpeg ────────────────────────────────────────────────

echo ""
if command -v ffmpeg &>/dev/null; then
    FFMPEG_VERSION=$(ffmpeg -version 2>&1 | head -1)
    ok "FFmpeg found: $FFMPEG_VERSION"

    # Check for H.264 support
    if ffmpeg -hide_banner -encoders 2>&1 | grep -q libx264; then
        ok "H.264 (libx264) available"
    else
        warn "libx264 not found — H.264 encoding won't work."
        warn "Install a full ffmpeg build with libx264 support."
    fi

    # Check for H.265 support
    if ffmpeg -hide_banner -encoders 2>&1 | grep -q libx265; then
        ok "H.265 (libx265) available"
    else
        info "libx265 not found — H.265 won't be available (H.264 is fine)"
    fi

    # Check for AV1 support
    if ffmpeg -hide_banner -encoders 2>&1 | grep -q libsvtav1; then
        ok "AV1 (SVT-AV1) available"
    else
        info "SVT-AV1 not found — AV1 software encoding won't be available"
    fi

    # Check for GPU encoders
    if ffmpeg -hide_banner -encoders 2>&1 | grep -q nvenc; then
        ok "NVIDIA NVENC GPU encoding available"
    fi
    if ffmpeg -hide_banner -encoders 2>&1 | grep -q vaapi; then
        ok "VAAPI GPU encoding available (Intel/AMD)"
    fi
    if ffmpeg -hide_banner -encoders 2>&1 | grep -q amf; then
        ok "AMD AMF GPU encoding available"
    fi
else
    err "FFmpeg not found! Video encoding will fall back to JPEG only."
fi

# ── Setup uinput ─────────────────────────────────────────────────

echo ""
info "Configuring uinput (virtual input devices)..."

# Create uinput group
if ! getent group uinput &>/dev/null; then
    groupadd uinput
    ok "Created 'uinput' group"
fi

# Add user to uinput group
usermod -aG uinput "$REAL_USER"
ok "Added '$REAL_USER' to 'uinput' group"

# udev rule
UDEV_RULE="/etc/udev/rules.d/99-teraguchi-uinput.rules"
cat > "$UDEV_RULE" << 'EOF'
# Teraguchi: Allow uinput group to access /dev/uinput
KERNEL=="uinput", GROUP="uinput", MODE="0660"
EOF
ok "Created udev rule: $UDEV_RULE"

# Load kernel modules
modprobe uinput
ok "Loaded uinput module"

modprobe usbip-core 2>/dev/null && ok "Loaded usbip-core module" || info "usbip-core not available (USB passthrough disabled)"
modprobe vhci-hcd 2>/dev/null && ok "Loaded vhci-hcd module" || info "vhci-hcd not available (USB passthrough disabled)"

# Persist modules
cat > /etc/modules-load.d/teraguchi.conf << 'EOF'
uinput
usbip-core
vhci-hcd
EOF

# Reload udev
udevadm control --reload-rules
udevadm trigger
ok "udev rules reloaded"

# ── Install Teraguchi ────────────────────────────────────────────

echo ""
info "Installing Teraguchi to $INSTALL_DIR ..."

mkdir -p "$INSTALL_DIR"

# Copy project files
cp -r "$SCRIPT_DIR/server"  "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/common"  "$INSTALL_DIR/"
cp    "$SCRIPT_DIR/requirements-server.txt" "$INSTALL_DIR/"
cp    "$SCRIPT_DIR/pyproject.toml"          "$INSTALL_DIR/"
ok "Copied source files"

# ── NvFBC helper (NVIDIA only) ───────────────────────────────────
#
# Build and install the small C helper that screen_capture.py uses
# for tear-free framebuffer capture on NVIDIA hosts. Silently skipped
# on non-NVIDIA machines — teraguchi falls back to mss/XShmGetImage
# automatically in that case.

NVFBC_LIB=""
for p in /usr/lib64/libnvidia-fbc.so.1 \
         /usr/lib/libnvidia-fbc.so.1 \
         /usr/lib/x86_64-linux-gnu/libnvidia-fbc.so.1; do
    if [ -e "$p" ]; then NVFBC_LIB="$p"; break; fi
done

if [ -n "$NVFBC_LIB" ] && command -v cc &>/dev/null; then
    info "Building NvFBC capture helper (${NVFBC_LIB})..."
    if (cd "$INSTALL_DIR/server/nvfbc" && make clean >/dev/null 2>&1 && make >/dev/null 2>&1); then
        ok "NvFBC helper built: $INSTALL_DIR/server/nvfbc/nvfbc_capture"
    else
        warn "NvFBC helper build failed — capture will fall back to mss."
        warn "Try building manually: cd $INSTALL_DIR/server/nvfbc && make"
    fi
elif [ -n "$NVFBC_LIB" ]; then
    warn "libnvidia-fbc.so.1 present but no C compiler found — install gcc"
    warn "and rebuild $INSTALL_DIR/server/nvfbc to enable tear-free capture."
else
    info "No libnvidia-fbc.so.1 detected (non-NVIDIA or headless) — skipping"
    info "NvFBC helper build. Capture will use mss/XShmGetImage."
fi

# Create venv and install Python deps
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements-server.txt" -q
ok "Python dependencies installed"

# Set ownership
chown -R "$REAL_USER:$REAL_USER" "$INSTALL_DIR"
ok "Set ownership to $REAL_USER"

# ── Create user account ─────────────────────────────────────────

if [ "$SETUP_AUTH" = true ]; then
    echo ""
    info "Setting up authentication..."
    echo "  Create a login for remote connections."
    echo "  (You can skip this and use --no-auth on the server later.)"
    echo ""
    read -rp "  Username [teraguchi]: " AUTH_USER
    AUTH_USER="${AUTH_USER:-teraguchi}"

    # Use the venv Python to add the user
    sudo -u "$REAL_USER" "$VENV_DIR/bin/python" -c "
import sys, getpass
sys.path.insert(0, '$INSTALL_DIR')
from server.auth import Authenticator
auth = Authenticator(enabled=True)
password = getpass.getpass('  Password: ')
auth.add_user('$AUTH_USER', password)
print('  User created: $AUTH_USER')
" || warn "Could not create user. You can do it later: python -m server.main --add-user USERNAME"
fi

# ── Systemd service ──────────────────────────────────────────────

if [ "$SETUP_SERVICE" = true ]; then
    echo ""
    info "Setting up systemd service..."

    # ── Resilient headless NVIDIA display ────────────────────────
    # On an NVIDIA host, install a dedicated Xorg service that regenerates its
    # own config (fresh BusID) into /run on every start and never touches
    # /etc/X11/xorg.conf — so NVIDIA driver updates can't break the display.
    # See server/setup-headless-display.sh for the resilience model.
    XORG_DEP="After=network.target"
    DISPLAY_ENV=""
    if command -v nvidia-smi &>/dev/null; then
        info "NVIDIA GPU detected — installing resilient headless Xorg service..."
        chmod +x "$INSTALL_DIR/server/setup-headless-display.sh"
        # Point the unit's /opt/teraguchi paths at the real install dir.
        sed "s|/opt/teraguchi|$INSTALL_DIR|g" \
            "$INSTALL_DIR/server/teraguchi-xorg.service" \
            > "/etc/systemd/system/teraguchi-xorg.service"
        ok "Headless Xorg service installed: teraguchi-xorg (display :0)"
        XORG_DEP=$'After=network.target teraguchi-xorg.service\nRequires=teraguchi-xorg.service'
        DISPLAY_ENV="Environment=DISPLAY=:0"
    fi

    cat > "/etc/systemd/system/${SERVICE_NAME}.service" << SVCEOF
[Unit]
Description=Teraguchi Remote Desktop Server
$XORG_DEP

[Service]
Type=simple
User=root
$DISPLAY_ENV
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python -m server.main --port 4443 --fps 30 --codec h264 --chroma yuv444
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/teraguchi/server.log
StandardError=append:/var/log/teraguchi/server.log

[Install]
WantedBy=multi-user.target
SVCEOF

    mkdir -p /var/log/teraguchi

    systemctl daemon-reload
    ok "Systemd service created: $SERVICE_NAME"

    read -rp "  Start the service now? [y/N]: " START_NOW
    if [[ "$START_NOW" =~ ^[Yy] ]]; then
        # Bring up the display first so the server finds DISPLAY=:0 on start.
        if [ -f /etc/systemd/system/teraguchi-xorg.service ]; then
            systemctl enable --now teraguchi-xorg.service
            ok "Headless display started and enabled on boot"
        fi
        systemctl enable --now "$SERVICE_NAME"
        ok "Service started and enabled on boot"
    else
        if [ -f /etc/systemd/system/teraguchi-xorg.service ]; then
            info "Start later with: sudo systemctl enable --now teraguchi-xorg $SERVICE_NAME"
        else
            info "Start later with: sudo systemctl enable --now $SERVICE_NAME"
        fi
    fi
fi

# ── Firewall ─────────────────────────────────────────────────────

echo ""
info "Firewall: Teraguchi uses port 443 (TCP+UDP) and 444 (QUIC/UDP)."

if command -v ufw &>/dev/null; then
    read -rp "  Open ports 443-444 (TCP+UDP) in UFW? [y/N]: " OPEN_FW
    if [[ "$OPEN_FW" =~ ^[Yy] ]]; then
        ufw allow 443/tcp
        ufw allow 443/udp
        ufw allow 444/udp
        ok "UFW: ports 443 TCP+UDP and 444 UDP opened"
    fi
elif command -v firewall-cmd &>/dev/null; then
    read -rp "  Open ports 443-444 (TCP+UDP) in firewalld? [y/N]: " OPEN_FW
    if [[ "$OPEN_FW" =~ ^[Yy] ]]; then
        firewall-cmd --permanent --add-port=443/tcp
        firewall-cmd --permanent --add-port=443/udp
        firewall-cmd --permanent --add-port=444/udp
        firewall-cmd --reload
        ok "firewalld: ports 443 TCP+UDP and 444 UDP opened"
    fi
else
    info "No firewall tool detected. Make sure ports 443 TCP+UDP and 444 UDP are open."
fi

# ── Done ─────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════"
echo -e "${GREEN}  Teraguchi server installation complete!${NC}"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Install dir:  $INSTALL_DIR"
echo "  Python venv:  $VENV_DIR"
echo "  User:         $REAL_USER"
echo ""
echo -e "${YELLOW}  IMPORTANT: Log out and back in for uinput group to take effect.${NC}"
echo ""
echo "  Start manually:"
echo "    cd $INSTALL_DIR"
echo "    $VENV_DIR/bin/python -m server.main --verbose"
echo ""
if [ "$SETUP_SERVICE" = true ]; then
    echo "  Or via systemd:"
    echo "    sudo systemctl start $SERVICE_NAME"
    echo "    sudo systemctl status $SERVICE_NAME"
    echo "    journalctl -u $SERVICE_NAME -f"
    echo ""
fi
echo "  Your IP addresses:"
hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^$' | sed 's/^/    /'
echo ""
echo "  Connect from client:"
echo "    python -m client.main --host <IP_ABOVE>"
echo ""
