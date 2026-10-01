/**
 * GORGONA Management API Client.
 * Communicates with /v1 authenticated staff routes with real PostgreSQL persistence.
 */

export interface OverviewStats {
  today_bookings_count: number;
  confirmed_bookings_count: number;
  cancelled_bookings_count: number;
  today_revenue_cents: number;
  active_staff_count: number;
  total_services_count: number;
}

export interface BookingSummary {
  booking_id: string;
  status: string;
  location_id: string;
  resource_id: string;
  resource_name: string;
  variant_id: string;
  service_name: string;
  variant_name: string;
  starts_at: string;
  ends_at: string;
  total_cents: number;
  currency: string;
  customer_name: string | null;
  customer_email: string | null;
  customer_phone: string | null;
  created_at: string;
  created_by: string;
}

export interface ActivityItem {
  id: string;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  details: Record<string, unknown>;
  occurred_at: string;
}

export interface SalonOverview {
  salon_id: string;
  salon_name: string;
  status: string;
  booking_state: string;
  stats: OverviewStats;
  today_bookings: BookingSummary[];
  recent_activity: ActivityItem[];
}

export interface ServiceView {
  id: string;
  service_id: string;
  code: string;
  name: string;
  status: string;
  price_cents: number;
  currency: string;
  booking_duration_minutes: number | null;
  is_bookable: boolean;
  revision: number;
}

export interface StaffView {
  id: string;
  location_id: string;
  kind: string;
  display_name: string;
  is_active: boolean;
}

export interface ResourceHours {
  id: string;
  weekday: number;
  opens_minute: number;
  closes_minute: number;
}

export interface StaffSchedule {
  resource_id: string;
  display_name: string;
  is_active: boolean;
  location_id: string;
  hours: ResourceHours[];
  service_ids: string[];
}

export interface ClientItem {
  customer_name: string;
  email: string;
  phone: string;
  total_bookings: number;
  confirmed_bookings: number;
  last_booking_at: string | null;
}

export interface LocationItem {
  id: string;
  name: string;
  timezone: string;
}

export interface BusinessHoursItem {
  id: string;
  location_id: string;
  weekday: number;
  opens_minute: number;
  closes_minute: number;
}

export interface SalonFactItem {
  fact_key: string;
  status: string;
  source_note: string | null;
  recorded_by: string;
  recorded_at: string;
}

export interface SalonEmbedOriginItem {
  id: string;
  origin: string;
  status: string;
  updated_at: string;
}

export interface SalonSettings {
  salon_id: string;
  slug: string;
  display_name: string;
  status: string;
  booking_state: string;
  locations: LocationItem[];
  business_hours: BusinessHoursItem[];
  policies: {
    cancellation_policy?: Record<string, unknown>;
    deposit_policy?: Record<string, unknown>;
    booking_rules?: Record<string, unknown>;
  };
  fact_confirmations: SalonFactItem[];
  embed_origins: SalonEmbedOriginItem[];
}

export interface MembershipView {
  salon_id: string;
  salon_name: string | null;
  role: string;
  status: string;
}

export interface MeView {
  user_id: string;
  display_name: string;
  platform_roles: string[];
  memberships: MembershipView[];
}

export class ManagementApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ManagementApiError";
  }
}

const TOKEN_KEY = "gorgona_staff_token";
const ACTIVE_SALON_KEY = "gorgona_active_salon";

export function getStaffToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setStaffToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
}

export function getActiveSalonId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACTIVE_SALON_KEY);
}

export function setActiveSalonId(salonId: string | null): void {
  if (typeof window === "undefined") return;
  if (salonId) {
    localStorage.setItem(ACTIVE_SALON_KEY, salonId);
  } else {
    localStorage.removeItem(ACTIVE_SALON_KEY);
  }
}

export async function managementFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const token = getStaffToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options?.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(path, {
    ...options,
    headers,
  });

  const text = await response.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!response.ok) {
    if (typeof data === "object" && data !== null && "error" in data) {
      const err = (data as { error: { code: string; message: string } }).error;
      throw new ManagementApiError(err.code, err.message);
    }
    if (response.status === 401) {
      throw new ManagementApiError(
        "AUTHENTICATION_REQUIRED",
        "Staff authentication required. Please sign in with your access token.",
      );
    }
    if (response.status === 403) {
      throw new ManagementApiError(
        "PERMISSION_DENIED",
        "You do not have permission for this salon action.",
      );
    }
    if (response.status === 503) {
      throw new ManagementApiError(
        "SERVICE_UNAVAILABLE",
        "Authentication or database service is temporarily unavailable.",
      );
    }
    throw new ManagementApiError(
      "UNKNOWN_ERROR",
      `Server returned ${response.status}: ${text || response.statusText}`,
    );
  }

  return data as T;
}

// --- Specific API Calls ---

export async function fetchMe(): Promise<MeView> {
  return managementFetch<MeView>("/v1/me");
}

export async function fetchOverview(salonId: string): Promise<SalonOverview> {
  return managementFetch<SalonOverview>(`/v1/salons/${salonId}/overview`);
}

export async function fetchBookings(
  salonId: string,
  params?: {
    startDate?: string;
    endDate?: string;
    resourceId?: string;
    status?: string;
  },
): Promise<BookingSummary[]> {
  const query = new URLSearchParams();
  if (params?.startDate) query.set("start_date", params.startDate);
  if (params?.endDate) query.set("end_date", params.endDate);
  if (params?.resourceId) query.set("resource_id", params.resourceId);
  if (params?.status) query.set("status", params.status);
  const qStr = query.toString();
  return managementFetch<BookingSummary[]>(
    `/v1/salons/${salonId}/bookings${qStr ? `?${qStr}` : ""}`,
  );
}

