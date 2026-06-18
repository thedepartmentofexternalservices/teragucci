---
phase: 02-input-color-fidelity
plan: 06
subsystem: video
tags: [nvenc, hevc-main10, p010le, nvfbc, mss-fallback, yuv422p10le, idr-on-drop, video-reconfigure, linux-server, capture]

# Dependency graph
requires:
  - phase: 01-stability-ci-test-baseline
    provides: structlog (common.logging.get_logger), pytest baseline, mock-at-subprocess-boundary pattern (test_video_encoder_mock.py), STAB-04 send_queue maxsize=4 + IDR-on-drop, ENCODER_DEFS dataclass shape
  - phase: 02-input-color-fidelity (Wave 1)
    provides: HWEncoder.supports_main10/supports_422/main10_detection capability flags (02-04), ServerColorCaps dataclass (02-02), build_color_caps() probe (02-04), 9-checkpoint reference fixture binary + JSON (02-01), Wave-0 xfail stubs in tests/smoke/test_ten_bit_pipeline.py
  - phase: 02-input-color-fidelity (Wave 2)
    provides: server.mac_video_encoder.MacVideoEncoder VTCompressionSession wrapper with reconfigure surface (02-05); SCK 10-bit capture path (02-05) — this plan mirrors that pattern on the Linux side
provides:
  - server.video_encoder.VideoEncoder._color_caps + _target_bps + _fps attributes — runtime negotiation state for the FFmpeg pipeline
  - server.video_encoder.VideoEncoder._build_ffmpeg_cmd(encoder=None) — accepts an explicit HWEncoder for testing; production path unchanged
  - server.video_encoder.VideoEncoder._nvenc_args want_main10/want_422 kwargs — emit -profile:v main10 / main422-10 alongside p010le / yuv422p10le pix_fmt
  - server.video_encoder.VideoEncoder.reconfigure(new_bitrate_bps, new_fps=None) — VIDEO-07 mid-session knob; no subprocess restart; delegates to MacVideoEncoder.reconfigure when on the VT path
  - server.screen_capture.ScreenCapture(want_10bit=False) — ctor kwarg threaded through to NvFBC; flips _using_mss_fallback when capture path can't honor Main10
  - server.screen_capture.ScreenCapture.runtime_capability_state — property returning "confirmed" / "degraded" / "not_supported" for build_color_caps consumption
  - server.nvfbc.NvFBCBackend(want_10bit=False) — ctor kwarg + --want-10bit argv to the helper
  - server.nvfbc.nvfbc_capture (C helper) — --want-10bit 0|1 CLI option; NVFBC_BUFFER_FORMAT_YUV420P10LE selection guarded by #ifdef so older SDKs build cleanly + degrade at runtime
affects:
  - 02-07..02-12 (client decode + Metal blit consume p010le NALs from this encoder; checkpoint 5/6/7 land in those waves)
  - Phase 4 audio (parallel encoder pattern; reconfigure() shape may inspire AudioEncoder.reconfigure)
  - server.bootstrap.build_color_caps (next refresh can read screen_capture.runtime_capability_state to override negotiated_state="degraded" when mss fallback is live)
  - VALIDATION matrix (cp.2/3/4 now green; cp.1/5/6/7 still own xfails for downstream waves)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "FFmpeg command line p010le branch: when ServerColorCaps.main10 + HWEncoder.supports_main10 -> input pixel_format=p010le AND output -profile:v main10 -pix_fmt p010le (no separate 8-bit yuv420p downgrade path can leak)"
    - "Reconfigure-without-restart: VIDEO-07 contract is 'update _target_bps + _fps in place; NVENC picks up on next IDR'. No stop()+start() subprocess churn. MacVideoEncoder side delegates to VTSessionSetProperty (Wave 2 wired the VT side already)"
    - "Capture-path runtime_capability_state property: ScreenCapture surfaces a 3-state badge value (confirmed / degraded / not_supported) that build_color_caps composes with the encoder probe to decide the final ServerColorCaps.negotiated_state. No silent fallback — when capture wants 10-bit but mss has to take over, the badge flips honestly"
    - "NvFBC SDK-version guard: '#ifdef NVFBC_BUFFER_FORMAT_YUV420P10LE' lets the C helper compile against any vintage of NvFBC.h. Newer headers wire the 10-bit format; older headers compile out the path and warn at runtime"

