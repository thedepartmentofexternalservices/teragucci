---
phase: 1
slug: stability-ci-test-baseline
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-18
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution. Sourced from `01-RESEARCH.md` §"Validation Architecture".

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest` 8.3+ with `pytest-asyncio` 0.25+ (`asyncio_mode = "auto"`) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` section (**NEW — Wave 0**) |
| **Quick run command** | `python -m pytest tests/common tests/server tests/client tests/broker -x --timeout=30` |
| **Full suite command** | `python -m pytest -x --timeout=60` |
| **Lint quick check** | `python -m ruff check . && python -m ruff format --check .` |
| **Type quick check** | `python -m mypy common/ client/protocol.py` |
| **Estimated runtime** | Quick ~15-30s local · Full ~3-5 min local (< 10 min CI) |

---

## Sampling Rate

- **After every task commit:** Run the **quick** suite (lint + typecheck + `tests/common tests/server tests/client tests/broker`; excludes `tests/integration` + `tests/smoke`). Target: < 30s local.
- **After every plan wave:** Run the **full** suite including `tests/integration`. Target: < 5 min local, < 10 min CI.
- **Before `/gsd-verify-work 1`:** Full suite green + all 4 CI jobs green on the PR + one nightly smoke run green on **each** runner (macos-14 + rockylinux:9).
- **Max feedback latency:** 30 seconds per task commit.

---

## Per-Task Verification Map

