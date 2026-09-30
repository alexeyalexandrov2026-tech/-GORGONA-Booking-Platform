# Multi-tenancy

Status: design only.

KA Nails is a tenant configuration, not a branch in business code. Resolve tenant and actor from an authenticated host/domain mapping or verified session. Never accept an arbitrary tenant ID in a browser request as authorization. Every tenant-owned row carries `tenant_id`; tenant-bound composite foreign keys prevent cross-tenant references. Memberships scope admin roles to tenant and, where needed, location. Public catalog access is limited to published records for the resolved tenant.

Use application authorization plus PostgreSQL RLS for defense in depth. Keep API and migration credentials separate; never expose a bypass-RLS credential to web clients. Set tenant context per transaction and clear it when the connection is returned to a pool. Test read, update, delete, foreign-key injection and storage isolation across two seeded tenants. No policies or tests exist yet.
