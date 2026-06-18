"""Diagnostic bundle export (OBS-05).

Produces a `teraguchi-diag-<timestamp>.zip` a user can attach to a
GitHub issue. Layout + redaction rules are locked by
RESEARCH §"Diagnostic bundle (OBS-05) format" (lines 932-973):

    teraguchi-diag-<ts>.zip
    ├── manifest.json              (version, timestamp, tier, hostname, OS, Python)
    ├── config/
    │   ├── <name>.toml            (redacted)
    │   └── env-vars-redacted.txt  (TERAGUCHI_* env vars; secrets redacted)
    ├── logs/
    │   └── <name>.log             (last 10 MB, re-scrubbed defense-in-depth)
    ├── state/
    │   ├── fsm-states.json
    │   ├── health-stats.json
    │   ├── queue-depths.json
    │   └── encoder-state.json
    ├── system/
    │   ├── python-packages.txt    (pip freeze)
    │   ├── ffmpeg-version.txt
    │   ├── gpu.txt                (nvidia-smi on Linux, system_profiler on macOS)
    │   ├── uname.txt
    │   └── uptime.txt
    └── protocol/
        └── last-hello.json        (optional)

Redaction rules (T-1-05 mitigation):

* TLS private keys → ``[REDACTED: TLS_PRIVATE_KEY]`` (whole-file refuse
  for ``*.key`` / ``*.pem`` / ``*.crt`` passed via ``config_files``)
* Broker HMAC secrets → ``[REDACTED: BROKER_HMAC_SECRET]``
* ``users.json`` password hashes → ``[REDACTED: USER_PASSWORD_HASH]``
* ``bookmarks.json`` ``password_encrypted`` → ``[REDACTED: BOOKMARK_PASSWORD]``
  (whole-file refuse — bookmarks never enter the bundle)
* Env var names matching ``*_PASSWORD|*_SECRET|*_KEY|*_TOKEN|*_PIN``
  → value replaced with ``[REDACTED]``
* Dict keys containing ``password``, ``credential``, ``token``, ``secret``,
  ``key``, ``pin`` (case-insensitive substring) → value ``[REDACTED]``

Regression gate: ``tests/integration/test_diag_bundle.py::
test_no_secret_strings_leak`` plants secrets in env + config + live-state,
then greps every zip member for plaintext. One leak = red CI.

Intentional design choices:

* stdlib only — ``zipfile``, ``subprocess``, ``json``, ``pathlib``
* Subprocess probes never raise — missing binaries return ``[not-available]``
* Bundle is structured-deterministic (contents reflect live runtime state,
  timestamps naturally differ across invocations)
* Defense-in-depth: ``_redact_text`` runs over EVERY bundled log/config file,
  even if upstream structlog already redacted (Plan 06). If someone ever
  introduces a path that bypasses the structlog processor chain (Pitfall 2
  in RESEARCH), this catches it anyway.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import platform
import re
import socket
import subprocess
import sys
import zipfile
from typing import Any, Optional

logger = logging.getLogger("teraguchi.diagnostic_bundle")


# ---------------------------------------------------------------------------
# Redaction primitives
# ---------------------------------------------------------------------------

# Env var NAME patterns that should have their VALUE redacted.
# Case-insensitive. Matches *_PASSWORD / *_SECRET / *_KEY / *_TOKEN / *_PIN.
_SECRET_ENV_RE = re.compile(
    r".*(_PASSWORD|_SECRET|_KEY|_TOKEN|_PIN).*",
    re.IGNORECASE,
)

# Dict-key substrings (case-insensitive) whose values should be redacted.
# Kept in sync with common.logging.REDACT_KEYS for consistency across the
# log + diag pipelines (T-1-04 + T-1-05 share the same threat surface).
_JSON_REDACT_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "credential",
        "token",
        "secret",
        "key",
        "pin",
    }
)

# Whole-file refuse list — these never go in the bundle regardless of what
# the caller passes via `config_files`. Pattern-matched against the
# on-disk file name (NOT the in-zip name). TLS private keys, PEM bundles,
# certificates, and bookmarks (which hold XOR-encrypted passwords) all
# qualify — even the encrypted form leaks information under known-plaintext.
_REFUSE_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.crt$", re.IGNORECASE),
    re.compile(r"bookmarks\.json$", re.IGNORECASE),
    re.compile(r"users\.json$", re.IGNORECASE),
)

# Text redaction patterns for TOML/INI/free-form configs. Runs AFTER any
# structured-JSON redaction — belt-and-suspenders.
_TEXT_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # `password = "value"` or `password="value"` or `password: value`
    (
        re.compile(
            r"^(\s*\S*(?:password|secret|token|key|credential|pin)\S*\s*[=:]\s*)"
            r'(["\']?)[^\n"\']*\2',
            re.IGNORECASE | re.MULTILINE,
        ),
        r"\1[REDACTED]",
    ),
    # Log-JSON style: "password": "hunter2". Accepts either the full key
    # word embedded in the JSON field name (e.g. "api_key") or any of the
    # explicit sub-matches. Consumes the closing quote of the value as
    # part of the replacement so the output stays syntactically valid.
    (
        re.compile(
            r'("(?:[^"]*(?:password|secret|token|key|credential|pin)[^"]*)"\s*:\s*)'
            r'"[^"]*"',
            re.IGNORECASE,
        ),
        r'\1"[REDACTED]"',
    ),
)


def _redact_dict(data: Any) -> Any:
    """Recursively redact password/secret/token/key fields.

    Mirrors ``common.logging._redact_value`` behaviour so the bundle's
    state + protocol JSON gets the same treatment as structlog emits.

    Never mutates input. Returns a new structure.
    """
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            lowered = str(k).lower()
            if any(needle in lowered for needle in _JSON_REDACT_KEYS):
                out[k] = "[REDACTED]"
            else:
                out[k] = _redact_dict(v)
        return out
    if isinstance(data, list):
        return [_redact_dict(x) for x in data]
    if isinstance(data, tuple):
        return tuple(_redact_dict(x) for x in data)
    return data


def _redact_text(text: str, *, category: str = "") -> str:
    """Scan text for secret-looking lines and redact the value portion.

    Hook for file types where structured redaction isn't applicable
    (TOML configs, raw log files that may have skipped the structlog
    processor chain). Defense-in-depth — never the only line of defense.

    ``category`` is a free-form tag for logging ("server.toml", "old.log").
    """
    for pat, repl in _TEXT_REDACT_PATTERNS:
        text = pat.sub(repl, text)
    return text


def _should_refuse_file(src: str) -> bool:
    """True if `src` matches a whole-file refuse pattern.

    Applied to every ``config_files`` entry. If matched, the bundle embeds
    a sentinel string instead of the file contents.
    """
    name = pathlib.Path(src).name
    return any(pat.search(name) for pat in _REFUSE_FILE_PATTERNS)


def _refuse_sentinel(src: str) -> str:
    """Sentinel text inserted in place of a refused file's contents."""
    name = pathlib.Path(src).name.lower()
    if name.endswith((".key", ".pem")):
        return "[REDACTED: TLS_PRIVATE_KEY — whole-file refused by diagnostic_bundle]\n"
    if name.endswith(".crt"):
        return "[REDACTED: TLS_CERTIFICATE — whole-file refused by diagnostic_bundle]\n"
    if name == "bookmarks.json":
        return "[REDACTED: BOOKMARK_PASSWORD store — whole-file refused by diagnostic_bundle]\n"
    if name == "users.json":
        return "[REDACTED: USER_PASSWORD_HASH store — whole-file refused by diagnostic_bundle]\n"
    return "[REDACTED: sensitive file — whole-file refused by diagnostic_bundle]\n"


