"""D-07 FAIL branch / INPUT-08 re-scope (Plan 02-10 Task 2).

The IOHIDUserDevice spike per ``02-CONTEXT.md`` D-07/D-08 was recorded
as FAIL in ``docs/release.md`` "Phase 2 IOHIDUserDevice spike outcome".
Mac-server pen pressure is a v1 known-limitation; the production path
for Flame is the Rocky Linux server.

Wave 0 left this file with 5 ``xfail`` skeletons (one per planned mock-
at-IOKit-boundary test). The FAIL branch removes the xfails and asserts
the **shape** of the documented limitation:

  1. ``server.mac_pen_injector`` does not exist (Task 2 acceptance:
     ``server/mac_pen_injector.py`` MUST NOT exist on the FAIL branch).
  2. ``server/mac_input_injector.py::pen_event`` retains the mouse-click
     fallback with the one-time WARNING log path (Task 2 acceptance).
  3. ``docs/release.md`` records the spike outcome unambiguously.

If a future spike re-attempt produces a PASS, this file is replaced
wholesale with the 5 mock-at-IOKit-boundary tests sketched in Plan
02-10 Task 1 (the plan retains them for the next attempt).
"""
from __future__ import annotations

import inspect
import pathlib

import pytest


def test_mac_pen_injector_is_documented_limitation_per_d07_fail():
    """INPUT-08 re-scoped per D-07 failure path; no production module.

    Asserts ``server/mac_pen_injector.py`` is NOT importable. If a
    future commit lands the module, this test fails loudly — the FAIL-
    branch state in ``docs/release.md`` and the PASS-branch tests would
    have to land together (and this file would be replaced wholesale).
    """
    try:
        from server.mac_pen_injector import MacPenInjector  # noqa: F401
    except ImportError:
        return  # expected — FAIL branch
    pytest.fail(
        "server/mac_pen_injector.py exists but docs/release.md records "
        "the D-07 IOHIDUserDevice spike as FAIL and INPUT-08 as a v1 "
        "known-limitation. Either remove the module (FAIL branch) or "
        "switch to the PASS branch (replace this test file with the 5 "
        "mock-at-IOKit-boundary tests sketched in Plan 02-10 Task 1)."
    )


def test_mac_input_injector_keeps_mouse_click_fallback():
    """The Phase 1 mouse-click downgrade path must still exist.

    The test is import-guarded: on Linux runners ``server.mac_input_injector``
    can't be imported because PyObjC's Quartz isn't on the system. We
    skip rather than fail in that case so the FAIL-branch shape stays
    cross-platform.
    """
    try:
        from server import mac_input_injector
    except Exception as e:
        pytest.skip(f"mac_input_injector unimportable on this platform: {e}")

    src = inspect.getsource(mac_input_injector.MacInputInjector.pen_event)
    assert "downgraded to mouse" in src or "_pen_warned" in src, (
        "MacInputInjector.pen_event no longer contains the v1 mouse-"
        "click downgrade fallback. INPUT-08 is documented as a v1 known-"
        "limitation in docs/release.md — the warning path must remain."
    )


def test_release_notes_document_d07_spike_outcome():
    """``docs/release.md`` must record the spike outcome per Task 2.

    Belt-and-suspenders for the Phase 2 verifier: even if someone deletes
    the spike-outcome paragraph from ``docs/release.md`` by mistake, this
    test catches it before the Phase 2 ROADMAP gate flips.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    release_md = repo_root / "docs" / "release.md"
    assert release_md.exists(), (
        f"docs/release.md missing at {release_md} — Plan 02-10 Task 2 "
        "requires the spike outcome be recorded there."
    )
    text = release_md.read_text()
    assert "INPUT-08" in text, "docs/release.md missing INPUT-08 reference"
    assert "spike" in text.lower(), "docs/release.md missing 'spike' keyword"
    # FAIL outcome OR PASS outcome — either is valid; the file must be
    # explicit about which branch landed.
    assert ("FAIL" in text or "PASS" in text), (
        "docs/release.md must explicitly record the D-07 spike outcome "
        "(FAIL or PASS), not leave it ambiguous."
    )


# =====================================================================
# INPUT-10 server-side injection-half coverage (Plan 02-11 Task 2 Step D)
#
# These three tests cover the *injection* half of INPUT-10 (eraser flag
# + side-button-1 + side-button-2 dispatch through the platform-backends
# InputInjector facade). The *hardware* half remains the manual 4-cell
# Wacom matrix in 02-12.
#
# FAIL-branch fallback: 02-10 recorded the IOHIDUserDevice spike as
# FAIL (per docs/release.md), so server/mac_pen_injector.py does not
# exist on this branch. ``pytest.importorskip`` skips these three tests
# cleanly with the documented v1-limitation reason. If a future commit
# lands the PASS branch, the importorskip becomes a no-op and the tests
# auto-activate against the real MacPenInjector + IOKit mock.
# =====================================================================


def test_inputinjector_dispatches_eraser_flag_through_platform_backend():
    """INPUT-10: pen_type='eraser' on the platform_backends.InputInjector
    facade reaches MacPenInjector with the eraser flag set in the HID
    report flags byte (bit 0x02).
    """
    pytest.importorskip(
        "server.mac_pen_injector",
        reason=(
            "SPIKE FAIL per 02-10 Task 2; INPUT-10 injection through "
            "MacPenInjector is a documented v1 limitation. The Mac "
            "server uses the mouse-click downgrade fallback in "
            "mac_input_injector.pen_event; full pen pressure / "
            "eraser / side-button injection is v1.1+ work."
        ),
    )
    # PASS branch: the test body lives in the next plan's executor;
    # this skeleton documents the assertion shape so the scope is
    # locked. Fail loudly if we somehow reach this point on the FAIL
    # branch (importorskip should have skipped above).
    pytest.fail(
        "server.mac_pen_injector imported successfully but the FAIL-"
        "branch test body has not been replaced. See Plan 02-10 Task 1 "
        "for the PASS-branch test body sketch."
    )


def test_inputinjector_dispatches_side_button_1_through_platform_backend():
    """INPUT-10: side-button-1 press dispatches through to the
    MacPenInjector report (bit 0x04 in the flags byte).
    """
    pytest.importorskip(
        "server.mac_pen_injector",
        reason=(
            "SPIKE FAIL per 02-10 Task 2; INPUT-10 injection through "
            "MacPenInjector is a documented v1 limitation."
        ),
    )
    pytest.fail(
        "server.mac_pen_injector imported but FAIL-branch test body "
        "not replaced. See Plan 02-10 Task 1 for the PASS-branch sketch."
    )


def test_inputinjector_dispatches_side_button_2_through_platform_backend():
    """INPUT-10: side-button-2 press dispatches through to the
    MacPenInjector report (bit 0x08 in the flags byte).
    """
    pytest.importorskip(
        "server.mac_pen_injector",
        reason=(
            "SPIKE FAIL per 02-10 Task 2; INPUT-10 injection through "
            "MacPenInjector is a documented v1 limitation."
        ),
    )
    pytest.fail(
        "server.mac_pen_injector imported but FAIL-branch test body "
        "not replaced. See Plan 02-10 Task 1 for the PASS-branch sketch."
    )
