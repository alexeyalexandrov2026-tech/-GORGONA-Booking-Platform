# ADR-0007: Provider-agnostic authentication boundary

- Status: Accepted (2026-09-30, M2)

## Context

Staff and admins need to sign in before the booking engine can be exposed to them. The identity provider (IdP) has not been chosen, and building password storage, login pages or session management would add risk and no product value.

## Decision

- Authentication answers only "who is this?". It is a `TokenVerifier` protocol (`gorgona_booking/auth/verifier.py`) returning a `VerifiedToken` (issuer, subject, email, email_verified, expiry).
- The implementation is `OidcJwtVerifier` on **PyJWT[crypto]**. It validates bearer JWTs from any OIDC/OAuth 2 provider against its JWKS and enforces:
  - issuer and audience;
  - `exp`, `nbf` and `iat`, with bounded leeway;
  - `sub` present;
  - an explicit asymmetric algorithm allowlist (RS256/ES256 by default), so `none` and HMAC are never accepted;
  - a required `kid`.

  The JWKS is fetched and cached off the event loop.
- The provider is chosen by configuration only: `GBA_AUTH_ISSUER`, `GBA_AUTH_AUDIENCE`, `GBA_AUTH_JWKS_URL`, `GBA_AUTH_ALGORITHMS`, `GBA_AUTH_LEEWAY_SECONDS`. Changing providers changes no domain code.
- No passwords, password hashes, sessions or login UI are built here.
- Tokens are never stored, logged or echoed. A 401 carries `WWW-Authenticate: Bearer` and a generic code only.
- If authentication is not configured, protected routes answer 503 `AUTH_NOT_CONFIGURED`. The public hold route keeps its M1 behaviour.

## Consequences

- Tests use locally generated keys and a static JWKS source. There is no network and no test-only endpoint.
- Revocation of an IdP session takes effect when the token expires. Tenant authority is revoked immediately server-side (ADR-0009).
