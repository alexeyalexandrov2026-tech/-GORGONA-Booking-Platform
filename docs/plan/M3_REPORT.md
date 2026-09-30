# M3 report: customer booking journey and repository separation

Date: 2026-09-30 (America/New_York). This report verifies the **current, separated** state of the GORGONA platform and the independent KA-nails site. It supersedes the pre-separation draft. Historical results are labelled as such and are not counted as current evidence.

## Gate

**M3 CUSTOMER BOOKING GATE: PASS** (local engineering milestone).

Every mandatory criterion below passed against the current separated trees. One open **security finding** (clickjacking) is tracked with an owner and a hard gate on production embedding. By owner decision it does not gate this local milestone, because M3 deploys nothing.

This PASS does **not** mean:
- KA Nails is production-ready or live;
- payments are enabled;
- business facts or a domain are approved;
- deployment or GitHub publication is authorized.

## Scope

M3 delivers the public customer booking journey on a live salon Host:

catalog → service/variant/add-ons → artist or Any Available → server-issued times → validated guest details → review → hold → confirmation

It also delivers a clean separation between the reusable platform and the first tenant's public site. Out of scope:
- payments and deposit collection;
- notifications;
- AI;
- analytics;
- admin UI for schedules;
- deployment.

## Separation

| Owner | Owns |
| --- | --- |
| GORGONA (`-GORGONA-Booking-Platform`) | FastAPI API, PostgreSQL schema/migrations, tenant/identity/security boundaries, catalog, pricing, schedules, availability, holds, confirmation, idempotency, concurrency, Host resolution, shared contracts, reusable `web/` booking export |
| KA-nails (`KA-nails`) | Public salon pages, copy, SEO metadata (noindex for now), KA design system/tokens, approved original logo, build-time booking URL, thin iframe/full-page integration |

Evidence the boundary holds:
- **KA source.** It contains no API client, `/v1/` calls, `fetch(`, SQL, migrations, FastAPI/psycopg code, wizard components or contracts. Checked by scanning `app/`, `components/`, `lib/` and the file list. `tools/test_gorgona_integration.py` is a test-only adapter that imports the canonical platform's test fixtures from an explicit `GORGONA_API_DIR`; it copies no implementation.
- **Git layout.** There are independent Git directories and no nested `.git` in either repo. KA's first commit has no platform ancestry: KA root is `f131b0b`, the platform root is `6b9a0f3`.
- **Platform assets.** The platform `web/` source and fresh `web/out` export contain no tenant logo. `api/tests/unit/test_customer_candidate.py` asserts this.
- **Classification.** Every original frontend file is classified in [`SEPARATION_MANIFEST.csv`](SEPARATION_MANIFEST.csv). Ownership rules are in [`REPOSITORY_SEPARATION.md`](../architecture/REPOSITORY_SEPARATION.md).
- **Single booking authority:** browser / KA site → GORGONA `/book/` export → GORGONA FastAPI → PostgreSQL.

Separation changes reviewed in this closeout:
1. **Logo relocation.** Two tracked copies were removed from the platform and exist only in KA-nails, byte-identical. The old bytes remain in platform Git history.
2. **Add-on compatibility hint.** The separation agent had removed the wizard's advisory hint. It is not tenant logic: it mirrors `catalog/quote.py` exactly (currency, duplicate component, requires, conflicts), and it was part of the accepted pre-separation state. On owner decision it was **restored verbatim** from the pre-separation backup. The server quote stays authoritative.
3. **Documentation.** Stale statements in `README.md` and `REPOSITORY_SEPARATION.md` were corrected.

## Platform implementation

- **API.**
  - `GET /v1/customer/bootstrap`
  - `POST /v1/customer/availability`
  - `POST /v1/customer/holds`
  - `POST /v1/customer/bookings/{booking_id}/confirm`

  All use the existing trusted Host resolution and live gate, and return `Cache-Control: no-store`.
- **PostgreSQL.** It is authoritative for prices, duration, occupancy (GiST exclusion), holds, expiry and confirmation. Migration `0006_customer_booking.sql` adds four tables with tenant-aware foreign keys and ENABLE + FORCE RLS: `resource_services`, `resource_hours`, `resource_blocks` and `booking_customers`.
  - 0006 SHA-256 is `cd315f8f4de1e819843d72aa316ec4e309a7f2d9f49e4896c09c9acd6eeb9576`, unchanged.
  - Migrations 0001–0005 are unchanged since M2 (`dd272e7`).
  - No SECURITY DEFINER was added.
