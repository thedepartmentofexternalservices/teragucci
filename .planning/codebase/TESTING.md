# Testing Patterns

**Analysis Date:** 2026-04-18

## Summary

**This project has no automated test suite.** No tests, no test framework configuration, no CI, no mocking. Verification is entirely manual, via runtime smoke tests and purpose-built diagnostic scripts.

This is the current state — document it accurately and plan accordingly. Any new phase adding tests is building testing infrastructure from scratch.

## Test Framework

**Declared dev dependency (unused):**
- `pytest>=7.0` listed in `requirements-dev.txt:5`

**Evidence of use:** None.
- No `test_*.py` or `*_test.py` files exist anywhere in `server/`, `client/`, `common/`, `broker/`, `tools/`, or at the project root (venv `.venv/lib/python3.14/site-packages/` tests for numpy/PIL/etc. are excluded)
- No `tests/` directory
- No `[tool.pytest.ini_options]` / `[pytest]` section in `pyproject.toml`
- No `conftest.py` anywhere in the project
- No `pytest.ini`, `tox.ini`, or `setup.cfg`
- Zero imports of `pytest`, `unittest`, `mock`, or `unittest.mock` in project source

**`unittest` is not used** either. The project simply ships without tests.

## Test Directory Layout

**Does not exist.** There is no convention to document. When tests are added, the Python-ecosystem-default options are:

- **Co-located** — `server/test_auth.py` next to `server/auth.py`. Package discovery in `pyproject.toml` currently uses `include = ["server*", "client*", "common*"]`, which would ship tests inside the wheel. Would need to tighten to e.g. `exclude = ["*.tests", "*.tests.*"]`.
- **Separate top-level `tests/`** — `tests/server/test_auth.py`, mirroring the source tree. Keeps tests out of shipped packages cleanly. This is the more conventional choice for this project's packaging.

Pick one before adding the first test.

## Mocking

**No patterns established.** Target areas that will need mocks when tests arrive:

- `subprocess.run` / `subprocess.Popen` — ffmpeg encoder, xclip/xsel, xrandr, pactl, usbip (heavily used across `server/video_encoder.py`, `server/clipboard.py`, `server/screen_capture.py`, `server/audio_capture.py`, `server/usb_passthrough.py`)
- `websockets.serve` / `websockets.connect` — server, client, broker socket paths
- `pam.pam()` — `server/pam_auth.py:33` (would use `unittest.mock.patch` since the module sets a global `PAM_AVAILABLE` flag)
- `pwd.getpwnam` / `grp.getgrall` — `server/pam_auth.py:56-57`, `server/auth.py:99-103`
- `mss.mss()` — Linux screen capture path
- PyObjC frameworks (`ScreenCaptureKit`, `Quartz`, `AppKit`, `CoreMedia`) — Mac backends. These are imported under `try/except` with `_HAS_SCK` / `_HAS_CG` / `_HAS_APPKIT` flags, so tests can import the modules on Linux CI and exercise the error paths without pyobjc installed.
- `Xlib.ext.damage`, `python-xlib` — also guarded with `_HAS_XLIB_DAMAGE`

**Platform-dispatch testing:** `server/platform_backends.py` makes its choice at import time from `sys.platform`. Tests that want to exercise both branches will need to reload the module with `sys.platform` monkeypatched (or factor the dispatch into a function).

## Fixtures and Factories

**None.** No fixtures, no factory functions, no sample data files.

When adding tests, high-value fixtures would be:
- A `QualitySettings` factory (dataclass in `common/messages.py:184`) for encoder tests
- A fake BGRA frame generator (numpy array shaped `(H, W, 4)`) for `VideoEncoder` / `ScreenCapture` round-trips
- A mock `PAMAuthenticator` for `Authenticator` tests
- A temp-dir `users.json` fixture for local-auth tests (`server/auth.py:108-128` reads/writes `~/.config/teraguchi/users.json`)

## Coverage

**Not measured.** No `coverage.py`, `pytest-cov`, or `.coveragerc`. No coverage target declared.

## CI Integration

**None.**
- No `.github/workflows/` directory
- No `.gitlab-ci.yml`, `.circleci/`, `.travis.yml`, or `azure-pipelines.yml`
- No `.buildkite/`
- No pre-push or pre-commit hooks

Every commit reaches `main`/`dev` without any automated check.

## Manual Test / Diagnostic Scripts

The project has a **diagnostic-tool culture** instead of unit tests. These are executable scripts for humans to verify behavior on live hardware:

