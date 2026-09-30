import { z } from "zod";
const errorSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    request_id: z.string(),
  }),
});
export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  schema: z.ZodType<T>,
  body?: unknown,
  headers?: Record<string, string>,
  signal?: AbortSignal,
): Promise<T> {
  try {
    const response = await fetch(`/v1/customer/${path}`, {
      method: body === undefined ? "GET" : "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(15000)])
        : AbortSignal.timeout(15000),
    });
    const data: unknown = await response.json();
    if (!response.ok) {
      const parsed = errorSchema.safeParse(data);
      if (parsed.success)
        throw new ApiError(parsed.data.error.code, parsed.data.error.message);
      throw new ApiError(
        "SERVICE_UNAVAILABLE",
        "Booking is temporarily unavailable. Please try again.",
      );
    }
    const parsed = schema.safeParse(data);
    if (!parsed.success)
      throw new ApiError(
        "INVALID_RESPONSE",
        "We couldn’t load booking information. Please try again.",
      );
    return parsed.data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      "NETWORK_ERROR",
      "We couldn’t reach the studio. Your request may have arrived; retry to check it safely.",
    );
  }
}
export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}
export function newCapability(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}
export function money(cents: number, currency: string): string {
  return new Intl.NumberFormat("en", { style: "currency", currency }).format(
    cents / 100,
  );
}
export function appointmentTime(instant: string, timezone: string): string {
  return new Intl.DateTimeFormat("en", {
    timeZone: timezone,
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(instant));
}
