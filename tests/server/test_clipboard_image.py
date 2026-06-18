"""Phase 3 D-13 / D-14 / D-16 — Linux clipboard PNG image path.

Plan 03-06 implementation of Plan 03-01 Wave 0 RED skeletons. Covers
:func:`validate_png_payload` (magic byte + 64 MB cap + PIL.verify) and
:meth:`ClipboardSync.set_clipboard_image` (xclip subprocess +
``_last_image_hash`` echo suppression).

Defense-in-depth: every trust boundary revalidates, so ``validate_png_payload``
runs BOTH before ``xclip -i`` (prevent malformed bytes leaking into
the system clipboard) AND after ``xclip -o`` (prevent malformed bytes
leaking into the client via broadcast).
"""
from __future__ import annotations

import hashlib
import subprocess

import pytest


# ──────────────────────────────────────────────────────────────────
# validate_png_payload
# ──────────────────────────────────────────────────────────────────


def test_validate_png_payload_accepts_valid_png(fixture_png):
    """D-16 — 8-byte PNG magic signature + PIL.verify accepts a real PNG."""
    from server.clipboard import validate_png_payload
    assert validate_png_payload(fixture_png) is True


def test_validate_png_payload_rejects_magic_mismatch():
    """D-16 — non-PNG payloads rejected with structlog warning."""
    from server.clipboard import validate_png_payload
    # JPEG SOI marker — not a PNG.
    assert validate_png_payload(b"\xff\xd8\xff\xe0JPEG fake") is False
    # Totally wrong bytes.
    assert validate_png_payload(b"not a png at all") is False
    # Empty.
    assert validate_png_payload(b"") is False
    # Truncated magic (< 8 bytes).
    assert validate_png_payload(b"\x89PNG") is False


def test_validate_png_payload_rejects_oversize():
    """D-14 — 64 MB + 1 byte payload rejected by size cap."""
    from server.clipboard import PNG_MAGIC, PNG_MAX_BYTES, validate_png_payload
    # Build a buffer past the cap — magic-byte correct but oversized.
    # We don't need a valid PIL-decodable payload here; the size check
    # must fire BEFORE PIL.verify (ordered short-circuit).
    oversized = PNG_MAGIC + b"\x00" * (PNG_MAX_BYTES - len(PNG_MAGIC) + 1)
    assert len(oversized) == PNG_MAX_BYTES + 1
    assert validate_png_payload(oversized) is False


def test_validate_png_payload_rejects_corrupt_internals(fixture_png):
    """D-16 — PIL.Image.verify() failure path rejects corrupt-but-magic-OK bytes."""
    from server.clipboard import PNG_MAGIC, validate_png_payload
    # Magic-byte correct but PIL.verify will raise on the garbage chunks.
    corrupt = PNG_MAGIC + b"\x00\x00\x00\x00corruptrestOfPNG" * 4
    assert validate_png_payload(corrupt) is False
    # Sanity: the real fixture_png still passes through this code path.
    assert validate_png_payload(fixture_png) is True


# ──────────────────────────────────────────────────────────────────
# set_clipboard_image — xclip subprocess invocation + echo cache
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def _xclip_clipboard_sync(monkeypatch):
    """Build a ``ClipboardSync`` with ``_tool='xclip'`` forced + env probe bypassed."""
    from server import clipboard as clipboard_module

    def _fake_tool(_self):
        return "xclip"

    monkeypatch.setattr(
        clipboard_module.ClipboardSync, "_find_clipboard_tool", _fake_tool,
    )
    return clipboard_module.ClipboardSync()


def test_set_clipboard_image_invokes_xclip_image_png(
    fixture_png, monkeypatch, _xclip_clipboard_sync,
):
    """D-13 — ``set_clipboard_image`` invokes ``xclip -t image/png -i`` with raw bytes."""
    sync = _xclip_clipboard_sync

    captured = {}

    class _FakeProc:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

        def communicate(self, input=None, timeout=None):
            captured["input"] = input
            captured["timeout"] = timeout
            return (b"", b"")

    def _fake_popen(cmd, **kwargs):
        return _FakeProc(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    sync.set_clipboard_image(fixture_png)

    # xclip command assembled correctly.
    assert captured["cmd"][:5] == [
        "xclip", "-selection", "clipboard", "-t", "image/png",
    ]
    assert captured["cmd"][-1] == "-i"
    # Raw PNG bytes piped verbatim — no decode/encode round-trip that
    # could strip the alpha channel (Pitfall 5).
    assert captured["input"] == fixture_png
    # Echo-suppression cache updated with sha256 of what we wrote.
    assert sync._last_image_hash == hashlib.sha256(fixture_png).digest()


def test_set_clipboard_image_rejects_bad_magic_before_xclip(monkeypatch, _xclip_clipboard_sync):
    """D-16 defense-in-depth — malformed PNG never reaches xclip."""
    sync = _xclip_clipboard_sync
    popen_calls = []

    def _fake_popen(cmd, **kwargs):
        popen_calls.append(cmd)
        raise AssertionError("xclip must NOT be invoked on invalid payload")

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    sync.set_clipboard_image(b"not a png")
    assert popen_calls == []
    # Echo cache untouched.
    assert sync._last_image_hash == b""


def test_get_clipboard_image_returns_validated_png(fixture_png, monkeypatch, _xclip_clipboard_sync):
    """D-13 / D-16 — ``get_clipboard_image`` returns validated PNG bytes."""
    sync = _xclip_clipboard_sync

    class _FakeResult:
        returncode = 0
        stdout = fixture_png

    def _fake_run(cmd, **kwargs):
        # Verify xclip args.
        assert cmd[:5] == [
            "xclip", "-selection", "clipboard", "-t", "image/png",
        ]
        assert cmd[-1] == "-o"
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    out = sync.get_clipboard_image()
    assert out == fixture_png
