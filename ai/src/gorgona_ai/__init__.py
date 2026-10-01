"""GORGONA AI learning plane workers (ADR-0013).

Evidence and corrections are validated, scoped and deduplicated; reviewed examples are
cut into immutable dataset versions; candidates are trained, evaluated against a
frozen benchmark and the promoted model, and verified by a policy gate. Promotion is
only ever an explicit, recorded decision. Knowledge documents are embedded for
retrieval. Everything runs as durable, idempotent, retried jobs.
"""