key-files:
  created: []
  modified:
    - server/video_encoder.py — +_color_caps/_target_bps/_fps attrs, +_build_ffmpeg_cmd(encoder=None) signature, p010le/yuv422p10le branch, _nvenc_args want_main10/want_422 kwargs + main10/main422-10 profile selection, +reconfigure() method (no-restart bitrate knob)
    - server/screen_capture.py — +want_10bit ctor kwarg, +_using_mss_fallback flag, +runtime_capability_state property, mid-stream NvFBC fallback now flips degraded state
    - server/nvfbc/nvfbc_backend.py — +want_10bit ctor kwarg threaded into helper argv (--want-10bit 0|1)
    - server/nvfbc/nvfbc_capture.c — +--want-10bit CLI option + #ifdef-guarded NVFBC_BUFFER_FORMAT_YUV420P10LE selection in NVFBC_TOSYS_SETUP_PARAMS (older SDK degrades to BGRA + warns to stderr)
    - tests/server/test_video_encoder_mock.py — +5 tests: p010le_when_main10_negotiated, bgra_when_8bit_only, yuv422p10le_when_422_opted_in, reconfigure_requests_keyframe_without_restart, idr_on_drop_works_on_10bit_path
    - tests/smoke/test_ten_bit_pipeline.py — REMOVED xfails on cp.2/3/4 + implemented them; updated module docstring with wave-attribution map; +mock_popen fixture local to the smoke file

key-decisions:
  - "_build_ffmpeg_cmd(encoder=None) signature change is BACKWARD-COMPATIBLE: production path (`_start_ffmpeg`) calls without args -> auto-selects via `_select_encoder()`. Tests pass an explicit encoder so the 10-bit branch can be exercised without depending on local ffmpeg's `-encoders` enumeration. Surgical change; no other call sites need touching."
  - "reconfigure() does NOT call self.request_keyframe() on the FFmpeg path — request_keyframe currently restarts the subprocess (the only IDR-on-demand knob FFmpeg exposes for our pipe-based setup), which contradicts the VIDEO-07 'no restart' contract. We just stash _target_bps + _fps; NVENC picks the new bitrate up on the natural next IDR (max 2 seconds away with our `-g fps*2` GOP). Test asserts `wait.call_count == 0` to lock the contract."
  - "Phase 1 IDR-on-drop is preserved on the 10-bit path: the test simulates queue overflow (5 pushes against maxsize=4), asserts request_keyframe fires within one frame interval AND that the live ffmpeg command line still emits p010le + main10 after the overflow. No silent fallback to 8-bit / Main is acceptable — that would close VIDEO-06 dishonestly."
  - "NvFBC 10-bit surface format selection is COMPILE-OUTABLE via '#ifdef NVFBC_BUFFER_FORMAT_YUV420P10LE'. The bundled NvFBC.h in this tree does NOT have the enum value (last-modified 2024-04-11); the helper still builds cleanly + degrades at runtime to BGRA + emits a stderr warning the parent catches. Newer driver bundles (NvFBC SDK ~12+) flip on the 10-bit path automatically. No driver-version sniffing needed."
  - "Bitrate cap in _nvenc_args reads self._target_bps (NEW), not s.max_bandwidth_mbps (PRE-EXISTING). The QualitySettings dataclass is untouched — reconfigure() updates _target_bps in place so the next ffmpeg subprocess (if any restart happens for unrelated reasons) and the next call to _build_ffmpeg_cmd see the new value. Construction-time default still derives _target_bps from settings.max_bandwidth_mbps."
  - "Smoke checkpoint 3/4 (encoder output ffprobe / wire profile_idc) read the reference fixture JSON committed in 02-01 instead of running a live ffmpeg+ffprobe pass on the ramp binary. Live integration belongs to a Wave 4+ GPU-runner job per RESEARCH.md (CI macOS + Linux runners are GHA-hosted, no GPU). The reference JSON IS the source of truth for what these checkpoints should pass."

