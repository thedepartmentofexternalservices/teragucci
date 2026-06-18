"""D-20 — TCC database read-only inspection tests.

Plan 02-11 Task 2 (Wave 8). The production module
``client/tcc_detect.py`` reads ``~/Library/Application Support/com.apple.TCC/TCC.db``
via ``sqlite3.connect("file:...?mode=ro", uri=True)``. These tests
prove:

  1. Missing DB returns the all-False sentinel without raising.
  2. A seeded fake DB with one Input Monitoring grant + one Accessibility
     deny returns the expected dict shape.
  3. The ``sqlite3.connect`` URI carries ``mode=ro`` and ``uri=True``
     (never any write/append/rwc mode).

T-02-30 / T-02-33 from the plan threat register: read-only sqlite3 is
safe (T-02-30 accept) and malformed DB must not crash (T-02-33
mitigate). The "all-False on failure" sentinel is the T-02-33
mitigation.
"""
from __future__ import annotations

import sqlite3

from client.tcc_detect import read_tcc_status


def test_tcc_read_returns_all_false_when_db_missing(monkeypatch, tmp_path):
    """Missing TCC.db => safe all-False sentinel; never raises."""
    fake_path = tmp_path / "nope.db"
    monkeypatch.setattr("client.tcc_detect.TCC_DB_PATH", fake_path)
    r = read_tcc_status()
    assert r["input_monitoring_granted"] is False
    assert r["accessibility_granted_for_wacom"] is False
    assert r["tcc_db_readable"] is False
    # raw_rows defaults to empty list when the DB cannot be opened.
    assert r["raw_rows"] == []


def test_tcc_read_returns_granted_for_seeded_input_monitoring(
    monkeypatch, tmp_path,
):
    """Seeded fake TCC schema -> input_monitoring_granted resolves to True
    when auth_value >= 2 (Apple's "allowed" sentinel) for the bundle id;
    Wacom Accessibility row with auth_value=0 stays False.
    """
    db = tmp_path / "fake_tcc.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE access (service TEXT, client TEXT, auth_value INTEGER)"
    )
    conn.execute(
        "INSERT INTO access VALUES (?, ?, ?)",
        ("kTCCServiceListenEvent", "com.teraguchi.client", 2),
    )
    conn.execute(
        "INSERT INTO access VALUES (?, ?, ?)",
        ("kTCCServiceAccessibility", "com.wacom.driver", 0),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("client.tcc_detect.TCC_DB_PATH", db)

    r = read_tcc_status(
        client_bundle_id="com.teraguchi.client",
        wacom_pattern="com.wacom.",
    )
    assert r["input_monitoring_granted"] is True
    assert r["accessibility_granted_for_wacom"] is False
    assert r["tcc_db_readable"] is True
    # raw_rows captured both rows verbatim (for the Wacom-tab debug pane).
    assert len(r["raw_rows"]) == 2


def test_tcc_read_marks_wacom_accessibility_granted_when_present(
    monkeypatch, tmp_path,
):
    """A com.wacom.* row with auth_value=2 resolves to
    accessibility_granted_for_wacom=True. Defensive against the inverse
    of test #2 (so an inverted flag in the SUT can't pass)."""
    db = tmp_path / "fake_tcc.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE access (service TEXT, client TEXT, auth_value INTEGER)"
    )
    conn.execute(
        "INSERT INTO access VALUES (?, ?, ?)",
        ("kTCCServiceAccessibility", "com.wacom.TabletDriver", 2),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("client.tcc_detect.TCC_DB_PATH", db)

    r = read_tcc_status(wacom_pattern="com.wacom.")
    assert r["accessibility_granted_for_wacom"] is True
    assert r["input_monitoring_granted"] is False  # not granted in this DB


def test_tcc_read_uses_readonly_uri(monkeypatch, tmp_path):
    """Ensure ``sqlite3.connect`` is called with ``mode=ro`` + ``uri=True``.

    T-02-30 accept disposition rests on the read-only mode being honored.
    A future refactor that drops the mode flag silently would re-open
    the threat — this test pins the contract.
    """
    from client import tcc_detect

    captured: dict = {}
    def spy(database_uri, *args, **kw):
        # ``database`` is the first positional arg of sqlite3.connect.
        # We rename it ``database_uri`` here to dodge the kwarg collision
        # with the ``uri=True`` flag (sqlite3's signature is
        # ``connect(database, ..., uri=False)`` in py3 stdlib).
        captured["database"] = database_uri
        captured["kw"] = kw
        # Abort: we only care about the call shape, not whether the
        # fake DB has rows.
        raise sqlite3.OperationalError("intentional abort from spy")

    monkeypatch.setattr(sqlite3, "connect", spy)
    db = tmp_path / "whatever.db"
    db.touch()
    monkeypatch.setattr(tcc_detect, "TCC_DB_PATH", db)

    r = read_tcc_status()  # must NOT raise
    assert "mode=ro" in captured["database"], (
        f"sqlite3.connect called without mode=ro; "
        f"database={captured['database']!r}"
    )
    assert captured["kw"].get("uri") is True, (
        "sqlite3.connect must pass uri=True to interpret the file:?mode=ro form"
    )
    # And the all-False sentinel survives the OperationalError.
    assert r["input_monitoring_granted"] is False
    assert r["tcc_db_readable"] is False
