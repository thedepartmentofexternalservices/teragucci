# Teraguchi AI Client

Headless Python client that exposes a Teraguchi remote desktop session as an
MCP (Model Context Protocol) server, so AI agents (Cursor, Claude, etc.) can
see and control a remote machine without opening a graphical window.

## Architecture

```
AI Agent (Cursor / Claude)
        │  MCP tools
        ▼
  mcp_server.py  (FastMCP)
        │
  HeadlessClient
        │  WebSocket + TLS
        ▼
  Teraguchi Server  (remote Linux, NVENC H.264)
        │
  Remote Desktop  (GNOME on :10, uinput)
```

## Files

| File | Purpose |
|---|---|
| `headless_client.py` | Async WebSocket client — video decode, input, ring buffer |
| `mcp_server.py` | FastMCP server — 14 MCP tools |
| `keymap.py` | Qt key codes + combo parser (`ctrl+c`, `Return`, etc.) |
| `__init__.py` | Package marker |

## MCP Tools

### Connection
| Tool | Description |
|---|---|
| `connect` | Connect to a Teraguchi server (host, port, user, pass) |
| `disconnect` | Disconnect |
| `connection_status` | Returns connected state + screen size |

### Vision
| Tool | Description |
|---|---|
| `screenshot` | Latest frame as base64 PNG |
| `get_screen_size` | Returns `(width, height)` of remote display |

### Mouse
| Tool | Description |
|---|---|
| `click` | Left/right/middle click at `(x, y)` |
| `double_click` | Double-click at `(x, y)` |
| `move_mouse` | Move without clicking |
| `scroll` | Scroll wheel at `(x, y)`, positive = up |

### Keyboard
| Tool | Description |
|---|---|
| `type_text` | Type a string character by character |
| `key` | Press a key combo: `"ctrl+c"`, `"Return"`, `"alt+F4"` |

### Stream watching
| Tool | Description |
|---|---|
| `watch` | Collect N frames over T ms — returns list of base64 PNGs |
| `wait_for_change` | Block until pixels change (diff threshold), return new frame |
| `frame_history` | Return last N frames from the 300-frame ring buffer |

## Auto-unlock

`HeadlessClient.connect()` detects GNOME lock screens by average frame
brightness (< 80 = dark lock screen) and automatically runs
`loginctl unlock-session` via SSH. No manual intervention needed.

```python
c.connect(host, port, user, password,
          auto_unlock=True,   # default
          ssh_user="mihai",   # defaults to Teraguchi username
          ssh_key="")         # defaults to ~/.ssh/id_ed25519
```

## Quick start

```bash
# Install deps
cd /path/to/teragucci
python3.12 -m venv .venv-ai
.venv-ai/bin/pip install -r requirements-ai.txt

# Run MCP server standalone
TERAGUCHI_HOST=192.168.1.10 \
TERAGUCHI_USER=mihai \
TERAGUCHI_PASS=secret \
TERAGUCHI_TLS=1 \
.venv-ai/bin/python -m ai.mcp_server
```

## Cursor mcp.json entry

```json
"teraguchi": {
  "command": "/path/to/teragucci/.venv-ai/bin/python",
  "args": ["-m", "ai.mcp_server"],
  "cwd": "/path/to/teragucci",
  "env": {
    "TERAGUCHI_HOST": "192.168.178.94",
    "TERAGUCHI_PORT": "4443",
    "TERAGUCHI_USER": "mihai",
    "TERAGUCHI_PASS": "mihai123",
    "TERAGUCHI_TLS": "1"
  }
}
```

## Stream watching example

```python
from ai.headless_client import HeadlessClient

c = HeadlessClient()
c.connect("192.168.178.94", 4443, "mihai", "secret")

# Wait for something to change on screen (e.g. a dialog opens)
frame_b64 = c.wait_for_change(timeout_ms=10000, diff_threshold=0.02)

# Sample 6 frames over 3 seconds
frames = c.watch_frames(duration_ms=3000, sample_every_ms=500)
```

## What makes this different from other MCP remote desktop tools

Most MCP desktop tools take one-shot screenshots. This client maintains a
**live H.264 video stream** from the server (NVENC-encoded) and keeps a
300-frame ring buffer. The `wait_for_change` and `watch` tools let the agent
react to visual events without polling — the agent can say "do X, then wait
until the screen changes" as a single operation.

## Requirements

```
websockets>=12.0
av>=12.0          # PyAV for H.264 decode
numpy>=1.24
Pillow>=10.0
fastmcp>=2.0
```

## Future improvements

- **AT-SPI integration**: use Linux accessibility tree for element-based clicks
  instead of pixel coordinates (no coordinate estimation needed)
- **ShowUI grounding**: local 2B VLA model that takes screenshot + text query
  and returns exact `[x, y]` coordinates of the target element
- **Diff streaming**: send only changed pixel regions to reduce bandwidth
- **Audio stream**: expose PCM audio as an MCP resource