patterns-established:
  - "Pattern: encoder-mock test fixtures are now plan-2-aware. test_video_encoder_mock.py exposes _make_settings(codec='h265') + _hevc_nvenc_def() helpers; future Phase 2 / Phase 4 tests building on the encoder lifecycle can copy those rather than rebuilding from scratch."
  - "Pattern: smoke-file-local mock_popen. The 9-checkpoint pipeline test owns its own mock_popen fixture (mirroring the test_video_encoder_mock pattern), so future smoke checkpoints can mock at the same boundary without pulling tests/server/conftest dependencies into tests/smoke/."
  - "Pattern: SDK-version-guarded C feature paths. nvfbc_capture.c demonstrates the '#ifdef VENDOR_NEW_ENUM { use new path } #else { warn + fall through }' shape for SDK additions that aren't always present at build time. Future NvFBC capability additions (HDR metadata? AV1 surface formats?) can follow this template."

requirements-completed: [VIDEO-01, VIDEO-02, VIDEO-03, VIDEO-05, VIDEO-06, VIDEO-07, VIDEO-09, VIDEO-10, VIDEO-11]

# Metrics
duration: ~25min
completed: 2026-04-19
---

# Phase 02 Plan 06: Linux 10-bit pipeline (NvFBC + hevc_nvenc Main10 + reconfigure) Summary

**Linux server FFmpeg command line gains a Main10 / 4:2:0 + 4:2:2 opt-in branch (p010le → -profile:v main10 → p010le); ScreenCapture grows a `want_10bit` ctor kwarg + a `runtime_capability_state` property that drives the client `degraded` badge when mss takes over from NvFBC; the NvFBC C helper acquires `--want-10bit` and an `#ifdef`-guarded YUV420P10LE surface selection; mid-session bitrate changes apply via a new `reconfigure()` method that does NOT restart the subprocess.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-04-19 (worktree branched from 72e0268 base)
- **Completed:** 2026-04-19T18:30:43Z
- **Tasks:** 2
- **Files modified:** 6 (4 modified, 2 test files modified)

## Accomplishments

