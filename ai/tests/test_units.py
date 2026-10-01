"""Pure logic: embedder and evidence validation rules (no database)."""

from uuid import uuid4

import pytest

from gorgona_ai.embedder import DIMENSIONS, HashingEmbedder, cosine
from gorgona_ai.evidence import EvidenceRejectedError, normalise, parse
from tests.conftest import envelope


def test_embedder_is_deterministic_normalised_and_semantic_by_overlap() -> None:
    e = HashingEmbedder()
    a, b = e.embed("gel manicure please"), e.embed("gel manicure please")
    assert a == b
    assert len(a) == DIMENSIONS
    assert abs(cosine(a, a) - 1.0) < 1e-9
    near = cosine(e.embed("gel manicure please"), e.embed("can I get a gel manicure"))
    far = cosine(e.embed("gel manicure please"), e.embed("lash lift and tint"))
    assert near > far
    assert e.embed("") == [0.0] * DIMENSIONS


def test_contact_data_is_redacted_from_free_text() -> None:
    text, n = normalise("Mail me at jane.doe@example.test or call +1 555 010 2345  please")
    assert "example.test" not in text
    assert "555" not in text
    assert n == 2
    assert text == "Mail me at [redacted] or call [redacted] please"


def test_non_production_evidence_never_trains() -> None:
    raw = envelope(uuid4(), "correction", {"text": "gel", "corrected_output": "GEL"},
                   env="staging", scope="training")  # fmt: skip
    env, _, _ = parse(raw)
    assert env.learning_scope == "none"


@pytest.mark.parametrize(
    ("raw_payload", "kind", "code"),
    [
        (
            {"text": "x", "corrected_output": "GEL", "email": "a@b.test"},
            "correction",
            "contact_data_field",
        ),
        ({"text": "x", "corrected_output": "not a code"}, "correction", "invalid_payload"),
        ({"text": "x"}, "correction", "invalid_payload"),
        (
            {"services": [{"service_code": "GEL", "service_name": "Gel", "customer_name": "x"}]},
            "catalog",
            "contact_data_field",
        ),
    ],
)
def test_invalid_or_contact_bearing_evidence_is_rejected(
    raw_payload: dict[str, object], kind: str, code: str
) -> None:
    with pytest.raises(EvidenceRejectedError) as exc:
        parse(envelope(uuid4(), kind, raw_payload))
    assert exc.value.code == code


def test_envelope_must_carry_scope_provenance_and_version() -> None:
    raw = envelope(uuid4(), "interaction", {"text": "hello"})
    del raw["provenance"]
    with pytest.raises(EvidenceRejectedError):
        parse(raw)
    raw = envelope(uuid4(), "interaction", {"text": "hello"})
    raw["schema_version"] = 2
    with pytest.raises(EvidenceRejectedError):
        parse(raw)
    catalog = {"services": [{"service_code": "A", "service_name": "A"}]}
    with pytest.raises(EvidenceRejectedError) as exc:
        parse(envelope(uuid4(), "catalog", catalog, scope="training"))
    assert exc.value.code == "catalog_is_knowledge_not_training"
