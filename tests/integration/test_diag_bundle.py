"""OBS-05 + T-1-05 — diagnostic bundle content + redaction regression.

Plan 01-15 ships `common/diagnostic_bundle.py::build_bundle()` which
produces a `teraguchi-diag-<timestamp>.zip` for bug-report attachment.
Layout is locked by RESEARCH §"Diagnostic bundle (OBS-05) format"
(lines 932-973).

Core regression gate: `test_no_secret_strings_leak`. T-1-05 in the
threat register says "diagnostic bundle exposes secrets". Mitigation is
recursive JSON redaction + inline-text redaction + env-var name
matching. This test plants secrets in env + config + live-state, builds
a bundle, and greps every file in the zip for plaintext. One leak = red
CI.
"""

from __future__ import annotations

import json
import pathlib
import zipfile

import pytest

from common.diagnostic_bundle import build_bundle, _redact_dict, _redact_text


# ---------------------------------------------------------------------------
# Smoke / shape
# ---------------------------------------------------------------------------


def test_build_bundle_writes_zip(tmp_path):
    out = build_bundle(str(tmp_path / "diag.zip"), tier="server")
    assert out.exists()
    assert out.stat().st_size > 0
    # Valid zip
    with zipfile.ZipFile(out) as zf:
        # testzip returns None if archive is valid; first-bad-name string otherwise
        assert zf.testzip() is None


def test_bundle_contains_expected_sections(tmp_path):
    out = build_bundle(str(tmp_path / "diag.zip"), tier="server")
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    # Manifest + each top-level section from RESEARCH §"Diagnostic bundle" layout
    assert "manifest.json" in names
    assert "config/env-vars-redacted.txt" in names
    assert any(n.startswith("state/") for n in names)
    assert any(n.startswith("system/") for n in names)


def test_bundle_tier_in_manifest(tmp_path):
    out = build_bundle(str(tmp_path / "d.zip"), tier="client")
    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["tier"] == "client"
    # Manifest MUST also carry version + timestamp + hostname + python_version
    assert "version" in manifest
    assert "timestamp" in manifest
    assert "hostname" in manifest
    assert "python_version" in manifest


def test_bundle_default_path_uses_home(monkeypatch, tmp_path):
    # Redirect HOME so the default path lands in a sandbox
    monkeypatch.setenv("HOME", str(tmp_path))
    out = build_bundle("", tier="server")
    try:
        # Default path format: <HOME>/teraguchi-diag-<ts>.zip
        assert out.parent == tmp_path
        assert out.name.startswith("teraguchi-diag-")
        assert out.name.endswith(".zip")
    finally:
        if out.exists():
            out.unlink()


# ---------------------------------------------------------------------------
# Redaction primitives (unit-style; live alongside integration for colocation)
# ---------------------------------------------------------------------------


def test_redact_dict_handles_nested():
    data = {
        "user": "alice",
        "password": "hunter2",
        "config": {"api_key": "secret-key-xyz", "ok_field": "visible"},
        # Outer list key is deliberately non-matching so we can inspect
        # nested redaction behaviour. A key containing "token" would get
        # whole-value redacted (see test below).
        "items": [{"token": "t1"}, {"other": "ok"}],
    }
    redacted = _redact_dict(data)
    assert redacted["user"] == "alice"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["config"]["api_key"] == "[REDACTED]"
    assert redacted["config"]["ok_field"] == "visible"
    assert redacted["items"][0]["token"] == "[REDACTED]"
    assert redacted["items"][1]["other"] == "ok"


def test_redact_dict_redacts_whole_value_when_outer_key_matches():
    """When a dict key itself matches a redact substring, the entire
    value (dict, list, whatever) gets replaced — matches the behaviour
    of common.logging._redact_value for consistency.
    """
    data = {"tokens": [{"token": "t1"}, {"other": "ok"}]}
    redacted = _redact_dict(data)
    assert redacted["tokens"] == "[REDACTED]"


def test_redact_text_replaces_inline_values():
    text = 'password=hunter2\napi_key = "abc123"\nlog_level=INFO\n'
    redacted = _redact_text(text)
    assert "hunter2" not in redacted
    assert "abc123" not in redacted
    assert "INFO" in redacted
    assert "[REDACTED]" in redacted


def test_redact_text_handles_toml_quoted_strings():
    text = 'tls_key = "/etc/teraguchi/keys/server.key"\nbroker_secret = "deadbeef"\n'
    redacted = _redact_text(text)
    assert "deadbeef" not in redacted
    assert "server.key" not in redacted


# ---------------------------------------------------------------------------
# T-1-05 REGRESSION GATE
# ---------------------------------------------------------------------------


