"""OBS-01 + error taxonomy — stable code attrs + inheritance + ProtocolErrorMsg.

TeraguchiError is the base of a 15-class hierarchy. Every concrete subclass has a
stable `code` class attribute that never changes across versions — it's a wire contract
consumed by ProtocolErrorMsg and by Phase-2+ FSM transitions that react deterministically
to specific codes (e.g. TERA_AUTH_FAILED → do NOT reconnect; TERA_TRANSPORT → back off).
"""
from common.errors import (
    TeraguchiError, TransportError, TlsVerificationError,
    ProtocolError, ProtocolVersionMismatch, InvalidMessageError,
    AuthError, AuthFailedError, AuthTimeoutError, TokenError,
    FSMError, InvalidTransitionError, EncoderError,
    EncoderRestartFailed, CaptureError,
)


def test_base_class_has_default_code():
    assert TeraguchiError.code == "TERA_UNKNOWN"


def test_every_subclass_has_stable_code():
    expected = {
        TransportError: "TERA_TRANSPORT",
        TlsVerificationError: "TERA_TLS_VERIFY",
        ProtocolError: "TERA_PROTOCOL",
        ProtocolVersionMismatch: "TERA_PROTO_VERSION",
        InvalidMessageError: "TERA_PROTO_INVALID",
        AuthError: "TERA_AUTH",
        AuthFailedError: "TERA_AUTH_FAILED",
        AuthTimeoutError: "TERA_AUTH_TIMEOUT",
        TokenError: "TERA_TOKEN",
        FSMError: "TERA_FSM",
        InvalidTransitionError: "TERA_FSM_TRANSITION",
        EncoderError: "TERA_ENCODER",
        EncoderRestartFailed: "TERA_ENCODER_RESTART",
        CaptureError: "TERA_CAPTURE",
    }
    for cls, code in expected.items():
        assert cls.code == code, f"{cls.__name__}.code should be {code}"


def test_hierarchy_is_catchable_by_base():
    """Catching TeraguchiError must catch every subclass."""
    for cls in (TlsVerificationError, AuthFailedError, InvalidTransitionError):
        try:
            raise cls("boom")
        except TeraguchiError as e:
            assert e.code.startswith("TERA_")


def test_tls_verification_is_transport_subtype():
    """SEC-01 related — TlsVerificationError → TransportError → TeraguchiError."""
    try:
        raise TlsVerificationError("cert bad")
    except TransportError as e:
        assert e.code == "TERA_TLS_VERIFY"


def test_protocol_error_msg_serializes():
    from common.messages import ProtocolErrorMsg, MsgType
    import json
    err = ProtocolErrorMsg(code="TERA_AUTH_FAILED", message="bad pw",
                           session_id="s1", recoverable=False)
    parsed = json.loads(err.to_json())
    assert parsed["type"] == MsgType.ERROR
    assert parsed["code"] == "TERA_AUTH_FAILED"
    assert parsed["recoverable"] is False
    # We never put secrets into ProtocolErrorMsg
    assert "password" not in parsed
