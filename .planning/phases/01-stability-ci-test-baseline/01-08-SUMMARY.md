---
phase: 01-stability-ci-test-baseline
plan: 08
subsystem: session-fsm-health-protocol
tags: [stab-06, fsm, health-ping, health-pong, protocol, direction-inversion]
dependency_graph:
  requires: [01-04, 01-07]
  provides: [fsm-state-on-wire, client-owns-ping, server-owns-pong, disagreement-detection-hook]
  affects: [server/main.py, client/protocol.py, common/messages.py]
tech_stack:
  added: []
  patterns: [python-statemachine-guarded-send, fsm-state-on-wire, healthpong-direction-inversion]
key_files:
  created:
    - tests/integration/test_fsm_state_sync.py
  modified:
    - common/messages.py
    - server/main.py
    - client/protocol.py
    - tests/common/test_messages.py
decisions:
  - "HealthPing/HealthPong direction inverted: CLIENT now originates HealthPing (stamped with client_state); SERVER now originates HealthPong (stamped with server_state). This aligns the dataclass fields with their source-of-truth peer."
  - "Empty-string default for client_state / server_state preserves backward compat so Plan 01-04 round-trip tests pass without modification."
  - "All fsm.send() calls wrapped in try/except Exception: pass — Plan 01-17 will upgrade to structured-log events on invalid transitions. Plan 01-08 is about data availability, not transition rigor."
  - "Disagreement detection wires a plain-stdlib warning log for now; the structured fsm.state_disagreement ERROR event is deferred to Plan 01-17 per the plan frontmatter."
metrics:
  duration_minutes: ~25
  completed_date: 2026-04-18
  requirements_addressed: [STAB-06]
  tests_added: 10
  tests_total_passing: 158
---

# Phase 1 Plan 08: Session FSM + Health-Ping/Pong Direction Inversion Summary

One-liner: Serialize ClientFSM / ServerFSM state into HealthPing / HealthPong wire format, wire both FSMs into the monolithic server and client entry points, and invert the ping/pong direction so each peer stamps its own FSM state on its own outbound messages — bootstrapping the state-disagreement detection hook that Plan 01-17 will promote to a structured ERROR event.

## Executive Summary

