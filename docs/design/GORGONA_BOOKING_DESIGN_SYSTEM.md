# GORGONA Booking — platform design system

**Version:** design proposal v1, 30 September 2026  
**Scope:** SaaS marketing, salon workspace, platform administration  
**Source:** supplied `DESIGN (2).md` (Integrated Biosciences style reference). This is an adaptation, not a copy of its scientific content or identity.

## Design idea

**A calm control room for the business of appointments.** Keep Design 2's cool deep ink, warm-white counter-surface, restrained lime signal, large regular-weight display type, hairline rules, and mono metadata. Replace laboratory imagery, molecular metaphors, publication cards, and monumental application typography with appointment, availability, customer, and operations information. The marketing site can be expressive; a staff member using a calendar all day needs compact, legible controls.

The source has an invalid shortened lime value (`#cef79`) in two generated CSS examples. All tokens here use the complete sampled reference value `#cef79e`.

## Design 2 → GORGONA translation

| Reference trait | Marketing site | Salon workspace / admin |
|---|---|---|
| `#222f30` dark canvas | full-width hero and feature bands | sidebar/header only; main working canvas is light |
| `#cef79e` lime signal | small arrows, active nav | selection and focus accents; never sole status cue |
| huge single-weight Aspekta | clamp-based editorial headline | 24–40 px headings; readable 14–16 px data/UI |
| Roboto Mono technical labels | section numbers, proof labels | dates, slot times, counts, keyboard hints |
| flat surfaces / thin lines | editorial cards | tables, lists, calendar grid, forms |
| scientific renders | remove | real product UI, diagrams of booking flow, authorized salon imagery only in tenant preview |

## Foundations

| Role | Token | Value | Use |
|---|---|---|---|
| ink | `--gb-ink` | `#222f30` | headings, dark chrome, primary action on light |
| canvas | `--gb-canvas` | `#f7f7f5` | page and workspace |
| surface | `--gb-surface` | `#ffffff` | content panels and inputs |
| muted surface | `--gb-surface-muted` | `#e7e8e1` | secondary grouping |
| secondary text | `--gb-text-muted` | `#4d5757` | supporting text |
| decorative line | `--gb-line` | `#c9cbbe` | noninteractive separators |
| control border | `--gb-control-border` | `#66716e` | inputs and actionable outlines |
| signal | `--gb-signal` | `#cef79e` | selected markers, progress, 40 px arrow tile |
| focus | `--gb-focus` | `#527b2a` | visible focus ring on light surfaces |

Lime communicates selection or progression. A booked, cancelled, failed, or pending appointment must also have a word label and icon; semantic state color is separate from `--gb-signal`.

### Type

- **Display:** Aspekta 400 when properly licensed and supplied; fallback `Inter Tight`, then system sans. Headline uses `clamp(52px, 7vw, 112px)` on marketing pages, 1.0–1.08 line height, tracking near `-0.025em`. Never reproduce 158 px type in the workspace.
- **UI/body:** same sans at 14–16 px, 1.45–1.55 line height. Tables may use 14 px; customer-facing form text is at least 16 px.
- **Metadata:** Roboto Mono 400 or system monospace, 12–13 px, for times, IDs, compact counters. Do not use mono for paragraphs.
- **Hierarchy:** regular display weight is a signature, but 500–600 is permitted for operational labels, selected tabs, and dense data. Fidelity to the reference must not impair scan speed.

### Space and shape

- 4 px base scale: `4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 88`.
- Marketing max width 1200 px; 80–120 px vertical sections. Workspace content max width is task dependent; calendar may use full available width.
- Sidebar 232–256 px on wide screens. Compact header 64–72 px. Main app gutter 24–32 px desktop, 16 px small screens.
- 8 px button, 12 px input/nav, 16 px card radii. Do not use 40 px cards for dense schedules.
- Flat surfaces, 1 px boundaries, no decorative drop shadows. Floating menus may use a restrained shadow only if needed for separation and keyboard focus.

## Components and states

