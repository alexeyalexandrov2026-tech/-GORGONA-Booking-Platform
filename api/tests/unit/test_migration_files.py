from pathlib import Path

import pytest

from gorgona_booking.db.bootstrap import BootstrapError, BootstrapSpec
from gorgona_booking.db.migrate import MigrationError, load_migrations


def test_packaged_migrations_are_contiguous_and_checksummed() -> None:
    migrations = load_migrations()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert migrations[0].name == "tenancy"
    assert all(len(m.checksum) == 64 for m in migrations)


def test_migrations_never_disable_or_bypass_rls() -> None:
    for migration in load_migrations():
        text = migration.sql.lower()
        assert "disable row level security" not in text, migration.name
        assert "no force row level security" not in text, migration.name
        assert "bypassrls" not in text, migration.name
        assert "security definer" not in text, migration.name


def test_every_created_table_enables_and_forces_rls() -> None:
    for migration in load_migrations():
        text = migration.sql.lower()
        for chunk in text.split("create table ")[1:]:
            table = chunk.split(maxsplit=1)[0].strip("(")
            assert f"alter table {table} enable row level security" in text, table
            assert f"alter table {table} force row level security" in text, table


@pytest.mark.parametrize(
    ("files", "message"),
    [
        (["0001_ok.sql", "0003_gap.sql"], "contiguous"),
        (["1_bad_name.sql"], "invalid migration filename"),
        (["0002_starts_late.sql"], "contiguous"),
    ],
)
def test_invalid_migration_sets_are_rejected(
    tmp_path: Path, files: list[str], message: str
) -> None:
    for name in files:
        (tmp_path / name).write_text("select 1;\n", encoding="utf-8")
    with pytest.raises(MigrationError, match=message):
        load_migrations(tmp_path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"owner_role": "gba_runtime"},
        {"app_role": "gba_owner"},
        {"database": "Bad-Name"},
        {"app_password": "short"},
    ],
)
def test_bootstrap_spec_validation(overrides: dict[str, str]) -> None:
    values = {
        "database": "gorgona_booking",
        "owner_role": "gba_owner",
        "owner_password": "owner-password-long",
        "app_role": "gba_app",
        "app_password": "app-password-long",
    } | overrides
    with pytest.raises(BootstrapError):
        BootstrapSpec(**values).validate()
