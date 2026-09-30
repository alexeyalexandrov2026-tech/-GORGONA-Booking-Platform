# ADR-0006: Hosting direction

- Status: Accepted as direction (2026-09-30); nothing is provisioned

## Decision

- **OCI** is the production target for the web/API and PostgreSQL. No OCI resource is created until a live compartment or instance is verified and cost, blast radius and rollback are reviewed with the owner. The former `gorgona-node` and its IP are not targets.
- **Supabase** is deferred. The design does not depend on it.
- **Cloudflare** is optional, for DNS/CDN/WAF only.
- The supplied architecture diagram (`assets/architecture/ka-nails-architecture-diagram.webp`) is reference material. Where it shows "PostgreSQL / Supabase" or "Cloudflare Edge" as required components, this ADR wins.
- The application does not depend on Docker. Docker Compose is a local-development convenience, and CI runs a PostgreSQL 18 container for parity.
