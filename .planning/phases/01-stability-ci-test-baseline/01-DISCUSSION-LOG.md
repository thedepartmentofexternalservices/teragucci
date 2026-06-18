# Phase 1: Stability + CI + Test Baseline — Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in `01-CONTEXT.md` — this log preserves the alternatives considered.

**Date:** 2026-04-18
**Phase:** 1-stability-ci-test-baseline
**Areas discussed:** Test Strategy, CI Runner Topology, Refactor Style, 8-Hour Smoke Harness

---

## Gray Area Selection

| Option | Description | Selected |
|--------|-------------|----------|
| Test strategy | Unit vs integration, mocking strategy, coverage target, TDD discipline | ✓ |
| CI runner topology | GitHub-hosted vs self-hosted DXS, branch protection, gate scope | ✓ |
| Refactor style | Big-bang vs incremental for the two ~1,191-line monoliths; FSM library; order | ✓ |
| 8-hour smoke harness | Synthetic vs real-hardware, where it runs, what it asserts, Phase 1 scope | ✓ |

**User selected:** all four.

---

## Test Strategy

### Coverage Philosophy

| Option | Description | Selected |
|--------|-------------|----------|
| Critical path only | Lock down `common/`, server auth/tokens, client bookmarks, SessionFSM. No coverage % chase. | ✓ |
| 80%+ across the board | Aim for high overall coverage from day one. CI fails on coverage drop. | |
| Change-only / regression-on-bug | Tests when bugs found or modules refactored. | |
| Let me describe | Different angle | |

**User's choice:** Critical path only.
**Notes:** Drives the bar all later phases inherit.

### Hardware Mocking

| Option | Description | Selected |
|--------|-------------|----------|
| Mock at FFmpeg subprocess boundary | Stub subprocess, feed canned encoded frames. Real encoder only on integration tests on hardware. | ✓ |
| Use software encoders in CI | libx264/libx265 software paths in GitHub-hosted CI. Slower; more realistic; misses NVENC bugs. | |
| Skip hardware tests in CI entirely | Hardware tests on self-hosted only. CI runs pure-Python tests. | |
| Hybrid: mocked in CI + real on self-hosted nightly | Best coverage; most ops. | |

**User's choice:** Mock at FFmpeg subprocess boundary.
**Notes:** Pairs with D-09 — the latency benchmark in CI needs synthetic stage-time accumulation since it can't measure real encoder time.

### Integration Tests in v1

| Option | Description | Selected |
|--------|-------------|----------|
| In-process loopback in CI | Full client+server in one process over localhost. Catches protocol/FSM/transport bugs. | ✓ |
| Two-process via Docker compose | Real client process talking to real server inside Docker. | |
| Self-hosted only — real Mac client + Rocky server | Maximum realism, no CI gating. | |
| No integration tests in v1 | Unit tests only. | |

**User's choice:** In-process loopback in CI.

### TDD Discipline

| Option | Description | Selected |
|--------|-------------|----------|
| Tests-with-code | Test+code in same commit; no strict TDD ceremony. | ✓ |
| Strict TDD, test-first | Red-green-refactor; test commits before implementation. | |
| Tests after, before merge | Code lands first; tests required before done; coverage check enforces. | |
| Bug-driven only | Tests only when bugs found. | |

**User's choice:** Tests-with-code.

---

## CI Runner Topology

### Primary Runners

| Option | Description | Selected |
|--------|-------------|----------|
| GitHub-hosted only | macos-14 + rockylinux:9 container. Free, zero ops, contributor-friendly. Mocked encoders handle no-NVIDIA constraint. | ✓ |
| Hybrid: GitHub + self-hosted DXS | GitHub for unit; one DXS box runs nightly real-hardware tests. | |
| Self-hosted DXS for everything | Maximum control; ties contributors to your infra. | |
| Let me describe | | |

**User's choice:** GitHub-hosted only.

### Mac CI Runner

| Option | Description | Selected |
|--------|-------------|----------|
| GitHub-hosted macos-14 | Apple Silicon, latest macOS, generous free tier on OSS repos. | ✓ |
| Self-hosted on Mac Studio (dxs-studio-01) | Real hardware fidelity; if Studio crashes, CI is down. | |
| GitHub for unit, self-hosted Mac Studio for integration | Hybrid. | |
| Skip Mac CI for v1 | Manual on laptop before tag. | |

**User's choice:** GitHub-hosted macos-14.

### Branch Protection

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, required from day one | Once CI exists, require green for merge. No self-merge exceptions. | ✓ |
| Required for `main` only | `dev` accepts WIP; `main` requires green + signed commits. | |
| Advisory only | CI runs but doesn't block merges. | |

**User's choice:** Yes, required from day one.

### CI Gates (multi-select)

| Option | Description | Selected |
|--------|-------------|----------|
| pytest green | Unit + in-process integration must pass. | ✓ |
| ruff + mypy | Lint + typecheck errors block merge. | ✓ |
| Build artifacts succeed | PyInstaller + RPM packaging must complete. | ✓ |
| Latency benchmark stays sub-25ms | Phase-1 instrumentation produces a number; CI fails if it drifts above 25ms LAN. | ✓ |

**User's choice:** All four — every gate is hard.
**Notes:** Latency-benchmark gating against mocked encoders is non-trivial; planner needs to design synthetic stage-time accumulation.

---

## Refactor Style

### Decomposition Approach

