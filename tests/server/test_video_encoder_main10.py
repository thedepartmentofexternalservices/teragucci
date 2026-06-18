"""ENCODER_DEFS capability-flag assertions (D-03, D-05).

Mirrors the Phase 1 mock-at-subprocess-boundary test philosophy (no real
ffmpeg invocation) but operates on the static ENCODER_DEFS registry —
D-05 keeps 4:4:4 and 4:2:2 defaults family-backed while D-03 promotes
main10 to a probe-backed runtime answer. This file asserts the static
table is internally consistent so the capability probe has a sane base
to negotiate against.
"""
from __future__ import annotations

import pytest  # noqa: F401  # kept for parity with other server tests

from server.video_encoder import ENCODER_DEFS, HWEncoder


def _by_name(name: str) -> HWEncoder:
    matches = [e for e in ENCODER_DEFS if e.name == name]
    assert matches, f"ENCODER_DEFS has no entry named {name!r}"
    return matches[0]


def test_hevc_nvenc_advertises_main10():
    enc = _by_name("hevc_nvenc")
    assert enc.supports_main10 is True
    assert enc.main10_detection == "probe"


def test_hevc_nvenc_blackwell_422_stays_false_without_probe():
    enc = _by_name("hevc_nvenc")
    # Static default; runtime probe promotes to True on Blackwell.
    assert enc.supports_422 is False


def test_hevc_videotoolbox_advertises_main10_via_vt_query():
    enc = _by_name("hevc_videotoolbox")
    assert enc.supports_main10 is True
    assert enc.main10_detection == "vt_query"


def test_libx265_software_main10_always_capable():
    enc = _by_name("libx265")
    assert enc.supports_main10 is True
    assert enc.supports_422 is True
    assert enc.main10_detection == "family"


def test_no_encoder_advertises_main10_without_detection_strategy():
    for enc in ENCODER_DEFS:
        if enc.supports_main10:
            assert enc.main10_detection in ("family", "probe", "vt_query"), (
                f"{enc.name} advertises main10 but has no detection strategy"
            )
