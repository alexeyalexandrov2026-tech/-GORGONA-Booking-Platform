-- 0004_identity: users, external identities, platform roles, identity-bound
-- memberships, invitations and an append-only audit log (ADR-0008, ADR-0009).
--
-- Cross-tenant lookups never use privileged (definer-rights) functions. The app sets
-- transaction-local context after verifying a bearer token:
--   gba.user_id                    the authenticated user
--   gba.auth_issuer / auth_subject the verified token's issuer and subject
-- and additional RLS policies expose only the caller's own rows.

-- Context helpers -----------------------------------------------------------
create function gba.current_user_id() returns uuid
    language sql
    stable
    parallel safe
as $$
    select nullif(pg_catalog.current_setting('gba.user_id', true), '')::uuid
$$;
create function gba.current_auth_issuer() returns text
    language sql
    stable
    parallel safe
as $$
    select nullif(pg_catalog.current_setting('gba.auth_issuer', true), '')
$$;
create function gba.current_auth_subject() returns text
    language sql
    stable
    parallel safe
as $$
    select nullif(pg_catalog.current_setting('gba.auth_subject', true), '')
$$;
revoke all on function gba.current_user_id() from public;
revoke all on function gba.current_auth_issuer() from public;
revoke all on function gba.current_auth_subject() from public;
grant execute on function gba.current_user_id() to gba_runtime;
grant execute on function gba.current_auth_issuer() to gba_runtime;
grant execute on function gba.current_auth_subject() to gba_runtime;

