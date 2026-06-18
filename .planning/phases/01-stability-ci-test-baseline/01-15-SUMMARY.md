---
phase: 01-stability-ci-test-baseline
plan: 15
subsystem: observability

tags: [obs-05, t-1-05, diagnostic-bundle, redaction, zipfile, cli-hook]

# Dependency graph
requires:
  - phase: 01-stability-ci-test-baseline
    provides: "common.logging redaction processor (Plan 01-06), server/main.py argparse (Plan 01-03 / 01-11), client/app.py argparse (Plan 01-12)"
provides:
  - "common/diagnostic_bundle.py — build_bundle(out_path, tier, config_files, log_files, live_state) writes teraguchi-diag-<ts>.zip"
  - "--diag-bundle [PATH] CLI flag on both server/main.py and client/app.py"
  - "T-1-05 regression guard (test_no_secret_strings_leak) — grep-greps every zip member for plaintext secrets"
  - "Redaction primitives: _redact_dict (recursive key-substring), _redact_text (TOML/JSON/free-form), _SECRET_ENV_RE (env-var name matching), _REFUSE_FILE_PATTERNS (whole-file refuse for *.key / *.pem / *.crt / bookmarks.json / users.json)"
affects: [phase-06-distribution-release, oss-polish-bug-report-template]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "zipfile.ZipFile ZIP_DEFLATED for bundle container (stdlib)"
    - "subprocess probe helper that never raises — [not-available:<bin>] sentinel"
    - "Whole-file refuse pattern for sensitive extensions (*.key / *.pem / *.crt / bookmarks.json / users.json)"
    - "Defense-in-depth redaction: every bundled log + config runs through _redact_text before zip write, even if upstream already redacted"
    - "Deferred import pattern — PySide6 imports moved into main() so the module loads without PySide6 installed"

key-files:
  created:
    - "common/diagnostic_bundle.py — bundle builder + redaction primitives (369 LOC)"
    - "tests/integration/test_diag_bundle.py — 13 tests including T-1-05 regression guard (312 LOC)"
  modified:
    - "server/main.py — +--diag-bundle flag + short-circuit (runs before logging / PAM / network setup)"
    - "client/app.py — +--diag-bundle flag + deferred PySide6 imports so module loads without PySide6"

key-decisions:
  - "Whole-file refuse (vs. partial redaction) for *.key / *.pem / *.crt / bookmarks.json / users.json. Even XOR-encrypted bookmark passwords leak under known-plaintext; refusing the entire file is the only defensible posture."
  - "_JSON_REDACT_KEYS aligned with common/logging.REDACT_KEYS ({'password','credential','token','secret','key','pin'}) — one threat surface, one substring list."
  - "CLI short-circuit runs BEFORE logging config AND BEFORE PAM/root guard — bundle export stays available on a broken install (the exact scenario a user files a bug report from)."
  - "Defer PySide6 imports into client/app.py::main() rather than top-level. Rule 2 auto-fix: the module needs to load in server-only CI runners so the --diag-bundle CLI smoke test can import and call main() without PySide6."
  - "stdlib-only: zipfile / subprocess / json / pathlib / re. Adding a new runtime dep for a diagnostic tool is unjustifiable."

patterns-established:
  - "Short-circuit-before-boot for diagnostic CLIs — any flag that must work on a broken install goes before logging/auth/network setup."
  - "Deferred import for optional-dep UI modules — keeps CLI smoke-testable without the UI dep installed."

requirements-completed: [OBS-05]
threats-addressed: [T-1-05]

# Metrics
duration: 24min
completed: 2026-04-18
---

# Phase 01 Plan 15: OBS-05 Diagnostic Bundle + T-1-05 Regression Guard Summary

**Single-module diagnostic-bundle exporter (`common/diagnostic_bundle.py`, 369 LOC, stdlib-only) producing a `teraguchi-diag-<ts>.zip` with manifest/config/logs/state/system/protocol sections, plus `--diag-bundle [PATH]` CLI flags on server and client that short-circuit before service boot. T-1-05 regression guard (`test_no_secret_strings_leak`) plants secrets in env + TOML + live-state and greps every zip member for plaintext — one leak fails CI.**

## Performance

