"""STAB-01 — client bookmarks XOR round-trip + platform-path resolution.

The XOR scheme is deliberately weak (obfuscation, not encryption). Phase 6
migrates credentials to macOS Keychain via SEC-06. Until then, round-trip
coverage prevents silent breakage during refactors.

Target-read divergence notes:
- ``client/bookmarks.py`` uses ``_encrypt_password`` / ``_decrypt_password``
  (module-level functions), NOT ``_xor_encrypt`` / ``_xor_decrypt``.
- Class is ``BookmarkManager``, not ``Bookmarks``.
- ``_get_config_dir()`` calls ``.mkdir(parents=True, exist_ok=True)`` as a
  side effect — tests monkeypatch ``Path.home`` to point at ``tmp_path``
  so the directory is created inside the per-test temp dir.
"""
import json
import platform as _platform
from pathlib import Path

import pytest

from client.bookmarks import (
    _decrypt_password,
    _encrypt_password,
    _get_config_dir,
)


# ─── XOR round-trip ──────────────────────────────────────────────────────


@pytest.mark.parametrize("password", [
    "hunter2",
    "correcthorsebatterystaple",
    "p@ssw0rd!#$%^&*()",
    "日本語パスワード",                # UTF-8 multi-byte
    "a" * 256,                         # long
    "!",                               # single char
])
def test_xor_encrypt_decrypt_roundtrip(password):
    """Every password type must round-trip cleanly. If this regresses, every
    saved bookmark silently loses its password on next load."""
    ciphertext = _encrypt_password(password)
    assert ciphertext != password, "ciphertext must differ from plaintext"
    assert _decrypt_password(ciphertext) == password


def test_encrypt_empty_password_returns_empty():
    assert _encrypt_password("") == ""


def test_decrypt_empty_password_returns_empty():
    assert _decrypt_password("") == ""


def test_decrypt_garbage_returns_empty_not_crashes():
    """The decrypt path must never propagate an exception — a corrupt
    bookmarks.json should degrade to 'no password', not crash the client."""
    assert _decrypt_password("not-valid-base64-!!!") == ""


def test_encrypt_same_password_produces_same_ciphertext():
    """Machine key is deterministic within a run, so encryption is too.
    Guard against someone accidentally injecting randomness (which would
    make saved passwords unrecoverable)."""
    a = _encrypt_password("hunter2")
    b = _encrypt_password("hunter2")
    assert a == b


# ─── Platform-appropriate config dir ─────────────────────────────────────


