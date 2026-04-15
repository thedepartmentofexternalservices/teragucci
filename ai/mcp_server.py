"""
Teraguchi MCP Master Server

Exposes remote desktop control as MCP tools. Implements the Daedalus Lab
adapter-hub pattern: one master server, multiple protocol candidates.

Adapters (in priority order):
  teraguchi  WebSocket + H.264/NVENC + uinput + PAM   [MASTER, default]
  vnc        VNC/RFB via vncdotool                    [candidate]
  rdp        RDP via xfreerdp + Xvfb                  [candidate]
  local      Local display via mss + pynput            [candidate]

Tools exposed:
  Adapter management:
    list_adapters()                    show all adapters + capabilities
    use_adapter(name)                  switch active adapter
    connect(host, port, user, pass)    connect via active adapter
    disconnect()
    connection_status()

  Vision:
    screenshot()                       base64 PNG, current frame
    get_screen_size()
    watch(duration_ms, sample_every_ms)  sample frames over time
    wait_for_change(timeout_ms, sensitivity)
    frame_history(count)              ring buffer (teraguchi only)

  Mouse:
    click(x, y, button)               normalized 0.0-1.0
    double_click(x, y)
    move_mouse(x, y)
    scroll(x, y, direction, amount)
    drag(x1, y1, x2, y2, steps, duration_ms)

  Keyboard:
    type_text(text)
    key(combo)                         "ctrl+c", "alt+F4", "enter" etc.

  Extras (teraguchi adapter only, degrade gracefully on others):
    clipboard_get()
    clipboard_set(text)
    run_remote(command, timeout_sec)
    find_element(description, action)

Configure in ~/.cursor/mcp.json:
    "teraguchi": {
        "command": "/path/to/.venv-ai/bin/python",
        "args": ["-m", "ai.mcp_server"],
        "cwd": "/path/to/teragucci",
        "env": {
            "TERAGUCHI_HOST": "192.168.178.94",
            "TERAGUCHI_USER": "mihai",
            "TERAGUCHI_PASS": "mihai123"
        }
    }
"""

import json
import logging
import os
import sys
import time

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from fastmcp import FastMCP
from ai.adapters import registry

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

mcp = FastMCP("teraguchi")


# ── Adapter management ────────────────────────────────────────────


@mcp.tool()
def list_adapters() -> str:
    """
    List all registered remote desktop adapters with their capabilities and status.

    Returns a JSON array describing each adapter. The 'active' field marks
    the one currently in use. 'connected' shows if it has an open session.

    Adapters in order of capability:
      teraguchi  - MASTER: H.264/NVENC, uinput, streaming ring buffer, PAM
      vnc        - candidate: VNC/RFB, polling screenshots
      rdp        - candidate: RDP via xfreerdp on Linux/macOS
      local      - candidate: local display control (mss + pynput)
    """
    return json.dumps(registry.list(), indent=2)


