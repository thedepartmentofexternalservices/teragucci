# Phase 3: Display + Multi-Monitor + Clipboard — Pattern Map

**Mapped:** 2026-04-19
**Files analyzed:** 28 (15 existing extends + 13 new files)
**Analogs found:** 28 / 28 (Phase 3 is pure extension — every concept maps to existing code)

---

## Project Conventions Snapshot (load-bearing across all plans)

Every new file in Phase 3 MUST follow these existing-codebase conventions (sourced from `.planning/codebase/CONVENTIONS.md` + spot-checked against the files):

- **Module docstring:** triple-quoted summary + design notes at top of every file (see `server/clipboard.py:1-7` or `server/mac_clipboard.py:1-13` as canonical clipboard-flavor examples).
- **Logger:** stdlib `logger = logging.getLogger(__name__)` at module top (the universal pattern). For new server-side hot paths needing structured telemetry, prefer `from common.logging import get_logger; logger = get_logger("server.<feature>")` per `server/monitor_hotplug.py:27` and `server/encoder_lifecycle.py:38`.
- **Naming:** `snake_case.py` files, `PascalCase` classes, `_leading_underscore` for private members. Mac variants prefix `mac_` (e.g. `mac_clipboard_image.py` if a separate file is needed); Linux is the un-prefixed default.
- **Type hints:** annotate public function signatures (`def foo(x: int, y: str) -> bool:`); pre-PEP-604 style (`Optional[X]`, `List[Y]`) is the existing convention.
- **Optional imports:** wrap PyObjC / Xlib / hardware imports in `try/except` with a `_HAS_<FRAMEWORK>` flag and gate runtime usage on the flag (canonical example: `server/mac_clipboard.py:22-30` `_HAS_APPKIT`).
- **Polling thread:** new background pollers use `threading.Thread(target=..., daemon=True, name="<feature>-monitor")` with a `_running` flag and a `stop()` that joins with timeout (canonical: `server/clipboard.py:101-126`).
- **Async-side broadcast:** when a polling thread or callback needs to push to clients, use `asyncio.run_coroutine_threadsafe(cs.enqueue(msg_json), self._event_loop)` per `server/session_runtime.py:540-541`.
- **Error handling:** broad `except Exception:` for best-effort optional paths with `logger.debug/warning(...)`. Custom exceptions inherit from `RuntimeError` and end in `Error` (canonical: `MacScreenCaptureError`, `MacInputInjectorError`).
- **Bookmark migration:** add new fields with safe defaults; migration runs inside `BookmarkManager._load()` (canonical: `client/bookmarks.py:112-135` Phase 2 D-10 swap_cmd_ctrl migration).
- **Tests:** `pytest`, `@pytest.mark.asyncio` for coroutines, `monkeypatch` to stub OS boundaries, fixtures from `tests/conftest.py` (`fake_ws`, `fake_encoder`, etc.), and TLS fixtures from `tests/integration/conftest.py` (`tls_ca_and_cert`, `free_port`). Round-trip tests live in `tests/common/test_messages_phase2.py` style.

---

## File Classification

### Server-side extensions

| File | Role | Data Flow | Closest Analog | Match Quality |
|------|------|-----------|----------------|---------------|
| `server/screen_capture.py` (extend) | capture / platform-backend | streaming + crop | `server/screen_capture.py` itself (existing list_monitors/detect_hotplug) | exact (self-extend) |
| `server/mac_screen_capture.py` (extend) | capture / platform-backend | streaming + push-callback | `server/mac_screen_capture.py::_StreamOutputHandler` (existing SCK delegate) | exact (self-extend) |
| `server/monitor_hotplug.py` (extend) | service / event-driven | poll + broadcast | `server/monitor_hotplug.py` itself | exact (self-extend) |
| `server/session_manager.py` (extend EDID) | config / file-I/O | one-shot generator | `server/session_manager.py::_generate_edid` lines 143-262 | exact (self-extend) |
| `server/clipboard.py` (extend with PNG) | service / pull-poll + push | poll + subprocess | `server/clipboard.py` (existing xclip text path) | exact (self-extend) |
| `server/mac_clipboard.py` (extend with PNG) | service / pull-poll + push | poll + NSPasteboard | `server/mac_clipboard.py` (existing string path) | exact (self-extend) |
| `server/session_runtime.py` (extend toggles + capture-mode) | session / request-response | dispatch | `server/session_runtime.py::_on_clipboard_change` line 537-541 | exact (self-extend) |

### Client-side extensions

| File | Role | Data Flow | Closest Analog | Match Quality |
|------|------|-----------|----------------|---------------|
| `client/viewer.py::_widget_to_remote` (rewrite) | utility / transform | per-pointer-event math | `client/viewer.py::_widget_to_remote` lines 655-694 | exact (self-rewrite) |
| `client/viewer.py::tabletEvent / mouse* / key*` (extend coords) | event handler | request-response | `client/viewer.py::tabletEvent` lines 761-841 | exact (self-extend) |
| `client/viewer.py::keyPressEvent` (F12 hook) | event handler | request-response | `client/viewer.py::keyPressEvent` lines 901-917 + `client/key_diagnostic.py` for overlay shape | exact (self-extend) |
| `client/bookmarks.py` (extend with 6 new fields) | model / file-I/O | CRUD + migration | `client/bookmarks.py::_load` migration block lines 112-135 (Phase 2 D-10) | exact (self-extend) |
| `client/protocol.py::send_clipboard` (extend image + chunked) | transport / request-response | send + receive | `client/protocol.py::send_clipboard` line 315-317 + `_handle_text` clipboard branch line 956-958 | exact (self-extend) |
| `client/main_window.py::ConnectionDialog` (extend mode picker) | UI / dialog | one-shot config | `client/main_window.py::ConnectionDialog` lines 51-208 (Phase 2 D-10 destination-kind combo) | exact (self-extend) |
| `client/fullscreen_toolbar.py` (extend mode badge + clipboard btn) | UI / toolbar | request-response | `client/fullscreen_toolbar.py::_build_ui` lines 63-128 | exact (self-extend) |
| `client/monitor_selector.py` (extend with radio mode) | UI / widget | event-driven | `client/monitor_selector.py` lines 1-126 | exact (self-extend) |
| `client/icons.py::icon_clipboard` (new entry) | UI / asset | static | `client/icons.py::icon_keyboard` lines 102-112 | exact |
| `client/session.py` (wire toggles + mode) | session / request-response | dispatch | `client/session.py::_wire_viewer` lines 259-274 | exact (self-extend) |

### Common-protocol extensions

| File | Role | Data Flow | Closest Analog | Match Quality |
|------|------|-----------|----------------|---------------|
| `common/messages.py` (server_x/y on input msgs) | model / dataclass | wire-shape | `common/messages.py::KeyEventMsg` lines 534-563 (Phase 2 D-14 backward-compat extension) | exact |
| `common/messages.py::ClipboardChunkMsg` (new) | model / dataclass | wire-shape | `common/messages.py::ClipboardMsg` lines 443-450 + `KeyResetModifiersMsg` lines 591-609 (D-11 add-new-message pattern) | exact |
| `common/messages.py::SessionConfigureMsg` (new — or ClientHelloMsg extension) | model / dataclass | wire-shape | `common/messages.py::ClientHelloMsg` lines 457-473 | exact |
| `common/messages.py::MonitorListMsg` (extend with physical px geometry) | model / dataclass | wire-shape | `common/messages.py::MonitorInfo` + `MonitorListMsg` lines 409-427 | exact |

### New client UI files

| File | Role | Data Flow | Closest Analog | Match Quality |
|------|------|-----------|----------------|---------------|
| `client/connect_dialog.py` (new — or `ModeSelector` inside `main_window.py`) | UI / composite widget | event-driven | `client/main_window.py::ConnectionDialog` lines 51-208 + `client/monitor_selector.py` (composition target) | exact |
| `client/coord_debug_overlay.py` (new — F12 dev overlay) | UI / overlay | per-pointer-event paint | `client/key_diagnostic.py` (pattern for dev-only diagnostic widget) + `client/health_display.py` (overlay aesthetic) | role-match |
| `client/remap_banner.py` (new — non-modal topology-change banner) | UI / banner | event-driven slide animation | `client/fullscreen_toolbar.py` lines 28-189 (slide animation + auto-hide pattern) | role-match |
| `client/toasts.py` (new — InfoToast for D-14 + D-09 fallback) | UI / transient notification | timer-driven | `client/fullscreen_toolbar.py` lines 41-50 (QPropertyAnimation + QTimer pattern) | role-match |
| `client/clipboard_toggle_menu.py` (new — 4-checkbox QMenu) | UI / toolbar dropdown | event-driven | `client/monitor_selector.py` lines 16-126 (QToolButton + QMenu + checkable QAction pattern) | exact |

### Test files (all NEW)

All test files follow `pytest` + `monkeypatch` conventions per `.planning/codebase/CONVENTIONS.md` and the existing layout (`tests/common/`, `tests/server/`, `tests/integration/`, `tests/client/`).

