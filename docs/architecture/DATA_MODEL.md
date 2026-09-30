# Data model proposal

Status: design only. No migration has been applied.

Use UUID primary keys and tenant-scoped foreign keys/uniques where tenant ownership matters. Prefer `(tenant_id, id)` composite uniqueness on owned tables and `(tenant_id, referenced_id)` foreign keys so cross-tenant IDs cannot be injected. Keep customer PII and internal SOP content out of public catalog views.

| Domain | Candidate tables | Key integrity rule |
| --- | --- | --- |
| Identity | tenants, locations, memberships, roles | unique membership per tenant/user; trusted server tenant context |
| Catalog | services, variants, components, add_ons, compatibility_rules, catalog_versions | immutable published versions and server-authoritative price composition |
| People | staff, skills, staff_services, clients | tenant-bound references; consent/retention metadata |
| Scheduling | resources, schedules, exceptions, breaks | IANA timezone on location; valid positive intervals |
| Booking | holds, appointments, appointment_items, reservation_allocations, appointment_history, idempotency_keys | exclusion constraint on active exclusive allocations; immutable quote snapshot |
| Payments | payments, payment_events, refunds, webhook_inbox | unique provider IDs; append-only provider event record |
| Async | outbox_events, notification_deliveries | durable retry state and unique event/delivery keys |
| Content | gallery_items, reviews, memberships, gift_cards, loyalty_accounts | ownership, moderation, expiry and visibility controls |
| Insight | analytics_events, audit_events | versioned envelope; PII classification and retention |

Index query paths: tenant/location plus active appointment range, resource plus range, staff schedules, client lookup, idempotency key, outbox readiness and webhook provider ID. Measure representative queries with `EXPLAIN ANALYZE` before adding more. Use a catalog import only after owner-approved service durations, staff, schedules and policies are supplied.

KA Nails seed facts from the brief: Hammam Luxury $145; + gel $160 with public 120–145 minute range; + gel French $175 with public 130–155 minute range; extra foot massage +$20 and exactly +15 booking minutes; other salon gel removal +$15; Hammam Spa Upgrade +$40 only for compatible pedicures; French Gel +$15 only with gel; included heel care cannot be charged twice. The base Hammam **booking duration is unknown**, so it must remain non-bookable until configured. These facts are catalog seed candidates, not active database records.