@mcp.tool()
def use_adapter(name: str) -> str:
    """
    Switch to a different remote desktop adapter.

    The active adapter is used by all subsequent tools (screenshot, click, etc.)
    Switching does NOT disconnect — call disconnect() first if needed.

    Args:
        name: Adapter name. One of: "teraguchi", "vnc", "rdp", "local"

    Returns the adapter description and its capabilities.
    """
    try:
        adapter = registry.use(name)
        info = adapter.adapter_info()
        caps = ", ".join(info["capabilities"])
        return f"Active adapter: {name}\n{info['description']}\nCapabilities: {caps}"
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def connect(
    host: str = "",
    port: int = 0,
    username: str = "",
    password: str = "",
    use_tls: bool = True,
) -> str:
    """
    Connect to a remote desktop using the active adapter.

    For teraguchi (default): WebSocket to port 4443, TLS by default.
    For vnc: RFB to port 5900. use_tls is ignored.
    For rdp: RDP to port 3389. use_tls is ignored.
    For local: no host needed -- controls the local machine.

    Args:
        host:     Remote IP or hostname. Falls back to TERAGUCHI_HOST env var.
        port:     Port. 0 = adapter default (4443/5900/3389).
        username: Login user. Falls back to TERAGUCHI_USER env var.
        password: Password. Falls back to TERAGUCHI_PASS env var.
        use_tls:  Use TLS (teraguchi only, default True).

    Returns a status message with screen resolution, or an error.
    """
    adapter = registry.active
    h = host or os.environ.get("TERAGUCHI_HOST", "")
    u = username or os.environ.get("TERAGUCHI_USER", "")
    pw = password or os.environ.get("TERAGUCHI_PASS", "")

    defaults = {"teraguchi": 4443, "vnc": 5900, "rdp": 3389, "local": 0}
    p = port or int(os.environ.get("TERAGUCHI_PORT", str(defaults.get(adapter.name, 4443))))

    if adapter.name == "local":
        h = h or "localhost"
        u = u or ""
        pw = pw or ""
    else:
        if not h:
            return "Error: host is required (pass as argument or set TERAGUCHI_HOST)"
        if not u and adapter.name in ("teraguchi", "rdp"):
            return "Error: username is required (pass as argument or set TERAGUCHI_USER)"

    if adapter.connected:
        adapter.disconnect()

    try:
        kwargs = {"use_tls": use_tls} if adapter.name == "teraguchi" else {}
        adapter.connect(h, p, u, pw, **kwargs)
    except Exception as e:
        return f"Connection failed ({adapter.name}): {e}"

    w, h_res = adapter.screen_size
    return f"Connected via {adapter.name} to {h}:{p} -- screen {w}x{h_res}"


@mcp.tool()
def disconnect() -> str:
    """Disconnect the active adapter from its current session."""
    adapter = registry.active
    if not adapter.connected:
        return f"Not connected (adapter: {adapter.name})"
    adapter.disconnect()
    return f"Disconnected ({adapter.name})"


@mcp.tool()
def connection_status() -> str:
    """Return current connection status, active adapter, and screen resolution."""
    adapter = registry.active
    if not adapter.connected:
        return f"Not connected (adapter: {adapter.name})"
    w, h = adapter.screen_size
    return f"Connected via {adapter.name} -- screen {w}x{h}"


# ── Vision ────────────────────────────────────────────────────────


@mcp.tool()
def screenshot() -> str:
    """
    Capture the current remote desktop screen as a base64-encoded PNG.

    Waits up to 5 seconds for the first frame if just connected.
    Returns an error string if not connected or no frame received.
    """
    adapter = registry.active
    if not adapter.connected:
        return f"Error: not connected -- call connect() first"

    for _ in range(50):
        data = adapter.screenshot()
        if data:
            return data
        time.sleep(0.1)

    return "Error: no frame received yet -- server may still be starting the session"


@mcp.tool()
def get_screen_size() -> dict:
    """Return the remote screen resolution as {width, height}."""
    adapter = registry.active
    if not adapter.connected:
        return {"error": "not connected"}
    w, h = adapter.screen_size
    return {"width": w, "height": h}


