# Phase 3: Display + Multi-Monitor + Clipboard — Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-19
**Phase:** 03-display-multi-monitor-clipboard
**Areas discussed:** Mode selector UX + lock-on-connect, Cursor-coord math + mixed-DPI, Hot-plug behavior (both sides), Clipboard image + per-direction toggle

---

## Gray Area Selection

| Option | Description | Selected |
|--------|-------------|----------|
| Mode selector UX + lock-on-connect | DISP-01 / DISP-07. Where the selector lives, mode semantics, mid-session enforcement. | ✓ |
| Cursor-coord math + mixed-DPI | DISP-03 / DISP-05. Wire coord space, DPR policy, diagnostics, spike bar. | ✓ |
| Hot-plug behavior (both sides) | DISP-02 / DISP-06. Degrade vs reconnect, xrandr guard, SCK upgrade, CI. | ✓ |
| Clipboard image + per-direction toggle | CLIP-01 / CLIP-02 / CLIP-03. Wire format, size cap, toggle UI, defaults. | ✓ |
| CustomEDID + Flame monitor-config | DISP-04. Routed to Claude's Discretion (thin problem, existing code covers it). | Claude's discretion |

**User's choice:** All 4 selected.

---

## Mode selector UX + lock-on-connect

### Q1 — Where should the monitor-mode picker live?

| Option | Description | Selected |
|--------|-------------|----------|
| Connect dialog only (Recommended) | Locked on connect; toolbar read-only; bookmark stores last-used as default. | ✓ |
| Bookmark setting + optional override | Bookmark default; advanced disclosure at connect. | |
| Toolbar dropdown (grayed mid-session) | Lives next to MonitorSelector; editable between sessions. | |

### Q2 — How should modes map to server-side capture + client-side rendering?

| Option | Description | Selected |
|--------|-------------|----------|
| Server-crops per mode (Recommended) | Server capture = full virtual desktop; GPU crop before encode for single/pick-one; mirror-all unchanged. | ✓ |
| Client-crops, server always sends full | Server always sends virtual desktop; client crops. | |
| Negotiated per session via ServerHelloMsg | Client declares mode in handshake; server reconfigures encoder geometry. | |

### Q3 — How do we enforce "no switch mid-session"?

| Option | Description | Selected |
|--------|-------------|----------|
| Grayed UI + tooltip explains (Recommended) | "Disconnect and reconnect to change monitor mode." | ✓ |
| Hidden entirely during session | Selector disappears; surfaces in Session Info panel. | |
| Allow with forced reconnect dialog | Confirm dialog on mid-session change. | |

### Q4 — Pick-one monitor selection UX?

| Option | Description | Selected |
|--------|-------------|----------|
| Reuse existing MonitorSelector, single-check only (Recommended) | Constrain to radio behavior; bookmark stores last pick by id+name; fallback to primary. | ✓ |
| Dedicated radio list in connect dialog | Numbered thumbnails; needs server thumbnails (new). | |
| Last-known-good per bookmark | Bookmark stores picked monitor; fallback to primary. | |

**Notes:** All recommendations accepted. Bookmark behavior combines Q4 recommended + last-known-good behavior (stores last pick by id+name).

---

## Cursor-coord math + mixed-DPI

### Q1 — Authoritative cursor coordinate space?

| Option | Description | Selected |
|--------|-------------|----------|
| Server physical pixels (Recommended) | Integer pixel coords on the wire; client does widget→physical math once. | ✓ |
| Keep normalized 0.0-1.0 | Existing path; float-precision artifacts on 10k-wide desktops. | |
| Hybrid physical/normalized | Mouse physical, pen normalized. | |

### Q2 — Qt per-window DPR policy on mixed-DPI Mac?

| Option | Description | Selected |
|--------|-------------|----------|
| Per-screen DPR via QScreen::devicePixelRatio() (Recommended) | Look up current QScreen's DPR; re-eval on screenChanged. | ✓ |
| Lock to primary screen DPR | Cache primary DPR at session start. | |
| Force-disable Qt HiDPI scaling | Handle all scale math ourselves. | |

### Q3 — Client-side cursor-accuracy diagnostic?

| Option | Description | Selected |
|--------|-------------|----------|
| Dev-only F12 overlay (Recommended) | Gated on TERAGUCHI_DEBUG env var; invisible in release. | ✓ |
| Hidden forever, rely on unit tests | Pure pytest coverage only. | |
| Shipped feature in Help menu | Full diagnostic dialog like key_diagnostic.py. | |

### Q4 — Pre-phase Cintiq Pro 24 + Retina spike pass bar?

| Option | Description | Selected |
|--------|-------------|----------|
| Cursor on-target at all 4 corners of each monitor (Recommended) | 4-corner click test, both client screens × pick-one / mirror-all modes, 1px tolerance. | ✓ |
| Subjective "feels right" after 30 min of Flame | Un-reproducible. | |
| Automated pixel-accuracy test with webcam + OCR | Gold-plated overkill. | |

**Notes:** All recommendations accepted. Follow-up: "Move to next area."

---

## Hot-plug behavior (both sides)

### Q1 — Server-side monitor add/remove mid-session?

| Option | Description | Selected |
|--------|-------------|----------|
| Degrade-in-place + remap UI (Recommended) | Stream keeps flowing; MonitorListMsg broadcasts; encoder restarts; non-modal banner "click to remap"; picked-monitor-gone auto-falls-back to primary + toast. | ✓ |
| Forced reconnect with preserved FSM state | Tear down + reattach with stored bookmark. | |
| Pause stream + confirm dialog | Modal "Remap / Reconnect / Disconnect." | |

