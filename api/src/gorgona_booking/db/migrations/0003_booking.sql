-- 0003_booking: resources, bookings, occupancy allocations under a GiST
-- exclusion constraint, lifecycle rules, audit events and idempotency keys.
--
-- PostgreSQL is the final authority against double booking (ADR-0003):
--   * occupancy is a half-open, bounded, non-empty tstzrange [start, end);
--   * booking_allocations_no_overlap rejects two capacity-reserving
--     allocations (HOLD, CONFIRMED) for the same tenant + resource that
--     overlap, raising SQLSTATE 23P01;
--   * allocations carry their booking's status through a composite FK with
--     ON UPDATE CASCADE, so a status change moves them in or out of the
--     exclusion set atomically;
--   * hold expiry is a transactional status change, never now() in a predicate.

-- Resources (artists, chairs, rooms) -------------------------------------------
create table gba.resources (
    tenant_id    uuid not null references gba.tenants (id),
    id           uuid not null default uuidv7(),
    location_id  uuid not null,
    kind         text not null
                 constraint resources_kind_valid check (kind in ('artist', 'chair', 'room')),
    display_name text not null
                 constraint resources_display_name_length
                 check (length(btrim(display_name)) between 1 and 200),
    is_active    boolean not null default true,
    created_at   timestamptz not null default now(),
    primary key (tenant_id, id),
    constraint resources_location_fk
        foreign key (tenant_id, location_id) references gba.locations (tenant_id, id)
);
alter table gba.resources enable row level security;
alter table gba.resources force row level security;
create policy resources_tenant_isolation on gba.resources
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update on gba.resources to gba_runtime;

-- Bookings -----------------------------------------------------------------------
create table gba.bookings (
    tenant_id       uuid not null references gba.tenants (id),
    id              uuid not null default uuidv7(),
    location_id     uuid not null,
    variant_id      uuid not null,
    status          text not null
                    constraint bookings_status_valid
                    check (status in ('HOLD', 'CONFIRMED', 'CANCELLED', 'EXPIRED')),
    starts_at       timestamptz not null,
    ends_at         timestamptz not null,
    hold_expires_at timestamptz,
    total_cents     integer not null
                    constraint bookings_total_nonnegative check (total_cents >= 0),
    currency        text not null
                    constraint bookings_currency_iso check (currency ~ '^[A-Z]{3}$'),
    quote           jsonb not null
                    constraint bookings_quote_object check (jsonb_typeof(quote) = 'object'),
    created_by      text not null
                    constraint bookings_created_by_length check (length(created_by) between 1 and 200),
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    primary key (tenant_id, id),
    -- Referenced by booking_allocations to carry status (see header).
    constraint bookings_status_key unique (tenant_id, id, status),
    constraint bookings_interval_valid check (ends_at > starts_at),
    constraint bookings_hold_has_expiry check (status <> 'HOLD' or hold_expires_at is not null),
    constraint bookings_location_fk
        foreign key (tenant_id, location_id) references gba.locations (tenant_id, id),
    constraint bookings_variant_fk
        foreign key (tenant_id, variant_id) references gba.service_variants (tenant_id, id)
);
create index bookings_due_holds_idx on gba.bookings (tenant_id, hold_expires_at)
    where status = 'HOLD';

-- Lifecycle: HOLD -> CONFIRMED | CANCELLED | EXPIRED; CONFIRMED -> CANCELLED.
-- CANCELLED and EXPIRED are terminal. Identity and interval are immutable.
create function gba.enforce_booking_transition() returns trigger
    language plpgsql
as $$
begin
    if tg_op = 'INSERT' then
        if new.status not in ('HOLD', 'CONFIRMED') then
            raise exception using
                errcode = 'check_violation',
                message = format('a booking cannot be created as %s', new.status),
                constraint = 'bookings_initial_status';
        end if;
        return new;
    end if;

    if old.status in ('CANCELLED', 'EXPIRED') then
        raise exception using
            errcode = 'check_violation',
            message = format('booking is %s and can no longer change', old.status),
            constraint = 'bookings_terminal_immutable';
    end if;
    if new.tenant_id is distinct from old.tenant_id
       or new.id is distinct from old.id
       or new.starts_at is distinct from old.starts_at
       or new.ends_at is distinct from old.ends_at then
        raise exception using
            errcode = 'check_violation',
            message = 'booking identity and interval are immutable',
            constraint = 'bookings_identity_immutable';
    end if;
    if new.status is distinct from old.status and not (
        (old.status = 'HOLD' and new.status in ('CONFIRMED', 'CANCELLED', 'EXPIRED'))
        or (old.status = 'CONFIRMED' and new.status = 'CANCELLED')
    ) then
        raise exception using
            errcode = 'check_violation',
            message = format('booking status cannot change from %s to %s', old.status, new.status),
            constraint = 'bookings_status_transition';
    end if;
    new.updated_at := now();
    return new;
