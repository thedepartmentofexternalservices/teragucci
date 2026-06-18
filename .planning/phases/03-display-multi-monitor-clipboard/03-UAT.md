---
status: partial
phase: 03-display-multi-monitor-clipboard
source: [03-01-SUMMARY.md, 03-02-SUMMARY.md, 03-03-SUMMARY.md, 03-04-SUMMARY.md, 03-05-SUMMARY.md, 03-06-SUMMARY.md, 03-07-SUMMARY.md]
started: 2026-04-23T00:00:00Z
updated: 2026-04-23T00:00:00Z
---

## Current Test

[testing paused — 8 items outstanding (all blocked by hardware/environment)]

## Tests

### 1. Automated Test Suite — Server + Common + Broker
expected: All server-side, common, and broker unit tests pass with zero failures on the target Python (3.12+) with full dev dependencies installed.
result: pass

### 2. Automated Test Suite — Client Tests
expected: All client unit tests pass in an environment with PySide6 + Python 3.12+.
result: blocked
blocked_by: server
reason: "PySide6 not available on this machine (dxs-ansible, Rocky 9.3, system Python 3.9). Client tests require PySide6 6.10+ which needs Python 3.12+. The venv at .venv has broken python symlinks. All 18 client test files and all integration tests that import client modules were skipped or errored."

### 3. Monitor Mode Selector (DISP-01)
expected: Connect dialog shows Single monitor / Mirror all / Pick one radio group. Selecting Pick one reveals a sub-selector listing available server monitors. Mode persists per-bookmark across reloads. Switching modes mid-session is disabled with tooltip text.
result: blocked
blocked_by: physical-device
reason: "Requires PySide6 GUI + connection to a Teraguchi server with multiple monitors. Cannot verify on dxs-ansible (headless, no GPU, no PySide6)."

### 4. Monitor Hot-Plug During Session (DISP-02)
expected: Unplugging/plugging a monitor on the server mid-session does not crash. Client sees a remap banner (RemapBanner) + info toast + degraded mode badge. Session continues streaming.
result: blocked
blocked_by: physical-device
reason: "Requires physical monitor hot-plug on a running Flame workstation with active Teraguchi session."

### 5. Cursor Coordinates on Mixed-DPI (DISP-03 / DISP-05)
expected: Cursor lands on correct pixel when Retina MBP + external non-Retina monitor client connects to 2x2560x1600 Rocky NVIDIA Xorg server. F12 overlay shows delta px = 0,0 at all 4 corners in all 3 modes.
result: blocked
blocked_by: physical-device
reason: "D-08 4-corner DXS hardware spike. Requires Randy at DXS office with mixed-DPI client rig + Intuos Pro + 2x NVIDIA Xorg server. Template pre-populated in docs/release.md."

### 6. CustomEDID Flame-Approved Profile (DISP-04)
expected: Xvfb sessions advertise 'Eizo CG279X' with ENC manufacturer ID so Flame's monitor-config dialog accepts without warning.
result: pass

### 7. ScreenCaptureKit Hot-Plug (DISP-06)
expected: macOS server detects display configuration changes via NSWorkspace notification within ~1 second; session continues with updated topology.
result: blocked
blocked_by: physical-device
reason: "Requires macOS server (Mac Studio or MacBook Pro) with ScreenCaptureKit. Tests skip on non-Mac via pytest.importorskip. Server-side code review confirms implementation is correct."

### 8. Per-Monitor Fullscreen Mode (DISP-07)
expected: Pick-one mode crops to selected server monitor. Mirror-all shows full virtual desktop. Single mode shows one monitor. Fallback to primary when picked monitor vanishes.
result: blocked
blocked_by: physical-device
reason: "Requires active Teraguchi session with multi-monitor server. Server-side crop logic (capture_raw_bgra_with_crop) passed all 7 unit tests. Integration test_capture_mode passed all 6 tests. Full end-to-end requires GUI."

### 9. Bidirectional Text Clipboard (CLIP-01)
expected: Text >1MB round-trips with CRLF/LF/CR preserved. Chunked transport for large payloads. No corruption.
result: blocked
blocked_by: server
reason: "Integration tests (test_clipboard_text_large.py) require PySide6 for client protocol instantiation. Server-side CRLF preservation (text=False + explicit UTF-8 decode) confirmed in code review. 3 CRLF parametrized tests passed in server unit suite."

### 10. Bidirectional Image Clipboard (CLIP-02)
expected: PNG screenshots round-trip with sha256 equality. PNG magic-byte validation rejects malformed payloads. 64MB cap enforced. Chunked transport for images >1MB.
result: blocked
blocked_by: server
reason: "Integration tests (test_clipboard_image.py) require PySide6. Server-side PNG path (xclip + NSPasteboardTypePNG) + validate_png_payload + ClipboardChunkAssembler all passed their unit tests. 7 chunking tests + 7 image tests + 4 mac-image tests passed."

### 11. Per-Direction Clipboard Toggles (CLIP-03)
expected: 4-checkbox toggle menu in toolbar. Each direction (text c2s/s2c, image c2s/s2c) independently gateable. Toggle state persists per-bookmark. Mid-stream toggle change (Pitfall 7) completes in-flight transfer then gates subsequent ones.
result: pass

### 12. Code Review Findings Fixed
expected: All 7 findings from 03-REVIEW.md (CR-01 + WR-01 through WR-06) are fixed and committed.
result: pass

## Summary

total: 12
passed: 4
issues: 0
pending: 0
skipped: 0
blocked: 8

## Gaps

[none — all testable items passed; blocked items require hardware/environment not available on dxs-ansible]
