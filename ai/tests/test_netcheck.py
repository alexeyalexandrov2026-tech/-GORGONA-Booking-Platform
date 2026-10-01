"""Network preflight logic with injected DNS/TCP (no network)."""

import pytest

from gorgona_ai.netcheck import Target, check, parse_targets


def _resolver(table: dict[str, list[str]]):  # type: ignore[no-untyped-def]
    def resolve(host: str, port: int) -> list[str]:
        if host not in table:
            raise OSError("NXDOMAIN")
        return table[host]

    return resolve


def _ok(address: str, port: int, timeout: float) -> None:
    return None


def test_parse_targets() -> None:
    assert parse_targets("db.example:5432:private, sb.example:5671:public") == [
        Target("db.example", 5432, "private"),
        Target("sb.example", 5671, "public"),
    ]
    with pytest.raises(ValueError, match="bad target"):
        parse_targets("db.example:5432:maybe")


def test_private_target_resolving_publicly_fails_closed() -> None:
    report = check(
        [Target("db.example", 5432, "private")],
        {},
        resolve=_resolver({"db.example": ["20.1.2.3"]}),
        connect=_ok,
    )
    assert report["ok"] is False
    assert report["targets"][0]["resolved"] == "public"
    assert "non-private" in report["targets"][0]["error"]


def test_all_dependencies_reachable_and_secret_presence_only() -> None:
    env = {"GAI_DATABASE_URL": "postgresql://user:SECRETVALUE@db/x"}
    report = check(
        [Target("db.example", 5432, "private"), Target("sb.example", 5671, "public")],
        env,
        ["GAI_DATABASE_URL"],
        resolve=_resolver({"db.example": ["10.30.2.4"], "sb.example": ["20.1.2.3"]}),
        connect=_ok,
    )
    assert report["ok"] is True
    assert report["secrets_present"] == {"GAI_DATABASE_URL": True}
    assert "SECRETVALUE" not in str(report)


def test_dns_tcp_and_missing_secret_failures_are_reported() -> None:
    def refuse(address: str, port: int, timeout: float) -> None:
        raise ConnectionRefusedError

    report = check(
        [Target("gone.example", 443, "private"), Target("kv.example", 443, "private")],
        {},
        ["GAI_DATABASE_URL"],
        resolve=_resolver({"kv.example": ["10.30.2.5"]}),
        connect=refuse,
    )
    assert report["ok"] is False
    assert [t["error"] for t in report["targets"]] == [
        "dns: OSError",
        "tcp: ConnectionRefusedError",
    ]
    assert report["secrets_present"] == {"GAI_DATABASE_URL": False}
