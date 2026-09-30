# GORGONA Booking AI

KA Nails is the first planned tenant of a reusable appointment platform. This repository contains the **Phase 0 audit and architecture package**. No booking, payment, AI, customer, or admin service has been implemented or deployed.

## Current state

- The official KA Nails logo is preserved at `assets/brand/ka-nails-logo.png` (source SHA-256 `BB2FE1C05EB7183B8B8F55EEE861B06D80256CF5349B2958E1432FA3B83FBF53`). Do not substitute the Fresh Nails brand assets.
- The full owner-supplied product brief is preserved verbatim at `source/PRODUCT_BRIEF.txt`; the handoff records later decisions and verified blockers.
- The existing Fresh Nails website and AI receptionist are separate references. They have not been copied or modified.
- Oracle Cloud Infrastructure is the preferred proposed host after the owner's question. A live instance and access are not verified; the old `gorgona-node` was previously terminated and the current CLI session is expired.
- Supabase creation is deferred by owner choice because the free organization already has two active projects. Cloudflare is optional for DNS, CDN and edge security; neither provider is required in the proposed core.
- GitHub and Cloudflare project provisioning require an authenticated session; see `docs/architecture/RELEASE_PLAN.md`.

Start with [the handoff](CLOUD_CODE_HANDOFF.md), [full brief](source/PRODUCT_BRIEF.txt), [audit](docs/architecture/INITIAL_AUDIT.md), [target architecture](docs/architecture/TARGET_ARCHITECTURE.md), and [release plan](docs/architecture/RELEASE_PLAN.md). All architecture decisions are proposals until the relevant implementation and verification gates pass.

## Source material and boundaries

The owner supplied a KA Nails logo and a detailed product brief. The brief references a second architecture diagram, but that image was not available as a file in this task. Any unconfirmed business data, including the location, hours, artist roster, base Hammam booking duration, deposit policy, tax treatment, and domain, remains an owner decision. No customer data or credentials belong in this repository.
