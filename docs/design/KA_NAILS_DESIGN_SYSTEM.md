# KA Nails — public salon website design system

**Version:** design proposal v1, 30 September 2026  
**Scope:** public tenant website and customer booking journey  
**Sources:** supplied `DESIGN (3).md` (Arsenijs Fabrica style reference) and the original [KA Nails logo](ka-nails-logo-original.png). The reference is a beauty editorial direction, not a salon content template.

## Design idea

**Quiet luxury, precise booking.** Preserve Design 3's open white space, fine rules, thin editorial headline, asymmetric composition, large photography, and pill-shaped actions. Replace its vivid orange, retail/cart patterns, product claims, and promotional modal with the logo's actual visual language: cream ground, dark espresso lettering, and muted rose-gold accent. This must read as **KA Nails Nail Studio**, never as a GORGONA campaign page.

## Design 3 → KA Nails translation

| Reference trait | Adopt | Change for KA Nails |
|---|---|---|
| pure white editorial field | generous space and clean grid | use warm cream where the supplied opaque logo sits; white for content panels |
| thin Onest headlines | restrained display scale | use Onest 300 if available; the **logo lettering itself** is never replaced with a font |
| orange CTA and modal | clear action hierarchy | use espresso CTA and rose-gold detail; no blanket discount modal |
| product cards and cart | fine outlines and rounded images | service cards with duration, price, and booking action; no fake shop |
| lifestyle beauty imagery | photography-led story | authentic nail work, hands, salon, artists with permission; no stock images misrepresented as KA Nails |
| trust-signal row | calm supporting facts | show only verified policies, hygiene/qualification information, and real reviews |

## Logo and asset rules

The attached logo is an original **1254×1254, nontransparent PNG**. It has a central `KA` monogram, `KA NAILS` wordmark, and `NAIL STUDIO` descriptor in warm metallic-looking lettering on a cream background. The dominant sampled ground is near `#fdf8f2`, with dark lettering near `#261b15`. Those are measurements from this image, not a vector brand master.

- Display the original image unmodified on the cream brand ground, with generous clear space. Set useful `alt="KA Nails Nail Studio"` on the link to home; decorative repeats use empty alt.
- Header can use a compact **text** label only if the full original logo is also visible elsewhere on the page. Do not crop the monogram from the supplied composite or pretend the background is transparent.
- Never invert, recolor, stretch, place over photography, or put it in a white circle that exposes its square cream background.
- Request a transparent SVG/PNG and explicit wordmark/monogram variants before using the logo on dark/photo backgrounds, favicons, or small mobile nav. The current PNG should stay sharp by serving at appropriate responsive resolution, not by upscaling beyond 1254 px.

## Foundations

| Role | Token | Value | Use |
|---|---|---|---|
| brand cream | `--ka-canvas` | `#fdf8f2` | logo zone, hero and warm sections |
| paper | `--ka-surface` | `#ffffff` | content and form surfaces |
| espresso | `--ka-ink` | `#261b15` | headings, main CTA, important body text |
| muted ink | `--ka-muted` | `#655951` | captions and secondary text |
| rose gold | `--ka-rose` | `#b8937f` | fine detail, rules, small decorative markers |
| action | `--ka-action` | `#261b15` | booking button fill |
| action text | `--ka-action-ink` | `#ffffff` | booking button text |
| subtle line | `--ka-line` | `#d9cbc1` | decorative dividers |
| strong border | `--ka-control-border` | `#756459` | input and actionable outline |
| focus | `--ka-focus` | `#76503d` | visible keyboard ring |

Rose gold is an accent, not body text on cream/white. Booking error, success, and pending states use separate semantic tokens plus labels. The palette is a first pass derived from the supplied raster logo; final color values should be reconciled against a brand master if supplied.

### Type

- **Logo:** always the supplied asset, never typeset as an imitation.
- **Editorial headlines:** Onest 300 (subject to availability/licensing), fallback a clean system sans, `clamp(42px, 5.7vw, 92px)` desktop hero, 1.0–1.08 line height, tracking between `-0.03em` and `-0.055em`. Keep mobile headlines around 40–52 px with no clipping.
- **Body/UI:** Onest 400–500, 16 px/1.55 for paragraphs and booking fields; 14–16 px for nav and metadata. A future approved serif may be used in isolated editorial titles, but not in functional booking controls.
- **Do not import** the source document's stray Times/Arial extraction as intentional brand fonts.

### Space and shape

