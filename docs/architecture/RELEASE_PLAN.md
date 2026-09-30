# Release plan and external setup

Status: Phase 0 architecture package complete; application implementation and infrastructure provisioning incomplete.

## Ordered milestones

1. **M0 audit/owner facts:** approve domain, location/timezone, hours, staff, authentic imagery, catalog, booking durations, deposit/tax/cancellation policies and architecture image.
2. **M1 infrastructure:** identify a current OCI compartment/instance or provision an isolated host after cost and blast-radius review. Restore OCI session. Verify OS/architecture/capacity, SSH, firewall, DNS/TLS, private PostgreSQL, backup target and staging separation.
3. **M2 foundation:** strict web/API types, CI, tenant identity and authorization, migration workflow, observability.
4. **M3 catalog/scheduling:** validated KA Nails seed, staff/resources/schedules, quote and availability logic.
5. **M4 booking:** holds, PostgreSQL exclusion constraint, state machine, idempotency, 100-way concurrency gate.
6. **M5 customer/admin:** accessible site and booking UX, account, calendar, policies and authentic content.
7. **M6 payments/async:** signed Stripe webhook, inbox/outbox, notification delivery and reconciliation.
8. **M7 AI/analytics:** narrow concierge tools and versioned events; no autonomous booking truth.
9. **M8 hardening/launch:** RLS and security regression, load/soak, restore drill, staging E2E, mobile visual QA, owner launch approval and monitored rollback.

## Platform provisioning state

| Platform | Desired state | Actual evidence on 2026-09-30 |
| --- | --- | --- |
| GitHub | New private `gorgona-booking-ai` repository for this source | **BLOCKED**: GitHub connector has repository read/write but no repository-create action; in-app browser is at GitHub sign-in, and no reusable Git credential was available. Local Git repository exists, without remote. |
| OCI | Isolated host for web/API/PostgreSQL, or verified managed DB alternative | **BLOCKED**: previously documented `gorgona-node` TERMINATED; current `GORGONA` CLI security-token session expired. No live target or cost has been verified. |
| Cloudflare | Optional DNS/CDN/WAF, only if chosen | **NOT SET UP**: in-app browser is at Cloudflare sign-in. No Cloudflare project was created. A Cloudflare Worker is not needed merely because the app uses OCI. |
| Supabase | No longer required for preferred OCI-first design | **DEFERRED by owner**: free organization had two active projects and new creation was rejected; neither existing project was changed. |

## Release acceptance

No customer booking, payment or AI route should be public before transactional booking and security tests pass. Run format/lint/typecheck/unit/integration/migration/RLS/concurrency/webhook/E2E/accessibility/load checks on exact commits, then deploy staging, verify runtime, perform restore drill and approve production. Use reversible releases and retain the prior version and database migration rollback plan. None of these gates has run.
