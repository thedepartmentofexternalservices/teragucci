# Teraguchi Release Notes / Runbook

This document seeds the Phase 6 release runbook. It records outcomes
from the Phase 2 input-color-fidelity work that have user-visible v1
implications, and is the source of truth for "what's a v1 known
limitation vs. what's a bug."

## Phase 2 IOHIDUserDevice spike outcome

- **Outcome: FAIL** (recorded 2026-04-19; INPUT-08 re-scoped per D-07)
- **Reason:** The D-07/D-08 spike per `02-CONTEXT.md` requires unsigned
  Python (`pyobjc-framework-IOKit`) to register a virtual Wacom HID
  tablet via `IOHIDUserDeviceCreate` and verify across a complete pen
  stroke that **Photoshop / Preview / a simple NSView app reads
  `NSEvent.pressure > 0`**. The PASS bar cannot be met within the
  Phase 2 plan-execution environment because:
  1. `pyobjc-framework-IOKit` is not present in the executor's Python
     environment, and installing it system-wide requires elevation
     outside the executor's sandbox.
  2. The D-08 success criterion is a manual interactive verification
     against Photoshop / Preview / an NSView test app — which requires
     a logged-in macOS GUI session, the apps installed and granted
     TCC permissions, and a real Wacom Pro stylus at the workstation.
     None of those is available to the autonomous Phase 2 executor.
  3. Per `CLAUDE.md` "Zero tolerance for Wacom pressure glitches" —
     fabricating a PASS without the manual verification would ship a
     half-working injector. That is the worst possible v1 outcome:
     Flame artists trust pressure, and a silent quantization or
     proximity bug would burn that trust on the first session.

  Per `02-CONTEXT.md` D-07 "document + ship" failure-path framing, the
  honest call is to keep the existing mouse-click fallback with a
  one-time WARNING, document the limitation, and direct Flame artists
  to the Rocky Linux server (the production path per `PROJECT.md`).

- **INPUT-08 scope:** Re-scoped to "Mac-server pen pressure is a v1
  known limitation. `mac_input_injector.pen_event` retains the
  Phase 1 mouse-click downgrade path with a one-time WARNING log on
  the first non-zero pressure event."

- **Workaround:** Run Autodesk Flame on the **Rocky Linux server**
  (the production path documented in `PROJECT.md` "Server (Rocky
  Linux) — production-grade"). Mac server is dev / testing /
  light-editing in v1; Flame-on-Mac-server with full pressure is
  v1.1+ work.

- **Roadmap:** A signed **HIDDriverKit** system extension is the v1.1
  investigation. Apple's `com.apple.developer.driverkit.family.hid.virtual.device`
  entitlement requires per-app approval (weeks of bureaucracy through
  the Logik Academy Pro Developer ID). Even with the entitlement, the
  system-extension distribution model breaks the "download the .app
  and run it" UX `PROJECT.md` commits to. Re-evaluate when a real
  studio commits to Flame-on-Mac-server as a load-bearing workflow.

- **Next-spike trigger:** Re-run the D-07/D-08 spike (2 days) on a
  development workstation with `pyobjc-framework-IOKit>=11.0`, real
  Wacom hardware, Photoshop / Preview installed, and the operator
  available to draw a stroke and verify pressure. The plan retains
  the PASS-branch implementation sketch in `02-10-PLAN.md` Task 1
  for the next attempt — no plan rework needed.

### Health-overlay badge

Client health overlay reuses the 02-04 `render_color_badge` pattern.
The pen-capability badge string for v1 macOS-server bookmarks is:

```
10-bit: confirmed / pen: LIMITATION (mouse-only, see release notes)
```

For Linux-server bookmarks the pen segment is omitted (`uinput`
delivers full pressure / tilt; no caveat).

## Phase 2 PenFSM + client proximity re-synth (D-19)

Lands on both spike branches per `02-10-PLAN.md` Task 3:

- `common/session_fsm.py::PenFSM` with `out_of_proximity` /
  `in_proximity` states; `enter_proximity` and `leave_proximity`
  transitions are idempotent (a duplicate enter from `in_proximity`
  is a no-op, matching the wire-message contract on the PenProximityMsg
  receiver in `server/client_session.py`).
