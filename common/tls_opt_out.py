"""TLS verification opt-out double-gate.

SEC-01 replaces every ``ssl.CERT_NONE`` in the codebase with real verification.
The dev escape hatch is retained for engineers whose dev boxes have no
real cert — but it is DOUBLE-GATED. Both must be present:

  1. ``--insecure-skip-verify`` on the CLI (passed in as ``insecure_cli_flag``)
  2. ``TERAGUCHI_ACCEPT_INSECURE=1`` in the environment

Rationale: a single gate (flag-only) is too easy to leak to production
via a forgotten systemd drop-in or a copy-pasted launch command. Requiring
an env var means "I explicitly know this process is not running under
verified TLS". Every use emits an ERROR-level log
(``transport.insecure_mode_active``).

Phase 1: this is the entire TLS-off story.
Phase 6: SEC-02..04 add Tailscale-native certs, TOFU fingerprint pinning,
         corporate CA bundle. At that point ``--insecure-skip-verify``
         becomes strictly a "last resort" escape hatch.
"""

import logging
import os
import ssl
from typing import Optional

logger = logging.getLogger(__name__)

_ENV_KEY = "TERAGUCHI_ACCEPT_INSECURE"
_ENV_VALUE = "1"


def insecure_tls_allowed(cli_flag: bool) -> bool:
    """Return True iff BOTH the CLI flag and env var are present.

    The double-gate is intentional: single gate (flag-only) is too easy
    to leak to production via launch scripts or systemd drop-ins.
    """
    return bool(cli_flag) and os.environ.get(_ENV_KEY) == _ENV_VALUE


def build_client_ssl_context(
    insecure_cli_flag: bool = False,
    ca_bundle: Optional[str] = None,
    site_label: str = "client",
) -> ssl.SSLContext:
    """Build an SSL context for client-side TLS.

    Verification is ON by default (stdlib ``create_default_context`` with
    ``Purpose.SERVER_AUTH``). The double-gate is the ONLY way to disable
    it. When the double-gate fires, an ERROR-level log event
    ``transport.insecure_mode_active`` is emitted per call.

    :param insecure_cli_flag: value of the ``--insecure-skip-verify`` CLI
        flag for the caller site (client, broker, quic, etc.).
    :param ca_bundle: optional path to a custom CA bundle (PEM). When
        omitted, the stdlib default trust store is used.
    :param site_label: short identifier of the caller site, included in
        the insecure-mode log event for diagnostics.
    """
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if ca_bundle:
        ctx.load_verify_locations(cafile=ca_bundle)

    if insecure_tls_allowed(insecure_cli_flag):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        logger.error(
            "transport.insecure_mode_active site=%s env=%s flag=%s",
            site_label,
            _ENV_KEY,
            "--insecure-skip-verify",
        )
    return ctx
