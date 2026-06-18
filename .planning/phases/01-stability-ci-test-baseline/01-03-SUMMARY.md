---
phase: 01-stability-ci-test-baseline
plan: 03
subsystem: infra
tags: [pyproject, pytest, pytest-asyncio, ruff, mypy, structlog, python-statemachine, packaging, python3.12, apache-2.0]

# Dependency graph
requires:
  - phase: 01-stability-ci-test-baseline
    provides: "Plans 01 (send_queue fix) + 02 (TLS hardening) landed without needing tool config; this plan now supplies the tool config every downstream Phase 1 plan depends on"
provides:
  - "pyproject.toml tool configuration for pytest (asyncio_mode=auto, testpaths=tests, timeout=30, filterwarnings ratchet), ruff (py312, line-length 100, select E/F/W/I/UP/ASYNC), mypy (narrow Phase 1 scope: common + client/protocol.py)"
  - "Python 3.12 floor via requires-python = '>=3.12'"
  - "Apache-2.0 license metadata (corrected from MIT)"
  - "Pinned Phase 1 dev toolchain in requirements-dev.txt: pytest 8.3.x, pytest-asyncio 0.25.x, pytest-timeout/cov/xdist, ruff 0.11.x, mypy 1.14.x, psutil 6.x, freezegun, pyinstaller 6.11"
  - "Runtime deps python-statemachine>=2.6 + structlog>=25.1 present in both server and client requirements files"
affects: [01-04, 01-05, 01-06, 01-07, 01-08, 01-09, 01-10, 01-11, 01-12, 01-13, 01-14, 01-15, 01-16, 01-17]

# Tech tracking
tech-stack:
  added:
    - "pytest 8.3.x + pytest-asyncio 0.25.x + pytest-timeout 2.3+ + pytest-cov + pytest-xdist"
    - "ruff 0.11.x (lint + import sort)"
    - "mypy 1.14.x (narrow Phase 1 scope)"
    - "psutil 6.x (cross-platform RSS for smoke harness)"
    - "freezegun (time mocking for TTL tests)"
    - "python-statemachine 2.6.x (server + client runtime — STAB-06 FSM library)"
    - "structlog 25.1.x (server + client runtime — OBS-01 structured JSON logging)"
  patterns:
    - "Tool config lives in pyproject.toml [tool.*] sections; requirements files declare version pins only"
    - "filterwarnings uses targeted `ignore::` allowlists rather than a blanket 'ignore' so new DeprecationWarnings surface in CI"
    - "mypy scope starts narrow (common + client/protocol.py) and widens progressively as modules stabilize"
    - "ruff rule set deliberately small (E/F/W/I/UP/ASYNC) with UP006/UP007/UP035 ignored so Optional[...]/List[...]/Dict[...] style remains legal during Phase 1 transition"

key-files:
  created: []
  modified:
    - "pyproject.toml — added [tool.pytest.ini_options], [tool.ruff], [tool.ruff.lint], [tool.mypy]; bumped requires-python and optional-deps floors; fixed license"
    - "requirements-dev.txt — pinned pytest/ruff/mypy/test utilities toolchain"
    - "requirements-server.txt — appended python-statemachine + structlog runtime deps"
    - "requirements-client.txt — appended python-statemachine + structlog runtime deps"

key-decisions:
  - "Python floor bumped 3.10 → 3.12 per RESEARCH §'Dependencies & Versions' (3.10 EOL Oct 2026; 3.12 has clean ARM64 wheels for PySide6/PyAV/PyObjC)"
  - "License metadata corrected MIT → Apache-2.0 per CLAUDE.md hard constraint + REQUIREMENTS.md line 8 (T-1-CFG mitigation — prevents SBOM / license-scanner misidentification)"
  - "ruff pyupgrade ignores UP006/UP007/UP035: existing codebase uses pre-PEP-604 type syntax pervasively; tightening deferred to Phase 2+"
  - "mypy files scope intentionally narrow (common + client/protocol.py): wider scope lands module-by-module in later phases per RESEARCH §'Standard Stack'"
  - "filterwarnings allowlists aioquic + websockets DeprecationWarnings so upstream library noise does not fail CI, while filterwarnings = ['error', ...] default still surfaces Teraguchi-originating warnings"
  - "Broker runtime deps (aiohttp) deliberately NOT pinned here: broker is paused per PROJECT.md v1 scope and excluded from wheel via [tool.setuptools.packages.find]"
  - "Wheel optional-deps floors raised to match Phase 1 research targets (websockets 15.x, PySide6 6.10, numpy 2.1) — requirements files keep the older floors so pre-refactor runtime still installs; requirements file bumps are owned by Plan 04 install verification"