# ---------------------------------------------------------------------------
# Subprocess probes (never raise — missing binaries return a sentinel)
# ---------------------------------------------------------------------------


def _probe_subprocess(argv: list[str], *, timeout: float = 5.0) -> str:
    """Run a subprocess probe; return stdout (+ stderr as comment) or
    a sentinel. Never raises.

    Matches the shape used elsewhere in the codebase
    (server/video_encoder.py, server/screen_capture.py).
    """
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        tail = f"\n---STDERR---\n{result.stderr}" if result.stderr else ""
        return result.stdout + tail
    except FileNotFoundError:
        return f"[not-available: {argv[0]}]\n"
    except subprocess.TimeoutExpired:
        return f"[timeout: {' '.join(argv)}]\n"
    except Exception as e:  # noqa: BLE001 — diag path must not break
        return f"[probe error: {e}]\n"


def _env_vars_redacted() -> str:
    """Return a newline-separated dump of TERAGUCHI_* env vars with secrets
    redacted by name."""
    lines = []
    for k, v in sorted(os.environ.items()):
        if not k.startswith("TERAGUCHI_"):
            continue
        if _SECRET_ENV_RE.match(k):
            lines.append(f"{k}=[REDACTED]")
        else:
            lines.append(f"{k}={v}")
    return ("\n".join(lines) + "\n") if lines else "[no TERAGUCHI_* env vars set]\n"


