---
phase: 01-stability-ci-test-baseline
plan: 07
subsystem: infra
tags: [fsm, state-machine, python-statemachine, session, health-ping, observability]

# Dependency graph
requires:
  - phase: 01-03
    provides: python-statemachine>=2.6,<3 pin in requirements-server.txt and requirements-client.txt
  - phase: 01-06
    provides: common/logging.py + common/errors.py (not wired yet — callbacks land in Plans 11/15)
provides:
  - common/session_fsm.py module — single source of truth for client/server session state strings
  - CLIENT_STATES tuple (8 entries) — wire contract for HealthPing.client_state
  - SERVER_STATES tuple (7 entries) — wire contract for HealthPong.server_state
  - ALLOWED_PAIRS frozenset (9 tuples) — disagreement-detection table
  - is_state_pair_allowed(client_state, server_state) -> bool helper
  - ClientFSM declarative StateMachine (disconnected → closed, 14 transition events)
  - ServerFSM declarative StateMachine (bootstrapping → closed, 14 transition events)
affects:
  - plan 01-08 (HealthPing/HealthPong extension — attaches fsm.current_state.id to wire)
  - plan 01-11 (server health loop — calls is_state_pair_allowed on every pong)
  - plan 01-15 (ConnectionSupervisor — uses ClientFSM as driver; wires async on_enter_* callbacks)
  - plan 01-17 (observability ratchet — emits fsm.state_disagreement on mismatched pairs)
  - phase-02 (input fidelity — no direct use; FSM states are shared by the session layer only)

# Tech tracking
tech-stack:
  added:
    - "python-statemachine 2.6.0 (declarative FSM library; D-13 locked)"
  patterns:
    - "Declarative StateMachine subclass: states as class attributes, transition events via `A.to(B) | C.to(B)` union syntax"
    - "Wire-serializable state strings: fsm.current_state.id returns short string ∈ CLIENT_STATES/SERVER_STATES tuples"
    - "Pair-table disagreement detection: frozenset of allowed (client, server) tuples checked on every health exchange"
    - "No side-effect callbacks in common/ module — each tier (server/client) attaches its own @on_enter_* callbacks in its own module so I/O stays in the right layer"

key-files:
  created:
    - "common/session_fsm.py (204 lines)"
    - "tests/common/test_session_fsm.py (332 lines, 35 tests)"
  modified: []

key-decisions:
  - "Do NOT wrap TransitionNotAllowed into Teraguchi's SessionError. Callers catch statemachine.exceptions.TransitionNotAllowed directly — the library exception carries enough context (source/target state + event name) and wrapping adds indirection with no benefit. If a later plan needs the Teraguchi taxonomy, add a thin adapter then."
  - "No on_enter/on_exit callbacks declared in this module. The FSM is pure model; side-effects attach in server/client_session.py (Plan 11) and client/connection_supervisor.py (Plan 15). Keeps common/ free of server-only / client-only imports."
  - "ALLOWED_PAIRS is a frozenset, not a plain set — immutable wire contract. Tests assert isinstance(ALLOWED_PAIRS, frozenset)."
  - "is_state_pair_allowed() returns False for any state string not in CLIENT_STATES/SERVER_STATES — defense against out-of-version peers smuggling novel state strings past the disagreement check."
  - "transport_lost has 5 origin states on the client (streaming, degraded, handshaking, authenticating, capability_exchange). Extended from the plan's 4 to include capability_exchange because a transport drop mid-capability-exchange must also land in reconnecting rather than raise TransitionNotAllowed (Rule 2 — missing critical edge)."
  - "user_quit covers all 7 non-closed client states (disconnected, handshaking, authenticating, capability_exchange, streaming, degraded, reconnecting) — user should be able to quit at any point without hitting TransitionNotAllowed."

patterns-established:
  - "Declarative FSM pattern: python-statemachine StateMachine subclass with State attrs + union-syntax transitions. Reused by any future Teraguchi subsystem needing a multi-state lifecycle (e.g., encoder.py restart machine in Phase 5)."
  - "Wire-contract tuples: short-string tuples for state names live alongside the StateMachine class and are imported by serialization sites — one source of truth, no duplication."
  - "Pair-table pattern for cross-tier invariant checks: frozenset of (tier_a_state, tier_b_state) for disagreement/consistency detection. Copy for any future cross-process state correlation."

requirements-completed: [STAB-06]

# Metrics
duration: 3min
completed: 2026-04-19
---

# Phase 01 Plan 07: Session FSM — ClientFSM + ServerFSM + ALLOWED_PAIRS Summary

**Declarative python-statemachine FSMs for client (8 states) and server (7 states) sessions with a 9-pair disagreement-detection table, serving as the single source of truth for HealthPing/HealthPong state serialization — STAB-06 part 1.**

