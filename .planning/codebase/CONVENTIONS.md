# Coding Conventions

**Analysis Date:** 2026-04-18

## Language & Version

- **Python 3.10+** required — declared in `pyproject.toml` (`requires-python = ">=3.10"`).
- macOS installer (`install-server-macos.sh`) searches for Python 3.10–3.13 explicitly (prefers Homebrew's `python3.13`).
- Project venv present in `.venv/` (Python 3.14 on this machine); `.gitignore` excludes `.venv/` and `venv/`.

## Formatting & Linting

**None configured.** The project ships no code-style tooling.

- No `[tool.black]`, `[tool.ruff]`, `[tool.isort]`, `[tool.mypy]` sections in `pyproject.toml`
- No `.pre-commit-config.yaml`
- No `tox.ini`, `noxfile.py`, or `Makefile` at project root (only `server/nvfbc/Makefile` for the C helper)
- No `.editorconfig`
- No `.flake8` or `pyproject.toml` linting config

**Observed informal style** (consistent across `server/`, `client/`, `common/`, `broker/`):

- 4-space indentation
- PEP 8 naming (see below)
- Line length hovers around ~90–100 chars; no enforced limit
- Double-quoted strings predominate; single quotes used occasionally for nested literals
- Trailing commas in multi-line tuples/lists are common but inconsistent

## Naming Patterns

**Files:**
- `snake_case.py` throughout all packages (`screen_capture.py`, `mac_input_injector.py`, `session_manager.py`, `video_encoder.py`)
- Platform-specific modules prefixed with `mac_`: `server/mac_screen_capture.py`, `server/mac_input_injector.py`, `server/mac_clipboard.py`
- Linux-default modules are unprefixed (`screen_capture.py`, `input_injector.py`, `clipboard.py`) — the Linux path is the baseline and Mac variants sit alongside

**Functions:**
- `snake_case` — e.g. `detect_encoders()`, `find_free_display()`, `check_ffmpeg_available()`
- Private/internal prefixed with `_` — e.g. `_find_input_event_device()`, `_load_users()`, `_save_users()`
- Factory/constructor helpers use `create_` / `detect_` / `find_` / `check_` verbs

**Variables:**
- `snake_case` for locals and instance attributes
- Private instance attributes prefixed with `_`: `self._users`, `self._pam`, `self._last_change_count`, `self._thread`, `self._lock`
- Module-level constants `UPPER_SNAKE_CASE`: `DEFAULT_USERS_FILE`, `IS_MACOS`, `IS_LINUX`, `DEFAULT_JPEG_QUALITY`, `MIN_DISPLAY`, `MAX_DISPLAY`, `XORG_CONFIG`

**Classes:**
- `PascalCase`: `Authenticator`, `SessionRuntime`, `ScreenCapture`, `MacScreenCapture`, `InputInjector`, `ClipboardSync`, `QualitySettings`, `HealthMonitor`, `VideoEncoder`
- Custom exceptions suffix `Error` and inherit from `RuntimeError`: `MacScreenCaptureError` (`server/mac_screen_capture.py:115`), `MacInputInjectorError` (`server/mac_input_injector.py:94`)

**Enums:** `IntEnum` subclasses in `PascalCase`, members `UPPER_SNAKE_CASE` — `FrameType`, `VideoCodec`, `ChromaSubsampling`, `AudioCodec`, `VideoFrameFlags` in `common/messages.py`

**Dataclasses:** Used heavily for protocol messages and config structs (~20 dataclasses in `common/messages.py`). Pattern: `@dataclass` decorator, `type: str = MsgType.X` sentinel field for JSON message discrimination.

## Package Organization

Top-level packages (see `pyproject.toml` `[tool.setuptools.packages.find]`):

- `server/` — Linux + macOS server-side runtime; entry point `server.main:main`
- `client/` — Cross-platform Qt (PySide6) client; entry point `client.main:main`
- `broker/` — Connection broker (PAM/FreeIPA auth + machine pool + admin UI)
- `common/` — Protocol messages, keymap, transport abstractions (`hybrid_transport`, `quic_transport`, `udp_transport`, `jitter_buffer`)
- `tools/` — Standalone diagnostic scripts (e.g. `tools/keydiag.py`)

Each package has a minimal `__init__.py` (a single comment line). No re-exports, no barrel files.

## Type Hints

**Partially typed** — no `mypy` enforcement, but type hints are common on public signatures:

- Function parameters frequently annotated: `def verify(self, username: str, client_hash: str, challenge: str) -> bool:` (`server/auth.py:149`)
- Return types usually annotated on non-trivial functions
- `from typing import Optional, List, Dict, Callable` used throughout (pre-PEP-604 style — `Optional[str]`, `List[dict]`, `Dict[str, Foo]`)
- Instance attributes typed via constructor annotations and inline: `self._thread: Optional[threading.Thread] = None`
- `TYPE_CHECKING` guard used once in `server/session_manager.py:28` to avoid a circular import

Typing is aspirational, not enforced. Local/helper functions often omit hints.

## Docstrings

**Module docstrings: always present.** Every module opens with a triple-quoted summary + design notes. Example style:

```python
"""
Server-side authentication.

Supports multiple auth backends:
- PAM:   Authenticates against Linux system users (local, LDAP, FreeIPA)
- Local: Custom JSON user database with challenge-response (legacy)
- None:  Authentication disabled

PAM mode requires the server to run as root.
"""
```

**Class docstrings:** Almost always present. Usage examples occasionally included (`Authenticator` in `server/auth.py:29-44`).

**Function docstrings:** Most public functions have a short description. Args/Returns blocks are used ad-hoc, not in a strict Google/Sphinx/NumPy style. Examples:

- `server/auth.py:49-53` — Google-ish `Args:` block
- `server/screen_capture.py:76-80` — one-line + "Returns list of..." paragraph

**Design-note docstrings:** Mac backend modules (`server/mac_screen_capture.py`, `server/mac_input_injector.py`, `server/mac_clipboard.py`) use extended "Design notes" sections with bullet points explaining platform quirks, TCC permissions, frame-delivery semantics, etc. Treat these as authoritative when touching those files.

## Import Organization

**Order observed:**
1. Standard library (`import asyncio`, `import logging`, ...) — alphabetical-ish, not strict
2. Third-party (`import websockets`, `import mss`, `import numpy as np`, `from PySide6.QtCore import ...`)
3. First-party (`from common.messages import ...`, `from server.auth import Authenticator`)

Blank line between groups is typical but not universal.

**Entry-point modules** (`server/main.py`, `client/main.py`, `broker/main.py`) start with:

```python
sys.path.insert(0, ".")
# or
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
```

This lets them be run as scripts (`python -m server.main`) from the repo root without a pip install. Not a best practice but intentional.

**Deferred imports** are used heavily to isolate platform-specific dependencies:

```python
# server/mac_screen_capture.py:67-100
try:
    import objc
    from Foundation import NSObject, NSCondition, ...
    from ScreenCaptureKit import SCShareableContent, ...
    _HAS_SCK = True
except Exception as _e:
    _HAS_SCK = False
```

Same pattern for `AppKit` in `server/mac_clipboard.py:22-30`, `Quartz/ApplicationServices` in `server/mac_input_injector.py:54-91`, `python-pam` in `server/pam_auth.py:20-25`, `Xlib.ext.damage` in `server/screen_capture.py:48-53`, and `NvFBCBackend` in `server/screen_capture.py:36-42`. A module-level `_HAS_XXX` flag gates usage at runtime.

**`# noqa: F401` comments** appear on re-exports in `server/platform_backends.py` — this is the only hint of any lint awareness.

## Platform Branching

Single dispatch module: **`server/platform_backends.py`** (62 lines).

```python
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

if IS_MACOS:
    from server.mac_screen_capture import MacScreenCapture as ScreenCapture
else:
    from server.screen_capture import ScreenCapture
```

**Rule:** `server/main.py` imports `ScreenCapture`, `InputInjector`, `XTestInputInjector`, `ClipboardSync`, and `IS_MACOS` only from `server.platform_backends`. New platform-divergent backends should be added here rather than scattering `if sys.platform == "darwin":` checks.

**Where `sys.platform == "darwin"` *does* appear elsewhere:**
- `client/main.py:1154` — `Qt.AA_MacDontSwapCtrlAndMeta` attribute
- `client/viewer.py:408,470,499` — Mac-specific event plumbing
- `client/key_diagnostic.py` and `tools/keydiag.py` — Menlo vs Monospace font selection
- `server/main.py` — `IS_MACOS` gates PAM-mode rejection, systemd-style features, audio/clipboard/uinput dep checks

**`platform.system()`** used in `client/bookmarks.py:26`, `client/usb_forward.py`, and `build_client.py:21` for filesystem paths, USB forwarding backend, and PyInstaller path separators.

**Shell installers** detect platform explicitly:
- `install-server.sh` — Linux-only, `detect_distro()` function parses `/etc/os-release` and switches on `ubuntu|debian|pop|linuxmint|elementary`, `fedora`, `rhel|rocky|almalinux|centos`
- `install-server-macos.sh` — checks `uname = Darwin`, refuses to run on Linux; refuses to run as root; requires Homebrew
- `install-client.sh` — POSIX systems (Mac/Linux)
- `install-client.ps1` — Windows PowerShell

## Error Handling

**Try/except patterns:**

- **Broad `except Exception:`** is common around optional imports and best-effort ops (clipboard polling, monitor detection, xrandr parsing). Errors are logged and degraded behavior continues. Example: `server/screen_capture.py:48-53` (XDamage optional), `server/auth.py:120-122` (user file load).
- **Specific exceptions** used for file/OS errors: `except KeyError` (pwd/grp lookups — `server/pam_auth.py:67`), `except (FileNotFoundError, subprocess.TimeoutExpired)` (`server/clipboard.py:49`), `except OSError` (`server/session_manager.py:43`), `except ValueError` (int parsing in `server/auth.py:196`).

**Raising:**
- Custom errors inherit from `RuntimeError` (`MacScreenCaptureError`, `MacInputInjectorError`)
- `raise RuntimeError("No free X display numbers...")` (`server/session_manager.py:77`) — generic runtime signaling is accepted
- `raise` (bare re-raise) used to propagate after logging: `server/auth.py:75`

**Fail-soft philosophy:** Missing system deps (ffmpeg, pactl, xclip, uinput) log warnings in `check_system_dependencies()` at `server/main.py:980-1000` and continue. The Mac branch (`if IS_MACOS: return`) skips all Linux CLI checks.

**Auth failures:** Always return `False` and log `logger.warning(...)` — never raise. See `server/auth.py:86-91, 149-168`.

## Logging

**Every module does:**

```python
import logging
logger = logging.getLogger(__name__)
```

**Exceptions** (intentional named loggers for top-level services):
- `server/main.py:71` — `logger = logging.getLogger("teraguchi.server")`
- `broker/main.py:37` — `logger = logging.getLogger("teraguchi.broker")`

**Configuration:** Only the three entry-points call `logging.basicConfig`:

```python
# server/main.py:1059, client/main.py:1149, broker/main.py:301
logging.basicConfig(
    level=logging.DEBUG if args.verbose else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
```

**Levels in practice:**
- `logger.info(...)` — lifecycle events (auth success, module init, user added)
- `logger.warning(...)` — recoverable failures (auth failed, missing optional deps, token expired)
- `logger.error(...)` — misconfiguration or hard failure paths (PAM requested but unavailable, file load failure)
- `logger.debug(...)` — wire-level / per-frame detail (used sparingly)
- `logger.exception(...)` — seen occasionally for unexpected exception traces

**%-style formatting preferred** for log messages: `logger.info("Loaded %d users from %s", len(self._users), self._users_file)` — avoids f-string evaluation cost when the level is filtered.

**No structured logging, no JSON logs, no external log shipping.** Logs go to stdout (captured by systemd journal on Linux, `~/Library/Logs/Teraguchi/` on macOS per `install-server-macos.sh`).

## Async vs Sync

**Async:** Used for all network I/O.

- `asyncio` + `websockets` drive the server main loop (`server/main.py`), broker (`broker/main.py`), and pool (`broker/pool.py`)
- `aiohttp` for broker admin HTTP (`broker/admin.py`)
- `aioquic` for optional QUIC transport (`common/quic_transport.py`)
- Client protocol layer is async (`client/protocol.py`)
- ~104 `async def` / `await` sites across the codebase

**Sync:** Used for everything CPU-bound / subsystem-bound.

- Screen capture, video encoding (FFmpeg subprocess), audio capture, input injection, clipboard polling all run in plain `threading.Thread` workers
- Latest-frame buffers are guarded with `threading.Lock` and fed pull-style into the async send loop
- `server/mac_screen_capture.py` wraps ScreenCaptureKit's push-based delegate stream behind a `threading.Lock`-guarded latest-frame buffer so the encoder's pull-style interface still works

**Bridge to Qt:** `client/session.py:33` defines `_Bridge(QObject)` with `Signal(...)` fields. Protocol callbacks (running on `asyncio` threads) marshal into the Qt main thread via these signals. Do not call Qt widgets directly from async callbacks.

## Module Design

- **No `__all__`** declarations anywhere. All non-underscore names are implicit public API.
- **Re-exports via `from X import Y`** used only in `server/platform_backends.py` for platform dispatch (with `# noqa: F401`).
- **`if TYPE_CHECKING:`** used once (`server/session_manager.py:28`) to import `VirtualPenTablet` for annotations without a runtime import cycle.
- **Shebangs (`#!/usr/bin/env python3`)** on the five executable entry points: `server/main.py`, `client/main.py`, `broker/main.py`, `build_client.py`, `tools/keydiag.py`.

## Shell Script Conventions

Installer scripts (`install-server.sh`, `install-server-macos.sh`, `install-client.sh`):

- `#!/usr/bin/env bash` shebang
- `set -euo pipefail` at the top
- `SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"` idiom for self-location
- Color log helpers: `info()`, `ok()`, `warn()`, `err()` using ANSI escapes (`\033[0;31m` etc.)
- Argument parsing via `for arg in "$@"; do case "$arg" in ...`
- Section headers as `# ── Title ─────────...` comment blocks
- `--help|-h` always supported
- Non-root vs root checks via `$EUID` — Linux server installer requires root, Mac server installer refuses root

## Pre-commit / Hooks

**None.** No `.pre-commit-config.yaml`, no `husky`, no git hooks in `.git/hooks/` beyond defaults.

## Build & Packaging

- `pyproject.toml` uses `setuptools` with `build-backend = "setuptools.build_meta"`
- Optional dependency groups: `server` and `client` (`[project.optional-dependencies]`)
- Console scripts: `teraguchi-server` → `server.main:main`, `teraguchi-client` → `client.main:main`
- Package discovery: `include = ["server*", "client*", "common*"]` — note `broker*` and `tools*` are **not** included in wheel builds
- Client distribution built via PyInstaller through `build_client.py` (onedir, PySide6 collect-all)
- No separate dev/test extra in `[project.optional-dependencies]` — dev tools live in `requirements-dev.txt` only

---

*Convention analysis: 2026-04-18*
