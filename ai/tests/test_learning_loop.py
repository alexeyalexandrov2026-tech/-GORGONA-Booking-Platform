"""The AI learning loop executes end to end on real PostgreSQL + pgvector.

ingest -> review -> dataset_cut -> train -> evaluate -> verify, then an explicit
promotion; a worse candidate is rejected and never replaces the promoted model;
non-production data never trains; retrieval embeddings refresh; tenants are isolated.
"""

import json
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from gorgona_ai import pipeline
from gorgona_ai.cli import main
from gorgona_ai.db import tenant_transaction
from gorgona_ai.evidence import review
from gorgona_ai.intake import ListSource, pump
from gorgona_ai.jobs.runtime import drain, enqueue
from tests.conftest import AiDatabase, envelope

TRAIN = [
    ("gel manicure please", "GEL_MANICURE"),
    ("I want gel nails on my hands", "GEL_MANICURE"),
    ("shellac manicure for my fingernails", "GEL_MANICURE"),
    ("pedicure for my feet", "PEDICURE"),
    ("spa pedicure with foot soak", "PEDICURE"),
    ("toenail pedicure appointment", "PEDICURE"),
    ("lash lift and tint", "LASH_LIFT"),
    ("eyelash lift please", "LASH_LIFT"),
    ("lift my lashes", "LASH_LIFT"),
]
BENCHMARK = [
    ("can I get a gel manicure", "GEL_MANICURE"),
    ("gel polish on fingernails", "GEL_MANICURE"),
    ("book a pedicure", "PEDICURE"),
    ("foot spa and toenails", "PEDICURE"),
    ("lash lift appointment", "LASH_LIFT"),
    ("eyelash tint and lift", "LASH_LIFT"),
]
CATALOG = {
    "services": [
        {"service_code": "GEL_MANICURE", "service_name": "FAKE Gel manicure",
         "description": "Gel polish manicure for fingernails"},
        {"service_code": "PEDICURE", "service_name": "FAKE Spa pedicure",
         "description": "Foot soak and toenail care"},
        {"service_code": "LASH_LIFT", "service_name": "FAKE Lash lift",
         "description": "Eyelash lift and tint"},
    ]
}  # fmt: skip