def test_config_dir_darwin(monkeypatch, tmp_path):
    monkeypatch.setattr(_platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    result = _get_config_dir()
    expected = tmp_path / "Library" / "Application Support" / "Teraguchi"
    assert result == expected
    # Side effect: the directory is created
    assert result.exists() and result.is_dir()


def test_config_dir_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(_platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    result = _get_config_dir()
    expected = tmp_path / ".config" / "teraguchi"
    assert result == expected
    assert result.exists() and result.is_dir()


def test_config_dir_windows(monkeypatch, tmp_path):
    """Windows path uses ``%APPDATA%`` if set, else ``Path.home()``. Windows is
    Phase 3 (post-v1) so this is a scaffolding-regression guard only."""
    monkeypatch.setattr(_platform, "system", lambda: "Windows")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    result = _get_config_dir()
    expected = tmp_path / "appdata" / "Teraguchi"
    assert result == expected
    assert result.exists() and result.is_dir()


# ═══════════════════════════════════════════════════════════════════════
# Phase 3 — DISP-01 bookmark migration + monitor_mode persistence
# Plan 03-02 implements the migration block in BookmarkManager._load.
# Pitfall 9 — pre-Phase-3 bookmarks MUST load with safe defaults.
# T-03-05 (STRIDE) — hand-edited bookmark monitor_mode must whitelist.
# ═══════════════════════════════════════════════════════════════════════


def _build_manager(tmp_path, monkeypatch):
    """Point BookmarkManager at a temp config dir.

    _get_config_dir() calls ``.mkdir(parents=True, exist_ok=True)`` so we
    redirect ``Path.home`` at the module root. Match the pattern in
    test_config_dir_linux so the Teraguchi config dir lands under tmp.
    """
    monkeypatch.setattr(_platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Compute the bookmarks path and return it with the constructed manager.
    # Importing after monkeypatching catches any module-import-time caching.
    from client.bookmarks import BookmarkManager
    mgr = BookmarkManager()
    return mgr, mgr._bookmarks_file


def _seed_bookmarks(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_monitor_mode_persistence(tmp_path, monkeypatch):
    """DISP-01 — bookmark stores monitor_mode and reloads cleanly.

    Save a Phase-3-shape bookmark with pick_one + DP-1 + id=2, reload, and
    confirm all three fields round-trip. This verifies BookmarkManager's
    from_dict + to_dict path cover the Phase 3 fields.
    """
    monkeypatch.setattr(_platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from client.bookmarks import BookmarkManager

    mgr = BookmarkManager()
    bid = mgr.add(
        name="rocky", host="rocky.tailnet", port=443,
        username="randy", password="hunter2",
        destination_kind="linux",
        swap_cmd_ctrl=True,
        monitor_mode="pick_one",
        picked_monitor_id=2,
        picked_monitor_name="DP-1",
    )
    # Reload from disk — a fresh manager must read back the same values.
    mgr2 = BookmarkManager()
    prof = mgr2.get(bid)
    assert prof is not None
    assert prof.monitor_mode == "pick_one"
    assert prof.picked_monitor_id == 2
    assert prof.picked_monitor_name == "DP-1"


def test_phase2_bookmark_migrates_to_phase3(tmp_path, monkeypatch):
    """Pitfall 9 — pre-Phase-3 bookmark loads with safe defaults.

    Seed a JSON file that only has Phase 2 fields (no monitor_mode / no
    clipboard toggles). Loading via BookmarkManager must:
    - produce a ConnectionProfile with Phase 3 defaults (mirror_all +
      all-on clipboard toggles) because the dataclass defaults fill in.
    - trigger a _save() pass so subsequent reads see the new shape
      persisted — prevents the "user flips a toggle but it never
      sticks because the field was never in the JSON" bug.
    """
    monkeypatch.setattr(_platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from client.bookmarks import BookmarkManager

    # Build the Phase-2-shape JSON before constructing the manager.
    cfg_dir = tmp_path / ".config" / "teraguchi"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "bookmarks.json"
    _seed_bookmarks(path, {
        "bm-1": {"host": "rocky", "port": 443, "username": "randy",
                 "destination_kind": "linux", "swap_cmd_ctrl": True},
    })

    mgr = BookmarkManager()
    prof = mgr._profiles["bm-1"]
    assert prof.monitor_mode == "mirror_all"
    assert prof.picked_monitor_id == -1
    assert prof.picked_monitor_name == ""
    assert prof.clipboard_text_c2s is True
    assert prof.clipboard_text_s2c is True
    assert prof.clipboard_image_c2s is True
    assert prof.clipboard_image_s2c is True
    # Migration fires _save() — the new JSON shape must carry Phase 3 fields.
    reloaded = json.loads(path.read_text())
    assert "monitor_mode" in reloaded["bm-1"]
    assert "clipboard_text_c2s" in reloaded["bm-1"]


def test_invalid_monitor_mode_falls_back_to_mirror_all(tmp_path, monkeypatch):
    """T-03-05 (STRIDE) — hand-edited bookmark with bogus monitor_mode.

    A bookmark JSON that declares ``monitor_mode: "rce"`` is tampering
    with the wire-shape enum. BookmarkManager must whitelist to
    single/mirror_all/pick_one; anything else reverts to mirror_all
    + structlog warning (no exception, no surprise behavior).
    """
    monkeypatch.setattr(_platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from client.bookmarks import BookmarkManager

    cfg_dir = tmp_path / ".config" / "teraguchi"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "bookmarks.json"
    _seed_bookmarks(path, {
        "bm-1": {
            "host": "rocky", "port": 443, "username": "randy",
            "destination_kind": "linux", "swap_cmd_ctrl": True,
            "monitor_mode": "rce",
            "picked_monitor_id": -1,
            "picked_monitor_name": "",
            "clipboard_text_c2s": True,
            "clipboard_text_s2c": True,
            "clipboard_image_c2s": True,
            "clipboard_image_s2c": True,
        },
    })

    mgr = BookmarkManager()
    prof = mgr._profiles["bm-1"]
    assert prof.monitor_mode == "mirror_all"
    # The migration also rewrites the file with the sanitized value.
    reloaded = json.loads(path.read_text())
    assert reloaded["bm-1"]["monitor_mode"] == "mirror_all"
