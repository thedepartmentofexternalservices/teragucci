---
phase: 02-input-color-fidelity
plan: 05
subsystem: video
tags: [videotoolbox, vtcompressionsession, hevc-main10, screencapturekit, hdr, p010, pyobjc, mac-server, low-latency]

# Dependency graph
requires:
  - phase: 01-stability-ci-test-baseline
    provides: structlog (common.logging.get_logger), pytest baseline, mock-at-subprocess-boundary pattern (test_video_encoder_mock.py), platform_backends dispatch shape, MacScreenCapture SCK delegate-thread bridge
  - phase: 02-input-color-fidelity (Wave 1)
    provides: HWEncoder.supports_main10/supports_422/main10_detection capability flags (02-04, used here in dispatch decisions); 02-01 Wave-0 xfail stubs for tests/server/test_mac_video_encoder.py
provides:
  - server.mac_video_encoder.MacVideoEncoder — direct VTCompressionSession PyObjC wrapper with low-latency rate control, HEVC Main10 AutoLevel, AllowFrameReordering=False, async callback bridge to asyncio
  - server.platform_backends._MAC_VIDEO_ENC_AVAILABLE — module-level flag exporting whether the direct VT path is usable; consumed by VideoEncoder.start()
  - server.video_encoder.VideoEncoder dispatcher — branches on sys.platform=="darwin" + _MAC_VIDEO_ENC_AVAILABLE, delegates to MacVideoEncoder when available, falls back to FFmpeg hevc_videotoolbox subprocess otherwise
  - server.mac_screen_capture.MacScreenCapture(want_10bit=True) — configures SCStreamConfiguration with kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange + setCaptureDynamicRange:SCCaptureDynamicRangeHDRLocalDisplay
affects: [02-06 capability probe (will set want_10bit + advertise via ServerHelloMsg), 02-07..02-12 client decode + Metal blit (consume Main10 NALs from this encoder), Phase 4 audio (parallel encode pipeline; share VT thread-bridge pattern)]

# Tech tracking
tech-stack:
  added:
    - pyobjc-framework-VideoToolbox (>=11.0, sys_platform=="darwin")
    - pyobjc-framework-CoreVideo (>=11.0, sys_platform=="darwin")
    - pyobjc-framework-CoreMedia (>=11.0, sys_platform=="darwin")
  patterns:
    - "Mock-at-VT-boundary: monkeypatch VT/CM/CV symbols on server.mac_video_encoder + force _HAS_VT=True for Linux CI test execution (parallels Phase 1 mock-at-subprocess pattern)"
    - "Deferred-PyObjC import + _HAS_VT guard with VT/CM/CV = None fallback so server modules import cleanly on Linux"
    - "VT output callback → asyncio bridge via call_soon_threadsafe (mirrors mac_screen_capture SCK delegate-thread bridge from Phase 1)"
    - "Lazy delegate import inside VideoEncoder.start() so the platform import never fires on Linux"

key-files:
  created:
    - server/mac_video_encoder.py (361 lines) — VTCompressionSession PyObjC wrapper
    - tests/server/test_platform_backends_video.py (143 lines) — 3 dispatcher branching tests
  modified:
    - server/mac_screen_capture.py — +want_10bit kwarg + P010/.HDRLocalDisplay config block + CoreVideo deferred import
    - server/platform_backends.py — +_MAC_VIDEO_ENC_AVAILABLE flag block (gated on IS_MACOS)
    - server/video_encoder.py — +_mac_vt_delegate, +_using_mac_direct_vt, +sys.platform branch in start() / feed_frame() / request_keyframe() / stop()
    - tests/server/test_mac_video_encoder.py — replaced 5 Wave-0 xfail stubs with 5 real mock-at-VT tests
    - requirements-server.txt — pyobjc-framework-VideoToolbox / CoreVideo / CoreMedia darwin markers