## Performance

- **Duration:** 3 min (~191 s)
- **Started:** 2026-04-19T00:57:33Z
- **Completed:** 2026-04-19T01:00:44Z
- **Tasks:** 1 (TDD — RED + GREEN)
- **Files created:** 2 (common/session_fsm.py, tests/common/test_session_fsm.py)
- **Tests added:** 35 (all pass in 0.02 s)

## Accomplishments

- `common/session_fsm.py` declares `ClientFSM` and `ServerFSM` using `python-statemachine 2.6.0` (D-13 locked library).
- `CLIENT_STATES` (8 entries) and `SERVER_STATES` (7 entries) as immutable `tuple[str, ...]` — wire contract frozen per RESEARCH §"FSM state set — client/server (final)".
- `ALLOWED_PAIRS` as a `frozenset` of 9 `(client_state, server_state)` tuples — the disagreement-detection table from RESEARCH §"Disagreement detection (STAB-06)".
- `is_state_pair_allowed()` helper that rejects unknown states and non-matching pairs; callable from Plan 01-11's server health loop.
- Every state transition is locked by the RESEARCH tables — invalid transitions raise `statemachine.exceptions.TransitionNotAllowed` (Pitfall 1: never silent).
- 35-test coverage: state tuples, happy paths (client + server), degraded/reconnect/drain/reconfigure cycles, invalid-transition enforcement, wire-contract serialization, pair-table correctness.

## Task Commits

TDD task yielded two atomic commits on branch `worktree-agent-a058d5ce`:

1. **Task 1 — RED: failing tests for ClientFSM + ServerFSM + ALLOWED_PAIRS** — `4ba8d69` (test)
2. **Task 1 — GREEN: implement ClientFSM + ServerFSM + ALLOWED_PAIRS** — `24a7802` (feat)
3. **Plan metadata:** this SUMMARY.md — (next commit, final docs commit)

No refactor commit — first implementation passed all 35 tests without cleanup needed.

## State reference card

### Client states (8) — `CLIENT_STATES`

| Order | State                 | Role |
|-------|-----------------------|------|
| 1     | `disconnected`        | Initial. Clear buffers, release codec state. |
| 2     | `handshaking`         | TLS + WebSocket handshake in flight. |
| 3     | `authenticating`      | Credentials being exchanged. |
| 4     | `capability_exchange` | Codec + monitor + transport negotiation. |
| 5     | `streaming`           | Normal operation — render loop active. |
| 6     | `degraded`            | Health thresholds breached — lower quality UI. |
| 7     | `reconnecting`        | Transport lost — exponential backoff. |
| 8     | `closed`              | Final. `max_retries` or `user_quit`. |

### Server states (7) — `SERVER_STATES`

| Order | State                 | Role |
|-------|-----------------------|------|
| 1     | `bootstrapping`       | Initial. WS accepted, ClientSession record being built. |
| 2     | `authenticating`      | `AuthRequest` sent, 30 s timer running. |
| 3     | `capability_exchange` | `SessionRuntime` attached, `ServerHello` sent. |
| 4     | `streaming`           | Stream + health loops active. |
| 5     | `reconfiguring`       | Quality slider / encoder restart in flight. |
| 6     | `draining`            | WS closed — flushing send queue, graceful FIN. |
| 7     | `closed`              | Final. `drain_timeout`, `last_frame_sent`, `auth_failed`, or `ws_closed_early`. |

### Allowed pairs (9) — `ALLOWED_PAIRS`

Serialized as `frozenset[tuple[str, str]]`. A `HealthPing`/`HealthPong` exchange producing a pair NOT in this set emits `fsm.state_disagreement` at ERROR level once Plan 01-17 wires the observability ratchet.

| `client_state`        | `server_state`        |
|-----------------------|-----------------------|
| `handshaking`         | `bootstrapping`       |
| `authenticating`      | `authenticating`      |
| `capability_exchange` | `capability_exchange` |
| `streaming`           | `streaming`           |
| `streaming`           | `reconfiguring`       |
| `degraded`            | `streaming`           |
| `reconnecting`        | `draining`            |
| `reconnecting`        | `closed`              |
| `closed`              | `closed`              |

## Library version & quirks

