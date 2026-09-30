# Project instructions for coding agents

Read `CLOUD_CODE_HANDOFF.md`, `source/PRODUCT_BRIEF.txt` and `docs/architecture/` before implementation. The brief is a supplied product reference; reconcile it with the owner's latest decisions recorded in the handoff. Preserve the supplied KA Nails logo and do not import Fresh Nails brand content or customer data.

Work in small, reviewable phases. Inspect Git state and current source before editing; keep tenant isolation, transactional booking integrity and human control over AI. Use typed, versioned contracts at API/agent boundaries. No fake booking, payments, availability, auth or health checks. Do not publish a booking flow until it performs real deterministic validation and payment verification where required.

Use the new project's OCI-first architecture proposal, but verify a live OCI target and owner approval before any infrastructure or production action. The old GORGONA node and IP are not deployable targets. Do not change existing Fresh Nails, GORGONA, Supabase or Cloudflare production systems as a shortcut. Do not store or output secrets. For each phase report exact changes, tests run, observed outcomes and remaining blockers; use PASS, FAIL, BLOCKED or NOT TESTED honestly.