- **Customer contracts.** Pydantic on the server, strict TypeScript plus Zod in the browser.
  - The capability is a browser-generated 256-bit `Booking-Token`; only its SHA-256 is stored.
  - Idempotency is scoped to the capability.
  - Contacts stay absent until confirmation.
- **Catalog and availability.**
  - Explicit location and artist hours, eligibility and dated blocks.
  - DST-aware; IANA timezone gaps excluded.
  - Nothing is invented: missing schedules or policies fail closed.
- **Holds and confirmation.** They reuse the M1 lifecycle, advisory lock and exclusion constraint. Confirmation rechecks live state, capability, policies and expiry.
- **Deposits.** Required deposits return `PAYMENT_REQUIRED` (fail closed). No payment is simulated.
- **Optional static web.**
  - `GBA_CUSTOMER_WEB_DIR` mounts the export after all API routes.
  - With it unset, the API runs API-only and needs no frontend assets (`test_customer_web_mount.py`).

## KA integration

- **Public shell.** Next.js static export (Node 24, Next 16.3.7, React 19.3.0).
  - Pages: Home, Services, Contact, Book.
  - All pages are `noindex, nofollow`.
  - Contact shows "coming soon". No invented address, hours, staff, prices, policies, photos or claims.
- **Booking URL.** Build-time `NEXT_PUBLIC_GORGONA_BOOKING_URL`.
  - `lib/booking-url.ts` accepts only HTTPS `/book/` without credentials, query or fragment; loopback HTTP is allowed for tests.
  - Visitor-supplied URLs and tenant IDs cannot change it.
  - Missing or invalid values produce an unavailable state with no iframe.
- **Iframe.**
  - Titled, `sandbox="allow-scripts allow-forms allow-same-origin"`, `referrerPolicy="no-referrer"`.
  - The wizard makes same-origin API calls on its own booking Host, so no CORS is needed.
  - No cookies, no `postMessage`, and no client-side confirmation.
- **Full-page fallback.** A link with `target=_blank rel="noopener noreferrer"` to the same approved URL. It is tested to open the hosted wizard at top level.
- **Optional mount.** The KA site is never served by, or required by, the platform API.
- **No duplicate booking authority.** See Separation.

## Logo migration

| Item | Status |
| --- | --- |
| Original location now | `KA-nails/public/assets/ka-nails-logo.png` (committed in `f131b0b`) |
| Byte integrity | SHA-256 `bb2fe1c05eb7183b8b8f55eee861b06d80256cf5349b2958e1432fa3b83fbf53`, 911,776 bytes. It matches the transfer original, both deleted platform blobs at `f64368b`, the KA working file, the KA committed blob and the served KA export (checked in the KA Playwright suite over HTTP). No derivative was created. |
| Generic platform absence | PASS: not in `web/` source or fresh `web/out`; asserted by `test_customer_candidate.py` |
| Candidate logo reference | `/assets/ka-nails-logo.png` plus hash, stored as candidate data only |
| Hosted asset route | **Pending.** The booking Host has no approved route serving tenant assets. Until one is approved, a live tenant with this reference would show no logo in the hosted wizard. Do not solve this by copying tenant assets back into the generic export. |

## Tenant and security

- **Host resolution.** The tenant comes only from the trusted Host mapping. Body, query and header tenant injection is ignored (`test_tenant_injection_and_not_live`). Unknown Hosts return `TENANT_NOT_FOUND`. Not-live Hosts are unbookable, and this is rechecked between hold and confirm.
- **Cross-tenant isolation.** Covered by `test_tenant_isolation.py` (13 tests) and `test_cross_tenant_confirmation_and_live_recheck`. `test_schedule_cross_tenant_fk_and_rls` also passes.
- **RLS.** `test_every_tenant_owned_table_has_forced_rls`, `test_customer_tables_force_rls` and the migration-file RLS checks pass.
- **Concurrency.** The 8-client slot race `test_slot_race_has_one_winner` gives exactly 1×201 and 7×409 `SLOT_CONFLICT`, with zero overlapping pairs. The M1 100-way race and two-instance race tests also pass.
- **Idempotency.** `test_concurrent_confirmation_is_idempotent` and the M1 idempotency race tests pass.