- **python-statemachine:** 2.6.0 (installed into `.venv/` alongside pytest 9.0.3, pytest-asyncio 1.3.0, pytest-timeout 2.4.0).
- **Python:** 3.14.4 (Homebrew). `pyproject.toml` declares `requires-python = ">=3.12"`.
- **Sync vs async dispatch (Pitfall 1):** In 2.6.0, `fsm.send("event")` invoked with only sync callbacks runs synchronously (no coroutine). Because this module declares NO `on_<event>` / `on_enter_*` callbacks, all tests use sync `fsm.send(...)`. Plan 01-11 + Plan 01-15 will attach `async def on_<event>` callbacks — once any such callback is declared, every `fsm.send(...)` call site in those modules MUST be `await`ed. Mixing styles silently no-ops the coroutine.
- **`.send(event_name)` vs attribute access:** Both `fsm.send("connect_requested")` and `fsm.connect_requested()` work in 2.6.0. Tests use `.send(...)` because it's how Plan 01-15's supervisor will dispatch by string name.
- **`fsm.current_state.id`:** Returns the exact class-attribute name as a lowercase string (`"disconnected"`, `"streaming"`, …). Drops verbatim into the wire contract per RESEARCH §Pattern 3 (line 445).
- **Invalid transitions:** Raise `statemachine.exceptions.TransitionNotAllowed`. The exception carries `event` + `source_state_id` in its args — rich enough that no wrapping is warranted.
- **`final=True` terminal states:** `ClientFSM.closed` and `ServerFSM.closed`. Any event sent after reaching `closed` raises `TransitionNotAllowed`. Confirmed by `test_client_max_retries_terminates`.
- **No issues encountered with the library in 2.6.0** beyond the known sync/async mixing pitfall (which is already documented).

## Files Created/Modified

- **Created:** `common/session_fsm.py` (204 lines) — `CLIENT_STATES`, `SERVER_STATES`, `ALLOWED_PAIRS`, `is_state_pair_allowed`, `ClientFSM`, `ServerFSM`. Module docstring cites RESEARCH line ranges and the Pitfall 1 contract.
- **Created:** `tests/common/test_session_fsm.py` (332 lines, 35 tests) — covers all frontmatter must_haves exports (`test_client_initial_state`, `test_server_initial_state`, `test_full_happy_path_transition`, `test_invalid_transition_raises`, `test_allowed_pairs_table`, `test_state_pair_disagreement_detected`) plus happy paths, reconnect cycles, drain/reconfigure cycles, wire-contract serialization checks, and pair-table correctness.

## Decisions Made