- `client/viewer.py::RemoteViewer.focusInEvent` and `showEvent`
  synthesize a `PenProximityMsg(in_proximity=True)` so the server FSM
  converges after Cmd-Tab cycles, screen-lock, and minimize/restore
  (D-19 fixes the canonical "proximity event eaten by lockscreen"
  PITFALLS #3 bug class).

## Notes for Phase 6 packaging

- No new TCC prompt is added by Phase 2's INPUT-08 work on the FAIL
  branch — the Mac server retains its existing Accessibility +
  Input Monitoring requirements from Phase 1.
- No `IOHIDUserDevice` lifecycle is introduced on the FAIL branch, so
  PITFALLS #4 (orphaned virtual-HID devices in `ioreg -l -c
  IOHIDUserDevice`) is not reachable in v1. If the v1.1 PASS-branch
  productionization lands, `atexit.register()` + SIGTERM/SIGINT
  handlers are mandatory in the new module per the Plan 02-10 Task 1
  acceptance criteria.

## Phase 2 Wacom matrix ritual (per-release)

Run before tagging any `v*` release. Requires DXS lab access — a
laptop / autonomous executor cannot satisfy this gate, only a real
Wacom + real Cintiq + real Mac/Rocky workstation in the DXS lab can.

The ritual exercises the D-17 6-step protocol against four cells of
the Wacom × macOS matrix. Cells:

1. Intuos Pro Large + macOS Sonoma
2. Intuos Pro Large + macOS Sequoia
3. Cintiq Pro 24 + macOS Sonoma
4. Cintiq Pro 24 + macOS Sequoia

For each cell:

1. **Pressure ramp** (INPUT-12): pen draws a slow 0→max stroke over
   ~5 seconds. Confirm client-side log captures ≥ 200
   `event="wacom_matrix"` pressure samples with
   `client_pressure ∈ (0, 1.0]`.

2. **Eraser flip** (INPUT-10): turn pen over, stroke. Confirm client
   log shows events with `client_pointer_type == "eraser"` and
   `client_pressure > 0`.

3. **Tilt test**: hold pen tilted while stroking. Confirm at least
   one event has `|client_tilt_x| > 10` or `|client_tilt_y| > 10`.

4. **Proximity cycle** (INPUT-11): lift pen out of range and back
   in. Confirm server `structlog` shows PenFSM transition
   `out_of_proximity → in_proximity` AND
   `in_proximity → out_of_proximity`.

5. **Tablet-side buttons** (INPUT-10): press each side button while
   stroking. Confirm server `structlog` has button-press events for
   both side buttons at least once.

6. **Reconnect mid-stroke**: during step 1, disconnect the
   WebSocket; reconnect within 5 seconds. Confirm post-reconnect:
   server PenFSM is `out_of_proximity`; no orphaned `TeraguchiTablet`
   in `ioreg -l -c IOHIDUserDevice`; first post-auth message was
   `KEY_RESET_MODIFIERS(reason="reconnect")`.

7. **RMS analysis** (D-18): for each cell, run

   ```bash
   python tools/wacom_quant_analysis.py \
       --client-log artifacts/client_<cell>.jsonl \
       --server-log artifacts/server_<cell>.jsonl \
       --cell <cell> \
       --out-svg artifacts/rms_<cell>.svg \
       --pass-threshold 0.01
   ```

   Must exit `0` (RMS < 1%). Exit `1` blocks the release. Exit `2`
   means the operator captured malformed logs — re-run the cell.

8. **Record video** of each cell's session; link below.

9. **Flame qualitative sign-off** (MANDATORY per D-17): a Flame
   artist draws a standard paint stroke on the Cintiq Pro 24 +
   Sequoia cell and signs off that quantization artifacts are not
   visible. This step is non-negotiable — it is the qualitative half
   of the D-17 hybrid pass criterion.

**Pass gate:** ALL 4 cells pass ALL 6 automated steps + RMS < 1%
per cell + qualitative sign-off on the Cintiq Pro 24 + Sequoia cell.

**T-02-34 mitigation:** the raw `client_<cell>.jsonl` /
`server_<cell>.jsonl` artifacts contain raw pressure / tilt samples
and **must not** be committed to the public repository. Only the SVG
plot + the summary numbers in the table below make it into git.

### Phase 2 matrix results

Matrix execution date: `<DEFERRED — Randy to execute at DXS>`

| Cell                       | RMS                                          | Pass / Fail                                  | Video                                        |
| -------------------------- | -------------------------------------------- | -------------------------------------------- | -------------------------------------------- |
| Intuos Pro Large + Sonoma  | `<DEFERRED — Randy to execute at DXS>`       | `<DEFERRED — Randy to execute at DXS>`       | `<DEFERRED — Randy to execute at DXS>`       |
| Intuos Pro Large + Sequoia | `<DEFERRED — Randy to execute at DXS>`       | `<DEFERRED — Randy to execute at DXS>`       | `<DEFERRED — Randy to execute at DXS>`       |
| Cintiq Pro 24 + Sonoma     | `<DEFERRED — Randy to execute at DXS>`       | `<DEFERRED — Randy to execute at DXS>`       | `<DEFERRED — Randy to execute at DXS>`       |
| Cintiq Pro 24 + Sequoia    | `<DEFERRED — Randy to execute at DXS>`       | `<DEFERRED — Randy to execute at DXS>`       | `<DEFERRED — Randy to execute at DXS>`       |

Flame artist sign-off: `<DEFERRED — Randy to execute at DXS>`
(name + date, on the Cintiq Pro 24 + Sequoia cell only)

> **Note for the operator (Randy):** the matrix has not yet been
> executed because Plan 02-12 was run from an autonomous worktree
> without physical Wacom / Cintiq / Mac / Rocky access. When the
> matrix runs at DXS, replace each `<DEFERRED — Randy to execute at
> DXS>` cell with the captured RMS value (e.g. `0.0042`), pass / fail
> verdict, and a link to the recorded video. Phase 2 sign-off is
> blocked on this table being fully populated.

## Phase 2 latency measurement (D-21)

One-shot real-hardware end-to-end input-to-photon latency
measurement on DXS, comparing HEAD-of-Phase-1-verification (pre-
Phase-2) against HEAD-of-Phase-2 (post). The CI gate (Phase 1
D-08 / D-09 synthetic p99 < 25 ms) is unchanged per D-21; this
table records the real-hardware comparison that the CI gate cannot
make on its own.

### Procedure

1. On a DXS Mac client + Rocky Flame workstation server, both on the
   same LAN Tailscale tailnet, with a real Wacom + Cintiq attached:
   - Check out the
     `HEAD-of-Phase-1-verification-complete` commit (see
     `.planning/phases/01-stability-ci-test-baseline/01-VERIFICATION.md`
     for the exact ref).
   - Start Teraguchi server + client.
   - Run **3 trial sessions**, each ~60 seconds of typical Flame
     interaction (pen strokes, hotkeys, viewport navigation), with
     `structlog` per-stage latency telemetry (Phase 1 OBS-02)
     enabled.
   - Record p50 / p95 / p99 end-to-end input-to-photon latencies.

2. Check out Phase 2 HEAD. Repeat step 1 verbatim. Record the same
   metrics.

3. Compute the median p99 across the 3 trials, pre and post. Fill
   in the table below.

4. **Regression gate (D-21):** if post-Phase-2 p99 > pre-Phase-2 p99
   × 1.15, DO NOT sign off the phase. Triage. Common culprits:
   QRhiWidget blit cost, VTCompressionSession lifecycle overhead,
   capability-probe startup cost (one-shot; shouldn't affect
   per-frame).

5. **Bonus check** (VIDEO-11 happy path): on at least one trial,
   confirm post-Phase-2 p99 < 20 ms on a compatible-hardware Wacom +
   good Tailscale LAN. If it does NOT, document as a known caveat —
   the CI gate still cites Phase 1 synthetic p99 < 25 ms (D-08 /
   D-09; unchanged in Phase 2 per D-21). Real-hardware DXS target
   remains sub-20 ms per `CLAUDE.md` core value, but CI pass / fail
   is the 25 ms number.

### Results

Latency measurement date: `<DEFERRED — Randy to execute at DXS>`

| Metric                   | Pre-Phase-2 (Phase 1 HEAD)             | Post-Phase-2                           | Delta                                  |
| ------------------------ | -------------------------------------- | -------------------------------------- | -------------------------------------- |
| p50 input-to-photon (ms) | `<DEFERRED — Randy to execute at DXS>` | `<DEFERRED — Randy to execute at DXS>` | `<DEFERRED — Randy to execute at DXS>` |
| p95 input-to-photon (ms) | `<DEFERRED — Randy to execute at DXS>` | `<DEFERRED — Randy to execute at DXS>` | `<DEFERRED — Randy to execute at DXS>` |
| p99 input-to-photon (ms) | `<DEFERRED — Randy to execute at DXS>` | `<DEFERRED — Randy to execute at DXS>` | `<DEFERRED — Randy to execute at DXS>` |

Synthetic-stage CI gate (Phase 1 D-08 / D-09 p99 < 25 ms; unchanged
per D-21): **green** at HEAD-of-Phase-2.

> **Note for the operator (Randy):** like the matrix, this table is
> deferred to a manual session at DXS. Replace each `<DEFERRED —
> Randy to execute at DXS>` cell with the measured ms value (e.g.
> `12.4`) and the delta as a percentage (e.g. `+3.2%`). Phase 2
> sign-off is blocked on this table being fully populated **and** the
> +15 % regression gate being clean.

## Phase 2 known v1 caveats

Caveats emerging from the Phase 2 matrix / latency / spike sessions
will be appended here once the manual checkpoints execute. Known
caveats so far:

- **Mac-server pen pressure:** silently downgraded to a mouse click
  in v1. See "Phase 2 IOHIDUserDevice spike outcome" above. The
  production path for Flame artists is the Rocky Linux server.
  Re-evaluate in v1.1 if a real studio commits to Flame-on-Mac as
  load-bearing.

- **Bookmark password storage is pseudo-obfuscated, not encrypted:**
  `client/bookmarks.py::_encrypt_password` uses XOR against a
  SHA-256 of a host-identity key (machine-id on Linux,
  `platform.node()` + `platform.machine()` + home-dir on macOS). This
  prevents casual reading of a saved bookmark file but is not
  cryptographic protection — anyone with shell access on the machine
  can reverse it. v1.1 follow-up is macOS Keychain-backed storage
  (tracked under Phase 6 SEC hardening). Treat saved passwords as
  obfuscated convenience, not a secure store.

- **Multi-session crop per host (v1.1 follow-up):** v1 supports one
  active streaming session per server host with per-session capture
  crop. When a second concurrent session attaches with a different
  `capture_mode`, the second session sees the first session's crop
  until v1.1 wires the per-session encode wrap. Existing PAM per-user
  X session isolation on Linux prevents cross-tenant capture; this
  limitation only applies to two sessions on the same X display,
  which the small-studio deployment model does not require. Forensic
  signal `event=session.multi_session_crop_collision` is logged at
  WARNING so the v1.1 work has a grep target.

## Phase 3.5 follow-ups

Deferred items discovered during Phase 3 execution that are not
blocking the v1 release but should land in v1.1 / Phase 3.5 before
feature-complete sign-off:

- **P010 raw-capture crop seam.** v1 ships BGRA-only
  `capture_raw_bgra_with_crop` on `ScreenCapture` / `MacScreenCapture`.
  When the NvFBC P010 raw-frame Python seam lands (the C-helper carries
  the `YUV420P10LE` format flag but `nvfbc_backend.py` only exposes
  `capture_raw_bgra` today), add `capture_raw_p010_with_crop` with
  even-pixel 4:2:0 alignment per RESEARCH.md Example 5. v1 correctness
  is preserved because the encoder's internal BGRA→P010 conversion
  preserves 10-bit fidelity end-to-end through the unchanged Phase 2
  pipeline; pick_one + single modes pay the BGRA→P010 conversion cost
  on every cropped frame, but the bandwidth + 10-bit guarantee holds.

- **Multi-session crop per host.** See v1 caveat above — v1.1 wires
  per-session encoder wrap so two concurrent sessions on the same X
  display can each request a different `capture_mode`.

## Phase 3 D-08 4-corner DXS hardware spike

Gate on DISP-03 + DISP-05 — verifies the Plan 03-04 cursor-math /
per-screen DPR implementation against a real mixed-DPI client
(Retina MBP + external non-Retina monitor) driving a 2× NVIDIA Xorg
Rocky server in the DXS lab. Status: **pending hardware session**.
Executor commits the code landed Plan 03-04 Task 1 + Task 2; Randy
runs the manual spike at the DXS office and records the matrix below.

> **Hardware note:** D-08 tests *mixed-DPI cursor math*, not a specific
> display tablet. Any Retina MBP + any external non-Retina monitor
> satisfies the client topology. Pen interaction row below is
> satisfied with an Intuos Pro (no display tablet required).

**Date:** <DEFERRED — Randy to execute at DXS>
**Tester:** Randy McEntee
**Hardware (client):** Mac model: ____ macOS version: ____ Internal DPR: 2.0 External monitor: ____ External DPR: ____
**Hardware (server):** Server: dxs-flame-XX, Rocky 9.X, NVIDIA driver: ____ Monitors: 2× ____

Procedure (from Plan 03-04 Task 3):
1. Launch client with `TERAGUCHI_DEBUG=1` so F12 toggles the coord overlay.
2. Connect in each of 3 modes (`single`, `mirror_all`, `pick_one`) to the
   2× NVIDIA Xorg Rocky server.
3. Move viewer window between Retina internal (DPR 2.0) and external 4K
   (DPR 1.0) for each mode.
4. Click each of the 4 corner pixels of each visible server monitor; read
   the server-px value from F12 overlay; assert it matches the expected
   corner within 1 px.
5. Pen interaction test — ensure Wacom pressure survives screenChanged
   transitions (Pitfall 2).

Pass criterion: `delta px` row in F12 overlay reads `0, 0` (or within
1 px) for every test click. Every cell in the matrix below checked off.

| Mode | Client Screen | Server Monitor | NW | NE | SW | SE | Notes |
|------|---------------|----------------|----|----|----|----|-------|
| single | Retina | primary | ☐ | ☐ | ☐ | ☐ | |
| single | External 4K | primary | ☐ | ☐ | ☐ | ☐ | |
| mirror_all | Retina | mon 1 | ☐ | ☐ | ☐ | ☐ | |
| mirror_all | Retina | mon 2 | ☐ | ☐ | ☐ | ☐ | |
| mirror_all | External 4K | mon 1 | ☐ | ☐ | ☐ | ☐ | |
| mirror_all | External 4K | mon 2 | ☐ | ☐ | ☐ | ☐ | |
| pick_one (mon 2) | Retina | mon 2 | ☐ | ☐ | ☐ | ☐ | |
| pick_one (mon 2) | External 4K | mon 2 | ☐ | ☐ | ☐ | ☐ | |

**Pen interaction across screenChanged:** ☐ verified with Wacom ____

**Sign-off:**
- ☐ DISP-03 verified (cursor lands within 1 px on every corner of every
  monitor in every mode, across both client screens)
- ☐ DISP-05 verified (mixed-DPI rendering — DPR lookup was correct per
  F12 overlay readout on each screen)
- ☐ Re-scope needed (file issue # ____ — D-05 wire model needs rework
  before Plan 05 + 06 proceed)

**Fail actions:** File a regression issue capturing (a) which corner,
(b) which screen, (c) F12 overlay state at failure, (d) measured delta
px. Per CONTEXT.md D-08, the planner re-scopes D-05 before any later
Phase 3 plans build further math on top of the broken foundation.

**Plan 03-04 ROADMAP checkbox:** stays un-flipped (code complete only)
until this table is populated and sign-off boxes checked. See
`.planning/STATE.md` + `.planning/ROADMAP.md` Plan 04 note.

---

*Last updated: 2026-04-20 (Phase 3, Plan 03-04 — added D-08 4-corner hardware spike template; code complete, hardware gate pending).*