| Component | Anatomy | Required behavior |
|---|---|---|
| Primary button | ink fill, white label, 8 px radius, ≥40 px height | default/hover/focus/disabled/busy; spinner does not erase label |
| Secondary button | transparent, ink label, visible border | focus and disabled states |
| Signal arrow | 40×40 px lime tile with ink icon | marketing navigation only; accessible name |
| Tenant switcher | tenant name, optional approved small logo, chevron | show active tenant and permission-scoped choices; no ambiguous context |
| Calendar | resource/time grid, current time, status key | timezone label, keyboard path, loading/empty/error, conflict feedback |
| Appointment row | time, customer, service, staff, state | actions scoped by permissions; destructive actions require clear confirmation |
| Form field | label, hint, input, validation message | never rely on placeholder as label; preserve entered data on errors |
| Status badge | text + shape/icon + semantic color | distinct pending/confirmed/completed/cancelled/error |
| Notice | title, plain explanation, recovery action | no silent failure and no color-only meaning |
| Empty state | context-specific message + next action | distinguish no bookings from failed load |

### Operational status palette

Use tokens such as `status-success`, `status-warning`, `status-danger`, and `status-info` through the shared component layer. Their exact rendered pairs must pass contrast checks in implementation. They are **not** interchangeable with the brand lime. Reserve red for errors/cancellations and warnings for at-risk or pending conditions; do not suggest a booking is confirmed solely through color.

## Surface blueprints

### Platform marketing

1. **Navigation:** GORGONA Booking wordmark at left, Product / For salons / How it works / Security at right, one clear action. The logo treatment belongs to the platform; no KA Nails mark.
2. **Hero:** dark ink ground, large regular headline about taking and managing bookings, concise explanation, primary neutral action and secondary text link. Use a real interface preview or diagram, never generic molecular art.
3. **Proof of workflow:** enquiry → availability → booking → salon calendar → customer updates. Make claims only when the function exists.
4. **Feature bands:** availability, staff calendars, service catalogue, client records, operational controls. Cards and images have no invented metrics or testimonials.
5. **Footer:** deep neutral closure; product, support, legal links when available.

### Salon workspace

1. Persistent GORGONA shell, explicit active tenant, role/account controls.
2. Overview prioritizes today's appointments and booking exceptions before metrics.
3. Calendar is the central working screen; filters for staff/service and date are visible and reversible.
4. Appointments, Clients, Staff, Services, Business hours, Booking rules, Branding, and Settings are scoped to the active tenant.
5. Analytics and payments enter navigation only when backed by real data and permissions; an unavailable capability gets an honest state, not a fake dashboard.

### Platform admin

Separate role-gated shell and route. Tenant list, domain and publication status, user access, audit trail, and health may be added incrementally. No salon customer data appears across tenants by default. An operator must see the current scope before acting.

## Responsive, accessibility, and motion

- At narrow widths, marketing hero drops to 48–64 px and content stacks; app sidebar becomes an accessible drawer with a visible current-page title.
- Calendar offers an agenda/list alternative at small widths. Never hide appointment actions only behind hover.
- Test 200% zoom, keyboard-only booking management, visible focus, 4.5:1 normal-text contrast and 3:1 large-text/control boundaries. Decorative reference lines may remain subtle; interactive borders may not.
- Use transitions of roughly 120–180 ms for state change. Respect `prefers-reduced-motion`.
- Dates and slot times include the business timezone, especially when staff and customers differ.

## Brand boundary and implementation

Use [gorgona-booking.tokens.css](gorgona-booking.tokens.css) only in a platform root. The tenant switcher is an identity cue, not a wholesale tenant theme. Public KA Nails screens use [KA_NAILS_DESIGN_SYSTEM.md](KA_NAILS_DESIGN_SYSTEM.md) and its own token root. See [BRAND_AND_TENANT_CONTRACT.md](BRAND_AND_TENANT_CONTRACT.md) for domain and data isolation.

**Validation before release:** rendered type, dark/light contrast, calendar density with real booking lengths, small-screen agenda, all component states, explicit tenant context, and a second-tenant isolation fixture. This document is a design specification; none of these checks are claimed to have run against a live application.
