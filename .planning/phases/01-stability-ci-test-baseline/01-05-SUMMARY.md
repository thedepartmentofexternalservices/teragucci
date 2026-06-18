---
phase: 01-stability-ci-test-baseline
plan: 05
subsystem: infra
tags: [github-actions, ci, pyinstaller, rpm, rockylinux, macos-14, ruff, mypy, pytest]

# Dependency graph
requires:
  - phase: 01-stability-ci-test-baseline
    provides: "Plan 01-03 pyproject toolchain (ruff/mypy/pytest config) that ci.yml invokes"
provides:
  - "GitHub Actions CI baseline with all 4 D-08 hard-gate job names installed"
  - "macos-14 + rockylinux:9 runner matrix per D-05/D-06 (no self-hosted)"
  - "PyInstaller .app dry-run build job on macos-14 arm64"
  - "rpmbuild --nodeps --buildonly dry-run job on rockylinux:9"
  - "Minimal Rocky 9 RPM spec (packaging/teraguchi-server.spec) that validates syntactically"
  - "latency-bench placeholder stub so D-07 branch protection references the full gate set immediately; Plan 16 swaps the body without renaming"
affects:
  - "01-06 — all subsequent Phase 1 plans merge through green CI"
  - "01-16 — replaces latency-bench body with synthetic-stage-time accumulator (job name preserved)"
  - "01-17 — Plan 17 adds smoke-nightly.yml (separate workflow, out of scope here)"
  - "06-distribution — Phase 6 DIST-02 reuses packaging/teraguchi-server.spec as the signing base"

# Tech tracking
tech-stack:
  added:
    - "GitHub Actions (actions/checkout@v4, actions/setup-python@v5, actions/cache@v4, actions/upload-artifact@v4)"
    - "PyInstaller 6.11-7.0 constraint for macos-14 arm64 builds"
    - "rpmbuild (RPM spec dry-run validation)"
  patterns:
    - "Two-workflow split: ci.yml for fast gates (pytest/lint/latency), build-artifacts.yml for slower packaging jobs"
    - "concurrency.cancel-in-progress across both workflows to save CI minutes on PR churn"
    - "Rocky 9 Python 3.12 via EPEL (default dnf python3 is 3.9) — documented in YAML comments"
    - "Job-name stability contract: latency-bench keeps its name across Plan 05 → Plan 16 so D-07 branch protection never needs reconfiguration"

key-files:
  created:
    - ".github/workflows/ci.yml — four D-08 hard-gate jobs"
    - ".github/workflows/build-artifacts.yml — PyInstaller + rpmbuild dry-run"
    - "packaging/teraguchi-server.spec — minimal Rocky 9 RPM spec"
  modified:
    - ".gitignore — allow packaging/*.spec through (root *.spec ignore was blocking)"

key-decisions:
  - "Installed all 4 D-08 gate job names in first ci.yml commit so D-07 branch protection configures once"
  - "latency-bench job is a PLACEHOLDER exiting 0; Plan 16 replaces the body only, keeps the name + D-09 comment"
  - "RPM spec ships minimal %files (empty) — Phase 6 DIST-02 owns real payload + signing"
  - "Dry-run uses rpmbuild --nodeps so CI container doesn't need ffmpeg-free installed; spec syntax still fails loudly"
  - ".gitignore exception for packaging/*.spec (global *.spec ignore was PyInstaller-oriented, wrong for RPM specs)"

patterns-established:
  - "Job-name contract across plans: D-07 branch protection selectors survive body swaps (e.g., Plan 05 → 16)"
  - "Dry-run vs production split: Phase 1 installs structure (specs, workflows); Phase 6 owns signing/distribution"
  - "EPEL-first for Rocky 9 Python: dnf install epel-release && python3.12 is the canonical recipe"

requirements-completed: [STAB-02, STAB-03]

# Metrics
duration: ~3min
completed: 2026-04-18
---

# Phase 01 Plan 05: CI Baseline Summary

**GitHub Actions CI installed with all four D-08 hard gates (pytest on macos-14 + rockylinux:9, ruff+mypy, PyInstaller .app + rpmbuild dry-run, latency-bench placeholder) plus a minimal Rocky 9 RPM spec, unblocking D-07 branch protection from the first commit.**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-04-19T00:27:54Z
- **Completed:** 2026-04-19T00:30:25Z
- **Tasks:** 2
- **Files created:** 3 (ci.yml, build-artifacts.yml, teraguchi-server.spec)
- **Files modified:** 1 (.gitignore)

