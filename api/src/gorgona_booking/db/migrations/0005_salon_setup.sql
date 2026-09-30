-- 0005_salon_setup: go-live state, business hours, booking policies, governed
-- fact confirmations and branding references (ADR-0010).
--
-- Unknown business facts stay absent (NULL / no rows) and are reported as
-- missing by the readiness check. Nothing here has a business default.

-- Public booking requires an explicit go-live after readiness passes.
alter table gba.tenants
    add column booking_state text not null default 'not_live'
        constraint tenants_booking_state_valid check (booking_state in ('not_live', 'live'));
-- Guarded by the restrictive platform-admin UPDATE policy from 0004.
grant update (booking_state) on gba.tenants to gba_runtime;

-- Business hours per location; ISO weekday (1 = Monday), minutes since local midnight.
create table gba.business_hours (
    tenant_id     uuid not null references gba.tenants (id),
    id            uuid not null default uuidv7(),
    location_id   uuid not null,
    weekday       smallint not null
                  constraint business_hours_weekday_iso check (weekday between 1 and 7),
    opens_minute  integer not null
                  constraint business_hours_opens_range check (opens_minute between 0 and 1439),
    closes_minute integer not null
                  constraint business_hours_closes_range check (closes_minute between 1 and 1440),
    primary key (tenant_id, id),
    constraint business_hours_order check (opens_minute < closes_minute),
    constraint business_hours_location_fk
        foreign key (tenant_id, location_id) references gba.locations (tenant_id, id),
    constraint business_hours_no_overlap
        exclude using gist (tenant_id with =, location_id with =, weekday with =,
                            int4range(opens_minute, closes_minute) with &&)
);
alter table gba.business_hours enable row level security;
alter table gba.business_hours force row level security;
create policy business_hours_tenant_isolation on gba.business_hours
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update, delete on gba.business_hours to gba_runtime;

-- Booking policies. Each is NULL until supplied (NULL = missing).
create table gba.salon_policies (
    tenant_id           uuid primary key references gba.tenants (id),
    cancellation_policy jsonb
                        constraint salon_policies_cancellation_object
                        check (cancellation_policy is null
                               or jsonb_typeof(cancellation_policy) = 'object'),
    deposit_policy      jsonb
                        constraint salon_policies_deposit_object
                        check (deposit_policy is null or jsonb_typeof(deposit_policy) = 'object'),
    booking_rules       jsonb
                        constraint salon_policies_rules_object
                        check (booking_rules is null or jsonb_typeof(booking_rules) = 'object'),
    updated_at          timestamptz not null default now()
);
alter table gba.salon_policies enable row level security;
alter table gba.salon_policies force row level security;
create policy salon_policies_tenant_isolation on gba.salon_policies
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update on gba.salon_policies to gba_runtime;

-- Governed confirmation status of each required fact. No row = not confirmed.
create table gba.salon_fact_confirmations (
    tenant_id   uuid not null references gba.tenants (id),
    id          uuid not null default uuidv7(),
    fact_key    text not null
                constraint salon_fact_confirmations_key_valid
                check (fact_key in ('timezone', 'business_hours', 'staff', 'catalog',
                                    'service_durations', 'cancellation_policy',
                                    'deposit_policy', 'booking_rules', 'domain')),
    status      text not null
                constraint salon_fact_confirmations_status_valid
                check (status in ('confirmed', 'unconfirmed')),
    source_note text
                constraint salon_fact_confirmations_source_length
                check (source_note is null or length(source_note) between 1 and 500),
    recorded_by text not null
                constraint salon_fact_confirmations_recorded_by_length
                check (length(recorded_by) between 1 and 200),
    recorded_at timestamptz not null default now(),
    primary key (tenant_id, fact_key),
    constraint salon_fact_confirmations_id_unique unique (id)
);
alter table gba.salon_fact_confirmations enable row level security;
alter table gba.salon_fact_confirmations force row level security;
create policy salon_fact_confirmations_tenant_isolation on gba.salon_fact_confirmations
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update on gba.salon_fact_confirmations to gba_runtime;
create trigger salon_fact_confirmations_audit
    after insert or update on gba.salon_fact_confirmations
    for each row execute function gba.audit_row_change('salon_fact');

-- Branding references (paths/URIs and digests only; no binaries in the database).
create table gba.salon_branding_refs (
    tenant_id  uuid not null references gba.tenants (id),
    kind       text not null
               constraint salon_branding_refs_kind_valid
               check (kind in ('logo', 'favicon', 'palette', 'typography')),
    asset_ref  text not null
               constraint salon_branding_refs_asset_ref_length check (length(asset_ref) between 1 and 500),
    sha256     text
               constraint salon_branding_refs_sha256_format check (sha256 is null or sha256 ~ '^[0-9a-f]{64}$'),
    updated_at timestamptz not null default now(),
    primary key (tenant_id, kind)
);
alter table gba.salon_branding_refs enable row level security;
alter table gba.salon_branding_refs force row level security;
create policy salon_branding_refs_tenant_isolation on gba.salon_branding_refs
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update on gba.salon_branding_refs to gba_runtime;
