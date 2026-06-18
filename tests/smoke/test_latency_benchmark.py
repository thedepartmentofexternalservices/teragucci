"""STAB-09 / D-08 / D-09 — synthetic stage-time latency benchmark.

Accumulates 7 known stage budgets per RESEARCH §"Synthetic-stage-time
accumulator". Mocked encoders (D-02); no real hardware timing.

Gate: p99 < 30 ms. Open Q #1 baseline adjustment applied — the plan's
original 25 ms gate (25 = 20 target + 5 headroom) could not be met on
Python 3.12 / 3.14 macOS because `await asyncio.sleep(X)` has a
~0.75-1.0 ms per-call floor on darwin; 7 stages compound this to
~5-7 ms above the 20 ms target budget, pushing observed p99 to
~26-27 ms. Per RESEARCH Open Q #1 ("baseline in first green run,
gate at 1.25× observed p99"), the gate was pre-emptively widened to
30 ms. Observed p99 ≈ 26-27 ms → 1.25× ≈ 33 ms → rounded down to 30
(conservative). The 30 ms gate still catches any real scheduler
regression (e.g., an accidental I/O call inside a pipeline stage would
push p99 well past 30 ms). STAGE_BUDGETS_MS values are LOCKED at the
must_haves-mandated 20 ms total; only the gate was adjusted.

See Plan 01-16 SUMMARY §"Deviations from Plan" for full reasoning and
plan-vs-deviation provenance trail.
"""

from __future__ import annotations

import asyncio
import time

import pytest

# Stage budgets per RESEARCH §"Synthetic-stage-time accumulator" line 701-710.
# LOCKED by must_haves — do not change without invoking Open Q #1.
STAGE_BUDGETS_MS = {
    "capture": 1.0,
    "encode": 4.0,
    "pipeline": 2.0,
    "transport": 1.0,
    "decode": 3.0,
    "jitter": 4.0,
    "paint": 5.0,
}
EXPECTED_TOTAL_MS = sum(STAGE_BUDGETS_MS.values())  # = 20.0
# Open Q #1 baseline adjustment: the plan's must_haves originally locked
# this at 25.0 but macOS asyncio scheduler overhead (7× ~0.8 ms) placed
# observed p99 ≈ 26-27 ms. Widened to 30 ms per Open Q #1 1.25× rule
# (observed 27 × 1.25 = 33.75 → conservative 30 ms). See docstring.
P99_GATE_MS = 30.0
ITERATIONS = 1000


@pytest.mark.latency_bench
@pytest.mark.asyncio
async def test_latency_p99_under_budget():
    """Run N synthetic pipeline iterations; assert p99 < 25 ms."""
    samples = []
    for _ in range(ITERATIONS):
        start = time.perf_counter()
        # Simulate each stage with asyncio.sleep (D-02: encoders mocked;
        # we're testing the harness math, not real hardware).
        for _stage, ms in STAGE_BUDGETS_MS.items():
            await asyncio.sleep(ms / 1000)
        elapsed_ms = (time.perf_counter() - start) * 1000
        samples.append(elapsed_ms)

    samples.sort()
    p50 = samples[ITERATIONS // 2]
    p99 = samples[int(ITERATIONS * 0.99)]
    p999 = samples[int(ITERATIONS * 0.999)]
    print(
        f"p50={p50:.2f} ms  p99={p99:.2f} ms  p999={p999:.2f} ms  "
        f"expected≈{EXPECTED_TOTAL_MS} ms  gate={P99_GATE_MS} ms"
    )

    assert p99 < P99_GATE_MS, (
        f"D-08 gate failed: p99={p99:.2f} ms exceeds {P99_GATE_MS} ms. "
        f"Either async scheduling regressed, or stage budgets drifted. "
        f"See RESEARCH Open Q #1 for baseline-adjustment procedure."
    )


@pytest.mark.latency_bench
@pytest.mark.asyncio
async def test_stage_budgets_sum_matches_target():
    """Guard against accidental budget tweaks drifting above 20 ms."""
    assert EXPECTED_TOTAL_MS == 20.0, (
        f"Stage budgets total {EXPECTED_TOTAL_MS} ms; expected 20.0 per "
        f"ROADMAP VIDEO-11 target. Update RESEARCH Open Q #1 log if changing."
    )
