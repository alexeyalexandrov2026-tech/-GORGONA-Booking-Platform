import pytest
from pydantic import ValidationError

from gorgona_booking.config import Settings, UnsafeEnvironmentError, assert_environment_allowed


def test_from_env_reads_only_known_variables() -> None:
    settings = Settings.from_env(
        {
            "GBA_ENV": "test",
            "GBA_DATABASE_URL": "postgresql://gba_app:secret@127.0.0.1:55432/gba",
            "GBA_DB_POOL_MAX_SIZE": "20",
            "GBA_HOLD_TTL_SECONDS": "300",
            "UNRELATED": "ignored",
        }
    )
    assert settings.environment == "test"
    assert settings.db_pool_max_size == 20
    assert settings.hold_ttl_seconds == 300
    assert settings.database_url is not None
    assert "secret" not in repr(settings)


def test_empty_values_fall_back_to_defaults() -> None:
    settings = Settings.from_env({"GBA_DATABASE_URL": "", "GBA_HOLD_TTL_SECONDS": ""})
    assert settings.database_url is None
    assert settings.hold_ttl_seconds == 600


@pytest.mark.parametrize(
    "environ",
    [
        {"GBA_DB_POOL_MIN_SIZE": "5", "GBA_DB_POOL_MAX_SIZE": "2"},
        {"GBA_HOLD_TTL_SECONDS": "5"},
        {"GBA_ENV": "prod"},
    ],
)
def test_invalid_settings_are_rejected(environ: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(environ)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_m1_refuses_to_run_outside_development(environment: str) -> None:
    with pytest.raises(UnsafeEnvironmentError):
        assert_environment_allowed(Settings.from_env({"GBA_ENV": environment}))
