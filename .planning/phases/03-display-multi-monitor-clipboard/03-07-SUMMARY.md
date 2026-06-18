---
phase: 03
plan: 07
subsystem: client
tags: [phase-03, clipboard, client-ui, integration, per-direction-toggle, w-6-midstream]
dependency_graph:
  requires:
    - "Plan 03-01 (Wave 0 RED skeletons for clipboard toggle menu + large text + image integration)"
    - "Plan 03-02 (FullscreenToolbar slot reservation between MonitorSelector and mode badge)"
    - "Plan 03-05 (client/icons.py::icon_clipboard + client/toasts.py::show_oversize_image_toast)"
    - "Plan 03-06 (common/clipboard_chunks.py::ClipboardChunkAssembler + server-side chunk handler + ClientHelloMsg toggle fields)"
  provides:
    - "client/clipboard_toggle_menu.py::ClipboardToggleButton (UI-SPEC Surface 7)"
    - "client/protocol.py::send_clipboard (content-type-aware dispatch + chunking)"
    - "client/protocol.py::_send_chunked (W-6 per-chunk c2s re-check)"
    - "client/protocol.py::_handle_clipboard_chunk (inbound reassembly via shared assembler)"
    - "client/protocol.py::set_clipboard_toggles (session -> protocol bridge)"
    - "client/session.py::_on_clipboard_toggles_changed (UI -> protocol + bookmark persist)"
    - "client/session.py::_on_oversize_image (D-14 toast bridge)"
    - "client/session.py::bind_toolbar / bind_bookmark_manager"
  affects:
    - "Phase 3 CLIP-01 (large text round-trip byte-equal)"
    - "Phase 3 CLIP-02 (PNG round-trip sha256-equal + alpha preservation)"
    - "Phase 3 CLIP-03 (per-direction toggle end-to-end)"
    - "T-03-28b closure on client outbound path (server closure in Plan 06)"
tech_stack:
  added:
    - PySide6.QtWidgets.QToolButton / QMenu / QWidgetAction / QCheckBox (already in stack; new consumer in clipboard_toggle_menu)
  patterns:
    - "Content-type-aware dispatch (send_clipboard branches text vs image before chunking)"
    - "Pitfall 7 snapshot-at-start + re-check-per-iteration cancellation guard"
    - "Chunk-0 boundary gating + _dropped_seqs continuation tracking"
    - "Shared ClipboardChunkAssembler on both ends (single impl; Plan 06 module)"
key_files:
  created:
    - "client/clipboard_toggle_menu.py (NEW — ClipboardToggleButton, 229 lines)"
  modified:
    - "client/protocol.py (send_clipboard refactor + _send_chunked + _handle_clipboard_chunk + hello extension + toggle state)"
    - "client/session.py (clipboard_recv signal widened to (str, object); oversize bridge; toggle changed slot; _push_clipboard_for_paste image path; connect toggles; bind helpers)"
    - "client/fullscreen_toolbar.py (ClipboardToggleButton inserted at Plan 02 marker + clipboard_toggle @property)"
    - "tests/client/test_clipboard_toggle_menu.py (Wave 0 RED -> 5 GREEN unit tests)"
    - "tests/integration/test_clipboard_text_large.py (Wave 0 RED -> 7 GREEN integration tests)"
    - "tests/integration/test_clipboard_image.py (Wave 0 RED -> 9 GREEN integration tests)"
decisions:
  - "Inbound CLIPBOARD_CHUNK text path treats chunks as raw UTF-8 text (not base64) matching the client outbound _send_chunked convention. Server currently only chunks images, so the client inbound text path is exercised by the round-trip tests and stays future-proof if server starts chunking text."
  - "toolbar binding added as bind_toolbar/bind_bookmark_manager helpers (vs. constructor injection) so the existing Session(parent) constructor signature is backward-compatible with main_window code paths."
metrics:
  duration_seconds: 650
  duration_human: "~11 minutes"
  completed_date: "2026-04-21"
  tasks_completed: 1
  files_changed: 7
  files_created: 1
  lines_added: 1326
  lines_removed: 51
---

# Phase 3 Plan 03-07: Clipboard Client UI + Integration Summary

Clipboard client UX (ClipboardToggleButton) + protocol refactor (content-type dispatch, chunked transport, W-6 mid-stream cancellation) + session wiring (bookmark + oversize toast + inbound image dispatch) + CLIP-01/CLIP-02 integration tests — 21 GREEN acceptance tests, full quick suite 3920 passed / 0 failed.

