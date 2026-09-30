# Platform and public tenant site ownership

The platform origin is `alexeyalexandrov2026-tech/-GORGONA-Booking-Platform`.
The independent first-tenant site origin is `alexeyalexandrov2026-tech/KA-nails`.
They have independent Git directories and histories. No KA site remote belongs in
the platform checkout. Neither app has been published or deployed.

## Runtime integration

The platform owns FastAPI, PostgreSQL, migrations, domain resolution, availability,
pricing, holds, confirmation, identity and the reusable `web/` booking app. The
public site owns its logo, editorial pages, copy, metadata and booking URL setting.
It embeds the hosted reusable `/book/` app and provides a full-page fallback link.
There is no copied API client, schema, wizard or booking engine in KA-nails.

`NEXT_PUBLIC_GORGONA_BOOKING_URL` is a build-time public site setting. An operator
must approve this URL and map its Host to the correct tenant in GORGONA. It accepts
HTTPS `/book/` URLs without query, fragment or credentials. Loopback HTTP is only
for local acceptance tests. Visitor-supplied tenant IDs/URLs cannot override it.
The embedded app makes same-origin `/v1/customer/*` requests on its own approved
booking Host. No cross-origin API bypass or browser-selected tenant is introduced.

`GBA_CUSTOMER_WEB_DIR` optionally serves the reusable export after the API routes.
It never serves a salon marketing site. With this setting absent the API remains
usable without any frontend directory. PostgreSQL remains authoritative; the UI
only displays server catalog values and validated quotes. Add-on composition is
validated by the platform (`catalog/quote.py`). The reusable wizard keeps an
advisory hint that disables incompatible add-ons using the same bootstrap
`provides`/`requires`/`conflicts_with` data; the server quote remains authoritative.
No composition logic exists in KA-nails.

## Framing policy (open security finding)

Neither the API nor the static export emits `X-Frame-Options` or a CSP
`frame-ancestors` directive, so `/book/` can currently be framed by any origin
(see M3_REPORT, "Clickjacking: /book/ framable by any origin"). The KA iframe
works locally only because of that absence. Production iframe embedding must not
ship or be advertised until the owner chooses a framing policy and it is
implemented. The policy has to be an HTTP response header from the host (FastAPI
when it serves the export), CDN or reverse proxy: `frame-ancestors` is ignored in
a `<meta>` CSP, and `X-Frame-Options` cannot allowlist specific origins. The
browser scenario "SECURITY GAP (invert when framing policy lands)" in
`web/tests/booking.spec.ts` documents the current behaviour and must be inverted
when the fix lands.

## Assets and branding

The original KA Nails PNG is owned solely by KA-nails at
`public/assets/ka-nails-logo.png`; SHA-256 is
`bb2fe1c05eb7183b8b8f55eee861b06d80256cf5349b2958e1432fa3b83fbf53`.
The reusable platform export does not include a KA Nails default logo.
The onboarding candidate retains the approved asset reference and hash as data.
Its unknown business facts and `not_live` state are preserved.

Published `logo_url` values currently accept safe relative `/assets/` references.
Before publishing a tenant logo in the hosted wizard, approve an asset route on
that tenant's booking origin to its independently owned static assets. Do not
read another repository's working directory from the production API, expand logo
URLs to arbitrary origins, or serve another tenant's assets as a fallback.
This asset route has not been deployed. The KA site itself displays its original
logo, independently of the optional booking-app logo.

The two design systems remain separate: GORGONA application surfaces use platform
tokens; public KA pages use the cream/espresso salon skin. Tenant design documents
in this repo are integration references, not public site content. Historical
handoffs record the original logo locations; those bytes remain in Git history.

## Preservation and verification

Every tracked and untracked source was archived before separation, with a hash
inventory and verified Git bundle outside both repos. See
`../plan/SEPARATION_MANIFEST.csv` for every original frontend file's destination.
Ignored dependencies, generated exports and browser evidence are not source files
and are not committed. No reset, clean, stash, rewrite, force push or nested Git
repository was used.

The platform suite uses real disposable PostgreSQL 18 and Chromium. The KA repo
has an acceptance adapter that imports the canonical platform's test fixtures
from an explicitly supplied `GORGONA_API_DIR`; no backend implementation is copied.
It starts the canonical API, builds a separate site, exercises the embedded real
wizard, and checks confirmed guest rows in PostgreSQL for desktop and mobile.
It uses explicitly FAKE tenants, never invented KA business facts.

Production/domain setup, confirmed salon facts and publication approval remain
separate gates. The platform GitHub repo is public and the project license is
proprietary. Do not push until the owner approves that publication.