### Security finding: Clickjacking — `/book/` framable by any origin

| Field | Value |
| --- | --- |
| Severity | **Medium.** A state-changing action ("Confirm booking") can be presented inside an attacker-controlled page on any origin. |
| Status | **Open. BLOCKED: owner decision required.** |
| Owner | GORGONA platform owner (repository owner) |
| Evidence | Neither the API nor the export sends `X-Frame-Options` or CSP `frame-ancestors`. The browser scenario `SECURITY GAP (invert when framing policy lands): /book/ is framable by an arbitrary origin` passes on desktop and mobile. It serves an embedder page from a separate, never-approved loopback origin and asserts the real wizard renders inside the frame. |
| Hard gate | **No milestone may ship or advertise iframe embedding in production until a framing policy is chosen and implemented.** When it lands, invert the scenario to assert that non-allowlisted origins are refused. |
| Where the fix must live | An HTTP response header from whatever serves `/book/`: FastAPI middleware when it serves the export, or the CDN or reverse proxy. A static export cannot enforce it: `frame-ancestors` is ignored in a `<meta>` CSP, and `X-Frame-Options` cannot allowlist specific origins (`ALLOW-FROM` is obsolete). |
| Options | (a) Per-tenant governed allowlist of approved embedding origins, emitted as `Content-Security-Policy: frame-ancestors 'self' <origins>`. Needs a new numbered migration and readiness coverage; this is a new trust model. (b) Per-Host edge/CDN header rules for each approved salon domain. (c) `frame-ancestors 'none'`, with KA using the full-page link only. (d) Same-origin: serve `/book/` under the salon domain through a reverse proxy, then use `frame-ancestors 'self'`. |

The KA iframe works in local acceptance only because of this gap. The KA site's own framing and CSP headers are also part of the pending hosting approval.

## Browser and E2E

| Suite | Top-level runner | Nested Chromium scenarios |
| --- | --- | --- |
| Platform bridge `api/tests/integration/test_customer_browser.py` | 1 pytest test (inside the 232) | **14** (7 × desktop 1280 / mobile 390) |
| KA unconfigured site suite | Playwright only | **6** |
| KA real integration `KA-nails/tools/test_gorgona_integration.py` | 1 pytest test (run separately, not part of the 232) | **10** |

**Nested scenarios are never added to pytest totals.**

Platform bridge:
- Uses the real FastAPI app, the fresh static export and a disposable PostgreSQL 18 database.
- Uses a live FAKE tenant plus a not_live FAKE tenant on separate Hosts.
- The Node process receives no DSN.
- A SQL check afterwards confirms 2 `FAKE Browser Customer` and 2 `FAKE Retry Guest` bookings.

Platform bridge scenarios:
1. Real catalog → add-on → Any Available → details → review → PostgreSQL confirmation (75-minute server quote; axe WCAG 2 A/AA on service, details and review; no mobile overflow).
2. A real competing hold wins the slot; the customer recovers to fresh times.
3. A not_live Host shows the unavailable state.
4. Explicit artist and keyboard; hold and confirm responses are lost *after* the real backend commits and are retried with identical keys.
5. **Network fault injection:** an availability request is aborted, then retried.
6. **Security-gap documentation:** framable by an unapproved origin.
7. **Contract/fault injection:** a documented `HOLD_EXPIRED` response is injected to exercise the UI recovery branch.

   This is **not** evidence of elapsed-time expiry. Real PostgreSQL expiry is verified separately by `test_expired_hold_releases_slot` and `test_expired_unconfirmed_hold_can_be_replaced`. Those tests backdate `expires_at` in PostgreSQL and then exercise the real expiry and confirm transaction. The M1 tests `test_sweeper_expires_due_holds` and `test_confirm_checks_expiry_under_lock` also cover it.

KA real integration:
- Starts the canonical API on one loopback origin.
- Builds the KA site with that `/book/` URL and serves it on a *different* loopback origin, so the iframe is cross-origin.
- Runs these scenarios on desktop and mobile:
  - site/logo/noindex/axe;
  - unsafe-URL rejection;
  - the embedded real booking to PostgreSQL confirmation;
  - the full-page fallback at top level;
  - **network fault injection:** an aborted bootstrap, then retry in the frame.
