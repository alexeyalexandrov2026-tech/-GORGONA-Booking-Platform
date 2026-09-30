-- Explicit customer schedules and booking-scoped guest capability. No business defaults.
create table gba.resource_services (
    tenant_id uuid not null references gba.tenants (id),
    resource_id uuid not null,
    service_id uuid not null,
    primary key (tenant_id, resource_id, service_id),
    foreign key (tenant_id, resource_id) references gba.resources (tenant_id, id),
    foreign key (tenant_id, service_id) references gba.services (tenant_id, id)
);
alter table gba.resource_services enable row level security;
alter table gba.resource_services force row level security;
create policy resource_services_tenant_isolation on gba.resource_services
    using (tenant_id = gba.current_tenant_id()) with check (tenant_id = gba.current_tenant_id());
grant select, insert, update, delete on gba.resource_services to gba_runtime;

create table gba.resource_hours (
    tenant_id uuid not null references gba.tenants (id),
    id uuid not null default uuidv7(),
    resource_id uuid not null,
    weekday smallint not null check (weekday between 1 and 7),
    opens_minute integer not null check (opens_minute between 0 and 1439),
    closes_minute integer not null check (closes_minute between 1 and 1440),
    primary key (tenant_id, id),
    check (opens_minute < closes_minute),
    foreign key (tenant_id, resource_id) references gba.resources (tenant_id, id),
    exclude using gist (tenant_id with =, resource_id with =, weekday with =,
                       int4range(opens_minute, closes_minute) with &&)
);
alter table gba.resource_hours enable row level security;
alter table gba.resource_hours force row level security;
create policy resource_hours_tenant_isolation on gba.resource_hours
    using (tenant_id = gba.current_tenant_id()) with check (tenant_id = gba.current_tenant_id());
grant select, insert, update, delete on gba.resource_hours to gba_runtime;

create table gba.resource_blocks (
    tenant_id uuid not null references gba.tenants (id),
    id uuid not null default uuidv7(),
    resource_id uuid not null,
    starts_at timestamptz not null,
    ends_at timestamptz not null,
    primary key (tenant_id, id),
    check (starts_at < ends_at),
    foreign key (tenant_id, resource_id) references gba.resources (tenant_id, id)
);
create index resource_blocks_schedule on gba.resource_blocks
    using gist (tenant_id, resource_id, tstzrange(starts_at, ends_at, '[)'));
alter table gba.resource_blocks enable row level security;
alter table gba.resource_blocks force row level security;
create policy resource_blocks_tenant_isolation on gba.resource_blocks
    using (tenant_id = gba.current_tenant_id()) with check (tenant_id = gba.current_tenant_id());
grant select, insert, update, delete on gba.resource_blocks to gba_runtime;

create table gba.booking_customers (
    tenant_id uuid not null references gba.tenants (id),
    booking_id uuid not null,
    capability_hash text not null check (capability_hash ~ '^[0-9a-f]{64}$'),
    customer_name text check (length(customer_name) between 1 and 100),
    email text check (length(email) between 3 and 254),
    phone text check (length(phone) between 7 and 32),
    primary key (tenant_id, booking_id),
    foreign key (tenant_id, booking_id) references gba.bookings (tenant_id, id),
    check ((customer_name is null and email is null and phone is null)
           or (customer_name is not null and email is not null and phone is not null))
);
alter table gba.booking_customers enable row level security;
alter table gba.booking_customers force row level security;
create policy booking_customers_tenant_isolation on gba.booking_customers
    using (tenant_id = gba.current_tenant_id()) with check (tenant_id = gba.current_tenant_id());
grant select, insert on gba.booking_customers to gba_runtime;
grant update (customer_name, email, phone) on gba.booking_customers to gba_runtime;