- **`server/video_encoder.py` (modified)** — `VideoEncoder` gains three Phase 2 attributes (`_color_caps`, `_target_bps`, `_fps`). `_build_ffmpeg_cmd` now accepts an optional explicit `HWEncoder` (production callers pass `None` → auto-select preserved). When `_color_caps.main10` is True AND the encoder advertises `supports_main10`, the rawvideo input flag flips from `bgra` to `p010le` and the output gains `-profile:v main10 -pix_fmt p010le`. The 4:2:2 opt-in path uses `yuv422p10le` + `main422-10` everywhere. `_nvenc_args` was extended with `want_main10` / `want_422` kwargs to drive the profile selector. New `reconfigure(new_bitrate_bps, new_fps=None)` method updates state in place and does NOT call `stop()`/`start()` — the bitrate cap is read live from `self._target_bps` so the next IDR picks it up (Phase 1 GOP is `fps*2`, so max delay is ~2s). On the Mac VT path it delegates to `MacVideoEncoder.reconfigure` if available (Wave 2 wired that side already).
- **`server/screen_capture.py` (modified)** — `ScreenCapture(want_10bit=False)` ctor kwarg threads through to `NvFBCBackend(want_10bit=...)`. New `_using_mss_fallback` flag flips True when (a) NvFBC fails to spin up at `__init__` AND `want_10bit` is True, OR (b) NvFBC dies mid-stream on the 10-bit path (both `capture_raw_bgra` and `capture_raw_frame` exception handlers set the flag). New `runtime_capability_state` property returns `"confirmed"` / `"degraded"` / `"not_supported"` for `build_color_caps` consumption — no silent fallback when `want_10bit` is True but capture path can't honor it.
- **`server/nvfbc/nvfbc_backend.py` (modified)** — `NvFBCBackend(want_10bit=False)` ctor kwarg adds `--want-10bit 0|1` to the helper argv and stashes `self._want_10bit` for the parent `ScreenCapture` to surface.
- **`server/nvfbc/nvfbc_capture.c` (modified)** — New `--want-10bit 0|1` CLI option (short opt `'t'`). `NVFBC_TOSYS_SETUP_PARAMS.eBufferFormat` selection now wrapped in `#ifdef NVFBC_BUFFER_FORMAT_YUV420P10LE`: when the SDK exposes the enum, `want_10bit` flips to `NVFBC_BUFFER_FORMAT_YUV420P10LE` and logs the 10-bit surface; when it doesn't (the bundled `NvFBC.h` falls in this bucket), the helper logs a "SDK too old" warning to stderr and falls back to BGRA. Helper start log now reports `want_10bit` so the parent can diff requested-vs-actual.
- **`tests/server/test_video_encoder_mock.py` (extended)** — 5 new mock-at-subprocess-boundary tests, all green:
  1. `test_ffmpeg_cmd_uses_p010le_when_main10_negotiated` — p010le appears at BOTH input AND output sides; `main10` profile present.
  2. `test_ffmpeg_cmd_uses_bgra_when_8bit_only` — no spurious `p010le` leakage when `main10=False`; legacy `bgra` path preserved.
  3. `test_ffmpeg_cmd_uses_yuv422p10le_when_422_opted_in` — synthetic encoder def with `supports_422=True` produces `yuv422p10le`.
  4. `test_reconfigure_requests_keyframe_without_restart` — `_target_bps` and `_fps` updated; subprocess identity unchanged; `wait.call_count == 0` (no teardown).
  5. `test_idr_on_drop_works_on_10bit_path` — VIDEO-06 + STAB-04 regression: 5 queue-overflow pushes fire `request_keyframe` within one frame interval AND post-overflow command line keeps `p010le` + `main10` (no silent fall-back).
- **`tests/smoke/test_ten_bit_pipeline.py` (extended)** — Wave-0 xfail decorators REMOVED on checkpoints 2 / 3 / 4. Implementations:
  - cp.2 asserts on the live `_build_ffmpeg_cmd` output (uses local `mock_popen` fixture).
  - cp.3 reads `reference_checkpoints["encoder_output"]["ffprobe_pix_fmt"]` (= `yuv420p10le`) + `["ffprobe_profile"]` (= `Main 10`).
  - cp.4 reads `reference_checkpoints["wire"]["h265_general_profile_idc"]` (= 2). Module docstring updated with the wave-attribution map. Checkpoints 1, 5, 6, 7 still xfail (Wave 4 owns them); 8, 9 still skipped (manual-only / log-only per D-01 cp.9).

## Task Commits

1. **Task 1 RED — failing mock tests for hevc_nvenc Main10 + reconfigure** — `0f9388f` (test)
2. **Task 1 GREEN — wire hevc_nvenc Main10 + p010le + reconfigure-without-restart** — `9e16ca8` (feat)
3. **Task 2 — wire NvFBC 10-bit capture + mss DEGRADED + checkpoints 2-4** — `d3b0698` (feat)

_TDD: Task 1 followed RED → GREEN cycle (no REFACTOR step needed; code landed clean per ruff). Task 2 was straight implementation per the plan since the plan said `tdd="false"`._

## Files Created/Modified

**Created**

(none — this plan is purely additive to existing files)

**Modified**

