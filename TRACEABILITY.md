# teraguchi — AI session traceability

Append-only ledger of AI agent sessions that modified this repository.
Each row links a Cursor/AI session to the tickets it worked on and what changed.

## Log

| Date (ISO) | Session ID | Ticket(s) | Scope | agent-hours | Status | Notes |
|------------|------------|-----------|-------|-------------|--------|-------|
| 2026-04-15 | 2e2ee3ac | teragucci#8, teragucci#9 | mic pipeline + bookmark bar hide | 4–6h | completed | Pushed to GitLab dev branch |

## Session notes

- 2e2ee3ac: **Microphone pipeline (teragucci#8)** — traced full chain: Mac client sends ~50 MIC frames/s via WebSocket binary (type 0x11); server binary handler calls `MicInjector.write()`; race condition in `MicInjector.start()` caused writer thread to exit immediately (`_started` was set after `thread.start()` so the loop saw `False` and quit); fix: move `self._started = True` before `self._thread.start()`. Virtual microphone uses `module-null-sink` + `module-virtual-source` (PipeWire 0.3.48 on Ubuntu 22.04); `teraguchi_mic` appears as a real input device in GNOME Sound Settings. Also fixed audio timestamp `OverflowError` (`& 0x7FFFFFFF` on both video and audio frame timestamps in `client/session.py`).
- 2e2ee3ac: **Bookmark bar toggle (teragucci#9)** — added `UISettings` class to `client/bookmarks.py` (load/save `ui_settings.json` in same config dir); dock defaults to hidden; shortcut `B` via `bm_action.setShortcut`; state saved on `visibilityChanged`, skipped during fullscreen transitions.