@mcp.tool()
def watch(duration_ms: int = 2000, sample_every_ms: int = 500) -> str:
    """
    Observe the remote screen over time and return a sequence of frames.

    Result: JSON array of {ts: unix_ms, frame: base64_png}.
    Use to watch animations, verify UI transitions, or track changes.

    Args:
        duration_ms:     Observation window in ms (default 2000).
        sample_every_ms: Interval between frames (default 500ms = 2fps).
                         Set to 100 for ~10fps, 33 for ~30fps.

    For the teraguchi adapter this reads directly from the ring buffer
    (zero overhead). Other adapters use polling (each sample = 1 screenshot).
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    frames = adapter.watch_frames(duration_ms=duration_ms, sample_every_ms=sample_every_ms)
    return json.dumps([{"ts": ts, "frame": b64} for ts, b64 in frames])


@mcp.tool()
def wait_for_change(timeout_ms: int = 10_000, sensitivity: float = 0.02) -> str:
    """
    Block until the screen changes, then return the new frame.

    More efficient than polling screenshot() in a loop.

    Args:
        timeout_ms:  Max wait in ms (default 10000).
        sensitivity: Fraction of pixels that must change (0.0-1.0):
                     0.005 -- cursor blink / clock tick
                     0.02  -- button click feedback, small popup (default)
                     0.05  -- window open/close
                     0.20  -- full scene change, new app launched

    Returns JSON: {ts, diff, frame} or timeout error.
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    result = adapter.wait_for_change(timeout_ms=timeout_ms, sensitivity=sensitivity)
    if result is None:
        return f"Timeout: no change detected in {timeout_ms}ms (sensitivity={sensitivity})"
    ts, b64, diff = result
    return json.dumps({"ts": ts, "diff": round(diff, 4), "frame": b64})


@mcp.tool()
def frame_history(count: int = 5) -> str:
    """
    Return the last N frames from the live ring buffer (teraguchi adapter only).

    Ring buffer holds up to 300 frames. Other adapters return 1 current frame.

    Args:
        count: Number of most recent frames (default 5, max 300).

    Returns JSON array: [{ts, frame}, ...].
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    frames = adapter.latest_frame_history(count=min(count, 300))
    return json.dumps([{"ts": ts, "frame": b64} for ts, b64 in frames])


# ── Mouse ─────────────────────────────────────────────────────────


@mcp.tool()
def click(x: float, y: float, button: str = "left") -> str:
    """
    Click at a normalized position on the remote screen.

    Args:
        x:      Horizontal 0.0 (left) to 1.0 (right).
        y:      Vertical 0.0 (top) to 1.0 (bottom).
        button: "left" (default), "right", "middle".
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    btn = {"left": 1, "middle": 2, "right": 3}.get(button.lower(), 1)
    adapter.click(x, y, button=btn)
    return "ok"


@mcp.tool()
def double_click(x: float, y: float) -> str:
    """Double-click at a normalized position (0.0-1.0)."""
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    adapter.click(x, y, button=1, double=True)
    return "ok"


@mcp.tool()
def move_mouse(x: float, y: float) -> str:
    """Move mouse to normalized position without clicking."""
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    adapter.move_mouse(x, y)
    return "ok"


@mcp.tool()
def scroll(x: float, y: float, direction: str = "down", amount: int = 3) -> str:
    """
    Scroll at a normalized position.

    Args:
        x, y:      Position 0.0-1.0.
        direction: "up", "down", "left", "right".
        amount:    Scroll steps (default 3).
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    dx, dy = 0, 0
    if direction == "up":
        dy = -amount
    elif direction == "down":
        dy = amount
    elif direction == "left":
        dx = -amount
    elif direction == "right":
        dx = amount
    adapter.scroll(x, y, dx=dx, dy=dy)
    return "ok"


@mcp.tool()
def drag(x1: float, y1: float, x2: float, y2: float,
         steps: int = 20, duration_ms: int = 300) -> str:
    """
    Click-drag from (x1,y1) to (x2,y2), both normalized 0.0-1.0.

    Args:
        steps:       Mouse-move steps for smoothness (default 20).
        duration_ms: Total drag time in ms (default 300).
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    adapter.drag(x1, y1, x2, y2, steps=steps, duration_ms=duration_ms)
    return f"ok -- dragged ({x1},{y1}) -> ({x2},{y2})"


# ── Keyboard ──────────────────────────────────────────────────────