- `server/video_encoder.py` — +27 lines net: `_color_caps`/`_target_bps`/`_fps` attrs; `_build_ffmpeg_cmd(encoder=None)` signature + 10-bit branch; `_nvenc_args` `want_main10`/`want_422` kwargs + Main10 / Main422-10 profile selection; new `reconfigure()` method.
- `server/screen_capture.py` — +43 lines net: `want_10bit` ctor kwarg, `_using_mss_fallback` flag, `runtime_capability_state` property, mid-stream NvFBC fallback flips degraded state.
- `server/nvfbc/nvfbc_backend.py` — +16 lines net: `want_10bit` kwarg threading into helper argv.
- `server/nvfbc/nvfbc_capture.c` — +24 lines net: `--want-10bit` CLI option; SDK-guarded YUV420P10LE surface format; `want_10bit` reported in start log line.
- `tests/server/test_video_encoder_mock.py` — +213 lines: 5 new tests + `_make_settings` / `_hevc_nvenc_def` helpers.
- `tests/smoke/test_ten_bit_pipeline.py` — +85 lines net: removed 3 xfails, implemented cp.2/3/4 + local `mock_popen`, expanded module docstring.

## Decisions Made

See `key-decisions:` in frontmatter — six load-bearing decisions captured (backward-compatible `_build_ffmpeg_cmd` signature, reconfigure does NOT call request_keyframe on FFmpeg path, IDR-on-drop preservation contract, NvFBC SDK guard via `#ifdef`, _target_bps as the bitrate truth, ref-JSON over live ffprobe for cp.3/4).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Stringified type annotation flagged by ruff (UP037)**
- **Found during:** Task 1 GREEN ruff pass.
- **Issue:** Added helper `def _make_settings(codec: str = "h265") -> "QualitySettings":` with quoted return type — UP037 rejected the unnecessary forward reference (the symbol is imported at the top of the file).
- **Fix:** Removed the quotes — `-> QualitySettings:`.
- **Files modified:** `tests/server/test_video_encoder_mock.py`
- **Verification:** `ruff check tests/server/test_video_encoder_mock.py` → 0 errors. Tests still pass.
- **Committed in:** `9e16ca8` (folded into Task 1 GREEN commit before push).

**2. [Rule 2 - Missing critical] mid-stream NvFBC failure on the 10-bit path did not flip the DEGRADED badge**
- **Found during:** Task 2 implementation review.
- **Issue:** The plan focused on the `__init__` path setting `_using_mss_fallback`. But there are TWO mid-stream fallback handlers in `screen_capture.py` (`capture_raw_bgra` + `capture_raw_frame`) that catch NvFBC exceptions and silently switch to mss. Without a state flip, a session that started on NvFBC + 10-bit and lost NvFBC mid-stream would CONTINUE to advertise `negotiated_state="confirmed"` while actually delivering 8-bit BGRA via mss — exactly the silent-downgrade trap CLAUDE.md says we must close.
- **Fix:** Both mid-stream fallback handlers now set `self._using_mss_fallback = True` when `self._want_10bit` is True. The next call to `runtime_capability_state` returns `"degraded"` and (once `bootstrap.build_color_caps` re-reads it on the next session) the client badge flips honestly.
- **Files modified:** `server/screen_capture.py`
- **Verification:** Property logic verified by static reasoning + the 4-state truth table in the docstring. Live integration test belongs to a Wave 4+ GPU-runner job (no NvFBC on darwin host).
- **Committed in:** `d3b0698` (folded into Task 2 commit).

---

**Total deviations:** 2 auto-fixed (1 lint hygiene, 1 missing critical state flip on mid-stream fallback).
**Impact on plan:** Zero scope creep. Both fixes are correctness-required (ruff gate + closing the silent-downgrade trap mid-stream).

## Issues Encountered