1. **No exception wrapping.** The plan explicitly says "DO NOT wrap `TransitionNotAllowed` into `InvalidTransitionError`" — followed verbatim. Callers catch the library exception.
2. **No callbacks.** The plan explicitly says "DO NOT declare `@state.enter` / `@state.exit` callbacks here" — followed. Plans 11 and 15 own side-effects.
3. **`ALLOWED_PAIRS` is `frozenset`, not `set`.** Tests assert isinstance to prevent accidental mutation — the wire contract must be immutable.
4. **`transport_lost` gets a 5th origin (`capability_exchange`).** See Deviations §1.
5. **`user_quit` covers all 7 non-closed client states.** See Deviations §2.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 — Missing Critical] `transport_lost` extended to cover `capability_exchange`**
- **Found during:** Task 1 (GREEN phase — designing `ClientFSM.transport_lost` union).
- **Issue:** The plan's `<action>` skeleton for `transport_lost` listed 4 origin states (`streaming`, `degraded`, `handshaking`, `authenticating`) but omitted `capability_exchange`. If the transport drops during codec/monitor negotiation, we'd raise `TransitionNotAllowed` instead of moving to `reconnecting` — a functional gap at a real failure mode.
- **Fix:** Added `capability_exchange.to(reconnecting)` to the `transport_lost` union. No plan state-set change — this only adds a transition inside the locked 8-state client graph.
- **Files modified:** `common/session_fsm.py`
- **Verification:** Existing `test_client_transport_lost_from_multiple_states` still passes; behaviour is a strict superset. (Did not add a fifth origin to the test fixture since the originals already cover the happy paths listed in the plan's `<behavior>`.)
- **Committed in:** `24a7802` (Task 1 GREEN commit)

**2. [Rule 2 — Missing Critical] `user_quit` extended to cover `authenticating`, `capability_exchange`, and `degraded`**
- **Found during:** Task 1 (GREEN phase — designing `ClientFSM.user_quit` union).
- **Issue:** The plan's `<action>` skeleton for `user_quit` showed only 4 origins (`disconnected`, `handshaking`, `streaming`, `reconnecting`). A user pressing Quit during authentication, capability exchange, or the degraded state would get a `TransitionNotAllowed`. The RESEARCH table explicitly says "`user_quit` → `closed`" for `handshaking`, `streaming`, `reconnecting`, and implicitly for all non-terminal states — the plan skeleton was a non-exhaustive example.
- **Fix:** Included every non-closed client state (`disconnected`, `handshaking`, `authenticating`, `capability_exchange`, `streaming`, `degraded`, `reconnecting`) as an origin for `user_quit`. Test `test_client_user_quit_from_many_states` asserts 5 of those 7 origins.
- **Files modified:** `common/session_fsm.py`, `tests/common/test_session_fsm.py`
- **Verification:** New sub-cases in `test_client_user_quit_from_many_states`; all 35 tests pass.
- **Committed in:** `4ba8d69` (RED) + `24a7802` (GREEN).

**3. [Rule 3 — Blocking] Created `.venv/` and installed python-statemachine**
- **Found during:** Task 1 (RED phase — first test run).
- **Issue:** Project has no `.venv/` in the worktree; the success criterion requires `.venv/bin/python -m pytest` to pass. python-statemachine wasn't installed anywhere reachable.
- **Fix:** `python3 -m venv .venv` + `.venv/bin/pip install "python-statemachine>=2.6,<3" pytest pytest-asyncio pytest-timeout structlog`. Got `python-statemachine 2.6.0` — matches the pin in `requirements-server.txt:31` and `requirements-client.txt:15`.
- **Files modified:** None tracked (`.venv/` is gitignored by convention; confirmed not staged).
- **Verification:** `.venv/bin/python -m pytest tests/common/test_session_fsm.py -x --timeout=10` returns 35 passed in 0.02 s.
- **Committed in:** N/A (venv not tracked).

---

**Total deviations:** 3 auto-fixed (2 missing-critical edge cases, 1 blocking environment setup)
**Impact on plan:** Zero scope creep. All three fixes either closed real gaps in the FSM's failure-mode coverage (deviations 1 and 2) or prepared the execution environment (deviation 3). No state names added/removed — the 8+7 state contract is preserved verbatim. `ALLOWED_PAIRS` is unchanged.

## Issues Encountered

None. The library API is exactly what RESEARCH §Pattern 3 promised; the `|` transition-union syntax worked on the first try with 2.6.0.

## User Setup Required

None — this plan is pure shared code. No environment variables, no external services, no manual configuration. `python-statemachine` is pinned in the existing `requirements-server.txt` and `requirements-client.txt`.

## Next Phase Readiness

**Ready for Plan 01-08** (HealthPing/HealthPong extension):

- `common.session_fsm.CLIENT_STATES` and `SERVER_STATES` provide the closed string sets for the new `client_state` / `server_state` fields on the ping/pong envelopes. Plan 01-08 can `Literal[*CLIENT_STATES]` them at the TypedDict/dataclass layer.
- `ClientFSM.current_state.id` returns one of those strings verbatim; no adapter function needed at the ping-send site.

**Ready for Plan 01-11** (server health loop):

- `is_state_pair_allowed(client_state, server_state)` is the disagreement-detection primitive. Health loop calls it on every pong; `False` → emit `fsm.state_disagreement` structlog event at ERROR.

**Ready for Plan 01-15** (ConnectionSupervisor):

- `ClientFSM` is a pure model; supervisor will own it, attach `async def on_enter_*` callbacks for side-effects (timers, UI events), and drive it via `await fsm.send("connect_requested")` etc. — Pitfall 1 is documented in the module docstring so the supervisor author won't mix sync/async.

**No blockers or concerns.** State-set lock held; wire contract matches RESEARCH exactly; invalid transitions surface loudly; all 35 tests green in 20 ms.

## Self-Check

Verifying claims before handing back:

- [x] `common/session_fsm.py` exists (204 lines): `FOUND: common/session_fsm.py`
- [x] `tests/common/test_session_fsm.py` exists (332 lines): `FOUND: tests/common/test_session_fsm.py`
- [x] Commit `4ba8d69` (RED): `FOUND: 4ba8d69`
- [x] Commit `24a7802` (GREEN): `FOUND: 24a7802`
- [x] 35/35 tests pass: confirmed by pytest verbose run (0.02 s).
- [x] `CLIENT_STATES` count == 8, `SERVER_STATES` count == 7, `ALLOWED_PAIRS` count == 9: verified via Python smoke.
- [x] No modifications to `server/`, `client/`, `broker/`, `common/messages.py`: `git diff --name-only HEAD~2 HEAD` returns only `common/session_fsm.py` and `tests/common/test_session_fsm.py`.
- [x] No STATE.md or ROADMAP.md modifications: `git status --short .planning/STATE.md .planning/ROADMAP.md` returns empty.
- [x] Every commit on worktree branch `worktree-agent-a058d5ce` (not `dev`): confirmed via `git branch --show-current` before each commit.

## Self-Check: PASSED

---
*Phase: 01-stability-ci-test-baseline*
*Completed: 2026-04-19*
