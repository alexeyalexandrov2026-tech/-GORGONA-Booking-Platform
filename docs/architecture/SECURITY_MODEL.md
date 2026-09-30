# Security model

Status: design only.

Trust boundaries: browser to web/API, API to database, payment webhook to inbox, AI model to tool runtime, and staff admin to tenant data. Authenticate staff and customers with short-lived sessions; enforce roles and ownership server-side. Guest booking uses a narrow capability scoped to its hold/appointment, with verification before viewing or changing personal data. CSRF protection, secure cookies, safe CORS, CSP, HSTS, rate limits, file validation and anti-abuse controls are release gates.

Keep PostgreSQL private to the host/VCN. Split migration, runtime read/write and worker privileges. Use tenant keys and RLS; avoid public `SECURITY DEFINER` functions. Validate webhook signatures and replay keys; store provider event IDs. Encrypt backups and secrets, rotate credentials through a documented process, redact logs, define retention/deletion rules, and record audit events for admin and financial actions.

Threat-model cross-tenant IDOR, booking races, payment replay/refund abuse, forged webhooks, malicious uploads, prompt injection, tool abuse and secret leakage. Test each relevant boundary before production. No auth, RLS, rate limiting, WAF or backup control has been deployed yet.