Plan 01-08 completes STAB-06 part 2 (part 1 was Plan 01-07's FSM definitions in `common/session_fsm.py`). Two tasks:

1. **Extend the dataclasses** — `HealthPing` gains `client_state: str = ""`, `HealthPong` gains `server_state: str = ""`. Both round-trip through `to_json` / `parse_message` and default to empty string for old-peer compatibility.

2. **Wire the FSM + invert direction** — Each `ClientSession` on the server owns a `ServerFSM`; each `ClientProtocol` on the client owns a `ClientFSM`. The server's `_health_ping_loop` now emits `HealthPong` (not `HealthPing`) stamped with `server_state=cs.fsm.current_state.id`. A new client-side `_client_health_ping_loop` emits `HealthPing` every 2s stamped with `client_state=self.fsm.current_state.id`. Inbound handling is also swapped: the server consumes `HealthPing` and runs disagreement detection against `ALLOWED_PAIRS`; the client consumes `HealthPong` and caches `server_state`.

Two load-bearing grep acceptance criteria encode the direction inversion in source:
- `grep -Eq "server_state[[:space:]]*=[[:space:]]*cs\.fsm\.current_state" server/main.py` — server stamps its own state on Pong
- `grep -Eq "client_state[[:space:]]*=[[:space:]]*self\.fsm\.current_state" client/protocol.py` — client stamps its own state on Ping

Both pass.

## What Was Built

### common/messages.py (~lines 328-350)

Extended the two health dataclasses:

```python
@dataclass
class HealthPing:
    type: str = MsgType.HEALTH_PING
    timestamp_ms: int = 0
    sequence: int = 0
    # STAB-06 — FSM state serialized verbatim from common.session_fsm.ClientFSM
    # HealthPing ORIGINATES ON THE CLIENT (Plan 01-08 inverts prior direction).
    client_state: str = ""
    def to_json(self) -> str: ...

@dataclass
class HealthPong:
    type: str = MsgType.HEALTH_PONG
    ping_timestamp_ms: int = 0
    sequence: int = 0
    server_timestamp_ms: int = 0
    # STAB-06 — FSM state serialized verbatim from common.session_fsm.ServerFSM
    # HealthPong ORIGINATES ON THE SERVER.
    server_state: str = ""
    def to_json(self) -> str: ...
```

### server/main.py (exact patched ranges)

- **Line 52** — `from common.session_fsm import ServerFSM, is_state_pair_allowed`
- **Lines 677-682** — `ClientSession.__init__` instantiates `self.fsm = ServerFSM()` and `self.last_reported_client_state: str = ""`
- **Lines 326-347** — `_health_ping_loop` INVERTED: builds `HealthPong(sequence=seq, server_state=cs.fsm.current_state.id)` and broadcasts it; old `HealthPing(sequence=seq).to_json()` line removed
- **Lines 559-587** — `handle_input` adds `elif msg_type == MsgType.HEALTH_PING` branch which extracts `client_state`, runs `is_state_pair_allowed` disagreement check, emits a stdlib `logger.warning("fsm.state_disagreement ...")` on mismatch (Plan 01-17 will upgrade to structured ERROR)
- **Lines 612-621** — handle_input `CLIENT_HELLO` branch calls `session.fsm.send("client_hello")`
- **Lines 773-777** — `handle_client` pre-auth calls `session.fsm.send("tls_ok")`
- **Lines 820-831** — PAM auth success path: `session.fsm.send("auth_ok")`
- **Lines 856-866** — local-mode auth success path: `session.fsm.send("auth_ok")`
- **Lines 870-876** — no-auth mode path: `session.fsm.send("auth_ok")`
- **Lines 971-979** — `handle_client` finally: `session.fsm.send("ws_closed")` on disconnect

`grep -c "fsm.send" server/main.py` → 6 call sites.

### client/protocol.py (exact patched ranges)

- **Line 31** — `from common.session_fsm import ClientFSM`
- **Lines 111-120** — `ClientProtocol.__init__` instantiates `self.fsm = ClientFSM()`, `self.last_reported_server_state: str = ""`, `self._ping_seq: int = 0`
- **Lines 265-275** — `_run_loop` disconnect path: `self.fsm.send("transport_lost")`
- **Lines 326-332** — `_connect_to_server` entry: `self.fsm.send("connect_requested")`
- **Lines 350-355** — after `websockets.connect` returns: `self.fsm.send("tls_ok")`
- **Lines 386-391** — `_client_health_ping_loop(ws)` task started alongside UDP stats loop
- **Lines 536-543** — `_handle_auth` success path: `self.fsm.send("auth_ok")`
- **Lines 597-603** — `_handle_token_auth` success path: `self.fsm.send("auth_ok")`
- **Lines 705-725** — NEW `_client_health_ping_loop` method: 2s cadence, builds `HealthPing(sequence=self._ping_seq, client_state=self.fsm.current_state.id)` and sends it on the open websocket
- **Lines 754-764** — `_handle_server_hello` drives `self.fsm.send("hello_received")`
- **Lines 779-785** — old HealthPing→HealthPong reply block REPLACED with `elif msg_type == MsgType.HEALTH_PONG` branch that caches `server_state`

`grep -c "fsm.send" client/protocol.py` → 6 call sites.

### tests/common/test_messages.py — 6 new regression tests

```
test_healthping_includes_client_state
test_healthping_default_client_state_is_empty
test_healthpong_includes_server_state
test_healthpong_default_server_state_is_empty
test_healthping_backward_compat_parse_without_client_state
test_healthping_client_state_restricted_to_declared_set
```

Existing Plan 01-04 tests `test_healthping_roundtrip` + `test_healthpong_roundtrip` pass unmodified.

### tests/integration/test_fsm_state_sync.py — 4 new tests (NEW FILE)

```
test_health_ping_carries_client_state         — loopback wss: client→server, asserts client_state
test_health_pong_carries_server_state         — loopback wss: server→client, asserts server_state
test_state_disagreement_detected              — unit guard on is_state_pair_allowed
test_allowed_pair_count_matches_table         — locks ALLOWED_PAIRS cardinality at 9
```

Uses Plan 02's `tls_ca_and_cert` + `free_port` fixtures from `tests/integration/conftest.py`.

## Direction Inversion Proof

```
$ grep -Eq "server_state[[:space:]]*=[[:space:]]*cs\.fsm\.current_state" server/main.py && echo OK
OK

$ grep -Eq "client_state[[:space:]]*=[[:space:]]*self\.fsm\.current_state" client/protocol.py && echo OK
OK

$ grep -q "ping_json = HealthPing(sequence=seq).to_json()" server/main.py && echo PRESENT || echo REMOVED
REMOVED
```

Each peer stamps its own FSM state on its own outbound message. The OLD direction (server→HealthPing, client→HealthPong) is gone from source.

## Test Results

### First passing run — targeted

```
$ .venv/bin/python -m pytest tests/common/test_messages.py tests/integration/test_fsm_state_sync.py -v --timeout=30
...
======================= 24 passed, 19 warnings in 0.51s ========================
```

Breakdown:
- 20/20 in `tests/common/test_messages.py` (14 pre-existing + 6 new STAB-06)
- 4/4 in `tests/integration/test_fsm_state_sync.py` (new)

### Full suite regression

```
$ .venv/bin/python -m pytest tests/ --timeout=30
================= 158 passed, 1 xfailed, 74 warnings in 1.09s ==================
```

No regressions. The single `xfailed` is pre-existing and unrelated.

## Commits (in order)

| Commit  | Type  | Description                                                              |
| ------- | ----- | ------------------------------------------------------------------------ |
| c8d3b98 | test  | add failing tests for HealthPing/HealthPong FSM state fields (RED)       |
| 637b3e7 | feat  | extend HealthPing/HealthPong with FSM state fields (GREEN)               |
| a3b30d4 | test  | add FSM state round-trip loopback integration tests                      |
| 122e9dd | feat  | wire ServerFSM+ClientFSM and invert HealthPing/Pong direction (GREEN)    |

TDD cycle honored: RED → GREEN for Task 1 dataclass extension; integration test added before Task 2 wiring; Task 2 wiring landed as a single commit because the server-side and client-side changes are coupled (swapping direction requires both sides in lockstep or one side goes silent).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Completeness] Added auth_ok transition for all three server auth paths**