- SQL checks afterwards: exactly 2 confirmed `FAKE KA Website Guest` bookings for tenant A, and 0 customer rows for tenant B.
- The site was then rebuilt unconfigured, and no loopback origin remains in `out/`.

Accessibility scope: axe checks on the tested states, plus keyboard and focus behaviour. This is not a full screen-reader or all-browser audit.

## Validation (current state)

Tools:

| Tool | Version |
| --- | --- |
| Python | 3.14.6 |
| uv | 0.12.3 |
| FastAPI | 0.142.1 |
| psycopg | 3.3.6 |
| pytest | 9.1.1 |
| PostgreSQL | 18.6 |
| Node | 24.18.0 |
| npm | 11.16.0 |
| Playwright | 1.63.0 (Chromium) |

Private settings came from `C:/Users/alexa/.gba/secrets/local-pg18.env`, which is outside the repositories; its contents are not reproduced.

| Working directory | Command | Result |
| --- | --- | --- |
| platform `api/` | `uv sync --locked` | PASS, 38 packages |
| platform `api/` | `uv run ruff format --check src tests` | PASS, 86 files |
| platform `api/` | `uv run ruff check src tests` | PASS |
| platform `api/` | `uv run mypy src tests` and `uv run mypy` | PASS, 86 files |
| platform `web/` | `npm ci --ignore-scripts` | PASS, 0 vulnerabilities reported |
| platform `web/` | `npm run typecheck` / `lint` / `format:check` | PASS / PASS / PASS |
| platform `web/` | `npm run build` | PASS: `/`, `/book`, 404; no PNG in `out/` |
| platform `api/` | `GBA_REQUIRE_POSTGRES=1 GBA_REQUIRE_BROWSER=1 uv run --env-file <private> pytest -q -s -rs` | **PASS: 232 passed**, 0 skipped, 0 failed; nested 14 Chromium passed |
| platform `api/` | focused: `pytest tests/integration/test_customer_web_mount.py tests/unit/test_customer_candidate.py tests/integration/test_customer_api.py` | PASS, 15 |
| KA root | `npm ci --ignore-scripts` | PASS, 0 vulnerabilities reported |
| KA root | `npm run typecheck` / `lint` | PASS / PASS |
| KA root | `npm run format:check` | Initially **FAIL** (pre-existing: Next-generated `tsconfig.json` not Prettier-formatted). Fixed with `prettier --write tsconfig.json` (whitespace only; it stays stable across rebuilds). Then PASS. |
| KA root | `npm run build` (unconfigured) | PASS: `/`, `/book`, `/contact`, `/services`, 404 |
| KA root | `npm run test:e2e -- --grep "website\|unsafe\|unconfigured"` | PASS, 6 |
| platform `api/` | `GBA_REQUIRE_POSTGRES=1 GORGONA_API_DIR=<platform api> uv run --env-file <private> pytest <KA>/tools/test_gorgona_integration.py -q -s -c pyproject.toml --rootdir . -p no:cacheprovider` | **PASS: 1 passed**; nested 10 Chromium passed |

Test-count breakdown:
- **232 = 230 historical (127 M1 + 81 M2 + 22 M3) + 2 new optional-mount tests.**
- The 14 platform browser scenarios and 10 KA integration scenarios are nested and are not added to that total.

Honest record of intermediate failures in this closeout:
- The first two runs of the new framing scenario failed.
- The embedder page was served via Playwright `route.fulfill`, which Chromium treated as a non-local context. It therefore blocked the loopback subframe under Private Network Access rules, which do not apply to a public booking Host.
- Serving the embedder from a real loopback server on a separate port fixed the test harness. No product code changed.
- All other suites passed on their first run.

Secret review: PASS.
- Every modified, untracked and to-be-committed file in both repos was scanned for private keys, cloud and GitHub tokens, JWTs, DSNs with passwords, and secret assignments.
- Only 7 lines were flagged:
  - `.env.example` (lines 8, 9, 19, 22);
  - `.github/workflows/ci.yml:23`;
  - `api/tests/unit/test_config.py:11`;
  - `docs/plan/ASTRA_HANDOFF.md:166`.
- All are placeholder, CI-only or test values, and all are identical to already-committed lines.
- No real `.env` exists in either repository. KA `.gitignore` excludes `.env*` except `.env.example`.

## Known limitations

