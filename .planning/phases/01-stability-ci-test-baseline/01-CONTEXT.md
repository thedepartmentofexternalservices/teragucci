# Phase 1: Stability + CI + Test Baseline — Context

**Gathered:** 2026-04-18
**Status:** Ready for planning

<domain>
## Phase Boundary

Make the existing prototype trustworthy. Three documented critical-path blockers must clear before any later phase has meaning:

1. `server/main.py:639` — `send_queue(maxsize=30)` → `maxsize=4` with request-IDR-on-drop (STAB-04)
2. `ssl.CERT_NONE` removed from all four locations: `client/protocol.py:296, 365`, `common/quic_transport.py:414`, `broker/pool.py:153` (SEC-01)
3. Zero pytest tests + zero CI today — install both with critical-path coverage (STAB-01, STAB-02, STAB-03)

Then decompose the two ~1,191-line monoliths (`server/main.py`, `client/main.py`) under tests, introduce an explicit `SessionFSM` on both ends with state serialized into health pings (STAB-06), enforce bounded queues across the capture→encode→transport pipeline (STAB-07), add a `ConnectionSupervisor` for client-side reconnect (STAB-08), and instrument structured logging + per-stage latency telemetry so every later phase has a measurement substrate (OBS-01..03, OBS-05).

Phase 1 ships when the 8-hour smoke harness (1-hour version, see D-18) runs green nightly in CI on both Mac and Rocky 9 runners with all four hard gates intact (D-08).

**In scope:** STAB-01, STAB-02, STAB-03, STAB-04, STAB-05, STAB-06, STAB-07, STAB-08, STAB-09, SEC-01, OBS-01, OBS-02, OBS-03, OBS-05 (14 requirements).

**Out of scope:** Anything user-visible (UI hint: no), 10-bit / Wacom work (Phase 2), multi-monitor work (Phase 3), audio (Phase 4), QUIC promotion / FEC (Phase 5), packaging + signing (Phase 6), docs (Phase 7).

</domain>

<decisions>
## Implementation Decisions

### Test Strategy

- **D-01:** Coverage philosophy = **critical path only**. Lock down `common/` (messages, keymap, transport, jitter buffer), server `auth.py` + `tokens.py`, client `bookmarks.py`, and the new `SessionFSM`. No coverage-percentage gate. Tests get added to other modules as later phases touch them.
- **D-02:** Hardware mocking = **mock at the FFmpeg subprocess boundary**. Tests stub the `ffmpeg` subprocess and feed canned encoded frames. Real NVENC / VideoToolbox / Xvfb run only on integration tests against actual hardware (which today means manual / dev-machine, since CI is GitHub-hosted only).
- **D-03:** Integration tests = **in-process loopback in CI**. Full client + server stack instantiated in the same Python process, talking over localhost. Catches protocol bugs, FSM transitions, transport handshakes, FEC behavior, SESSION_RESUME flows. Fast enough for PR CI; doesn't need real GPU.
- **D-04:** Test discipline = **tests-with-code**. Every plan that touches code includes a test commitment; test and code can land in the same commit. No strict red-green-refactor TDD ceremony; no after-the-fact bug-driven testing either. CI enforces test presence implicitly via the critical-path coverage rule.

### CI Runner Topology

