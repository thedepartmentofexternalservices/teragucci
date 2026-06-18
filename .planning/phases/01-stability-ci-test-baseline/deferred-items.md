# Phase 01 — Deferred Items

## Pre-existing test failures (out of scope for Plan 01-14)

### `tests/server/test_pam_auth.py` — 1 failed, 3 errored

**Error:** `AttributeError: module 'server.pam_auth' has no attribute 'pam_module'`

**Tests affected:**
- `test_pam_unavailable_returns_false` (failed)
- `test_pam_authenticate_success` (errored)
- `test_pam_authenticate_failure` (errored)
- `test_pam_custom_service` (errored)

**Status:** Pre-existing on base commit `7a0d089` (confirmed before any Plan
01-14 changes). `server/pam_auth.py` does not expose a `pam_module` attribute
that the tests try to monkey-patch. Likely drift between test expectations and
a recent refactor of the PAM auth module. Unrelated to OBS-02/OBS-03 wiring.

**Recommendation:** File a separate plan to reconcile `tests/server/test_pam_auth.py`
against the current `server/pam_auth.py` interface. Defer until after Phase 1
wave 8b lands or treat as low-priority cleanup.