> Task IDs populated from each plan's `<id>` field (populated 2026-04-18 during revision iteration 1). `nyquist_compliant: false` remains until Wave 0 runs green; the executor flips the flag to `true` after Wave 0 completes.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 1-04-01 | 04 | 2 | STAB-01 | — | N/A | unit | `pytest tests/common/test_messages.py -x` | ❌ W0 | ⬜ pending |
| 1-04-01 | 04 | 2 | STAB-01 | — | N/A | unit | `pytest tests/common/test_keymap.py -x` | ❌ W0 | ⬜ pending |
| 1-04-01 | 04 | 2 | STAB-01 | — | N/A | unit | `pytest tests/common/test_jitter_buffer.py -x` | ❌ W0 | ⬜ pending |
| 1-04-01 | 04 | 2 | STAB-01 | — | N/A | unit | `pytest tests/common/test_hybrid_transport.py -x` | ❌ W0 | ⬜ pending |
| 1-04-02 | 04 | 2 | STAB-01 | — | PAM mock boundary | unit | `pytest tests/server/test_auth.py tests/server/test_pam_auth.py -x` | ❌ W0 | ⬜ pending |
| 1-04-02 | 04 | 2 | STAB-01 | T-1-02 | HMAC round-trip + TTL + tamper + compare_digest | unit | `pytest tests/broker/test_tokens.py -x` | ❌ W0 | ⬜ pending |
| 1-04-02 | 04 | 2 | STAB-01 | T-1-03 | Shell-injection regex stub | unit | `pytest tests/broker/test_tokens.py::test_bus_id_regex_accepts_valid_and_rejects_injection -x` | ❌ W0 | ⬜ pending |
| 1-04-02 | 04 | 2 | STAB-01 | — | XOR encrypt/decrypt round-trip | unit | `pytest tests/client/test_bookmarks.py -x` | ❌ W0 | ⬜ pending |
| 1-09-03 | 09 | 5 | STAB-01 | — | Full FSM-driven auth path (multi-module integration) | integration | `pytest tests/integration/test_auth_flow.py -x` | ❌ W0 | ⬜ pending |
| 1-05-01 | 05 | 2 | STAB-02 | — | N/A | workflow | `.github/workflows/ci.yml` exists + macos-14 + rockylinux:9 jobs green | ❌ W0 | ⬜ pending |
| 1-05-01 | 05 | 2 | STAB-03 | — | N/A | workflow | `ruff check` + `mypy` exit 0 in CI (lint-typecheck job) | ❌ W0 | ⬜ pending |
| 1-05-02 | 05 | 2 | STAB-02 | — | N/A | workflow | `.github/workflows/build-artifacts.yml` dry-run passes (build-mac-app + build-rpm-dryrun) | ❌ W0 | ⬜ pending |
| 1-01-02 | 01 | 1 | STAB-04 | T-1-04 | `send_queue` drop triggers exactly one IDR per drop streak | unit | `pytest tests/server/test_pipelines.py::test_send_queue_idr_on_drop -x` | ❌ W0 | ⬜ pending |
| 1-09-01 | 09 | 5 | STAB-05 | — | Characterization: server/main.py behaviour unchanged post-extraction | integration | `pytest tests/integration/test_server_bootstrap.py -x` | ❌ W0 | ⬜ pending |
| 1-09-04 | 09 | 5 | STAB-05 | — | Characterization: client/main.py behaviour unchanged post-extraction (D-10 safety net for Plan 12) | integration | `pytest tests/integration/test_client_bootstrap.py -x` | ❌ W0 | ⬜ pending |
| 1-09-02 | 09 | 5 | STAB-05 | — | D-02 mocked ffmpeg subprocess boundary holds | unit | `pytest tests/server/test_video_encoder_mock.py -x` | ❌ W0 | ⬜ pending |
| 1-10-01 | 10 | 6 | STAB-05 | — | Server extraction: EncoderLifecycle module created; characterization tests still green | unit + integration | `pytest tests/server tests/integration/test_server_bootstrap.py -x` | ❌ W0 | ⬜ pending |
| 1-11-01 | 11 | 7 | STAB-05 | — | Server extraction: SessionRuntime + handle_client split; characterization tests still green | integration | `pytest tests/integration/test_server_bootstrap.py -x` | ❌ W0 | ⬜ pending |
| 1-12-01 | 12 | 7 | STAB-05 | — | Client extraction: 5 modules (app, main_window, tab_manager, session_view, connection_supervisor); client characterization still green | integration | `pytest tests/integration/test_client_bootstrap.py -x` | ❌ W0 | ⬜ pending |
| 1-07-01 | 07 | 3 | STAB-06 | — | FSM state tables (CLIENT_STATES + SERVER_STATES + ALLOWED_PAIRS) authoritative | unit | `pytest tests/common/test_session_fsm.py -x` | ❌ W0 | ⬜ pending |
| 1-08-01 | 08 | 4 | STAB-06 | — | HealthPing.client_state + HealthPong.server_state dataclass round-trip | unit | `pytest tests/common/test_messages.py::test_healthping_includes_client_state tests/common/test_messages.py::test_healthpong_includes_server_state -x` | ❌ W0 | ⬜ pending |
| 1-08-02 | 08 | 4 | STAB-06 | — | FSM state transitions only follow allowed pairs; direction-inverted ping/pong round-trips both states | integration | `pytest tests/integration/test_fsm_state_sync.py -x` | ❌ W0 | ⬜ pending |
| 1-13-01 | 13 | 8 | STAB-07 | — | All 4 pipeline queues enforce documented drop policy | unit | `pytest tests/server/test_pipelines.py -x` | ❌ W0 | ⬜ pending |
| 1-12-02 | 12 | 7 | STAB-08 | — | ConnectionSupervisor backoff + jitter + retry cap + FSM drive | unit | `pytest tests/client/test_connection_supervisor.py -x` | ❌ W0 | ⬜ pending |
| 1-12-03 | 12 | 7 | STAB-08 | — | ConnectionSupervisor end-to-end reconnect against loopback server | integration | `pytest tests/integration/test_reconnect.py -x` | ❌ W0 | ⬜ pending |
| 1-17-01 | 17 | 10 | STAB-09 | — | 1-hour synthetic smoke harness green; all 4 D-17 assertions pass | smoke | `pytest tests/smoke/test_synthetic_1h.py -v --timeout=4000` | ❌ W0 | ⬜ pending |
| 1-02-01 | 02 | 1 | SEC-01 | T-1-01, T-1-04 | Double-gate helper + test CA fixture land | unit | `pytest --co tests/integration/conftest.py && python3 -c "from common.tls_opt_out import insecure_tls_allowed"` | ❌ W0 | ⬜ pending |
| 1-02-02 | 02 | 1 | SEC-01 | T-1-01 | All 4 `CERT_NONE` sites removed; client rejects self-signed cert from wrong CA | integration | `pytest tests/integration/test_tls_verify.py -x` | ❌ W0 | ⬜ pending |
| 1-06-01 | 06 | 3 | OBS-01 | T-1-04 | structlog JSON emits include canonical schema (event, phase, session_id, client_id, stage, level, ts, logger); session-scope warning processor + redaction | unit | `pytest tests/common/test_logging.py tests/common/test_errors.py -x` | ❌ W0 | ⬜ pending |
| 1-06-02 | 06 | 3 | OBS-01 | — | Entry-point wiring: configure(phase=...) in server/client/broker main() | unit | `grep -q 'phase="server"' server/main.py && grep -q 'phase="client"' client/main.py && grep -q 'phase="broker"' broker/main.py` | ❌ W0 | ⬜ pending |
| 1-14-01 | 14 | 8 | OBS-02 | — | Per-stage latency breakdown emitted in HealthStats | unit | `pytest tests/server/test_health_monitor.py -x` | ❌ W0 | ⬜ pending |
| 1-14-02 | 14 | 8 | OBS-02 | — | Latency breakdown round-trips over loopback | integration | `pytest tests/integration/test_latency_breakdown.py -x` | ❌ W0 | ⬜ pending |
| 1-14-01 | 14 | 8 | OBS-03 | — | keyframe_requested / keyframe_emitted counters exposed | unit | `pytest tests/server/test_health_monitor.py::test_keyframe_telemetry -x` | ❌ W0 | ⬜ pending |
| 1-15-01 | 15 | 9 | OBS-05 | T-1-05 | Diagnostic bundle contains all documented sections; redaction rules enforced | integration | `pytest tests/integration/test_diag_bundle.py -x` | ❌ W0 | ⬜ pending |
| 1-16-01 | 16 | 9 | D-08 gate | — | Latency p99 < 25 ms LAN in synthetic benchmark (D-09 harness-math gate; replaces Plan 05's placeholder stub) | smoke | `pytest tests/smoke/test_latency_benchmark.py -x -m latency_bench` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*Task IDs populated from plan `<id>` fields. Every row is traceable back to a specific task. `nyquist_compliant` flips to `true` after the executor lands Wave 0 green (not the planner's job to flip).*

