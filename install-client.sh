#!/usr/bin/env bash
#
# Teraguchi Client Installer (macOS / Linux)
#
# Installs Python dependencies, creates a launch script, and optionally
# builds a standalone app bundle with PyInstaller.
#
# Usage:
#   bash install-client.sh
#   bash install-client.sh --build    # also build standalone app
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

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

BUILD_APP=false
for arg in "$@"; do
    case "$arg" in
        --build) BUILD_APP=true ;;
        --help|-h)
            echo "Usage: bash install-client.sh [--build]"
            echo "  --build  Also build a standalone app with PyInstaller"
            exit 0
            ;;
    esac
done

SYSTEM=$(uname -s)
info "Platform: $SYSTEM"
echo ""

# ── Check Python ─────────────────────────────────────────────────

PYTHON=""
for cmd in python3 python python3.10; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$cmd"
            ok "Found $cmd ($("$cmd" --version))"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    err "Python 3.10+ is required but not found."
    echo ""
    if [ "$SYSTEM" = "Darwin" ]; then
        echo "  Install with Homebrew:"
        echo "    brew install python@3.10"
        echo ""
        echo "  Or download from: https://www.python.org/downloads/"
    else
        echo "  Install with your package manager:"
        echo "    sudo apt install python3    # Ubuntu/Debian"
        echo "    sudo dnf install python3    # Fedora/RHEL"
    fi
    exit 1
fi

# ── macOS: check for Xcode command line tools ────────────────────

if [ "$SYSTEM" = "Darwin" ]; then
    if ! xcode-select -p &>/dev/null; then
        warn "Xcode Command Line Tools not installed."
        info "Installing... (this may take a few minutes)"
        xcode-select --install 2>/dev/null || true
        echo "  If a dialog popped up, click Install, then re-run this script."
        exit 1
    fi
    ok "Xcode Command Line Tools present"
fi

# ── Create virtual environment ───────────────────────────────────

echo ""
info "Creating Python virtual environment..."

if [ -d "$VENV_DIR" ]; then
    info "Existing venv found, upgrading..."
else
    "$PYTHON" -m venv "$VENV_DIR"
fi

# Activate
source "$VENV_DIR/bin/activate"
ok "Virtual environment: $VENV_DIR"

# ── Install Python dependencies ──────────────────────────────────

info "Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements-client.txt" -q
ok "Dependencies installed (PySide6 + websockets)"

# ── Verify PySide6 ───────────────────────────────────────────────

echo ""
if python -c "from PySide6.QtWidgets import QApplication; print('PySide6 OK')" 2>/dev/null; then
    ok "PySide6 works"
else
    err "PySide6 import failed. On macOS, you may need:"
    echo "    brew install qt@6"
    echo "  Or try reinstalling:"
    echo "    pip install --force-reinstall PySide6"
    exit 1
fi

# ── Create launch script ────────────────────────────────────────

LAUNCHER="$SCRIPT_DIR/teraguchi"
cat > "$LAUNCHER" << LAUNCHEOF
#!/usr/bin/env bash
# Teraguchi Client Launcher
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
source "\$SCRIPT_DIR/.venv/bin/activate"
cd "\$SCRIPT_DIR"
python -m client.main "\$@"
LAUNCHEOF
chmod +x "$LAUNCHER"
ok "Created launcher: $LAUNCHER"

# ── macOS: Create .app bundle shortcut ───────────────────────────

if [ "$SYSTEM" = "Darwin" ]; then
    APP_DIR="$SCRIPT_DIR/Teraguchi.app/Contents/MacOS"
    mkdir -p "$APP_DIR"

    cat > "$APP_DIR/Teraguchi" << APPEOF
#!/usr/bin/env bash
SCRIPT_DIR="$SCRIPT_DIR"
source "\$SCRIPT_DIR/.venv/bin/activate"
cd "\$SCRIPT_DIR"
exec python -m client.main "\$@"
APPEOF
    chmod +x "$APP_DIR/Teraguchi"

    # Info.plist
    cat > "$SCRIPT_DIR/Teraguchi.app/Contents/Info.plist" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Teraguchi</string>
    <key>CFBundleIdentifier</key>
    <string>com.teraguchi.client</string>
    <key>CFBundleName</key>
    <string>Teraguchi</string>
    <key>CFBundleVersion</key>
    <string>2.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>2.0.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLISTEOF

    ok "Created macOS app: Teraguchi.app"
    info "You can drag Teraguchi.app to /Applications or double-click it."
fi

# ── Optional: Build standalone app ───────────────────────────────

if [ "$BUILD_APP" = true ]; then
    echo ""
    info "Building standalone application with PyInstaller..."
    pip install pyinstaller -q
    python "$SCRIPT_DIR/build_client.py"
    ok "Standalone app built in: $SCRIPT_DIR/dist/Teraguchi/"
fi

# ── Done ─────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════"
echo -e "${GREEN}  Teraguchi client installation complete!${NC}"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Launch:"
echo "    $LAUNCHER"
echo ""
echo "  Or with a direct connection:"
echo "    $LAUNCHER --host 192.168.1.100"
echo ""
echo "  With credentials:"
echo "    $LAUNCHER --host 192.168.1.100 -u myuser"
echo ""
if [ "$SYSTEM" = "Darwin" ]; then
    echo "  macOS: Double-click Teraguchi.app or drag it to Applications."
    echo ""
fi
echo "  To build a portable .app/.exe later:"
echo "    bash install-client.sh --build"
echo ""