### Q2 — NVIDIA xrandr-segfault guard?

| Option | Description | Selected |
|--------|-------------|----------|
| Keep RESIZE_REQUEST stubbed + document (Recommended) | No xrandr-set in Phase 3; fixed geometry from CustomEDID at bootstrap; read-only hot-plug. | ✓ |
| Isolate xrandr calls to subprocess with timeout | SIGKILL on timeout so driver crash can't kill server. | |
| Investigate and fix the root cause | v1.1 scope. | |

### Q3 — macOS SCK hot-plug signature upgrade?

| Option | Description | Selected |
|--------|-------------|----------|
| Signature = (displayID, width, height, x, y) + SCStreamDelegate callback (Recommended) | Catches reorder + same-size swap + reposition; push + 5s poll safety net. | ✓ |
| Subscribe to NSScreen NSApplicationDidChangeScreenParametersNotification | Push-only via AppKit; kills 5s poll on Mac; diverges from Linux. | |
| Keep current shallow check | Accept the misses. | |

### Q4 — Hot-plug CI coverage?

| Option | Description | Selected |
|--------|-------------|----------|
| Mocked mss/SCK + protocol assertions (Recommended) | Unit + in-process loopback integration; no real hardware in CI. | ✓ |
| CI + chaos test that randomly toggles monitors for 10 min | Goes beyond P1 smoke. | |
| No CI — covered by real-hardware ritual only | Too loose. | |

**Notes:** All recommendations accepted. Follow-up: "Move to next area."

---

## Clipboard image + per-direction toggle

### Q1 — How should image clipboard data travel on the wire?

| Option | Description | Selected |
|--------|-------------|----------|
| PNG lossless, base64 in existing ClipboardMsg (Recommended) | One format, one codepath; universal on NSPasteboard + X11. | ✓ |
| PNG + JPEG, client negotiates | Two formats; bandwidth-sensitive but JPEG lossy-compresses screenshots. | |
| Raw RGBA over a new binary clipboard channel | Skip PNG encode; not interop with native clipboards without re-encode. | |

### Q2 — Image size cap?

| Option | Description | Selected |
|--------|-------------|----------|
| 16 MB cap, silent-fail-closed + toast (Recommended) | Covers 4K screenshot; fail-closed local toast. | |
| 64 MB cap, same fail-closed behavior | More headroom for high-res paint references + multi-layer PSD screenshots. | ✓ |
| No cap, let QUIC/WS frame size win | DoS risk. | |

### Q3 — Toggle UI location?

| Option | Description | Selected |
|--------|-------------|----------|
| Per-bookmark + session-override via toolbar (Recommended) | Four bool flags on bookmark; toolbar 4-checkbox menu for in-session flip; mirrors P2 D-10 Cmd↔Ctrl swap pattern. | ✓ |
| Global-only in Settings | One toggle set for all sessions. | |
| Connect dialog only (lock-at-session-start) | Lock mode; clipboard privacy often discovered mid-session. | |

### Q4 — Default policy + security defaults?

| Option | Description | Selected |
|--------|-------------|----------|
| All directions ON, magic-byte + size validated (Recommended) | PNG sig check + 64MB cap on receive; preserve native CRLF/LF; small-studio trust. | ✓ |
| Text↔ ON, Image OFF by default | Conservative; friction for paint-reference flow. | |
| All ON but c2s paste prompts first time per session | Security-theater prompt. | |

**Notes:** Q2 user chose 64 MB (non-recommended) over 16 MB. Follow-up Q5 below addresses the base64-JSON size consequence.

### Q5 — 64 MB PNG (~85 MB base64 JSON) transport path?

| Option | Description | Selected |
|--------|-------------|----------|
| Keep on control channel (Recommended) | Chunk into 1 MB JSON messages with sequence ID so input events interleave; clipboard chunks get own queue slot distinct from streaming queue. | ✓ |
| Side channel via existing file_transfer.py path | Reuse FILE_TRANSFER infra; more implementation; zero control-channel stall. | |
| Raise to 64 MB without explicit interleave guarantee | Risks head-of-line blocking. | |

---

## Claude's Discretion

- CustomEDID Flame-approved profile selection (DISP-04) — existing `session_manager.py::find_edid_file` has PCoIP fallback + 1920×1200 TGC generator; planner picks a Flame-approved profile name at implementation time.
- Exact `MonitorListMsg` / new protocol message field shapes for D-02, D-05, D-17.
- Banner / toast visual design for D-09 remap.
- 4-checkbox menu visual design for D-15 toolbar toggle.
- F12 overlay visual design for D-07.
- `SCStreamDelegate` placement (D-11) — in `mac_screen_capture.py` or thin wrapper.
- Test fixture layout for hotplug mocks.

---

## Deferred Ideas

See `03-CONTEXT.md` `<deferred>` section for the full list — includes RESIZE_REQUEST resurrection, rich-text / file-reference clipboard, JPEG as second image format, side-channel clipboard transport, window-straddling DPR correctness, CGEventTap aggressive capture, self-hosted CI, chaos-test extensions, DISPLAY env-var thread-safety, cursor-shape mixed-DPI sync, reconnect-clipboard confirmation, HIDDriverKit, full ICC pipeline, Wayland/Rocky 10.