- **Duration:** ~24 min
- **Started:** 2026-04-18
- **Completed:** 2026-04-18
- **Tasks:** 1 (TDD — RED commit then GREEN commit)
- **Files created:** 2 (`common/diagnostic_bundle.py`, `tests/integration/test_diag_bundle.py`)
- **Files modified:** 2 (`server/main.py`, `client/app.py`)
- **Typical bundle size (empty env, macOS M4 Max):** 2.8 KB with 11 members

## Accomplishments

- `common/diagnostic_bundle.py::build_bundle()` writes a RESEARCH-locked zip layout: `manifest.json` + `config/` + `logs/` + `state/` + `system/` + optional `protocol/`
- Redaction primitives exported for reuse and test coverage:
  - `_redact_dict(data)` — recursive key-substring match (case-insensitive) against `{password, credential, token, secret, key, pin}`, mirrors `common.logging._redact_value`
  - `_redact_text(text)` — two-pattern regex pass covering TOML/INI `key = value`, `key: value`, `key="quoted"`, and JSON `"key": "value"` shapes
  - `_SECRET_ENV_RE` — case-insensitive regex matching env var names ending in `_PASSWORD|_SECRET|_KEY|_TOKEN|_PIN`
  - `_REFUSE_FILE_PATTERNS` — whole-file refuse for `*.key`, `*.pem`, `*.crt`, `bookmarks.json`, `users.json` (sentinel text substituted instead of file contents)
- `server/main.py --diag-bundle [PATH]` short-circuits before logging config, PAM/root guard, and network setup — bundle export stays available on a misconfigured install
- `client/app.py --diag-bundle [PATH]` short-circuits before `QApplication(sys.argv)`. PySide6 imports deferred into `main()` so the module loads without PySide6 installed (enables server-only CI runners to import `client.app`)
- Subprocess probes (`pip freeze`, `ffmpeg -version`, `nvidia-smi` on Linux, `system_profiler SPDisplaysDataType` on macOS, `uname -a`, `uptime`) never raise — missing binaries return `[not-available:<bin>]` sentinel; all 6 probes succeeded on the runner (macOS M4 Max)

## T-1-05 Mitigation (threat register)

`test_no_secret_strings_leak` plants 4 different secrets:

1. `hunter2-very-secret` in `TERAGUCHI_USER_PASSWORD` env + TOML `password = "..."` + live-state `password_hash`
2. `broker-hmac-deadbeef` in `TERAGUCHI_BROKER_SECRET` env + TOML `broker_secret = "..."` + live-state `token`
3. `tls-pk-abc123xyz` in `TERAGUCHI_TLS_KEY` env + TOML `tls_key = "..."` + nested `credentials.api_token`
4. `BOOKMARK-PW-SUPERSECRET` in live-state `password_encrypted`

Then iterates every file in the output zip and asserts none of the 4 plaintext strings appear. If any pathway ever bypasses redaction, the test fails with a pointer to the leaking filename.

## Tests Added

```
tests/integration/test_diag_bundle.py
├── test_build_bundle_writes_zip                                  — smoke
├── test_bundle_contains_expected_sections                        — RESEARCH layout
├── test_bundle_tier_in_manifest                                  — manifest contract
├── test_bundle_default_path_uses_home                            — ~/teraguchi-diag-<ts>.zip
├── test_redact_dict_handles_nested                               — recursive redaction
├── test_redact_dict_redacts_whole_value_when_outer_key_matches   — whole-value redaction
├── test_redact_text_replaces_inline_values                       — key=value pattern
├── test_redact_text_handles_toml_quoted_strings                  — key = "quoted" pattern
├── test_no_secret_strings_leak                                   — T-1-05 REGRESSION GUARD
├── test_bundle_redacts_credentials_in_logs                       — defense-in-depth scrub
├── test_bundle_excludes_sensitive_files_if_passed                — *.key / bookmarks.json refuse
├── test_server_main_accepts_diag_bundle_flag                     — CLI short-circuit
└── test_client_main_accepts_diag_bundle_flag                     — CLI short-circuit
```

**Total:** 13 tests, all passing. Full-suite regression: 225 passed (pam_auth pre-existing failures unchanged).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 — Missing critical functionality] Deferred PySide6 imports in `client/app.py`**

