"""Integration-test fixtures: loopback server/client harness + real TLS CA.

SEC-01: these fixtures build a real CA and server cert using
``cryptography.x509`` so integration tests can exercise end-to-end TLS
verification. No verification opt-out is used anywhere in these
fixtures — that would defeat the entire purpose of SEC-01.

Exports:
    - ``tls_ca_and_cert``: trusted CA + server cert signed under it
    - ``untrusted_ca_cert``: a DIFFERENT CA + cert, for negative-path tests
    - ``free_port``: a bound-then-released loopback TCP port
    - ``fake_server_injector``: in-memory mock InputInjector that records
      every call made against it (INPUT-04 mock-at-server-boundary)
"""
from __future__ import annotations

import datetime
import ipaddress
import pathlib
import socket
from unittest import mock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _build_ca(common_name: str):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    # SubjectKeyIdentifier + AuthorityKeyIdentifier required by OpenSSL
    # strict verification in recent Python (3.14+) — without them the
    # handshake fails with "Missing Authority Key Identifier".
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ski, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _sign_server_cert(ca_key, ca_cert, host: str):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    san = [x509.DNSName(host)]
    try:
        san.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        pass
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    aki = x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(ski, critical=False)
        .add_extension(aki, critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _write_pair(tmp: pathlib.Path, name: str, key, cert):
    key_pem = tmp / f"{name}.key.pem"
    cert_pem = tmp / f"{name}.cert.pem"
    key_pem.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key_pem, cert_pem


@pytest.fixture
def tls_ca_and_cert(tmp_path):
    """Trusted CA + server cert for 127.0.0.1."""
    ca_key, ca_cert = _build_ca("teraguchi-test-ca")
    srv_key, srv_cert = _sign_server_cert(ca_key, ca_cert, "127.0.0.1")
    _ca_key_pem, ca_cert_pem = _write_pair(tmp_path, "ca", ca_key, ca_cert)
    srv_key_pem, srv_cert_pem = _write_pair(tmp_path, "server", srv_key, srv_cert)
    return {
        "ca_cert": ca_cert_pem,
        "server_cert": srv_cert_pem,
        "server_key": srv_key_pem,
    }


@pytest.fixture
def untrusted_ca_cert(tmp_path):
    """A second, unrelated CA.

    A cert signed by this CA must be REJECTED by a client trusting only
    the first (``tls_ca_and_cert``) CA.
    """
    ca_key, ca_cert = _build_ca("attacker-ca")
    srv_key, srv_cert = _sign_server_cert(ca_key, ca_cert, "127.0.0.1")
    _ca_key_pem, ca_cert_pem = _write_pair(tmp_path, "evil_ca", ca_key, ca_cert)
    srv_key_pem, srv_cert_pem = _write_pair(tmp_path, "evil_server", srv_key, srv_cert)
    return {
        "ca_cert": ca_cert_pem,
        "server_cert": srv_cert_pem,
        "server_key": srv_key_pem,
    }


@pytest.fixture
def free_port():
    """Return a free loopback TCP port (bound-then-released)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_server_injector():
    """In-memory mock InputInjector that records every call made against it.

    INPUT-04 (Plan 02-11) loopback test fixture. Mirrors the Phase 1
    mock-at-FFmpeg-subprocess-boundary pattern from
    ``tests/server/test_video_encoder_mock.py`` — the test handler in a
    ``websockets.serve`` loopback consumes parsed wire dicts and feeds
    them into this in-memory injector instead of touching real
    ``/dev/uinput`` or CGEventPost. The recorded ``events`` list lets
    tests assert the exact dispatch tuples reached "the server side".

    Schema of ``inj.events`` entries:
      - ``("key_event", scan_code:int, pressed:bool, caps_lock_on:bool)``
      - ``("text_commit", text:str)``
      - ``("reset_modifiers",)``
    """
    inj = mock.MagicMock()
    inj.events = []

    def key_event(scan_code, pressed, caps_lock_on=False, num_lock_on=False,
                  scroll_lock_on=False, **kw):
        inj.events.append(("key_event", scan_code, pressed, caps_lock_on))

    def text_commit(text):
        inj.events.append(("text_commit", text))

    def reset_modifiers(**kw):
        inj.events.append(("reset_modifiers",))

    inj.key_event = key_event
    inj.text_commit = text_commit
    inj.reset_modifiers = reset_modifiers
    return inj
