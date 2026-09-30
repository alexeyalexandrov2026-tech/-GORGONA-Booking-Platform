# Observability and reliability

Status: design only.

Correlate web request, API command, database transaction, outbox event, notification and payment webhook with request/trace IDs. Structured logs contain operation, tenant ID where safe, result, duration and error code; never log tokens, payment secrets, client notes or raw provider bodies. Instrument booking conflict/latency, hold expiry lag, webhook age, outbox backlog/retries, API error rates, availability query time and database capacity. Define alert thresholds from measured baseline and an on-call owner.

Provide liveness and readiness checks that actually verify dependencies; use bounded retries, dead-letter recovery, graceful shutdown and rollback. For OCI-hosted PostgreSQL, specify backup interval, WAL archive/PITR, encrypted off-host storage, RPO/RTO and a documented restore drill. A configured backup is not a verified recovery until a restore succeeds. No live telemetry, alerts or restore evidence exists.
