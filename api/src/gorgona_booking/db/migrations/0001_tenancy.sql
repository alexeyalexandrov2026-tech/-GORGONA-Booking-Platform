-- 0001_tenancy: schema, transaction-scoped tenant context, tenants, host routing,
-- locations and memberships.
--
-- Applied by the owner role in a single transaction. Requires the NOLOGIN group
-- role gba_runtime, created by `gba-db bootstrap`. Runtime privileges are granted
-- to gba_runtime only; the owner role is never used by the API.

create schema gba;
revoke all on schema gba from public;
grant usage on schema gba to gba_runtime;

-- Trusted extension (the database owner may create it). Supplies the btree
-- operator classes that let GiST combine scalar equality with range overlap.
create extension if not exists btree_gist;

-- Tenant context -------------------------------------------------------------
-- Set only with set_config('gba.tenant_id', <uuid>, true), i.e. transaction-local.
-- Returns NULL when unset, so every tenant policy matches nothing.
create function gba.current_tenant_id() returns uuid
    language sql
    stable
    parallel safe
as $$
    select nullif(pg_catalog.current_setting('gba.tenant_id', true), '')::uuid
$$;
revoke all on function gba.current_tenant_id() from public;
grant execute on function gba.current_tenant_id() to gba_runtime;

-- Tenants (salons) -----------------------------------------------------------
create table gba.tenants (
    id           uuid primary key default uuidv7(),
    slug         text not null unique
                 constraint tenants_slug_format
                 check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' and length(slug) <= 63),
    display_name text not null
                 constraint tenants_display_name_length
                 check (length(btrim(display_name)) between 1 and 200),
    status       text not null default 'active'
                 constraint tenants_status_valid check (status in ('active', 'suspended')),
    created_at   timestamptz not null default now()
);
alter table gba.tenants enable row level security;
alter table gba.tenants force row level security;
create policy tenants_tenant_isolation on gba.tenants
    using (id = gba.current_tenant_id())
    with check (id = gba.current_tenant_id());
-- Tenants are provisioned by the owner role, never by the runtime.
grant select on gba.tenants to gba_runtime;

-- Host routing ---------------------------------------------------------------
-- Resolves a request host to a tenant before any tenant context exists, so
-- reads are deliberately not tenant-filtered. It holds hostnames and IDs only,
-- and the runtime role cannot write to it.
create table gba.tenant_hosts (
    host       text primary key
               constraint tenant_hosts_host_format
               check (host ~ '^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$'
                      and length(host) <= 260),
    tenant_id  uuid not null references gba.tenants (id),
    created_at timestamptz not null default now()
);
create index tenant_hosts_tenant_id_idx on gba.tenant_hosts (tenant_id);
alter table gba.tenant_hosts enable row level security;
alter table gba.tenant_hosts force row level security;
create policy tenant_hosts_resolve on gba.tenant_hosts
    for select using (true);
create policy tenant_hosts_insert on gba.tenant_hosts
    for insert with check (tenant_id = gba.current_tenant_id());
create policy tenant_hosts_update on gba.tenant_hosts
    for update using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
create policy tenant_hosts_delete on gba.tenant_hosts
    for delete using (tenant_id = gba.current_tenant_id());
grant select on gba.tenant_hosts to gba_runtime;

-- Locations ------------------------------------------------------------------
create function gba.assert_valid_timezone() returns trigger
    language plpgsql
as $$
begin
    if new.timezone !~ '^(UTC|[A-Za-z_]+(/[A-Za-z0-9_+-]+)+)$'
       or not exists (select 1 from pg_catalog.pg_timezone_names where name = new.timezone) then
        raise exception using
            errcode = 'invalid_parameter_value',
            message = format('invalid IANA time zone: %s', new.timezone),
            constraint = 'locations_timezone_iana';
    end if;
    return new;
end;
$$;
revoke all on function gba.assert_valid_timezone() from public;

create table gba.locations (
    tenant_id  uuid not null references gba.tenants (id),
    id         uuid not null default uuidv7(),
    name       text not null
               constraint locations_name_length check (length(btrim(name)) between 1 and 200),
    timezone   text not null,
    created_at timestamptz not null default now(),
    primary key (tenant_id, id)
);
create trigger locations_timezone_iana
    before insert or update of timezone on gba.locations
    for each row execute function gba.assert_valid_timezone();
alter table gba.locations enable row level security;
alter table gba.locations force row level security;
create policy locations_tenant_isolation on gba.locations
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update, delete on gba.locations to gba_runtime;

-- Memberships (staff access to a tenant, optionally scoped to one location) --
create table gba.memberships (
    tenant_id   uuid not null references gba.tenants (id),
    id          uuid not null default uuidv7(),
    subject     text not null
                constraint memberships_subject_length check (length(subject) between 1 and 255),
    role        text not null
                constraint memberships_role_valid
                check (role in ('owner', 'manager', 'front_desk', 'artist')),
    location_id uuid,
    created_at  timestamptz not null default now(),
    primary key (tenant_id, id),
    constraint memberships_unique_grant
        unique nulls not distinct (tenant_id, subject, role, location_id),
    constraint memberships_location_fk
        foreign key (tenant_id, location_id) references gba.locations (tenant_id, id)
);
alter table gba.memberships enable row level security;
alter table gba.memberships force row level security;
create policy memberships_tenant_isolation on gba.memberships
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update, delete on gba.memberships to gba_runtime;