---

## Wave 0 Requirements

Every item below is a **prerequisite** before the per-task matrix can turn green. All are net-new (zero tests today, zero CI today). The owning plan is noted in `[Plan NN]` brackets for traceability.

### Config

- [ ] `pyproject.toml` — add `[tool.pytest.ini_options]`, `[tool.ruff]`, `[tool.mypy]`; bump `requires-python = ">=3.12"` — [Plan 03]
- [ ] `requirements-dev.txt` — add `pytest==8.3.*`, `pytest-asyncio==0.25.*`, `pytest-timeout`, `pytest-cov`, `pytest-xdist`, `ruff==0.11.*`, `mypy==1.14.*`, `python-statemachine>=2.6`, `structlog==25.1.*`, `psutil`, `freezegun` — [Plan 03]
- [ ] `tests/conftest.py` — root fixtures: TLS CA builder, structlog capture, offscreen Qt, canned encoded frames — [Plan 01]

### `tests/common/`

- [ ] `tests/common/__init__.py` — [Plan 04]
- [ ] `tests/common/test_messages.py` — STAB-01 — [Plan 04]
- [ ] `tests/common/test_keymap.py` — STAB-01 — [Plan 04]
- [ ] `tests/common/test_jitter_buffer.py` — STAB-01 — [Plan 04]
- [ ] `tests/common/test_hybrid_transport.py` — STAB-01 — [Plan 04]
- [ ] `tests/common/test_session_fsm.py` — STAB-06 — [Plan 07]
- [ ] `tests/common/test_logging.py` — OBS-01 — [Plan 06]
- [ ] `tests/common/test_errors.py` — OBS-01 / error taxonomy — [Plan 06]

### `tests/server/`

- [ ] `tests/server/__init__.py` — [Plan 01]
- [ ] `tests/server/test_auth.py` — STAB-01 — [Plan 04]
- [ ] `tests/server/test_pam_auth.py` — STAB-01 with mocked `pam.pam()` — [Plan 04]
- [ ] `tests/server/test_video_encoder_mock.py` — D-02 mocked FFmpeg subprocess — [Plan 09]
- [ ] `tests/server/test_pipelines.py` — STAB-04, STAB-07 — [Plan 01] (STAB-04 send_queue IDR), [Plan 13] (STAB-07 bounded queues)
- [ ] `tests/server/test_health_monitor.py` — OBS-02, OBS-03 — [Plan 14]

### `tests/broker/`

- [ ] `tests/broker/__init__.py` — [Plan 04]
- [ ] `tests/broker/test_tokens.py` — STAB-01 + T-1-02 + T-1-03 stub — [Plan 04]

### `tests/client/`

- [ ] `tests/client/__init__.py` — [Plan 04]
- [ ] `tests/client/test_bookmarks.py` — STAB-01 — [Plan 04]
- [ ] `tests/client/test_connection_supervisor.py` — STAB-08 — [Plan 12]

### `tests/integration/`

