# Initial audit — 2026-09-30

## Executive summary

No canonical KA Nails or GORGONA Booking AI source was identified in the supplied workspace or the owner's accessible GitHub repositories. The current folder was not a Git repository. Two related, owner-controlled GitHub repositories were inspected as references: `alexeyalexandrov2026-tech/Fresh-Nails` at `5f5b842` (2026-08-08) and `alexeyalexandrov2026-tech/Fresh-Nails-AI-Receptionist-by-Gorgona-One-AI` at `5541f06` (2026-08-07). They are Fresh Nails products, not KA Nails. This is a new, isolated KA Nails architecture repository by the owner's explicit request.

The only accessible KA Nails visual asset is the supplied official logo. The product brief refers to an architecture image that was not present. No claim is made about a running KA Nails product.

## Existing repository map and runtime architecture

| Area | Observed state | Evidence / limit |
| --- | --- | --- |
| Fresh Nails website | Vite 8, React 19, React Router; routes `/` and `/masters`; static catalog in JSX; external Square booking links | `Fresh-Nails/package.json`, `src/App.jsx`, `src/pages/Home.jsx` |
| Fresh Nails AI receptionist | Next.js 16, chat route, Dify adapter, Supabase client, Cloudflare Wrangler/OpenNext config | `Fresh-Nails-AI-Receptionist-by-Gorgona-One-AI/package.json`, `src/app/api/chat/route.ts`, `wrangler.jsonc` |
| Database | Existing receptionist migrations define masters, services, clients, appointments, payments and conversations; RLS is enabled without visible tenant policies in the inspected migration | `supabase/migrations/00001_init.sql`, `00002_enable_rls.sql` |
| KA Nails | No source, deployment, database, auth, tests, CI, API, design tokens, or observability found | Supplied task files and repo discovery |

## Frontend, backend, auth, booking, AI, integrations

The Fresh Nails site is a static marketing site. Its hero, imagery, copy and prices belong to Fresh Nails and cannot become KA Nails facts. Its mobile menu is rendered as a button in the inspected source; functionality was not verified. Its online-booking links leave for Square; there is no visible transactional booking engine in that repository.

The AI receptionist is a separate application whose `/api/chat` calls Dify. When its key is missing, it returns a success-shaped mock conversation; on provider error it returns raw provider text in the API response. Both behaviors are unsuitable for production reuse. The examined Supabase migration has no tenant key, appointment range, resource exclusion constraint, hold, outbox, webhook inbox, or price snapshot. An enabled RLS flag alone does not prove usable or correct policies. No KA Nails authentication or authorization path was found.

## Current deployment and observability

The receptionist has a Wrangler config with observability enabled, but no live Cloudflare deployment or production telemetry was verified. The Fresh Nails site has no Cloudflare config in the inspected file set. The Supabase organization `Gargona One` is on the free plan with two active projects; new project creation returned the provider's active free-project limit. No existing project was paused or changed.

## Testing status

This was a read-only code and configuration audit. No application test suite, live booking test, API request, migration, RLS test, performance test, or visual QA was run. No production readiness claim is possible.

## Risks and technical debt

| Risk | Impact | Response |
| --- | --- | --- |
| Reusing Fresh Nails source or data without product mapping | Wrong brand, prices, customer promises, and ownership | Keep repositories separate; review reusable patterns file by file |
| AI or frontend becomes booking truth | Invented slots or double booking | Deterministic domain service plus PostgreSQL exclusion invariant |
| Broad service credential or incomplete RLS | Cross-tenant exposure | Trusted tenant context, scoped roles, RLS tests |
| Missing base Hammam booking duration | False availability | Block publication of that bookable variant until owner configures it |
| Provider redirect interpreted as payment success | Unpaid confirmation | Verified, idempotent webhook processing |
| Existing receptionist leaks upstream details | Sensitive data exposure | Do not copy route; build typed gateway with sanitized errors |
| Unverified diagrams, photos, location, hours, policies | Misleading public site | Obtain authoritative assets and owner decisions before launch |

## Reuse and preservation

Preserve the official KA Nails logo exactly. Fresh Nails offers examples of a web framework and Cloudflare deployment configuration, but neither codebase is a proven core for this new product. The external Square booking link and Dify adapter are integration candidates only after contract and ownership review. Keep existing Fresh Nails repositories and Supabase projects unchanged.

## Target, migration and missing capabilities

Use a modular monolith with a typed web application, Python domain API, PostgreSQL transaction boundary, and explicit tenant isolation. The proposed modules and sequencing are in `TARGET_ARCHITECTURE.md` and `RELEASE_PLAN.md`. First implement tenant/catalog and resource scheduling; then concurrency-safe holds and appointments; then payments, account/admin, notifications, AI tools, analytics, and launch gates. There is no legacy KA Nails data to migrate. Any later import from Square/Fresh Nails requires a separate mapping, consent and reconciliation plan.

## Owner decisions

Confirm project domain and Cloudflare account; KA Nails location/timezone, contact information, opening hours, staff and authentic photos; complete catalog and base Hammam booking duration; deposit/cancellation/tax policy; legal/privacy wording; whether Square remains the temporary booking channel; and a Supabase capacity plan. The owner chose to keep existing Supabase projects active and defer creation of the new one.