- **Found during:** Task 2 implementation
- **Issue:** The plan's action block called out PAM success as the `auth_ok` trigger but `server/main.py` has three auth branches (PAM, local challenge-response, no-auth). Wiring only the PAM branch would leave `ServerFSM` stuck in `authenticating` in the other two modes, which would fail `is_state_pair_allowed` checks once clients start streaming.
- **Fix:** Added `fsm.send("auth_ok")` at the success-confirmed site in all three branches (PAM at ~line 820, local at ~line 856, no-auth at ~line 870).
- **Files modified:** server/main.py
- **Commit:** 122e9dd

**2. [Rule 2 - Completeness] Added transport_lost FSM transition on client disconnect**

- **Found during:** Task 2 implementation
- **Issue:** The plan mentioned `transport_lost` as a drive point but didn't pin the specific location. The cleanest site is `ClientProtocol._run_loop` right after `_connected = False` is set — before reconnect backoff.
- **Fix:** Added guarded `self.fsm.send("transport_lost")` at that site. Wrapped in try/except pass (Plan 01-17 scope is invalid-transition rigor).
- **Files modified:** client/protocol.py
- **Commit:** 122e9dd

**3. [Rule 2 - Wiring] on_health_ping callback repurposed**

- **Found during:** Task 2 implementation
- **Issue:** `ClientProtocol` has an `on_health_ping` callback that used to fire when the client received a `HealthPing`. Post-inversion the client never receives HealthPing, so the callback would be dead code.
- **Fix:** Repurposed `on_health_ping` to fire on the new `HealthPong` path — keeps the callback name stable for UI code that listens for health-event notifications (the event name in the UI isn't direction-specific, it's "health tick").
- **Files modified:** client/protocol.py (line ~784)
- **Commit:** 122e9dd

## Grep Acceptance Criteria

| Criterion                                                                               | Result |
| --------------------------------------------------------------------------------------- | ------ |
| `grep -q "client_state: str" common/messages.py`                                        | PASS   |
| `grep -q "server_state: str" common/messages.py`                                        | PASS   |
| `grep -q "from common.session_fsm import ServerFSM" server/main.py`                     | PASS   |
| `grep -q "self.fsm = ServerFSM" server/main.py`                                         | PASS   |
| `grep -q "from common.session_fsm import ClientFSM" client/protocol.py`                 | PASS   |
| `grep -q "self.fsm = ClientFSM" client/protocol.py`                                     | PASS   |
| `grep -c "fsm.send" server/main.py` (>=3)                                               | 6      |
| `grep -c "fsm.send" client/protocol.py` (>=4)                                           | 6      |
| `grep -Eq "server_state[[:space:]]*=[[:space:]]*cs\.fsm\.current_state" server/main.py` | PASS   |
| `grep -Eq "client_state[[:space:]]*=[[:space:]]*self\.fsm\.current_state" client/protocol.py` | PASS |
| `! grep -q "ping_json = HealthPing(sequence=seq).to_json()" server/main.py`             | PASS   |
| `test -f tests/integration/test_fsm_state_sync.py`                                      | PASS   |
| `grep -q "def test_health_ping_carries_client_state" tests/integration/test_fsm_state_sync.py` | PASS |
| `grep -q "def test_health_pong_carries_server_state" tests/integration/test_fsm_state_sync.py` | PASS |

## Self-Check: PASSED

### Files verified to exist

- `common/messages.py` — FOUND
- `server/main.py` — FOUND
- `client/protocol.py` — FOUND
- `tests/common/test_messages.py` — FOUND
- `tests/integration/test_fsm_state_sync.py` — FOUND
- `.planning/phases/01-stability-ci-test-baseline/01-08-SUMMARY.md` — FOUND (this file)

### Commits verified to exist

- c8d3b98 — FOUND (RED)
- 637b3e7 — FOUND (GREEN dataclass)
- a3b30d4 — FOUND (integration tests)
- 122e9dd — FOUND (GREEN FSM wiring)

### Test results verified

- `tests/common/test_messages.py`: 20/20 PASS
- `tests/integration/test_fsm_state_sync.py`: 4/4 PASS
- Full suite: 158 passed, 1 xfailed (unrelated pre-existing)

All plan acceptance criteria met. Plan 01-08 complete on worktree-agent-a5270d2c branch.