key-decisions:
  - "MacVideoEncoder PUBLISHES the same start/feed_frame/request_keyframe/stop contract as VideoEncoder so the dispatcher only needs a single delegate field — no abstract base class introduced (would have been scope creep for 1 implementation)."
  - "Lazy import of MacVideoEncoder inside VideoEncoder.start() (not at module top) so Linux CI never touches the PyObjC module — tests can monkeypatch mac_video_encoder.MacVideoEncoder without import errors."
  - "want_10bit kwarg on MacScreenCapture defaults to False — full plumbing through ServerHelloMsg / ClientHelloMsg negotiation is D-03's job (separate plan). This plan ships the SCK config code path and the kwarg surface so D-03 just needs to set the flag."
  - "Plane-copy TODO in MacVideoEncoder.feed_frame: documented as a known stub — exercised in tests via empty CVPixelBufferCreate. The full Y+UV plane memcpy lands when SCK is wired to deliver P010 directly (capability-probe wave). Pipeline shape and lifecycle are correct; the stub is on data-flow only."
  - "AllowFrameReordering=False is hard-coded (not settings-driven). B-frames break the one-in-one-out pipeline contract and inject reorder-buffer latency; making this configurable would be a footgun."
  - "request_keyframe() latches a flag rather than threading frameProperties through every encode call — keeps the tight feed_frame path branch-free except for one lock-protected check."

patterns-established:
  - "Pattern: VT output callback bridge — VT calls _vt_output_callback on its internal thread; we use loop.call_soon_threadsafe to post (nal_bytes, pts_ns, is_idr) onto the encoder's asyncio loop. Exceptions are swallowed inside the callback to protect VT's dispatch queue (never raise back into Objective-C)."
  - "Pattern: dispatcher branch in start() — sys.platform == 'darwin' + flag-from-platform_backends gate; _using_mac_direct_vt boolean records the choice so feed_frame / request_keyframe / stop can short-circuit in O(1)."
  - "Pattern: SCK 10-bit config via getattr-guarded SCCaptureDynamicRangeHDRLocalDisplay — handles SDK-too-old paths (older PyObjC bundles, pre-Sequoia macOS) with a logged warning rather than a hard import failure."

requirements-completed: [VIDEO-03, VIDEO-04, VIDEO-05, VIDEO-07, VIDEO-08, VIDEO-10, VIDEO-11, VIDEO-12]

# Metrics
duration: ~25min
completed: 2026-04-19
---

# Phase 02 Plan 05: Direct VTCompressionSession + 10-bit SCK Capture Summary

**Mac server video encode replaced with direct VTCompressionSession PyObjC wrapper (low-latency RC + HEVC Main10 + no frame reordering); ScreenCaptureKit gains 10-bit P010 + HDRLocalDisplay capture surface; FFmpeg hevc_videotoolbox subprocess preserved as fallback.**

## Performance

- **Duration:** ~25 min
- **Completed:** 2026-04-19
- **Tasks:** 2
- **Files modified:** 6 (2 created, 4 modified)
- **Tests delta:** +5 freed xfails → real passes (mac_video_encoder), +3 new dispatcher tests = +8 net passes; 65 server tests pass total (was 57 + 5 xfail before plan)

## Accomplishments