## Accomplishments
- Installed `.github/workflows/ci.yml` with all four D-08 hard-gate job names from first commit: `lint-typecheck`, `test-linux`, `test-macos`, `latency-bench`. The `latency-bench` body is a placeholder that exits 0; Plan 16 replaces the body only, preserving the job name so D-07 branch protection never needs reconfiguration.
- Installed `.github/workflows/build-artifacts.yml` with `build-mac-app` (PyInstaller on macos-14 arm64, uploads Teraguchi-app-arm64 artifact with 7-day retention) and `build-rpm-dryrun` (rpmbuild --nodeps --buildonly on rockylinux:9).
- Installed `packaging/teraguchi-server.spec` — a minimal noarch Rocky 9 RPM spec with the correct `Requires:` block mirroring `install-server.sh`. Empty `%files` keeps this a dry-run validator; Phase 6 DIST-02 populates for real distribution.
- All four GHA actions pinned at current majors per RESEARCH §"Dependencies & Versions": `actions/checkout@v4`, `actions/setup-python@v5`, `actions/cache@v4`, `actions/upload-artifact@v4`.
- Rocky 9 container uses EPEL for Python 3.12 per RESEARCH Pitfall 5 (`dnf -y install epel-release && dnf -y install python3.12`).
- macOS job sets `QT_QPA_PLATFORM=offscreen` so client tests don't require a display.
- `concurrency.cancel-in-progress` on both workflows cancels stale PR builds.

## Task Commits

Each task was committed atomically:

1. **Task 1: Create ci.yml with four D-08 gate job names** — `af38293` (feat)
2. **Task 2: Create build-artifacts.yml + teraguchi-server.spec** — `c9bb3b8` (feat, includes .gitignore fix deviation)

## Files Created/Modified
- `.github/workflows/ci.yml` — Four D-08 gate jobs: lint-typecheck (ruff+mypy on ubuntu-latest), test-linux (pytest in rockylinux:9 container), test-macos (pytest on macos-14 arm64), latency-bench (placeholder stub per D-09)
- `.github/workflows/build-artifacts.yml` — build-mac-app (PyInstaller dry-run on macos-14) + build-rpm-dryrun (rpmbuild --nodeps on rockylinux:9)
- `packaging/teraguchi-server.spec` — Minimal Rocky 9 spec: `Name: teraguchi-server`, `Version: 3.0.0`, `BuildArch: noarch`, `License: ASL 2.0` (Apache 2.0 in RPM syntax), `Requires: python3.12 ffmpeg-free xorg-x11-server-Xvfb pulseaudio-utils`, empty `%files`
- `.gitignore` — Added `!packaging/*.spec` exception so the RPM spec is tracked (root `*.spec` rule was PyInstaller-oriented)

## Decisions Made
- **Two-workflow split (ci.yml + build-artifacts.yml):** ci.yml runs on every PR commit for fast feedback; build-artifacts.yml runs the slower packaging jobs. Each has `concurrency.cancel-in-progress` to stop stale builds.
- **latency-bench job is a placeholder, not deferred:** Installing the job NAME in the first commit lets D-07 branch protection select the full gate set immediately. Plan 16 replaces the job body (not the name), so branch protection never needs reconfiguration when the real benchmark lands.
- **YAML comment `# Synthetic harness-math gate per D-09` persists across Plan 05 → Plan 16:** Documents D-09 provenance for any maintainer reading the workflow later.
- **RPM spec is dry-run only (empty `%files`, License: ASL 2.0):** Per RESEARCH Open Q #2, shipping a minimal spec that `rpmbuild --nodeps --buildonly` accepts is better than skipping the gate entirely. Phase 6 DIST-02 populates `%files` and adds signing/GPG keys.
- **`--nodeps` on rpmbuild:** Runner container does not install `ffmpeg-free` at build time (only `rpm-build rpmdevtools python3.12` for the spec validation). `--nodeps` tells rpmbuild to ignore the Requires during the build; Phase 6 CI will run full dependency validation when signing is wired up.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `.gitignore` was ignoring `packaging/teraguchi-server.spec`**
- **Found during:** Task 2 (`git add packaging/teraguchi-server.spec` failed)
- **Issue:** Root `.gitignore` line 6 contains `*.spec` (intended for PyInstaller-generated specs at repo root). This glob also captured the RPM spec at `packaging/teraguchi-server.spec`, making it un-committable.
- **Fix:** Added `!packaging/*.spec` exception on a new line under the `*.spec` rule, with an inline comment explaining the Phase 1 STAB-03 origin.
- **Files modified:** `.gitignore`
- **Verification:** `git check-ignore packaging/teraguchi-server.spec` now exits 1 (no match); `git add packaging/teraguchi-server.spec` succeeds.
- **Committed in:** `c9bb3b8` (Task 2 commit — bundled the .gitignore fix with the files it unblocked)