- KA Nails remains **`not_live`**. Local acceptance uses explicit FAKE tenants only.
- Business facts are incomplete: timezone, hours, staff/eligibility, booking durations, cancellation/deposit/booking rules, contact details, address, photos and production domain are unconfirmed. The three Hammam candidate variants have no authoritative booking duration; displayed ranges are not booking durations.
- There is no payment implementation, and required deposits fail closed.
- The candidate logo asset route on the booking Host is pending.
- Security finding: clickjacking. Production iframe embedding is blocked until a framing policy exists.
- No hosted CI run, no production deployment and no push.
- Admin UI for schedules, notifications, AI and analytics are outside M3.

## Repository status

**GORGONA Booking Platform**
- Path: `C:\Users\alexa\Documents\Codex\2026-09-30\also-create-new-project-in-a-2\outputs\gorgona-booking-ai`
- Branch: `m2-identity`
- Base HEAD at closeout start: `f64368b06093ee91808e09d2e173a6ff174dbe90`
- Origin (fetch/push): `https://github.com/alexeyalexandrov2026-tech/-GORGONA-Booking-Platform.git`
- Remote: `main` = `784312d0e76f6941e2bf62f8285bf5a9a55a1d3a` ("Initial commit", README only).
- Remote relationship: **UNRELATED HISTORIES**. The comparison used a scratch bare clone and found no merge-base with local `m2-identity` or `main`. No synchronization was attempted.
- Local commits created in this closeout:
  - `e74507b8b10d21cd0f1d6b65e3c530a1aa63d629` feat(customer-web): M3 static customer export, optional FastAPI mount and browser gate
  - `2d2d5d68ab697d407aedd91c777e2d1a47c62813` platform: separate KA Nails branding from generic booking export
  - the documentation commit that adds this report (see `git log`)
- Push: **not performed**.

**KA Nails**
- Path: `C:\Users\alexa\Documents\Codex\2026-09-30\referenced-chatgpt-conversation-this-is-an\work\ka-nails-public`
- Branch: `main`. HEAD was unborn at the start; it is now `f131b0b539d9697bed24ddd3c3a9198f85367c05` (first commit).
- Origin (fetch/push): `https://github.com/alexeyalexandrov2026-tech/KA-nails.git`
- Remote: empty (no refs). No shared history with the platform.
- Local commits created: `f131b0b` ka-nails: independent public site with hosted GORGONA booking integration
- Push: **not performed**.

## Mandatory criteria (current state)

| # | Criterion | Status |
| --- | --- | --- |
| 1 | Public flow for a live (FAKE) test tenant | PASS |
| 2 | Backend catalog | PASS |
| 3 | Service/variant/add-on selection (advisory hint restored, server authoritative) | PASS |
| 4 | Artist / Any Available | PASS |
| 5 | Server-authoritative dates/times | PASS |
| 6 | Server-authoritative price/duration summary | PASS |
| 7 | Customer detail validation | PASS |
| 8 | Existing hold/booking protections | PASS |
| 9 | Slot-race recovery (8-client race + browser race) | PASS |
| 10 | Cross-tenant isolation / Host-only tenant | PASS |
| 11 | not_live remains unbookable | PASS |
| 12 | Mobile UX (Chromium 390px) | PASS (tested scope) |
| 13 | Accessibility basics (axe, keyboard, focus) | PASS (tested scope) |
| 14 | Full M1 regression | PASS |
| 15 | Full M2 regression | PASS |
| 16 | M3 tests incl. real PostgreSQL expiry | PASS |
| 17 | Lint / format / strict typing (Python + both frontends) | PASS |
| 18 | No secrets in commits | PASS |
| 19 | Clean working trees after commits | PASS (verified by `git status` in both repos after the final commit) |
| 20 | Canonical M3 report | PASS (this document) |
| 21 | API-only mode and optional export mount | PASS |
| 22 | Generic export has no tenant brand asset | PASS |
| 23 | Original logo byte-identical in KA | PASS |
| 24 | KA build/typecheck/lint/format/tests | PASS |
| 25 | KA iframe integration against real API/PostgreSQL (local) | PASS |
| 26 | KA full-page fallback | PASS |
| 27 | No duplicate booking authority / independent histories | PASS |
| — | Production framing policy | Open security finding. BLOCKED: owner decision. Non-gating for local M3 by owner decision; it hard-gates production embedding. |