## Commits

| Commit | Type | Description |
|--------|------|-------------|
| `09f2c95` | feat | clipboard client UI + W-6 per-chunk mid-stream cancel (4 files, +759/-16) |
| `53c2a17` | test | flip clipboard Wave 0 RED skeletons GREEN + coverage (3 files, +567/-35) |
| `<docs>` | docs | this SUMMARY + STATE + ROADMAP update |

## What shipped

### `client/clipboard_toggle_menu.py` (NEW)

`ClipboardToggleButton(QToolButton)` per UI-SPEC C-07 / Surface 7. 4-checkbox `QMenu` with:

- **Row 1** (parent): `Copy on this Mac → paste on server` + secondary copy
- **Row 3** (indented under row 1): `Include images (client → server)`
- **Row 2** (parent): `Copy on server → paste on this Mac` + secondary copy
- **Row 4** (indented under row 2): `Include images (server → client)`
- **Header**: `Clipboard direction` (13px / 600 weight / TEXT_PRIMARY)
- **Footer**: `Changes apply to the next copy. Saved per bookmark.` (10px italic / TEXT_MUTED)
- **Accessibility**: accessibleName=`Clipboard direction`; full accessibleDescription

Signal: `toggles_changed = Signal(dict)` carrying `{"text_c2s", "text_s2c", "image_c2s", "image_s2c"}`.

Nested-disable preserves checked state — turning off row 1 grays out row 3 but does NOT clear its checkbox, so re-enabling row 1 restores the prior row 3 state. Disabled-state tooltip copies: `Turn on "Copy on this Mac → paste on server" first.` (and symmetric for row 4).

Default state is all four ON (D-16 secure defaults). `set_state(...)` pre-fill path uses `blockSignals` so it does NOT emit `toggles_changed`. Zero inline hex colors — only `theme.TEXT_PRIMARY / TEXT_SECONDARY / TEXT_MUTED` tokens.

### `client/protocol.py`

**`send_clipboard(content_type, data)`** — replaces the legacy 1-arg `send_clipboard(text)`:
- `text/plain` <=1MB → 1-shot `CLIPBOARD_SEND` (existing behavior).
- `text/plain` >1MB → `_send_chunked` (raw UTF-8 slicing).
- `image/png` → validates magic bytes + 64MB cap; base64-encodes; `_send_chunked`. Oversize fires `on_oversize_image(size_mb)` callback (D-14 toast hook).
- D-15 outbound c2s gating — text and image each gated on their own flag at send-start.
- Legacy single-arg callers (`send_clipboard("hello")`) still route to text/plain.

**`_send_chunked(content_type, payload)`** — emits `ClipboardChunkMsg` per 1MB chunk with W-6 Pitfall 7 mid-stream cancellation:
- Snapshot `c2s_at_start` at sequence start.
- Per-iteration re-read `current_c2s` at loop top; break on False.
- Logs `clipboard.chunk_send_cancelled_mid_stream seq=X sent_chunks=Y total=Z content_type=...` — counts only, no payload (T-03-32b).
- Server-side assembler drops orphan chunks at `CHUNK_TIMEOUT_S` (30s) — no explicit abort wire frame needed.

**`_handle_clipboard_chunk(msg)`** — inbound reassembly via `ClipboardChunkAssembler`:
- D-15 chunk-0-boundary s2c gate; `_dropped_seqs` continuation tracks dropped sequences.
- Per-protocol `_clipboard_chunks` dict (seq_id → assembler).
- D-16 defense-in-depth: PNG magic + 64MB cap re-validated on completion BEFORE firing `on_clipboard`.
- Stale-cleanup on every inbound chunk via `assembler.is_stale(now)`.
- Image path: base64-decode after reassembly, then validate, then `on_clipboard("image/png", raw)`.
- Text path: raw UTF-8 (no base64) per convention, `on_clipboard("text/plain", text)`.

**`set_clipboard_toggles(text_c2s, text_s2c, image_c2s, image_s2c)`** — session-to-protocol bridge.

**`ClientHelloMsg` extension** — the 4 `clipboard_*` toggle fields ride on every connect (server mirrors to `ClientSession` on first packet).

### `client/session.py`

**Bridge signals widened**:
- `clipboard_recv = Signal(str, object)` — `(content_type, payload)`; `object` permits both str + bytes.
- `oversize_image = Signal(float)` — size in MB, bridges from I/O thread to GUI thread.

