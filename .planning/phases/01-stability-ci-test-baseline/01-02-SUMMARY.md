---
phase: 01-stability-ci-test-baseline
plan: 02
subsystem: security
tags: [tls, ssl, cert-verification, double-gate, integration-tests, cryptography-x509, websockets, aiohttp, aioquic]

# Dependency graph
requires:
  - phase: 00-init
    provides: Working client, broker, and QUIC client-side TLS connection code paths (with CERT_NONE bugs to fix)
provides:
  - TLS certificate verification ON by default at all 4 client-side call sites (server WSS, broker WSS, QUIC client, aiohttp broker-probe)
  - Single double-gated escape hatch in `common/tls_opt_out.py` used consistently by every caller
  - ERROR-level log event `transport.insecure_mode_active` whenever the escape hatch is active
  - Real-CA integration test fixtures (trusted + untrusted) built via `cryptography.x509`
  - Grep-level regression guard test to prevent future CERT_NONE leaks
affects: [01-03 (pytest baseline), 01-04 (more integration tests), 01-05 (CI), 06 (phase 6 security hardening — SEC-02..04 will layer Tailscale / TOFU on top of this)]

# Tech tracking
tech-stack:
  added:
    - cryptography (test-only, for x509 CA fixture generation)
    - websockets (already in requirements; now exercised under TLS in tests)
  patterns:
    - "Double-gate security escape hatch: CLI flag AND env var required; single gate is intentionally insufficient"
    - "Shared `build_client_ssl_context()` factory used at every client-side TLS entry point — no more inline ssl.SSLContext construction in call sites"
    - "Real-CA test fixture via `cryptography.x509` + `SubjectKeyIdentifier` / `AuthorityKeyIdentifier` / `KeyUsage` / `ExtendedKeyUsage` extensions for OpenSSL strict verification on Python 3.14+"

key-files:
  created:
    - common/tls_opt_out.py (75 lines — double-gate helper + ssl context builder)
    - tests/integration/__init__.py (empty package marker)
    - tests/integration/conftest.py (164 lines — real CA + server-cert fixtures)
    - tests/integration/test_tls_verify.py (123 lines — 5 integration tests)
  modified:
    - client/protocol.py (SITE 1 _connect_to_server:291-296, SITE 2 _broker_handshake:360-365; + `_insecure_skip_verify` / `_ca_bundle` attrs on __init__; + `set_insecure_skip_verify()` setter)
    - common/quic_transport.py (SITE 3 connect:402-430 — verify_cert defaults True; CERT_REQUIRED default; CERT_NONE only behind double-gate)
    - broker/pool.py (SITE 4 _probe_one:148-157 — `ssl=False` → verified context; + `_insecure_skip_verify` / `_ca_bundle` attrs on MachinePool.__init__)

key-decisions:
  - "Dev escape hatch retained as DOUBLE-gate (CLI flag AND TERAGUCHI_ACCEPT_INSECURE=1) per RESEARCH Open Question #3 planner-lock. Single gate (flag only) was considered too easy to leak to production."
  - "Every insecure-mode activation emits an ERROR-level log event `transport.insecure_mode_active` with site label for operational visibility. Asserted by regression test."
  - "All client-side TLS contexts routed through one shared helper (`build_client_ssl_context`) rather than reproducing `ssl.create_default_context(...)` inline at each call site. Reduces future drift risk."
  - "QUIC escape-hatch signature reuses existing `verify_cert=True` parameter (flipped from False) rather than introducing a new parameter — minimises blast radius on existing callers."
  - "Test CAs built with full X.509 extension set (SKI/AKI/KeyUsage/ExtendedKeyUsage) so fixtures satisfy OpenSSL strict verification on Python 3.14+ (encountered during CI-style local validation)."

patterns-established:
  - "Pattern: `build_client_ssl_context(insecure_cli_flag, ca_bundle, site_label)` — single point of truth for every client-side TLS context in the codebase."
  - "Pattern: `site_label` string in every TLS-context construction call so insecure-mode log events can be traced to their origin (server_wss, broker_wss, quic, broker_probe)."
  - "Pattern: integration tests generate their own CA at test-time via `cryptography.x509`; no checked-in cert material; no `CERT_NONE` in test setup."

requirements-completed: [SEC-01]

# Metrics
duration: 6min
completed: 2026-04-18
---

# Phase 01 Plan 02: SEC-01 TLS Verification Hardening Summary

**Removed every unconditional `ssl.CERT_NONE` from the client, broker, and QUIC code paths and replaced them with stdlib-verified TLS contexts routed through a single shared helper; retained the dev escape hatch behind a CLI-flag + env-var double-gate with mandatory ERROR-level audit logging on every activation.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-04-18T23:47:24Z
- **Completed:** 2026-04-18T23:53:10Z
- **Tasks:** 2 / 2
- **Files modified:** 3 production files (client/protocol.py, common/quic_transport.py, broker/pool.py) + 4 new files (common/tls_opt_out.py, tests/integration/__init__.py, tests/integration/conftest.py, tests/integration/test_tls_verify.py)