- 4 px base scale: `4, 8, 12, 16, 20, 24, 32, 40, 56, 80`.
- 1280 px content max width; 80–112 px major section spacing desktop, 48–64 px on mobile; long text max width about 65 characters.
- 10–12 px service cards, 15 px photography, full-pill primary actions, 24–30 px customer input corners. These are service/booking elements, not e-commerce cards.
- 1 px fine rules; no decorative shadows or gradients. Only overlay scrim when real photography requires text contrast.

## Public pages

| Page | Core content | Main action |
|---|---|---|
| Home | logo, real salon photography, value proposition, selected services, location preview | Book an appointment |
| Services | categories, actual names, duration, price/range, what is included | choose service |
| Service detail | description, duration, price, preparation/aftercare if approved | continue to booking |
| Gallery | consented, credited work organized by technique/style | view service / book |
| Team | real artists, specialties, optional booking eligibility | choose artist |
| About | verified salon story and values | explore services |
| Contact & location | approved address, map link, hours, contact method | directions / book |
| Policies | real cancellation, late arrival, deposit, accessibility and privacy terms | understand before confirmation |

Do not invent names, prices, address, hours, reviews, images, or policy terms to fill the design. If these are not yet supplied, render a clear unpublished/content-needed state in private preview and avoid public publication of the incomplete page.

## Booking journey

**Service → optional artist → date/time → contact details → review/policies → confirmation.** The stepper shows progress, permits back navigation without data loss, and displays the salon name on every step. Availability comes from the shared booking platform but is scoped to KA Nails. Holds, expired slots, rescheduling, cancellation, and payment/deposit requirements must reflect the real backend contract; no fake success response.

| State | Customer-facing treatment |
|---|---|
| Loading | lightweight skeleton/announced progress, preserve context |
| No availability | distinguish no slots from failed fetch; suggest another date or contact path |
| Slot lost | explain conflict and return to current availability |
| Validation error | field-level text, focus summary, keep entered values |
| Service unavailable | show reason if safe, link back to Services |
| Confirmation | appointment ID, service, artist if chosen, local date/time with timezone, location, next steps |

The main booking action is dark espresso on cream/white; rose gold can mark the selected step or underline a text link. Avoid multiple competing CTAs in the hero. The salon owns all customer copy. A small platform disclosure, if required, stays in the footer.

## Components and interaction

| Component | Treatment | Required states |
|---|---|---|
| Header | quiet white/cream, logo or approved compact lockup, Services / Gallery / Team / About / Contact, Book | mobile menu, active link, keyboard focus |
| Primary CTA | espresso fill, white label, full pill, ≥44 px target | hover, focus, disabled, busy |
| Secondary CTA | transparent, espresso border/label, full pill | hover, focus, disabled |
| Service card | thin control border, name, duration, price, concise description; rose detail may be decorative | focus and selected state; no image required |
| Artist card | approved portrait, name, specialty | selected and unavailable states |
| Gallery tile | approved photograph, consistent crop, meaningful alt | full-screen viewer with keyboard close if used |
| Booking slot | readable local time and state, large touch target | available, selected, unavailable, loading |
| Form | persistent label, help text, inline error | auto-complete, field validation, recovery |
| Footer | salon identity, location/contact/policies, optional small platform disclosure | no mixed branding |

## Mobile, accessibility, SEO, performance

- Use mobile-first single-column booking. The CTA remains visible in context without hiding content; do not force an overlapping sticky bar.
- All click/tap targets at least 44×44 px, visible focus, keyboard-operable date and time choice, no color-only state, reduced-motion support.
- Verify at 320, 390, 768, and 1280 px; test 200% zoom and real localized service names.
- Provide unique page titles, descriptions, canonical URLs, structured business data only when details are verified, descriptive image alt, and noindex on unpublished previews.
- Serve optimized responsive gallery/hero images with correct dimensions and lazy loading below the fold; preserve the supplied logo without destructive recompression.

## Tenant relationship

This is **the first tenant skin of a shared website/booking engine**, not a forked one-off system. The site receives published brand, content, staff, services, policies, and booking rules for its tenant. GORGONA is the engine and authenticated workspace; KA Nails is the customer-facing brand. See [BRAND_AND_TENANT_CONTRACT.md](BRAND_AND_TENANT_CONTRACT.md) and [ka-nails.tokens.css](ka-nails.tokens.css).

**Validation before release:** approved real content and photography, rendered visual QA against this spec and the logo, accessibility/contrast, mobile booking, end-to-end slot confirmation, tenant isolation, and legal/policy review as applicable. This specification does not claim those checks have occurred.
