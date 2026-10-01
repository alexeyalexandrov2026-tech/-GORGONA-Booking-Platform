"""Production-bridge acceptance: evidence that a deployed environment meets every gate.

Production is gated, not disabled: it is reached only through the topology that this
tool has proven on the production-parity staging environment (ADR-0012). Each gate is a
set of named checks; a gate passes only when every required check is present and ok.
Evidence comes from three sources and is merged per image digest:

  http    through Front Door (and, negatively, around it) from an outside client
  azure   read-only `az` inspection of the deployed resources and logs
  record  operator drills that change state (revision rollback, PITR restore); the
          tool never performs them, it only records their outcome

`verify` exits 0 only when all gates pass for one image digest. The gated production
promotion runs `verify` before anything else.

Safety: bookings are created only for a tenant whose public name starts with "FAKE ".
No customer data, tokens or booking IDs are written to the evidence.

    uv run python tools/bridge_acceptance.py http \\
        --front-door-url https://<ep>.azurefd.net \\
        --second-tenant-url https://<ep2>.azurefd.net \\
        --origin-fqdn <app>.<env>.azurecontainerapps.io \\
        --postgres-fqdn <server>.postgres.database.azure.com \\
        --authorized-origin https://<ka-site> --day 2026-10-20 \\
        --image-digest sha256:... --allow-remote --out http.json
"""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

FAKE_PREFIX = "FAKE "
DETAILS = {
    "name": "FAKE Bridge Guest",
    "email": "fake-bridge@example.test",
    "phone": "+1 555 010 0000",
    "accept_policy": True,
}

# Gate -> required checks. A gate with any check missing is "blocked", never "pass".
REQUIRED: dict[str, frozenset[str]] = {
    "azure_e2e_booking": frozenset({"bootstrap_fake_tenant", "availability", "hold", "confirm"}),
    "oidc": frozenset(
        {
            "staff_api_rejects_anonymous",
            "staff_api_rejects_invalid_token",
            "staff_api_accepts_token",
        }
    ),
    "trusted_front_door_boundary": frozenset(
        {"spoofed_forwarded_host_ignored", "api_bound_to_front_door_id"}
    ),
    "waf_rate_limiting": frozenset(
        {"attack_signature_blocked", "write_rate_limited", "waf_policy_prevention"}
    ),
    "private_origin": frozenset(
        {
            "origin_not_publicly_reachable",
            "environment_public_access_disabled",
            "front_door_private_link_approved",
        }
    ),
    "private_postgresql": frozenset(
        {"postgres_not_publicly_reachable", "postgres_public_access_disabled"}
    ),
    "tenant_isolation": frozenset({"cross_tenant_booking_refused", "tenants_resolve_separately"}),
    "csp_frame_ancestors": frozenset(
        {"html_frame_ancestors_governed", "api_frame_ancestors_none", "unauthorized_origin_absent"}
    ),
    "authorized_ka_origin": frozenset({"ka_origin_authorized"}),
    "direct_origin_bypass": frozenset({"direct_origin_forged_headers_refused"}),
    "secrets_managed_identity": frozenset(
        {"no_plaintext_secrets", "registry_pull_by_identity", "key_vault_public_access_disabled"}
    ),
    "monitoring": frozenset({"run_requests_in_logs", "alerts_enabled"}),
    "rollback_recovery": frozenset({"revision_rollback_drill", "postgres_pitr_drill"}),
}

type Check = dict[str, Any]


def check(name: str, ok: bool | None, detail: str = "") -> Check:
    """ok=None means BLOCKED (an input was not supplied); never counted as a pass."""
    return {"check": name, "ok": ok, "detail": detail}


def from_app(response: httpx.Response) -> bool:
    """Every GORGONA response carries x-request-id; Front Door and platform pages do not."""
    return "x-request-id" in response.headers


def edge_blocked(response: httpx.Response) -> bool:
    return response.status_code == 403 and not from_app(response)