## Accomplishments

- All four `ssl.CERT_NONE` / `ssl=False` sites listed in PITFALLS.md are now closed. Verification is ON by default end-to-end for every TLS connection the codebase initiates.
- A single shared `common/tls_opt_out.py::build_client_ssl_context()` is now the one place that constructs client-side TLS contexts — future regressions caused by inline context construction are prevented architecturally.
- The dev escape hatch is retained for engineers whose boxes lack a real cert, but it is DOUBLE-gated (both CLI flag and env var required) and every activation fires an ERROR-level `transport.insecure_mode_active` log event with a site label. A regression test asserts the log fires.
- 5 integration tests — including a grep-level regression guard — confirm real verification (trusted CA accepted, untrusted CA rejected with `ssl.SSLCertVerificationError`), double-gate semantics, ERROR log event, and no ungated `CERT_NONE` anywhere in `client/`, `common/`, or `broker/`.
- Threats mitigated: **T-1-01** (MITM via CERT_NONE — Spoofing) and **T-1-04** (escape-hatch hardening — no silent insecure mode).

## Task Commits

Each task committed atomically (worktree mode: `--no-verify` used to avoid parallel-executor hook contention):

1. **Task 1: Add TLS double-gate helper and CA test fixtures** — `7183eab` (feat)
2. **Task 2: Remove ssl.CERT_NONE from client, QUIC, and broker TLS paths** — `f4bae89` (fix)

## Files Created/Modified

### Created

- `common/tls_opt_out.py` — 75-line helper module. Two public functions:
  - `insecure_tls_allowed(cli_flag: bool) -> bool` — pure double-gate predicate. Returns True iff BOTH CLI flag True AND env `TERAGUCHI_ACCEPT_INSECURE=1`.
  - `build_client_ssl_context(insecure_cli_flag=False, ca_bundle=None, site_label="client") -> ssl.SSLContext` — stdlib `create_default_context(SERVER_AUTH)` with TLSv1.2 minimum. `check_hostname` and `verify_mode` stay ON unless the double-gate fires, in which case `logger.error("transport.insecure_mode_active site=...")` is emitted before downgrading.
- `tests/integration/__init__.py` — empty package marker.
- `tests/integration/conftest.py` — 164-line CA fixture module. Three fixtures:
  - `tls_ca_and_cert` — trusted CA + server cert for 127.0.0.1
  - `untrusted_ca_cert` — a different CA + cert (for negative-path tests)
  - `free_port` — a free loopback TCP port
  - Cert set includes `SubjectKeyIdentifier`, `AuthorityKeyIdentifier`, `KeyUsage`, and `ExtendedKeyUsage` extensions to satisfy OpenSSL strict verification on Python 3.14+.
- `tests/integration/test_tls_verify.py` — 123-line, 5-test SEC-01 suite:
  - `test_trusted_cert_accepted` — real handshake completes with CA-signed cert
  - `test_untrusted_cert_rejected` — raises `ssl.SSLCertVerificationError` when cert is signed by a different CA
  - `test_insecure_flag_requires_env_var` — double-gate semantics (asserts False for single-gate conditions and "yes" vs "1" distinction)
  - `test_insecure_flag_emits_error_log` — asserts ERROR log event `transport.insecure_mode_active` with caplog
  - `test_no_cert_none_in_client_broker_common` — grep-level regression guard: any file in `client/`, `common/`, or `broker/` that contains `CERT_NONE` must also reference `insecure_tls_allowed` (or be `tls_opt_out.py` itself). Fails if anyone ever re-introduces ungated `CERT_NONE`.

### Modified

- `client/protocol.py`:
  - `__init__`: added `_insecure_skip_verify: bool = False` and `_ca_bundle: Optional[str] = None` attributes.
  - Added public `set_insecure_skip_verify(enable, ca_bundle=None)` setter with warning docstring.
  - `_connect_to_server` (Site 1, was lines 291-296): `ssl.SSLContext(PROTOCOL_TLS_CLIENT)` + `check_hostname=False` + `verify_mode=CERT_NONE` → `build_client_ssl_context(..., site_label="server_wss")`.
  - `_broker_handshake` (Site 2, was lines 360-365): same replacement with `site_label="broker_wss"`.
- `common/quic_transport.py`:
  - `connect()` (Site 3, was lines 402-414):
    - Signature default flipped: `verify_cert: bool = False` → `verify_cert: bool = True`.
    - New default: `config.verify_mode = ssl.CERT_REQUIRED`.
    - `CERT_NONE` branch now gated by `if not verify_cert and insecure_tls_allowed(cli_flag=True):` with an ERROR log.
- `broker/pool.py`:
  - `MachinePool.__init__`: added `_insecure_skip_verify` / `_ca_bundle` attrs.
  - `_probe_one` (Site 4, was lines 148-154): `aiohttp.TCPConnector(ssl=False)` → `TCPConnector(ssl=build_client_ssl_context(..., site_label="broker_probe"))`.

