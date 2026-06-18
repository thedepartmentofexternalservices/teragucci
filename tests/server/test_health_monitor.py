"""OBS-02 + OBS-03 — HealthMonitor extensions + keyframe telemetry + clock offset.

Plan 01-14 introduces:

* ``HealthMonitor._transmit_times`` / ``._decode_times`` / ``._display_times``
  deques (maxlen=120, same pattern as the existing ``_encode_times``,
  ``_capture_times``, ``_input_latencies``). The client-reported stages
  (decode + display) are not yet wired through the protocol — the server-side
  recorders are in place so Phase 4's smoke harness extension can feed them
  without further HealthMonitor surgery.
* ``record_transmit_time`` / ``record_decode_time`` / ``record_display_time``
  follow the existing ``record_encode_time`` pattern.
* Two plain-int counters: ``keyframe_requested`` (incremented by the STAB-04
  IDR-on-drop path in ClientSession.enqueue) and ``keyframe_emitted``
  (incremented when the encoder callback fires with ``is_keyframe=True``).
* :class:`common.clock_offset.ClockOffsetEstimator` — EWMA sliding estimator
  of client↔server clock drift, ~40 LOC per RESEARCH Open Q #5 (planner-open
  locked: "ship the ~40-line helper, re-estimate every 60 s").

All new ``HealthStats`` fields default to 0.0 / 0 so existing callers
(``test_healthstats_roundtrip`` in ``tests/common/test_messages.py``) keep
passing without modification.
"""
from __future__ import annotations

import json
import time

import pytest

from common.clock_offset import ClockOffsetEstimator
from common.messages import HealthStats, MsgType
from server.health import HealthMonitor


# ── New deque recorders (OBS-02) ─────────────────────────────────────


def test_record_transmit_time():
    """transmit stage populates its own deque and surfaces via get_stats()."""
    h = HealthMonitor()
    h.record_transmit_time(2.1)
    stats = h.get_stats()
    assert stats.transmit_time_ms == 2.1


def test_record_decode_time():
    h = HealthMonitor()
    h.record_decode_time(3.3)
    stats = h.get_stats()
    assert stats.decode_time_ms == 3.3


def test_record_display_time():
    h = HealthMonitor()
    h.record_display_time(5.5)
    stats = h.get_stats()
    assert stats.display_time_ms == 5.5


def test_stage_deques_averaged_across_samples():
    """Multiple samples are averaged (follows existing _avg_deque pattern)."""
    h = HealthMonitor()
    for v in (2.0, 4.0, 6.0):
        h.record_transmit_time(v)
    stats = h.get_stats()
    assert stats.transmit_time_ms == 4.0


def test_stage_deques_bounded_at_maxlen_120():
    """Deque size matches existing stage deques (maxlen=120)."""
    h = HealthMonitor()
    for i in range(200):
        h.record_transmit_time(float(i))
    # Only the last 120 remain; average is (80 + 199) / 2 = 139.5
    stats = h.get_stats()
    assert stats.transmit_time_ms == pytest.approx(139.5, abs=0.1)


# ── Keyframe telemetry (OBS-03) ──────────────────────────────────────


def test_keyframe_counters_default_zero():
    h = HealthMonitor()
    assert h.keyframe_requested == 0
    assert h.keyframe_emitted == 0


def test_keyframe_telemetry():
    """record_keyframe_{requested,emitted} increment the counters and surface
    via HealthStats (wire contract for Phase 1 observability)."""
    h = HealthMonitor()
    h.record_keyframe_requested()
    h.record_keyframe_requested()
    h.record_keyframe_emitted()
    assert h.keyframe_requested == 2
    assert h.keyframe_emitted == 1
    stats = h.get_stats()
    assert stats.keyframe_requested == 2
    assert stats.keyframe_emitted == 1


def test_healthstats_includes_new_fields_json():
    """HealthStats JSON wire round-trip includes the 5 OBS-02/OBS-03 fields."""
    s = HealthStats(
        transmit_time_ms=1.1,
        decode_time_ms=2.2,
        display_time_ms=3.3,
        keyframe_requested=5,
        keyframe_emitted=4,
    )
    parsed = json.loads(s.to_json())
    assert parsed["type"] == MsgType.HEALTH_STATS
    assert parsed["transmit_time_ms"] == 1.1
    assert parsed["decode_time_ms"] == 2.2
    assert parsed["display_time_ms"] == 3.3
    assert parsed["keyframe_requested"] == 5
    assert parsed["keyframe_emitted"] == 4


def test_healthstats_defaults_preserve_backward_compat():
    """Existing callers that don't set the new fields still get defaults — no
    break in the pre-OBS-02 round-trip coverage in tests/common/test_messages.py."""
    s = HealthStats()
    parsed = json.loads(s.to_json())
    assert parsed["transmit_time_ms"] == 0.0
    assert parsed["decode_time_ms"] == 0.0
    assert parsed["display_time_ms"] == 0.0
    assert parsed["keyframe_requested"] == 0
    assert parsed["keyframe_emitted"] == 0


# ── ClockOffsetEstimator (OBS-02 helper) ─────────────────────────────


def test_clock_offset_zero_until_seeded():
    est = ClockOffsetEstimator()
    assert est.current_offset_ms == 0.0
    assert est.should_reestimate() is True


def test_clock_offset_basic_math_no_drift():
    """Round-trip 40 ms, zero drift.

    T1=1000 (client sent ping), T2=1020 (server got it 20 ms later),
    T3=1040 (client received pong). one_way=(1040-1000)/2=20.
    offset = 1020 - 1000 - 20 = 0.
    """
    est = ClockOffsetEstimator(alpha=1.0)  # alpha=1 -> single sample IS the estimate
    est.submit_exchange(1000, 1020, 1040)
    assert est.current_offset_ms == 0.0


def test_clock_offset_server_ahead_by_50ms():
    """Server clock 50 ms ahead of client. one_way=20, offset=1070-1000-20=50."""
    est = ClockOffsetEstimator(alpha=1.0)
    est.submit_exchange(1000, 1070, 1040)
    assert est.current_offset_ms == 50.0


def test_clock_offset_ewma_damps_noisy_samples():
    """Successive samples converge via EWMA — a single outlier doesn't swing
    the estimator hard. alpha=0.2 means new sample contributes 20%."""
    est = ClockOffsetEstimator(alpha=0.2)
    # Seed with offset=0 (T1=1000, T2=1020, T3=1040).
    est.submit_exchange(1000, 1020, 1040)
    assert est.current_offset_ms == pytest.approx(0.0, abs=0.001)
    # Noisy sample claiming offset=100 — should move estimator 20% of the way.
    # one_way=20, offset = 1120 - 1000 - 20 = 100.
    est.submit_exchange(1000, 1120, 1040)
    assert est.current_offset_ms == pytest.approx(20.0, abs=0.001)


def test_clock_offset_reestimate_interval():
    """should_reestimate() flips True again after the configured interval."""
    est = ClockOffsetEstimator(reestimate_interval_s=0.05)
    est.submit_exchange(1000, 1020, 1040)
    assert est.should_reestimate() is False
    time.sleep(0.06)
    assert est.should_reestimate() is True


def test_clock_offset_default_reestimate_interval_is_60s():
    """RESEARCH Open Q #5 locked: re-estimate every 60 s of session lifetime."""
    est = ClockOffsetEstimator()
    assert est._interval == 60.0