@mcp.tool()
def type_text(text: str) -> str:
    """
    Type a string of text on the remote desktop.

    Supports printable ASCII, newline (\\n), tab (\\t).
    For special keys or combos use key() instead.
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    adapter.type_text(text)
    return f"ok -- typed {len(text)} characters"


@mcp.tool()
def key(combo: str) -> str:
    """
    Press a key or key combination.

    Examples:
        "enter", "escape", "tab", "backspace", "delete"
        "ctrl+c", "ctrl+v", "ctrl+z", "ctrl+a"
        "ctrl+shift+t", "alt+F4"
        "F1" through "F12"
        "left", "right", "up", "down", "home", "end", "pageup", "pagedown"
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    try:
        adapter.press_combo(combo)
        return f"ok -- pressed {combo!r}"
    except ValueError as e:
        return f"Error: {e}"


# ── Audio ─────────────────────────────────────────────────────────


@mcp.tool()
def audio_info() -> str:
    """
    Return information about the remote audio stream.

    Shows codec, sample rate, channels, how many frames have been received,
    and how many milliseconds of audio are currently buffered.

    Works with teraguchi adapter only (the server streams PCM/Opus audio
    alongside video). Other adapters return 'not supported'.

    Returns a JSON object:
        {
          "codec": "PCM" | "OPUS" | "none",
          "sample_rate": 48000,
          "channels": 2,
          "frames_received": 1234,
          "buffered_chunks": 150,
          "buffered_ms": 3000,
          "receiving": true
        }
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    if hasattr(adapter, "audio_info"):
        return json.dumps(adapter.audio_info(), indent=2)
    return f"Not supported by {adapter.name} adapter (teraguchi only)"


@mcp.tool()
def listen(duration_ms: int = 3000) -> str:
    """
    Record audio from the remote desktop session.

    Captures *duration_ms* milliseconds of the server-side audio stream
    (system audio — whatever is playing on the remote machine) and returns
    it as a base64-encoded WAV file (PCM s16le, 48 kHz, stereo).

    Works with teraguchi adapter only.

    Args:
        duration_ms: How long to record in milliseconds (default 3000 = 3s).
                     Maximum useful value is 30000 (30s) — older audio is
                     discarded from the ring buffer.

    Returns a JSON object:
        {"duration_ms": 3000, "wav_b64": "<base64>", "size_bytes": 288044}
    or an error string.

    To transcribe, pipe the WAV through a local Whisper call:
        import base64, subprocess, json, tempfile, pathlib
        r = json.loads(listen(5000))
        wav = base64.b64decode(r["wav_b64"])
        tmp = pathlib.Path("/tmp/remote_audio.wav")
        tmp.write_bytes(wav)
        subprocess.run(["whisper", str(tmp), "--model", "base"])
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    if not hasattr(adapter, "listen_audio"):
        return f"Not supported by {adapter.name} adapter (teraguchi only)"

    import base64
    wav = adapter.listen_audio(duration_ms=duration_ms)
    if wav is None:
        info = adapter.audio_info() if hasattr(adapter, "audio_info") else {}
        received = info.get("frames_received", 0) if isinstance(info, dict) else 0
        if received == 0:
            return (
                "No audio received from server. "
                "Check that the remote machine has PulseAudio/PipeWire running "
                "and that the Teraguchi server was started with audio enabled."
            )
        return "No audio captured in the requested duration"

    b64 = base64.b64encode(wav).decode()
    return json.dumps({
        "duration_ms": duration_ms,
        "wav_b64": b64,
        "size_bytes": len(wav),
        "format": "PCM s16le 48000 Hz stereo WAV",
    })


# ── Extras (teraguchi adapter; degrade gracefully on others) ──────


@mcp.tool()
def clipboard_get() -> str:
    """
    Return the remote clipboard text content.

    Works with teraguchi adapter (via SSH + xclip).
    Other adapters return 'not supported'.
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    if hasattr(adapter, "clipboard_get"):
        return adapter.clipboard_get()
    return f"Not supported by {adapter.name} adapter"


@mcp.tool()
def clipboard_set(text: str) -> str:
    """
    Set the remote clipboard to the given text.

    Works with teraguchi adapter (via SSH + xclip).
    Other adapters return 'not supported'.

    Args:
        text: Text to put on the clipboard.
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    if hasattr(adapter, "clipboard_set"):
        return adapter.clipboard_set(text)
    return f"Not supported by {adapter.name} adapter"


