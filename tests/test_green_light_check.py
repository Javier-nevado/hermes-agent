"""Unit tests for gateway/green_light_check.py.

The green-light token is signed by abi-service in Go, but the format is
language-agnostic (base64url(payload_json).base64url(ed25519_sig)); Go→Python
interop is proven live (8/8). These tests sign tokens in Python with the SAME
format to exercise the verifier's branches without a network or Docker.
"""
import base64
import json
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gateway.green_light_check import verify_green_light

NOW = 1_700_000_000


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _sign(priv, payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return _b64u(body) + "." + _b64u(priv.sign(body))


def _pub_raw(pub) -> bytes:
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _make_gl(tmp_path, pub, tokens: dict[str, str]) -> str:
    gl = tmp_path / "greenlights"
    gl.mkdir()
    (gl / "greenlight.pub").write_bytes(_pub_raw(pub))
    for name, tok in tokens.items():
        (gl / f"{name}.token").write_text(tok)
    return str(gl)


def test_ok_valid_token(tmp_path):
    priv, pub = _new_keypair()
    tok = _sign(priv, {"agent": "abi-agent-ailean", "fp": "abcd", "exp": NOW + 3600, "cap": 3})
    gl = _make_gl(tmp_path, pub, {"abi-agent-ailean": tok})
    v = verify_green_light("abi-agent-ailean", gl, now=NOW)
    assert v.ok and not v.is_denied and not v.is_transient


def test_ok_cap_omitted_unlimited(tmp_path):
    priv, pub = _new_keypair()
    tok = _sign(priv, {"agent": "abi-agent-ailean", "fp": "x", "exp": NOW + 3600})  # no cap
    gl = _make_gl(tmp_path, pub, {"abi-agent-ailean": tok})
    assert verify_green_light("abi-agent-ailean", gl, now=NOW).ok


def test_no_token_is_over_cap_denial(tmp_path):
    priv, pub = _new_keypair()
    gl = _make_gl(tmp_path, pub, {})  # no token for this agent
    v = verify_green_light("abi-agent-ailean", gl, now=NOW)
    assert v.is_denied and v.reason == "no_token"
    assert "over its licensed agent count" in v.user_message


def test_expired_is_denied(tmp_path):
    priv, pub = _new_keypair()
    tok = _sign(priv, {"agent": "abi-agent-ailean", "exp": NOW - 1})
    gl = _make_gl(tmp_path, pub, {"abi-agent-ailean": tok})
    v = verify_green_light("abi-agent-ailean", gl, now=NOW)
    assert v.is_denied and v.reason == "expired"


def test_bad_signature_is_denied(tmp_path):
    priv, pub = _new_keypair()
    tok = _sign(priv, {"agent": "abi-agent-ailean", "exp": NOW + 3600})
    body_b64, sig_b64 = tok.split(".")
    sig = bytearray(base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4)))
    sig[0] ^= 0xFF
    bad = body_b64 + "." + _b64u(bytes(sig))
    gl = _make_gl(tmp_path, pub, {"abi-agent-ailean": bad})
    v = verify_green_light("abi-agent-ailean", gl, now=NOW)
    assert v.is_denied and v.reason == "bad_sig"


def test_name_mismatch_is_denied(tmp_path):
    priv, pub = _new_keypair()
    # Token minted for a DIFFERENT agent name.
    tok = _sign(priv, {"agent": "abi-agent-atlas", "exp": NOW + 3600})
    gl = _make_gl(tmp_path, pub, {"abi-agent-ailean": tok})
    v = verify_green_light("abi-agent-ailean", gl, now=NOW)
    assert v.is_denied and v.reason == "name_mismatch"


def test_malformed_token_no_dot_is_denied(tmp_path):
    priv, pub = _new_keypair()
    gl = _make_gl(tmp_path, pub, {"abi-agent-ailean": "not-a-valid-token"})
    v = verify_green_light("abi-agent-ailean", gl, now=NOW)
    assert v.is_denied and v.reason == "bad_sig"


def test_pubkey_missing_is_transient_fail_open(tmp_path):
    priv, pub = _new_keypair()
    tok = _sign(priv, {"agent": "abi-agent-ailean", "exp": NOW + 3600})
    gl = _make_gl(tmp_path, pub, {"abi-agent-ailean": tok})
    os.remove(os.path.join(gl, "greenlight.pub"))  # break the mount
    v = verify_green_light("abi-agent-ailean", gl, now=NOW)
    assert v.is_transient and v.reason == "pub_missing"


def test_empty_agent_name_is_transient(tmp_path):
    priv, pub = _new_keypair()
    gl = _make_gl(tmp_path, pub, {})
    v = verify_green_light("", gl, now=NOW)
    assert v.is_transient and v.reason == "no_agent_name"


def test_wrong_pubkey_rejects(tmp_path):
    # Token signed by one key, verified against a DIFFERENT published pubkey.
    priv1, _ = _new_keypair()
    _, pub2 = _new_keypair()
    tok = _sign(priv1, {"agent": "abi-agent-ailean", "exp": NOW + 3600})
    gl = _make_gl(tmp_path, pub2, {"abi-agent-ailean": tok})
    v = verify_green_light("abi-agent-ailean", gl, now=NOW)
    assert v.is_denied and v.reason == "bad_sig"


def _new_keypair():
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()
