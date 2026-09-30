"""Request dependencies for authenticated routes."""

from fastapi import Request

from gorgona_booking.auth.principal import Principal, resolve_principal
from gorgona_booking.auth.verifier import (
    AuthenticationRequiredError,
    AuthNotConfiguredError,
    TokenVerifier,
    VerifiedToken,
)
from gorgona_booking.db.pool import RuntimePool
from gorgona_booking.errors import DatabaseUnavailableError


def runtime_pool(request: Request) -> RuntimePool:
    pool: RuntimePool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise DatabaseUnavailableError("The service is not available right now")
    return pool


async def get_verified_token(request: Request) -> VerifiedToken:
    """Who is calling (authentication only; grants nothing by itself)."""
    verifier: TokenVerifier | None = getattr(request.app.state, "token_verifier", None)
    if verifier is None:
        raise AuthNotConfiguredError("Authentication is not configured")
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        raise AuthenticationRequiredError("Authentication required")
    return await verifier.verify(credentials.strip())


async def get_principal(request: Request) -> Principal:
    """An authenticated, linked and active user. Tenant rights are decided per route."""
    token = await get_verified_token(request)
    return await resolve_principal(runtime_pool(request), token)