-- Users (platform-level identity; one row per person) ------------------------
create table gba.users (
    id               uuid primary key default uuidv7(),
    display_name     text not null
                     constraint users_display_name_length
                     check (length(btrim(display_name)) between 1 and 200),
    email_normalized text
                     constraint users_email_normalized
                     check (email_normalized is null
                            or (email_normalized = lower(btrim(email_normalized))
                                and email_normalized ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
                                and length(email_normalized) <= 320)),
    status           text not null default 'active'
                     constraint users_status_valid check (status in ('active', 'disabled')),
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

-- Memberships become identity-bound (the M1 placeholder `subject` is replaced).
alter table gba.memberships drop constraint memberships_unique_grant;
alter table gba.memberships drop column subject;
alter table gba.memberships
    add column user_id uuid not null,
    add column status text not null default 'active'
        constraint memberships_status_valid check (status in ('active', 'suspended', 'revoked')),
    add column updated_at timestamptz not null default now(),
    add constraint memberships_user_fk foreign key (user_id) references gba.users (id);
create unique index memberships_one_live_per_user
    on gba.memberships (tenant_id, user_id) where status <> 'revoked';
create index memberships_user_idx on gba.memberships (user_id);

create function gba.enforce_membership_transition() returns trigger
    language plpgsql
as $$
begin
    if new.tenant_id is distinct from old.tenant_id
       or new.id is distinct from old.id
       or new.user_id is distinct from old.user_id then
        raise exception using
            errcode = 'check_violation',
            message = 'membership identity is immutable',
            constraint = 'memberships_identity_immutable';
    end if;
    if old.status = 'revoked' then
        raise exception using
            errcode = 'check_violation',
            message = 'a revoked membership can no longer change',
            constraint = 'memberships_revoked_terminal';
    end if;
    new.updated_at := now();
    return new;
end;
$$;
revoke all on function gba.enforce_membership_transition() from public;
create trigger memberships_enforce_transition
    before update on gba.memberships
    for each row execute function gba.enforce_membership_transition();

-- A caller may read their own memberships in any salon (for "which salons am I in").
create policy memberships_self_read on gba.memberships
    for select using (user_id = gba.current_user_id());
-- History is kept: memberships are revoked, never deleted.
revoke delete on gba.memberships from gba_runtime;

alter table gba.users enable row level security;
alter table gba.users force row level security;
create policy users_self on gba.users
    using (id = gba.current_user_id())
    with check (id = gba.current_user_id());
create policy users_salon_members on gba.users
    for select using (exists (
        select 1 from gba.memberships m
        where m.user_id = users.id and m.tenant_id = gba.current_tenant_id()));
grant select, insert on gba.users to gba_runtime;
grant update (display_name, email_normalized, updated_at) on gba.users to gba_runtime;

-- External identities ---------------------------------------------------------
create table gba.user_identities (
    id         uuid primary key default uuidv7(),
    user_id    uuid not null references gba.users (id),
    issuer     text not null
               constraint user_identities_issuer_format
               check (issuer ~ '^https?://\S+$' and length(issuer) <= 500),
    subject    text not null
               constraint user_identities_subject_length check (length(subject) between 1 and 255),
    created_at timestamptz not null default now(),
    -- One external account can never be linked to two users.
    constraint user_identities_issuer_subject_key unique (issuer, subject)
);
create index user_identities_user_idx on gba.user_identities (user_id);
alter table gba.user_identities enable row level security;
alter table gba.user_identities force row level security;
create policy user_identities_token_match on gba.user_identities
    for select using (issuer = gba.current_auth_issuer() and subject = gba.current_auth_subject());
create policy user_identities_self_read on gba.user_identities
    for select using (user_id = gba.current_user_id());
-- Linking is possible only for the verified token's own issuer/subject.
create policy user_identities_link on gba.user_identities
    for insert with check (user_id = gba.current_user_id()
                           and issuer = gba.current_auth_issuer()
                           and subject = gba.current_auth_subject());
grant select, insert on gba.user_identities to gba_runtime;

-- Platform roles (granted owner-side only) ------------------------------------
create table gba.platform_roles (
    id         uuid primary key default uuidv7(),
    user_id    uuid not null references gba.users (id),
    role       text not null
               constraint platform_roles_role_valid check (role in ('platform_admin')),
    granted_by text not null
               constraint platform_roles_granted_by_length check (length(granted_by) between 1 and 200),
    granted_at timestamptz not null default now(),
    revoked_by text,
    revoked_at timestamptz,
    constraint platform_roles_revocation_complete check ((revoked_at is null) = (revoked_by is null))
);
create unique index platform_roles_one_active
    on gba.platform_roles (user_id, role) where revoked_at is null;
alter table gba.platform_roles enable row level security;
alter table gba.platform_roles force row level security;
create policy platform_roles_self on gba.platform_roles
    using (user_id = gba.current_user_id())
    with check (user_id = gba.current_user_id());
grant select on gba.platform_roles to gba_runtime;

-- Invitations (tenant-owned; only a SHA-256 of the token is stored) -----------
create table gba.invitations (
    tenant_id              uuid not null references gba.tenants (id),
    id                     uuid not null default uuidv7(),
    email_normalized       text not null
                           constraint invitations_email_normalized
                           check (email_normalized = lower(btrim(email_normalized))
                                  and email_normalized ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
                                  and length(email_normalized) <= 320),
    role                   text not null
                           constraint invitations_role_valid
                           check (role in ('owner', 'manager', 'front_desk', 'artist')),
    token_sha256           text not null
                           constraint invitations_token_sha256_format
                           check (token_sha256 ~ '^[0-9a-f]{64}$'),
    status                 text not null default 'pending'
                           constraint invitations_status_valid
                           check (status in ('pending', 'accepted', 'revoked', 'expired')),
    expires_at             timestamptz not null,
    created_at             timestamptz not null default now(),
    accepted_by_user_id    uuid references gba.users (id),
    accepted_membership_id uuid,
    accepted_at            timestamptz,
    primary key (tenant_id, id),
    constraint invitations_token_unique unique (token_sha256),
    constraint invitations_expiry_after_creation check (expires_at > created_at),
    constraint invitations_acceptance_complete check (
        (status = 'accepted') = (accepted_by_user_id is not null
                                 and accepted_membership_id is not null
                                 and accepted_at is not null)),
    constraint invitations_membership_fk
        foreign key (tenant_id, accepted_membership_id) references gba.memberships (tenant_id, id)
);
create unique index invitations_one_pending_per_email
    on gba.invitations (tenant_id, email_normalized) where status = 'pending';

create function gba.enforce_invitation_transition() returns trigger
    language plpgsql
as $$
begin
    if new.tenant_id is distinct from old.tenant_id or new.id is distinct from old.id
       or new.token_sha256 is distinct from old.token_sha256
       or new.email_normalized is distinct from old.email_normalized
       or new.role is distinct from old.role then
        raise exception using
            errcode = 'check_violation',
            message = 'invitation identity is immutable',
            constraint = 'invitations_identity_immutable';
    end if;
    if old.status <> 'pending' then
        raise exception using
            errcode = 'check_violation',
            message = format('invitation is %s and can no longer change', old.status),
            constraint = 'invitations_terminal_immutable';
    end if;
    return new;
end;
$$;
revoke all on function gba.enforce_invitation_transition() from public;
create trigger invitations_enforce_transition
    before update on gba.invitations
    for each row execute function gba.enforce_invitation_transition();
alter table gba.invitations enable row level security;
alter table gba.invitations force row level security;
create policy invitations_tenant_isolation on gba.invitations
    using (tenant_id = gba.current_tenant_id())
    with check (tenant_id = gba.current_tenant_id());
grant select, insert, update on gba.invitations to gba_runtime;

-- Audit log (append-only for the runtime role) --------------------------------
-- tenant_id NULL marks a platform-scope event (e.g. a user or platform role).
create table gba.audit_events (
    id          uuid primary key default uuidv7(),
    tenant_id   uuid references gba.tenants (id),
    actor       text not null
                constraint audit_events_actor_length check (length(actor) between 1 and 200),
    action      text not null
                constraint audit_events_action_format
                check (action ~ '^[a-z][a-z_]*(\.[a-z][a-z_]*)+$'),
    target_type text not null
                constraint audit_events_target_type_length check (length(target_type) between 1 and 60),
    target_id   text not null
                constraint audit_events_target_id_length check (length(target_id) between 1 and 200),
    details     jsonb not null default '{}'::jsonb
                constraint audit_events_details_object check (jsonb_typeof(details) = 'object'),
    request_id  text,
    occurred_at timestamptz not null default now()
);
create index audit_events_tenant_time_idx on gba.audit_events (tenant_id, occurred_at);
alter table gba.audit_events enable row level security;
alter table gba.audit_events force row level security;
create policy audit_events_scope on gba.audit_events
    using (tenant_id is not distinct from gba.current_tenant_id())
    with check (tenant_id is not distinct from gba.current_tenant_id());
grant select, insert on gba.audit_events to gba_runtime;

-- Row-change audit trigger. Secrets and personal data are excluded from details;
-- actor and request ID come from transaction-local gba.actor / gba.request_id.
create function gba.audit_row_change() returns trigger
    language plpgsql
as $$
declare
    excluded constant text[] := array['token_sha256', 'email_normalized', 'created_at',
                                      'updated_at', 'granted_at'];
    v_new     jsonb := to_jsonb(new) - excluded;
    v_old     jsonb;
    v_details jsonb;
    v_tenant  uuid;
begin
    if tg_op = 'UPDATE' then
        v_old := to_jsonb(old) - excluded;
        select coalesce(jsonb_object_agg(n.key, jsonb_build_object('from', v_old -> n.key,
                                                                   'to', n.value)),
                        '{}'::jsonb)
          into v_details
          from jsonb_each(v_new) as n
         where v_old -> n.key is distinct from n.value;
        if v_details = '{}'::jsonb then
            return null;
        end if;
    else
        v_details := v_new;
    end if;
    if tg_table_name = 'tenants' then
        v_tenant := (to_jsonb(new) ->> 'id')::uuid;
    else
        v_tenant := coalesce((to_jsonb(new) ->> 'tenant_id')::uuid, gba.current_tenant_id());
    end if;
    insert into gba.audit_events (tenant_id, actor, action, target_type, target_id, details,
                                  request_id)
    values (
        v_tenant,
        coalesce(nullif(pg_catalog.current_setting('gba.actor', true), ''), current_user::text),
        tg_argv[0] || case when tg_op = 'INSERT' then '.created' else '.updated' end,
        tg_argv[0],
        to_jsonb(new) ->> 'id',
        v_details,
        nullif(pg_catalog.current_setting('gba.request_id', true), '')
    );
    return null;
end;
$$;
revoke all on function gba.audit_row_change() from public;
create trigger users_audit after insert or update on gba.users
    for each row execute function gba.audit_row_change('user');
create trigger user_identities_audit after insert on gba.user_identities
    for each row execute function gba.audit_row_change('user_identity');
create trigger platform_roles_audit after insert or update on gba.platform_roles
    for each row execute function gba.audit_row_change('platform_role');
create trigger memberships_audit after insert or update on gba.memberships
    for each row execute function gba.audit_row_change('membership');
create trigger invitations_audit after insert or update on gba.invitations
    for each row execute function gba.audit_row_change('invitation');
create trigger tenants_audit after update on gba.tenants
    for each row execute function gba.audit_row_change('tenant');

-- Tenants: visible to active members and platform admins; status is changed by
-- platform admins only (the runtime role still cannot create tenants).
create policy tenants_member_read on gba.tenants
    for select using (exists (
        select 1 from gba.memberships m
        where m.tenant_id = tenants.id and m.user_id = gba.current_user_id()
          and m.status = 'active'));
create policy tenants_platform_admin_read on gba.tenants
    for select using (exists (
        select 1 from gba.platform_roles p
        where p.user_id = gba.current_user_id() and p.role = 'platform_admin'
          and p.revoked_at is null));
create policy tenants_runtime_update_platform_admin_only on gba.tenants
    as restrictive
    for update
    to gba_runtime
    using (exists (
        select 1 from gba.platform_roles p
        where p.user_id = gba.current_user_id() and p.role = 'platform_admin'
          and p.revoked_at is null))
    with check (exists (
        select 1 from gba.platform_roles p
        where p.user_id = gba.current_user_id() and p.role = 'platform_admin'
          and p.revoked_at is null));
grant update (status) on gba.tenants to gba_runtime;