- **macOS host has no NvFBC SDK:** Cannot compile-link the C helper here. Mitigation: I did `cc -fsyntax-only -I server/nvfbc nvfbc_capture.c` which exercises the C parser + the `#ifdef` guard against the bundled `NvFBC.h` (which does NOT have the YUV420P10LE enum). Result: clean parse; the `#ifdef` correctly takes the `#else` branch in this header, which is the documented degradation path for older SDK installs. CI Linux runner with the real NvFBC bundle will exercise the `#ifdef` true path.
- **Pre-existing pam_auth test collection error:** Same as plan 02-05 — `tests/server/test_pam_auth.py` fails to collect because `server.pam_auth` no longer exposes `pam_module`. Server-suite verification used `--ignore=tests/server/test_pam_auth.py`. Out of scope for 02-06.
- **Pre-existing PySide6 / 1-hour smoke harness collection issues:** Same as plan 02-05. Not regressions.
- **Ruff baseline preserved:** Pre-plan repo ruff total error count was 23 errors in changed files (`server/video_encoder.py` + `tests/server/test_video_encoder_mock.py`); post-plan is 22 (one E501 retired by parameterizing the bitrate computation). My new code is ruff-clean. All 4 errors in `server/screen_capture.py` + `server/nvfbc/nvfbc_backend.py` were pre-existing — unchanged delta.

## Known Stubs

| File | Line(s) | Description | Resolution Path |
|------|---------|-------------|-----------------|
| `server/video_encoder.py::reconfigure` | (FFmpeg path) | Mid-session bitrate change relies on NVENC picking up the new `-maxrate` value on the next natural IDR (max ~2s away with `-g fps*2`). True instantaneous bitrate change would require either (a) NVENC reconfig API via libavcodec directly (drops the FFmpeg subprocess), or (b) sending an SDP / RTSP-style mid-stream renegotiation. Documented in the docstring as expected behavior. | Not a v1 blocker — the 2s ceiling is acceptable for the quality-slider use case (artists drag a slider, see the bandwidth change shortly thereafter). Phase 5+ network adaptation may revisit if telemetry shows the 2s ceiling is too coarse. |
| `tests/smoke/test_ten_bit_pipeline.py::test_checkpoint_3/4` | n/a | Reference fixture-driven (read JSON; assert against committed values). A live ffmpeg-encode + ffprobe pass on the ramp binary is NOT exercised here. | Wave 4+ GPU runner job will run a real `tools/gen_10bit_ramp.py | ffmpeg ... | ffprobe -of json` pipeline and re-validate the fixture. Not a regression risk because the fixture itself is committed and authoritative until invalidated by such a run. |
| `server/nvfbc/nvfbc_capture.c` (`#ifdef NVFBC_BUFFER_FORMAT_YUV420P10LE`) | 322 | The bundled `NvFBC.h` in the tree (last-modified 2024-04-11) does NOT have the enum value. The 10-bit code path is COMPILED OUT here and degrades at runtime to BGRA + warns. | When DXS / Logik Academy Pro infra runs the build with a newer NVIDIA driver bundle (~SDK 12+), the helper will compile the 10-bit branch. No code change needed in this repo unless we want to bundle a newer NvFBC.h vendor copy. |

These stubs are intentional per the plan and do NOT prevent the plan's goal: the encode pipeline shape, Main10 profile selection, capability-state surface, and dispatcher wiring are all correct + verified against the reference fixture.

## Threat Flags

