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


_STAGING_READY = {
    "GBA_ENV": "staging",
    "GBA_AUTH_ISSUER": "https://idp.example.test/",
    "GBA_AUTH_AUDIENCE": "api://gorgona-staging",
    "GBA_AUTH_JWKS_URL": "https://idp.example.test/jwks",
    "GBA_TRUSTED_PROXY": "azure_front_door",
    "GBA_FRONT_DOOR_ID": "a0a0a0a0-bbbb-cccc-dddd-e1e1e1e1e1e1",
}


def test_staging_starts_only_behind_front_door_with_oidc() -> None:
    assert_environment_allowed(Settings.from_env(_STAGING_READY))


@pytest.mark.parametrize(
    ("drop", "reason"),
    [
        (("GBA_AUTH_ISSUER", "GBA_AUTH_AUDIENCE", "GBA_AUTH_JWKS_URL"), "OIDC"),
        (("GBA_TRUSTED_PROXY", "GBA_FRONT_DOOR_ID"), "Front Door"),
    ],
)
def test_staging_is_refused_without_each_condition(drop: tuple[str, ...], reason: str) -> None:
    environ = {k: v for k, v in _STAGING_READY.items() if k not in drop}
    with pytest.raises(UnsafeEnvironmentError, match=reason):
        assert_environment_allowed(Settings.from_env(environ))


_PRODUCTION_READY = {**_STAGING_READY, "GBA_ENV": "production"}


def test_production_is_gated_without_an_explicit_authorization() -> None:
    # Every bridge condition is met, but the cutover was not authorized.
    with pytest.raises(UnsafeEnvironmentError, match="production authorization"):
        assert_environment_allowed(Settings.from_env(_PRODUCTION_READY))


def test_authorized_production_runs_only_on_the_bridge_topology() -> None:
    authorized = {**_PRODUCTION_READY, "GBA_PRODUCTION_AUTHORIZATION": "PA-20261015-cutover"}
    assert_environment_allowed(Settings.from_env(authorized))
    for drop, reason in (
        (("GBA_TRUSTED_PROXY", "GBA_FRONT_DOOR_ID"), "Front Door"),
        (("GBA_AUTH_ISSUER", "GBA_AUTH_AUDIENCE", "GBA_AUTH_JWKS_URL"), "OIDC"),
    ):
        environ = {k: v for k, v in authorized.items() if k not in drop}
        with pytest.raises(UnsafeEnvironmentError, match=reason):
            assert_environment_allowed(Settings.from_env(environ))


@pytest.mark.parametrize(
    "environ",
    [
        {**_STAGING_READY, "GBA_PRODUCTION_AUTHORIZATION": "PA-20261015-cutover"},
        {**_PRODUCTION_READY, "GBA_PRODUCTION_AUTHORIZATION": "yes"},
        {**_PRODUCTION_READY, "GBA_PRODUCTION_AUTHORIZATION": "PA-2026-cutover"},
    ],
)
def test_production_authorization_is_strict(environ: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(environ)
