"""Bearer-token verification (ADR-0007). FAKE keys only; no network."""

import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from gorgona_booking.auth.verifier import InvalidTokenError
from tests.support.fake_idp import FAKE_AUDIENCE, FAKE_ISSUER, FakeIdp

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def idp() -> FakeIdp:
    return FakeIdp()


async def test_valid_token_yields_identity_claims(idp: FakeIdp) -> None:
    token = idp.token("fake-sub-1", email="Person@Example.test", name="FAKE Person")
    verified = await idp.verifier().verify(token)
    assert (verified.issuer, verified.subject) == (FAKE_ISSUER, "fake-sub-1")
    assert verified.email == "Person@Example.test"
    assert verified.email_verified is True
    assert verified.name == "FAKE Person"


async def test_unverified_email_is_reported_as_unverified(idp: FakeIdp) -> None:
    verified = await idp.verifier().verify(
        idp.token("fake-sub-1", email="p@example.test", email_verified=False)
    )
    assert verified.email_verified is False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _compact(header: dict[str, object], payload: dict[str, object], signature: bytes) -> str:
    return ".".join(
        [_b64(json.dumps(header).encode()), _b64(json.dumps(payload).encode()), _b64(signature)]
    )


def _claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    return {
        "iss": FAKE_ISSUER,
        "aud": FAKE_AUDIENCE,
        "sub": "fake-sub-1",
        "iat": now,
        "nbf": now,
        "exp": now + 300,
    } | overrides


def _hs256_with_public_key(idp: FakeIdp) -> str:
    # Classic algorithm-confusion attack: HMAC keyed with the public key bytes.
    public_pem = idp.private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header: dict[str, object] = {"alg": "HS256", "typ": "JWT", "kid": idp.kid}
    signing_input = (
        _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(_claims()).encode())
    ).encode()
    signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    return signing_input.decode() + "." + _b64(signature)


def _tamper(token: str) -> str:
    header, payload, signature = token.split(".")
    claims = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    return ".".join([header, _b64(claims.replace(b"fake-sub-1", b"fake-sub-2")), signature])


def _bad_token(idp: FakeIdp, case: str) -> str:
    now = int(time.time())
    match case:
        case "expired":
            return idp.token("fake-sub-1", expires_in=-60)
        case "not_yet_valid":
            return idp.token("fake-sub-1", nbf=now + 3600)
        case "wrong_issuer":
            return idp.token("fake-sub-1", iss="https://evil.test/")
        case "wrong_audience":
            return idp.token("fake-sub-1", aud="someone-else")
        case "missing_sub":
            return idp.token("fake-sub-1", sub=None)
        case "alg_none":
            return _compact({"alg": "none", "typ": "JWT", "kid": idp.kid}, _claims(), b"")
        case "hs256_confusion":
            return _hs256_with_public_key(idp)
        case "missing_kid":
            return jwt.encode(_claims(), idp.private_key, algorithm="RS256")
        case "unknown_kid":
            return idp.token("fake-sub-1", headers={"kid": "nope"})
        case "tampered":
            return _tamper(idp.token("fake-sub-1"))
        case "garbage":
            return "not-a-jwt"
        case _:
            return "a" * 20_000


@pytest.mark.parametrize(
    "case",
    [
        "expired",
        "not_yet_valid",
        "wrong_issuer",
        "wrong_audience",
        "missing_sub",
        "alg_none",
        "hs256_confusion",
        "missing_kid",
        "unknown_kid",
        "tampered",
        "garbage",
        "oversized",
    ],
)
async def test_invalid_tokens_are_rejected_without_echoing_them(idp: FakeIdp, case: str) -> None:
    token = _bad_token(idp, case)
    with pytest.raises(InvalidTokenError) as info:
        await idp.verifier().verify(token)
    assert token not in str(info.value)
    assert info.value.code == "INVALID_TOKEN"


async def test_a_token_from_a_different_key_is_rejected(idp: FakeIdp) -> None:
    impostor = FakeIdp(kid=idp.kid)  # same kid, different key
    with pytest.raises(InvalidTokenError):
        await idp.verifier().verify(impostor.token("fake-sub-1"))