end;
$$;
revoke all on function gba.enforce_booking_transition() from public;
create trigger bookings_enforce_transition
    before insert or update on gba.bookings
    for each row execute function gba.enforce_booking_transition();

alter table gba.bookings enable row level security;
alter table gba.bookings force row level security;
create policy bookings_tenant_isolation on gba.bookings
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
-- No DELETE: history is kept; state changes are UPDATEs checked above.
grant select, insert, update on gba.bookings to gba_runtime;

-- Occupancy ----------------------------------------------------------------------
create table gba.booking_allocations (
    tenant_id      uuid not null references gba.tenants (id),
    id             uuid not null default uuidv7(),
    booking_id     uuid not null,
    booking_status text not null,
    resource_id    uuid not null,
    during         tstzrange not null,
    primary key (tenant_id, id),
    constraint booking_allocations_booking_fk
        foreign key (tenant_id, booking_id, booking_status)
        references gba.bookings (tenant_id, id, status)
        on update cascade,
    constraint booking_allocations_resource_fk
        foreign key (tenant_id, resource_id) references gba.resources (tenant_id, id),
    constraint booking_allocations_one_per_resource unique (tenant_id, booking_id, resource_id),
    constraint booking_allocations_half_open check (
        not isempty(during)
        and lower_inc(during) and not upper_inc(during)
        and not lower_inf(during) and not upper_inf(during)
    ),
    constraint booking_allocations_no_overlap
        exclude using gist (tenant_id with =, resource_id with =, during with &&)
        where (booking_status in ('HOLD', 'CONFIRMED'))
);
alter table gba.booking_allocations enable row level security;
alter table gba.booking_allocations force row level security;
create policy booking_allocations_tenant_isolation on gba.booking_allocations
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
-- Insert only: status follows the booking via the cascade, never set directly.
grant select, insert on gba.booking_allocations to gba_runtime;

-- Audit events: written by trigger for every insert and status change --------
create table gba.booking_events (
    tenant_id   uuid not null references gba.tenants (id),
    id          uuid not null default uuidv7(),
    booking_id  uuid not null,
    from_status text,
    to_status   text not null,
    actor       text not null constraint booking_events_actor_length
                check (length(actor) between 1 and 200),
    reason      text not null constraint booking_events_reason_length
                check (length(reason) between 1 and 200),
    request_id  text,
    occurred_at timestamptz not null default now(),
    primary key (tenant_id, id),
    constraint booking_events_booking_fk
        foreign key (tenant_id, booking_id) references gba.bookings (tenant_id, id)
);
alter table gba.booking_events enable row level security;
alter table gba.booking_events force row level security;
create policy booking_events_tenant_isolation on gba.booking_events
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert on gba.booking_events to gba_runtime;

-- Actor, reason and request ID come from transaction-local settings
-- (gba.actor, gba.reason, gba.request_id) set by the application.
create function gba.record_booking_event() returns trigger
    language plpgsql
as $$
begin
    if tg_op = 'UPDATE' and new.status is not distinct from old.status then
        return null;
    end if;
    insert into gba.booking_events
        (tenant_id, booking_id, from_status, to_status, actor, reason, request_id)
    values (
        new.tenant_id,
        new.id,
        case when tg_op = 'UPDATE' then old.status end,
        new.status,
        coalesce(nullif(pg_catalog.current_setting('gba.actor', true), ''), current_user::text),
        coalesce(nullif(pg_catalog.current_setting('gba.reason', true), ''), 'unspecified'),
        nullif(pg_catalog.current_setting('gba.request_id', true), '')
    );
    return null;
end;
$$;
revoke all on function gba.record_booking_event() from public;
create trigger bookings_record_event
    after insert or update of status on gba.bookings
    for each row execute function gba.record_booking_event();

-- Idempotency (ADR-0004) ---------------------------------------------------------
create table gba.idempotency_keys (
    tenant_id       uuid not null references gba.tenants (id),
    actor_key       text not null
                    constraint idempotency_keys_actor_length check (length(actor_key) between 1 and 200),
    operation       text not null
                    constraint idempotency_keys_operation_format
                    check (operation ~ '^[a-z][a-z0-9_.]{0,62}$'),
    idempotency_key text not null
                    constraint idempotency_keys_key_format
                    check (idempotency_key ~ '^[A-Za-z0-9._:-]{8,255}$'),
    request_hash    text not null
                    constraint idempotency_keys_hash_format check (request_hash ~ '^[0-9a-f]{64}$'),
    response_status integer,
    response_body   jsonb,
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null default now() + interval '24 hours',
    primary key (tenant_id, actor_key, operation, idempotency_key),
    constraint idempotency_keys_response_complete
        check ((response_status is null) = (response_body is null))
);
alter table gba.idempotency_keys enable row level security;
alter table gba.idempotency_keys force row level security;
create policy idempotency_keys_tenant_isolation on gba.idempotency_keys
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update on gba.idempotency_keys to gba_runtime;
