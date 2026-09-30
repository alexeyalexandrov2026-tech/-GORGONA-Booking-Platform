# ADR-0008: Identity and authority model

- Status: Accepted (2026-09-30, M2)

## Decision

- **Identity is platform-level.**
  - `gba.users` holds one row per person.
  - `gba.user_identities` maps an external `(issuer, subject)` to a user, with a `unique (issuer, subject)` constraint, so one external account can never be linked to two users.
  - No user is created on first sight of a token. A user is created only when an invitation is accepted, or owner-side by an operator.
- **Authority is server-side and only server-side.**
  - `gba.memberships` (tenant-owned, forced RLS) grants a role in one salon. Its status (`active`, `suspended`, `revoked`) follows enforced transitions, and `revoked` is terminal. At most one non-revoked membership exists per salon and user (partial unique index).
  - `gba.platform_roles` grants `platform_admin`. Grants are made owner-side only; the runtime role can read them but not write them.
  - JWT claims never carry tenant rights. A client-supplied tenant, salon, host or body field never grants access by itself.
- **Roles map to scopes.** `owner` and `manager` are salon admins; `front_desk` and `artist` are salon staff. Permissions are a static, versioned map in `gorgona_booking/auth/permissions.py`.
  - Only an owner may grant or change `owner`/`manager` memberships.
  - The last active owner cannot be suspended or revoked.
- **Invitations.** `gba.invitations` stores only a SHA-256 of a random token, which is shown once. Acceptance requires a verified email that matches the invitation, and is idempotent for the same user. It is serialized by a row lock.
- **Audit.** `gba.audit_events` is append-only for the runtime role (INSERT and SELECT only). Database triggers record every insert or update of memberships, invitations, identities, platform roles and tenant status, so no code path can skip the audit. The application adds events for decisions that change no row, such as platform-admin support access. Token hashes and emails are excluded from event details.
- **Cross-tenant lookups without `SECURITY DEFINER`.** The application sets transaction-local `gba.user_id`, `gba.auth_issuer` and `gba.auth_subject` after verifying a token. Additional RLS policies expose only the caller's own identity row, own memberships and own platform roles.

## Consequences

- The M1 placeholder `gba.memberships.subject` column is replaced by `user_id` (owner decision, 2026-09-30).
- Disabling a user (`users.status`) or revoking a membership takes effect on the next request (ADR-0009).