## Decisions Made

1. **Route every call site through `build_client_ssl_context()`** rather than let each site call `ssl.create_default_context()` inline. Reduces drift; a future `SEC-02..04` (Tailscale-native certs, TOFU pinning, corporate CA bundle) can be layered by extending the one helper rather than touching four call sites.
2. **Keep the QUIC `verify_cert` parameter** but flip its default to `True`. This preserves ABI compatibility with any existing caller that already passes `verify_cert=True` (a forward-compatible improvement) while making the insecure path require explicit opt-in.
3. **Test-fixture extensions** (`SubjectKeyIdentifier` + `AuthorityKeyIdentifier` + `KeyUsage` + `ExtendedKeyUsage`) added because Python 3.14 / OpenSSL on macOS-14 now enforces strict X.509 — first run without them failed with "Missing Authority Key Identifier" then "CA cert does not include key usage extension". Fixture now generates cert sets that pass strict verification.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 – Test-fixture bug] OpenSSL strict X.509 verification on Python 3.14 required extra cert extensions**

- **Found during:** Task 2 (integration-test execution)
- **Issue:** The conftest CA/server cert pair as-specified in the plan lacked `SubjectKeyIdentifier`, `AuthorityKeyIdentifier`, `KeyUsage`, and `ExtendedKeyUsage` extensions. Modern OpenSSL (Python 3.14 on macOS) rejects such certs during handshake with `Missing Authority Key Identifier` → then `CA cert does not include key usage extension`.
- **Fix:** Added SKI+AKI to both CA and server certs; added KeyUsage(`key_cert_sign=True, crl_sign=True`) to CA; added KeyUsage(`digital_signature=True, key_encipherment=True`) + ExtendedKeyUsage(SERVER_AUTH) to server cert.
- **Files modified:** `tests/integration/conftest.py`
- **Verification:** All 5 tests now pass under Python 3.14.4 / OpenSSL on macOS. `test_trusted_cert_accepted` succeeds, `test_untrusted_cert_rejected` still raises `SSLCertVerificationError` as expected.
- **Committed in:** `f4bae89` (part of Task 2 commit)

## Grep-level Regression Baseline

After this plan completes, the following grep commands define the expected baseline:

```
# Ungated CERT_NONE outside common/tls_opt_out.py — MUST be empty
grep -rln "CERT_NONE" client/ common/ broker/ --include="*.py" \
  | grep -v tls_opt_out.py \
  | xargs -I{} sh -c 'grep -q "insecure_tls_allowed" "{}" || echo "LEAK: {}"'
# → empty

# ssl=False in production code — MUST be empty
grep -rn "ssl=False" client/ common/ broker/ --include="*.py"
# → empty

# Expected CERT_NONE occurrences (gated) — 1 match only, inside double-gated branch
grep -rn "CERT_NONE" client/ common/ broker/ --include="*.py" | grep -v tls_opt_out.py
# → common/quic_transport.py:<line>:            config.verify_mode = ssl.CERT_NONE
```

`test_no_cert_none_in_client_broker_common` encodes this invariant as a unit test that fails CI if anyone ever adds ungated `CERT_NONE` back to `client/`, `common/`, or `broker/`.

## Platform-Specific Notes

- **Python 3.14 / macOS-14 (author's local)**: Tests pass. Required the stricter cert extensions noted above.
- **Rocky 9 (Phase 1 CI target)**: Python 3.11/3.12 default stdlib OpenSSL should accept the extended cert set identically; the extension additions are strictly additive relative to plan-as-written. **No additional changes expected** for the Rocky 9 runner path.
- **Plan 03 (`pytest` baseline)** will land the test runner config + dev requirements file; **Plan 04** will add `cryptography`, `websockets`, `aiohttp`, `pytest-asyncio`, and `pytest-timeout` to `requirements-dev.txt`; **Plan 05** (CI wiring) will ensure both macos-14 and rockylinux:9 runners execute `tests/integration/test_tls_verify.py` on every PR. The 5 tests here will then run automatically.

## Self-Check: PASSED

Verification commands run after SUMMARY.md draft:

- `[ -f common/tls_opt_out.py ]` → FOUND
- `[ -f tests/integration/__init__.py ]` → FOUND
- `[ -f tests/integration/conftest.py ]` → FOUND
- `[ -f tests/integration/test_tls_verify.py ]` → FOUND
- `git log --oneline | grep 7183eab` → FOUND (Task 1 commit)
- `git log --oneline | grep f4bae89` → FOUND (Task 2 commit)
- `grep -rln "CERT_NONE" client/ common/ broker/ --include="*.py" | grep -v tls_opt_out.py | xargs -I{} sh -c 'grep -q "insecure_tls_allowed" "{}" || echo "LEAK"'` → no LEAK output
- `grep -rn "ssl=False" client/ common/ broker/ --include="*.py"` → zero matches
- All 5 integration tests pass under Python 3.14.4 + pytest 9.0.3 + pytest-asyncio 1.3.0