---

**Total deviations:** 1 auto-fixed (1 blocking fix via Rule 3)
**Impact on plan:** The .gitignore exception is a one-line change required to track an RPM spec that was explicitly in the plan. No scope creep. All other plan items shipped as written.

## Issues Encountered
- None during execution. The first CI run on a PR with these workflows merged will confirm YAML validity against GHA's real parser; offline `yaml.safe_load` already confirms both files parse.

## Pre-existing untracked files (out of scope — not owned by this plan)
`git status` showed untracked files at the start of the executor (`.DS_Store`, `.claude/`, `Teraguchi.app/`, `teraguchi`, `tests/common/__init__.py`). These were present in the worktree before Plan 05 started and are out of scope per the executor's scope-boundary rule. Not added, not gitignored — left as-is for the next plan or manual cleanup.

## User Setup Required

**D-07 branch protection is a MANUAL POST-MERGE STEP.** After this plan merges to `dev`, the maintainer must configure GitHub branch protection via the UI:

1. **Settings → Branches → Add rule for `dev` (and `main`)**
2. Enable **"Require status checks to pass before merging"**
3. Select these required status checks (job names from `ci.yml` and `build-artifacts.yml`):
   - `Lint + typecheck (ruff + mypy)` (from `lint-typecheck`)
   - `Tests (Rocky 9 + Python 3.12)` (from `test-linux`)
   - `Tests (macOS 14 / Apple Silicon)` (from `test-macos`)
   - `Latency benchmark (synthetic p99 < 25 ms) — PENDING Plan 16` (from `latency-bench` — placeholder; Plan 16 renames the display name but keeps the job key `latency-bench`)
   - `PyInstaller .app (macos-14 arm64 dry-run)` (from `build-mac-app`)
   - `RPM dry-run (Rocky 9)` (from `build-rpm-dryrun`)
4. Enable **"Require branches to be up to date before merging"** so stale PRs re-run CI.

The placeholder latency-bench job exits 0 by design so the gate is green; Plan 16 replaces the body with the real synthetic-stage-time accumulator without renaming the job — zero reconfiguration needed in the branch protection rule.

No environment variables or external service configuration required.

## Next Phase Readiness

- CI baseline ready — every future Phase 1 plan merges through four green gates
- `build-artifacts.yml` + `teraguchi-server.spec` are the foundation Phase 6 DIST-02 extends (signing, GPG keys, real `%files`, real Apple notarization)
- Plan 16 has a zero-config swap: replace the `latency-bench` step body with the real benchmark; the job name, file, and D-09 comment all stay
- Plan 17 will add `.github/workflows/smoke-nightly.yml` as a separate workflow (out of scope here)
- Known concern: first real CI run may surface test failures that were latent (tests exist after Plan 01-04 lands). Those should be triaged back into Plan 04 follow-ups, not Plan 05.

## Self-Check: PASSED

Verified post-write:
- `.github/workflows/ci.yml` exists — FOUND
- `.github/workflows/build-artifacts.yml` exists — FOUND
- `packaging/teraguchi-server.spec` exists — FOUND
- `.gitignore` contains `!packaging/*.spec` — FOUND
- Commit `af38293` (Task 1) — FOUND in `git log`
- Commit `c9bb3b8` (Task 2) — FOUND in `git log`
- `grep -q "Synthetic harness-math gate per D-09" .github/workflows/ci.yml` — PASSES (Plan 16 comment preserved)
- `grep -q "latency-bench:" .github/workflows/ci.yml` — PASSES (D-07 gate name locked)
- YAML files parse via `yaml.safe_load` — PASSES for both

---
*Phase: 01-stability-ci-test-baseline*
*Completed: 2026-04-18*
