#!/usr/bin/env bash
# Materialize a resilient headless Xorg config for the NVIDIA GPU.
#
# WHY THIS EXISTS / RESILIENCE MODEL:
#   NVIDIA driver installs + updates (and tools like `nvidia-xconfig`) rewrite
#   /etc/X11/xorg.conf and can change the GPU's PCI BusID. If we depended on
#   that file we'd break on every driver update. Instead:
#     * We NEVER touch /etc/X11/xorg.conf. Xorg is started with an explicit
#       `-config /run/teraguchi/xorg.conf`, so the system config is ignored.
#     * We regenerate our config on EVERY start (this script is the service's
#       ExecStartPre), detecting the current BusID fresh — so a moved BusID
#       after a driver/hardware change is picked up automatically.
#     * On a VM/vGPU host there are often two display adapters (virtio VGA +
#       the NVIDIA GPU). Without an explicit BusID the NVIDIA driver reports
#       "No devices detected". Pinning the detected BusID fixes that.
#
# This mirrors how PCoIP / HP Anyware and NICE DCV run Autodesk Flame headlessly.
#
# Usage: setup-headless-display.sh [TEMPLATE] [OUT]
set -euo pipefail

TEMPLATE="${1:-/opt/teraguchi/server/xorg-teraguchi.conf}"
OUT="${2:-/run/teraguchi/xorg.conf}"

if [ ! -r "$TEMPLATE" ]; then
    echo "setup-headless-display: template not found: $TEMPLATE" >&2
    exit 1
fi

# Detect the NVIDIA GPU's PCI BusID (X-server format, e.g. "PCI:6:16:0").
BUSID=""
if command -v nvidia-xconfig >/dev/null 2>&1; then
    BUSID=$(nvidia-xconfig --query-gpu-info 2>/dev/null \
        | awk -F': ' '/PCI BusID/{gsub(/ /,"",$2); print $2; exit}')
fi
# Fallback: derive from nvidia-smi PCI bus id (form 00000000:06:10.0 -> PCI:6:16:0).
if [ -z "$BUSID" ] && command -v nvidia-smi >/dev/null 2>&1; then
    raw=$(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader 2>/dev/null | head -1)
    # raw = 00000000:06:10.0  -> bus=06 dev=10 fn=0 (decimal), X wants decimal ints
    if [[ "$raw" =~ :([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2})\.([0-9A-Fa-f]) ]]; then
        BUSID="PCI:$((16#${BASH_REMATCH[1]})):$((16#${BASH_REMATCH[2]})):$((16#${BASH_REMATCH[3]}))"
    fi
fi

if [ -z "$BUSID" ]; then
    echo "setup-headless-display: could not detect an NVIDIA GPU BusID" >&2
    echo "  (is the NVIDIA driver installed? try: nvidia-smi)" >&2
    exit 1
fi

mkdir -p "$(dirname "$OUT")"

# Inject the detected BusID right after the nvidia Driver line. If the template
# already carries a BusID (idempotent re-runs / hand-edited), replace it.
if grep -qE '^\s*BusID' "$TEMPLATE"; then
    sed -E "s|^\s*BusID.*|    BusID          \"$BUSID\"|" "$TEMPLATE" > "$OUT"
else
    sed "s|\(Driver[[:space:]]*\"nvidia\"\)|\1\n    BusID          \"$BUSID\"|" "$TEMPLATE" > "$OUT"
fi

echo "setup-headless-display: wrote $OUT (BusID $BUSID)"