| File | Layer | Closest Analog | Match Quality |
|------|-------|----------------|---------------|
| `tests/common/test_cursor_math.py` | unit (pure-math) | `tests/common/test_messages.py` round-trip pattern | exact |
| `tests/common/test_clipboard_chunking.py` | unit (assembler) | `tests/common/test_messages_phase2.py` | exact |
| `tests/server/test_screen_capture_hotplug.py` | unit (mocked OS boundary) | `tests/server/test_health_monitor.py` (deque pattern) + `tests/server/test_pipelines.py` (`_FakeRuntime` pattern) | role-match |
| `tests/server/test_mac_screen_capture_hotplug.py` | unit (mocked SCK boundary) | `tests/server/test_mac_video_encoder.py` + `tests/server/test_mac_pen_injector.py` | role-match |
| `tests/server/test_clipboard_image.py` (Linux PNG path) | unit (mocked subprocess boundary) | `tests/server/test_health_monitor.py` (no-OS-boundary unit pattern) | role-match |
| `tests/server/test_mac_clipboard_image.py` | unit (mocked AppKit boundary) | `tests/server/test_mac_pen_injector.py` (mock-at-IOKit-boundary) | role-match |
| `tests/server/test_clipboard_toggles.py` | unit (policy gating) | `tests/server/test_modifier_dispatch.py` (D-10 swap policy gating) | role-match |
| `tests/server/test_session_manager_edid.py` | unit (file-I/O round-trip) | `tests/server/test_auth.py` (file-roundtrip pattern) | role-match |
| `tests/integration/test_monitor_hotplug.py` | integration (in-process loopback) | `tests/integration/test_reconnect.py` lines 36-99 | exact |
| `tests/integration/test_capture_mode.py` | integration (capture-mode negotiation) | `tests/integration/test_auth_flow.py` (handshake-flow pattern) | role-match |
| `tests/integration/test_clipboard_text_large.py` | integration (>1 MB text round-trip) | `tests/integration/test_reconnect.py` (loopback pattern) | role-match |
| `tests/integration/test_clipboard_image.py` | integration (PNG round-trip) | `tests/integration/test_reconnect.py` (loopback pattern) | role-match |
| `tests/client/test_viewer_dpr.py` | unit (Qt widget mock) | `tests/client/test_viewer_modifier_triggers.py` + `tests/client/test_viewer_proximity.py` | role-match |
| `tests/client/test_viewer_screen_changed.py` | unit (Qt signal mock) | `tests/client/test_viewer_modifier_triggers.py` | role-match |
| `tests/client/test_clipboard_toggle_menu.py` | unit (Qt widget) | `tests/client/test_health_display.py` (Qt widget unit-test pattern) | role-match |
| `tests/client/test_connect_dialog_mode.py` | unit (Qt dialog) | `tests/client/test_bookmarks.py` (settings round-trip) + `tests/client/test_health_display.py` | role-match |
| `tests/client/test_fullscreen_toolbar_mode_badge.py` | unit (Qt widget) | `tests/client/test_health_display.py` | role-match |

---

## Pattern Assignments

### `common/messages.py` — Wire-shape extensions (D-02, D-05, D-13, D-17)

**Analog:** itself — Phase 2 D-14 added new fields to `KeyEventMsg` while preserving Phase 1 wire compat. Phase 2 D-11 added an entirely new `KeyResetModifiersMsg` class. Both patterns are templates Phase 3 should follow verbatim.

**Existing dataclass shape (lines 534-563):**

```python
@dataclass
class KeyEventMsg:
    """Keyboard key event (D-14 — promoted from dict to dataclass).

    Phase 1 wire shape (``{"type": "key_event", "scan_code": N,
    "pressed": bool}``) remains compatible — the three new lock-state
    fields default to False so the parsed dict on the server side
    behaves identically when older clients omit them.
    """
    type: str = MsgType.KEY_EVENT
    scan_code: int = 0
    pressed: bool = False
    # Phase 2 additions (D-14) — lock-state bits sent on every KeyEvent.
    caps_lock_on: bool = False
    num_lock_on: bool = False
    scroll_lock_on: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self))
```

**Existing new-message shape (lines 591-609):**

```python
@dataclass
class KeyResetModifiersMsg:
    """Client → Server: release all held modifiers on the server side (D-11)."""
    type: str = MsgType.KEY_RESET_MODIFIERS
    reason: str = "unknown"

    def to_json(self) -> str:
        return json.dumps(asdict(self))
```

**Existing MsgType registration (lines 120-150):**

```python
class MsgType:
    # --- Input (Client → Server) ---
    MOUSE_MOVE = "mouse_move"
    ...
    # Phase 2 additions (D-11, D-15, D-19):
    KEY_RESET_MODIFIERS = "key_reset_modifiers"
    TEXT_COMMIT = "text_commit"
    PEN_PROXIMITY = "pen_proximity"
```

**Phase 3 must:**

1. Add `MsgType.CLIPBOARD_CHUNK = "clipboard_chunk"` and `MsgType.SESSION_CONFIGURE = "session_configure"` (planner picks names) under a `# Phase 3 additions (D-02, D-17):` comment.
2. Extend `MouseMoveMsg`, `MouseButtonMsg`, `MouseScrollMsg`, `KeyEventMsg`, `PenEventMsg` with `server_x: int = -1` + `server_y: int = -1` fields. Default `-1` is the "client did not compute physical px" sentinel — server falls back to the existing normalized fields when present.
3. Add `ClipboardChunkMsg` mirroring `KeyResetModifiersMsg` shape: `{type, sequence_id: int, chunk_index: int, total_chunks: int, content_type: str, data: str}`.
4. Extend `MonitorInfo` (lines 409-418) with `physical_width: int = 0`, `physical_height: int = 0`, `dpi: float = 96.0` fields if needed by D-05 client math (planner decides — current `width`/`height`/`scale` may suffice).
5. Either extend `ClientHelloMsg` (lines 457-473) with `capture_mode: str = "mirror_all"` + `picked_monitor_id: int = -1` + `picked_monitor_name: str = ""` (D-02), OR add a new `SessionConfigureMsg`. Both are within the canonical-refs allowance.

**Do not deviate:**
- All new fields default to a value that makes pre-Phase-3 servers/clients behave identically (Phase 2 D-14 backward-compat discipline).
- `to_json()` body is exactly `return json.dumps(asdict(self))` — never deviate from the one-line idiom.
- Test in `tests/common/test_messages_phase2.py` style (see Tests section below).

**Test analog (`tests/common/test_messages_phase2.py:64-85`):**

```python
def test_key_event_extended_with_caps_num_scroll_lock_bits():
    from common.messages import KeyEventMsg, MsgType, parse_message
    msg = KeyEventMsg(
        scan_code=30, pressed=True,
        caps_lock_on=True, num_lock_on=False, scroll_lock_on=False,
    )
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.KEY_EVENT
    assert parsed["scan_code"] == 30
    assert parsed["pressed"] is True
    assert parsed["caps_lock_on"] is True
```

---

### `server/screen_capture.py` — GPU crop + full hot-plug signature (D-02, D-09)

**Analog:** itself — `list_monitors` already produces `MonitorInfo` with full geometry; `detect_hotplug` exists but has shallow signature.

**Existing `list_monitors` (lines 383-417):**

```python
def list_monitors(self) -> List[MonitorInfo]:
    """Enumerate all available monitors with detailed info."""
    monitors = []
    xrandr_by_idx = {}
    for xi, xinfo in enumerate(self._xrandr_info):
        xrandr_by_idx[xi] = xinfo

    for i, mon in enumerate(self._sct.monitors):
        if i == 0:
            name = "All Monitors (Virtual Desktop)"
            primary = False
            ...
        else:
            xinfo = xrandr_by_idx.get(i - 1, {})
            name = xinfo.get("name", f"Monitor {i}")
            primary = xinfo.get("primary", (i == 1))
            ...
        monitors.append(MonitorInfo(
            id=i, name=name,
            width=mon["width"], height=mon["height"],
            x=mon.get("left", 0), y=mon.get("top", 0),
            primary=primary, scale=scale,
        ))
    return monitors
```

**Existing `detect_hotplug` (lines 419-444) — shallow signature target for D-09 expansion:**

```python
def detect_hotplug(self) -> bool:
    old_count = len(self._sct.monitors)
    try:
        new_sct = mss.mss()
        new_count = len(new_sct.monitors)
        if new_count != old_count:
            self._sct = new_sct
            self._refresh_monitor_info()
            logger.info("Monitor hotplug detected: %d -> %d monitors",
                       old_count, new_count)
            return True
        # Also check if resolutions changed
        for i, (old, new) in enumerate(zip(self._sct.monitors, new_sct.monitors)):
            if old["width"] != new["width"] or old["height"] != new["height"]:
                ...
                return True
    except Exception:
        pass
    return False
```

**Phase 3 must:**

1. Upgrade `detect_hotplug` to the `(displayID, width, height, x, y)` tuple signature so monitor-reorder + same-size swap + repositioning all trigger the change signal (parallels D-11 Mac upgrade).
2. Add `capture_raw_bgra_with_crop(crop: Optional[Tuple[int,int,int,int]]) -> bytes` (D-02). Capture stays full-virtual-desktop (preserves Phase 2 10-bit fixture); crop is a NumPy view + `.tobytes()` — see RESEARCH.md §"Pattern 1" sketch lines 280-289.
3. For 10-bit P010 path (NvFBC YUV420P10LE), crop must operate on Y and UV planes separately (UV is half-resolution both axes). Test fixture extension lives in `tests/server/test_pipelines.py`.

**Do not deviate:**
- Imports go through `from common.messages import MonitorInfo` (preserve the existing line 26 import).
- `logger.info("Monitor hotplug detected: ...")` lifecycle log line stays — Phase 1 OBS-01 + monitor-hotplug observability depends on it.
- All optional Xlib/NvFBC paths stay behind their `_HAS_*` flags (lines 36-53).

