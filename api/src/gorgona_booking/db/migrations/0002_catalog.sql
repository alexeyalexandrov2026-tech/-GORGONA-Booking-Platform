-- 0002_catalog: service components, services, bookable variants, add-ons and
-- composition rules.
--
-- Bookability is a database invariant (ADR-0005): a variant or add-on cannot be
-- bookable until its booking duration is known, and only when published.
-- Public duration ranges are display data and never drive scheduling.
-- Cross-row composition rules (REQUIRES / CONFLICTS_WITH / no duplicate
-- component) are enforced by the domain quote engine.

create function gba.bump_revision() returns trigger
    language plpgsql
as $$
begin
    if row(new.*) is distinct from row(old.*) then
        new.revision := old.revision + 1;
        new.updated_at := now();
    end if;
    return new;
end;
$$;
revoke all on function gba.bump_revision() from public;

-- Components: what a service or add-on delivers (e.g. HEEL_CARE). -----------
create table gba.service_components (
    tenant_id  uuid not null references gba.tenants (id),
    code       text not null
               constraint service_components_code_format check (code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    name       text not null
               constraint service_components_name_length check (length(btrim(name)) between 1 and 200),
    created_at timestamptz not null default now(),
    primary key (tenant_id, code)
);
alter table gba.service_components enable row level security;
alter table gba.service_components force row level security;
create policy service_components_tenant_isolation on gba.service_components
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update on gba.service_components to gba_runtime;

-- Services: a family such as "Hammam". -----------------------------------------
create table gba.services (
    tenant_id  uuid not null references gba.tenants (id),
    id         uuid not null default uuidv7(),
    code       text not null
               constraint services_code_format check (code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    name       text not null
               constraint services_name_length check (length(btrim(name)) between 1 and 200),
    created_at timestamptz not null default now(),
    primary key (tenant_id, id),
    constraint services_code_unique unique (tenant_id, code)
);
alter table gba.services enable row level security;
alter table gba.services force row level security;
create policy services_tenant_isolation on gba.services
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update on gba.services to gba_runtime;

-- Variants: the bookable unit (e.g. "Hammam Luxury + Gel"). -------------------
create table gba.service_variants (
    tenant_id                    uuid not null references gba.tenants (id),
    id                           uuid not null default uuidv7(),
    service_id                   uuid not null,
    code                         text not null
                                 constraint service_variants_code_format
                                 check (code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    name                         text not null
                                 constraint service_variants_name_length
                                 check (length(btrim(name)) between 1 and 200),
    status                       text not null default 'draft'
                                 constraint service_variants_status_valid
                                 check (status in ('draft', 'published', 'archived')),
    price_cents                  integer not null
                                 constraint service_variants_price_nonnegative check (price_cents >= 0),
    currency                     text not null
                                 constraint service_variants_currency_iso check (currency ~ '^[A-Z]{3}$'),
    display_duration_min_minutes integer,
    display_duration_max_minutes integer,
    booking_duration_minutes     integer
                                 constraint service_variants_booking_duration_range
                                 check (booking_duration_minutes between 1 and 720),
    is_bookable                  boolean not null default false,
    revision                     integer not null default 1,
    created_at                   timestamptz not null default now(),
    updated_at                   timestamptz not null default now(),
    primary key (tenant_id, id),
    constraint service_variants_code_unique unique (tenant_id, code),
    constraint service_variants_service_fk
        foreign key (tenant_id, service_id) references gba.services (tenant_id, id),
    constraint service_variants_display_range check (
        (display_duration_min_minutes is null) = (display_duration_max_minutes is null)
        and (display_duration_min_minutes is null
             or (display_duration_min_minutes > 0
                 and display_duration_min_minutes <= display_duration_max_minutes))
    ),
    constraint service_variants_bookable_requires_duration
        check (not is_bookable or booking_duration_minutes is not null),
    constraint service_variants_bookable_requires_published
        check (not is_bookable or status = 'published')
);
create trigger service_variants_revision
    before update on gba.service_variants
    for each row execute function gba.bump_revision();
alter table gba.service_variants enable row level security;
alter table gba.service_variants force row level security;
create policy service_variants_tenant_isolation on gba.service_variants
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update on gba.service_variants to gba_runtime;

create table gba.variant_components (
    tenant_id      uuid not null references gba.tenants (id),
    variant_id     uuid not null,
    component_code text not null,
    primary key (tenant_id, variant_id, component_code),
    constraint variant_components_variant_fk
        foreign key (tenant_id, variant_id) references gba.service_variants (tenant_id, id),
    constraint variant_components_component_fk
        foreign key (tenant_id, component_code) references gba.service_components (tenant_id, code)
);
alter table gba.variant_components enable row level security;
alter table gba.variant_components force row level security;
create policy variant_components_tenant_isolation on gba.variant_components
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, delete on gba.variant_components to gba_runtime;

-- Add-ons ----------------------------------------------------------------------
create table gba.add_ons (
    tenant_id              uuid not null references gba.tenants (id),
    id                     uuid not null default uuidv7(),
    code                   text not null
                           constraint add_ons_code_format check (code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    name                   text not null
                           constraint add_ons_name_length check (length(btrim(name)) between 1 and 200),
    status                 text not null default 'draft'
                           constraint add_ons_status_valid
                           check (status in ('draft', 'published', 'archived')),
    price_cents            integer not null
                           constraint add_ons_price_nonnegative check (price_cents >= 0),
    currency               text not null
                           constraint add_ons_currency_iso check (currency ~ '^[A-Z]{3}$'),
    duration_delta_minutes integer
                           constraint add_ons_duration_delta_range
                           check (duration_delta_minutes between 0 and 240),
    is_bookable            boolean not null default false,
    revision               integer not null default 1,
    created_at             timestamptz not null default now(),
    updated_at             timestamptz not null default now(),
    primary key (tenant_id, id),
    constraint add_ons_code_unique unique (tenant_id, code),
    constraint add_ons_bookable_requires_duration
        check (not is_bookable or duration_delta_minutes is not null),
    constraint add_ons_bookable_requires_published
        check (not is_bookable or status = 'published')
);
create trigger add_ons_revision
    before update on gba.add_ons
    for each row execute function gba.bump_revision();
alter table gba.add_ons enable row level security;
alter table gba.add_ons force row level security;
create policy add_ons_tenant_isolation on gba.add_ons
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update on gba.add_ons to gba_runtime;

create table gba.add_on_rules (
    tenant_id      uuid not null references gba.tenants (id),
    add_on_id      uuid not null,
    relation       text not null
                   constraint add_on_rules_relation_valid
                   check (relation in ('PROVIDES', 'REQUIRES', 'CONFLICTS_WITH')),
    component_code text not null,
    primary key (tenant_id, add_on_id, relation, component_code),
    constraint add_on_rules_add_on_fk
        foreign key (tenant_id, add_on_id) references gba.add_ons (tenant_id, id),
    constraint add_on_rules_component_fk
        foreign key (tenant_id, component_code) references gba.service_components (tenant_id, code)
);
alter table gba.add_on_rules enable row level security;
alter table gba.add_on_rules force row level security;
create policy add_on_rules_tenant_isolation on gba.add_on_rules
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, delete on gba.add_on_rules to gba_runtime;