# ---------------------------------------------------------------- HTTP (through Front Door)


def booking_flow(client: httpx.Client, day: str, run_id: str) -> tuple[list[Check], str | None]:
    checks: list[Check] = []
    boot = client.get("/v1/customer/bootstrap", headers={"x-request-id": f"{run_id}-boot"})
    data = boot.json() if boot.status_code == 200 else {}
    fake = str(data.get("name", "")).startswith(FAKE_PREFIX)
    checks.append(
        check(
            "bootstrap_fake_tenant",
            boot.status_code == 200 and fake,
            f"status {boot.status_code}, FAKE tenant: {fake}",
        )
    )
    if not (boot.status_code == 200 and fake and data.get("variants") and data.get("locations")):
        return checks, None
    query = {
        "location_id": data["locations"][0]["id"],
        "variant_id": data["variants"][0]["id"],
        "add_on_ids": [],
        "day": day,
        "resource_id": None,
    }
    slots = client.post(
        "/v1/customer/availability", json=query, headers={"x-request-id": f"{run_id}-avail"}
    )
    found = slots.json().get("slots", []) if slots.status_code == 200 else []
    checks.append(
        check("availability", bool(found), f"status {slots.status_code}, slots {len(found)}")
    )
    if not found:
        return checks, None
    body = {k: v for k, v in query.items() if k != "day"}
    body.update({"resource_id": found[0]["resource_id"], "start_at": found[0]["start_at"]})
    token = secrets.token_urlsafe(32)
    held = client.post(
        "/v1/customer/holds",
        json=body,
        headers={
            "Booking-Token": token,
            "Idempotency-Key": f"{run_id}-hold",
            "x-request-id": f"{run_id}-hold",
        },
    )
    checks.append(check("hold", held.status_code == 201, f"status {held.status_code}"))
    if held.status_code != 201:
        return checks, None
    booking_id = str(held.json()["booking_id"])
    confirmed = client.post(
        f"/v1/customer/bookings/{booking_id}/confirm",
        json=DETAILS,
        headers={
            "Booking-Token": token,
            "Idempotency-Key": f"{run_id}-confirm",
            "x-request-id": f"{run_id}-confirm",
        },
    )
    ok = confirmed.status_code == 200 and confirmed.json().get("status") == "CONFIRMED"
    checks.append(check("confirm", ok, f"status {confirmed.status_code}"))
    return checks, booking_id if ok else None


def oidc_checks(client: httpx.Client, token: str | None) -> list[Check]:
    anonymous = client.get("/v1/me")
    invalid = client.get("/v1/me", headers={"Authorization": "Bearer not-a-token"})
    checks = [
        check(
            "staff_api_rejects_anonymous",
            anonymous.status_code == 401,
            f"status {anonymous.status_code}",
        ),
        check(
            "staff_api_rejects_invalid_token",
            invalid.status_code == 401,
            f"status {invalid.status_code}",
        ),
    ]
    if token is None:
        checks.append(check("staff_api_accepts_token", None, "no staff token supplied"))
    else:
        valid = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        checks.append(
            check(
                "staff_api_accepts_token", valid.status_code == 200, f"status {valid.status_code}"
            )
        )
    return checks


def boundary_checks(client: httpx.Client, expected_name: str) -> list[Check]:
    # Front Door overwrites X-Forwarded-Host and X-Azure-FDID; spoofed values must not
    # change the tenant the request resolves to.
    spoofed = client.get(
        "/v1/customer/bootstrap",
        headers={"X-Forwarded-Host": "attacker.example.test", "X-Azure-FDID": str(uuid.uuid4())},
    )
    name = spoofed.json().get("name") if spoofed.status_code == 200 else None
    return [
        check(
            "spoofed_forwarded_host_ignored",
            name == expected_name,
            f"status {spoofed.status_code}, same tenant: {name == expected_name}",
        )
    ]