def _manifest(tier: str) -> dict[str, Any]:
    return {
        "version": "3.0.0",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tier": tier,
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "platform": platform.platform(),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "cwd": str(pathlib.Path.cwd()),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_bundle(
    out_path: str,
    *,
    tier: str = "server",
    config_files: Optional[list[tuple[str, str]]] = None,
    log_files: Optional[list[tuple[str, str]]] = None,
    live_state: Optional[dict[str, Any]] = None,
) -> pathlib.Path:
    """Build the diagnostic zip at ``out_path``. Returns the final path.

    Args:
        out_path: Where to write the zip. Falsy → derives a default at
            ``~/teraguchi-diag-<timestamp>.zip``.
        tier: ``"server" | "client" | "broker"`` — recorded in manifest.
        config_files: List of ``(display_name, path_on_disk)`` tuples for
            the ``config/`` section. Files matching ``_REFUSE_FILE_PATTERNS``
            (``*.key``, ``*.pem``, ``*.crt``, ``bookmarks.json``,
            ``users.json``) get a sentinel instead of their contents.
        log_files: List of ``(display_name, path_on_disk)`` tuples for the
            ``logs/`` section. Last 10 MB of each file is included, with
            a defense-in-depth ``_redact_text`` pass before zip write.
        live_state: Runtime state dict. Recognized top-level keys:
            ``fsm-states``, ``health-stats``, ``queue-depths``,
            ``encoder-state``, ``last_hello``. Each is recursively redacted
            via ``_redact_dict`` before JSON serialization.

    Returns:
        ``pathlib.Path`` pointing at the written zip.
    """
    if not out_path:
        ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%SZ")
        out_path = str(pathlib.Path.home() / f"teraguchi-diag-{ts}.zip")
    out = pathlib.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        # --- manifest ---
        zf.writestr("manifest.json", json.dumps(_manifest(tier), indent=2))

        # --- config/ ---
        zf.writestr("config/env-vars-redacted.txt", _env_vars_redacted())
        for name, src in (config_files or []):
            if _should_refuse_file(src):
                zf.writestr(f"config/{name}", _refuse_sentinel(src))
                continue
            try:
                text = pathlib.Path(src).read_text()
                zf.writestr(f"config/{name}", _redact_text(text, category=name))
            except FileNotFoundError:
                zf.writestr(f"config/{name}", f"[not-found: {src}]\n")
            except Exception as e:  # noqa: BLE001
                zf.writestr(f"config/{name}", f"[read error: {e}]\n")

        # --- logs/ (last 10 MB, scrubbed defense-in-depth) ---
        LOG_TAIL_BYTES = 10 * 1024 * 1024
        for name, src in (log_files or []):
            try:
                p = pathlib.Path(src)
                data = p.read_bytes()[-LOG_TAIL_BYTES:]
                text = data.decode("utf-8", errors="replace")
                zf.writestr(f"logs/{name}", _redact_text(text, category=name))
            except FileNotFoundError:
                zf.writestr(f"logs/{name}", f"[not-found: {src}]\n")
            except Exception as e:  # noqa: BLE001
                zf.writestr(f"logs/{name}", f"[read error: {e}]\n")

        # --- state/ ---
        state = live_state or {}
        # Every known section lands in state/, missing ones render as {}.
        for section in ("fsm-states", "health-stats", "queue-depths", "encoder-state"):
            content = state.get(section, {})
            zf.writestr(
                f"state/{section}.json",
                json.dumps(_redact_dict(content), indent=2, default=str),
            )

        # --- system/ ---
        zf.writestr(
            "system/python-packages.txt",
            _probe_subprocess([sys.executable, "-m", "pip", "freeze"], timeout=10.0),
        )
        zf.writestr("system/ffmpeg-version.txt", _probe_subprocess(["ffmpeg", "-version"]))
        if platform.system() == "Darwin":
            zf.writestr(
                "system/gpu.txt",
                _probe_subprocess(["system_profiler", "SPDisplaysDataType"], timeout=10.0),
            )
        else:
            zf.writestr("system/gpu.txt", _probe_subprocess(["nvidia-smi"]))
        zf.writestr("system/uname.txt", _probe_subprocess(["uname", "-a"]))
        zf.writestr("system/uptime.txt", _probe_subprocess(["uptime"]))

        # --- protocol/ ---
        last_hello = state.get("last_hello")
        if last_hello is not None:
            zf.writestr(
                "protocol/last-hello.json",
                json.dumps(_redact_dict(last_hello), indent=2, default=str),
            )

    try:
        size = out.stat().st_size
    except OSError:
        size = -1
    logger.info("diag.bundle_created path=%s size=%d", str(out), size)
    return out