def _ingest_all(conn: psycopg.Connection, raws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for raw in raws:
        enqueue(conn, "ingest", f"ingest:{uuid4()}", payload=raw)
    drain(conn, pipeline.HANDLERS, worker="test", kinds=["ingest"])
    rows = conn.execute(
        "select result from ai.job_runs where kind = 'ingest' order by created_at"
    ).fetchall()
    return [r[0] for r in rows][-len(raws) :]


def _corrections(
    tenant: UUID, items: list[tuple[str, str]], scope: str, env: str = "production"
) -> list[dict[str, Any]]:
    return [
        envelope(
            tenant,
            "correction",
            {"text": t, "corrected_output": label, "original_output": None},
            env=env,
            scope=scope,
        )
        for t, label in items
    ]


def _approve_all(conn: psycopg.Connection, tenant: UUID) -> None:
    with tenant_transaction(conn, tenant):
        ids = [r[0] for r in conn.execute(
            "select id from ai.evidence where review_status = 'pending'").fetchall()]  # fmt: skip
    for evidence_id in ids:
        review(conn, tenant, evidence_id, "approved", "fake-reviewer")


def _tick(conn: psycopg.Connection) -> dict[str, int]:
    conn.execute("update ai.job_runs set not_before = now() where status = 'queued'")
    enqueue(conn, "dataset_cut", f"dataset_cut:{uuid4()}")
    enqueue(conn, "rag_refresh", f"rag_refresh:{uuid4()}")
    return drain(conn, pipeline.HANDLERS, worker="test")


def _models(conn: psycopg.Connection, tenant: UUID) -> list[tuple[UUID, str]]:
    with tenant_transaction(conn, tenant):
        return [(r[0], r[1]) for r in conn.execute(
            "select id, status from ai.models order by created_at").fetchall()]  # fmt: skip


def test_full_learning_loop_and_explicit_promotion(conn: psycopg.Connection) -> None:
    tenant = uuid4()
    raws = [
        envelope(tenant, "catalog", CATALOG),
        *_corrections(tenant, TRAIN, "training"),
        *_corrections(tenant, BENCHMARK, "evaluation"),
    ]
    results = _ingest_all(conn, raws)
    assert all(r["outcome"] == "stored" for r in results)
    # Nothing learns before human review.
    assert _tick(conn).get("dead", 0) == 0
    assert _models(conn, tenant) == []

    _approve_all(conn, tenant)
    counts = _tick(conn)
    assert counts.get("dead", 0) == 0
    [(model_id, status)] = _models(conn, tenant)
    assert status == "verified", "a verified candidate is NOT promoted automatically"
    assert pipeline.promoted_model(conn, tenant) is None

    with tenant_transaction(conn, tenant):
        (metrics,) = conn.execute(
            "select metrics from ai.evaluations where model_id = %s", (model_id,)
        ).fetchone()  # type: ignore[misc]
        (checks,) = conn.execute(
            "select checks from ai.verifications where model_id = %s", (model_id,)
        ).fetchone()  # type: ignore[misc]
    assert metrics["items"] == len(BENCHMARK)
    assert metrics["accuracy"] >= pipeline.MIN_ACCURACY
    assert all(c["passed"] for c in checks.values())

    pipeline.promote(conn, tenant, model_id, "fake-operator", "first verified intent model")
    assert pipeline.promoted_model(conn, tenant) == model_id
    with tenant_transaction(conn, tenant):
        label, share = pipeline.predict(conn, model_id, "gel manicure for my nails")
    assert label == "GEL_MANICURE"
    assert share > 0.5

    # A second dataset version with mislabelled reviewed corrections trains a worse
    # candidate; the gate rejects it and production keeps the promoted model.
    bad = [(f"pedicure foot soak toenail {i}", "LASH_LIFT") for i in range(12)]
    _ingest_all(conn, _corrections(tenant, bad, "training"))
    _approve_all(conn, tenant)
    _tick(conn)
    models = dict(_models(conn, tenant))
    assert len(models) == 2
    candidate = next(m for m in models if m != model_id)
    assert models[candidate] == "rejected"
    assert pipeline.promoted_model(conn, tenant) == model_id
    with tenant_transaction(conn, tenant):
        failed = conn.execute(
            "select checks from ai.verifications where model_id = %s", (candidate,)
        ).fetchone()[0]  # type: ignore[index]
    assert failed["no_regression"]["passed"] is False

    # Rejected candidates cannot be promoted; rollback needs a previous model.
    with pytest.raises(PermissionError):
        pipeline.promote(conn, tenant, candidate, "x", "y")
    with pytest.raises(LookupError):
        pipeline.rollback(conn, tenant, "x", "y")


def test_non_production_and_unreviewed_data_never_enter_a_dataset(conn: psycopg.Connection) -> None:
    tenant = uuid4()
    _ingest_all(conn, _corrections(tenant, TRAIN, "training", env="staging")
                + _corrections(tenant, BENCHMARK, "evaluation", env="verification"))  # fmt: skip
    _approve_all(conn, tenant)
    _tick(conn)
    with tenant_transaction(conn, tenant):
        scopes = conn.execute("select distinct learning_scope from ai.evidence").fetchall()
        datasets = conn.execute("select count(*) from ai.datasets").fetchone()
    assert scopes == [("none",)]
    assert datasets == (0,)
    assert _models(conn, tenant) == []


def test_benchmark_leakage_is_rejected_by_the_gate(conn: psycopg.Connection) -> None:
    tenant = uuid4()
    # Identical evidence is stored once (content-hash dedupe), so an exact copy cannot leak.
    # The same utterance with a different original output is distinct evidence: the gate
    # must catch it by text overlap.
    text, label = BENCHMARK[0]
    leak_payload = {"text": text, "corrected_output": label, "original_output": "PEDICURE"}
    leak = envelope(tenant, "correction", leak_payload, scope="training")
    raws = [
        *_corrections(tenant, TRAIN, "training"),
        leak,
        *_corrections(tenant, BENCHMARK, "evaluation"),
    ]
    assert all(r["outcome"] == "stored" for r in _ingest_all(conn, raws))
    _approve_all(conn, tenant)
    _tick(conn)
    [(model_id, status)] = _models(conn, tenant)
    assert status == "rejected"
    with tenant_transaction(conn, tenant):
        checks = conn.execute(
            "select checks from ai.verifications where model_id = %s", (model_id,)
        ).fetchone()[0]  # type: ignore[index]
    assert checks["no_benchmark_leakage"]["passed"] is False


def test_rag_refresh_embeds_updates_and_searches_within_the_tenant(
    conn: psycopg.Connection,
) -> None:
    a, b = uuid4(), uuid4()
    _ingest_all(conn, [envelope(a, "catalog", CATALOG),
                       envelope(b, "catalog", {"services": [{"service_code": "B_ONLY", "service_name": "Tenant B secret service"}]})])  # noqa: E501  # fmt: skip
    _tick(conn)
    hits = pipeline.search(conn, a, "eyelash tint")
    assert hits[0][0] == "LASH_LIFT"
    assert all(ref != "B_ONLY" for ref, _, _ in hits), "no cross-tenant retrieval"
    with tenant_transaction(conn, b):
        assert conn.execute("select count(*) from ai.documents").fetchone() == (1,)
    # Re-running is a no-op; a changed catalog re-embeds only what changed and drops removals.
    assert conn.execute("select count(*) from ai.embeddings").fetchone() == (0,), (
        "RLS hides rows without tenant context"
    )
    changed = {"services": [CATALOG["services"][0] | {"description": "Gel manicure with nail art"},
                            CATALOG["services"][1]]}  # fmt: skip
    _ingest_all(conn, [envelope(a, "catalog", changed)])
    counts = _tick(conn)
    assert counts.get("dead", 0) == 0
    result = conn.execute(
        "select result from ai.job_runs where kind = 'rag_refresh' order by created_at desc limit 1"
    ).fetchone()[0]  # type: ignore[index]
    assert result["embedded"] == 1
    refs = {ref for ref, _, _ in pipeline.search(conn, a, "nail art", k=5)}
    assert refs == {"GEL_MANICURE", "PEDICURE"}


def test_ingest_rules_on_real_storage(conn: psycopg.Connection) -> None:
    tenant = uuid4()
    raw = _corrections(
        tenant, [("call me on +1 555 010 2345 for gel", "GEL_MANICURE")], "training"
    )[0]
    first, duplicate = _ingest_all(conn, [raw, raw])
    assert first["outcome"] == "stored"
    assert first["redactions"] == 1
    assert duplicate["outcome"] == "duplicate"
    rejected = _ingest_all(
        conn,
        [envelope(tenant, "correction", {"text": "x", "corrected_output": "OK", "phone": "1"})],
    )
    assert rejected == [{"outcome": "rejected", "reason": "contact_data_field"}]
    with tenant_transaction(conn, tenant):
        rows = conn.execute("select payload->>'text', review_status from ai.evidence").fetchall()
    assert rows == [("call me on [redacted] for gel", "pending")]


def test_queue_intake_is_durable_idempotent_and_dead_letters_garbage(
    conn: psycopg.Connection,
) -> None:
    tenant = uuid4()
    body = json.dumps(envelope(tenant, "interaction", {"text": "hi"})).encode()
    source = ListSource([body, body, b"not json", b"[1, 2]"])
    counts = pump(conn, source)
    assert counts == {"queued": 1, "duplicate": 1, "dead_lettered": 2}
    assert len(source.completed) == 2
    assert [r for _, r in source.dead] == ["malformed_json"] * 2
    assert drain(conn, pipeline.HANDLERS, worker="t", kinds=["ingest"]) == {"succeeded": 1}


def test_health_and_cli_entrypoints(
    ai_db: AiDatabase, conn: psycopg.Connection, monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.setenv("GAI_DATABASE_URL", ai_db.worker_dsn)
    assert main(["health"]) == 1, "never ran: periodic jobs are stale"
    assert main(["tick"]) == 0
    capsys.readouterr()
    assert main(["health"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["healthy"] is True
    assert report["runs"]["dataset_cut:succeeded"] == 1
    # A dead run makes the plane unhealthy.
    enqueue(conn, "train", "train:bogus", payload={"dataset_id": "nope"}, tenant_id=uuid4())
    assert main(["run", "--kinds", "train"]) == 0
    assert main(["health"]) == 1