def waf_checks(client: httpx.Client, rate_probe: int) -> list[Check]:
    attack = client.get(
        "/v1/customer/bootstrap", params={"q": "' OR 1=1 --<script>alert(1)</script>"}
    )
    checks = [
        check(
            "attack_signature_blocked",
            edge_blocked(attack),
            f"status {attack.status_code}, from app: {from_app(attack)}",
        )
    ]
    if rate_probe <= 0:
        checks.append(check("write_rate_limited", None, "rate-limit probe not enabled"))
        return checks
    blocked_after = None
    for i in range(rate_probe):
        r = client.post("/v1/customer/holds", json={}, headers={"Idempotency-Key": f"rl-{i}"})
        if edge_blocked(r):
            blocked_after = i + 1
            break
    checks.append(
        check(
            "write_rate_limited",
            blocked_after is not None,
            f"edge block after {blocked_after} writes"
            if blocked_after
            else f"no edge block in {rate_probe} writes",
        )
    )
    return checks


def csp_checks(client: httpx.Client, authorized: str | None, unauthorized: str) -> list[Check]:
    page = client.get("/book/")
    csp = page.headers.get("content-security-policy", "")
    api = client.get("/v1/customer/bootstrap")
    checks = [
        check(
            "html_frame_ancestors_governed",
            csp.startswith("frame-ancestors 'self'"),
            f"status {page.status_code}",
        ),
        check(
            "api_frame_ancestors_none",
            api.headers.get("content-security-policy") == "frame-ancestors 'none'",
        ),
        check("unauthorized_origin_absent", unauthorized not in csp.split()),
    ]
    ka = check("ka_origin_authorized", None, "no authorized origin supplied")
    if authorized:
        ka = check("ka_origin_authorized", authorized in csp.split(), "listed in frame-ancestors")
    return [*checks, ka]


def isolation_checks(
    first: httpx.Client, second: httpx.Client | None, booking_id: str | None, first_name: str
) -> list[Check]:
    if second is None or booking_id is None:
        reason = "no second FAKE tenant URL" if second is None else "no booking from gate 1"
        return [
            check("tenants_resolve_separately", None, reason),
            check("cross_tenant_booking_refused", None, reason),
        ]
    other = second.get("/v1/customer/bootstrap")
    other_name = other.json().get("name") if other.status_code == 200 else None
    separate = (
        bool(other_name) and str(other_name).startswith(FAKE_PREFIX) and other_name != first_name
    )
    cross = second.post(
        f"/v1/customer/bookings/{booking_id}/confirm",
        json=DETAILS,
        headers={"Booking-Token": secrets.token_urlsafe(32), "Idempotency-Key": "cross-tenant"},
    )
    return [
        check("tenants_resolve_separately", separate, f"status {other.status_code}"),
        check(
            "cross_tenant_booking_refused",
            cross.status_code == 404 and from_app(cross),
            f"status {cross.status_code}",
        ),
    ]


def direct_origin_checks(
    origin_fqdn: str, timeout: float, transport: httpx.BaseTransport | None, front_door_host: str
) -> list[Check]:
    """The container app must be unreachable except through Front Door's private endpoint."""
    url = f"https://{origin_fqdn}/v1/customer/bootstrap"
    headers = {"X-Azure-FDID": str(uuid.uuid4()), "X-Forwarded-Host": front_door_host}
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            r = client.get(url, headers=headers)
        reached = from_app(r)
        detail = f"status {r.status_code}, reached app: {reached}"
    except httpx.TransportError as exc:
        reached, detail = False, f"unreachable ({type(exc).__name__})"
    return [
        check("origin_not_publicly_reachable", not reached, detail),
        check("direct_origin_forged_headers_refused", not reached, detail),
    ]


def postgres_check(
    fqdn: str, timeout: float, connect: Callable[[str, float], str] | None = None
) -> Check:
    """Pass unless a login attempt from outside reaches PostgreSQL authentication."""
    probe = connect or _pg_probe
    outcome = probe(fqdn, timeout)
    reached_auth = "password authentication failed" in outcome
    return check("postgres_not_publicly_reachable", not reached_auth, outcome[:120])


