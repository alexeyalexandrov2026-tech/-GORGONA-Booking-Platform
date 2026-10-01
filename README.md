# GORGONA Booking AI

KA Nails is the first planned tenant of a reusable appointment platform. **M1** supplies tenant-isolated PostgreSQL booking/catalog logic, forced RLS and transactional holds. **M2** adds OIDC identity, memberships, invitations, authorization and governed readiness/go-live. **M3** adds the customer booking journey at `/book/`: backend catalog, variants/add-ons, artist/Any Available, server-calculated times, price review, validated guest details and real booking confirmation. The Next.js static export and FastAPI API share one origin; customer requests resolve the salon through the existing Host mechanism.

Evidence and limitations are recorded in [`M1_REPORT`](docs/plan/M1_REPORT.md), [`M2_REPORT`](docs/plan/M2_REPORT.md) and [`M3_REPORT`](docs/plan/M3_REPORT.md). Local M3 acceptance uses explicitly labelled FAKE live salons and real PostgreSQL/Chromium. KA Nails remains `not_live`: its timezone, hours, staff, exact durations, policies, booking rules and production domain still require confirmed owner facts. Nothing is deployed; payment collection, notifications, AI and admin interfaces remain outside M3. Production remains gated behind the approved production-bridge acceptance process. No final production cutover occurs until the required bridge/security/E2E gates pass and the production deployment is explicitly authorized.

## Local customer development

Build the static customer web with Node.js 24:

```bash
cd web
npm ci --ignore-scripts
npm run build
```

Start the existing API with a runtime-role database credential and `GBA_CUSTOMER_WEB_DIR` set to the absolute `web/out` path. Visit `/book/` using a configured salon Host. Unknown or not-live salons receive an unavailable screen. Migration 0006 adds explicit artist schedules/eligibility/blocks and booking-scoped guest contacts; migrations 0001–0005 are unchanged.

See [`DEVELOPMENT`](docs/DEVELOPMENT.md) for setup, the disposable real-browser gate and exact checks. Required deposits fail closed; no payment success is simulated. The generic export bundles no tenant logo; a tenant's branding reference (for example `/assets/ka-nails-logo.png`) needs an approved asset route on its booking Host, which is not yet deployed. No KA Nails booking fact or tenant ID is hard-coded into the customer engine.

## Current state

- The official KA Nails logo is owned by the independent `KA-nails` repository at `public/assets/ka-nails-logo.png` (source SHA-256 `BB2FE1C05EB7183B8B8F55EEE861B06D80256CF5349B2958E1432FA3B83FBF53`). Do not substitute the Fresh Nails brand assets.
- The full owner-supplied product brief is preserved verbatim at `source/PRODUCT_BRIEF.txt`; the handoff records later decisions and verified blockers.
- The existing Fresh Nails website and AI receptionist are separate references. They have not been copied or modified.
- **Hosting target: Microsoft Azure, Central US** (owner decision 2026-10-01, replacing East US 2 because PostgreSQL Flexible Server is offer-restricted for this subscription in East US 2; [ADR-0012](docs/adr/0012-azure-hosting.md), superseding the earlier OCI preference; the separate camera instance `gorgona-node` stays outside this project). M4 adds the Azure design, Bicep IaC, the production container, the Front Door trusted-proxy mode, the governed `frame-ancestors` embedding allowlist and observability. Deployed so far (each step owner-approved): budgets, the shared registry and monitoring, and the AI plane's Central US data plane; the production-parity staging bridge is not deployed yet. Production remains gated behind the approved production-bridge acceptance process. No final production cutover occurs until the required bridge/security/E2E gates pass and the production deployment is explicitly authorized. Every Azure action needs separate owner approval. See [AZURE_ARCHITECTURE](docs/architecture/AZURE_ARCHITECTURE.md), [M4_PLAN](docs/plan/M4_PLAN.md) and [M4_REPORT](docs/plan/M4_REPORT.md).
- Supabase creation is deferred by owner choice because the free organization already has two active projects. Cloudflare is optional for DNS, CDN and edge security; neither provider is required in the proposed core.
- The canonical origin is `alexeyalexandrov2026-tech/-GORGONA-Booking-Platform`. It is public; proprietary-code publication still requires owner approval. M3 is committed locally only. No push or hosted CI run is claimed. See `docs/architecture/RELEASE_PLAN.md` for future infrastructure gates.

For current customer work start with [M3_PLAN](docs/plan/M3_PLAN.md), [ADR-0011](docs/adr/0011_customer_booking.md) and [M3_REPORT](docs/plan/M3_REPORT.md). The [Phase 0 handoff](CLOUD_CODE_HANDOFF.md), [full brief](source/PRODUCT_BRIEF.txt), [initial audit](docs/architecture/INITIAL_AUDIT.md), [target architecture](docs/architecture/TARGET_ARCHITECTURE.md), and [release plan](docs/architecture/RELEASE_PLAN.md) preserve earlier context; later owner instructions and implemented milestone reports govern current work.

## Source material and boundaries

The owner supplied a KA Nails logo and a detailed product brief. The architecture diagram the brief references was supplied on 2026-09-30 and is saved at `assets/architecture/ka-nails-architecture-diagram.webp`; where it shows Supabase or Cloudflare, the later OCI-first decision in the handoff takes precedence (see `docs/plan/M1_PLAN.md`). Any unconfirmed business data, including the location, hours, artist roster, base Hammam booking duration, deposit policy, tax treatment, and domain, remains an owner decision. No customer data or credentials belong in this repository.
