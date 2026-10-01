import { z } from "zod";
import { availabilitySchema } from "./contracts";
const id = z.uuid();
const instant = z.iso.datetime({ offset: true });
const count = z.number().int().nonnegative();
const record = z.record(z.string(), z.unknown());
const booking = z.object({
  booking_id: id,
  status: z.enum(["HOLD", "CONFIRMED", "CANCELLED", "EXPIRED"]),
  location_id: id,
  resource_id: id,
  resource_name: z.string(),
  variant_id: id,
  service_name: z.string(),
  variant_name: z.string(),
  starts_at: instant,
  ends_at: instant,
  total_cents: count,
  currency: z.string().length(3),
  customer_name: z.string().nullable(),
  customer_email: z.string().nullable(),
  customer_phone: z.string().nullable(),
  created_at: instant,
  created_by: z.string(),
});
const activity = z.object({
  id,
  actor: z.string(),
  action: z.string(),
  target_type: z.string(),
  target_id: z.string(),
  details: record,
  occurred_at: instant,
});
const service = z.object({
  id,
  service_id: id,
  code: z.string(),
  name: z.string(),
  status: z.string(),
  price_cents: count,
  currency: z.string().length(3),
  booking_duration_minutes: count.nullable(),
  is_bookable: z.boolean(),
  revision: z.number().int().positive(),
});
const staff = z.object({
  id,
  location_id: id,
  kind: z.string(),
  display_name: z.string(),
  is_active: z.boolean(),
});
const hours = z.object({
  id,
  weekday: z.number().int(),
  opens_minute: count,
  closes_minute: count,
});
const schedule = z.object({
  resource_id: id,
  display_name: z.string(),
  is_active: z.boolean(),
  location_id: id,
  hours: z.array(hours),
  service_ids: z.array(id),
});
const client = z.object({
  customer_name: z.string(),
  email: z.string(),
  phone: z.string(),
  total_bookings: count,
  confirmed_bookings: count,
  last_booking_at: instant.nullable(),
});
const me = z.object({
  user_id: id,
  display_name: z.string(),
  platform_roles: z.array(z.string()),
  memberships: z.array(
    z.object({
      salon_id: id,
      salon_name: z.string().nullable(),
      role: z.string(),
      status: z.string(),
    }),
  ),
});
const overview = z.object({
  salon_id: id,
  salon_name: z.string(),
  status: z.string(),
  booking_state: z.string(),
  stats: z.object({
    today_bookings_count: count,
    confirmed_bookings_count: count,
    cancelled_bookings_count: count,
    today_revenue_cents: count,
    active_staff_count: count,
    total_services_count: count,
  }),
  today_bookings: z.array(booking),
  recent_activity: z.array(activity),
});
const settings = z.object({
  salon_id: id,
  slug: z.string(),
  display_name: z.string(),
  status: z.string(),
  booking_state: z.string(),
  locations: z.array(z.object({ id, name: z.string(), timezone: z.string() })),
  business_hours: z.array(hours.extend({ location_id: id })),
  policies: z.object({
    booking_rules: record.optional(),
    cancellation_policy: record.optional(),
    deposit_policy: record.optional(),
  }),
  fact_confirmations: z.array(
    z.object({
      fact_key: z.string(),
      status: z.string(),
      source_note: z.string().nullable(),
      recorded_by: z.string(),
      recorded_at: instant,
    }),
  ),
  embed_origins: z.array(
    z.object({
      id,
      origin: z.string(),
      status: z.string(),
      updated_at: instant,
    }),
  ),
});
const readiness = z.object({
  salon_id: id,
  ready: z.boolean(),
  missing: z.array(z.string()),
  booking_state: z.string(),
});

/** A response is validated before management views receive any external fields. */
export function managementResponseSchema(
  path: string,
  method: string,
): z.ZodType {
  const clean = path.split("?")[0] ?? "";
  if (clean === "/v1/me") return me;
  const suffix = clean.replace(/^\/v1\/salons\/[0-9a-f-]{36}/i, "");
  if (suffix === "/availability") return availabilitySchema;
  if (suffix === "/overview") return overview;
  if (suffix === "/settings") return settings;
  if (suffix === "/activity") return z.array(activity);
  if (suffix === "/clients") return z.array(client);
  if (suffix === "/clients/history") return z.array(booking);
  if (suffix === "/bookings")
    return method === "GET" ? z.array(booking) : booking;
  if (/^\/bookings\//.test(suffix)) return booking;
  if (suffix === "/services")
    return method === "GET" ? z.array(service) : service;
  if (/^\/services\//.test(suffix)) return service;
  if (suffix === "/staff") return method === "GET" ? z.array(staff) : staff;
  if (/^\/staff\/[^/]+\/(schedule|services)$/.test(suffix)) return schedule;
  if (/^\/staff\//.test(suffix)) return staff;
  if (
    suffix === "/business-hours" ||
    suffix === "/policies" ||
    /^\/facts\//.test(suffix)
  )
    return readiness;
  throw new Error("Unrecognized management response contract");
}