def _pg_probe(fqdn: str, timeout: float) -> str:
    import psycopg

    try:
        socket.getaddrinfo(fqdn, 5432)
    except OSError as exc:
        return f"dns: {type(exc).__name__}"
    try:
        psycopg.connect(
            host=fqdn,
            port=5432,
            user="bridge_probe",
            password=secrets.token_hex(8),
            dbname="postgres",
            sslmode="require",
            connect_timeout=int(timeout),
        )
    except psycopg.Error as exc:
        return str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return "password authentication failed (unexpected success)"


def run_http(
    args: argparse.Namespace,
    transport: httpx.BaseTransport | None = None,
    pg_connect: Callable[[str, float], str] | None = None,
) -> dict[str, Any]:
    run_id = f"bridge-{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
    token = os.environ.get(args.staff_token_env) if args.staff_token_env else None
    fd_host = urlsplit(args.front_door_url).hostname or ""
    gates: dict[str, list[Check]] = {}
    with httpx.Client(
        base_url=args.front_door_url, timeout=args.timeout, transport=transport
    ) as fd:
        flow, booking_id = booking_flow(fd, args.day, run_id)
        gates["azure_e2e_booking"] = flow
        boot = fd.get("/v1/customer/bootstrap")
        name = boot.json().get("name", "") if boot.status_code == 200 else ""
        gates["oidc"] = oidc_checks(fd, token)
        gates["trusted_front_door_boundary"] = boundary_checks(fd, name)
        gates["waf_rate_limiting"] = waf_checks(fd, args.rate_limit_probe)
        csp = csp_checks(fd, args.authorized_origin, args.unauthorized_origin)
        gates["csp_frame_ancestors"] = csp[:3]
        gates["authorized_ka_origin"] = csp[3:]
        second = None
        if args.second_tenant_url:
            second = httpx.Client(
                base_url=args.second_tenant_url, timeout=args.timeout, transport=transport
            )
        try:
            gates["tenant_isolation"] = isolation_checks(fd, second, booking_id, name)
        finally:
            if second is not None:
                second.close()
    direct = direct_origin_checks(args.origin_fqdn, args.timeout, transport, fd_host)
    gates["private_origin"] = direct[:1]
    gates["direct_origin_bypass"] = direct[1:]
    gates["private_postgresql"] = [postgres_check(args.postgres_fqdn, args.timeout, pg_connect)]
    return evidence(args.image_digest, "http", gates, run_id=run_id)


# ---------------------------------------------------------------- Azure (read-only az)


