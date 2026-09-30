"""A FAKE OIDC identity provider for tests: local RSA keys, static JWKS, no network."""

import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from gorgona_booking.auth.verifier import OidcJwtVerifier, StaticJwksKeySource

FAKE_ISSUER = "https://fake-idp.test/"
FAKE_AUDIENCE = "gorgona-booking-test"


class FakeIdp:
    def __init__(self, kid: str = "fake-kid-1") -> None:
        self.kid = kid
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        public_jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
        self.jwks: dict[str, Any] = {"keys": [public_jwk]}

    def verifier(self, *, leeway: int = 0) -> OidcJwtVerifier:
        return OidcJwtVerifier(
            issuer=FAKE_ISSUER,
            audience=FAKE_AUDIENCE,
            key_source=StaticJwksKeySource(self.jwks),
            leeway_seconds=leeway,
        )

    def token(
        self,
        subject: str,
        *,
        email: str | None = None,
        email_verified: bool = True,
        expires_in: int = 300,
        headers: dict[str, Any] | None = None,
        **claims: Any,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": FAKE_ISSUER,
            "aud": FAKE_AUDIENCE,
            "sub": subject,
            "iat": now,
            "nbf": now,
            "exp": now + expires_in,
        }
        if email is not None:
            payload |= {"email": email, "email_verified": email_verified}
        payload |= claims
        return jwt.encode(
            {k: v for k, v in payload.items() if v is not None},
            self.private_key,
            algorithm="RS256",
            headers={"kid": self.kid} | (headers or {}),
        )

    def bearer(self, subject: str, **kwargs: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(subject, **kwargs)}"}