def test_no_secret_strings_leak(tmp_path, monkeypatch):
    """T-1-05 — secrets planted in env + config + live-state MUST NOT
    appear as plaintext anywhere in the zip.

    If this ever fails, someone added a pathway that bypasses redaction.
    Fix the pathway — don't suppress the test.
    """
    SECRETS = [
        "hunter2-very-secret",
        "broker-hmac-deadbeef",
        "tls-pk-abc123xyz",
        "BOOKMARK-PW-SUPERSECRET",
    ]

    # 1) env — names matching _SECRET_ENV_RE get their values redacted
    monkeypatch.setenv("TERAGUCHI_BROKER_SECRET", SECRETS[1])
    monkeypatch.setenv("TERAGUCHI_TLS_KEY", SECRETS[2])
    monkeypatch.setenv("TERAGUCHI_USER_PASSWORD", SECRETS[0])

    # 2) config file — planted in TOML-style text; _redact_text strips
    cfg = tmp_path / "server.toml"
    cfg.write_text(
        f'# Fake config\n'
        f'password = "{SECRETS[0]}"\n'
        f'tls_key = "{SECRETS[2]}"\n'
        f'broker_secret = "{SECRETS[1]}"\n'
        f'listen_port = 443\n'
    )

    # 3) live state dict — _redact_dict recurses through this
    live = {
        "fsm-states": {
            "client1": {
                "state": "streaming",
                "token": SECRETS[1],
                "password_hash": SECRETS[0],
            },
        },
        "last_hello": {
            "version": "3.0",
            "password_encrypted": SECRETS[3],
            "credentials": {"api_token": SECRETS[2]},
        },
    }

    # 4) Build bundle
    out = build_bundle(
        str(tmp_path / "diag.zip"),
        tier="server",
        config_files=[("server.toml", str(cfg))],
        live_state=live,
    )

    # 5) Greppable extraction — every member decoded best-effort
    with zipfile.ZipFile(out) as zf:
        for name in zf.namelist():
            try:
                content = zf.read(name).decode(errors="ignore")
            except Exception:
                continue
            for secret in SECRETS:
                assert secret not in content, (
                    f"PLAINTEXT SECRET LEAKED in {name}: contains {secret!r}"
                )


def test_bundle_redacts_credentials_in_logs(tmp_path):
    """Defense-in-depth: even a raw log file landing in logs/ must have
    credential-looking lines redacted before inclusion.

    If a caller passes an un-redacted structlog JSON stream that has a
    `password` field unredacted (pre-Plan 06 log, or a third-party
    agent-managed log), `build_bundle` MUST apply a final pass.
    """
    log = tmp_path / "old.log"
    log.write_text(
        '{"event": "auth.request", "user": "alice", "password": "hunter2-leaky"}\n'
        '{"event": "ok", "level": "info"}\n'
    )
    out = build_bundle(
        str(tmp_path / "d.zip"),
        tier="server",
        log_files=[("current.log", str(log))],
    )
    with zipfile.ZipFile(out) as zf:
        data = zf.read("logs/current.log").decode(errors="ignore")
    assert "hunter2-leaky" not in data
    assert "[REDACTED]" in data


def test_bundle_excludes_sensitive_files_if_passed(tmp_path):
    """T-1-05 defense: even if a caller tries to include a *.key /
    *.pem / bookmarks.json via config_files, the module MUST refuse to
    embed their contents (either skip or redact whole-file).
    """
    key = tmp_path / "server.key"
    key.write_text("-----BEGIN PRIVATE KEY-----\nSUPERSECRET-DONT-LEAK\n-----END PRIVATE KEY-----\n")
    bookmarks = tmp_path / "bookmarks.json"
    bookmarks.write_text('{"default": {"password_encrypted": "ENCRYPTED-SECRET-XYZ"}}\n')

    out = build_bundle(
        str(tmp_path / "d.zip"),
        tier="client",
        config_files=[
            ("server.key", str(key)),
            ("bookmarks.json", str(bookmarks)),
        ],
    )
    with zipfile.ZipFile(out) as zf:
        for name in zf.namelist():
            try:
                content = zf.read(name).decode(errors="ignore")
            except Exception:
                continue
            assert "SUPERSECRET-DONT-LEAK" not in content, f"leak in {name}"
            assert "ENCRYPTED-SECRET-XYZ" not in content, f"leak in {name}"


# ---------------------------------------------------------------------------
# CLI hook smoke (argparse only; no service boot)
# ---------------------------------------------------------------------------


def test_server_main_accepts_diag_bundle_flag(tmp_path, monkeypatch):
    """Server CLI: --diag-bundle <path> should parse, build, and
    short-circuit before asyncio/ssl startup.

    The diag path MUST exit before auth/root checks. If someone moves
    the branch past the root-check for PAM mode, exit code becomes 1
    and this test fails.
    """
    from server import main as server_main

    import sys

    out_zip = str(tmp_path / "server-diag.zip")
    argv = ["teraguchi-server", "--diag-bundle", out_zip]
    monkeypatch.setattr(sys, "argv", argv)

    def _fake_exit(code=0):
        raise SystemExit(code or 0)

    monkeypatch.setattr(sys, "exit", _fake_exit)

    with pytest.raises(SystemExit) as e:
        server_main.main()
    assert e.value.code == 0, (
        "server --diag-bundle must short-circuit with exit code 0 "
        "before any auth/root guards run."
    )
    assert pathlib.Path(out_zip).exists()


def test_client_main_accepts_diag_bundle_flag(tmp_path, monkeypatch):
    """Client CLI: --diag-bundle <path> should parse, build, and
    short-circuit before QApplication is constructed."""
    import sys
    from client import app as client_app

    out_zip = str(tmp_path / "client-diag.zip")
    argv = ["teraguchi-client", "--diag-bundle", out_zip]
    monkeypatch.setattr(sys, "argv", argv)

    def _fake_exit(code=0):
        raise SystemExit(code or 0)

    monkeypatch.setattr(sys, "exit", _fake_exit)

    # Fail loud if QApplication gets constructed — the diag path must
    # exit first.
    def _boom(*a, **kw):
        raise AssertionError("QApplication was constructed — diag path did not short-circuit")

    monkeypatch.setattr(client_app, "QApplication", _boom)

    with pytest.raises(SystemExit) as e:
        client_app.main()
    assert e.value.code == 0
    assert pathlib.Path(out_zip).exists()