def az_json(*args: str) -> Any:
    az = shutil.which("az")
    if az is None:
        raise SystemExit("the Azure CLI (az) is required for the azure checks")
    out = subprocess.run(  # noqa: S603 - fixed az CLI, read-only commands built in this module
        [az, *args, "-o", "json", "--only-show-errors"],
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    return json.loads(out.stdout) if out.stdout.strip() else None


def azure_checks(
    app: dict[str, Any],
    env: dict[str, Any],
    vault: dict[str, Any],
    server: dict[str, Any],
    waf: dict[str, Any],
    fd_profile: dict[str, Any],
    origin_links: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    log_hits: int,
) -> dict[str, list[Check]]:
    config = app["properties"]["configuration"]
    containers = app["properties"]["template"]["containers"]
    plain = [s["name"] for s in config.get("secrets", []) if not s.get("keyVaultUrl")]
    literal_secret_env = [
        e["name"]
        for c in containers
        for e in c.get("env", [])
        if "value" in e and any(k in e["name"] for k in ("PASSWORD", "DATABASE_URL", "SECRET"))
    ]
    registries = config.get("registries", [])
    env_vars = {e["name"]: e.get("value") for c in containers for e in c.get("env", [])}
    fdid = fd_profile["properties"]["frontDoorId"]
    approved = [
        p
        for p in origin_links
        if p.get("properties", {}).get("privateLinkServiceConnectionState", {}).get("status")
        == "Approved"
    ]
    return {
        "secrets_managed_identity": [
            check(
                "no_plaintext_secrets",
                not plain and not literal_secret_env,
                f"plain secrets: {len(plain)}, literal secret env: {len(literal_secret_env)}",
            ),
            check(
                "registry_pull_by_identity",
                bool(registries)
                and all(r.get("identity") and not r.get("passwordSecretRef") for r in registries),
            ),
            check(
                "key_vault_public_access_disabled",
                vault["properties"].get("publicNetworkAccess") == "Disabled",
            ),
        ],
        "trusted_front_door_boundary": [
            check(
                "api_bound_to_front_door_id",
                env_vars.get("GBA_TRUSTED_PROXY") == "azure_front_door"
                and str(env_vars.get("GBA_FRONT_DOOR_ID", "")).lower() == str(fdid).lower(),
            ),
        ],
        "waf_rate_limiting": [
            check(
                "waf_policy_prevention",
                waf["properties"]["policySettings"].get("mode") == "Prevention"
                and waf["properties"]["policySettings"].get("enabledState") == "Enabled",
            ),
        ],
        "private_origin": [
            check(
                "environment_public_access_disabled",
                env["properties"].get("publicNetworkAccess") == "Disabled",
            ),
            check(
                "front_door_private_link_approved",
                bool(approved),
                f"approved private endpoint connections: {len(approved)}",
            ),
        ],
        "private_postgresql": [
            check(
                "postgres_public_access_disabled",
                server["properties"].get("network", {}).get("publicNetworkAccess") == "Disabled",
            ),
        ],
        "monitoring": [
            check("run_requests_in_logs", log_hits > 0, f"log lines for this run: {log_hits}"),
            check(
                "alerts_enabled",
                bool(alerts) and all(a["properties"].get("enabled") for a in alerts),
                f"alert rules: {len(alerts)}",
            ),
        ],
    }


def run_azure(args: argparse.Namespace) -> dict[str, Any]:
    rg = args.resource_group
    app = az_json("containerapp", "show", "-g", rg, "-n", args.app)
    env = az_json("resource", "show", "--ids", app["properties"]["environmentId"])
    vault = az_json("keyvault", "show", "-g", rg, "-n", args.vault)
    server = az_json("postgres", "flexible-server", "show", "-g", rg, "-n", args.postgres)
    profiles = az_json("afd", "profile", "list", "-g", rg)
    fd_profile = profiles[0]
    waf = az_json("network", "front-door", "waf-policy", "list", "-g", rg)[0]
    links = az_json("network", "private-endpoint-connection", "list", "--id", env["id"]) or []
    alerts = az_json("monitor", "metrics", "alert", "list", "-g", rg) or []
    query = (
        f"ContainerAppConsoleLogs_CL | where TimeGenerated > ago(2h) "
        f"| where Log_s has '{args.run_id}' | count"
    )
    hits = az_json(
        "monitor",
        "log-analytics",
        "query",
        "-w",
        args.workspace_customer_id,
        "--analytics-query",
        query,
    )
    count = int(hits[0].get("Count", 0)) if hits else 0
    gates = azure_checks(app, env, vault, server, waf, fd_profile, links, alerts, count)
    return evidence(args.image_digest, "azure", gates, run_id=args.run_id)


# ---------------------------------------------------------------- evidence, merge, verify


def evidence(
    digest: str, source: str, gates: dict[str, list[Check]], **extra: Any
) -> dict[str, Any]:
    return {
        "image_digest": digest,
        "source": source,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **extra,
        "gates": gates,
    }


def merge(documents: Iterable[dict[str, Any]]) -> dict[str, Any]:
    docs = list(documents)
    digests = {d["image_digest"] for d in docs}
    if len(digests) != 1:
        raise ValueError(f"evidence covers {len(digests)} image digests; exactly one is required")
    gates: dict[str, list[Check]] = {name: [] for name in REQUIRED}
    for d in docs:
        for name, checks in d["gates"].items():
            gates.setdefault(name, []).extend(checks)
    summary = {name: gate_status(name, checks) for name, checks in gates.items()}
    return {
        "image_digest": digests.pop(),
        "sources": sorted({d["source"] for d in docs}),
        "gates": gates,
        "summary": summary,
        "passed": all(summary.get(name) == "pass" for name in REQUIRED),
    }


def gate_status(name: str, checks: list[Check]) -> str:
    if any(c["ok"] is False for c in checks):
        return "fail"
    ok = {c["check"] for c in checks if c["ok"] is True}
    return "pass" if REQUIRED.get(name, frozenset()) <= ok and ok else "blocked"


def verify(paths: list[str], digest: str) -> tuple[bool, dict[str, Any]]:
    docs = [json.loads(open(p, encoding="utf-8").read()) for p in paths]  # noqa: SIM115
    merged = merge(docs)
    return merged["passed"] and merged["image_digest"] == digest, merged


# ---------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    h = sub.add_parser("http", help="gates checked through (and around) Front Door")
    h.add_argument("--front-door-url", required=True)
    h.add_argument("--second-tenant-url", help="Front Door URL of a second FAKE tenant")
    h.add_argument("--origin-fqdn", required=True, help="container app FQDN (must be unreachable)")
    h.add_argument("--postgres-fqdn", required=True)
    h.add_argument("--authorized-origin", help="the KA Nails site origin approved for framing")
    h.add_argument("--unauthorized-origin", default="https://unapproved.example.test")
    h.add_argument("--day", required=True, help="ISO date with open FAKE availability")
    h.add_argument("--staff-token-env", help="env var holding a valid staff bearer token")
    h.add_argument("--rate-limit-probe", type=int, default=0, help="max writes for the probe")
    h.add_argument("--image-digest", required=True)
    h.add_argument("--timeout", type=float, default=10.0)
    h.add_argument("--allow-remote", action="store_true")
    h.add_argument("--out")
    a = sub.add_parser("azure", help="read-only az inspection")
    for flag in (
        "--resource-group",
        "--app",
        "--vault",
        "--postgres",
        "--workspace-customer-id",
        "--run-id",
        "--image-digest",
    ):
        a.add_argument(flag, required=True)
    a.add_argument("--out")
    r = sub.add_parser("record", help="record an operator drill outcome")
    r.add_argument("--gate", required=True, choices=sorted(REQUIRED))
    r.add_argument("--check", required=True)
    r.add_argument("--status", required=True, choices=["pass", "fail"])
    r.add_argument("--detail", required=True)
    r.add_argument("--image-digest", required=True)
    r.add_argument("--out", required=True)
    v = sub.add_parser("verify", help="exit 0 only if every gate passed for this digest")
    v.add_argument("--image-digest", required=True)
    v.add_argument("evidence", nargs="+")
    return parser.parse_args(argv)


def _write(doc: dict[str, Any], out: str | None) -> None:
    text = json.dumps(doc, indent=2)
    sys.stdout.write(text + "\n")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "http":
        host = urlsplit(args.front_door_url).hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1") and not args.allow_remote:
            raise SystemExit("a remote target needs --allow-remote (owner-approved staging window)")
        _write(run_http(args), args.out)
        return 0
    if args.command == "azure":
        _write(run_azure(args), args.out)
        return 0
    if args.command == "record":
        doc = evidence(
            args.image_digest,
            "record",
            {args.gate: [check(args.check, args.status == "pass", args.detail)]},
        )
        _write(doc, args.out)
        return 0
    ok, merged = verify(args.evidence, args.image_digest)
    sys.stdout.write(json.dumps({"passed": ok, "summary": merged["summary"]}, indent=2) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