---

### `server/mac_screen_capture.py` — SCStreamDelegate hot-plug + full signature (D-11)

**Analog:** itself — the existing `_StreamOutputHandler` (lines 141-212) is the canonical SCK delegate pattern. The new display-change delegate follows the same `NSObject` subclass shape.

**Existing delegate pattern (lines 141-212):**

```python
if _HAS_SCK:

    class _StreamOutputHandler(NSObject):
        """SCStreamOutput delegate.

        SCK calls ``stream:didOutputSampleBuffer:ofType:`` on every frame.
        We latch the most recent frame's BGRA bytes into ``_latest_bgra``
        under ``_lock``. The capture class reads this buffer on demand.
        """

        def initWithCapture_(self, capture):
            self = objc.super(_StreamOutputHandler, self).init()
            if self is None:
                return None
            self._capture = capture
            return self

        # objc selector: stream:didOutputSampleBuffer:ofType:
        def stream_didOutputSampleBuffer_ofType_(
            self, stream, sample_buffer, output_type
        ):
            ...
            try:
                ...
                with self._capture._lock:
                    self._capture._latest_bgra = raw
                    ...
            except Exception as e:
                # Never let an exception cross back into Objective-C —
                # it will crash the SCK dispatch queue.
                logger.error("SCK frame handler error: %s", e, exc_info=True)
```

**Existing `detect_hotplug` (lines 568-580) — shallow signature, the D-11 upgrade target:**

```python
def detect_hotplug(self) -> bool:
    """Check if SCK reports a different number/size of displays."""
    try:
        old_sig = [(int(d.width()), int(d.height())) for d in self._displays]
        self._enumerate_displays()
        new_sig = [(int(d.width()), int(d.height())) for d in self._displays]
        if old_sig != new_sig:
            logger.info("Display hotplug detected: %s -> %s",
                        old_sig, new_sig)
            return True
    except Exception as e:
        logger.warning("detect_hotplug failed: %s", e)
    return False
```

**Phase 3 must:**

1. Upgrade signature tuple to `(int(d.displayID()), int(d.width()), int(d.height()), int(d.frame().origin.x), int(d.frame().origin.y))`. The existing `list_monitors` block at lines 540-566 already pulls displayID + frame; replicate that here.
2. Add a new `_DisplayChangeDelegate(NSObject)` class (mirror `_StreamOutputHandler` shape — `initWithCapture_` + an objc selector method) gated under `if _HAS_SCK:`. Subscribe to ScreenCaptureKit's display-change callback; the delegate sets `self._capture._hotplug_pending = True` under the existing `self._lock`.
3. `MonitorHotplug.run()` (in `server/monitor_hotplug.py`) checks the flag in addition to the 5s poll cadence so the handler fires faster.
4. Wrap the delegate `try/except` exactly like `_StreamOutputHandler` — `logger.error("SCK display-change handler error: %s", e, exc_info=True)` and never let an exception cross back into Objective-C.

**Do not deviate:**
- Stay behind the `if _HAS_SCK:` gate (line 139). Non-Mac CI must not pull SCK imports.
- The `MacScreenCaptureError` (line 126-127) raise pattern stays for any unrecoverable init path.
- Polling 5s safety net per CONTEXT D-11 — DO NOT remove the poll, only augment with push.

---

### `server/monitor_hotplug.py` — Auto-fallback to primary (D-09)

**Analog:** the existing `MonitorHotplug.run` loop is the canonical extension point.

**Existing loop (lines 39-53):**

```python
async def run(self) -> None:
    while self._running:
        await asyncio.sleep(5.0)
        runtime = self._runtime
        if runtime.capture and runtime.capture.detect_hotplug():
            monitors = [asdict(m) for m in runtime.capture.list_monitors()]
            msg_json = MonitorListMsg(monitors=monitors).to_json()
            for ws, cs in list(runtime.clients.items()):
                if cs.authenticated:
                    try:
                        await cs.enqueue(msg_json)
                    except Exception:
                        pass
            if runtime.encoder:
                runtime.encoder_lifecycle.restart()
```

**Phase 3 must:**

1. Inside the `if runtime.capture.detect_hotplug():` block, add fall-back logic: for each authenticated client whose session is in pick-one mode, check whether their `picked_monitor_id` (or name) is still present in `runtime.capture.list_monitors()`. If not, set the session's effective monitor to the primary + emit a structured-log + queue an "InfoToast" message (or piggyback the fallback flag inside the `MonitorListMsg`).
2. After the encoder restart (line 53), also reset the per-session crop rect on the capture pipeline so the new geometry takes effect on the next frame. The crop rect comes from the per-session pick (D-02).
3. Keep the `await asyncio.sleep(5.0)` poll as safety net per D-11. Add a fast-path early exit when `runtime.capture._hotplug_pending` is True (set by the SCK push delegate).
4. Convert the `logger` to `from common.logging import get_logger; logger = get_logger("server.monitor_hotplug")` if not already (it currently uses stdlib at line 27 — verify before touching).

**Do not deviate:**
- The `for ws, cs in list(runtime.clients.items()):` iteration pattern (line 46) protects against dict mutation during broadcast — don't switch to direct `runtime.clients.items()`.
- `runtime.encoder_lifecycle.restart()` is the only sanctioned encoder-restart entry point (per `server/encoder_lifecycle.py:1-25`). Do not call `_restart_encoder` directly.
- No `xrandr --mode` / `xrandr --addmode` / any X11 set-operation per D-10 (NVIDIA segfault guard). Read-only enumeration only.

---

### `server/clipboard.py` — PNG image clipboard (D-13, D-14, D-16)

**Analog:** itself — the existing text path lines 17-126 is the canonical "subprocess-driven X11 clipboard with poll + echo-suppression" pattern. The image path mirrors it byte-for-byte.

**Existing text path (lines 73-119):**

```python
def get_clipboard(self) -> str:
    if not self._tool:
        return ""
    try:
        if self._tool == "xclip":
            cmd = ["xclip", "-selection", "clipboard", "-o"]
        else:
            cmd = ["xsel", "--clipboard", "--output"]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=2, env=self._get_env())
        return result.stdout if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, Exception):
        return ""

def set_clipboard(self, text: str):
    if not self._tool:
        return
    try:
        if self._tool == "xclip":
            cmd = ["xclip", "-selection", "clipboard", "-i"]
        else:
            cmd = ["xsel", "--clipboard", "--input"]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, env=self._get_env())
        proc.communicate(input=text.encode("utf-8"), timeout=2)
        self._last_content = text
        logger.debug("Clipboard set: %d chars", len(text))
    except (subprocess.TimeoutExpired, Exception) as e:
        logger.error("Failed to set clipboard: %s", e)
```

**Phase 3 must:**

1. Add `get_clipboard_image() -> Optional[bytes]` using `xclip -selection clipboard -t image/png -o`; return raw PNG bytes or None on empty/error. Mirror the `try/except (subprocess.TimeoutExpired, Exception):` discipline.
2. Add `set_clipboard_image(png_bytes: bytes)` using `xclip -selection clipboard -t image/png -i`; pipe `png_bytes` into stdin via `proc.communicate(input=png_bytes, timeout=2)`. Cache `self._last_image_hash = hashlib.sha256(png_bytes).digest()` for echo-suppression (parallels `_last_content` for text).
3. Magic-byte validation on receive (D-16): `if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"): logger.warning(...); return` BEFORE invoking xclip. Validate inside `set_clipboard_image`, not at the protocol layer — defense-in-depth.
4. Size cap (D-14): `if len(png_bytes) > 64 * 1024 * 1024: logger.warning(...); return`. Both on send and receive.
5. Extend `_poll_loop` (lines 108-119) to also detect image changes. Two implementation options the planner picks: (a) poll xclip for available targets via `xclip -t TARGETS -o` and route by whether `image/png` is present, or (b) keep two parallel poll cadences. Option (a) keeps one poll thread (preserves existing pattern), preferred.
6. CRLF preservation (D-16): The current `text=True` flag in `subprocess.run` (line 67) auto-converts CRLF → LF. Switch to `text=False` + `result.stdout.decode("utf-8")` so the originating newline encoding survives the round-trip.

**Do not deviate:**
- Keep the `_last_content` echo-suppression cache pattern exactly. Phase 3 just adds a second cache for images.
- Keep the `_find_clipboard_tool()` xclip→xsel fallback (lines 42-52). xsel does not support image PNG, so image methods should `if self._tool != "xclip": return ""` (xsel-only hosts get text-only clipboard, log once at module init).
- `start_monitoring(on_change: Callable)` signature stays binary-compatible. The new image path either piggy-backs on the existing `on_change` callback (passing a tuple `(content_type, payload)`) or adds a parallel `on_image_change` callback. Planner picks; consistency with `mac_clipboard.py` is the gate.
- All new structlog/log lines use `event=clipboard_image` + `size_bytes=...` per D-18 telemetry budget tracking.

---

### `server/mac_clipboard.py` — NSPasteboardTypePNG image clipboard (D-13, D-14, D-16)

**Analog:** itself — the existing string path lines 71-99 is the canonical NSPasteboard pattern. PNG mirror follows.

**Existing text path (lines 71-99):**

