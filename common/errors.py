"""Teraguchi exception hierarchy for protocol-level errors.

Every concrete subclass has a stable string `code` class attribute that:
  - lives in structlog events as `err_code`
  - serializes onto the wire in :class:`common.messages.ProtocolErrorMsg`
  - never changes across versions (wire contract — FSMs in Phase 2+ react
    deterministically to specific codes, e.g. TERA_AUTH_FAILED → do NOT
    reconnect; TERA_TRANSPORT → exponential back-off + retry)

See RESEARCH §"Error taxonomy" lines 975-1038 for the rationale + full catalog.
See ``common/messages.py::ProtocolErrorMsg`` for the wire envelope that carries
one of these ``code`` strings to the peer.

Usage:

    from common.errors import AuthFailedError, TransportError

    try:
        await handshake(ws)
    except TlsVerificationError as e:
        log.error("tls.verify.failed", err_code=e.code, err=str(e))
        raise       # propagate to ProtocolErrorMsg serializer
"""
from __future__ import annotations


class TeraguchiError(Exception):
    """Base for all Teraguchi-specific exceptions.

    Catching :class:`TeraguchiError` catches every subclass below. The default
    ``code`` is ``TERA_UNKNOWN`` — concrete subclasses MUST override it with a
    stable ``TERA_<DOMAIN>[_<DETAIL>]`` string.
    """

    code: str = "TERA_UNKNOWN"


# ---------------------------------------------------------------------------
# Transport (TLS / WebSocket / QUIC / UDP)
# ---------------------------------------------------------------------------


class TransportError(TeraguchiError):
    """Generic transport-layer failure (connect / read / write)."""

    code = "TERA_TRANSPORT"


class TlsVerificationError(TransportError):
    """TLS certificate verification failed.

    Used by SEC-01 + Phase-6 hardening — distinct from generic TransportError
    so the client UI can surface a specific actionable message ("trust the
    server cert" vs. "network is down").
    """

    code = "TERA_TLS_VERIFY"


# ---------------------------------------------------------------------------
# Protocol (message parsing, version negotiation)
# ---------------------------------------------------------------------------


class ProtocolError(TeraguchiError):
    """Generic wire-protocol fault (malformed frame, bad state)."""

    code = "TERA_PROTOCOL"


class ProtocolVersionMismatch(ProtocolError):
    """Client and server protocol versions are incompatible."""

    code = "TERA_PROTO_VERSION"


class InvalidMessageError(ProtocolError):
    """A JSON control message did not match the expected schema."""

    code = "TERA_PROTO_INVALID"


# ---------------------------------------------------------------------------
# Authentication / authorization
# ---------------------------------------------------------------------------


class AuthError(TeraguchiError):
    """Generic authentication failure."""

    code = "TERA_AUTH"


class AuthFailedError(AuthError):
    """Credentials rejected by the server (wrong password, bad HMAC)."""

    code = "TERA_AUTH_FAILED"


class AuthTimeoutError(AuthError):
    """Authentication handshake exceeded its deadline."""

    code = "TERA_AUTH_TIMEOUT"


class TokenError(AuthError):
    """Broker-issued token is invalid, expired, or replayed."""

    code = "TERA_TOKEN"


# ---------------------------------------------------------------------------
# Session FSM (Phase-1 SessionFSM + Phase-2+ handlers)
# ---------------------------------------------------------------------------


class FSMError(TeraguchiError):
    """Generic finite-state-machine fault."""

    code = "TERA_FSM"


class InvalidTransitionError(FSMError):
    """Attempted a transition the FSM does not allow from the current state."""

    code = "TERA_FSM_TRANSITION"


# ---------------------------------------------------------------------------
# Encoder / capture
# ---------------------------------------------------------------------------


class EncoderError(TeraguchiError):
    """Generic video-encoder failure (NVENC/VAAPI/VideoToolbox/software)."""

    code = "TERA_ENCODER"


class EncoderRestartFailed(EncoderError):
    """Auto-restart of a crashed encoder hit the retry budget."""

    code = "TERA_ENCODER_RESTART"


class CaptureError(TeraguchiError):
    """Screen-capture backend failed (NvFBC / XComposite / ScreenCaptureKit)."""

    code = "TERA_CAPTURE"
