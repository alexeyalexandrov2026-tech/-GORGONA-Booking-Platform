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
}


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: Environment = "local"
    # Runtime (non-owner) role DSN. Never the migration/owner credential.
    database_url: SecretStr | None = None
    db_pool_min_size: int = Field(default=1, ge=1, le=100)
    db_pool_max_size: int = Field(default=10, ge=1, le=500)
    hold_ttl_seconds: int = Field(default=600, ge=60, le=3600)

    @model_validator(mode="after")
    def _pool_bounds(self) -> Self:
        if self.db_pool_max_size < self.db_pool_min_size:
            raise ValueError("GBA_DB_POOL_MAX_SIZE must be >= GBA_DB_POOL_MIN_SIZE")
        return self

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        env = os.environ if environ is None else environ
        values = {field: env[key] for key, field in _ENV_FIELDS.items() if env.get(key)}
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
