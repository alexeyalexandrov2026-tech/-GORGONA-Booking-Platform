"""Provider-agnostic bearer-token verification (ADR-0007).

Answers only "who is this?". Tenant authority never comes from token claims.
Token text is never logged, stored or echoed; every failure is the same error.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import jwt

from gorgona_booking.errors import DomainError

# Asymmetric algorithms only: "none" and HMAC are never acceptable for an IdP token.
ALLOWED_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "PS256", "ES256", "ES384"})
_MAX_TOKEN_LENGTH = 8192
_INVALID = "Invalid or expired access token"


class InvalidTokenError(DomainError):
    code = "INVALID_TOKEN"


class AuthenticationRequiredError(DomainError):
    code = "AUTHENTICATION_REQUIRED"


class AuthNotConfiguredError(DomainError):
    code = "AUTH_NOT_CONFIGURED"


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None
    expires_at: datetime


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> VerifiedToken: ...


class KeySource(Protocol):
    async def signing_key(self, kid: str) -> jwt.PyJWK: ...


class StaticJwksKeySource:
    """A fixed JWKS document (tests, or an IdP whose keys are pinned by config)."""

    def __init__(self, jwks: Mapping[str, Any]) -> None:
        keys = jwt.PyJWKSet.from_dict(dict(jwks)).keys
        self._keys = {key.key_id: key for key in keys if key.key_id}

    async def signing_key(self, kid: str) -> jwt.PyJWK:
        key = self._keys.get(kid)
        if key is None:
            raise InvalidTokenError(_INVALID)
        return key


class RemoteJwksKeySource:
    """The IdP's published JWKS, cached; fetched off the event loop."""

    def __init__(self, jwks_url: str, *, cache_seconds: int = 300, timeout: float = 5.0) -> None:
        self._client = jwt.PyJWKClient(
            jwks_url, cache_keys=True, lifespan=cache_seconds, timeout=timeout
        )

    async def signing_key(self, kid: str) -> jwt.PyJWK:
        try:
            return await asyncio.to_thread(self._client.get_signing_key, kid)
        except jwt.PyJWKClientError:
            raise InvalidTokenError(_INVALID) from None


class OidcJwtVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        key_source: KeySource,
        algorithms: Sequence[str] = ("RS256", "ES256"),
        leeway_seconds: int = 30,
    ) -> None:
        unsupported = set(algorithms) - ALLOWED_ALGORITHMS
        if unsupported or not algorithms:
            raise ValueError(f"unsupported token algorithms: {sorted(unsupported)}")
        self._issuer = issuer
        self._audience = audience
        self._keys = key_source
        self._algorithms = frozenset(algorithms)
        self._leeway = leeway_seconds

    async def verify(self, token: str) -> VerifiedToken:
        if not token or len(token) > _MAX_TOKEN_LENGTH:
            raise InvalidTokenError(_INVALID)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise InvalidTokenError(_INVALID) from None
        algorithm, kid = header.get("alg"), header.get("kid")
        if algorithm not in self._algorithms or not isinstance(kid, str) or not kid:
            raise InvalidTokenError(_INVALID)
        key = await self._keys.signing_key(kid)
        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError:
            raise InvalidTokenError(_INVALID) from None
        subject = claims.get("sub")
        if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
            raise InvalidTokenError(_INVALID)
        email = claims.get("email")
        name = claims.get("name")
        return VerifiedToken(
            issuer=self._issuer,
            subject=subject,
            email=email if isinstance(email, str) else None,
            email_verified=claims.get("email_verified") is True,
            name=name if isinstance(name, str) else None,
            expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
        )