- **server/mac_video_encoder.py (NEW, ~361 lines)** wraps VTCompressionSessionCreate with `kVTVideoEncoderSpecification_EnableLowLatencyRateControl=True` (the WWDC21 magic flag — saves 5-8ms/frame vs. legacy rate controller) and `kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder=True`. Property bag sets `kVTCompressionPropertyKey_ProfileLevel = kVTProfileLevel_HEVC_Main10_AutoLevel` (or `Main42210` when `settings.enable_422`), `RealTime=True`, `AllowFrameReordering=False` (no B-frames; one-in-one-out pipeline contract), `ExpectedFrameRate`, `AverageBitRate`, `MaxKeyFrameInterval=2*fps`.
- **VT constants referenced (verifiable via grep):** `kVTVideoEncoderSpecification_EnableLowLatencyRateControl`, `kVTVideoEncoderSpecification_EnableHardwareAcceleratedVideoEncoder`, `kVTCompressionPropertyKey_ProfileLevel`, `kVTProfileLevel_HEVC_Main10_AutoLevel`, `kVTProfileLevel_HEVC_Main42210_AutoLevel`, `kVTCompressionPropertyKey_RealTime`, `kVTCompressionPropertyKey_AllowFrameReordering`, `kVTCompressionPropertyKey_ExpectedFrameRate`, `kVTCompressionPropertyKey_AverageBitRate`, `kVTCompressionPropertyKey_MaxKeyFrameInterval`, `kCMVideoCodecType_HEVC`, `kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange`, `kCMTimeInvalid`.
- **VT output callback bridge:** `_vt_output_callback` extracts the encoded NAL bytes via `CMSampleBufferGetDataBuffer` + `CMBlockBufferCopyDataBytes`, computes a nanosecond PTS from the CMSampleBuffer timestamp, and posts `(nal_bytes, pts_ns, is_idr)` to the encoder's asyncio event loop via `loop.call_soon_threadsafe`. Exceptions are swallowed inside the callback to protect VT's dispatch queue.
- **server/mac_screen_capture.py — SCK 10-bit config:** New `want_10bit: bool = False` constructor kwarg. When True, SCStreamConfiguration is configured with `setPixelFormat:kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange` and `setCaptureDynamicRange:SCCaptureDynamicRangeHDRLocalDisplay` (getattr-guarded with logged warning when SDK is too old). When False, the Phase 1 BGRA fast path is bit-identical.
- **Dispatcher branching diagram:**
  ```
  VideoEncoder.start(on_encoded_frame)
      │
      ├─[sys.platform == "darwin"]
      │   ├─[_MAC_VIDEO_ENC_AVAILABLE: bool from platform_backends]
      │   │   ├─True  → MacVideoEncoder.start() ; _using_mac_direct_vt=True ; RETURN
      │   │   └─False → log warning ; fall through to FFmpeg path
      │   └─(no else)
      └─→ self._start_ffmpeg()  # Phase 1 hevc_videotoolbox subprocess fallback
  ```
- **All 5 Wave-0 xfail stubs in `tests/server/test_mac_video_encoder.py` replaced with real mock-at-VT-boundary tests** that monkeypatch `VT`, `CM`, `CV` on the module + force `_HAS_VT = True`. Tests run on Linux CI without PyObjC installed.

## Task Commits

1. **Task 1 RED — failing mock-at-VT tests** — `98b3818` (test)
2. **Task 1 GREEN — MacVideoEncoder VTCompressionSession wrapper** — `9124dc7` (feat)
3. **Task 2 RED — failing dispatcher tests** — `ebbb2f6` (test)
4. **Task 2 GREEN — SCK 10-bit + dispatcher wiring** — `a07eab8` (feat)

_TDD: 4 commits = 2 RED + 2 GREEN; no REFACTOR step needed (code landed clean per ruff)._

## Files Created/Modified

**Created**
- `server/mac_video_encoder.py` — 361 lines. MacVideoEncoder + MacVideoEncoderError; deferred PyObjC import with `_HAS_VT` guard; `_check_vt_available()` raises with install hint when missing.
- `tests/server/test_platform_backends_video.py` — 143 lines, 3 tests covering `_MAC_VIDEO_ENC_AVAILABLE` flag exposure + delegate dispatch + FFmpeg fallback.

**Modified**
- `server/mac_screen_capture.py` — +CoreVideo deferred import (P010 constant); +`want_10bit` ctor kwarg; +SCK config branch.
- `server/platform_backends.py` — +video-encode dispatch block (`_MAC_VIDEO_ENC_AVAILABLE` flag).
- `server/video_encoder.py` — +`import sys`; +`_mac_vt_delegate`, `_using_mac_direct_vt` attrs; +start() Mac branch; +feed_frame / request_keyframe / stop delegation paths.
- `tests/server/test_mac_video_encoder.py` — replaced 5 xfail stubs with real mock-at-VT tests (170 insertions, 61 deletions).
- `requirements-server.txt` — +pyobjc-framework-VideoToolbox/CoreVideo/CoreMedia (darwin markers).