```python
def get_clipboard(self) -> str:
    if self._pb is None:
        return ""
    try:
        s = self._pb.stringForType_(NSPasteboardTypeString)
        return str(s) if s is not None else ""
    except Exception as e:
        logger.debug("NSPasteboard read failed: %s", e)
        return ""

def set_clipboard(self, text: str):
    if self._pb is None:
        return
    try:
        self._pb.clearContents()
        ok = self._pb.setString_forType_(text, NSPasteboardTypeString)
        if not ok:
            logger.warning("NSPasteboard setString returned False")
            return
        self._last_content = text
        try:
            self._last_change_count = int(self._pb.changeCount())
        except Exception:
            pass
        logger.debug("Clipboard set: %d chars", len(text))
    except Exception as e:
        logger.error("Failed to set clipboard: %s", e)
```

**Phase 3 must:**

1. Extend the optional-import block (lines 22-30) to also pull `NSPasteboardTypePNG`:
   ```python
   try:
       from AppKit import (
           NSPasteboard,
           NSPasteboardTypeString,
           NSPasteboardTypePNG,  # Phase 3 D-13
       )
       _HAS_APPKIT = True
   ```
2. Add `get_clipboard_image() -> Optional[bytes]` using `self._pb.dataForType_(NSPasteboardTypePNG)` and converting the returned `NSData` via `bytes(data)` (PyObjC handles the bridge). Return `None` on empty.
3. Add `set_clipboard_image(png_bytes: bytes)` using `self._pb.clearContents()` + `self._pb.setData_forType_(NSData.dataWithBytes_length_(png_bytes, len(png_bytes)), NSPasteboardTypePNG)`. Bump `self._last_change_count` after write — same echo-suppression idiom as text path lines 91-95.
4. Magic-byte (D-16) and 64 MB cap (D-14) — identical to the Linux side, applied inside both `get_clipboard_image` and `set_clipboard_image`.
5. Extend `_poll_loop` (lines 118-136) to also check NSPasteboardTypePNG availability after each `changeCount` increment. The `changeCount` change is the trigger; type check determines text-vs-image dispatch.

**Do not deviate:**
- `_HAS_APPKIT` import gate (lines 22-30) is the canonical PyObjC-optional pattern. Don't bypass.
- The 250 ms `poll_interval` default (line 43) stays — Phase 3 does NOT change polling cadence.
- `_last_change_count` bump after self-write (lines 92-96) prevents echo loops; preserve for the image path.
- Class is `MacClipboardSync`. The shape mirrors Linux `ClipboardSync` per the docstring contract (lines 36-40); any new methods MUST appear on both classes with the same signature.

---

### `server/session_runtime.py` — Per-direction clipboard toggle gating + capture-mode (D-02, D-15)

**Analog:** itself — `_on_clipboard_change` (line 537-541) is the broadcast site to gate.

**Existing broadcast (lines 537-541):**

```python
def _on_clipboard_change(self, text: str):
    msg_json = ClipboardMsg(type=MsgType.CLIPBOARD_RECV, data=text).to_json()
    for ws, cs in list(self.clients.items()):
        if cs.authenticated and self._event_loop:
            asyncio.run_coroutine_threadsafe(cs.enqueue(msg_json), self._event_loop)
```

**Existing inbound dispatch (lines 788-790):**

```python
elif msg_type == MsgType.CLIPBOARD_SEND:
    if self.clipboard:
        self.clipboard.set_clipboard(msg.get("data", ""))
```

**Phase 3 must:**

1. Add per-`ClientSession` toggle fields: `clipboard_text_c2s`, `clipboard_text_s2c`, `clipboard_image_c2s`, `clipboard_image_s2c` — all default True. Set from `ClientHelloMsg`/`SessionConfigureMsg` payload at handshake time.
2. Gate the outbound broadcast (`_on_clipboard_change`): `if not cs.clipboard_text_s2c: continue` for text, `if not cs.clipboard_image_s2c: continue` for image. Short-circuit BEFORE the JSON encode where possible to avoid wire traffic.
3. Gate the inbound dispatch (the `elif msg_type == MsgType.CLIPBOARD_SEND:` branch): `if not session.clipboard_text_c2s: return` (or image variant). Drop silently — no error response.
4. Add a `apply_capture_mode(session, mode, picked_monitor_id, picked_monitor_name)` method that sets the per-session crop rect on the capture pipeline. Called from `handle_client` once `ClientHelloMsg` is parsed.
5. The fall-back-to-primary path from `monitor_hotplug.py` D-09 hooks back here — flip the session's effective monitor + emit an INFO message back to the client.

**Do not deviate:**
- All client iteration uses `for ws, cs in list(self.clients.items()):` to tolerate dict mutation (lines 539, 550). Don't switch to live iteration.
- All thread→asyncio jumps go through `asyncio.run_coroutine_threadsafe(..., self._event_loop)` (line 541). Never call `cs.enqueue` directly from a non-loop thread.
- Keep the per-session structure additive — new `clipboard_*` toggles default True so legacy clients (no toggles negotiated) get the existing behavior.

---

### `server/session_manager.py` — Flame-approved CustomEDID profile (DISP-04)

**Analog:** itself — `find_edid_file` + `_generate_edid` lines 121-262 is the entire EDID infrastructure. Phase 3 just picks a profile name.

**Existing lookup chain (lines 121-140):**

```python
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
```

**Existing generator monitor-name field (lines 234-239):**

```python
# Descriptor #2: Monitor name
name_offset = 72
edid[name_offset:name_offset + 5] = b'\x00\x00\x00\xFC\x00'
name = "Teraguchi"
name_bytes = name.encode('ascii')[:13].ljust(13, b'\x0a')
edid[name_offset + 5:name_offset + 18] = name_bytes
```

**Phase 3 must:**