- **D-05:** Primary runners = **GitHub-hosted only**. `macos-14` (Apple Silicon) for Mac builds + tests; `rockylinux:9` Docker image on `ubuntu-latest` for Linux builds + tests. No self-hosted runners in v1 — keeps the contributor experience clean and avoids tying CI to the DXS lab.
- **D-06:** Mac CI = **GitHub-hosted `macos-14`**. Implications: no Wacom hardware in CI (real-Wacom tests live in Phase 2's hardware session, not in PR CI), no Apple Silicon GPU encoder validation in CI (mocked at subprocess boundary per D-02), free tier on public repo when published.
- **D-07:** Branch protection = **required from day one**. Once Phase 1 ships, every merge to `dev` (and eventually `main`) requires green CI. Solo-maintainer reality means the rule is mostly about future contributors and self-discipline; no exceptions for self-merges.
- **D-08:** CI hard gates (all four — none advisory):
  - `pytest` green (critical-path tests defined in D-01)
  - `ruff` + `mypy` clean (lint + typecheck errors block merge; warnings allowed)
  - Build artifacts succeed (PyInstaller `.app` for Mac client; RPM packaging dry-run for Rocky server — catches packaging bugs before Phase 6)
  - Latency benchmark p99 stays **< 25ms LAN** (the actual sub-20ms target is in REQUIREMENTS.md / VIDEO-11; gating at 25ms gives 5ms drift headroom before failing)
- **D-09 — Planner consideration:** the latency benchmark in CI runs against mocked encoders (per D-02), so it cannot measure real encoder time. Planner must design a synthetic-stage-time accumulator: instrument each pipeline stage with a known synthetic budget so the test asserts "the harness math works and stays within budget" rather than "real hardware is fast enough." Real-hardware latency measurement happens manually on DXS until Phase 4 audio harness extension.

### Refactor Style

- **D-10:** Decomposition approach = **incremental, test-gated**. Each module extraction is its own plan: write characterization tests against the existing monolith first, extract the module, prove characterization tests still pass, commit. Many small plans. Prevents the "big-bang refactor with no tests" landmine.
- **D-11:** `server/main.py` (1,191 lines) target = **aggressive split**. Extract approximately 6-7 modules of 100-300 lines each, with a thin entrypoint:
  - `SessionRuntime` (per-user session lifecycle)
  - `ClientSession` (per-WebSocket client connection state)
  - `StreamLoop` (h264 + jpeg encode/dispatch loops)
  - `HealthLoop` (RTT + FPS + bandwidth ping/pong)
  - `MonitorHotplugLoop` (display change detection)
  - `EncoderLifecycle` (encoder spawn / restart / quality reconfigure)
  - thin `__main__` entrypoint
- **D-12:** `client/main.py` (1,189 lines) gets the **same incremental, test-gated, aggressive-split treatment in Phase 1**. (Implicit from D-10 — the "Server first, client deferred" option was NOT selected.) Specific module boundaries for the client to be proposed during planning, mirroring the server pattern: extract `SessionView`, `MainWindow`, tab management, fullscreen toolbar, etc. Aim for ~5-7 client modules.
- **D-13:** `SessionFSM` library = **`python-statemachine`**. Mature, declarative, type-friendly, supports async transitions, ~17k weekly downloads. Used on both client (states like `connecting`, `authenticated`, `streaming`, `degraded`, `reconnecting`, `closed`) and server (states like `bootstrapping`, `streaming`, `reconfiguring`, `draining`, `closed`). State serialized into health pings per STAB-06 so disagreement surfaces immediately.
- **D-14:** Order-of-operations within Phase 1 = **critical bugs first**. Sequence:
  1. `send_queue` fix + IDR-on-drop (STAB-04 / VIDEO-06) — 5-line patch + tests
  2. `ssl.CERT_NONE` removal across all 4 locations (SEC-01) — replace with verified TLS or explicit TOFU pin where Tailscale certs aren't applicable yet (full Tailscale-cert / TOFU / corporate-CA story lives in Phase 6)
  3. Pytest baseline + GitHub Actions CI green on macos-14 + rockylinux:9 (STAB-01, 02, 03)
  4. Then refactor under the safety net (STAB-05 server decomposition, client decomposition, FSM introduction, queue audit, supervisor)
  5. Observability instrumentation rolls in alongside the refactor (OBS-01..03, OBS-05)
  6. Smoke harness lands last and depends on everything above (STAB-09)

### 8-Hour Smoke Harness

- **D-15:** Harness style = **synthetic client + server loop**. Headless. Simulates input events at realistic rates (Wacom pressure stream, modifier chord pumps, mouse moves), decodes frames into a `numpy` sink, measures per-stage latency, RSS growth, dropped frames, audio underflows. No real Flame, no real Wacom hardware — those are caught by the Phase 2 real-hardware session, not the regression harness.
- **D-16:** Where it runs = **GitHub Actions nightly cron** (`schedule: cron`). One nightly job each on `macos-14` and `rockylinux:9` runners. Failures open a GitHub issue automatically; persistent failure across 3 nights blocks the next release tag.
- **D-17:** Harness assertions (all four — any one failing fails the run):
  - Zero unhandled exceptions (any uncaught exception in client or server process)
  - RSS / heap memory growth under 10% over the run window (catches leaks)
  - Per-stage latency p99 stays under 25ms LAN budget
  - Zero modifier-stuck events (FSM modifier-state-clean assertion at end of run) AND zero audio dropouts
- **D-18:** Phase 1 scope = **1-hour version**. Phase 1 ships a 1-hour synthetic harness running nightly. Phase 4 (Audio) extends it to the full 8-hour run because audio continuity (the 30-min PulseAudio hang, AirPods reconnect, sample-rate drift) needs the longer window to surface. Phase 1 establishes the harness shape, assertions, and CI integration; Phase 4 turns the dial.

### Claude's Discretion

The planner has freedom on these — they fall out naturally from the locked decisions above:

- **`structlog` schema** — required fields per event (e.g., `event`, `phase`, `session_id`, `client_id`, `stage`, `level`, `ts`); optional contextual fields. Pick a sensible default and codify in `common/logging.py`.
- **Specific FSM state set** — the rough lists in D-13 are sketches. Planner should derive the precise state set from existing `server/main.py` flow + `client/protocol.py` flow + the failure-mode catalog in `.planning/research/ARCHITECTURE.md`.
- **Diagnostic bundle format** (OBS-05) — zip layout, included files, redaction rules for credentials/tokens.
- **Error taxonomy** — exception class hierarchy and structured-error envelope for protocol-level errors.
- **Specific module boundaries for `client/main.py` decomposition** — the planner proposes them in plan-phase, mirroring D-11's server pattern.
- **Test fixture organization** — single repo-root `conftest.py` vs per-package; either works, planner picks based on what reads cleanest.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents (researcher, planner, executor) MUST read these before planning or implementing.**

### Project / Phase context

- `.planning/PROJECT.md` — Core Value, v1 scope, locked constraints, Out-of-Scope, Key Decisions table
- `.planning/REQUIREMENTS.md` — full 108 requirements; Phase 1 owns STAB-01..09, SEC-01, OBS-01, OBS-02, OBS-03, OBS-05
- `.planning/ROADMAP.md` §"Phase 1: Stability + CI + Test Baseline" — phase goal, requirement mapping, 5 success criteria, dependencies
- `.planning/STATE.md` — current position, performance metrics targets, accumulated context
- `CLAUDE.md` — project guide with critical-path summary

### Research outputs (consult before planning Phase 1 work)

- `.planning/research/SUMMARY.md` — synthesized stack prescription, critical-path table, phase-order recommendation
- `.planning/research/ARCHITECTURE.md` — FSM design pattern, pipeline-queue fix detail, TLS gap audit, failure-mode catalog with recovery patterns, suggested state machines for client + server
- `.planning/research/PITFALLS.md` — the 9 silent 10-bit downgrade points (deferred to Phase 2 verification but worth knowing); zero-tests / zero-CI compounding-risk reasoning; solo-maintainer burnout risk + mitigation
- `.planning/research/STACK.md` — Python 3.12+ floor, `structlog 25.x`, `pytest`, `python-statemachine`, `aioquic 1.3.0` (not yet promoted in this phase but referenced)

### Existing-code analysis (codebase as-it-is today)

- `.planning/codebase/ARCHITECTURE.md` — three-tier client/broker/server architecture, transport layers, session model
- `.planning/codebase/STRUCTURE.md` — file tree, module boundaries (informs the split in D-11/D-12)
- `.planning/codebase/CONCERNS.md` — known issues including the `send_queue` and `ssl.CERT_NONE` problems Phase 1 fixes
- `.planning/codebase/TESTING.md` — confirms zero-tests baseline; no `tests/` directory exists yet
- `.planning/codebase/CONVENTIONS.md` — existing coding conventions to preserve

### Documentation Phase 1 will produce or update

- `tests/` directory tree — created from scratch (no existing tests)
- `.github/workflows/` — created from scratch
- `pyproject.toml` — extend with `[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]`
- `requirements-dev.txt` — add `python-statemachine`, `structlog`, `mypy`, `ruff` (pytest already present)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- `common/messages.py` — protocol message dataclasses (already isolated, well-shaped for unit tests)
- `common/keymap.py` — `qt_key_to_linux_scancode()` table (table-driven tests slot in cleanly)
- `common/jitter_buffer.py` — already a discrete class; testable in isolation
- `common/hybrid_transport.py` (276 lines) — manageable size; FSM integration target for transport state
- `server/auth.py` + `server/pam_auth.py` + `broker/tokens.py` — auth path; high-value test targets
- `client/bookmarks.py` — encrypted-credential path (test the XOR obfuscation, then plan to swap to keychain in Phase 6)
- `server/health.py` — `HealthMonitor` already exists; OBS work extends it rather than rebuilds

### Established Patterns

- `asyncio` event loop drives every long-running component; `threading.Lock` only inside capture/encode pipelines
- `python-pam` for PAM auth (Linux only); explicit `IS_MACOS` / `IS_LINUX` branches in `server/main.py`
- Per-role `requirements-*.txt` files (server, client, broker, dev) — the FSM library and structlog go in `requirements-dev.txt` if test-only, or per-role files if runtime
- Bash installer scripts (`install-server.sh`, `install-server-macos.sh`) — tests can invoke these in `--dry-run` mode if added

### Integration Points

- **Pipeline queue fix** — `server/main.py:639` (`self.send_queue: asyncio.Queue = asyncio.Queue(maxsize=30)`); related call sites at lines 648, 659, 666. Single change site for STAB-04, but IDR-on-drop logic also touches `_on_encoded_frame` and the encoder-side keyframe-request path.
- **TLS verification fix** — four locations:
  - `client/protocol.py:295-296` — first WebSocket endpoint (`ssl_context.check_hostname = False; ssl_context.verify_mode = ssl.CERT_NONE`)
  - `client/protocol.py:364-365` — second WebSocket endpoint (broker connect)
  - `common/quic_transport.py:414` — QUIC client config
  - `broker/pool.py:153` — `aiohttp.TCPConnector(ssl=False)` for health probes
- **Decomposition** — `server/main.py` is 1,191 lines; `client/main.py` is 1,189 lines (incremental extraction targets per D-11 / D-12)
- **FSM placement** — `common/session_fsm.py` (new module) so both `server/main.py` (and its extracted children) and `client/protocol.py` import the same definitions
- **Health-ping serialization** — `common/messages.py::HealthPing` / `HealthPong` already exist; extend with `client_state` / `server_state` fields per STAB-06

</code_context>

<specifics>
## Specific Ideas

- The "Indistinguishable from local" core value drives every Phase 1 success criterion. If Phase 1 ships and the prototype is *less* responsive than before (because of FSM overhead, supervisor wrapping, or instrumentation cost), that's a regression — the Phase 1 verification step needs a before/after latency comparison on real hardware (manual, on a DXS box).
- "Bulletproof" definition stays the four-failure-modes set from initialization: zero crashes/disconnects, zero Wacom pressure glitches, zero hotkey combo mangling, zero video stutter / color shift. Phase 1 directly addresses the first failure mode (crashes/disconnects) via FSM + supervisor + queue fix; the other three are validated in Phase 2.
- Solo-maintainer burnout was flagged as the #1 existential risk in `research/PITFALLS.md`. Phase 1's CI + pytest baseline IS the burnout mitigation — no test means no regression catch means every change is risky means the maintainer stalls. This is structural support, not yak-shaving.

</specifics>

<deferred>
## Deferred Ideas

- **Self-hosted CI runners on the DXS Flame fleet** — considered, deferred. Would unlock real-NVENC, real-Xorg, real-Wacom validation in CI. Tradeoff: ties contributors to your infra and adds ops burden. Revisit if mocked-encoder testing lets too many real-hardware bugs slip through.
- **Real-hardware monthly smoke runs** — captured as a planner suggestion (a manual ritual: run a real 8-hour Flame session on dxs-flame-01 monthly + before each release tag). Not a v1 commitment; recommend adopting informally in Phase 7 when release cadence stabilizes.
- **Full 8-hour smoke harness** → Phase 4 (audio continuity timing requires the long window). Phase 1 ships the 1-hour version per D-18.
- **OpenTelemetry / Prometheus integration** — `OBS-04` (Prometheus endpoint) is explicitly assigned to Phase 5 by the roadmapper; structlog JSON logs in Phase 1 are sufficient for now. Don't pull OBS-04 forward.
- **Tailscale-native cert provisioning, TOFU fingerprint pinning, corporate CA bundle** (SEC-02..04) — all deferred to Phase 6. SEC-01 in Phase 1 just removes `CERT_NONE`; the verified-cert story uses standard certs (system trust store) until Phase 6 builds out the Tailscale-aware path.
- **Diagnostic bundle export command** (OBS-05) — in Phase 1 scope but the exact format / redaction rules are planner discretion. If complex, can ship a v0 zip of logs in Phase 1 and refine in Phase 6 alongside the security audit.

### Reviewed Todos (not folded)

None — `node ~/.claude/get-shit-done/bin/gsd-tools.cjs list-todos` returned `count: 0` at discussion time.

</deferred>

---
*Phase: 01-stability-ci-test-baseline*
*Context gathered: 2026-04-18*
