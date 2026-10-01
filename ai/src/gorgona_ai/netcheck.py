"""Network preflight for a jobs environment: DNS + TCP to every dependency.

Each target is `host:port:expect`, where expect is `private` (the name must resolve
only to private addresses, i.e. through a linked private DNS zone to a private
endpoint; a public answer fails, so there is no silent public fallback) or `public`
(a dependency that is reached on its public endpoint by design, reported as such).
Required secret variables are checked for presence only; values are never read out.
"""

import ipaddress
import socket
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

type Expect = Literal["private", "public"]
type Resolver = Callable[[str, int], list[str]]
type Connector = Callable[[str, int, float], None]


@dataclass(frozen=True, slots=True)
class Target:
    host: str
    port: int
    expect: Expect


def parse_targets(spec: str) -> list[Target]:
    targets = []
    for item in filter(None, (part.strip() for part in spec.split(","))):
        host, port, expect = item.rsplit(":", 2)
        if expect not in ("private", "public") or not host:
            raise ValueError(f"bad target {item!r}: use host:port:private|public")
        targets.append(Target(host, int(port), expect))  # type: ignore[arg-type]
    return targets


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _connect(address: str, port: int, timeout: float) -> None:
    with socket.create_connection((address, port), timeout=timeout):
        pass


def check(
    targets: Iterable[Target],
    environ: Mapping[str, str],
    required_secrets: Iterable[str] = (),
    *,
    resolve: Resolver = _resolve,
    connect: Connector = _connect,
    timeout: float = 5.0,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for t in targets:
        row: dict[str, Any] = {"host": t.host, "port": t.port, "expect": t.expect}
        try:
            addresses = resolve(t.host, t.port)
        except OSError as exc:
            results.append({**row, "ok": False, "error": f"dns: {type(exc).__name__}"})
            continue
        private = [ipaddress.ip_address(a).is_private for a in addresses]
        row["addresses"] = addresses
        row["resolved"] = "private" if all(private) else "public" if not any(private) else "mixed"
        if t.expect == "private" and row["resolved"] != "private":
            results.append({**row, "ok": False, "error": "resolved to a non-private address"})
            continue
        try:
            connect(addresses[0], t.port, timeout)
        except OSError as exc:
            results.append({**row, "ok": False, "error": f"tcp: {type(exc).__name__}"})
            continue
        results.append({**row, "ok": True})
    secrets = {name: bool(environ.get(name)) for name in required_secrets}
    return {
        "ok": all(r["ok"] for r in results) and all(secrets.values()),
        "targets": results,
        "secrets_present": secrets,
    }
