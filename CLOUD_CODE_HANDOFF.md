# Cloud Code handoff — GORGONA Booking AI / KA Nails

Prepared 2026-09-30. This document is the starting context for the next coding environment. Read it before writing application code. The complete local project is this repository; all paths below are relative to its root.

## Objective and scope

Build a reusable, tenant-aware appointment platform named **GORGONA Booking AI** with **KA Nails** as its first tenant. The customer-facing KA Nails experience should follow the supplied official logo. Implement a deterministic booking system before the AI concierge. This handoff is Phase 0 audit/design; do not report the product as implemented.

The owner asked for a new GitHub project and considered Cloudflare/Supabase. They then asked whether **Oracle server could replace Supabase and Cloudflare**. Proposed direction: OCI-hosted web/API plus PostgreSQL, with Cloudflare optional for DNS/CDN/WAF. This is an architecture proposal, not a deployed system. The owner explicitly chose **not** to pause existing Supabase projects; the new Supabase project is deferred. Preserve existing GORGONA/Fresh Nails services and data.

## What exists here

- `assets/brand/ka-nails-logo.png`: exact supplied logo; SHA-256 `BB2FE1C05EB7183B8B8F55EEE861B06D80256CF5349B2958E1432FA3B83FBF53`.
- `source/PRODUCT_BRIEF.txt`: complete pasted owner brief, copied verbatim for full context. The later OCI/deferred-Supabase decisions in this handoff take precedence.
- `docs/architecture/INITIAL_AUDIT.md`: evidence and limitations from the two owner-controlled Fresh Nails reference repositories.
- `docs/architecture/TARGET_ARCHITECTURE.md`: proposed OCI-first modular monolith and decision list.
- `docs/architecture/DATA_MODEL.md`, `BOOKING_ENGINE.md`, `MULTITENANCY.md`, `SECURITY_MODEL.md`, `AI_TOOL_MODEL.md`: domain, trust and concurrency contracts.
- `docs/architecture/DESIGN_SYSTEM.md`, `ANALYTICS.md`, `OBSERVABILITY.md`, `RELEASE_PLAN.md`: customer experience and quality gates.
- Local Git repository on `main`, with no external remote until account access is restored.

## Verified findings and blockers

1. This task's starting folder had no Git repository. The owner's accessible GitHub account is `alexeyalexandrov2026-tech`. `Fresh-Nails` is a Vite/React marketing site with static JSX prices and Square links. `Fresh-Nails-AI-Receptionist-by-Gorgona-One-AI` is a separate Next.js/Dify/Supabase chat project. Neither is a KA Nails booking core. They were cloned read-only into this task's `work/` folder, outside this deliverable.
2. The old OCI `gorgona-node` was recorded as **TERMINATED** in a prior audit. On 2026-09-30, `oci iam region-subscription list --profile GORGONA --auth security_token` failed because the session expired. No current VM, cost, IP, SSH access or deployed application was verified. Do not target historical IP `150.136.90.12`.
3. Supabase organization `Gargona One` is free; creation reported $0/month but failed because its owner has reached the two active free-project limit. Active projects are `gorgona-camera-feed` and `Gorgona-one-claude`. The owner chose to keep both running and defer the new project.
4. The GitHub connector lacks a create-repository operation. GitHub's in-app page was unauthenticated and Git Credential Manager supplied no reusable credential. No GitHub repo or remote exists for this project. Cloudflare's in-app page was also at sign-in. No Cloudflare project or DNS change was made.
5. The product brief refers to a separate GORGONA/KA Nails architecture image, but only the logo image and text brief were available. Authentic KA Nails photos, domain, address, schedule, staff, legal/policy wording and the base Hammam booking duration were not supplied/verified.

## Next execution sequence

1. Reconcile owner decisions: confirm OCI compartment and current host plan, budget, domain, business timezone, staff/hours, service durations, deposit/cancellation/tax rules, photo rights and whether Square remains temporary booking. Do not infer these from Fresh Nails.
2. Renew OCI browser security-token session using `GORGONA` profile; list current instances/compartments and verify exact target. No VM restart, infrastructure creation or production deployment before cost, blast radius and rollback are reviewed with owner. Do not create an OCI API signing key by default.
3. Once GitHub login is available, create a **private** `gorgona-booking-ai` repository under the owner's chosen account, add its URL as `origin`, push the reviewed local `main`, and verify GitHub file/hash parity. Do not push cloned Fresh Nails references, secrets or task `work/` files.
4. Turn the approved architecture into ADRs, exact migrations, typed API contracts and tests. Implement tenant/catalog and staff/scheduling first; then transactional holds and appointments. Use PostgreSQL `tstzrange` + GiST exclusion for exclusive resources; the 100-concurrent-attempt acceptance is one success and 99 typed conflicts. Keep the AI outside booking authority.
5. Set up isolated staging on verified OCI infrastructure, private PostgreSQL, TLS, backups/PITR, restore drill, telemetry and CI before production. Cloudflare remains optional. Do not turn on a public booking CTA until it reaches a real validated booking/payment flow.

## Product facts from supplied brief that may be seeded after owner validation

- Hammam Luxury: $145; heel care and ritual included; gel not included.
- Hammam Luxury + Gel: $160, public duration range 120–145 minutes.
- Hammam Luxury + Gel French: $175, public range 130–155 minutes.
- Extra Foot Massage: +$20 and exactly +15 minutes booking time.
- Other Salon Gel Removal: +$15, subject to staff assessment when required.
- Hammam Spa Upgrade: +$40 only for compatible pedicures; must not stack with Hammam Luxury.
- French Gel: +$15 and only with compatible gel service.
- Base Hammam deterministic booking duration is **unknown**. Do not invent one or publish it as bookable.

## Security and release rules

No browser service-role credentials; trusted tenant context; composite tenant foreign keys and RLS; verified payment webhooks and idempotency; transactional outbox; structured, redacted logs; tested restore. AI tools are typed, tenant-scoped, policy-checked and have no arbitrary SQL/shell/HTTP access. Preserve human correction and audit history. Treat tests and live validation as separate evidence, and keep the status labels PASS / FAIL / BLOCKED / NOT TESTED.

## Suggested first prompt in Cloud Code

> Open this repository and read `CLOUD_CODE_HANDOFF.md` and every file in `docs/architecture/`. Treat the supplied KA Nails logo as the brand source. First verify Git state and the current OCI/GitHub access. Do not reuse Fresh Nails as KA Nails source. Make a detailed, testable M1 plan for an OCI-first modular monolith, identify owner decisions and deployment gates, and implement only the next approved, reviewable slice. Do not claim application or infrastructure completion from these design documents.

## Repository separation update (30 September 2026)
The logo source paths above record Phase 0 provenance. The unchanged PNG now belongs to the independent KA-nails repository at `public/assets/ka-nails-logo.png`; the old bytes remain in Git history. Platform origin is `alexeyalexandrov2026-tech/-GORGONA-Booking-Platform`. No push or deployment was performed. See `docs/architecture/REPOSITORY_SEPARATION.md`.
