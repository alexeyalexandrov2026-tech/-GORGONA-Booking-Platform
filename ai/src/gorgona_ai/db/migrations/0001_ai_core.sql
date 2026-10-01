-- GORGONA AI learning plane (ADR-0013): evidence, knowledge documents, embeddings,
-- immutable dataset versions, model registry, evaluations, verifications, explicit
-- promotions, and the durable job store. Runs as gai_owner. Every tenant table has
-- FORCE row-level security keyed on the transaction-local setting gai.tenant_id.

create extension if not exists vector;

create schema ai;

create function ai.current_tenant() returns uuid
language sql stable
as $$ select nullif(current_setting('gai.tenant_id', true), '')::uuid $$;

-- Evidence: validated, normalised, deduplicated, scoped. No customer contact data.
create table ai.evidence (
    tenant_id      uuid not null,
    id             uuid primary key default uuidv7(),
    kind           text not null check (kind in ('interaction', 'correction', 'catalog')),
    env            text not null check (env in ('production', 'staging', 'test', 'verification')),
    source         text not null check (length(source) between 1 and 100),
    schema_version integer not null check (schema_version = 1),
    learning_scope text not null check (learning_scope in ('training', 'evaluation', 'none')),
    provenance     jsonb not null,
    payload        jsonb not null,
    content_hash   text not null check (content_hash ~ '^[0-9a-f]{64}$'),
    redactions     integer not null default 0 check (redactions >= 0),
    review_status  text not null
                   check (review_status in ('not_required', 'pending', 'approved', 'rejected')),
    reviewed_by    text,
    reviewed_at    timestamptz,
    received_at    timestamptz not null default now(),
    unique (tenant_id, content_hash),
    -- Non-production evidence never trains (ADR-0013 data scope).
    check (env = 'production' or learning_scope = 'none')
);

-- Knowledge documents for retrieval (RAG), derived from authoritative evidence.
create table ai.documents (
    tenant_id    uuid not null,
    id           uuid primary key default uuidv7(),
    kind         text not null check (kind in ('service')),
    ref          text not null,
    body         text not null,
    content_hash text not null,
    updated_at   timestamptz not null default now(),
    unique (tenant_id, kind, ref)
);

create table ai.embeddings (
    tenant_id    uuid not null,
    document_id  uuid not null references ai.documents (id) on delete cascade,
    embedder     text not null,
    content_hash text not null,
    embedding    vector(256) not null,
    updated_at   timestamptz not null default now(),
    primary key (document_id, embedder)
);

-- Immutable dataset versions (training sets and frozen evaluation benchmarks).
create table ai.datasets (
    tenant_id    uuid not null,
    id           uuid primary key default uuidv7(),
    purpose      text not null check (purpose in ('training', 'evaluation')),
    version      integer not null check (version >= 1),
    evidence_ids uuid[] not null,
    item_count   integer not null check (item_count >= 1),
    content_hash text not null,
    created_at   timestamptz not null default now(),
    unique (tenant_id, purpose, version),
    unique (tenant_id, purpose, content_hash)
);

-- Model registry. A candidate never replaces the promoted model automatically.
create table ai.models (
    tenant_id  uuid not null,
    id         uuid primary key default uuidv7(),
    kind       text not null check (kind in ('intent_knn')),
    dataset_id uuid not null references ai.datasets (id),
    embedder   text not null,
    params     jsonb not null,
    status     text not null
               check (status in ('candidate', 'evaluated', 'verified', 'rejected', 'promoted', 'retired')),
    created_at timestamptz not null default now(),
    unique (dataset_id, embedder, kind)
);
create unique index models_one_promoted_per_tenant on ai.models (tenant_id) where status = 'promoted';

create table ai.model_examples (
    tenant_id uuid not null,
    model_id  uuid not null references ai.models (id) on delete cascade,
    ordinal   integer not null,
    label     text not null,
    embedding vector(256) not null,
    primary key (model_id, ordinal)
);

create table ai.evaluations (
    tenant_id         uuid not null,
    id                uuid primary key default uuidv7(),
    model_id          uuid not null references ai.models (id),
    benchmark_id      uuid not null references ai.datasets (id),
    baseline_model_id uuid references ai.models (id),
    metrics           jsonb not null,
    baseline_metrics  jsonb,
    created_at        timestamptz not null default now(),
    unique (model_id, benchmark_id)
);

create table ai.verifications (
    tenant_id      uuid not null,
    model_id       uuid primary key references ai.models (id),
    evaluation_id  uuid not null references ai.evaluations (id),
    passed         boolean not null,
    checks         jsonb not null,
    policy_version integer not null,
    created_at     timestamptz not null default now()
);

-- Explicit, recorded promotion decisions with their rollback target.
create table ai.promotions (
    tenant_id         uuid not null,
    id                uuid primary key default uuidv7(),
    model_id          uuid not null references ai.models (id),
    previous_model_id uuid references ai.models (id),
    decided_by        text not null check (length(decided_by) between 1 and 200),
    reason            text not null check (length(reason) between 1 and 2000),
    decided_at        timestamptz not null default now()
);

-- Platform tables (no tenant content): durable jobs and per-tenant activity markers.
create table ai.job_runs (
    id              uuid primary key default uuidv7(),
    kind            text not null,
    tenant_id       uuid,
    idempotency_key text not null unique,
    status          text not null default 'queued'
                    check (status in ('queued', 'running', 'succeeded', 'dead')),
    attempts        integer not null default 0,
    max_attempts    integer not null default 5 check (max_attempts between 1 and 20),
    not_before      timestamptz not null default now(),
    lease_owner     text,
    lease_until     timestamptz,
    payload         jsonb not null default '{}'::jsonb,
    result          jsonb,
    last_error      text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    finished_at     timestamptz
);
create index job_runs_claim on ai.job_runs (kind, status, not_before);

create table ai.tenant_activity (
    tenant_id        uuid primary key,
    last_evidence_at timestamptz not null default now(),
    last_cut_at      timestamptz
);

do $$
declare t text;
begin
    foreach t in array array['evidence', 'documents', 'embeddings', 'datasets', 'models',
                             'model_examples', 'evaluations', 'verifications', 'promotions'] loop
        execute format('alter table ai.%I enable row level security', t);
        execute format('alter table ai.%I force row level security', t);
        execute format(
            'create policy tenant_isolation on ai.%I using (tenant_id = ai.current_tenant()) '
            'with check (tenant_id = ai.current_tenant())', t);
    end loop;
end $$;

grant usage on schema ai to gai_worker;
grant execute on function ai.current_tenant() to gai_worker;
grant select, insert, update, delete on ai.evidence, ai.documents, ai.embeddings to gai_worker;
grant select, insert on ai.datasets, ai.model_examples, ai.evaluations, ai.verifications,
    ai.promotions to gai_worker;
grant select, insert, update on ai.models, ai.job_runs, ai.tenant_activity to gai_worker;