- **Found during:** Task 1 GREEN run (test_client_main_accepts_diag_bundle_flag)
- **Issue:** `client/app.py` imported `from PySide6.QtCore import Qt` at module top. The CLI smoke test on a runner without PySide6 installed couldn't even import `client.app`, so `--diag-bundle` was effectively unreachable from server-only installs.
- **Fix:** Moved `from PySide6.QtCore import Qt` and `from PySide6.QtWidgets import QApplication` (and `from client.main_window import MainWindow`, `from client import theme`) into `main()` after the diag-bundle short-circuit. Exposed `QApplication` as a module-level attribute so test monkeypatches still work.
- **Rationale:** Bundle export was supposed to work even on a broken install — that includes a Python environment without the full Qt stack. Hard-blocking the CLI on a UI dep is a bug.
- **Files modified:** `client/app.py`
- **Commit:** `628e228`

**2. [Rule 1 — Bug] Regex alternation missing bare `key`**

- **Found during:** Task 1 GREEN (test_redact_text_replaces_inline_values)
- **Issue:** The text-redaction regex listed `tls_key|private_key` but not the bare substring `key`. `api_key = "abc123"` slipped through; `abc123` appeared in the redacted output.
- **Fix:** Alternation updated to `password|secret|token|key|credential|pin` (matching the `_JSON_REDACT_KEYS` substring list verbatim). Now one threat surface, one substring list across text and dict paths.
- **Files modified:** `common/diagnostic_bundle.py`
- **Commit:** `628e228`

**3. [Rule 1 — Test bug] Ambiguous test assertion on matching outer key**

- **Found during:** Task 1 GREEN (test_redact_dict_handles_nested)
- **Issue:** Test wrapped a list under key `tokens`, which matches the `token` substring — so the whole list becomes `"[REDACTED]"` (correct behaviour). But the test then tried to index `redacted["tokens"][0]["token"]`, which hit a string-subscript TypeError.
- **Fix:** Renamed outer key `tokens` → `items` (non-matching) and added a separate test `test_redact_dict_redacts_whole_value_when_outer_key_matches` documenting the correct whole-value-redaction behaviour.
- **Files modified:** `tests/integration/test_diag_bundle.py`
- **Commit:** `628e228`

### Authentication Gates

None.

## Self-Check: PASSED

- `common/diagnostic_bundle.py` exists (369 LOC ≥ 120 required) ✓
- `grep -q "def build_bundle" common/diagnostic_bundle.py` passes ✓
- `grep -q "_redact_dict" common/diagnostic_bundle.py` passes ✓
- `grep -q "_redact_text" common/diagnostic_bundle.py` passes ✓
- `grep -q "_SECRET_ENV_RE" common/diagnostic_bundle.py` passes ✓
- `grep -q "diag_bundle" server/main.py` passes ✓
- `grep -q "diag_bundle" client/app.py` passes ✓
- `tests/integration/test_diag_bundle.py` exists with `def test_no_secret_strings_leak` ✓
- 13/13 bundle tests pass ✓
- Full-suite regression: 225 passed, 4 pre-existing pam_auth failures unchanged ✓
- Commit `4c5511c` (RED) and `628e228` (GREEN) on `worktree-agent-aca67115` ✓

## Decisions Made

- **Whole-file refuse over partial redaction for cryptographic material.** Even encrypted bookmark passwords leak under known-plaintext attacks. A `[REDACTED: ...]` sentinel replaces the entire file.
- **Shared redaction substring list with `common/logging`.** `_JSON_REDACT_KEYS` mirrors `common.logging.REDACT_KEYS` exactly so the log + diag pipelines have one threat surface to audit.
- **Short-circuit before logging + auth + network.** `--diag-bundle` users are typically filing bug reports — their install is by definition broken. The CLI path must work when nothing else does.
- **stdlib-only implementation.** Adding a runtime dep for a diagnostic tool would be absurd. `zipfile` + `subprocess` + `json` + `pathlib` + `re` cover 100% of the need.
- **Deferred Qt import in `client/app.py`.** The CLI path cannot require the UI stack to be installed. This also has the nice side effect that `python3 -c "import client.app"` works on headless CI without installing `PySide6>=6.10`.

## Known Stubs

None.

## Threat Flags

None — plan scope is threat mitigation (T-1-05) rather than new surface introduction. Redaction covers dict, list, tuple, text, JSON-in-string, and env-var cases.