**`_on_clipboard_recv(content_type, data)`** — `image/png` routes via `QImage.fromData` → `QApplication.clipboard().setPixmap`; text via `.setText`. Echo-suppression flag prevents the `dataChanged` feedback loop.

**`_on_oversize_image(size_mb)`** — calls `show_oversize_image_toast(self.viewer, size_mb)` (D-14 Surface 8).

**`_on_clipboard_toggles_changed(state)`** — called by `ClipboardToggleButton.toggles_changed`; pushes into `protocol.set_clipboard_toggles` and persists to bookmark via `bookmark_manager.update(bid, clipboard_*=...)`.

**`_push_clipboard_for_paste()`** extended — QImage on clipboard gets encoded to PNG via `QBuffer` and pushed via `send_clipboard("image/png", bytes)`. Text push still fires if present.

**`connect(...)` / `connect_with_profile(...)`** — 4 new clipboard_* kwargs. Before the handshake, `protocol.set_clipboard_toggles` is called so the first `ClientHelloMsg` carries the bookmark's saved toggle state, and `toolbar.clipboard_toggle.set_state(...)` syncs the UI without emitting signals.

**`bind_toolbar(toolbar)` / `bind_bookmark_manager(manager)`** — late-binding helpers so `main_window` can attach the toolbar + bookmark manager after construction without changing the `Session()` constructor signature.

### `client/fullscreen_toolbar.py`

`ClipboardToggleButton` inserted at the slot Plan 02 reserved between `MonitorSelector` and the mode badge. New `clipboard_toggle` `@property` exposes it to session wiring.

## Test gate

### Plan acceptance battery (21/21 GREEN)

```
tests/client/test_clipboard_toggle_menu.py .....                          [ 23%]
tests/integration/test_clipboard_text_large.py .......                    [ 57%]
tests/integration/test_clipboard_image.py .........                       [100%]

21 passed in 1.43s
```

Breakdown:

| File | Tests | Coverage |
|------|-------|----------|
| `test_clipboard_toggle_menu.py` | 5 | toggles_changed emission / default-ON / nested-disable rows 3+4 / set_state no-emit |
| `test_clipboard_text_large.py` | 7 | >1MB sha256 round-trip / CRLF+LF+CR preservation / small text 1-shot path / text_c2s gate / inbound chunked text |
| `test_clipboard_image.py` | 9 | fixture_png sha256 / RGBA alpha-preserved / >1MB chunked round-trip / image_c2s gate / oversize toast / bad magic / W-6 mid-stream cancel / inbound chunk s2c gate / inbound round-trip |

### Full quick suite

```
3920 passed, 105 skipped, 4 deselected, 2 xfailed in 23.41s
```

**+21 tests vs Plan 06 baseline (3899). -10 Wave 0 skips converted GREEN (3 toggle + 4 text + 3 image).**

Zero regressions. All Phase 1+2+3 gates stay green with chunked transport + toggles active.

### Plan acceptance grep battery (all pass)

- `ls client/clipboard_toggle_menu.py` — exists
- `grep "class ClipboardToggleButton" client/clipboard_toggle_menu.py` — 1
- `grep "Clipboard direction" client/clipboard_toggle_menu.py` — 4 (accessible name + menu header + 2 doc mentions)
- Surface 7 row 1 verbatim — present (2 matches: row label + nested tooltip reference)
- Surface 7 rows 2/3/4 + footer + disabled tooltip — present
- `grep "def send_clipboard" client/protocol.py` — 1
- `grep -E "_clipboard_chunks|_dropped_seqs" client/protocol.py` — 15
- `grep "ClipboardToggleButton" client/fullscreen_toolbar.py` — 2
- `grep "set_clipboard_toggles" client/protocol.py client/session.py` — 5
- `grep "show_oversize_image_toast" client/session.py` — 2
- Zero `#[0-9a-fA-F]{6}` hex in `clipboard_toggle_menu.py` — PASS
- `grep "chunk_send_cancelled_mid_stream" client/protocol.py` — 1
- `grep -E "current_c2s|c2s_at_start" client/protocol.py` — 4

## Requirements shipped

