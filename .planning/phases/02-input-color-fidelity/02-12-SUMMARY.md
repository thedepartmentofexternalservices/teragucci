---
phase: 02-input-color-fidelity
plan: 12
plan_id: 02-12
subsystem: input + tooling + release-runbook
tags: [wacom, rms, d-17, d-18, d-21, release-runbook, hardware-checkpoint]
type: execute
wave: 9
status: PARTIAL — automated tasks complete; manual DXS checkpoints DEFERRED
autonomous_completion: true
manual_checkpoints_pending:
  - "Task 2: 4-cell DXS Wacom matrix session (INPUT-09..12, D-17 6-step protocol)"
  - "Task 3: DXS pre/post-Phase-2 input-to-photon latency comparison (VIDEO-11, D-21)"
requirements:
  fully_satisfied: []
  partially_satisfied: [INPUT-09, INPUT-10, INPUT-11, INPUT-12, VIDEO-11]
dependencies:
  requires:
    - 02-01-SUMMARY.md  # Wave 0 stub of tools/wacom_quant_analysis.py
    - 02-10-SUMMARY.md  # IOHIDUserDevice spike outcome (FAIL branch)
  provides:
    - real RMS analysis tooling (tools/wacom_quant_analysis.py)
    - D-17 emission contract tests (tests/server/test_wacom_matrix_emission.py)
    - Phase 6 release-runbook seed (docs/release.md)
  affects:
    - tools/wacom_quant_analysis.py
    - tests/server/test_wacom_matrix_emission.py
    - requirements-dev.txt
    - docs/release.md
tech-stack:
  added:
    - "scipy>=1.14 (interp1d timestamp alignment for D-18 RMS)"
    - "matplotlib>=3.8 (SVG rendering of pressure curves)"
  patterns:
    - "Lazy import of scipy + matplotlib inside functions (keeps --help fast and tool importable from tests without forcing matplotlib backend)"
    - "Cell + event filtering on JSONL stream (T-02-36 mitigation: malformed lines silently skipped)"
key-files:
  created:
    - tests/server/test_wacom_matrix_emission.py
  modified:
    - tools/wacom_quant_analysis.py  # full rewrite of Wave-0 stub
    - requirements-dev.txt  # +scipy +matplotlib +numpy explicit pin
    - docs/release.md  # +Wacom matrix ritual +matrix results +latency table
decisions:
  - "Tasks 2 + 3 cannot be executed from an autonomous worktree -- they require physical DXS hardware (Wacom Pro, Cintiq Pro 24, real Mac + Rocky workstations on a tailnet). Defer to a manual session and seed docs/release.md with explicit DEFERRED markers."
  - "scipy + matplotlib added to requirements-dev.txt only (not to runtime requirements-server / -client). The RMS tool is operator-local; clients and servers do not need scipy at runtime."
  - "_render_svg lazily imports matplotlib (after matplotlib.use('Agg')) so unit tests that only exercise compute_rms do not pay the matplotlib import cost."
metrics:
  completed_date: 2026-04-19
  duration_minutes: ~25
  tasks_completed_automatically: 2 of 4
  tasks_pending_manual_checkpoint: 2 of 4
---

# Phase 02 Plan 02-12: Wacom matrix verification + DXS latency measurement Summary

**One-liner:** Replaced the Wave-0 `tools/wacom_quant_analysis.py` stub with the production D-18 implementation (numpy + scipy.interpolate + matplotlib), landed 6 D-17 emission contract tests, and seeded `docs/release.md` with the four Phase 6 release-runbook sections. The 4-cell Wacom matrix session and DXS pre/post latency measurement are blocked on physical DXS hardware and remain DEFERRED.

## What was completed automatically (this run)

### Task 1 — `tools/wacom_quant_analysis.py` is real

- **Commit:** `37c5a9c`
- **Files:** `tools/wacom_quant_analysis.py` (198 lines, cap 250), `tests/server/test_wacom_matrix_emission.py` (NEW), `requirements-dev.txt` (+scipy +matplotlib +numpy)
- **Implementation:** numpy-array-based timestamp + pressure ingest from JSONL streams, `scipy.interpolate.interp1d` linear alignment of server samples to client timestamps, RMS error of the difference, matplotlib (Agg backend) SVG render. Exit codes: `0` pass, `1` fail, `2` missing/malformed.
- **Tests passed:** 6/6 in `tests/server/test_wacom_matrix_emission.py`:
  - `test_rms_on_identical_ramp_is_near_zero` (RMS < 1e-6)
  - `test_rms_on_offset_ramp_reflects_offset` (RMS ≈ 0.05)
  - `test_rms_with_missing_stream_returns_nan`
  - `test_main_exits_2_on_missing_input`
  - `test_main_exits_0_on_matched_rampy_log` (full E2E with synthetic JSONL → SVG produced → exit 0)
  - `test_main_exits_1_on_known_offset` (5% offset > 1% threshold → exit 1)
- **Acceptance criteria check:** line count ≤ 250 (198), `numpy` + `scipy.interpolate` + `matplotlib` imports present, `--help` exits 0, all 6 tests green.

