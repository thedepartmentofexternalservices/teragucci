#!/bin/bash
# Test Teraguchi server on flame-02 without a GUI client
# Run from dxs-ansible: bash test_server.sh

HOST="10.10.0.12"
DISPLAY_NUM=10

echo "=== Teraguchi Server Test ==="
echo ""

# 1. Check service
echo "1. Service status:"
ssh -i ~/.ssh/dxs-2025-v1 randy.mcentee@$HOST "systemctl is-active teraguchi-server"

# 2. Check GPU
echo ""
echo "2. GPU:"
ssh -i ~/.ssh/dxs-2025-v1 randy.mcentee@$HOST "nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader"

# 3. Check if Xorg is running on our display
echo ""
echo "3. Xorg process:"
ssh -i ~/.ssh/dxs-2025-v1 randy.mcentee@$HOST "ps aux | grep 'Xorg.*:$DISPLAY_NUM' | grep -v grep || echo 'NOT RUNNING'"

# 4. Check gnome-shell
echo ""
echo "4. Window manager:"
ssh -i ~/.ssh/dxs-2025-v1 randy.mcentee@$HOST "ps aux | grep 'gnome-shell.*:$DISPLAY_NUM' | grep -v grep || echo 'NOT RUNNING'"

# 5. Test X display works (glxinfo for GPU verification)
echo ""
echo "5. GLX info (GPU verification):"
ssh -i ~/.ssh/dxs-2025-v1 randy.mcentee@$HOST "DISPLAY=:$DISPLAY_NUM glxinfo 2>/dev/null | grep -E 'direct rendering|OpenGL vendor|OpenGL renderer' || echo 'glxinfo not available or display not ready'"

# 6. Test xrandr
echo ""
echo "6. Display resolution:"
ssh -i ~/.ssh/dxs-2025-v1 randy.mcentee@$HOST "DISPLAY=:$DISPLAY_NUM xrandr --query 2>/dev/null | head -5 || echo 'xrandr failed'"

# 7. Check NVIDIA metamodes (critical for Flame)
echo ""
echo "7. NVIDIA MetaModes:"
ssh -i ~/.ssh/dxs-2025-v1 randy.mcentee@$HOST "DISPLAY=:$DISPLAY_NUM nvidia-settings -q CurrentMetaMode 2>/dev/null | head -3 || echo 'nvidia-settings failed'"

# 8. Test app launch (xterm as simple test)
echo ""
echo "8. App launch test (xterm):"
ssh -i ~/.ssh/dxs-2025-v1 randy.mcentee@$HOST "DISPLAY=:$DISPLAY_NUM DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/\$(id -u)/bus timeout 3 xterm -e 'echo APP_WORKS && sleep 1' 2>&1 && echo 'PASS' || echo 'xterm not installed or failed (non-critical)'"

# 9. Server log tail
echo ""
echo "9. Recent server log:"
ssh -i ~/.ssh/dxs-2025-v1 randy.mcentee@$HOST "tail -10 /var/log/teraguchi/server.log 2>/dev/null"

echo ""
echo "=== Test Complete ==="
