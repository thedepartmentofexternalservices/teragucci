# Teraguchi — Claude Context

Open-source high-performance remote workstation for VFX / Flame workflows. Replacing HP Anyware (PCoIP) for small independent VFX studios. Python 3.10+, PySide6 client, Linux + macOS servers.

## Project memory — read first

- `.planning/PROJECT.md` — canonical project context, Core Value, Active requirements, Out of Scope, Key Decisions
- `.planning/REQUIREMENTS.md` — 108 v1 requirements with REQ-IDs + phase traceability
- `.planning/ROADMAP.md` — 7 phases (Stability → Input/Color → Display → Audio → Network → Distribution → OSS Polish)
- `.planning/STATE.md` — current phase, in-flight plans, session memory
- `.planning/research/SUMMARY.md` — research synthesis, stack prescription, critical-path items
- `.planning/codebase/` — existing-code analysis (STACK, ARCHITECTURE, STRUCTURE, CONCERNS, INTEGRATIONS, CONVENTIONS, TESTING)

## GSD Workflow

This project uses the Get-Shit-Done workflow. Do not freelance outside it. Use slash commands to drive work:

- `/gsd-progress` — where are we / what's next
- `/gsd-plan-phase <n>` — decompose a phase into plans (research → plan → plan-check)
- `/gsd-execute-phase <n>` — execute plans (with verification)
- `/gsd-discuss-phase <n>` — clarify approach before planning
- `/gsd-verify-work <n>` — validate phase against requirements
- `/gsd-next` — auto-advance

Config in `.planning/config.json`: **yolo mode**, **standard granularity**, **parallel execution**, **quality models (Opus/Sonnet)**, **research + plan-check + verifier all on**.

## Hard constraints (from PROJECT.md — do not violate)

- **v1 scope**: Mac client + Rocky Linux server + macOS server. NO Windows, NO Linux client. Broker is paused.
- **Color**: 10-bit end-to-end, HEVC Main10. 9 silent-downgrade points exist — every video change must be verified against `VIDEO-01`/`VIDEO-02` test fixture.
- **Input fidelity**: Zero tolerance for Wacom pressure glitches or modifier-chord mangling. Flame artist muscle memory is load-bearing.
- **Latency target**: Sub-20ms LAN input-to-photon. CI benchmark keeps this honest — don't break it silently.
- **Linux OS target**: Rocky 9 only. Rocky 10 / Wayland blocked until Autodesk ships Wayland support for Flame.
- **Transport**: QUIC primary for WAN, TLS-WS + UDP for LAN. Promoted from experimental; treat as production path.
- **License**: Apache 2.0. Don't import code under incompatible licenses.
- **Distribution**: Signed + notarized `.app` (Logik Academy Pro + contractor handles certs in Phase 6). Signed RPM for Rocky 9.

## Known traps (from PITFALLS.md — bake into solutions)

- `server/main.py` `send_queue(maxsize=30)` is a latency time-bomb — fix to `maxsize=4` + IDR-on-drop
- `ssl.CERT_NONE` is in 4 places — remove all of them in Phase 1
- `mac_input_injector.pen_event` silently downgrades pen pressure to mouse click — needs IOHIDUserDevice replacement
- Zero tests today, zero CI — do not ship a refactor without a test
- 10-bit downgrade points: BGRA capture default, encoder input, NVENC Main10 fallback, chroma subsampling default, VideoToolbox P010 software fallback, Qt QImage 8-bit formats, macOS EDR tone-mapping, Reference Mode requirement, ICC interference

## Solo maintainer discipline

This is a solo project (Randy). Scope drift is the #1 existential risk. When in doubt:

- Out-of-Scope and anti-feature lists in PROJECT.md and REQUIREMENTS.md are enforceable — if a feature isn't listed, it doesn't ship in v1
- Prefer hardening existing code to building new subsystems
- Every new feature must come with a regression test
- Phase boundaries are load-bearing — don't leak Phase 5 work into Phase 2

## Coding conventions

See `.planning/codebase/CONVENTIONS.md` for existing patterns. Summary:

- Python 3.10+, asyncio-driven throughout
- Type hints required on new code (mypy in CI after Phase 1)
- `structlog` JSON logging (rolling out in Phase 1)
- `pytest` for tests, fixtures in `tests/`
- Per-role requirements files: `requirements-server.txt`, `requirements-client.txt`, `requirements-broker.txt`, `requirements-dev.txt`

## Commits

- Commit docs are tracked (`.planning/` is in git). Commit messages in imperative mood.
- Use atomic commits. GSD workflow already does this for planning artifacts.