### Task 4 — `docs/release.md` extended with all four sections

- **Commit:** `7f42112`
- **Sections present** (per Task 4 automated verification grep):
  1. **Phase 2 Wacom matrix ritual (per-release)** — full 9-step D-17 protocol, including the exact `wacom_quant_analysis.py` invocation per cell and the T-02-34 mitigation note that raw JSONL must not be committed.
  2. **Phase 2 matrix results** — 4-row × 3-column table (RMS / pass / video) with all 12 result cells + matrix date + Flame artist sign-off line set to `<DEFERRED — Randy to execute at DXS>`.
  3. **Phase 2 latency measurement (D-21)** — procedure + 3-row × 3-column pre/post/delta table, also DEFERRED. Reiterates that the synthetic CI gate (Phase 1 D-08/D-09 p99 < 25 ms) remains green at HEAD-of-Phase-2.
  4. **Phase 2 IOHIDUserDevice spike outcome** — already seeded by Plan 02-10 (FAIL branch); retained verbatim.
- **Existing content preserved:** the 02-10 spike outcome, health-overlay badge, PenFSM + client proximity re-synth sections, and packaging notes are untouched.

## What is awaiting manual checkpoint (DXS hardware required)

### Task 2 — 4-cell DXS Wacom matrix session (DEFERRED)

**Why this cannot run autonomously:** the D-17 6-step protocol requires a real Wacom Pro stylus + Cintiq Pro 24 physically present at a Mac client + Rocky / Mac server on a Tailscale LAN. Pen strokes, eraser flips, tilt, proximity cycles, side-button presses, and mid-stroke reconnects are all physical interactions. No autonomous environment can synthesize them honestly without falsifying the JSONL streams — and `CLAUDE.md` is explicit that fabricating Wacom data is the worst possible v1 outcome.

**Runbook for Randy at DXS:**

For each of the 4 cells (Intuos Pro L + Sonoma; Intuos Pro L + Sequoia; Cintiq Pro 24 + Sonoma; Cintiq Pro 24 + Sequoia):

```bash
# 1. Start Teraguchi server with structlog wacom_matrix telemetry enabled.
# 2. Connect the Mac client; redirect client structlog to a file.
# 3. Execute the 6-step D-17 protocol (pressure ramp, eraser flip, tilt,
#    proximity cycle, side buttons, mid-stroke reconnect) per
#    docs/release.md "Phase 2 Wacom matrix ritual (per-release)".
# 4. Capture the streams as artifacts/client_<cell>.jsonl and
#    artifacts/server_<cell>.jsonl (do NOT commit these to git -- T-02-34).
# 5. Run the RMS analysis:
python tools/wacom_quant_analysis.py \
    --client-log artifacts/client_intuos-pro-L-sonoma.jsonl \
    --server-log artifacts/server_intuos-pro-L-sonoma.jsonl \
    --cell intuos-pro-L-sonoma \
    --out-svg artifacts/rms_intuos-pro-L-sonoma.svg \
    --pass-threshold 0.01
# Expect exit 0 + a printed "cell=... RMS=... threshold=0.0100" line.
# 6. Repeat for the other 3 cells with --cell intuos-pro-L-sequoia,
#    cintiq-pro-24-sonoma, cintiq-pro-24-sequoia.
# 7. Record video of each cell's session (separate file per cell).
# 8. On the Cintiq Pro 24 + Sequoia cell, get a Flame artist to draw
#    a standard paint stroke and sign off that pressure quantization
#    is not visible. This step is MANDATORY per D-17.
# 9. Edit docs/release.md "Phase 2 matrix results" -- replace each
#    <DEFERRED — Randy to execute at DXS> cell with the captured
#    RMS value, pass / fail verdict, video link; add the matrix date
#    and the Flame artist sign-off (name + date).
```

**Pass gate (do not sign off Phase 2 if any of these fail):**

- All 4 cells exit `0` from `wacom_quant_analysis.py` (RMS < 1%).
- All 6 protocol steps pass per cell.
- Flame artist sign-off recorded on the Cintiq Pro 24 + Sequoia cell.

**Failure-path note:** if a cell fails, triage per Plan 02-12 Task 2 `<resume-signal>` -- distinguish a Phase 2 implementation bug (fix and re-run) from a real hardware driver issue (document as a v1 caveat in `docs/release.md` "Phase 2 known v1 caveats").

### Task 3 — DXS pre/post-Phase-2 latency measurement (DEFERRED)

**Why this cannot run autonomously:** the D-21 measurement requires a Mac client + Rocky Flame workstation server on the same Tailscale LAN, with real Wacom + real Cintiq + real display, running ~60 s of "typical Flame interaction" per trial × 3 trials × 2 git refs (Phase 1 HEAD vs Phase 2 HEAD). All of that requires physical DXS hardware and a Flame session.

**Runbook for Randy at DXS:**