| Option | Description | Selected |
|--------|-------------|----------|
| Incremental, test-gated | Each extraction = own plan: characterization tests first, extract, prove tests pass, commit. Many small plans. | ✓ |
| Big-bang refactor + comprehensive test sweep | One large plan does the full decomposition + tests. | |
| Prep-then-extract | Plan A: tests on monolith. Plan B: extract under tests. | |
| Server first, client deferred to later phase | Decompose server in Phase 1; defer client to Phase 3. | |

**User's choice:** Incremental, test-gated.
**Notes:** Because "Server first, client deferred" was NOT selected, both `server/main.py` AND `client/main.py` get Phase 1 decomposition. Client module boundaries to be proposed during planning.

### Module Split Target (server/main.py)

| Option | Description | Selected |
|--------|-------------|----------|
| Aggressive split | ~6-7 modules: SessionRuntime, ClientSession, StreamLoop, HealthLoop, MonitorHotplugLoop, EncoderLifecycle, thin entrypoint. | ✓ |
| Conservative split | Just SessionRuntime + ClientSession; stream loops stay in main.py. | |
| Let the planner decide | | |

**User's choice:** Aggressive split.

### SessionFSM Library

| Option | Description | Selected |
|--------|-------------|----------|
| python-statemachine | Mature, declarative, type-friendly, async-friendly. ~17k weekly downloads. | ✓ |
| transitions library | Older, more popular (~600k downloads), supports HSMs. Less Pythonic. | |
| Hand-rolled enum + dispatch | Zero deps, full control, more code. | |
| Let me describe / spike-required | | |

**User's choice:** python-statemachine.

### Order-of-Operations

| Option | Description | Selected |
|--------|-------------|----------|
| Critical bugs first | send_queue → ssl.CERT_NONE → pytest+CI → refactor under test. | ✓ |
| Tests + CI first | pytest baseline + CI green → bug fixes → refactor. "Build safety net before climbing." | |
| Parallel tracks | Bug-fix track + CI/test track simultaneously. | |
| Let the planner decide | | |

**User's choice:** Critical bugs first.

---

## 8-Hour Smoke Harness

### Harness Style

| Option | Description | Selected |
|--------|-------------|----------|
| Synthetic client + server loop | Headless. Simulates input, decodes frames, measures memory/latency/dropped frames. Catches plumbing, not workflow. | ✓ |
| Real Flame on real DXS hardware | Run against actual Flame on dxs-flame-01 with scripted input. Maximum realism, no CI. | |
| Hybrid — synthetic in CI nightly + real-hardware monthly | Both. | |
| Manual — sit and use it for 8 hours before tag | Lightweight; no automation regression coverage. | |

**User's choice:** Synthetic client + server loop.

### Where It Runs

| Option | Description | Selected |
|--------|-------------|----------|
| GitHub Actions nightly cron | Reuses GitHub-hosted runner. Free for public repos. Reports failures as GitHub issues. | ✓ |
| Self-hosted runner only | Requires self-hosted setup (declined above). | |
| Triggered on tag | Only runs on release. | |
| Locally only, not in CI | On-demand script. | |

**User's choice:** GitHub Actions nightly cron.

### Assertions (multi-select)

| Option | Description | Selected |
|--------|-------------|----------|
| Zero unhandled exceptions | Any uncaught exception fails the run. | ✓ |
| Memory growth < 10% over 8h | RSS / heap bounded. | ✓ |
| Latency p99 stays < 25ms LAN | Per-stage instrumentation budget. | ✓ |
| Zero modifier-stuck events + zero audio dropouts | FSM modifier-clean + audio continuity. | ✓ |

**User's choice:** All four assertions are hard fails.

### Phase 1 Scope

| Option | Description | Selected |
|--------|-------------|----------|
| 1-hour version in Phase 1, full 8-hour by Phase 4 | Ship 1-hour now (catches most bugs, fits CI runtime). Extend to 8-hour with Phase 4 audio (continuity needs long window). | ✓ |
| Full 8-hour in Phase 1 | Phase 1 sprawls; nightly minutes get expensive. | |
| Synthetic short in Phase 1, real 8-hour deferred to Phase 7 | Phase 1 = 5-min synthetic; 8-hour run = manual release-gate checklist. | |
| Let me describe | | |

**User's choice:** 1-hour in Phase 1, full 8-hour by Phase 4.

---

## Closing

### Final Confirmation

| Option | Description | Selected |
|--------|-------------|----------|
| Ready for context | Write CONTEXT.md and move to plan-phase. | ✓ |
| One more area | Dig into another gray area. | |
| Revisit something we discussed | Change an answer. | |

**User's choice:** Ready for context.

---

## Claude's Discretion

Areas the planner is empowered to decide without coming back to the user:

- Specific `structlog` field schema (required + optional fields)
- Specific FSM state set for client + server (derive from existing flow + research/ARCHITECTURE.md)
- Diagnostic bundle (OBS-05) zip layout + redaction rules
- Error taxonomy / structured-error envelope
- Specific module boundaries for `client/main.py` decomposition (mirror server pattern)
- pytest fixture organization (conftest.py placement strategy)

## Deferred Ideas

Surfaced during discussion but explicitly out-of-Phase-1:

- Self-hosted CI runners on DXS — could unlock real-hardware testing but ties contributors to your infra; defer
- Real-hardware monthly smoke runs — informal Phase 7 ritual recommendation
- Full 8-hour smoke harness — Phase 4 (audio continuity needs the long window)
- OpenTelemetry / Prometheus — already assigned to Phase 5 (OBS-04); don't pull forward
- Tailscale-native cert / TOFU / corporate CA — Phase 6 (SEC-02..04); Phase 1 only removes CERT_NONE

---

*Generated 2026-04-18 by gsd-discuss-phase from interactive Q&A.*
