"""Bridge acceptance runner logic against a simulated Front Door deployment (no network)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from tools import bridge_acceptance as ba

FD = "https://gorgona-stg.example.azurefd.net"
SECOND = "https://second.example.azurefd.net"
ORIGIN = "api.internal.example.azurecontainerapps.io"
KA = "https://ka-nails.example.test"
APP = {"x-request-id": "r"}
BOOT = {"name": "FAKE salon", "locations": [{"id": "loc"}], "variants": [{"id": "var"}]}
FDID = "a0a0a0a0-bbbb-cccc-dddd-e1e1e1e1e1e1"


def fake_front_door(
    *, origin_reachable: bool = False, waf: bool = True, rate_limit_after: int = 3
) -> httpx.MockTransport:
    writes = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == ORIGIN:
            if origin_reachable:
                return httpx.Response(
                    404, headers=APP, json={"error": {"code": "TENANT_NOT_FOUND"}}
                )
            raise httpx.ConnectError("refused", request=request)
        second = url.host.startswith("second")
        if waf and "%3Cscript%3E" in str(url):
            return httpx.Response(403, text="blocked by WAF")
        if url.path == "/v1/customer/bootstrap":
            name = "FAKE second salon" if second else "FAKE salon"
            headers = {**APP, "content-security-policy": "frame-ancestors 'none'"}
            return httpx.Response(200, headers=headers, json={**BOOT, "name": name})
        if url.path == "/v1/customer/availability":
            slots = [{"resource_id": "r1", "start_at": "t"}]
            return httpx.Response(200, headers=APP, json={"slots": slots})
        if url.path == "/v1/customer/holds":
            if request.content == b"{}":
                writes["n"] += 1
                if writes["n"] > rate_limit_after:
                    return httpx.Response(403, text="rate limited")
                return httpx.Response(422, headers=APP, json={"error": {"code": "VALIDATION"}})
            return httpx.Response(201, headers=APP, json={"booking_id": "b1"})
        if url.path.endswith("/confirm"):
            if second:
                return httpx.Response(404, headers=APP, json={"error": {"code": "NOT_FOUND"}})
            return httpx.Response(200, headers=APP, json={"status": "CONFIRMED"})
        if url.path == "/v1/me":
            ok = request.headers.get("authorization") == "Bearer good"
            return httpx.Response(200 if ok else 401, headers=APP, json={})
        if url.path == "/book/":
            csp = f"frame-ancestors 'self' {KA}"
            headers = {**APP, "content-type": "text/html", "content-security-policy": csp}
            return httpx.Response(200, headers=headers, text="<html></html>")
        return httpx.Response(404, headers=APP)

    return httpx.MockTransport(handler)


def _args(**overrides: Any) -> Any:
    args = ba.parse_args(
        [
            "http",
            *("--front-door-url", FD, "--second-tenant-url", SECOND),
            *("--origin-fqdn", ORIGIN, "--postgres-fqdn", "db.example"),
            *("--authorized-origin", KA, "--day", "2026-10-20"),
            *("--staff-token-env", "BRIDGE_TOKEN", "--rate-limit-probe", "10"),
            *("--image-digest", "sha256:abc", "--allow-remote"),
        ]
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _azure_doc() -> dict[str, Any]:
    env = [
        {"name": "GBA_TRUSTED_PROXY", "value": "azure_front_door"},
        {"name": "GBA_FRONT_DOOR_ID", "value": FDID},
        {"name": "GBA_DATABASE_URL", "secretRef": "db"},
    ]
    app = {
        "properties": {
            "configuration": {
                "secrets": [{"name": "db", "keyVaultUrl": "https://kv/secrets/db"}],
                "registries": [{"server": "acr", "identity": "/id"}],
            },
            "template": {"containers": [{"env": env}]},
        }
    }
    gates = ba.azure_checks(
        app,
        {"properties": {"publicNetworkAccess": "Disabled"}},
        {"properties": {"publicNetworkAccess": "Disabled"}},
        {"properties": {"network": {"publicNetworkAccess": "Disabled"}}},
        {"properties": {"policySettings": {"mode": "Prevention", "enabledState": "Enabled"}}},
        {"properties": {"frontDoorId": FDID}},
        [{"properties": {"privateLinkServiceConnectionState": {"status": "Approved"}}}],
        [{"properties": {"enabled": True}}],
        log_hits=4,
    )
    return ba.evidence("sha256:abc", "azure", gates)


def _drills() -> list[dict[str, Any]]:
    return [
        ba.evidence("sha256:abc", "record", {"rollback_recovery": [ba.check(c, True, "drill")]})
        for c in ("revision_rollback_drill", "postgres_pitr_drill")
    ]


def _run(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.MockTransport,
    pg: str = "connection timeout expired",
    **overrides: Any,
) -> dict[str, Any]:
    monkeypatch.setenv("BRIDGE_TOKEN", "good")
    return ba.run_http(_args(**overrides), transport=transport, pg_connect=lambda h, t: pg)


def test_a_correct_deployment_passes_every_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _run(monkeypatch, fake_front_door())
    merged = ba.merge([http, _azure_doc(), *_drills()])
    assert merged["summary"] == dict.fromkeys(ba.REQUIRED, "pass"), merged["summary"]
    assert merged["passed"] is True
    text = json.dumps(http)
    assert '"b1"' not in text  # no booking IDs in evidence
    assert "good" not in text  # no tokens in evidence


@pytest.mark.parametrize(
    ("transport", "pg", "gate"),
    [
        (fake_front_door(origin_reachable=True), "timeout", "direct_origin_bypass"),
        (fake_front_door(origin_reachable=True), "timeout", "private_origin"),
        (fake_front_door(), 'password authentication failed for user "p"', "private_postgresql"),
        (fake_front_door(waf=False), "timeout", "waf_rate_limiting"),
        (fake_front_door(rate_limit_after=100), "timeout", "waf_rate_limiting"),
    ],
)
def test_each_bypass_or_missing_control_fails_its_gate(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport, pg: str, gate: str
) -> None:
    merged = ba.merge([_run(monkeypatch, transport, pg), _azure_doc(), *_drills()])
    assert merged["summary"][gate] == "fail"
    assert merged["passed"] is False


def test_missing_inputs_block_instead_of_passing(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _run(
        monkeypatch,
        fake_front_door(),
        staff_token_env=None,
        authorized_origin=None,
        second_tenant_url=None,
        rate_limit_probe=0,
    )
    summary = ba.merge([http, _azure_doc()])["summary"]
    for gate in (
        "oidc",
        "authorized_ka_origin",
        "tenant_isolation",
        "waf_rate_limiting",
        "rollback_recovery",
    ):
        assert summary[gate] == "blocked", gate


def test_verify_requires_one_digest_and_every_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    docs = [_run(monkeypatch, fake_front_door()), _azure_doc(), *_drills()]
    paths = []
    for i, doc in enumerate(docs):
        path = tmp_path / f"e{i}.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        paths.append(str(path))
    assert ba.verify(paths, "sha256:abc")[0] is True
    assert ba.verify(paths, "sha256:other")[0] is False  # evidence is for another image
    assert ba.verify(paths[:-1], "sha256:abc")[0] is False  # PITR drill not recorded
    other = tmp_path / "other.json"
    other.write_text(json.dumps({**docs[1], "image_digest": "sha256:zzz"}), encoding="utf-8")
    with pytest.raises(ValueError, match="digests"):
        ba.verify([*paths, str(other)], "sha256:abc")


def test_remote_target_needs_explicit_opt_in() -> None:
    argv = [
        "http",
        *("--front-door-url", FD, "--origin-fqdn", ORIGIN, "--postgres-fqdn", "db"),
        *("--day", "2026-10-20", "--image-digest", "sha256:abc"),
    ]
    with pytest.raises(SystemExit, match="allow-remote"):
        ba.main(argv)