```bash
# 1. Identify the Phase 1 verification HEAD commit:
#    .planning/phases/01-stability-ci-test-baseline/01-VERIFICATION.md
#    (or `git log --oneline | grep "Phase 1"`).
PRE_PHASE2_REF=<commit-from-01-VERIFICATION.md>
POST_PHASE2_REF=<HEAD-of-Phase-2-after-02-12-merges>

# 2. Pre-Phase-2 measurement:
git -C <DXS-checkout> checkout $PRE_PHASE2_REF
# Start Teraguchi with OBS-02 structlog per-stage latency telemetry on.
# Run 3 × ~60 s of typical Flame interaction (pen strokes, hotkeys,
# viewport navigation). Capture p50 / p95 / p99 input-to-photon per
# trial. Compute the median p99 across the 3 trials.

# 3. Post-Phase-2 measurement:
git -C <DXS-checkout> checkout $POST_PHASE2_REF
# Repeat step 2 verbatim.

# 4. Edit docs/release.md "Phase 2 latency measurement (D-21)" --
#    replace each <DEFERRED — Randy to execute at DXS> cell with the
#    measured ms value (e.g. "12.4") and the delta as a percentage
#    (e.g. "+3.2%").
```

**Pass gate (regression D-21):** post-Phase-2 p99 ≤ pre-Phase-2 p99 × 1.15.

**Bonus check (VIDEO-11 happy path):** confirm post-Phase-2 p99 < 20 ms on at least one trial. If it does not, document as a known caveat -- the CI pass / fail gate (Phase 1 D-08/D-09 p99 < 25 ms; unchanged per D-21) remains the binary signal.

## Deviations from Plan

### Auto-fixed issues

None. The plan's Tasks 1 and 4 executed exactly as written.

### Scope deviations (deliberate)

**1. [Scope] Tasks 2 and 3 deferred to manual DXS checkpoint**
- **Trigger:** spawned-agent objective explicitly excluded the two manual checkpoints (`autonomous: false` in plan frontmatter; `02-12-PLAN.md` Tasks 2 and 3 are `checkpoint:human-verify`).
- **Action taken:** populated the `docs/release.md` matrix and latency tables with explicit `<DEFERRED — Randy to execute at DXS>` markers and an inline operator note pointing to the runbook in this SUMMARY. Did NOT fabricate RMS or latency numbers — `CLAUDE.md` "Zero tolerance for Wacom pressure glitches" makes faking these values worse than admitting they are deferred.
- **Files affected:** `docs/release.md` (matrix-results table cells + latency-results table cells + matrix date + sign-off line + latency date all marked DEFERRED).
- **Resolution path:** Randy executes the runbook above at DXS; replaces DEFERRED markers with real values; commits the result. Phase 2 verification (`/gsd-verify-work 2`) should not sign off Phase 2 until both tables are populated.

## Authentication gates encountered

None. The plan involves no external auth.

## Self-Check: PASSED

- `tools/wacom_quant_analysis.py`: FOUND (198 lines, ≤ 250 cap)
- `tests/server/test_wacom_matrix_emission.py`: FOUND (6 tests, all passed in 0.4 s)
- `requirements-dev.txt`: FOUND (matplotlib, scipy, numpy lines added)
- `docs/release.md`: FOUND (4 sections present per `grep -qE "Wacom matrix ritual|Phase 2 matrix results|Phase 2 latency measurement"` + `grep -q "IOHIDUserDevice spike outcome"`)
- Commit `37c5a9c` (Task 1): present in `git log`
- Commit `7f42112` (Task 4): present in `git log`
- No STATE.md or ROADMAP.md modifications (per parallel-executor rules)

## Threat surface scan

No new threat surface introduced. The plan adds an operator-local CLI tool (`tools/wacom_quant_analysis.py`) and a markdown doc (`docs/release.md`); neither crosses a network or trust boundary. The plan's existing T-02-36 mitigation (malformed JSONL → silent skip → exit 2) is implemented in `_read_wacom_samples`. The T-02-34 information-disclosure mitigation (raw pressure / tilt JSONL must stay developer-local, not committed to the public repo) is documented in `docs/release.md` "Phase 2 Wacom matrix ritual (per-release)" verbatim.

## Known Stubs

None introduced by this plan. The Wave-0 stub at `tools/wacom_quant_analysis.py` was the *target* of this plan and has been replaced with the production implementation. The `<DEFERRED — Randy to execute at DXS>` markers in `docs/release.md` are intentional placeholders for the manual checkpoint, not stubs that prevent the plan's automated goal from being achieved — they are explicitly tracked in the "What is awaiting manual checkpoint" section above and will be resolved by Randy at DXS.

## Pointers

- Real-hardware runbook: `docs/release.md` "Phase 2 Wacom matrix ritual (per-release)"
- Latency procedure: `docs/release.md` "Phase 2 latency measurement (D-21)" → "Procedure"
- Spike outcome (already final): `docs/release.md` "Phase 2 IOHIDUserDevice spike outcome"
- D-17 emission schema canonical: `.planning/phases/02-input-color-fidelity/02-CONTEXT.md` D-17 + D-18
- Off-line analysis tests: `tests/server/test_wacom_matrix_emission.py`
- Off-line analysis tool: `tools/wacom_quant_analysis.py`