| REQ-ID | Behavior | Status |
|--------|----------|--------|
| **CLIP-01** | Bidirectional text clipboard, CRLF-safe | COMPLETE (server-side in Plan 06; client-side integration + newline preservation tests land here) |
| **CLIP-02** | Bidirectional image clipboard | COMPLETE (server paths in Plan 06; client UI + wire chunking + inbound QImage.setPixmap + round-trip integration tests land here) |
| **CLIP-03** | Per-direction paste toggle | COMPLETE (server gating in Plan 06; client ClipboardToggleButton UI + bookmark persist + protocol state + W-6 mid-stream cancellation land here) |

## STRIDE threat closure

| Threat | Status | Closure |
|--------|--------|---------|
| T-03-28b (per-direction toggle bypass via mid-stream chunk) | CLOSED | Server path closed in Plan 06 (_dropped_seqs + chunk-0 boundary gate + assembler timeout). Client path closed here: `_send_chunked` snapshots c2s at sequence start AND re-checks per-chunk (breaks loop + logs `clipboard.chunk_send_cancelled_mid_stream`); `_handle_clipboard_chunk` gates inbound s2c at chunk-0 boundary with `_dropped_seqs` continuation tracking. |
| T-03-29 (clipboard exfiltration via background paste) | MITIGATED | ClipboardToggleButton surfaces the 4-direction knob; D-16 secure defaults (all ON) stay policy-aligned for small-studio trust model; W-6 closes the mid-stream race so the toggle reflects user intent even while a large payload is chunking. |
| T-03-32b (structlog payload leak) | MITIGATED | All new structlog events (`chunk_send_cancelled_mid_stream`, `image_oversize`, `image_recv_set`, `chunk_dropped_toggle`, `chunk_assembled`, `chunk_invalid_total`, `chunk_timeout`) carry counts + sizes + seq_id only — never the data field. |

## Deviations from plan

### Rule 1 fix — inbound chunked text path semantics

**Found during:** Task 1 integration tests (`test_inbound_chunked_text_reassembles_byte_equal`).

**Issue:** The initial implementation of `_handle_clipboard_chunk` in the client assumed text chunks were base64-encoded (symmetric with image chunks). That would be correct IF the server chunked text, but per Plan 06 the server only chunks images (text goes 1-shot via `CLIPBOARD_RECV`). So the client inbound text branch was unreachable in the real wire contract, but the integration test exposed the asymmetry: my client outbound `_send_chunked` text path slices raw UTF-8 (no base64), so a symmetric client round-trip test would fail on the base64 decode.

**Fix:** Refactored `_handle_clipboard_chunk` to branch AFTER reassembly — base64-decode only for `image/png`, treat `text/plain` as raw UTF-8 matching the outbound chunker. This is the correct symmetric design: both text chunk paths (outbound `_send_chunked` + inbound `_handle_clipboard_chunk`) share the raw-UTF-8 convention; both image chunk paths (outbound `_send_chunked` post-base64 + inbound `_handle_clipboard_chunk` post-base64-decode) share the base64 convention.

**Impact:** Zero behavior change against the current server (server doesn't chunk text today). Future-proofs the client to consume server-emitted chunked text without surprise.

**Files modified:** `client/protocol.py` (`_handle_clipboard_chunk` refactored).

**Commit:** `09f2c95` (single implementation commit includes the fix).

### Rule 3 fix — noise PNG generator for chunking test

**Found during:** Task 1 image integration tests (`test_png_chunked_transport_reassembles`).

**Issue:** Initial 1024×1024 RGBA gradient PNG compressed to ~82 KB (well below the 1 MB chunk threshold) because PIL's zlib handles smooth gradients efficiently. That meant the chunked-transport test never actually chunked.

**Fix:** Added `noisy=True` parameter to `_generate_rgba_png` that fills pixels with deterministic pseudo-random RGBA (seed=42). 800×800 noise yields 2+ MB PNG, forcing multi-chunk wire traffic.

**Impact:** Test actually exercises the chunking path. Zero production code change.

**Files modified:** `tests/integration/test_clipboard_image.py` (`_generate_rgba_png` helper + 2 test sites).

**Commit:** `53c2a17`.

## Auth gates

None encountered.

## Known stubs

None. All code paths are wired end-to-end.

## Self-Check: PASSED

- File `client/clipboard_toggle_menu.py` — FOUND
- Commit `09f2c95` (feat) — FOUND
- Commit `53c2a17` (test) — FOUND
- Plan acceptance battery 21/21 GREEN — VERIFIED
- Full quick suite 3920 passed / 0 failed — VERIFIED
- All plan acceptance greps pass — VERIFIED