@mcp.tool()
def run_remote(command: str, timeout_sec: int = 30) -> str:
    """
    Run a shell command on the remote machine via SSH and return output.

    Works with teraguchi adapter only (SSH passthrough).
    Examples:
      run_remote("nvidia-smi")
      run_remote("ps aux | grep UnrealEditor")
      run_remote("tail -50 /tmp/ue.log")

    Args:
        command:     Shell command string.
        timeout_sec: Max wait in seconds (default 30).
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"
    if hasattr(adapter, "run_remote"):
        return adapter.run_remote(command, timeout_sec=timeout_sec)
    return f"Not supported by {adapter.name} adapter (requires SSH)"


@mcp.tool()
def find_element(description: str, action: str = "click") -> str:
    """
    Find a UI element by name using the Linux AT-SPI accessibility tree.

    Works with teraguchi adapter (SSH + pyatspi on remote machine).
    More reliable than coordinate clicks: works regardless of window position.

    Args:
        description: Text to match -- button label, menu item, field name.
                     Case-insensitive substring.
        action:      "click" (default) or "info" (return position only).
    """
    adapter = registry.active
    if not adapter.connected:
        return "Error: not connected"

    if not hasattr(adapter, "run_remote"):
        return f"Not supported by {adapter.name} adapter (requires SSH + pyatspi)"

    script = f"""
import pyatspi, json, sys
query = {repr(description.lower())}
results = []

def scan(node, depth=0):
    if depth > 15:
        return
    try:
        name = (node.name or "").lower()
        role = pyatspi.roleName(node.getRole())
        if query in name or query in role:
            try:
                ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                results.append({{"name": node.name, "role": role,
                                  "x": ext.x, "y": ext.y, "w": ext.width, "h": ext.height}})
            except:
                results.append({{"name": node.name, "role": role, "x": -1, "y": -1}})
        for child in node:
            scan(child, depth+1)
    except:
        pass

desktop = pyatspi.Registry.getDesktop(0)
for app in desktop:
    scan(app)
print(json.dumps(results[:10]))
"""

    import json as _json
    raw = adapter.run_remote(
        f"DISPLAY=:10 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
        f"python3 -c {repr(script)}",
        timeout_sec=15,
    )

    try:
        elements = _json.loads(raw.strip())
    except Exception:
        return f"Parse error: {raw[:200]}"

    if not elements:
        return f"No element found matching {description!r}"

    best = elements[0]
    info = f"Found: {best['role']} {best['name']!r} at ({best['x']}, {best['y']})"

    if action == "click" and best["x"] >= 0:
        cx = best["x"] + best.get("w", 10) // 2
        cy = best["y"] + best.get("h", 10) // 2
        w, h = adapter.screen_size
        adapter.click(cx / w, cy / h)
        return f"ok -- {info} -- clicked at ({cx}, {cy})"

    return f"ok -- {info} -- {_json.dumps(elements)}"


# ── Entry point ───────────────────────────────────────────────────


def main():
    host = os.environ.get("TERAGUCHI_HOST", "")
    user = os.environ.get("TERAGUCHI_USER", "")
    pw   = os.environ.get("TERAGUCHI_PASS", "")
    port = int(os.environ.get("TERAGUCHI_PORT", "4443"))
    tls  = os.environ.get("TERAGUCHI_TLS", "1") != "0"

    if host and user and pw:
        try:
            adapter = registry.active  # teraguchi by default
            adapter.connect(host, port, user, pw, use_tls=tls, timeout=15.0)
            w, h = adapter.screen_size
            logger.warning("Pre-connected via %s to %s:%d -- %dx%d", adapter.name, host, port, w, h)
        except Exception as e:
            logger.warning("Pre-connect failed: %s -- use connect() tool", e)

    mcp.run()


if __name__ == "__main__":
    main()
