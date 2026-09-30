# ADR-0013: Persistent AI learning plane

- Status: Accepted as design direction (2026-09-30, M4 checkpoint A). Nothing is provisioned or implemented. Extends `docs/architecture/AI_TOOL_MODEL.md`.

## Context

The owner intends GORGONA to run a continuous learning loop. Staging, by contrast, is ephemeral (ADR-0012). The learning plane's memory, evidence and history must never be lost when staging is deleted, and must not depend on any single application environment.

## Decision

- **Separation.** The AI learning plane is a separate lifecycle boundary:
  - its own resource group (`rg-gorgona-ai`) and deployment stack;
  - deny-delete settings and a delete lock;
  - no resource shared with, or managed by, the staging or production stacks.
- **Persistent 24/7 components (low-cost).**
  - An evidence store: StorageV2 blob storage with versioning, soft delete, immutability policies and geo-redundancy. It deliberately has no hierarchical namespace, because blob versioning requires that.
  - An AI PostgreSQL 18 server, separate from the booking database. It holds memory, knowledge, corrections, dataset/version lineage, capability registry metadata, evaluation history and embeddings (`vector` extension).
  - A Service Bus queue for validation and training requests.
  - A Key Vault.
  - An Azure Machine Learning workspace (registry, experiments, evaluation records).
- **Scale-to-zero or on-demand components.**
  - Container Apps jobs for ingestion validation and orchestration, triggered by events.
  - Azure ML CPU/GPU compute clusters with `minimum nodes = 0`, started only for training or evaluation.
  - "24/7 learning" means continuous ingestion and orchestration. It does not mean continuous GPU training.
- **Loop.** inference/events → evidence → memory/dataset → validation → training queue → training → evaluation → candidate → verification/policy gate → registry → explicit promotion → controlled deployment → new evidence.
- **Promotion is explicit.**
  - A candidate model never replaces the approved production model just because training finished. It needs evaluation results, verification against policy thresholds, a versioned registry entry, and a recorded promotion decision with a rollback target.
  - The approved production model keeps serving while candidates train.
- **Data scope.**
  - Every evidence record carries `env`, `tenant_id`, `source`, `schema_version`, `consent/learning_scope` and provenance.
  - Staging and test data (FAKE tenants) are excluded from training by default. Staging has no write access to the AI plane.
  - Tenant isolation applies: RLS in the AI database, and per-tenant learning scope.
  - Customer contact data is not copied into learning data unless a future, explicitly approved policy allows it.
- **Model boundary (unchanged).** Models only propose. Prices, availability, holds and confirmations stay with the deterministic booking domain and PostgreSQL (AI_TOOL_MODEL).

## Consequences

- There is a small but real 24/7 cost: storage, AI PostgreSQL, Service Bus, private endpoints and log ingestion.
- No provider or model is selected yet. Training compute and GPU quota are requested only when a real workload exists.
- Building the ingestion, memory and training code is a separate, future milestone. M4 delivers the design and infrastructure definitions only.
