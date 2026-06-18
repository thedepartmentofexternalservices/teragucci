"""Phase 3 D-13 / D-14 / D-16 — macOS clipboard PNG image path.

Plan 03-06 implementation of Plan 03-01 Wave 0 RED skeletons.

Mocks NSPasteboard at the PyObjC boundary per the Phase 2 mock-at-
IOKit-boundary discipline. The AppKit / Foundation imports in
``server/mac_clipboard.py`` run at module import time — tests stub
the resolved ``NSPasteboardTypePNG`` + ``NSData`` module attributes,
plus replace ``self._pb`` on the instance with a ``MagicMock``.

Skipped on non-Mac environments via ``pytest.importorskip("AppKit")``.
"""
from __future__ import annotations

import hashlib
import sys
from unittest import mock

import pytest


# Entire module skip on non-Mac environments where PyObjC AppKit is
# unavailable — matches the Phase 2 IOHID test discipline. On Mac CI
# runners AppKit is guaranteed present.
pytest.importorskip("AppKit")


@pytest.fixture
def _mac_sync_with_fake_pb(monkeypatch):
    """Build a ``MacClipboardSync`` with ``self._pb`` replaced by a mock.

    PyObjC's ``NSPasteboard.generalPasteboard()`` is a live system call;
    swapping the instance attribute for a ``MagicMock`` isolates the
    test from the real system pasteboard and lets us assert which
    AppKit methods were invoked.
    """
    from server import mac_clipboard as mac_clipboard_module

    sync = mac_clipboard_module.MacClipboardSync()
    if sync._pb is None:
        pytest.skip("AppKit/NSPasteboard unavailable on this runner")
    fake_pb = mock.MagicMock()
    fake_pb.changeCount.return_value = 42
    fake_pb.setData_forType_.return_value = True
    sync._pb = fake_pb
    return sync, fake_pb


def test_set_clipboard_image_writes_nspasteboard_png(
    fixture_png, monkeypatch, _mac_sync_with_fake_pb,
):
    """D-13 — ``set_clipboard_image`` calls ``pb.setData_forType_(NSPasteboardTypePNG)``."""
    from server import mac_clipboard as mac_clipboard_module

    sync, fake_pb = _mac_sync_with_fake_pb

    # Stub NSData.dataWithBytes_length_ so we don't rely on real PyObjC
    # bridge allocation.
    ns_sentinel = object()
    fake_nsdata_cls = mock.MagicMock()
    fake_nsdata_cls.dataWithBytes_length_.return_value = ns_sentinel
    monkeypatch.setattr(mac_clipboard_module, "NSData", fake_nsdata_cls)

    sync.set_clipboard_image(fixture_png)

    # clearContents called before setData.
    fake_pb.clearContents.assert_called_once()
    # NSData built from the raw bytes verbatim — no decode/encode round
    # trip (Pitfall 5 alpha loss).
    fake_nsdata_cls.dataWithBytes_length_.assert_called_once_with(
        fixture_png, len(fixture_png),
    )
    # setData_forType_ invoked with the NSData + PNG type constant.
    fake_pb.setData_forType_.assert_called_once()
    call_args = fake_pb.setData_forType_.call_args
    assert call_args.args[0] is ns_sentinel
    assert call_args.args[1] == mac_clipboard_module.NSPasteboardTypePNG

    # Echo suppression cache updated: sha256 + last_change_count bumped.
    assert sync._last_image_hash == hashlib.sha256(fixture_png).digest()
    assert sync._last_change_count == 42


def test_get_clipboard_image_reads_nspasteboard_png(
    fixture_png, monkeypatch, _mac_sync_with_fake_pb,
):
    """D-13 — ``get_clipboard_image`` returns bytes from mocked NSData payload."""
    from server import mac_clipboard as mac_clipboard_module

    sync, fake_pb = _mac_sync_with_fake_pb

    # dataForType_ returns an NSData-ish object whose bytes(x) → fixture_png.
    # PyObjC's NSData proxies buffer protocol; emulate with a bytes wrapper.
    class _FakeNSData:
        def __init__(self, raw: bytes):
            self._raw = raw

        def __bytes__(self):
            return self._raw

    fake_pb.dataForType_.return_value = _FakeNSData(fixture_png)

    result = sync.get_clipboard_image()
    fake_pb.dataForType_.assert_called_once_with(
        mac_clipboard_module.NSPasteboardTypePNG,
    )
    assert result == fixture_png


def test_get_clipboard_image_rejects_magic_mismatch(
    monkeypatch, _mac_sync_with_fake_pb,
):
    """D-16 — inbound non-PNG NSPasteboard payload rejected on receive."""
    sync, fake_pb = _mac_sync_with_fake_pb

    class _FakeNSData:
        def __bytes__(self):
            return b"not a png at all"

    fake_pb.dataForType_.return_value = _FakeNSData()

    # Malformed magic bytes → None (validate_png_payload rejects).
    assert sync.get_clipboard_image() is None


def test_set_clipboard_image_rejects_bad_magic_before_pyobjc(
    monkeypatch, _mac_sync_with_fake_pb,
):
    """D-16 defense-in-depth — malformed PNG never reaches NSPasteboard on Mac."""
    from server import mac_clipboard as mac_clipboard_module

    sync, fake_pb = _mac_sync_with_fake_pb

    fake_nsdata_cls = mock.MagicMock()
    monkeypatch.setattr(mac_clipboard_module, "NSData", fake_nsdata_cls)

    sync.set_clipboard_image(b"definitely not a png")

    # Neither NSData allocation nor setData_forType_ should run.
    fake_nsdata_cls.dataWithBytes_length_.assert_not_called()
    fake_pb.setData_forType_.assert_not_called()
    # Echo cache untouched.
    assert sync._last_image_hash == b""
