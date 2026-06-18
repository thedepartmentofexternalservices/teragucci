"""STAB-01 — server auth local-mode + token-mode round-trips.

PAM mode lives in test_pam_auth.py (mocked). This file covers:
- ``Authenticator(mode="none")`` disables auth
- ``Authenticator(mode="local")`` loads a user file on init
- ``create_challenge()`` emits a random hex challenge
- Local challenge-response round-trip (add_user → create_challenge →
  hash on client → verify on server)
- ``verify_token()`` HMAC round-trip using the broker token format

Target-read divergence notes:
- ``Authenticator`` constructor signature is ``(mode, users_file, enabled)`` —
  no separate ``create_challenge`` ctor arg.
- Local user DB schema: ``{username: {"password_hash": <sha256(password:salt)>,
  "salt": <hex>}}``.
- Local ``verify(username, client_hash, challenge)`` expects the client to
  compute ``sha256(password_hash:challenge)`` — i.e. it hashes the STORED
  hash (not the raw password) with the challenge.
- ``verify_token`` lives on ``Authenticator`` (not just ``broker.tokens``) —
  this is what the server uses when a broker hand-off arrives.
"""
import hashlib
import json

import pytest

from server.auth import Authenticator


@pytest.fixture
def local_users_file(tmp_path):
    """Write a tiny users DB in the exact schema ``Authenticator._load_users`` reads.

    User ``alice`` has password ``hunter2``; salt + hash computed to match the
    format that ``Authenticator.add_user`` would produce.
    """
    salt = "cafebabecafebabecafebabecafebabe"
    password_hash = hashlib.sha256(f"hunter2:{salt}".encode()).hexdigest()
    f = tmp_path / "users.json"
    f.write_text(json.dumps({
        "alice": {"password_hash": password_hash, "salt": salt},
    }))
    return f


def test_authenticator_mode_none_is_disabled(tmp_path):
    auth = Authenticator(mode="none", users_file=str(tmp_path / "unused.json"))
    assert auth.enabled is False
    assert auth.mode == "none"


def test_authenticator_enabled_flag_overrides_mode(tmp_path):
    """``enabled=False`` forces mode to 'none' regardless of what the caller passed."""
    auth = Authenticator(mode="local", users_file=str(tmp_path / "unused.json"),
                         enabled=False)
    assert auth.mode == "none"
    assert auth.enabled is False


def test_authenticator_local_mode_loads_users(local_users_file):
    auth = Authenticator(mode="local", users_file=str(local_users_file))
    assert auth.enabled is True
    assert "alice" in auth.list_users()


def test_local_mode_missing_file_disables_auth(tmp_path):
    """If the users file doesn't exist, local auth silently disables (by design —
    don't kill server startup)."""
    auth = Authenticator(mode="local", users_file=str(tmp_path / "nope.json"))
    # _load_users() flips enabled off when file is missing
    assert auth.enabled is False


def test_create_challenge_returns_hex_string():
    """Local-mode challenges are 64-char hex strings (secrets.token_hex(32))."""
    auth = Authenticator(mode="local", users_file="/tmp/teraguchi-test-nonexistent")
    ch = auth.create_challenge()
    assert isinstance(ch, str)
    assert len(ch) == 64
    # Valid hex
    int(ch, 16)


def test_create_challenge_is_unique():
    auth = Authenticator(mode="local", users_file="/tmp/teraguchi-test-nonexistent")
    challenges = {auth.create_challenge() for _ in range(10)}
    assert len(challenges) == 10


def test_local_challenge_response_success(local_users_file):
    """Full challenge-response round trip: the client hashes ``<stored_hash>:<challenge>``
    and the server must accept it."""
    auth = Authenticator(mode="local", users_file=str(local_users_file))
    challenge = auth.create_challenge()

    # Client side: compute the same hash the server expects
    users_db = json.loads(local_users_file.read_text())
    stored_hash = users_db["alice"]["password_hash"]
    client_hash = hashlib.sha256(f"{stored_hash}:{challenge}".encode()).hexdigest()

    assert auth.verify("alice", client_hash, challenge) is True


def test_local_challenge_response_wrong_password_rejected(local_users_file):
    auth = Authenticator(mode="local", users_file=str(local_users_file))
    challenge = auth.create_challenge()
    # Intentionally wrong hash
    bad_hash = hashlib.sha256(b"not the right thing").hexdigest()
    assert auth.verify("alice", bad_hash, challenge) is False


def test_local_verify_unknown_user_rejected(local_users_file):
    auth = Authenticator(mode="local", users_file=str(local_users_file))
    challenge = auth.create_challenge()
    assert auth.verify("nonexistent", "anyhash", challenge) is False


def test_local_verify_unknown_challenge_rejected(local_users_file):
    """Challenge replay protection — a challenge the server never issued must fail."""
    auth = Authenticator(mode="local", users_file=str(local_users_file))
    assert auth.verify("alice", "anyhash", "fabricated-challenge") is False


def test_local_challenge_consumed_on_use(local_users_file):
    """After one verify attempt, the challenge is removed — prevents replay
    even with the real hash."""
    auth = Authenticator(mode="local", users_file=str(local_users_file))
    challenge = auth.create_challenge()

    users_db = json.loads(local_users_file.read_text())
    stored_hash = users_db["alice"]["password_hash"]
    client_hash = hashlib.sha256(f"{stored_hash}:{challenge}".encode()).hexdigest()

    # First attempt succeeds
    assert auth.verify("alice", client_hash, challenge) is True
    # Second attempt with the same challenge must fail (consumed)
    assert auth.verify("alice", client_hash, challenge) is False


def test_add_user_persists_and_authenticates(tmp_path):
    """End-to-end add_user → verify loop without hand-crafting the users file."""
    users_file = tmp_path / "users.json"
    auth = Authenticator(mode="local", users_file=str(users_file))
    # Must force-enable — _load_users disabled it because the file didn't exist yet
    auth.enabled = True
    auth.add_user("bob", "correcthorsebatterystaple")
    assert "bob" in auth.list_users()

    # Round-trip auth
    challenge = auth.create_challenge()
    salt = auth.get_user_salt("bob")
    assert salt is not None
    stored_hash = hashlib.sha256(f"correcthorsebatterystaple:{salt}".encode()).hexdigest()
    client_hash = hashlib.sha256(f"{stored_hash}:{challenge}".encode()).hexdigest()
    assert auth.verify("bob", client_hash, challenge) is True


def test_verify_token_roundtrip():
    """server/auth.py verify_token must accept a broker-format token with the
    shared secret."""
    from broker.tokens import generate_token

    auth = Authenticator(mode="none")
    secret = "shared-secret-between-broker-and-server"
    tok = generate_token("alice", "dxs-flame-01", secret)
    assert auth.verify_token(tok, secret) == "alice"


def test_verify_token_wrong_secret_rejected():
    from broker.tokens import generate_token

    auth = Authenticator(mode="none")
    tok = generate_token("alice", "dxs-flame-01", "real-secret")
    assert auth.verify_token(tok, "wrong-secret") is None


def test_verify_token_malformed_parts_rejected():
    auth = Authenticator(mode="none")
    # Wrong number of ':'-separated parts — should reject without crashing
    assert auth.verify_token("not-a-valid-token", "secret") is None
    assert auth.verify_token("a:b:c:d", "secret") is None
    assert auth.verify_token("a:b:c:d:e:f", "secret") is None