patterns-established:
  - "pyproject.toml extension pattern: runtime/build deps in [project], tool config in [tool.*], setuptools opts under [tool.setuptools.*]"
  - "requirements files are append-only for runtime deps within a phase: never rewrite, always append with section comments tying to REQ-IDs"
  - "license-of-record lives in pyproject.toml [project.license]; any mismatch with REQUIREMENTS.md / CLAUDE.md is a Rule 2 (critical) fix"

requirements-completed: [STAB-01, STAB-03]

# Metrics
duration: 7min
completed: 2026-04-19
---

# Phase 01 Plan 03: Tool Configuration Baseline Summary

**pytest/ruff/mypy tool config in pyproject.toml, Python 3.12 floor, Apache-2.0 license fix, and pinned Phase 1 toolchain (pytest 8.3, ruff 0.11, mypy 1.14) + runtime deps (python-statemachine 2.6, structlog 25.1) across requirements-dev/server/client**

## Performance

- **Duration:** 7 min
- **Started:** 2026-04-19T00:21:35Z
- **Completed:** 2026-04-19T00:28:36Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- pyproject.toml rewritten with four new `[tool.*]` sections (pytest, ruff, ruff.lint, mypy) plus Python 3.12 floor and Apache-2.0 license — validates as clean TOML and passes all 11 acceptance-grep checks
- Optional-dependencies bumped in-file to the Phase 1 research targets (websockets 15.x, numpy 2.1, PySide6 6.10) so wheel metadata reflects the floors Plans 04+ will test against
- requirements-dev.txt fully replaced with the Phase 1 pinned toolchain: pytest 8.3.x, pytest-asyncio 0.25.x, pytest-timeout 2.3+, pytest-cov, pytest-xdist, ruff 0.11.x, mypy 1.14.x, psutil 6.x, freezegun, pyinstaller 6.11.x
- requirements-server.txt + requirements-client.txt appended with python-statemachine>=2.6,<3 and structlog>=25.1,<26 (runtime deps for STAB-06 FSM + OBS-01 logging — both server-side and client-side)
- License metadata corrected MIT → Apache-2.0 (T-1-CFG threat mitigation in plan's threat model)
- Zero source code changes — config-only diff as scoped by the plan

## Task Commits

Each task was committed atomically:

1. **Task 1: Patch pyproject.toml — Python 3.12 floor, Apache-2.0 license, pytest/ruff/mypy config** — `32a066f` (chore)
2. **Task 2: Update requirements-dev.txt + requirements-server.txt + requirements-client.txt with pinned deps** — `3cb3c07` (chore)

_Note: This plan has no TDD tasks — it is pure tool configuration. First `pytest --collect-only` run will be exercised in Plan 04._

## Files Created/Modified

- `pyproject.toml` — rewrote with [tool.pytest.ini_options], [tool.ruff], [tool.ruff.lint], [tool.mypy]; bumped requires-python 3.10→3.12; fixed license MIT→Apache-2.0; bumped optional-deps floors (websockets 15.x, numpy 2.1, PySide6 6.10)
- `requirements-dev.txt` — replaced 6-line stub with full Phase 1 pinned toolchain (pytest family, ruff, mypy, test utilities, pyinstaller)
- `requirements-server.txt` — appended python-statemachine>=2.6,<3 and structlog>=25.1,<26 at end with section comment
- `requirements-client.txt` — appended python-statemachine>=2.6,<3 and structlog>=25.1,<26 at end with section comment

## Decisions Made

- **Python 3.10 → 3.12 floor:** Per RESEARCH §"Dependencies & Versions" — 3.10 reaches EOL Oct 2026, 3.12 has clean ARM64 wheels for the Phase-1-critical deps (PySide6, PyAV, PyObjC). Upgrading floor now is cheaper than a forced bump mid-phase.
- **License MIT → Apache-2.0:** CLAUDE.md "Hard constraints" lists Apache 2.0 as the project license; REQUIREMENTS.md line 8 says "License | Apache 2.0". The old MIT metadata was a stale leftover from an earlier generation of the codebase. Flipping here in Plan 03 lets every downstream plan assume correct license-of-record.
- **ruff ignores UP006/UP007/UP035 in Phase 1:** Codebase currently uses `Optional[...]`, `List[...]`, `Dict[...]` pervasively (see `.planning/codebase/CONVENTIONS.md §"Type Hints"`). Forcing pyupgrade to rewrite every file to PEP-604 unions is out of scope for Phase 1 stability work. Deferred to Phase 2+.
- **mypy scope intentionally narrow (`files = ["common", "client/protocol.py"]`):** Per RESEARCH §"Standard Stack" recommendation — widening mypy scope is a per-module progressive exercise, not a big-bang rollout. New FSM module in Plan 06 will add itself to this list.
- **Wheel optional-deps floors bumped; requirements files deliberately NOT bumped:** Reason — Plan 03 is config-only, no install verification. Plan 04 owns the actual `pip install -r` verification that confirms the higher floors resolve cleanly on Python 3.12. If Plan 04's install surfaces a conflict, that's where the requirements-file floor gets reconciled.
- **Broker deps (aiohttp) left alone:** Broker is paused per PROJECT.md v1 scope. requirements-broker.txt is out of Plan 03's frontmatter-declared `files_modified` list, so not touched.

## Deviations from Plan

None — plan executed exactly as written. The frontmatter `must_haves.truths` listed a slight mismatch vs. the task action body (frontmatter said `line-length=100` as the assertion but also mentioned 120 in one orchestrator spawn note; the `<action>` block specified 100 and acceptance criteria tested for py312/pytest/mypy fundamentals — followed the action body authoritatively). All 11 Task 1 acceptance-grep checks and all 16 Task 2 acceptance-grep checks passed without modification.

## Issues Encountered

- None. Both writes completed on first attempt; TOML parsed cleanly; no BOMs or CRLF issues; no deletions; no untracked file leakage.
- `grep -c "^\[tool\." pyproject.toml` returns 5 rather than the 3 mentioned in `<verification>` step 2. This is not a deviation — the 5 is correct: `[tool.pytest.ini_options]` + `[tool.ruff]` + `[tool.ruff.lint]` + `[tool.mypy]` + preserved `[tool.setuptools.packages.find]`. The verification comment's "3" referred to the three *new* tool families (pytest, ruff, mypy), not a literal count inclusive of the preserved setuptools section.

## User Setup Required

None — no external service configuration required. Plan 04's `pip install -r requirements-dev.txt` (on Python 3.12) is the first time these pins will be resolver-tested.

## Next Phase Readiness

- **Plan 04 (pytest install + smoke tests):** Can proceed immediately — `pip install -r requirements-dev.txt` now pulls the full Phase 1 toolchain. `pytest --collect-only` will discover tests under `tests/` per `testpaths = ["tests"]`, with asyncio tests auto-running via `asyncio_mode = "auto"`.
- **Plan 05 (GitHub Actions CI):** Can proceed — CI workflow can invoke `ruff check .`, `mypy`, and `pytest` directly against the `[tool.*]` config now in pyproject.toml, no extra `pytest.ini` or `.ruff.toml` coordination needed.
- **Plan 06 (structlog logging schema):** `structlog>=25.1,<26` is now a runtime dep on both server and client; Plan 06 can `import structlog` without adding a requirements line.
- **Plan 07+ (FSM):** `python-statemachine>=2.6,<3` likewise available server + client; the FSM module can be added to the mypy `files = [...]` list when Plan 07 lands.
- **No blockers.** Downstream plans have the toolchain and runtime deps they need.

## Self-Check: PASSED

Verified after SUMMARY write:

- `pyproject.toml` exists at repo root — FOUND
- `requirements-dev.txt`, `requirements-server.txt`, `requirements-client.txt` all exist — FOUND
- Commit `32a066f` exists in worktree branch — FOUND
- Commit `3cb3c07` exists in worktree branch — FOUND
- `grep -q "Apache-2.0" pyproject.toml` — PASS
- `grep -q 'requires-python = ">=3.12"' pyproject.toml` — PASS
- `grep -q 'asyncio_mode = "auto"' pyproject.toml` — PASS
- No source code files in `git diff --name-only ebb6722..HEAD` outside `pyproject.toml` + the three requirements files — PASS

---
*Phase: 01-stability-ci-test-baseline*
*Completed: 2026-04-19*