- [ ] `tests/integration/__init__.py` — [Plan 02]
- [ ] `tests/integration/conftest.py` — loopback server/client harness (D-03) + TLS CA fixtures — [Plan 02]
- [ ] `tests/integration/test_auth_flow.py` — STAB-01 integration (full FSM auth path) — [Plan 09]
- [ ] `tests/integration/test_tls_verify.py` — SEC-01 — [Plan 02]
- [ ] `tests/integration/test_fsm_state_sync.py` — STAB-06 — [Plan 08]
- [ ] `tests/integration/test_reconnect.py` — STAB-08 — [Plan 12]
- [ ] `tests/integration/test_diag_bundle.py` — OBS-05 — [Plan 15]
- [ ] `tests/integration/test_latency_breakdown.py` — OBS-02 — [Plan 14]
- [ ] `tests/integration/test_server_bootstrap.py` — STAB-05 characterization (server, pre-Plans-10/11) — [Plan 09]
- [ ] `tests/integration/test_client_bootstrap.py` — STAB-05 characterization (client, pre-Plan-12 D-10 safety net) — [Plan 09]

### `tests/smoke/`

- [ ] `tests/smoke/__init__.py` — [Plan 17]
- [ ] `tests/smoke/fixtures/canned_encoded_frames.bin` — D-02 canned data — [Plan 01]
- [ ] `tests/smoke/test_latency_benchmark.py` — D-08 gate, D-09 synthetic accumulator — [Plan 16]
- [ ] `tests/smoke/test_synthetic_1h.py` — STAB-09 (Phase 1 = 1-hour per D-18) — [Plan 17]

### CI Workflows

- [ ] `.github/workflows/ci.yml` — STAB-02, STAB-03 (pytest + ruff + mypy on macos-14 + rockylinux:9) + latency-bench placeholder from Plan 05 (Plan 16 replaces body) — [Plan 05 installs; Plan 16 replaces body]
- [ ] `.github/workflows/build-artifacts.yml` — D-08 artifact gate (PyInstaller `.app` arm64-only; RPM spec `rpmbuild --buildonly` dry-run) — [Plan 05]
- [ ] `.github/workflows/smoke-nightly.yml` — D-16 nightly cron (1-hour harness per D-18) — [Plan 17]

### Framework install

Covered once `requirements-dev.txt` diff lands and Wave 0 executes `pip install -r requirements-dev.txt`.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real-hardware before/after latency comparison on DXS Flame box | CONTEXT.md §"Specific Ideas" (no-regression core-value check) | GitHub-hosted CI uses mocked encoders (D-02); real-NVENC/VideoToolbox numbers must come from a dev workstation | On `dxs-flame-01`: run a full Flame session pre-Phase-1-merge → capture p99 input-to-photon → run same session post-merge → assert delta < 5% |
| PyInstaller `.app` on macos-14 arm64 actually launches (not just exit-code 0) | D-08 artifact gate | CI smoke-builds the `.app` but only asserts exit code; a launch check would require `open -W` + window inspection that GHA can't reliably do | On Randy's Mac Studio: download the CI artifact, `open Teraguchi.app`, confirm window appears, close cleanly |
| Rocky 9 RPM install on a fresh VM | D-08 artifact gate | CI dry-runs `rpmbuild --buildonly`; actual `dnf install ./teraguchi-server-*.rpm` on a clean Rocky 9 VM confirms deps + postinstall | On a throwaway Rocky 9 VM: `dnf install ./<rpm>` then `systemctl status teraguchi-server` |
| `python-statemachine` async transition race under real socket backpressure | STAB-06 + A6 in research assumptions log | Unit tests use in-process loopback; real socket backpressure needs wire latency | On LAN between Mac Studio and dxs-flame-07: drive handshake → streaming → degraded → streaming loop 100× while throttling via `tc qdisc`; assert no `UnknownStateError` |
| D-07 branch protection configuration | D-07 (CONTEXT.md) | GitHub UI-only setting; Claude cannot write repo-settings.json | After Plan 05 lands, enable branch protection in GitHub UI for `dev` and `main`; require all four hard-gate job names: `lint-typecheck`, `test-linux`, `test-macos`, `build-mac-app`, `build-rpm-dryrun`, `latency-bench`. The `latency-bench` job is a Plan 05 placeholder stub that Plan 16 replaces without renaming — no reconfiguration needed when Plan 16 lands. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies explicitly linked
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all ❌ MISSING references in the per-task matrix
- [ ] No watch-mode flags (`pytest --watch`, `ruff --watch`, etc. — all one-shot)
- [ ] Feedback latency < 30s on quick suite
- [ ] `nyquist_compliant: true` flipped in frontmatter by executor AFTER Wave 0 runs green (not by planner)

**Approval:** pending
