"use client";

import Link from "next/link";
import React, { useEffect, useState, useCallback } from "react";
import { ManagementLayout } from "../../components/management-layout";
import {
  BookingSummary,
  createStaffBooking,
  fetchOverview,
  fetchServices,
  fetchStaff,
  formatDate,
  formatPrice,
  formatTime,
  SalonOverview,
  ServiceView,
  StaffView,
} from "../../lib/management-api";

export default function OverviewPage() {
  return (
    <ManagementLayout>
      {({ salonId, refresh: triggerRefresh }) => (
        <OverviewContent salonId={salonId} onRefresh={triggerRefresh} />
      )}
    </ManagementLayout>
  );
}

function OverviewContent({
  salonId,
  onRefresh,
}: {
  salonId: string;
  onRefresh: () => void;
}) {
  const [overview, setOverview] = useState<SalonOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  // New Booking Modal State
  const [showNewBookingModal, setShowNewBookingModal] = useState(false);
  const [staffList, setStaffList] = useState<StaffView[]>([]);
  const [servicesList, setServicesList] = useState<ServiceView[]>([]);
  const [selectedStaffId, setSelectedStaffId] = useState("");
  const [selectedVariantId, setSelectedVariantId] = useState("");
  const [bookingDate, setBookingDate] = useState(
    () => new Date().toISOString().split("T")[0] || "",
  );
  const [bookingTime, setBookingTime] = useState("10:00");
  const [customerName, setCustomerName] = useState("");
  const [customerEmail, setCustomerEmail] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [creatingBooking, setCreatingBooking] = useState(false);
  const [bookingError, setBookingError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let isSubscribed = true;
    fetchOverview(salonId)
      .then((data) => {
        if (!isSubscribed) return;
        setOverview(data);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!isSubscribed) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (isSubscribed) setLoading(false);
      });
    return () => {
      isSubscribed = false;
    };
  }, [salonId, refreshKey]);

  const loadData = useCallback(() => {
    setLoading(true);
    setRefreshKey((k) => k + 1);
  }, []);

  const handleOpenBookingModal = async () => {
    setBookingError(null);
    setShowNewBookingModal(true);
    try {
      const [staff, services] = await Promise.all([
        fetchStaff(salonId),
        fetchServices(salonId),
      ]);
      setStaffList(staff);
      setServicesList(services.filter((s) => s.is_bookable));
      if (staff.length > 0 && staff[0] && !selectedStaffId) {
        setSelectedStaffId(staff[0].id);
      }
      const bookable = services.filter((s) => s.is_bookable);
      if (bookable.length > 0 && bookable[0] && !selectedVariantId) {
        setSelectedVariantId(bookable[0].id);
      }
    } catch (err: unknown) {
      setBookingError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleCreateBooking = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!overview || !selectedStaffId || !selectedVariantId) return;

    setCreatingBooking(true);
    setBookingError(null);

    const locationId =
      staffList.find((s) => s.id === selectedStaffId)?.location_id ||
      overview.today_bookings[0]?.location_id;

    if (!locationId) {
      setBookingError("No valid location found for this staff member");
      setCreatingBooking(false);
      return;
    }

    try {
      const startsAt = new Date(
        `${bookingDate}T${bookingTime}:00Z`,
      ).toISOString();
      await createStaffBooking(salonId, {
        location_id: locationId,
        resource_id: selectedStaffId,
        variant_id: selectedVariantId,
        starts_at: startsAt,
        customer_name: customerName.trim(),
        customer_email: customerEmail.trim(),
        customer_phone: customerPhone.trim(),
      });
      setShowNewBookingModal(false);
      setActionMessage("Booking created successfully!");
      setCustomerName("");
      setCustomerEmail("");
      setCustomerPhone("");
      loadData();
      onRefresh();
    } catch (err: unknown) {
      setBookingError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreatingBooking(false);
    }
  };

  if (loading) {
    return <p className="muted">Loading salon overview...</p>;
  }

  if (error || !overview) {
    return (
      <div className="mgmt-error-banner" role="alert">
        <h3>Could not load salon overview</h3>
        <p>{error || "Unknown error"}</p>
        <button type="button" onClick={loadData}>
          Retry
        </button>
      </div>
    );
  }

  const { stats } = overview;

  return (
    <div>
      {actionMessage && (
        <div className="hold-note" style={{ marginBottom: "20px" }}>
          <strong>Notification:</strong> {actionMessage}
        </div>
      )}

      <div className="mgmt-section-header">
        <div>
          <h2>Today’s Overview — {overview.salon_name}</h2>
          <p className="muted">
            Status:{" "}
            <span className="mgmt-status-pill status-active">
              {overview.status}
            </span>{" "}
            &bull; Booking:{" "}
            <span
              className={`mgmt-status-pill status-${overview.booking_state}`}
            >
              {overview.booking_state}
            </span>
          </p>
        </div>
        <div style={{ display: "flex", gap: "10px" }}>
          <button type="button" onClick={handleOpenBookingModal}>
            + New Appointment
          </button>
          <Link href="/calendar/" className="button secondary">
            Open Calendar
          </Link>
        </div>
      </div>

      <div className="mgmt-kpi-grid">
        <div className="mgmt-kpi-card">
          <div className="mgmt-kpi-label">Today’s Bookings</div>
          <div className="mgmt-kpi-number">{stats.today_bookings_count}</div>
        </div>
        <div className="mgmt-kpi-card">
          <div className="mgmt-kpi-label">Confirmed</div>
          <div className="mgmt-kpi-number">
            {stats.confirmed_bookings_count}
          </div>
        </div>
        <div className="mgmt-kpi-card">
          <div className="mgmt-kpi-label">Cancelled</div>
          <div className="mgmt-kpi-number">
            {stats.cancelled_bookings_count}
          </div>
        </div>
        <div className="mgmt-kpi-card">
          <div className="mgmt-kpi-label">Today’s Revenue</div>
          <div className="mgmt-kpi-number">
            {formatPrice(stats.today_revenue_cents)}
          </div>
        </div>
        <div className="mgmt-kpi-card">
          <div className="mgmt-kpi-label">Active Staff</div>
          <div className="mgmt-kpi-number">{stats.active_staff_count}</div>
        </div>
        <div className="mgmt-kpi-card">
          <div className="mgmt-kpi-label">Live Services</div>
          <div className="mgmt-kpi-number">{stats.total_services_count}</div>
        </div>
      </div>

      <div className="mgmt-section-header">
        <h2>Today’s Schedule</h2>
        <span className="muted">
          {overview.today_bookings.length} appointments
        </span>
      </div>

      <div className="mgmt-table-container">
        <table className="mgmt-table" aria-label="Today's Appointments">
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Client</th>
              <th scope="col">Service</th>
              <th scope="col">Staff</th>
              <th scope="col">Price</th>
              <th scope="col">Status</th>
              <th scope="col">Action</th>
            </tr>
          </thead>
          <tbody>
            {overview.today_bookings.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  style={{ textAlign: "center", padding: "32px" }}
                >
                  <p className="muted">No appointments scheduled for today.</p>
                  <button
                    type="button"
                    className="secondary"
                    onClick={handleOpenBookingModal}
                  >
                    Book First Appointment
                  </button>
                </td>
              </tr>
            ) : (
              overview.today_bookings.map((b: BookingSummary) => (
                <tr key={b.booking_id}>
                  <td>
                    <strong>{formatTime(b.starts_at)}</strong> -{" "}
                    {formatTime(b.ends_at)}
                  </td>
                  <td>
                    <div>{b.customer_name || "Guest Client"}</div>
                    <small className="muted">
                      {b.customer_phone || b.customer_email || "No contact"}
                    </small>
                  </td>
                  <td>
                    <div>{b.service_name}</div>
                    <small className="muted">{b.variant_name}</small>
                  </td>
                  <td>{b.resource_name}</td>
                  <td>{formatPrice(b.total_cents, b.currency)}</td>
                  <td>
                    <span
                      className={`mgmt-status-pill status-${b.status.toLowerCase()}`}
                    >
                      {b.status}
                    </span>
                  </td>
                  <td>
                    <Link
                      href={`/calendar/?booking_id=${b.booking_id}`}
                      className="button secondary mgmt-btn-small"
                    >
                      Inspect
                    </Link>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mgmt-section-header">
        <h2>Recent Audit Activity</h2>
        <span className="muted">
          Last {overview.recent_activity.length} operations
        </span>
      </div>

      <div className="mgmt-table-container">
        <table className="mgmt-table" aria-label="Recent Audit Activity">
          <thead>
            <tr>
              <th scope="col">Timestamp</th>
              <th scope="col">Actor</th>
              <th scope="col">Action</th>
              <th scope="col">Target</th>
            </tr>
          </thead>
          <tbody>
            {overview.recent_activity.length === 0 ? (
              <tr>
                <td
                  colSpan={4}
                  style={{ textAlign: "center", padding: "20px" }}
                >
                  <span className="muted">No recent activity recorded.</span>
                </td>
              </tr>
            ) : (
              overview.recent_activity.map((act) => (
                <tr key={act.id}>
                  <td>
                    {formatDate(act.occurred_at)} {formatTime(act.occurred_at)}
                  </td>
                  <td>
                    <code>{act.actor}</code>
                  </td>
                  <td>
                    <strong>{act.action}</strong>
                  </td>
                  <td>
                    {act.target_type} ({act.target_id.slice(0, 8)}...)
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {showNewBookingModal && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="new-booking-title"
        >
          <div className="mgmt-modal-card">
            <h2 id="new-booking-title">Create Staff Appointment</h2>
            <p className="muted">
              Schedule a confirmed booking directly with PostgreSQL
              double-booking protection.
            </p>

            {bookingError && (
              <div className="error" style={{ marginBottom: "16px" }}>
                {bookingError}
              </div>
            )}

            <form onSubmit={handleCreateBooking}>
              <label htmlFor="booking-service">
                Service:
                <select
                  id="booking-service"
                  value={selectedVariantId}
                  onChange={(e) => setSelectedVariantId(e.target.value)}
                  required
                >
                  {servicesList.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} ({s.booking_duration_minutes || 60}m) &bull;{" "}
                      {formatPrice(s.price_cents, s.currency)}
                    </option>
                  ))}
                </select>
              </label>

              <label htmlFor="booking-staff">
                Staff Member:
                <select
                  id="booking-staff"
                  value={selectedStaffId}
                  onChange={(e) => setSelectedStaffId(e.target.value)}
                  required
                >
                  {staffList.map((st) => (
                    <option key={st.id} value={st.id}>
                      {st.display_name} ({st.kind})
                    </option>
                  ))}
                </select>
              </label>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "16px",
                }}
              >
                <label htmlFor="booking-date">
                  Date:
                  <input
                    id="booking-date"
                    type="date"
                    value={bookingDate}
                    onChange={(e) => setBookingDate(e.target.value)}
                    required
                  />
                </label>
                <label htmlFor="booking-time">
                  Start Time:
                  <input
                    id="booking-time"
                    type="time"
                    value={bookingTime}
                    onChange={(e) => setBookingTime(e.target.value)}
                    required
                  />
                </label>
              </div>

              <label htmlFor="booking-client-name">
                Client Name:
                <input
                  id="booking-client-name"
                  type="text"
                  placeholder="Full name"
                  value={customerName}
                  onChange={(e) => setCustomerName(e.target.value)}
                  required
                />
              </label>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "16px",
                }}
              >
                <label htmlFor="booking-client-email">
                  Email:
                  <input
                    id="booking-client-email"
                    type="email"
                    placeholder="email@example.com"
                    value={customerEmail}
                    onChange={(e) => setCustomerEmail(e.target.value)}
                    required
                  />
                </label>
                <label htmlFor="booking-client-phone">
                  Phone:
                  <input
                    id="booking-client-phone"
                    type="tel"
                    placeholder="+1 555 123 4567"
                    value={customerPhone}
                    onChange={(e) => setCustomerPhone(e.target.value)}
                    required
                  />
                </label>
              </div>

              <div className="mgmt-form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setShowNewBookingModal(false)}
                  disabled={creatingBooking}
                >
                  Cancel
                </button>
                <button type="submit" disabled={creatingBooking}>
                  {creatingBooking ? "Reserving Slot..." : "Confirm Booking"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
