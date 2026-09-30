# GORGONA Booking AI

KA Nails is the first planned tenant of a reusable appointment platform. This repository contains the Phase 0 audit and architecture package and the **M1 local booking foundation** (`api/`): tenancy with forced RLS, catalog bookability rules, and holds protected by a PostgreSQL exclusion constraint. The full suite (127 tests, including every PostgreSQL 18 integration test and both 100-way races) passes against a real, local PostgreSQL 18.6 server; see [`docs/plan/M1_REPORT.md`](docs/plan/M1_REPORT.md). The OCI database VM is still blocked by A1 capacity. Nothing is deployed, and there is no payment, AI, customer UI or admin service.

## Current state

- The official KA Nails logo is preserved at `assets/brand/ka-nails-logo.png` (source SHA-256 `BB2FE1C05EB7183B8B8F55EEE861B06D80256CF5349B2958E1432FA3B83FBF53`). Do not substitute the Fresh Nails brand assets.
- The full owner-supplied product brief is preserved verbatim at `source/PRODUCT_BRIEF.txt`; the handoff records later decisions and verified blockers.
- The existing Fresh Nails website and AI receptionist are separate references. They have not been copied or modified.
- Oracle Cloud Infrastructure is the preferred proposed host after the owner's question. A live instance and access are not verified; the old `gorgona-node` was previously terminated and the current CLI session is expired.
- Supabase creation is deferred by owner choice because the free organization already has two active projects. Cloudflare is optional for DNS, CDN and edge security; neither provider is required in the proposed core.
- GitHub and Cloudflare project provisioning require an authenticated session; see `docs/architecture/RELEASE_PLAN.md`.

Start with [the handoff](CLOUD_CODE_HANDOFF.md), [full brief](source/PRODUCT_BRIEF.txt), [audit](docs/architecture/INITIAL_AUDIT.md), [target architecture](docs/architecture/TARGET_ARCHITECTURE.md), and [release plan](docs/architecture/RELEASE_PLAN.md). All architecture decisions are proposals until the relevant implementation and verification gates pass.

## Source material and boundaries

The owner supplied a KA Nails logo and a detailed product brief. The architecture diagram the brief references was supplied on 2026-09-30 and is saved at `assets/architecture/ka-nails-architecture-diagram.webp`; where it shows Supabase or Cloudflare, the later OCI-first decision in the handoff takes precedence (see `docs/plan/M1_PLAN.md`). Any unconfirmed business data, including the location, hours, artist roster, base Hammam booking duration, deposit policy, tax treatment, and domain, remains an owner decision. No customer data or credentials belong in this repository.
