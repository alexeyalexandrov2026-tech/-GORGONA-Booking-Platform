-- 0007_tenant_embed_origins: governed allowlist of web origins that may embed a
-- tenant's hosted booking pages. It is emitted as the HTTP header
-- Content-Security-Policy: frame-ancestors (ADR-0012).
--
-- Managed by operators with the owner credential (`gba-db embed-origin`). Changes
-- are audited. Origins are revoked by status, never deleted, so history is kept.
-- The runtime role can only read.
-- Loopback http origins exist for local acceptance only; the application ignores
-- them outside local/test/ci environments.

create table gba.tenant_embed_origins (
    tenant_id  uuid not null references gba.tenants (id),
    id         uuid not null default uuidv7(),
    origin     text not null
               constraint tenant_embed_origins_origin_format check (
                   origin ~ '^https://[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+(:[0-9]{1,5})?$'
                   or origin ~ '^http://(127\.0\.0\.1|localhost)(:[0-9]{1,5})?$'
               ),
    status     text not null default 'approved'
               constraint tenant_embed_origins_status_valid check (status in ('approved', 'revoked')),
    updated_at timestamptz not null default now(),
    primary key (tenant_id, id),
    constraint tenant_embed_origins_unique unique (tenant_id, origin)
);
alter table gba.tenant_embed_origins enable row level security;
alter table gba.tenant_embed_origins force row level security;
create policy tenant_embed_origins_tenant_isolation on gba.tenant_embed_origins
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select on gba.tenant_embed_origins to gba_runtime;

create trigger tenant_embed_origins_audit after insert or update on gba.tenant_embed_origins
    for each row execute function gba.audit_row_change('embed_origin');
