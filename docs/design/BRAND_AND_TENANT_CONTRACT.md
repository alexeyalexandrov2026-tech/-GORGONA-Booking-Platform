# GORGONA Booking × KA Nails — brand and tenant contract

**Status:** design specification, 30 September 2026. This defines the visual and product boundary; it does not claim an implemented website or booking service.

## Product surfaces

| Surface | Audience | Visual owner | Data scope | Primary action |
|---|---|---|---|---|
| GORGONA Booking marketing | salon prospects | GORGONA | public platform content | explore platform / request access |
| Salon workspace | authenticated owner and staff | GORGONA application shell; explicit tenant identity | selected authorized salon | manage appointments |
| Platform administration | authorized platform operators | GORGONA | authorized cross-tenant operations | manage tenant accounts |
| KA Nails public website | salon customers | KA Nails | KA Nails public content | book a service |
| KA Nails booking journey | salon customers | KA Nails, rendered by shared booking engine | KA Nails availability and booking rules | confirm a booking |

The customer should perceive a continuous **KA Nails** journey from landing page through confirmation. The salon staff should perceive a reliable **GORGONA Booking** workspace with a clearly named active salon. “Powered by GORGONA Booking” may appear as a small footer disclosure where contractually appropriate; it must never replace the salon identity or appear as a second main logo in the public journey.

## Theming boundary

1. Resolve the tenant from an approved domain/route mapping on the server. Never accept a client-supplied tenant ID as authorization.
2. Fetch the tenant's published brand configuration and public content together with its canonical tenant ID. Use that same ID for availability, holds, booking rules, and confirmation.
3. The public website and booking screens use tenant tokens and tenant assets only. No platform lime, dark GORGONA hero, or platform logo leaks into KA Nails pages.
4. The workspace uses GORGONA navigation, controls, and status semantics. Show the tenant name/logo only in the scoped tenant switcher or workspace header. Do not repaint the entire app with salon colors.
5. Platform administration always uses GORGONA chrome. Tenant preview is a bounded embedded preview with the tenant name and domain shown outside it.
6. On unresolved domain, missing published brand, or stale theme payload, show an explicit unavailable state. Never fall back to another tenant's assets, content, services, or booking rules.
7. Uploaded logos and photos require validated file type, size, ownership, and tenant scope; publish only approved asset URLs. No arbitrary CSS or HTML in tenant configuration.

## Typed configuration proposal

This is a frontend/backend contract to implement against the actual repository schema, not a claim that these fields already exist.

```ts
type PublishedTenantBrandV1 = {
  schemaVersion: 1;
  tenantId: string;              // server-issued opaque identifier
  publicName: string;
  canonicalDomain: string;       // verified before publication
  logo: {
    primaryUrl: string;          // tenant-owned approved asset
    altText: string;             // e.g. "KA Nails Nail Studio"
    background: "cream" | "transparent";
  };
  theme: {
    canvas: string;              // validated hex, contrast checked
    surface: string;
    ink: string;
    mutedInk: string;
    accent: string;
    action: string;
    actionInk: string;
    border: string;
  };
  typography: {
    displayFamily: "onest" | "approved-serif" | "system";
    uiFamily: "onest" | "system";
  };
  publishedAt: string;          // ISO-8601
};
```

Use a allowlist for font choices and bounded color inputs. Token validation must reject inaccessible action/text combinations. A logo URL is media, never a source of trusted layout code. A missing optional font falls back to a system font without moving booking controls off screen.

## Shared design primitives, separate skins

| Shared behavior | Platform presentation | KA Nails presentation |
|---|---|---|
| Booking stepper | compact, neutral, operational | editorial, spacious, salon branded |
| Service choice | efficient list with durations and prices | photographic or typographic service cards |
| Calendar and time slots | dense enough for staff management | touch friendly customer selection |
| Validation and alerts | semantic status colors with text | semantic status colors with text; brand color remains decorative |
| Confirmation | operational detail and management actions | clear appointment summary and salon instructions |
| Loading / empty / error | explicit app state with recovery | explicit customer state with recovery |

Shared components should be headless or use semantic component tokens. Theme variables are scoped to `data-brand="gorgona"` and `data-brand="tenant"` roots. Never attach tenant variables globally to `:root` in an authenticated workspace. Support keyboard navigation, visible focus, screen-reader labels, 44×44 px minimum customer touch targets, reduced motion, and text plus icon for statuses. Treat 320–389 px, 390–767 px, 768–1199 px, and ≥1200 px as validation widths, not hardcoded device categories.

## Brand asset rule

The supplied KA Nails image is a **1254×1254 opaque PNG** with a warm cream ground, dark `KA` monogram and wordmark, and rose-gold accent. It is preserved without redrawing in [ka-nails-logo-original.png](ka-nails-logo-original.png). Use it as the primary logo on the same cream surface. Do not invert it, put it over a photograph, or claim it has transparency. A compact transparent/vector derivative requires an approved original asset or a separate logo-design decision.

## Handoff and acceptance

- Implement separate theme packages/configurations; no shared brand-specific color names in the common booking component API.
- Verify domain → tenant → brand → availability → booking all resolve to the same tenant, including a second fixture tenant.
- Test that a missing brand/domain cannot display another tenant's data or logo.
- Review desktop and mobile public pages, dashboard, keyboard/focus, zoom, and contrast with actual rendered fonts and content.
- Keep real business address, hours, staff, services, prices, policies, and photography unpublished until KA Nails supplies and approves them. The design specification intentionally invents none of those facts.

**Companion files:** [GORGONA_BOOKING_DESIGN_SYSTEM.md](GORGONA_BOOKING_DESIGN_SYSTEM.md), [KA_NAILS_DESIGN_SYSTEM.md](KA_NAILS_DESIGN_SYSTEM.md), [gorgona-booking.tokens.css](gorgona-booking.tokens.css), [ka-nails.tokens.css](ka-nails.tokens.css).
