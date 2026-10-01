"""Learning loop stages (ADR-0013), each a durable, idempotent job.

dataset_cut (scheduled) -> train -> evaluate -> verify. Promotion is never a job: it is
an explicit, recorded operator decision with the previous model as rollback target.
rag_refresh (scheduled) keeps retrieval embeddings in step with knowledge documents.

The learned artifact is a per-tenant intent model: k-nearest-neighbour over embedded,
human-reviewed corrections (utterance -> service code). It proposes only; prices,
availability and bookings stay with the deterministic booking domain.
"""

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from gorgona_ai.db import tenant_transaction, vector_literal
from gorgona_ai.embedder import Embedder, HashingEmbedder
from gorgona_ai.evidence import ingest
from gorgona_ai.jobs.runtime import Handler, JobRun, PermanentJobError, enqueue

EMBEDDER: Embedder = HashingEmbedder()
K_NEIGHBOURS = 3
POLICY_VERSION = 1
MIN_BENCHMARK_ITEMS = 5
MIN_ACCURACY = 0.6
MAX_REGRESSION = 0.02
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_]{0,63}$")
SCHEDULE_BUCKET_MINUTES = 15


def _uuid(run: JobRun, key: str) -> UUID:
    try:
        return UUID(str(run.payload[key]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError(f"missing or invalid {key}") from exc


def _tenant(run: JobRun) -> UUID:
    if run.tenant_id is None:
        raise PermanentJobError("tenant-scoped job without tenant")
    return run.tenant_id


# --- dataset cut -------------------------------------------------------------------


def dataset_cut(conn: psycopg.Connection, run: JobRun) -> dict[str, Any]:
    tenants = [
        row[0]
        for row in conn.execute(
            "select tenant_id from ai.tenant_activity"
            " where last_cut_at is null or last_evidence_at > last_cut_at order by tenant_id"
        ).fetchall()
    ]
    created = 0
    for tenant_id in tenants:
        with tenant_transaction(conn, tenant_id):
            training = _cut(conn, tenant_id, "training")
            benchmark = _cut(conn, tenant_id, "evaluation")
            latest_benchmark = benchmark or _latest_benchmark(conn)
            if training is not None:
                created += 1
                enqueue(
                    conn,
                    "train",
                    f"train:{training}:{EMBEDDER.version}",
                    tenant_id=tenant_id,
                    payload={"dataset_id": str(training)},
                )
            if benchmark is not None:
                created += 1
                for (model_id,) in conn.execute(
                    "select id from ai.models where status = 'candidate'"
                ).fetchall():
                    enqueue(
                        conn,
                        "evaluate",
                        f"evaluate:{model_id}:{latest_benchmark}",
                        tenant_id=tenant_id,
                        payload={"model_id": str(model_id)},
                    )
        conn.execute(
            "update ai.tenant_activity set last_cut_at = now() where tenant_id = %s", (tenant_id,)
        )
    return {"tenants": len(tenants), "datasets_created": created}


def _cut(conn: psycopg.Connection, tenant_id: UUID, purpose: str) -> UUID | None:
    rows = conn.execute(
        "select id from ai.evidence where kind = 'correction' and env = 'production'"
        " and learning_scope = %s and review_status = 'approved' order by received_at, id",
        (purpose,),
    ).fetchall()
    if not rows:
        return None
    ids = [r[0] for r in rows]
    digest = hashlib.sha256(",".join(str(i) for i in ids).encode()).hexdigest()
    row = conn.execute(
        "insert into ai.datasets"
        " (tenant_id, purpose, version, evidence_ids, item_count, content_hash)"
        " select %s, %s, coalesce(max(version), 0) + 1, %s, %s, %s"
        " from ai.datasets where purpose = %s"
        " on conflict do nothing returning id",
        (tenant_id, purpose, ids, len(ids), digest, purpose),
    ).fetchone()
    return row[0] if row else None


def _latest_benchmark(conn: psycopg.Connection) -> UUID | None:
    row = conn.execute(
        "select id from ai.datasets where purpose = 'evaluation' order by version desc limit 1"
    ).fetchone()
    return row[0] if row else None


# --- train -------------------------------------------------------------------------


def train(conn: psycopg.Connection, run: JobRun) -> dict[str, Any]:
    tenant_id, dataset_id = _tenant(run), _uuid(run, "dataset_id")
    with tenant_transaction(conn, tenant_id):
        items = _items(conn, dataset_id, "training")
        if not items:
            raise PermanentJobError("training dataset not found for tenant")
        row = conn.execute(
            "insert into ai.models (tenant_id, kind, dataset_id, embedder, params, status)"
            " values (%s, 'intent_knn', %s, %s, %s, 'candidate')"
            " on conflict (dataset_id, embedder, kind) do nothing returning id",
            (tenant_id, dataset_id, EMBEDDER.version, Jsonb({"k": K_NEIGHBOURS})),
        ).fetchone()
        if row is None:
            return {"outcome": "already_trained"}
        model_id = row[0]
        with conn.cursor() as cur:
            cur.executemany(
                "insert into ai.model_examples (tenant_id, model_id, ordinal, label, embedding)"
                " values (%s, %s, %s, %s, %s::vector)",
                [
                    (tenant_id, model_id, i, label, vector_literal(EMBEDDER.embed(text)))
                    for i, (text, label) in enumerate(items)
                ],
            )
        benchmark = _latest_benchmark(conn)
        enqueue(
            conn,
            "evaluate",
            f"evaluate:{model_id}:{benchmark}",
            tenant_id=tenant_id,
            payload={"model_id": str(model_id)},
        )
    return {"outcome": "trained", "model_id": str(model_id), "examples": len(items)}


def _items(conn: psycopg.Connection, dataset_id: UUID, purpose: str) -> list[tuple[str, str]]:
    return [
        (row[0], row[1])
        for row in conn.execute(
            "select e.payload->>'text', e.payload->>'corrected_output' from ai.datasets d"
            " join ai.evidence e on e.id = any(d.evidence_ids)"
            " where d.id = %s and d.purpose = %s order by e.received_at, e.id",
            (dataset_id, purpose),
        ).fetchall()
    ]


# --- inference used by evaluation (and by the future concierge) --------------------


def predict(conn: psycopg.Connection, model_id: UUID, text: str) -> tuple[str | None, float]:
    """k-NN vote over the model's examples. Returns (label, share of votes)."""
    rows = conn.execute(
        "select label, embedding <=> %s::vector as distance from ai.model_examples"
        " where model_id = %s order by distance, ordinal limit %s",
        (vector_literal(EMBEDDER.embed(text)), model_id, K_NEIGHBOURS),
    ).fetchall()
    if not rows:
        return None, 0.0
    votes = Counter(label for label, _ in rows)
    top = max(votes.values())
    # Ties go to the label of the nearest neighbour among the tied labels.
    label = next(label for label, _ in rows if votes[label] == top)
    return label, top / len(rows)


def _accuracy(
    conn: psycopg.Connection, model_id: UUID, items: list[tuple[str, str]]
) -> dict[str, Any]:
    correct = sum(1 for text, label in items if predict(conn, model_id, text)[0] == label)
    return {"accuracy": round(correct / len(items), 6), "correct": correct, "items": len(items)}


# --- evaluate ----------------------------------------------------------------------


def evaluate(conn: psycopg.Connection, run: JobRun) -> dict[str, Any]:
    tenant_id, model_id = _tenant(run), _uuid(run, "model_id")
    with tenant_transaction(conn, tenant_id):
        model = conn.execute("select status from ai.models where id = %s", (model_id,)).fetchone()
        if model is None:
            raise PermanentJobError("model not found for tenant")
        benchmark = _latest_benchmark(conn)
        if benchmark is None:
            return {"outcome": "awaiting_benchmark"}
        items = _items(conn, benchmark, "evaluation")
        metrics = _accuracy(conn, model_id, items)
        baseline = conn.execute(
            "select id from ai.models where status = 'promoted' and id <> %s", (model_id,)
        ).fetchone()
        baseline_id = baseline[0] if baseline else None
        baseline_metrics = _accuracy(conn, baseline_id, items) if baseline_id else None
        row = conn.execute(
            "insert into ai.evaluations (tenant_id, model_id, benchmark_id, baseline_model_id,"
            " metrics, baseline_metrics) values (%s, %s, %s, %s, %s, %s)"
            " on conflict (model_id, benchmark_id) do nothing returning id",
            (
                tenant_id,
                model_id,
                benchmark,
                baseline_id,
                Jsonb(metrics),
                Jsonb(baseline_metrics) if baseline_metrics else None,
            ),
        ).fetchone()
        if row is None:
            return {"outcome": "already_evaluated"}
        conn.execute(
            "update ai.models set status = 'evaluated' where id = %s and status = 'candidate'",
            (model_id,),
        )
        enqueue(
            conn,
            "verify",
            f"verify:{row[0]}",
            tenant_id=tenant_id,
            payload={"evaluation_id": str(row[0])},
        )
    return {"outcome": "evaluated", "evaluation_id": str(row[0]), **metrics}


# --- verify (policy gate) ------------------------------------------------------------


def verify(conn: psycopg.Connection, run: JobRun) -> dict[str, Any]:
    tenant_id, evaluation_id = _tenant(run), _uuid(run, "evaluation_id")
    with tenant_transaction(conn, tenant_id):
        ev = conn.execute(
            "select model_id, benchmark_id, metrics, baseline_metrics from ai.evaluations"
            " where id = %s",
            (evaluation_id,),
        ).fetchone()
        if ev is None:
            raise PermanentJobError("evaluation not found for tenant")
        model_id, benchmark_id, metrics, baseline = ev
        dataset_id = conn.execute(
            "select dataset_id from ai.models where id = %s", (model_id,)
        ).fetchone()[0]  # type: ignore[index]
        checks = _policy_checks(conn, dataset_id, benchmark_id, model_id, metrics, baseline)
        passed = all(c["passed"] for c in checks.values())
        row = conn.execute(
            "insert into ai.verifications (tenant_id, model_id, evaluation_id, passed, checks,"
            " policy_version) values (%s, %s, %s, %s, %s, %s)"
            " on conflict (model_id) do nothing returning model_id",
            (tenant_id, model_id, evaluation_id, passed, Jsonb(checks), POLICY_VERSION),
        ).fetchone()
        if row is None:
            return {"outcome": "already_verified"}
        conn.execute(
            "update ai.models set status = %s where id = %s and status = 'evaluated'",
            ("verified" if passed else "rejected", model_id),
        )
    return {
        "outcome": "verified" if passed else "rejected",
        "model_id": str(model_id),
        "failed_checks": [name for name, c in checks.items() if not c["passed"]],
    }


def _policy_checks(
    conn: psycopg.Connection,
    dataset_id: UUID,
    benchmark_id: UUID,
    model_id: UUID,
    metrics: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    out_of_scope = conn.execute(
        "select count(*) from ai.datasets d join ai.evidence e on e.id = any(d.evidence_ids)"
        " where d.id = %s and not (e.kind = 'correction' and e.env = 'production'"
        " and e.learning_scope = 'training' and e.review_status = 'approved')",
        (dataset_id,),
    ).fetchone()[0]  # type: ignore[index]
    leaked = conn.execute(
        "select count(*) from ai.datasets t join ai.evidence te on te.id = any(t.evidence_ids),"
        " ai.datasets b join ai.evidence be on be.id = any(b.evidence_ids)"
        " where t.id = %s and b.id = %s"
        " and lower(te.payload->>'text') = lower(be.payload->>'text')",
        (dataset_id, benchmark_id),
    ).fetchone()[0]  # type: ignore[index]
    labels = [
        r[0]
        for r in conn.execute(
            "select distinct label from ai.model_examples where model_id = %s", (model_id,)
        ).fetchall()
    ]
    accuracy = float(metrics["accuracy"])
    checks: dict[str, dict[str, Any]] = {
        "benchmark_size": {
            "passed": metrics["items"] >= MIN_BENCHMARK_ITEMS,
            "value": metrics["items"],
            "min": MIN_BENCHMARK_ITEMS,
        },
        "min_accuracy": {
            "passed": accuracy >= MIN_ACCURACY,
            "value": accuracy,
            "min": MIN_ACCURACY,
        },
        "training_scope": {"passed": out_of_scope == 0, "out_of_scope_items": out_of_scope},
        "no_benchmark_leakage": {"passed": leaked == 0, "overlapping_items": leaked},
        "labels_are_service_codes": {"passed": all(_CODE.fullmatch(lbl) for lbl in labels)},
    }
    if baseline is not None:
        floor = float(baseline["accuracy"]) - MAX_REGRESSION
        checks["no_regression"] = {
            "passed": accuracy >= floor,
            "value": accuracy,
            "baseline": baseline["accuracy"],
            "tolerance": MAX_REGRESSION,
        }
    return checks


# --- explicit promotion and rollback (never a job) -----------------------------------


def promote(
    conn: psycopg.Connection, tenant_id: UUID, model_id: UUID, decided_by: str, reason: str
) -> UUID:
    with tenant_transaction(conn, tenant_id):
        status = conn.execute(
            "select status from ai.models where id = %s for update", (model_id,)
        ).fetchone()
        if status is None:
            raise LookupError("model not found for tenant")
        if status[0] not in ("verified", "retired"):
            raise PermissionError(
                f"only a verified (or previously promoted) model can be promoted, not {status[0]}"
            )
        if (
            status[0] == "retired"
            and not conn.execute(
                "select 1 from ai.verifications where model_id = %s and passed", (model_id,)
            ).fetchone()
        ):
            raise PermissionError("model never passed verification")
        previous = conn.execute(
            "update ai.models set status = 'retired' where status = 'promoted' returning id"
        ).fetchone()
        conn.execute("update ai.models set status = 'promoted' where id = %s", (model_id,))
        row = conn.execute(
            "insert into ai.promotions (tenant_id, model_id, previous_model_id, decided_by, reason)"
            " values (%s, %s, %s, %s, %s) returning id",
            (tenant_id, model_id, previous[0] if previous else None, decided_by, reason),
        ).fetchone()
    if row is None:
        raise RuntimeError("promotion was not recorded")
    promotion_id: UUID = row[0]
    return promotion_id


def rollback(conn: psycopg.Connection, tenant_id: UUID, decided_by: str, reason: str) -> UUID:
    with tenant_transaction(conn, tenant_id):
        last = conn.execute(
            "select previous_model_id from ai.promotions order by decided_at desc, id desc limit 1"
        ).fetchone()
    if last is None or last[0] is None:
        raise LookupError("no previous model to roll back to")
    return promote(conn, tenant_id, last[0], decided_by, f"rollback: {reason}")


def promoted_model(conn: psycopg.Connection, tenant_id: UUID) -> UUID | None:
    with tenant_transaction(conn, tenant_id):
        row = conn.execute("select id from ai.models where status = 'promoted'").fetchone()
    return row[0] if row else None


# --- RAG refresh and retrieval -------------------------------------------------------


def rag_refresh(conn: psycopg.Connection, run: JobRun) -> dict[str, Any]:
    tenants = [r[0] for r in conn.execute("select tenant_id from ai.tenant_activity").fetchall()]
    embedded = 0
    for tenant_id in tenants:
        with tenant_transaction(conn, tenant_id):
            stale = conn.execute(
                "select d.id, d.body, d.content_hash from ai.documents d"
                " left join ai.embeddings e on e.document_id = d.id and e.embedder = %s"
                " where e.document_id is null or e.content_hash <> d.content_hash",
                (EMBEDDER.version,),
            ).fetchall()
            for doc_id, body, digest in stale:
                conn.execute(
                    "insert into ai.embeddings"
                    " (tenant_id, document_id, embedder, content_hash, embedding)"
                    " values (%s, %s, %s, %s, %s::vector)"
                    " on conflict (document_id, embedder) do update"
                    " set content_hash = excluded.content_hash,"
                    " embedding = excluded.embedding, updated_at = now()",
                    (
                        tenant_id,
                        doc_id,
                        EMBEDDER.version,
                        digest,
                        vector_literal(EMBEDDER.embed(body)),
                    ),
                )
            embedded += len(stale)
    return {"tenants": len(tenants), "embedded": embedded}


def search(
    conn: psycopg.Connection, tenant_id: UUID, query: str, k: int = 3
) -> list[tuple[str, str, float]]:
    """Retrieve the tenant's closest knowledge documents (ref, body, cosine similarity)."""
    with tenant_transaction(conn, tenant_id):
        rows = conn.execute(
            "select d.ref, d.body, 1 - (e.embedding <=> %s::vector) as score from ai.embeddings e"
            " join ai.documents d on d.id = e.document_id where e.embedder = %s"
            " order by e.embedding <=> %s::vector limit %s",
            (
                vector_literal(EMBEDDER.embed(query)),
                EMBEDDER.version,
                vector_literal(EMBEDDER.embed(query)),
                k,
            ),
        ).fetchall()
    return [(r[0], r[1], round(float(r[2]), 6)) for r in rows]


# --- scheduling ----------------------------------------------------------------------

HANDLERS: dict[str, Handler] = {
    "ingest": ingest,
    "dataset_cut": dataset_cut,
    "train": train,
    "evaluate": evaluate,
    "verify": verify,
    "rag_refresh": rag_refresh,
}
PERIODIC = ("dataset_cut", "rag_refresh")


def schedule_periodic(conn: psycopg.Connection, now: datetime | None = None) -> list[str]:
    """Enqueue this time bucket's periodic runs (idempotent per bucket)."""
    moment = now or datetime.now(UTC)
    bucket = moment.replace(
        minute=moment.minute - moment.minute % SCHEDULE_BUCKET_MINUTES, second=0, microsecond=0
    ).strftime("%Y%m%dT%H%M")
    created = []
    with conn.transaction():
        for kind in PERIODIC:
            if enqueue(conn, kind, f"{kind}:{bucket}", max_attempts=3):
                created.append(kind)
    return created
