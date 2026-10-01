"""Evidence contract and ingestion: validate -> normalise -> redact -> scope -> dedupe.

Rules (ADR-0013, AI_TOOL_MODEL):
- Every record carries env, tenant_id, source, schema_version, learning scope and
  provenance.
- Non-production evidence never trains: its learning scope is forced to 'none'.
- Customer contact data is never stored. Payload fields that look like contact data
  are refused; e-mail addresses and phone numbers in free text are redacted.
- Corrections need human review before they can enter a dataset.
- A catalog snapshot is authoritative knowledge for retrieval and replaces the
  tenant's service documents.
"""

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from gorgona_ai.db import tenant_transaction
from gorgona_ai.jobs.runtime import JobRun

_CODE = r"^[A-Z0-9][A-Z0-9_]{0,63}$"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
_CONTACT_KEYS = frozenset(
    {"email", "phone", "customer_name", "customer_email", "customer_phone", "name"}
)

Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Code = Annotated[str, StringConstraints(pattern=_CODE)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Provenance(Strict):
    producer: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    produced_at: datetime
    trace_id: Annotated[str, StringConstraints(max_length=100)] | None = None
    model_version: Annotated[str, StringConstraints(max_length=100)] | None = None


class InteractionPayload(Strict):
    text: Text
    predicted_service_code: Code | None = None


class CorrectionPayload(Strict):
    text: Text
    original_output: Code | None = None
    corrected_output: Code


class CatalogService(Strict):
    service_code: Code
    service_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None


class CatalogPayload(Strict):
    services: list[CatalogService] = Field(min_length=1, max_length=500)


class Envelope(Strict):
    schema_version: Literal[1]
    tenant_id: UUID
    env: Literal["production", "staging", "test", "verification"]
    source: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    kind: Literal["interaction", "correction", "catalog"]
    learning_scope: Literal["training", "evaluation", "none"]
    provenance: Provenance
    payload: dict[str, Any]


class EvidenceRejectedError(ValueError):
    """The evidence is invalid or not allowed; it is recorded as rejected, never stored."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_PAYLOADS: dict[str, type[Strict]] = {
    "interaction": InteractionPayload,
    "correction": CorrectionPayload,
    "catalog": CatalogPayload,
}


def normalise(text: str) -> tuple[str, int]:
    """NFKC, collapse whitespace, redact e-mail addresses and phone numbers."""
    value = " ".join(unicodedata.normalize("NFKC", text).split())
    value, emails = _EMAIL.subn("[redacted]", value)
    value, phones = _PHONE.subn("[redacted]", value)
    return value, emails + phones


def _contact_keys(value: Any) -> bool:
    if isinstance(value, dict):
        return any(k.lower() in _CONTACT_KEYS or _contact_keys(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contact_keys(v) for v in value)
    return False


def parse(raw: dict[str, Any]) -> tuple[Envelope, Strict, int]:
    """Validate and normalise one evidence envelope. Raises EvidenceRejectedError."""
    try:
        envelope = Envelope.model_validate(raw)
    except ValidationError as exc:
        raise EvidenceRejectedError("invalid_envelope") from exc
    if _contact_keys(envelope.payload):
        raise EvidenceRejectedError("contact_data_field")
    try:
        payload = _PAYLOADS[envelope.kind].model_validate(envelope.payload)
    except ValidationError as exc:
        raise EvidenceRejectedError("invalid_payload") from exc
    redactions = 0
    if isinstance(payload, (InteractionPayload, CorrectionPayload)):
        text, redactions = normalise(payload.text)
        payload = payload.model_copy(update={"text": text})
    if envelope.kind == "catalog" and envelope.learning_scope != "none":
        raise EvidenceRejectedError("catalog_is_knowledge_not_training")
    if envelope.env != "production" and envelope.learning_scope != "none":
        envelope = envelope.model_copy(update={"learning_scope": "none"})
    return envelope, payload, redactions


def content_hash(kind: str, payload: Strict) -> str:
    canonical = json.dumps(
        {"kind": kind, "payload": payload.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ingest(conn: psycopg.Connection, run: JobRun) -> dict[str, Any]:
    """Job handler: store one evidence envelope (idempotent)."""
    try:
        envelope, payload, redactions = parse(run.payload)
    except EvidenceRejectedError as exc:
        return {"outcome": "rejected", "reason": exc.code}
    digest = content_hash(envelope.kind, payload)
    review = "pending" if envelope.kind == "correction" else "not_required"
    with tenant_transaction(conn, envelope.tenant_id):
        row = conn.execute(
            "insert into ai.evidence (tenant_id, kind, env, source, schema_version, learning_scope,"
            " provenance, payload, content_hash, redactions, review_status)"
            " values (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s)"
            " on conflict (tenant_id, content_hash) do nothing returning id",
            (
                envelope.tenant_id,
                envelope.kind,
                envelope.env,
                envelope.source,
                envelope.learning_scope,
                Jsonb(envelope.provenance.model_dump(mode="json")),
                Jsonb(payload.model_dump(mode="json")),
                digest,
                redactions,
                review,
            ),
        ).fetchone()
        if row is None:
            return {"outcome": "duplicate"}
        if isinstance(payload, CatalogPayload):
            _apply_catalog(conn, envelope.tenant_id, payload)
        conn.execute(
            "insert into ai.tenant_activity (tenant_id) values (%s)"
            " on conflict (tenant_id) do update set last_evidence_at = now()",
            (envelope.tenant_id,),
        )
    return {"outcome": "stored", "evidence_id": str(row[0]), "redactions": redactions}


def _apply_catalog(conn: psycopg.Connection, tenant_id: UUID, catalog: CatalogPayload) -> None:
    refs = [s.service_code for s in catalog.services]
    for service in catalog.services:
        body = service.service_name + (f". {service.description}" if service.description else "")
        body, _ = normalise(body)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        conn.execute(
            "insert into ai.documents (tenant_id, kind, ref, body, content_hash)"
            " values (%s, 'service', %s, %s, %s)"
            " on conflict (tenant_id, kind, ref) do update set body = excluded.body,"
            " content_hash = excluded.content_hash, updated_at = now()"
            " where ai.documents.content_hash <> excluded.content_hash",
            (tenant_id, service.service_code, body, digest),
        )
    conn.execute(
        "delete from ai.documents"
        " where tenant_id = %s and kind = 'service' and not (ref = any(%s))",
        (tenant_id, refs),
    )


def review(
    conn: psycopg.Connection, tenant_id: UUID, evidence_id: UUID, decision: str, reviewer: str
) -> str:
    """Record a human review decision for a pending correction."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    with tenant_transaction(conn, tenant_id):
        row = conn.execute(
            "update ai.evidence set review_status = %s, reviewed_by = %s, reviewed_at = now()"
            " where id = %s and review_status = 'pending' returning id",
            (decision, reviewer, evidence_id),
        ).fetchone()
        if row is None:
            raise LookupError("no pending correction with that id for this tenant")
        conn.execute(
            "insert into ai.tenant_activity (tenant_id) values (%s)"
            " on conflict (tenant_id) do update set last_evidence_at = now()",
            (tenant_id,),
        )
    return decision
