"""STAB-01 — PAM auth mocked (no real PAM needed on the runner).

Target-read divergence notes:
- ``server/pam_auth.py`` uses ``PAMAuthenticator`` class, NOT a free
  ``authenticate_via_pam`` function. We test the class.
- The module does ``import pam as pam_module`` inside a try/except and sets
  ``PAM_AVAILABLE``. Tests monkeypatch both symbols so the class behaves as
  if ``python-pam`` is installed — no real PAM stack touched.
- Deferred-import pattern (per CONVENTIONS): the module must not fail at
  import time when python-pam is missing.
"""
from unittest import mock

import pytest

import server.pam_auth as pam_auth


@pytest.fixture
def pam_module_available(monkeypatch):
    """Force the module to behave as if python-pam is installed.

    We replace ``pam_module`` + the ``PAM_AVAILABLE`` flag with a Mock so the
    ``PAMAuthenticator`` class treats itself as available.
    """
    fake_pam_module = mock.MagicMock()
    # pam_module.pam() returns a stateful session object with .authenticate + .reason
    fake_session = mock.MagicMock()
    fake_session.reason = ""
    fake_pam_module.pam.return_value = fake_session

    monkeypatch.setattr(pam_auth, "pam_module", fake_pam_module)
    monkeypatch.setattr(pam_auth, "PAM_AVAILABLE", True)
    return fake_session


def test_pam_authenticate_success(pam_module_available):
    pam_module_available.authenticate.return_value = True

    auth = pam_auth.PAMAuthenticator(service="login")
    assert auth.available is True
    assert auth.authenticate("alice", "correct-password") is True
    pam_module_available.authenticate.assert_called_once_with(
        "alice", "correct-password", service="login"
    )


def test_pam_authenticate_failure(pam_module_available):
    pam_module_available.authenticate.return_value = False
    pam_module_available.reason = "Authentication failed"

    auth = pam_auth.PAMAuthenticator()
    assert auth.authenticate("alice", "wrong-password") is False


def test_pam_custom_service(pam_module_available):
    """The ``service`` kwarg must be threaded through to pam_module.pam().authenticate."""
    pam_module_available.authenticate.return_value = True

    auth = pam_auth.PAMAuthenticator(service="teraguchi")
    auth.authenticate("bob", "pw")
    pam_module_available.authenticate.assert_called_once_with(
        "bob", "pw", service="teraguchi"
    )


def test_pam_unavailable_returns_false(monkeypatch):
    """If PAM is not installed on the runner, authenticate must short-circuit
    to False, not raise."""
    monkeypatch.setattr(pam_auth, "PAM_AVAILABLE", False)
    monkeypatch.setattr(pam_auth, "pam_module", None)

    auth = pam_auth.PAMAuthenticator()
    assert auth.available is False
    assert auth.authenticate("alice", "pw") is False


def test_pam_module_has_deferred_import_guard():
    """CONVENTIONS.md §"Deferred imports": pam_auth must not hard-fail at import
    time when python-pam is missing. The documented signal is ``PAM_AVAILABLE``."""
    assert hasattr(pam_auth, "PAM_AVAILABLE")
    assert isinstance(pam_auth.PAM_AVAILABLE, bool)


def test_get_user_info_unknown_user_returns_none():
    """``get_user_info`` is a static method that wraps ``pwd.getpwnam`` — unknown
    user must return None, not raise."""
    result = pam_auth.PAMAuthenticator.get_user_info("definitely-not-a-real-user-xyzzy")
    assert result is None


def test_get_user_info_shape_for_real_user():
    """Sanity: for any real system user (current user, guaranteed to exist on
    any runner), the returned dict has the documented keys."""
    import getpass
    username = getpass.getuser()
    info = pam_auth.PAMAuthenticator.get_user_info(username)
    assert info is not None
    for key in ("uid", "gid", "home", "shell", "groups"):
        assert key in info, f"missing key: {key}"
    assert isinstance(info["uid"], int)
    assert isinstance(info["gid"], int)
    assert isinstance(info["groups"], list)