1. Update the `name = "Teraguchi"` (line 237) to a Flame-approved monitor name (planner's discretion — "Eizo CG279X" or similar industry-standard grading monitor; keep it ≤13 ASCII chars per the EDID spec).
2. Update the `Manufacturer ID` block (lines 152-155) to the matching IEEE-assigned 3-letter code if the profile name suggests one (e.g. "ENC" for Eizo) — OR keep "TGC" if the profile name doesn't claim a specific vendor.
3. Add a `tests/server/test_session_manager_edid.py` round-trip: write a generated EDID, parse it back, assert the monitor-name descriptor matches the chosen profile.
4. Document the choice in a docstring comment block above `_generate_edid` so the next maintainer understands why "Eizo CG279X" (or whatever) is the chosen Flame-friendly default.

**Do not deviate:**
- The PCoIP fallback path (lines 124-127) stays unchanged — users with PCoIP installed continue to get its bundled EDID, which is already Flame-tested.
- The `with open(path, 'wb') as f: f.write(bytes(edid))` write idiom (lines 260-261) and `logger.info("Generated EDID file: %s ...")` log line stay.
- Checksum logic (line 258) is correct as-is; do not touch.

---

### `client/viewer.py::_widget_to_remote` — Physical-pixel rewrite (D-05, D-06)

**Analog:** itself, lines 655-694 — the existing composite-mode math is the rewrite target.

**Existing math (lines 655-694):**

```python
def _widget_to_remote(self, x: float, y: float) -> tuple:
    """Convert widget coordinates to normalized remote coordinates (0.0-1.0).

    When monitor regions are active, maps through the composite layout
    back to full virtual desktop coordinates so XTest moves the cursor
    to the correct position.
    """
    if not self._monitor_regions:
        # Simple: widget → full remote desktop
        rx = (x - self._offset_x) / (self._scale_x * self._remote_width)
        ry = (y - self._offset_y) / (self._scale_y * self._remote_height)
        return max(0.0, min(1.0, rx)), max(0.0, min(1.0, ry))

    # Composite mode: find which monitor the click is in
    cx = (x - self._offset_x) / self._scale_x
    cy = (y - self._offset_y) / self._scale_y

    composite_x = 0
    for region in self._monitor_regions:
        rw = region["width"]
        rh = region["height"]
        if cx < composite_x + rw:
            local_x = cx - composite_x
            local_y = cy
            desktop_x = region["x"] + local_x
            desktop_y = region["y"] + local_y
            rx = desktop_x / self._remote_width
            ry = desktop_y / self._remote_height
            return max(0.0, min(1.0, rx)), max(0.0, min(1.0, ry))
        composite_x += rw

    # Past the last monitor — clamp
    last = self._monitor_regions[-1]
    rx = (last["x"] + last["width"] - 1) / self._remote_width
    ry = cy / self._remote_height if self._remote_height else 0
    return max(0.0, min(1.0, rx)), max(0.0, min(1.0, ry))
```

**Phase 3 must:**

1. Rewrite the function to return `tuple[float, float, int, int]` — `(rx_norm, ry_norm, server_x_px, server_y_px)`. The two normalized floats remain (Phase 3 wire compat per D-05); the two integer fields are the new D-05 physical-pixel coords.
2. Compute `dpr = self.window().windowHandle().screen().devicePixelRatio()` (or `self.screen().devicePixelRatio()` per Qt 6.10) at the call site, NOT inside this function. Pass DPR in as an arg if needed, OR cache it on the widget and refresh it on `screenChanged` (the cleaner path per D-06).
3. Add a `_screen_changed` slot connected to `self.window().windowHandle().screenChanged` (or the equivalent Qt 6.10 signal) that recomputes the cached DPR and re-runs `_update_scaling`. The signal must be connected in `__init__` AFTER the widget is shown (windowHandle is None pre-show — connect inside `showEvent` instead).
4. Update every call site of `_widget_to_remote` (lines 784, 849, 856, 877, 892) to consume the new 4-tuple. The wire payload (in `client/protocol.py::send_input`) emits both normalized floats AND the new `server_x`/`server_y` ints (server prefers the ints when present per D-05).
5. F12 dev overlay (D-07) reads the same widget→server-px math directly via a parallel `_widget_to_server_px(x, y) -> (sx, sy)` helper so the overlay shows what the wire shows.

**Do not deviate:**
- The composite-mode walk (lines 673-688) stays — its monitor-region iteration is correct; just multiply the final values by DPR at the end.
- `max(0.0, min(1.0, rx))` clamping stays for the normalized floats (compat); integer outputs may go outside the visible bounds (server side handles bounds enforcement).
- The `tabletEvent` integer-pressure path lines 813-824 must also pass server_x/server_y through — extend the `pen_data` dict.

**Test analog (`tests/client/test_viewer_modifier_triggers.py`):** lives in the same directory, same Qt fixture pattern.

---

### `client/viewer.py::keyPressEvent` — F12 debug-overlay hook (D-07)

**Analog:** itself, lines 901-917 + `client/key_diagnostic.py` for overlay shape.

**Existing keyPressEvent (lines 901-917):**

```python
def keyPressEvent(self, event: QKeyEvent):
    if event.isAutoRepeat():
        return
    key = self._remap_key(event.key())
    modifiers = self._qt_modifiers_to_int(event.modifiers())
    logger.debug("Key press: key=0x%x mod=0x%x", key, modifiers)

    # Detect paste: Ctrl+V or Cmd+V → ensure clipboard is synced
    # to server. Phase 2 WR-08: MODIFIER_BIT_CTRL is the wire-format
    # bit position (defined in common/keymap.py); on darwin
    # _qt_modifiers_to_int folds Cmd into the same bit so Cmd+V on
    # Mac correctly fires paste here.
    if key == Qt.Key_V and (modifiers & MODIFIER_BIT_CTRL):
        self.paste_requested.emit()

    self.key_changed.emit(key, key, True, modifiers)
    event.accept()
```

**Phase 3 must:**

1. Insert a guarded F12 branch BEFORE the paste-detect block:
   ```python
   if event.key() == Qt.Key_F12 and os.environ.get("TERAGUCHI_DEBUG") == "1":
       self._coord_overlay_visible = not getattr(self, "_coord_overlay_visible", False)
       if self._coord_overlay:
           self._coord_overlay.setVisible(self._coord_overlay_visible)
       event.accept()
       return
   ```
2. The `_coord_overlay` attribute is a `client/coord_debug_overlay.py::CoordDebugOverlay` instance (new file — see next section), instantiated in `RemoteViewer.__init__` ONLY if `TERAGUCHI_DEBUG=1` (else stays `None`).
3. Production builds (no env var set) get a no-op F12 — the existing behavior is preserved.

**Do not deviate:**
- The `event.accept()` discipline (line 917) — F12 branch must accept BEFORE return, otherwise the event propagates to parents.
- The existing paste-detect block (lines 913-914) and `key_changed.emit` (line 916) are load-bearing — F12 branch must not skip them when F12 is NOT pressed.
- `import os` — add at top of file with the existing imports.

---

### `client/coord_debug_overlay.py` (NEW) — F12 dev overlay (D-07, UI-SPEC C-06 / Surface 6)

**Analog:** `client/key_diagnostic.py` (full module — pattern for dev-only debug Qt widget) + `client/health_display.py` (overlay aesthetic).

**Pattern from `client/key_diagnostic.py:122-165`:**

```python
class KeyDiagnosticTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        layout = QVBoxLayout(self)

        header = QLabel(...)
        header.setWordWrap(True)
        layout.addWidget(header)

        mono = QFont("Menlo" if sys.platform == "darwin" else "Monospace", 16)
        self._current = QLabel("Press a key...")
        self._current.setFont(mono)
        self._current.setStyleSheet(
            "background: #12121c; color: #ececf1; padding: 16px; "
            "border-radius: 10px; border: 1px solid #262640;")
```

**Phase 3 must (per UI-SPEC Surface 6):**

1. Module docstring naming D-07 + the env-var gate.
2. `class CoordDebugOverlay(QWidget):` with `Qt.WA_TransparentForMouseEvents` so it never blocks clicks.
3. 11 pt monospace font (use `QFont("SF Mono" if sys.platform == "darwin" else "Monospace", 11)` — mirror the `Menlo`/`Monospace` pattern from key_diagnostic.py:133).
4. Background `BG_PRIMARY` at 85% opacity + `BORDER` 1 px + 6 px rounded corners — colors from `client/theme.py`.
5. Anchored bottom-left of viewer, 12 px margin (inherits `health_display.py` 12 px).
6. Six text lines per UI-SPEC Surface 6:
   - `F12 · coord debug · {TERAGUCHI_DEBUG=1}` (header, ACCENT)
   - `widget px : {x}, {y}`
   - `server px : {x}, {y}`
   - `DPR       : {dpr:.2f} ({screen_name})`
   - `monitor   : {monitor_name} {w}x{h}+{x}+{y}`
   - `crop rect : {w}x{h}+{x}+{y}` (pick-one only)
   - `delta px  : {wx-sx}, {wy-sy}` (in WARNING color when non-zero)
   - `F12 to hide` (footer, TEXT_MUTED)
7. `update_coords(widget_x, widget_y, server_x, server_y, dpr, screen_name, monitor_dict, crop_rect_or_none)` slot called from `viewer.mouseMoveEvent` and `viewer._screen_changed`.
8. **No animation on show/hide** — UI-SPEC: instant, "dev tool, latency matters more than polish."

**Do not deviate:**
- Colors come from `from client import theme` — no inline hex values.
- The widget MUST be a child of `RemoteViewer` so it inherits z-order above the Metal blit.
- Logger name follows `logger = logging.getLogger(__name__)` (this file is dev-only — no need for structlog).

---

### `client/remap_banner.py` (NEW) — Non-modal topology-change banner (D-09, UI-SPEC C-05 / Surface 5)

**Analog:** `client/fullscreen_toolbar.py` lines 28-189 — slide-down animation + auto-hide pattern. Phase 3 banner is the logical sibling.

**Pattern from `client/fullscreen_toolbar.py:37-128`:**

```python
class FullscreenToolbar(QWidget):
    exit_fullscreen = Signal()
    disconnect_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedHeight(TOOLBAR_HEIGHT)
        self.setMouseTracking(True)

        self._visible = False
        self._animation = QPropertyAnimation(self, b"pos")
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        ...

    def reveal(self):
        if self._visible:
            self._hide_timer.stop()
            return
        self._visible = True
        self.show()
        self.raise_()
        self._animation.stop()
        self._animation.setStartValue(self.pos())
        self._animation.setEndValue(self._shown_pos)
        self._animation.start()
```

**Phase 3 must (per UI-SPEC Surface 5):**

1. `class RemapBanner(QFrame):` (or QWidget) with two signals: `dismissed = Signal()` + `remap_requested = Signal()`.
2. 48 px tall, full viewer width, slides in from top.
3. Animation: `QPropertyAnimation(self, b"pos")` 200 ms `QEasingCurve.OutCubic` — mirror `fullscreen_toolbar.py:46-48`.
4. Layout per UI-SPEC: icon + title (13 px weight 600) + body (12 px weight 400) + action link (only in pick-one case, ACCENT) + dismiss × glyph.
5. WARNING (#f5a623) 3 px left border per UI-SPEC color matrix.
6. **Sticky** until dismissed — NO auto-timeout (per Surface 5 rationale).
7. Esc dismisses, Enter activates the action link when banner is focused.
8. Banner outside its bbox is click-through-to-viewer — set `Qt.WA_TransparentForMouseEvents` only on the empty regions (not the title/dismiss/link), OR use a child-widget composition.
9. Body text varies by case (mirror-all add, mirror-all remove, single, pick-one missing) — UI-SPEC has all four variants verbatim. NO PARAPHRASE.

**Do not deviate:**
- Colors via `from client import theme` (theme.WARNING, theme.ACCENT, theme.BG_TERTIARY).
- Animation duration EXACTLY 200 ms — UI-SPEC motion contract.
- Logger: `logger = logging.getLogger(__name__)`.

---

### `client/toasts.py` (NEW) — Reusable InfoToast (D-14 + D-09, UI-SPEC C-09 / Surfaces 8 & 9)

**Analog:** `client/fullscreen_toolbar.py` for `QPropertyAnimation` + `QTimer` self-hide. The pattern is the same shape.

**Phase 3 must (per UI-SPEC Surfaces 8 + 9):**

1. `class InfoToast(QFrame):` with constructor args `(icon, title, body, anchor=BottomRight, duration_ms=6000)`.
2. Fixed 360 px width, min 56 px height (UI-SPEC component dimensions).
3. Stacks vertically sm=8 px apart, max 3 visible — implement a module-level `_toast_stack` list + `_relayout_stack()` helper.
4. Anchored bottom-right of viewer with md=16 px margin.
5. Animation: 120 ms linear fade-in, 200 ms linear fade-out (UI-SPEC motion contract).
6. `QTimer` with `setSingleShot(True)` for auto-dismiss; hover stops the timer (per Surface 8 hover-pause).
7. Esc dismisses when focused; click anywhere on the toast dismisses.
8. Helper functions `show_oversize_image_toast(parent, size_mb)` (Surface 8) and `show_monitor_switched_toast(parent, monitor_name)` (Surface 9) — wraps `InfoToast` with the locked copy strings.

**Do not deviate:**
- The locked copy strings in UI-SPEC Surface 8 + 9 are load-bearing — copy verbatim.
- Width is EXACTLY 360 px, not a theme token (UI-SPEC dimension exception).
- Logger: `logger = logging.getLogger(__name__)`.

---

### `client/clipboard_toggle_menu.py` (NEW) — 4-checkbox toolbar QMenu (D-15, UI-SPEC C-07 / Surface 7)

**Analog:** `client/monitor_selector.py` (the entire module, lines 1-126) — canonical QToolButton + QMenu + checkable QAction pattern.

**Pattern from `client/monitor_selector.py:16-78`:**

```python
class MonitorSelector(QToolButton):
    selection_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._monitors: list = []
        self._actions: list = []

        self.setText("Monitors")
        self.setPopupMode(QToolButton.InstantPopup)
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self.setMinimumWidth(120)

    def update_monitors(self, monitors: list):
        ...
        for mon in monitors:
            ...
            action = self._menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(self._on_toggled)
            self._actions.append((action, mon))
```

**Phase 3 must (per UI-SPEC Surface 7):**

1. `class ClipboardToggleButton(QToolButton):` with one signal: `toggles_changed = Signal(dict)` emitting `{"text_c2s": bool, "text_s2c": bool, "image_c2s": bool, "image_s2c": bool}`.
2. 28×28 px clickable, 18 px icon — uses `client/icons.py::icon_clipboard()` (new icon — see next section).
3. `setAccessibleName("Clipboard direction")` and `setAccessibleDescription("Toggle which direction clipboard content flows...")` per UI-SPEC Surface 7 verbatim.
4. Popup is a `QMenu` with `QWidgetAction` rows wrapping `QCheckBox` widgets — 4 rows per UI-SPEC. Header label + footer note via additional `QWidgetAction` with `QLabel` content.
5. Rows 3 + 4 are nested under rows 1 + 2: when row 1 is unchecked, row 3 becomes disabled (`setEnabled(False)`) — but its checked state is preserved (no surprise state loss).
6. Menu does NOT auto-close on checkbox change (UI-SPEC behavior note) — user can flip multiple toggles in one open.
7. Tooltip varies by current state per UI-SPEC Surface 7 ("all directions on" / compact-summary / "disabled").
8. All copy strings copied verbatim from UI-SPEC Surface 7.

**Do not deviate:**
- Inherit from `QToolButton`, NOT a custom widget — preserves the existing toolbar visual rhythm and `:checked` styling from `theme.py`.
- The 4 default-ON state per D-15. Initial emit on `__init__` so connected slots have a baseline.

---

### `client/icons.py::icon_clipboard` (NEW) — Clipboard SVG icon (UI-SPEC C-08)

**Analog:** any existing icon — `client/icons.py::icon_keyboard` (lines 102-112) is the closest visual cousin (rectangle-based outline).

**Pattern from `client/icons.py:114-119`:**

```python
def icon_monitor(color="#8b8ba3"):
    """Monitor — display/screen."""
    return _svg_icon(
        '<rect x="2" y="3" width="20" height="14" rx="2"/>'
        '<line x1="8" y1="21" x2="16" y2="21"/>'
        '<line x1="12" y1="17" x2="12" y2="21"/>', color)
```

**Phase 3 must:**

1. Add `icon_clipboard(color="#8b8ba3")` function returning `_svg_icon(...)` with the exact SVG body from UI-SPEC Surface 7:
   ```python
   def icon_clipboard(color="#8b8ba3"):
       """Clipboard silhouette — copy/paste direction toggles (Phase 3 D-15)."""
       return _svg_icon(
           '<rect x="7" y="3" width="10" height="4" rx="1"/>'
           '<rect x="4" y="5" width="16" height="16" rx="2"/>'
           '<line x1="8" y1="11" x2="16" y2="11"/>'
           '<line x1="8" y1="15" x2="14" y2="15"/>',
           color)
   ```
2. Add it under the existing `# ── Toolbar ────` section (after `icon_monitor`).

**Do not deviate:**
- 24×24 viewBox / 1.5 px stroke is enforced by `_svg_icon` (lines 16-40). Don't reinvent.
- `color="#8b8ba3"` default matches the file convention.

---

### `client/bookmarks.py` — Six new fields + migration (D-01, D-04, D-15)

**Analog:** itself — `_load` migration block lines 112-135 (Phase 2 D-10) is the canonical bookmark-migration pattern.

**Existing migration (lines 112-135):**

```python
def _load(self):
    if self._bookmarks_file.exists():
        try:
            with open(self._bookmarks_file) as f:
                data = json.load(f)
            migrated = False
            for bid, pdata in data.items():
                self._profiles[bid] = ConnectionProfile.from_dict(pdata)
                # Phase 2 D-10 migration — pre-Phase-2 bookmarks did not
                # carry destination_kind / swap_cmd_ctrl. The dataclass
                # default lands swap_cmd_ctrl=True (correct for Linux),
                # but if the saved JSON omits the field entirely AND the
                # destination is a Mac, the default is wrong. ...
                if "swap_cmd_ctrl" not in pdata or "destination_kind" not in pdata:
                    migrated = True
                    prof = self._profiles[bid]
                    if prof.destination_kind == "mac":
                        prof.swap_cmd_ctrl = pdata.get("swap_cmd_ctrl", False)
                    else:
                        prof.swap_cmd_ctrl = pdata.get("swap_cmd_ctrl", True)
            logger.info("Loaded %d bookmarks", len(self._profiles))
            if migrated:
                self._save()
        except Exception as e:
            logger.error("Failed to load bookmarks: %s", e)
```

**Existing atomic write (lines 151-175) — keep verbatim, Phase 3 doesn't touch this:**

```python
def _save(self):
    """Save bookmarks to disk atomically."""
    try:
        data = {bid: p.to_dict() for bid, p in self._profiles.items()}
        tmp = self._bookmarks_file.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._bookmarks_file)
    except Exception as e:
        logger.error("Failed to save bookmarks: %s", e)
```

**Phase 3 must:**

1. Extend `ConnectionProfile` in `common/messages.py` (lines 679-712) with six new fields — all default to safe values:
   ```python
   # Phase 3 D-01 / D-04 / D-15 — display + clipboard preferences
   monitor_mode: str = "mirror_all"   # "single" | "mirror_all" | "pick_one"
   picked_monitor_id: int = -1        # -1 = no pick
   picked_monitor_name: str = ""
   clipboard_text_c2s: bool = True
   clipboard_text_s2c: bool = True
   clipboard_image_c2s: bool = True
   clipboard_image_s2c: bool = True
   ```
2. Add a Phase 3 migration block in `_load` after the existing Phase 2 D-10 block (lines 125-132). Mirror its shape verbatim:
   ```python
   # Phase 3 D-01 / D-15 migration — pre-Phase-3 bookmarks lack the new
   # display + clipboard fields. Dataclass defaults already pick the
   # safe values (mirror_all + all clipboard toggles ON), but we mark
   # the bookmark as migrated so _save() persists the new shape on
   # next load — that prevents a subtle "user enables a toggle but it
   # never persists because the field was never in the JSON" bug.
   phase3_fields = ("monitor_mode", "picked_monitor_id",
                    "picked_monitor_name", "clipboard_text_c2s",
                    "clipboard_text_s2c", "clipboard_image_c2s",
                    "clipboard_image_s2c")
   if any(f not in pdata for f in phase3_fields):
       migrated = True
   ```
3. The existing `if migrated: self._save()` (line 135) catches the new flag and atomically writes the new shape — no further changes needed.

**Do not deviate:**
- The atomic-write pattern (`_save`, lines 151-175) is the WR-01 fix from Phase 2 — DO NOT regress to direct `with open(self._bookmarks_file, "w")`.
- `ConnectionProfile.from_dict` (line 718) uses `cls.__dataclass_fields__` filter — DO NOT add fields outside the dataclass.
- Logger output strings are documented telemetry — preserve `Loaded %d bookmarks` (line 133).

---

### `client/protocol.py::send_clipboard` — Image + chunked transport (D-13, D-15, D-17)

**Analog:** itself — `send_clipboard` (line 315-317) and the inbound CLIPBOARD_RECV branch (lines 956-958) are the extension points.

**Existing send (lines 315-317):**

```python
def send_clipboard(self, text: str):
    self.send_input({"type": MsgType.CLIPBOARD_SEND,
                     "content_type": "text/plain", "data": text})
```

**Existing receive (lines 956-958):**

```python
elif msg_type == MsgType.CLIPBOARD_RECV:
    if self.on_clipboard:
        self.on_clipboard(msg.get("data", ""))
```

**Phase 3 must:**

1. Refactor `send_clipboard(self, content_type: str, data: bytes_or_str)` to dispatch on `content_type`:
   - `"text/plain"`: keep existing one-shot path.
   - `"image/png"`: validate magic bytes (`if not data.startswith(b"\x89PNG\r\n\x1a\n"): logger.warning(...); return`); validate size (≤ 64 MB); base64-encode; if encoded length > 1 MB, split into 1 MB `ClipboardChunkMsg` frames (D-17) and emit each via `self.send_input(json.loads(chunk.to_json()))`.
2. Add a per-session inbound chunk assembler: `self._clipboard_chunks: dict[int, list] = {}` keyed by sequence_id. On receiving a `MsgType.CLIPBOARD_CHUNK`, append the chunk; when `total_chunks` chunks have arrived, concatenate, base64-decode, validate magic bytes again (D-16 defense-in-depth), and fire `self.on_clipboard(content_type, decoded_bytes)`.
3. Update the `on_clipboard` callback signature to `Callable[[str, bytes_or_str], None]` — content_type + data. Update `client/session.py` `_on_clipboard_recv` (line 248 wire-up + line 378-380 implementation) accordingly.
4. Per-direction toggle gating (D-15): inside `send_clipboard`, `if not self._clipboard_text_c2s and content_type == "text/plain": return`; etc. The four bools come from `ClientProtocol` state set by the bookmark / toolbar.
5. CLIPBOARD_CHUNK chunks get their OWN bounded queue slot per P1 STAB-07. Mirror the bounded queue pattern from `server/client_session.py` send_queue (maxsize=4 per `tests/server/test_pipelines.py:30`).

**Do not deviate:**
- `self.send_input(...)` is the wire-emit entry point — never bypass to `await ws.send(...)` directly.
- The `MsgType.CLIPBOARD_RECV` branch dispatch (line 956) stays as the text path; new `MsgType.CLIPBOARD_CHUNK` branch routes into the assembler.
- `on_clipboard = Optional[Callable]` (line 144) — preserve the optional pattern; assembler completion fires only if the callback is set.

---

### `client/main_window.py::ConnectionDialog` — Mode picker (D-01, D-04)

**Analog:** itself — Phase 2 D-10 added a destination-kind combobox (lines 98-124) and a swap-checkbox (lines 116-124). The same pattern lands the mode picker.

**Existing Phase 2 D-10 widget shape (lines 98-124):**

```python
# Phase 2 D-10 / WR-03 — destination kind picker drives the
# per-bookmark Cmd<->Ctrl swap default. ...
self.destination_kind_combo = QComboBox()
self.destination_kind_combo.addItem(
    "Linux (Rocky / Flame production)", "linux"
)
self.destination_kind_combo.addItem(
    "Mac (macOS server)", "mac"
)
self.destination_kind_combo.currentIndexChanged.connect(
    self._on_destination_kind_changed
)
layout.addRow("Destination:", self.destination_kind_combo)

self.swap_cmd_ctrl_check = QCheckBox(
    "Swap Cmd/Ctrl for this server (Mac client → Linux Flame)"
)
```

**Existing property accessor (lines 200-208):**

```python
@property
def destination_kind(self) -> str:
    """Phase 2 WR-03 — chosen destination platform ('linux' or 'mac')."""
    data = self.destination_kind_combo.currentData()
    return data if data in ("linux", "mac") else "linux"
```

**Phase 3 must (per UI-SPEC Surface 1):**

1. Add a `ModeSelector` sub-widget (planner picks: inline class in main_window.py OR new `client/connect_dialog.py`). It composes:
   - Section heading `Monitor mode` (12 px label).
   - `QRadioButton` group with three options: `Single monitor` / `Mirror all` / `Pick one` (label + help text per UI-SPEC Surface 1 verbatim).
   - When `Pick one` is checked, reveal a sub-selector — the existing `client/monitor_selector.MonitorSelector` constrained to single-check (radio) mode (D-04).
2. Add `mode_changed = Signal(str, int, str)` emitting `(mode, picked_monitor_id, picked_monitor_name)`.
3. Pre-fill from the bookmark's `monitor_mode` + `picked_monitor_id`/`picked_monitor_name` if present.
4. Tooltip on the mode group when bookmark pre-fills: `Last used: {mode}. Change anytime before connecting.` (UI-SPEC verbatim).
5. Add `@property monitor_mode`, `picked_monitor_id`, `picked_monitor_name` accessors mirroring `destination_kind` (lines 200-208).

**Do not deviate:**
- Layout uses the existing `QFormLayout` (line 59) with `setLabelAlignment(Qt.AlignRight)` — preserve the existing visual rhythm.
- All copy strings from UI-SPEC Surface 1 verbatim. NO PARAPHRASE.
- The dialog title `"New Connection"` (line 55) does not change.

---

### `client/monitor_selector.py` — Radio (single-check) mode (D-04)

**Analog:** itself — the existing checkable-action menu is the extension target.

**Existing checkable-action loop (lines 50-65):**

```python
for mon in monitors:
    mon_id = mon.get("id", 0)
    name = mon.get("name", f"Monitor {mon_id}")
    w = mon.get("width", 0)
    h = mon.get("height", 0)
    label = f"{name} ({w}x{h})"

    action = self._menu.addAction(label)
    action.setCheckable(True)
    if old_selected_ids:
        action.setChecked(mon_id in old_selected_ids)
    else:
        action.setChecked(True)
    action.toggled.connect(self._on_toggled)
    self._actions.append((action, mon))
```

**Phase 3 must (per UI-SPEC Surface 4):**

1. Add a `mode` property: `"checkbox"` (default, mirror-all) or `"radio"` (pick-one).
2. In `radio` mode, hijack `_on_toggled`: when one action toggles to checked, set every other action to unchecked (block signals during the bulk update).
3. Hide `Select All` and `Select None` quick-actions (lines 68-72) in radio mode — meaningless for radio.
4. Update `_update_label` (lines 96-107): in radio mode, the button shows the picked monitor's name only (NOT "All (N)" or "N of M").
5. Menu header text varies by mode: `"Monitors"` (checkbox) vs `"Pick one monitor"` (radio) per UI-SPEC.
6. Bookmarked-monitor-missing toast (UI-SPEC Surface 4): emit a one-shot signal `monitor_missing = Signal(str)` carrying the missing monitor name; consumer (session.py) shows the toast via `client/toasts.py`.

**Do not deviate:**
- The `selection_changed = Signal(list)` (line 24) signal contract stays — radio mode emits a 1-element list.
- The `update_monitors(monitors: list)` signature (line 37) stays binary-compatible.
- `setPopupMode(QToolButton.InstantPopup)` (line 32) stays.

---

### `client/fullscreen_toolbar.py` — Mode badge + clipboard button (D-01, D-15)

**Analog:** itself — `_build_ui` (lines 63-128) is the extension point. Mode badge inserts near the health summary; clipboard button inserts near `MonitorSelector`.

**Existing toolbar layout (lines 63-128):**

```python
def _build_ui(self):
    layout = QHBoxLayout(self)
    layout.setContentsMargins(16, 0, 16, 0)
    layout.setSpacing(12)

    self._conn_label = QLabel("Not Connected")
    ...
    layout.addWidget(self._conn_label)
    layout.addSpacing(8)

    sep = QLabel("|")
    layout.addWidget(sep)
    layout.addSpacing(8)

    from client.monitor_selector import MonitorSelector
    self._monitor_selector = MonitorSelector()
    layout.addWidget(self._monitor_selector)

    layout.addStretch()

    self._health_dot = QLabel()
    ...
```

**Phase 3 must (per UI-SPEC C-02 + C-07 + Surfaces 2 + 7):**

1. Insert a `ClipboardToggleButton` (new — see clipboard_toggle_menu.py) AFTER `self._monitor_selector` (line 88) and BEFORE the `addStretch()` (line 90):
   ```python
   from client.clipboard_toggle_menu import ClipboardToggleButton
   self._clipboard_toggle = ClipboardToggleButton()
   layout.addWidget(self._clipboard_toggle)
   ```
2. Insert a mode badge BEFORE `self._health_dot` (line 93). The badge is a `QLabel` (or small `QFrame` with `QPainter` underline — UI-SPEC Surface 2 specifies a 2 px ACCENT or WARNING underline). Renders `Mode: single|mirror|pick: {monitor_name}` exactly per UI-SPEC.
3. Add `update_capture_mode(mode: str, picked_name: str = "", degraded: bool = False)` slot setting badge text + tooltip per UI-SPEC Surface 2 + 3.
4. Expose `clipboard_toggle_menu` and `mode_badge` as properties (mirror line 192 `monitor_selector` property).

**Do not deviate:**
- Layout `setSpacing(12)` (line 66) — the 12 px is inherited per UI-SPEC, do not change.
- Toolbar height `TOOLBAR_HEIGHT = 48` (line 23) — UI-SPEC inheritance.
- Color via `from client import theme` — never inline hex.

---

### `client/session.py` — Wire toggles + capture-mode + clipboard image (D-01, D-02, D-13, D-15)

**Analog:** itself — `_wire_viewer` (lines 259-274) and `_on_clipboard_recv` (lines 378-380) are the extension points.

**Existing wiring (lines 259-274):**

```python
def _wire_viewer(self):
    v = self.viewer
    v.mouse_moved.connect(self._send_mouse_move)
    v.mouse_button_changed.connect(self._send_mouse_button)
    v.mouse_scrolled.connect(self._send_mouse_scroll)
    v.key_changed.connect(self._send_key_event)
    v.pen_event.connect(self._send_pen_event)
    v.paste_requested.connect(self._push_clipboard_for_paste)
    v.files_dropped.connect(self.send_files)
    v.reset_modifiers_requested.connect(self.protocol.send_reset_modifiers)
    v.text_commit.connect(self.protocol.send_text_commit)
```

**Phase 3 must:**

1. On bookmark connect, push `monitor_mode` + `picked_monitor_id` + `picked_monitor_name` + the four clipboard toggles into the protocol via either an extended `ClientHelloMsg` or a new `SessionConfigureMsg` send (whichever shape the messages.py extension picks). Pattern parallels Phase 2 `protocol.set_swap_cmd_ctrl(profile.swap_cmd_ctrl)`.
2. Wire the `ClipboardToggleButton.toggles_changed` signal to a new `_on_clipboard_toggles_changed` slot that calls `protocol.set_clipboard_toggles(...)` and persists the change to the bookmark (`self.bookmark_manager.update(bid, clipboard_text_c2s=..., ...)`).
3. Update `_on_clipboard_recv(self, content_type: str, data)` to dispatch on content_type — text path keeps `QApplication.clipboard().setText(text)`; image path calls `QApplication.clipboard().setPixmap(QPixmap.fromImage(QImage.fromData(png_bytes, "PNG")))` after PNG magic-byte revalidate.
4. `_push_clipboard_for_paste` (lines 299-306) extends to also check for a clipboard image: `if QApplication.clipboard().mimeData().hasImage(): protocol.send_clipboard("image/png", png_bytes)`.

**Do not deviate:**
- The `_Bridge` signal pattern (line 232 `p.on_clipboard = b.clipboard_recv.emit`) is load-bearing — Qt thread safety. Never call viewer/QApplication.clipboard() from the asyncio thread directly.
- `getattr(self, '_clipboard_from_server', False)` echo-suppression (line 384) stays — extend to also suppress image echos.

---

## Shared Patterns

### Pattern A — Optional-import platform gate

**Source:** `server/mac_clipboard.py:22-30`

**Apply to:** Any new file pulling PyObjC, Xlib, NvFBC, or any optional dependency.

```python
try:
    from AppKit import (
        NSPasteboard,
        NSPasteboardTypeString,
    )
    _HAS_APPKIT = True
except Exception as _e:
    _HAS_APPKIT = False
    _import_error = _e
```

Then gate runtime usage:
```python
if not _HAS_APPKIT:
    self._pb = None
    logger.warning("AppKit not available (%s) — clipboard sync disabled",
                   _import_error)
```

### Pattern B — Polling thread with echo suppression

**Source:** `server/clipboard.py:89-126` and `server/mac_clipboard.py:101-142`

**Apply to:** The PNG image-clipboard polls in `server/clipboard.py` and `server/mac_clipboard.py`.

```python
def start_monitoring(self, on_change: Callable):
    if not self._tool:
        return
    self._on_clipboard_change = on_change
    self._running = True
    self._last_content = self.get_clipboard()
    self._thread = threading.Thread(
        target=self._poll_loop,
        daemon=True,
        name="clipboard-monitor",
    )
    self._thread.start()

def _poll_loop(self):
    while self._running:
        try:
            current = self.get_clipboard()
            if current and current != self._last_content:
                self._last_content = current
                if self._on_clipboard_change:
                    self._on_clipboard_change(current)
        except Exception as e:
            logger.debug("Clipboard poll error: %s", e)
        time.sleep(self.poll_interval)

def stop(self):
    self._running = False
    if self._thread:
        self._thread.join(timeout=2)
        self._thread = None
```

### Pattern C — Async broadcast from a sync thread

**Source:** `server/session_runtime.py:539-541`

**Apply to:** Every Phase 3 server-side event (hot-plug, image clipboard, fall-back-toast) that needs to push to authenticated clients.

```python
for ws, cs in list(self.clients.items()):
    if cs.authenticated and self._event_loop:
        asyncio.run_coroutine_threadsafe(cs.enqueue(msg_json), self._event_loop)
```

### Pattern D — `MsgType` constant + dataclass + to_json round-trip

**Source:** `common/messages.py:120-150` (MsgType strings) + `:534-563` (dataclass with to_json)

**Apply to:** `ClipboardChunkMsg`, `SessionConfigureMsg`, any new wire shape Phase 3 introduces.

```python
class MsgType:
    ...
    # Phase 3 additions (D-02, D-17):
    CLIPBOARD_CHUNK = "clipboard_chunk"
    SESSION_CONFIGURE = "session_configure"

@dataclass
class ClipboardChunkMsg:
    """Client/Server: chunked image clipboard transport (D-17)."""
    type: str = MsgType.CLIPBOARD_CHUNK
    sequence_id: int = 0
    chunk_index: int = 0
    total_chunks: int = 1
    content_type: str = "image/png"
    data: str = ""  # base64-encoded chunk

    def to_json(self) -> str:
        return json.dumps(asdict(self))
```

### Pattern E — Bookmark migration block

**Source:** `client/bookmarks.py:112-135`

**Apply to:** Phase 3's six new fields on `ConnectionProfile`.

```python
for bid, pdata in data.items():
    self._profiles[bid] = ConnectionProfile.from_dict(pdata)
    # Phase X migration — pre-Phase-X bookmarks did not carry <field>.
    if "<field>" not in pdata:
        migrated = True
        # Optional: re-apply destination-aware default
        prof = self._profiles[bid]
        ...
if migrated:
    self._save()
```

### Pattern F — pytest unit test (mock at OS boundary)

**Source:** `tests/server/test_health_monitor.py:39-78`

**Apply to:** Phase 3 unit tests for `screen_capture`, `mac_screen_capture`, `clipboard`, `mac_clipboard` (mock `mss.mss`, `subprocess.run`, `NSPasteboard`, `SCK` boundaries).

```python
def test_record_transmit_time():
    """transmit stage populates its own deque and surfaces via get_stats()."""
    h = HealthMonitor()
    h.record_transmit_time(2.1)
    stats = h.get_stats()
    assert stats.transmit_time_ms == 2.1
```

For OS-boundary tests, use `monkeypatch`:

```python
def test_clipboard_set_image(monkeypatch):
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R: returncode = 0; stdout = b""
        return R()
    monkeypatch.setattr("server.clipboard.subprocess.run", fake_run)
    ...
```

### Pattern G — pytest integration test (in-process loopback with TLS)

**Source:** `tests/integration/test_reconnect.py:36-99`

**Apply to:** `tests/integration/test_monitor_hotplug.py`, `test_capture_mode.py`, `test_clipboard_text_large.py`, `test_clipboard_image.py`.

```python
@pytest.mark.asyncio
async def test_supervisor_reconnects_after_transport_lost(
    tls_ca_and_cert, free_port,
):
    async def server_handler(ws):
        ...

    ssl_srv = _server_ctx(
        tls_ca_and_cert["server_cert"], tls_ca_and_cert["server_key"],
    )
    async with websockets.serve(
        server_handler, "127.0.0.1", free_port, ssl=ssl_srv,
    ):
        cli_ctx = ssl.create_default_context(
            cafile=str(tls_ca_and_cert["ca_cert"]))
        ...
```

Fixtures `tls_ca_and_cert` and `free_port` come from `tests/integration/conftest.py` lines 1-100 — re-use, do not invent new TLS scaffolding.

### Pattern H — pytest message round-trip

**Source:** `tests/common/test_messages_phase2.py:22-110`

**Apply to:** `tests/common/test_messages_phase3.py` (or extend `test_messages.py`) for `ClipboardChunkMsg`, `SessionConfigureMsg`, extended `MouseEventMsg`, etc.

```python
def test_key_reset_modifiers_roundtrip():
    from common.messages import KeyResetModifiersMsg, MsgType, parse_message
    msg = KeyResetModifiersMsg(reason="focus_out")
    parsed = parse_message(msg.to_json())
    assert parsed["type"] == MsgType.KEY_RESET_MODIFIERS
    assert parsed["reason"] == "focus_out"
```

### Pattern I — structlog stage telemetry (D-18 latency budget tracking)

**Source:** `common/logging.py:298-309` (`get_logger`) + `common/logging.py:317-355` (`StageTimer` context manager)

**Apply to:** Phase 3 hot paths needing measurable latency contribution: `screen_capture.py` GPU crop stage, `clipboard.py` PNG codec stage, `clipboard chunk assembly` on receive.

```python
from common.logging import get_logger
logger = get_logger("server.screen_capture")

# Per-event structured emit:
logger.info("monitor_hotplug", old_count=old_count, new_count=new_count,
            duration_ms=elapsed * 1000)
```

For span-style: `with StageTimer("clipboard_image_codec"): ...` (auto-emits `stage.timing` with `latency_ms`).

---

## No Analog Found

None. Phase 3 is pure extension — every new file or extension point has a clean codebase analog.

---

## Metadata

**Analog search scope:** `client/`, `server/`, `common/`, `tests/` (entire packages — Phase 3 stays inside the existing v1 deployable boundaries).

**Files scanned:**
- `client/`: viewer.py, bookmarks.py, monitor_selector.py, fullscreen_toolbar.py, main_window.py, session.py, protocol.py, key_diagnostic.py, icons.py, theme.py, health_display.py
- `server/`: screen_capture.py, mac_screen_capture.py, monitor_hotplug.py, session_manager.py, clipboard.py, mac_clipboard.py, session_runtime.py, encoder_lifecycle.py
- `common/`: messages.py, logging.py
- `tests/`: conftest.py, integration/conftest.py, server/test_health_monitor.py, server/test_pipelines.py, common/test_messages_phase2.py, client/test_bookmarks.py, integration/test_reconnect.py
- Project conventions: `.planning/codebase/STRUCTURE.md`, `.planning/codebase/CONVENTIONS.md`

**Pattern extraction date:** 2026-04-19

---

## PATTERN MAPPING COMPLETE