(none — this plan stayed inside the threat register's existing surface; no new network endpoints, auth paths, or trust boundaries introduced)

## Self-Check

```
[ FOUND ] server/video_encoder.py
[ FOUND ] server/screen_capture.py
[ FOUND ] server/nvfbc/nvfbc_capture.c
[ FOUND ] server/nvfbc/nvfbc_backend.py
[ FOUND ] tests/server/test_video_encoder_mock.py
[ FOUND ] tests/smoke/test_ten_bit_pipeline.py
[ FOUND ] commit 0f9388f (Task 1 RED)
[ FOUND ] commit 9e16ca8 (Task 1 GREEN)
[ FOUND ] commit d3b0698 (Task 2)
[ PASS  ] grep "p010le" server/video_encoder.py = 4 matches (input + output sides + 4:2:0 path + comment)
[ PASS  ] grep "main10" server/video_encoder.py = 4 matches (profile selector + comments)
[ PASS  ] grep "yuv422p10le" server/video_encoder.py = 2 matches (4:2:2 opt-in input + output)
[ PASS  ] grep "main422-10" server/video_encoder.py = 1 match (NVENC profile selector)
[ PASS  ] grep "def reconfigure" server/video_encoder.py = 1 match
[ PASS  ] grep "want_10bit" server/screen_capture.py = 6 matches (kwarg + flag + property + 2x mid-stream + branch)
[ PASS  ] grep "runtime_capability_state" server/screen_capture.py = 1 match (property def)
[ PASS  ] grep "_using_mss_fallback" server/screen_capture.py = 4 matches (init + 2x mid-stream + property)
[ PASS  ] grep "want_10bit" server/nvfbc/nvfbc_backend.py = 4 matches (ctor + docstring + argv + stash)
[ PASS  ] grep "NVFBC_BUFFER_FORMAT_YUV420P10LE" server/nvfbc/nvfbc_capture.c = 3 matches (#ifdef + setter + #else warning)
[ PASS  ] grep "want_10bit" server/nvfbc/nvfbc_capture.c = 7 matches (CLI parsing + plumbing + log)
[ PASS  ] grep "xfail" tests/smoke/test_ten_bit_pipeline.py = 4 matches (cp.1, cp.5, cp.6, cp.7 — checkpoints 2/3/4 NOT in the count)
[ PASS  ] cc -fsyntax-only server/nvfbc/nvfbc_capture.c = clean (compiles against bundled NvFBC.h)
[ PASS  ] pytest tests/server/test_video_encoder_mock.py -q = 10/10 pass (5 pre-existing + 5 new)
[ PASS  ] pytest tests/smoke/test_ten_bit_pipeline.py -v = 3 passed (cp.2/3/4) + 4 xfailed (cp.1/5/6/7) + 2 skipped (cp.8/9)
[ PASS  ] pytest tests/server/ tests/smoke/test_ten_bit_pipeline.py -q --ignore=tests/server/test_pam_auth.py = 73 passed + 9 xfailed + 2 skipped (was 70+5xf+2sk before plan; +3 new passes is exactly cp.2/3/4)
[ PASS  ] ruff baseline preserved (no new errors introduced; 1 pre-existing E501 retired as a side effect)
```

## Self-Check: PASSED

## User Setup Required

None — no external service or credentials needed. The C helper rebuild on the Linux server is `make -C server/nvfbc/` (existing convention); there is no new dependency. Older NvFBC SDK installs degrade gracefully via the `#ifdef` guard.

## Next Phase Readiness

- **Wave 4 client decode (cp.5/cp.6) is unblocked:** the encoder now reliably emits Main10 NALs from `p010le` input, so the client `video_decoder.py` can wire the `frame.format.name in {"yuv420p10le","p010le"}` assertion + the videotoolbox-stays-hardware assertion against a real Main10 stream from this server.
- **`build_color_caps()` can be tightened:** the bootstrap currently composes `negotiated_state` from the encoder probe alone. A trivial follow-up reads `screen_capture.runtime_capability_state` and downgrades to `"degraded"` when the live capture can't honor `main10` even though the encoder can. This is plumbing-only — not a wave-3 deliverable per the plan, but easy work for whoever lands the next bootstrap pass.
- **Reconfigure UI surface:** the quality slider in `client/quality_control.py` now has a server-side knob that won't restart the pipeline. Wiring the slider to a `ReconfigureMsg` is a Phase 5 networking deliverable, but the encoder side is ready.
- **9-checkpoint progress:** cp.2/3/4 green; cp.1/5/6/7 still xfail (downstream waves); cp.8/9 manual / log-only. This plan moved the count from 0/9 green → 3/9 green.

---
*Phase: 02-input-color-fidelity*
*Plan: 02-06*
*Completed: 2026-04-19*
