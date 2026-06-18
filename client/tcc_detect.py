"""D-20 — read-only TCC database inspection + Wacom driver detection.

Plan 02-11 Task 2 (Wave 8). Surfaces "why did my pen stop working" into
the client UX (``client.key_diagnostic.WacomSetupTab``) instead of into
the logs.

Trust boundaries (per Plan 02-11 ``<threat_model>``):
  * ``client -> TCC.db`` is read-only via ``sqlite3.connect("file:?mode=ro", uri=True)``.
    Never writes, never bypasses, never decrypts. Apple's SIP guarantees
    a user can read TCC entries for their own account; we don't try
    other accounts.
  * ``client -> system_profiler`` is a read-only subprocess (5s timeout).
  * ``client -> System Settings`` uses the public ``x-apple.systempreferences:``
    URL handler.

Threats covered (per Plan 02-11 ``<threat_model>``):
  * T-02-30 (Tampering, accept): mode=ro guarantees no write attempt.
  * T-02-31 (Info-Disclosure, accept): row data is displayed locally only.
  * T-02-32 (Spoofing, accept): bundle ID hardcoded; Phase 6 notarization
    is the right defense, not Phase 2.
  * T-02-33 (DoS, mitigate): every sqlite3 call wrapped in
    ``try/except sqlite3.Error``; failure path returns the all-False
    sentinel rather than raising.

Cross-platform: this module is import-safe on Linux (``sqlite3`` is
stdlib; ``TCC_DB_PATH.exists()`` returns False). On Linux + Windows
hosts the all-False sentinel is the right answer — TCC is a macOS
concept and the Wacom Setup tab only renders on darwin.
"""
from __future__ import annotations

import pathlib
import sqlite3
import subprocess
from typing import Any

from common.logging import get_logger

log = get_logger("client.tcc_detect")

# macOS canonical location of the per-user TCC database. ``expanduser()``
# resolves to ``$HOME/Library/...`` which on a non-darwin host is a
# perfectly valid (but non-existent) path -- the ``.exists()`` guard
# below makes the rest of the module a no-op there.
TCC_DB_PATH = pathlib.Path(
    "~/Library/Application Support/com.apple.TCC/TCC.db"
).expanduser()

# Apple TCC service identifiers we care about.
SERVICE_INPUT_MONITORING = "kTCCServiceListenEvent"
SERVICE_ACCESSIBILITY = "kTCCServiceAccessibility"

# auth_value sentinel — Apple uses 2 for "allowed" on modern macOS;
# 0 = denied, 1 = unknown / prompt. Treating ``>= 2`` as granted is
# defensive against future "allow with conditions" values.
AUTH_VALUE_GRANTED = 2

# Deep-link URL schemes for the System Settings panes we want.
SYSTEM_SETTINGS_INPUT_MONITORING = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
)
SYSTEM_SETTINGS_ACCESSIBILITY = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)
WACOM_DRIVER_URL = "https://www.wacom.com/en-us/support/product-support/drivers"


def read_tcc_status(
    client_bundle_id: str = "com.teraguchi.client",
    wacom_pattern: str = "com.wacom.",
) -> dict[str, Any]:
    """Return TCC permission status for the Teraguchi client + Wacom driver.

    Args:
        client_bundle_id: The bundle ID Teraguchi advertises to TCC.
            Defaults to ``com.teraguchi.client``; Phase 6 signing /
            notarization will lock this to the production identifier.
        wacom_pattern: Substring matched against the ``access.client``
            column to find Wacom-driver Accessibility rows. Default
            ``com.wacom.`` matches the documented identifier prefix
            shipped by the official Wacom driver bundle.

    Returns:
        A dict with the following keys (all defined; never raises):

        * ``input_monitoring_granted`` (bool): True iff there's a row
          for the Teraguchi client with auth_value >= 2.
        * ``accessibility_granted_for_wacom`` (bool): True iff there's
          any Accessibility row whose client matches ``wacom_pattern``
          and has auth_value >= 2.
        * ``wacom_driver_installed`` (bool): True iff a known Wacom
          driver path exists on disk (lightweight check).
        * ``raw_rows`` (list): every Input Monitoring + Accessibility row
          read from the DB, as ``(service, client, auth_value)`` tuples.
          Surfaced to the Wacom-tab debug pane so artists can copy/paste
          into a bug report.
        * ``tcc_db_readable`` (bool): True iff the sqlite3 open
          succeeded. Distinguishes "no permissions granted" (all-False
          but readable) from "TCC.db not present" (all-False and
          unreadable).
    """
    result: dict[str, Any] = {
        "input_monitoring_granted": False,
        "accessibility_granted_for_wacom": False,
        "wacom_driver_installed": False,
        "raw_rows": [],
        "tcc_db_readable": False,
    }

    if not TCC_DB_PATH.exists():
        log.info("tcc_detect.db_not_found", path=str(TCC_DB_PATH))
        # Even if the DB is missing, still surface whether the Wacom
        # driver bundle exists on disk -- on Linux this is False, on
        # Mac without TCC permissions yet it's the only signal we have.
        result["wacom_driver_installed"] = _detect_wacom_driver()
        return result

    # T-02-30 / T-02-33: mode=ro is the safety net. ``uri=True`` is
    # required for sqlite3 to interpret the ``file:?mode=ro`` form.
    uri = f"file:{TCC_DB_PATH}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    except sqlite3.Error as e:
        log.warning("tcc_detect.open_failed", error=str(e), path=str(TCC_DB_PATH))
        result["wacom_driver_installed"] = _detect_wacom_driver()
        return result

    result["tcc_db_readable"] = True
    try:
        cursor = conn.execute(
            "SELECT service, client, auth_value FROM access "
            "WHERE service IN (?, ?)",
            (SERVICE_INPUT_MONITORING, SERVICE_ACCESSIBILITY),
        )
        for service, client, auth_value in cursor:
            result["raw_rows"].append((service, client, auth_value))
            if (
                service == SERVICE_INPUT_MONITORING
                and client == client_bundle_id
                and auth_value >= AUTH_VALUE_GRANTED
            ):
                result["input_monitoring_granted"] = True
            if (
                service == SERVICE_ACCESSIBILITY
                and wacom_pattern in (client or "")
                and auth_value >= AUTH_VALUE_GRANTED
            ):
                result["accessibility_granted_for_wacom"] = True
    except sqlite3.Error as e:
        log.warning("tcc_detect.query_failed", error=str(e))
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    result["wacom_driver_installed"] = _detect_wacom_driver()
    return result


def _detect_wacom_driver() -> bool:
    """Lightweight on-disk check for the Wacom driver bundle.

    Looks for the canonical install paths shipped by the official
    Wacom driver. Returns False on non-darwin hosts (the paths don't
    exist) and on Macs without the driver installed.
    """
    candidates = (
        "/Applications/Wacom Tablet.app",
        "/Library/Application Support/Tablet",
    )
    return any(pathlib.Path(p).exists() for p in candidates)


def list_connected_wacom_devices() -> list[str]:
    """Return product strings for any Wacom devices currently on USB.

    Uses ``system_profiler SPUSBDataType`` (read-only, 5s timeout). On
    non-darwin hosts ``system_profiler`` is missing and the helper
    returns ``[]``.
    """
    try:
        out = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-xml"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log.info("tcc_detect.system_profiler_unavailable", error=str(e))
        return []

    matches = [
        line.strip()
        for line in out.stdout.splitlines()
        if "Wacom" in line
    ]
    # Trim to a sane upper bound so a flood of USB siblings doesn't
    # blow up the log dialog.
    return matches[:20]
