"""Server-side clipboard tests — Linux (xclip / xsel) path.

Plan 03-06 CLIP-01 / D-16 — ``ClipboardSync.get_clipboard`` switched
to ``subprocess.run(..., text=False)`` + explicit UTF-8 decode so the
originating newline encoding (CRLF / LF / CR) survives the round-trip.
The old ``text=True`` flag engaged Python's universal-newlines layer
which silently converted CRLF → LF, breaking Windows-origin clipboard
text copy-pasted through a Mac client to a Rocky server.
"""
from __future__ import annotations

import subprocess

import pytest


@pytest.fixture
def _patched_clipboard_sync(monkeypatch):
    """Build a ``ClipboardSync`` with ``_tool='xclip'`` bypassed.

    Normal construction probes ``xclip --version`` via subprocess; that
    fails on CI runners without xclip installed. We stub
    ``_find_clipboard_tool`` to return ``"xclip"`` so every test exercises
    the xclip branch without the probe side effect.
    """
    from server import clipboard as clipboard_module

    def _fake_tool(_self):
        return "xclip"

    monkeypatch.setattr(
        clipboard_module.ClipboardSync,
        "_find_clipboard_tool",
        _fake_tool,
    )
    return clipboard_module.ClipboardSync()


@pytest.mark.parametrize(
    "raw, expected",
    [
        # CRLF — Windows-origin; must NOT be collapsed to LF.
        (b"hello\r\nworld\r\n", "hello\r\nworld\r\n"),
        # LF — Linux/Mac-origin; pass-through identity.
        (b"hello\nworld\n", "hello\nworld\n"),
        # CR — classic Mac / some MS Word payloads; also must survive.
        (b"hello\rworld\r", "hello\rworld\r"),
    ],
    ids=["crlf", "lf", "cr"],
)
def test_newline_preservation(raw, expected, _patched_clipboard_sync, monkeypatch):
    """CLIP-01 / D-16 — text=False bytes path preserves CRLF / LF / CR.

    Stubs subprocess.run to return the three newline forms as raw bytes
    and asserts ``get_clipboard()`` decodes them back to the exact same
    Python string. The old ``text=True`` path would have munged CRLF →
    LF at this boundary.
    """
    sync = _patched_clipboard_sync

    class _FakeResult:
        def __init__(self, stdout: bytes):
            self.stdout = stdout
            self.returncode = 0

    captured_kwargs: dict = {}

    def _fake_run(cmd, **kwargs):
        captured_kwargs.update(kwargs)
        return _FakeResult(stdout=raw)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = sync.get_clipboard()
    assert result == expected
    # Lock the contract: text=False is load-bearing. If text=True creeps
    # back in, universal-newlines re-engages and CRLF → LF conversion
    # returns — this assertion is the canary.
    assert captured_kwargs.get("text") is False