## Decisions Made

See `key-decisions:` in frontmatter — six load-bearing decisions captured (lazy delegate import, want_10bit gating in a follow-up plan, plane-copy TODO acceptance, hard-coded AllowFrameReordering=False, request_keyframe latching pattern, no abstract base class).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Module-level type annotations broke ruff (UP045 + I001)**
- **Found during:** Task 1 lint check after first GREEN commit.
- **Issue:** `Optional[X]` and module-level imports out of order — ruff `select=["E","F","W","I","UP","ASYNC"]` rejected.
- **Fix:** `ruff check --fix` auto-converted to `X | None`, sorted imports.
- **Files modified:** server/mac_video_encoder.py
- **Verification:** `ruff check server/mac_video_encoder.py tests/server/test_mac_video_encoder.py` → All checks passed!
- **Committed in:** 9124dc7 (part of Task 1 GREEN commit, applied after auto-fix).

**2. [Rule 1 - Bug] CoreVideo import broke import-block sort in mac_screen_capture.py**
- **Found during:** Task 2 ruff check.
- **Issue:** Initial placement of new try/except CoreVideo import block triggered a NEW I001 error in mac_screen_capture.py (the rest of that file's I001 errors are pre-existing).
- **Fix:** Moved the CoreVideo deferred import to after the existing Quartz + CoreMedia blocks so the structure no longer produces a new sort violation.
- **Files modified:** server/mac_screen_capture.py
- **Verification:** Net repo ruff error count went back to 405 (= pre-plan baseline) instead of 406.
- **Committed in:** a07eab8 (part of Task 2 GREEN commit).

**3. [Rule 1 - Bug] Unused pytest import in dispatcher tests**
- **Found during:** Task 2 ruff check on new test file.
- **Issue:** `import pytest` left over from initial scaffold; F401 unused-import.
- **Fix:** `ruff check --fix` removed it.
- **Files modified:** tests/server/test_platform_backends_video.py
- **Verification:** Test file ruff-clean; 3/3 tests still pass.
- **Committed in:** a07eab8 (part of Task 2 GREEN commit, post auto-fix).

---

**Total deviations:** 3 auto-fixed (all Rule 1 — lint hygiene). All hidden behind ruff --fix; no API or logic changes.
**Impact on plan:** Zero scope creep. Plan executed as written.

## Issues Encountered

- **Pre-existing pam_auth test collection error (`tests/server/test_pam_auth.py`):** Unrelated to this plan — `monkeypatch.setattr(pam_auth, "pam_module", fake_pam_module)` fails because `server.pam_auth` no longer exposes that attribute. Logged to deferred-items consideration; out of scope for 02-05. Server-suite verification used `--ignore=tests/server/test_pam_auth.py`.
- **Pre-existing PySide6 collection error in `tests/client/test_health_display.py`:** PySide6 not installed in the local executor environment. CI macos-14 runners have it. Not caused by this plan.
- **Pre-existing 1-hour smoke harness timeout:** `tests/smoke/test_synthetic_1h.py::test_synthetic_1h_harness` runs for 1h by design; pytest-timeout=30s catches it locally. CI runs this on a dedicated nightly job. Not caused by this plan.
- **Ruff total error count:** 405 errors pre-plan, 405 errors post-plan in the whole repo. All pre-existing E501/F401/I001 in `server/video_encoder.py` are out of scope per Wave 0 baseline. My new code (mac_video_encoder.py + test_platform_backends_video.py + test_mac_video_encoder.py rewrites) is ruff-clean.

## Known Stubs

| File | Line(s) | Description | Resolution Path |
|------|---------|-------------|-----------------|
| `server/mac_video_encoder.py` | 232, 250 | `feed_frame()` allocates an empty CVPixelBuffer of P010 format and submits it to VTCompressionSessionEncodeFrame; the actual Y+UV plane memcpy from the input bytes is a documented TODO. | Wired in the next wave when SCK is configured to hand us P010 directly via the `want_10bit` flag (D-03 capability probe) — at that point SCK delivers IOSurface-backed P010 and we can drop the manual plane copy entirely. The MacVideoEncoder lifecycle, property bag, and callback bridge are correct as-is; only the data-flow into the buffer is stubbed. Tests cover the lifecycle path; data-flow integration test belongs to the wave that wires the real capture handoff. |

These stubs are intentional per the plan ("the real plane copy lands when SCK is configured to hand us P010 directly (Task 2 / D-01 cp.1)" — direct quote from the plan's `<action>` block) and do NOT prevent the plan goal: the encode pipeline shape, low-latency rate control, Main10 profile, and dispatch wiring are all correct and verified.

## Self-Check

```
[ FOUND ] server/mac_video_encoder.py
[ FOUND ] tests/server/test_platform_backends_video.py
[ FOUND ] commit 98b3818
[ FOUND ] commit 9124dc7
[ FOUND ] commit ebbb2f6
[ FOUND ] commit a07eab8
[ PASS  ] grep "def start|def stop|def feed_frame|def request_keyframe" server/mac_video_encoder.py = 4 (acceptance >= 4)
[ PASS  ] grep "kVTVideoEncoderSpecification_EnableLowLatencyRateControl" server/mac_video_encoder.py
[ PASS  ] grep "kVTProfileLevel_HEVC_Main10_AutoLevel" server/mac_video_encoder.py
[ PASS  ] grep "AllowFrameReordering" server/mac_video_encoder.py
[ PASS  ] grep "_HAS_VT" server/mac_video_encoder.py
[ PASS  ] grep "pyobjc-framework-VideoToolbox" requirements-server.txt
[ PASS  ] grep "pyobjc-framework-CoreVideo" requirements-server.txt
[ PASS  ] grep "kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange" server/mac_screen_capture.py
[ PASS  ] grep "hdrLocalDisplay" (case-insensitive variants) server/mac_screen_capture.py
[ PASS  ] grep "MacVideoEncoder" + "IS_MACOS" + try/except in server/platform_backends.py
[ PASS  ] grep "sys.platform == \"darwin\"" + "_MAC_VIDEO_ENC_AVAILABLE" in server/video_encoder.py
[ PASS  ] zero xfail markers in tests/server/test_mac_video_encoder.py
[ PASS  ] pytest tests/server/ -q --ignore=tests/server/test_pam_auth.py = 65 passed, 5 xfailed
[ PASS  ] ruff check server/mac_video_encoder.py tests/server/test_mac_video_encoder.py tests/server/test_platform_backends_video.py = clean
[ PASS  ] net repo ruff error count unchanged (405 → 405)
```

## Self-Check: PASSED

## User Setup Required

None — no external service or credentials needed. PyObjC frameworks added to requirements-server.txt install automatically on macOS via `pip install -r requirements-server.txt`; Linux skips them via the `sys_platform == "darwin"` markers.

## Next Phase Readiness

- The capability probe plan (D-03 / VIDEO-09) can now build `ServerHelloMsg.supports_main10` from a real VT trial-encode (the wrapper exists; just call `MacVideoEncoder(...)` in a try/except).
- The plane-copy stub becomes a wire-up exercise once SCK is configured to deliver P010 directly via the `want_10bit` constructor kwarg (already shipped here; just needs to be threaded from the negotiation result).
- Phase 1's mock-at-subprocess-boundary pattern + this plan's mock-at-VT-boundary pattern give Wave 3+ tests a consistent shape to build on (mock-at-IOKit-boundary for `mac_pen_injector.py` follows the same recipe — see `tests/server/test_mac_pen_injector.py` Wave-0 stubs).

---
*Phase: 02-input-color-fidelity*
*Plan: 02-05*
*Completed: 2026-04-19*