export async function createStaffBooking(
  salonId: string,
  data: {
    location_id: string;
    resource_id: string;
    variant_id: string;
    starts_at: string;
    customer_name: string;
    customer_email: string;
    customer_phone: string;
    add_on_ids?: string[];
  },
): Promise<BookingSummary> {
  return managementFetch<BookingSummary>(`/v1/salons/${salonId}/bookings`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function rescheduleBooking(
  salonId: string,
  bookingId: string,
  data: {
    new_starts_at: string;
    new_resource_id?: string;
  },
): Promise<BookingSummary> {
  return managementFetch<BookingSummary>(
    `/v1/salons/${salonId}/bookings/${bookingId}/reschedule`,
    {
      method: "POST",
      body: JSON.stringify(data),
    },
  );
}

export async function cancelBooking(
  salonId: string,
  bookingId: string,
  reason: string = "Cancelled by staff",
): Promise<BookingSummary> {
  return managementFetch<BookingSummary>(
    `/v1/salons/${salonId}/bookings/${bookingId}/cancel`,
    {
      method: "POST",
      body: JSON.stringify({ reason }),
    },
  );
}

export async function fetchClients(
  salonId: string,
  search?: string,
): Promise<ClientItem[]> {
  const q = search?.trim() ? `?q=${encodeURIComponent(search.trim())}` : "";
  return managementFetch<ClientItem[]>(`/v1/salons/${salonId}/clients${q}`);
}

export async function fetchClientHistory(
  salonId: string,
  params: { email?: string; phone?: string },
): Promise<BookingSummary[]> {
  const query = new URLSearchParams();
  if (params.email) query.set("email", params.email);
  if (params.phone) query.set("phone", params.phone);
  return managementFetch<BookingSummary[]>(
    `/v1/salons/${salonId}/clients/history?${query.toString()}`,
  );
}

export async function fetchServices(salonId: string): Promise<ServiceView[]> {
  return managementFetch<ServiceView[]>(`/v1/salons/${salonId}/services`);
}

export async function createService(
  salonId: string,
  data: {
    service_code: string;
    service_name: string;
    code: string;
    name: string;
    price_cents: number;
    currency: string;
    booking_duration_minutes?: number;
    display_duration_min_minutes?: number;
    display_duration_max_minutes?: number;
  },
): Promise<ServiceView> {
  return managementFetch<ServiceView>(`/v1/salons/${salonId}/services`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateService(
  salonId: string,
  variantId: string,
  data: {
    name?: string;
    price_cents?: number;
    booking_duration_minutes?: number;
    is_bookable?: boolean;
  },
): Promise<ServiceView> {
  return managementFetch<ServiceView>(
    `/v1/salons/${salonId}/services/${variantId}`,
    {
      method: "PATCH",
      body: JSON.stringify(data),
    },
  );
}

export async function publishService(
  salonId: string,
  variantId: string,
): Promise<ServiceView> {
  return managementFetch<ServiceView>(
    `/v1/salons/${salonId}/services/${variantId}/publish`,
    {
      method: "POST",
    },
  );
}

export async function unpublishService(
  salonId: string,
  variantId: string,
): Promise<ServiceView> {
  return managementFetch<ServiceView>(
    `/v1/salons/${salonId}/services/${variantId}/unpublish`,
    {
      method: "POST",
    },
  );
}

export async function fetchStaff(salonId: string): Promise<StaffView[]> {
  return managementFetch<StaffView[]>(`/v1/salons/${salonId}/staff`);
}

export async function createStaff(
  salonId: string,
  data: {
    display_name: string;
    location_id: string;
    kind: "artist" | "chair" | "room";
  },
): Promise<StaffView> {
  return managementFetch<StaffView>(`/v1/salons/${salonId}/staff`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateStaff(
  salonId: string,
  resourceId: string,
  data: {
    display_name?: string;
    is_active?: boolean;
  },
): Promise<StaffView> {
  return managementFetch<StaffView>(
    `/v1/salons/${salonId}/staff/${resourceId}`,
    {
      method: "PATCH",
      body: JSON.stringify(data),
    },
  );
}

export async function fetchStaffSchedule(
  salonId: string,
  resourceId: string,
): Promise<StaffSchedule> {
  return managementFetch<StaffSchedule>(
    `/v1/salons/${salonId}/staff/${resourceId}/schedule`,
  );
}

export async function updateStaffSchedule(
  salonId: string,
  resourceId: string,
  hours: { weekday: number; opens_minute: number; closes_minute: number }[],
): Promise<StaffSchedule> {
  return managementFetch<StaffSchedule>(
    `/v1/salons/${salonId}/staff/${resourceId}/schedule`,
    {
      method: "PUT",
      body: JSON.stringify({ hours }),
    },
  );
}

export async function updateStaffServices(
  salonId: string,
  resourceId: string,
  serviceIds: string[],
): Promise<StaffSchedule> {
  return managementFetch<StaffSchedule>(
    `/v1/salons/${salonId}/staff/${resourceId}/services`,
    {
      method: "PUT",
      body: JSON.stringify({ service_ids: serviceIds }),
    },
  );
}

export async function fetchActivity(salonId: string): Promise<ActivityItem[]> {
  return managementFetch<ActivityItem[]>(`/v1/salons/${salonId}/activity`);
}

export async function fetchSettings(salonId: string): Promise<SalonSettings> {
  return managementFetch<SalonSettings>(`/v1/salons/${salonId}/settings`);
}

export function formatPrice(cents: number, currency: string = "USD"): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
  }).format(cents / 100);
}

export function formatTime(isoString: string): string {
  const d = new Date(isoString);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function formatDate(isoString: string): string {
  const d = new Date(isoString);
  return d.toLocaleDateString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
