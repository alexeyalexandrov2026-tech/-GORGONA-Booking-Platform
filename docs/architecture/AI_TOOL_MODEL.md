# AI concierge tool model

Status: design only; no AI provider or model selected.

The model extracts service intent and explains options. It cannot invent prices, availability, business hours or a confirmed appointment. A policy engine outside the model resolves tenant/actor permissions, validates typed versioned tool requests, applies timeouts/cost limits, and records trace/audit IDs. Expose narrow tools: `search_services`, `get_service_options`, `check_availability`, `quote_booking`, `create_booking_hold`, `get_appointment`, `reschedule_appointment`, `cancel_appointment`, and `join_waitlist`. Payment initiation and booking confirmation require deterministic domain validation and appropriate customer action.

Never expose arbitrary SQL, shell, generic HTTP, tenant selection, refund issuance, or service-role access to the model. Treat retrieved text and model output as untrusted. On provider failure, offer the regular booking flow or human contact without a success-shaped mock. Record user corrections and notes with original/corrected output, evidence, context, model/tool version, validation status and learning scope; promote learning only after review and evaluation.
