#!/usr/bin/env bash
# Teraguchi AI Client installer
# Creates .venv-ai, installs dependencies, prints MCP config snippet.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV=".venv-ai"
PYTHON_BIN="$VENV/bin/python"

echo "==> Creating virtual environment: $VENV"
uv venv "$VENV"

echo "==> Installing AI client dependencies"
uv pip install --python "$PYTHON_BIN" -r requirements-ai.txt

echo ""
echo "==> Done. Add the following to ~/.cursor/mcp.json under 'mcpServers':"
echo ""
cat <<EOF
    "teraguchi": {
      "command": "$SCRIPT_DIR/$PYTHON_BIN",
      "args": ["-m", "ai.mcp_server"],
      "cwd": "$SCRIPT_DIR",
      "env": {
        "TERAGUCHI_HOST": "192.168.178.94",
        "TERAGUCHI_PORT": "4443",
        "TERAGUCHI_USER": "mihai",
        "TERAGUCHI_PASS": "mihai123",
        "TERAGUCHI_TLS":  "1"
      }
    }
EOF
echo ""
echo "==> Quick test (after adding to mcp.json, restart Cursor):"
echo "    $SCRIPT_DIR/$PYTHON_BIN -c \\"
echo "      'import sys; sys.path.insert(0, \"$SCRIPT_DIR\");"
echo "       from ai.headless_client import HeadlessClient; print(\"import ok\")'"
