# Analytics and KPI contracts

Status: design only.

Use a versioned event envelope: `event_id`, `event_name`, `event_version`, UTC `occurred_at`, `tenant_id`, optional `location_id`, actor type, correlation/causation IDs, PII class and validated properties. Start with `service_viewed`, `booking_started`, `slot_selected`, `hold_created`, `deposit_succeeded`, `booking_confirmed`, `booking_cancelled`, `appointment_completed`, `review_submitted` and `rebook_completed`. Operational events come from committed domain transitions/outbox; marketing events cannot substitute for revenue truth. Do not send names, phone numbers, appointment notes or payment details to analytics.

Metric definitions before dashboards: booking conversion = confirmed bookings / booking starts; deposit conversion = captured deposits / deposit starts; completed revenue = captured service revenue attributable to completed appointments net of refunds; booked revenue = confirmed quoted totals less cancellations; average ticket = completed revenue / completed appointments; cancellation and no-show rates divide by eligible scheduled appointments. Rebooking, retention, utilization, attribution and LTV need cohort/window policies before publication. All reports use tenant location timezone and explicit refund/cancellation handling. Owner and source table for each metric are to be assigned during implementation.
