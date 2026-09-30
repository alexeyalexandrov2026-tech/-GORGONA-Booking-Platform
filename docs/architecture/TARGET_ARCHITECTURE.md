# Target architecture

Status: proposed; no application runtime exists yet.

## System boundaries

Customer web, admin web and concierge call a typed application API. The modular Python domain layer owns tenancy, catalog, staff, scheduling, availability, booking, client/CRM, payments, notifications, audit and analytics. PostgreSQL is the transactional source of truth. The preferred candidate is a newly verified OCI deployment, with the web/API, PostgreSQL, object storage and backup responsibilities explicitly assigned. Cloudflare may later provide DNS/CDN/WAF but is optional. Supabase is deferred. Stripe, messaging and calendar are adapters behind domain interfaces. An AI gateway may interpret requests but can only use explicitly authorized domain tools.

Avoid a network microservice per module. Keep a single deployable backend until load, team boundaries or failure isolation justify splitting it. The customer UI must never hold a service-role key. No continuous video or GORGONA camera dependency is part of this booking platform.

## Proposed deployments

- `web`: strict TypeScript, Next.js App Router on a verified OCI Linux host behind TLS and a reverse proxy. Validate ARM64 compatibility if an Ampere VM is selected. Do not deploy onto an unrelated live GORGONA service.
- `api`: Python/FastAPI/Pydantic v2 modular monolith on the same isolated OCI environment initially, with a private local or VCN database connection.
- `db`: PostgreSQL with restricted network exposure, encrypted storage, least-privilege roles, tested backups and point-in-time recovery targets. All mutations pass through a domain transaction boundary, with RLS as defense in depth. Self-management replaces Supabase's managed operations and must be explicitly staffed.
- `jobs`: outbox-driven workers for notifications, payment reconciliation and waitlist processing; no direct network call inside a booking transaction.

The former `gorgona-node` was recorded as TERMINATED in a prior audit, and the OCI CLI session was expired when checked on 2026-09-30. No live host, IP, capacity, SSH, database, TLS or backup path is currently verified. Provisioning, DNS and deployment are blocked until a current target is identified and cost/security reviewed. An OCI Always Free instance may face regional capacity limits; do not promise a free deployment.

## Dependencies and contracts

Identity resolves authenticated actor and tenant from trusted server context. Catalog publishes versioned service/variant/add-on definitions. Availability consumes schedules, exceptions, resources and holds. Booking commits holds/appointments and immutable quote snapshots. Payments consumes booking state and verified provider events. Notifications consumes outbox events. AI tools call these same public module interfaces. Each boundary uses a versioned request/response schema and typed domain errors.

## Decisions to validate

ADR-001 modular monolith; ADR-002 PostgreSQL range exclusion; ADR-003 shared-schema tenant model with tenant keys; ADR-004 app authorization plus RLS; ADR-005 short-lived transactional holds; ADR-006 provider and API idempotency; ADR-007 outbox/webhook inbox; ADR-008 provider-neutral AI gateway; ADR-009 versioned analytics events; ADR-010 trace-correlated observability; ADR-011 KA Nails brand tokens and authentic media. These are proposals, not implemented ADRs.

References: [OCI Compute](https://docs.oracle.com/en-us/iaas/Content/Compute/Concepts/computeoverview.htm), [OCI Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [PostgreSQL backup/PITR](https://www.postgresql.org/docs/current/backup.html), [PostgreSQL exclusion constraints](https://www.postgresql.org/docs/current/ddl-constraints.html#DDL-CONSTRAINTS-EXCLUSION).
