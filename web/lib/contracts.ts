import { z } from "zod";
const id = z.uuid();
const money = z.number().int().nonnegative();
const instant = z.iso.datetime({ offset: true });
const line = z.strictObject({
  kind: z.enum(["variant", "add_on"]),
  id,
  code: z.string(),
  name: z.string(),
  price_cents: money,
  duration_minutes: money,
  revision: z.number().int().positive(),
});
export const quoteSchema = z.strictObject({
  version: z.literal(1),
  currency: z.string().length(3),
  total_cents: money,
  booking_duration_minutes: z.number().int().positive(),
  lines: z.array(line),
});
const location = z.strictObject({
  id,
  name: z.string(),
  timezone: z.string(),
  today: z.iso.date(),
  last_day: z.iso.date(),
});
const variant = z.strictObject({
  id,
  service_id: id,
  service_name: z.string(),
  name: z.string(),
  price_cents: money,
  currency: z.string(),
  duration_minutes: z.number().int().positive(),
  provides: z.array(z.string()),
});
const addon = z.strictObject({
  id,
  name: z.string(),
  price_cents: money,
  currency: z.string(),
  duration_minutes: money,
  provides: z.array(z.string()),
  requires: z.array(z.string()),
  conflicts_with: z.array(z.string()),
});
export const bootstrapSchema = z.strictObject({
  version: z.literal(1),
  name: z.string(),
  branding: z.strictObject({
    logo_url: z
      .string()
      .regex(/^\/assets\/[A-Za-z0-9_/-]+\.(png|webp|svg|jpg)$/)
      .nullable(),
    accent: z
      .string()
      .regex(/^#[0-9a-fA-F]{6}$/)
      .nullable(),
  }),
  locations: z.array(location),
  variants: z.array(variant),
  add_ons: z.array(addon),
  artists: z.array(
    z.strictObject({
      id,
      name: z.string(),
      location_id: id,
      service_ids: z.array(id),
    }),
  ),
  rules: z.strictObject({
    version: z.literal(1),
    slot_interval_minutes: z.number().int(),
    advance_notice_minutes: money,
    max_days_ahead: z.number().int(),
  }),
  cancellation: z.strictObject({ version: z.literal(1), summary: z.string() }),
});
export const slotSchema = z.strictObject({
  resource_id: id,
  start_at: instant,
  end_at: instant,
});
export const availabilitySchema = z.strictObject({
  version: z.literal(1),
  timezone: z.string(),
  quote: quoteSchema,
  slots: z.array(slotSchema),
});
export const bookingSchema = z.strictObject({
  version: z.literal(1),
  booking_id: id,
  status: z.enum(["HOLD", "CONFIRMED", "CANCELLED", "EXPIRED"]),
  resource_id: id,
  start_at: instant,
  end_at: instant,
  hold_expires_at: instant.nullable(),
  quote: quoteSchema,
});
export type Bootstrap = z.infer<typeof bootstrapSchema>;
export type Availability = z.infer<typeof availabilitySchema>;
export type Booking = z.infer<typeof bookingSchema>;
export type Slot = z.infer<typeof slotSchema>;
export type Quote = z.infer<typeof quoteSchema>;
export type Details = {
  name: string;
  email: string;
  phone: string;
  accept_policy: true;
};
export type Selection = {
  location_id: string;
  variant_id: string;
  add_on_ids: string[];
  resource_id: string | null;
};
