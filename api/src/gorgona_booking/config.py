"""Process configuration, read explicitly from environment variables."""

import os
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

type Environment = Literal["local", "test", "ci", "staging", "production"]

# Environment variable -> Settings field. Nothing else is read from the environment.
_ENV_FIELDS: Mapping[str, str] = {
    "GBA_ENV": "environment",
    "GBA_DATABASE_URL": "database_url",
    "GBA_DB_POOL_MIN_SIZE": "db_pool_min_size",
    "GBA_DB_POOL_MAX_SIZE": "db_pool_max_size",
    "GBA_HOLD_TTL_SECONDS": "hold_ttl_seconds",
    "GBA_AUTH_ISSUER": "auth_issuer",
    "GBA_AUTH_AUDIENCE": "auth_audience",
    "GBA_AUTH_JWKS_URL": "auth_jwks_url",
    "GBA_AUTH_ALGORITHMS": "auth_algorithms",
    "GBA_AUTH_LEEWAY_SECONDS": "auth_leeway_seconds",
}
_LIST_FIELDS = frozenset({"auth_algorithms"})
# Asymmetric only (ADR-0007); mirrors gorgona_booking.auth.verifier.ALLOWED_ALGORITHMS.
_AUTH_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "PS256", "ES256", "ES384"})


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: Environment = "local"
    # Runtime (non-owner) role DSN. Never the migration/owner credential.
    database_url: SecretStr | None = None
    db_pool_min_size: int = Field(default=1, ge=1, le=100)
    db_pool_max_size: int = Field(default=10, ge=1, le=500)
    hold_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    # External OIDC provider (ADR-0007). All three or none.
    auth_issuer: str | None = None
    auth_audience: str | None = None
    auth_jwks_url: str | None = None
    auth_algorithms: tuple[str, ...] = ("RS256", "ES256")
    auth_leeway_seconds: int = Field(default=30, ge=0, le=300)

    @model_validator(mode="after")
    def _pool_bounds(self) -> Self:
        if self.db_pool_max_size < self.db_pool_min_size:
            raise ValueError("GBA_DB_POOL_MAX_SIZE must be >= GBA_DB_POOL_MIN_SIZE")
        return self

    @model_validator(mode="after")
    def _auth_settings(self) -> Self:
        values = (self.auth_issuer, self.auth_audience, self.auth_jwks_url)
        if any(values) and not all(values):
            raise ValueError("GBA_AUTH_ISSUER, GBA_AUTH_AUDIENCE and GBA_AUTH_JWKS_URL go together")
        if not self.auth_algorithms or set(self.auth_algorithms) - _AUTH_ALGORITHMS:
            raise ValueError(f"GBA_AUTH_ALGORITHMS must be a subset of {sorted(_AUTH_ALGORITHMS)}")
        insecure_ok = self.environment in ("local", "test")
        for url in (self.auth_issuer, self.auth_jwks_url):
            if (
                url
                and not url.startswith("https://")
                and not (insecure_ok and url.startswith(("http://localhost", "http://127.0.0.1")))
            ):
                raise ValueError("identity provider URLs must use https")
        return self

    @property
    def auth_configured(self) -> bool:
        return self.auth_issuer is not None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        env = os.environ if environ is None else environ
        values: dict[str, object] = {
            field: env[key] for key, field in _ENV_FIELDS.items() if env.get(key)
        }
        for field in _LIST_FIELDS & values.keys():
            values[field] = tuple(v.strip() for v in str(values[field]).split(",") if v.strip())
        return cls.model_validate(values)


class UnsafeEnvironmentError(RuntimeError):
    """Raised when the app is started somewhere M1 is not allowed to run."""


def assert_environment_allowed(settings: Settings) -> None:
    # M1 has no authentication or rate limiting; it must not serve real traffic.
    if settings.environment in ("staging", "production"):
        raise UnsafeEnvironmentError(
            f"refusing to start in {settings.environment!r}: M1 has no authentication, "
            "rate limiting or payment verification"
        )