**`tools/keydiag.py`** (~430 lines)
- Shebang: `#!/usr/bin/env python3`
- Docstring: "Teraguchi Keystroke Diagnostic Tool — Shows the full key translation path: Physical key → Qt key code → Wire message → X11 keysym → X11 keycode"
- Two modes via argparse:
  - `python tools/keydiag.py` — Qt window that captures real keystrokes and dumps the translation chain
  - `python tools/keydiag.py --server :10` — attaches to an X11 display and dumps received scancodes
- Used to verify Flame hotkeys survive the Mac → Linux key translation end-to-end
- Contains its own `QT_KEY_NAMES` reverse-lookup dict and a `QT_TO_XKEYSYM` map that **must stay in sync with `server/xtest_injector.py`** — comment at `tools/keydiag.py:41` flags this

**`client/key_diagnostic.py`** (~7 KB)
- In-app diagnostic widget (distinct from the standalone `tools/keydiag.py`). Exposed from the client UI for users to verify their keyboard works against the server.

**`server/setup_uinput.sh`**
- Shell utility that `modprobe`s `uinput` and configures the permissions/udev rule. Run during install or manually when `/dev/uinput` is missing (warning logged in `server/main.py:999-1000`).

**`server/nvfbc/nvfbc_capture` (C helper, built from `nvfbc_capture.c`)**
- Built per-host by the Linux installer (`install-server.sh`) via `server/nvfbc/Makefile`. Presence/absence is detected at runtime by `server/screen_capture.py:37` (`_nvfbc_helper_available()`); absence falls back to mss.

**Runtime self-checks inside `server/main.py`:**
- `check_system_dependencies()` at line 980 — warns on missing `ffmpeg`, `pactl`, `xclip`/`xsel`, `/dev/uinput`. Short-circuits on macOS (`if IS_MACOS: return`).
- `check_ffmpeg_available()` and `detect_encoders()` in `server/video_encoder.py` — probed at startup and logged; no exit on failure
- `check_audio_available()` in `server/audio_capture.py`
- `quic_available()` in `common/quic_transport.py` — optional transport

**Runtime self-checks inside `server/main.py`:**
- macOS + `--auth-mode pam` → log warning, force `--auth-mode none` (`server/main.py:1070-1073`)
- `--auth-mode pam` + non-root → `logger.error(...)` + `sys.exit(1)` (`server/main.py:1085-1089`)

These are diagnostics, not tests — they validate the environment at startup rather than asserting on code behavior.

## Manual Run Commands (how developers currently verify changes)

No `make test`, no `pytest` target. The verification loop is:

```bash
# Launch the server (Linux, PAM mode)
sudo python -m server.main --auth-mode pam --verbose

# Or Mac, single-user legacy mode
python -m server.main --auth-mode none --verbose

# Launch the broker
python -m broker.main

# Launch the client against a running server
./teraguchi --host <server-ip> --port 443 --verbose
# Or directly:
python -m client.main --host <server-ip> --port 443 --verbose

# Verify key translation end-to-end
python tools/keydiag.py
```

The shell wrapper at `./teraguchi` sources `.venv/bin/activate` and launches `python -m client.main`.

## Test Types

- **Unit tests:** None.
- **Integration tests:** None.
- **E2E tests:** Manual only — connect a real client to a real server and eyeball the behavior. The diagnostic scripts above are the closest thing to E2E verification.
- **Performance tests:** Encoder output observed manually via `--verbose` logs; `common/jitter_buffer.py` and `BandwidthEstimator` in `common/udp_transport.py` log stats but are not asserted.

## Recommendations for Future Testing Phases

If a phase proposes adding tests, these are the natural seams — pre-existing abstractions that would mock cleanly:

1. **`common/messages.py`** — pure dataclass serialization. `parse_message()`, `encode_video_header()` / `decode_video_header()`, `encode_jpeg_header()` / `decode_jpeg_header()`, `encode_audio_header()` / `decode_audio_header()` are pure functions with no side effects. Lowest-effort starting point.
2. **`common/keymap.py`** — pure lookup table + `qt_key_to_linux_scancode()`. Trivial to test.
3. **`QualitySettings.effective_crf/preset/chroma/fps`** (`common/messages.py:211-240`) — pure functions of the dataclass, ideal for table-driven tests.
4. **`server/auth.py` `Authenticator`** — local-mode challenge/response and `verify_token()` HMAC logic are pure; PAM mode needs mocking.
5. **`broker/tokens.py`** — token generation + verification. Pure crypto, easy to test.

Platform-dispatched modules (`server/mac_*.py`, `server/screen_capture.py`, `server/input_injector.py`, `server/video_encoder.py`) and the async `websockets` paths are expensive to test and should come later.

---

*Testing analysis: 2026-04-18*
