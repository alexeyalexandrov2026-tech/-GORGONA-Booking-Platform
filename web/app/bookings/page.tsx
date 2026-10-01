"use client";

import React, { useEffect, useState, useCallback } from "react";
import { ManagementLayout } from "../../components/management-layout";
import {
  BookingSummary,
  cancelBooking,
  createStaffBooking,
  fetchBookings,
  fetchServices,
  fetchStaff,
  formatDate,
  formatPrice,
  formatTime,
  rescheduleBooking,
  ServiceView,
  StaffView,
} from "../../lib/management-api";

export default function BookingsPage() {
  return (
    <ManagementLayout>
      {({ salonId, refresh: triggerRefresh }) => (
        <BookingsContent salonId={salonId} onRefresh={triggerRefresh} />
      )}
    </ManagementLayout>
  );
}

function BookingsContent({
  salonId,
  onRefresh,
}: {
  salonId: string;
  onRefresh: () => void;
}) {
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 7);
    return d.toISOString().split("T")[0] || "";
  });
  const [endDate, setEndDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 30);
    return d.toISOString().split("T")[0] || "";
  });
  const [selectedStaffFilter, setSelectedStaffFilter] = useState("all");
  const [selectedStatusFilter, setSelectedStatusFilter] = useState("all");

  const [bookings, setBookings] = useState<BookingSummary[]>([]);
  const [staffList, setStaffList] = useState<StaffView[]>([]);
  const [servicesList, setServicesList] = useState<ServiceView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Modals
  const [inspectBooking, setInspectBooking] = useState<BookingSummary | null>(
    null,
  );

  // Reschedule Modal
  const [rescheduleTarget, setRescheduleTarget] =
    useState<BookingSummary | null>(null);
  const [rescheduleDate, setRescheduleDate] = useState(
    () => new Date().toISOString().split("T")[0] || "",
  );
  const [rescheduleTime, setRescheduleTime] = useState("10:00");
  const [rescheduleStaffId, setRescheduleStaffId] = useState("");
  const [isRescheduling, setIsRescheduling] = useState(false);
  const [rescheduleError, setRescheduleError] = useState<string | null>(null);

  // Cancel Modal
  const [cancelTarget, setCancelTarget] = useState<BookingSummary | null>(null);
  const [cancelReason, setCancelReason] = useState(
    "Customer requested cancellation",
  );
  const [isCancelling, setIsCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  // New Booking Modal
  const [showNewBookingModal, setShowNewBookingModal] = useState(false);
  const [newStaffId, setNewStaffId] = useState("");
  const [newVariantId, setNewVariantId] = useState("");
  const [newBookingDate, setNewBookingDate] = useState(
    () => new Date().toISOString().split("T")[0] || "",
  );
  const [newBookingTime, setNewBookingTime] = useState("10:00");
  const [newClientName, setNewClientName] = useState("");
  const [newClientEmail, setNewClientEmail] = useState("");
  const [newClientPhone, setNewClientPhone] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [newBookingError, setNewBookingError] = useState<string | null>(null);

  useEffect(() => {
    let isSubscribed = true;
    const startIso = startDate
      ? new Date(`${startDate}T00:00:00Z`).toISOString()
      : undefined;
    const endIso = endDate
      ? new Date(`${endDate}T23:59:59Z`).toISOString()
      : undefined;

    Promise.all([
      fetchBookings(salonId, {
        startDate: startIso,
        endDate: endIso,
        resourceId:
          selectedStaffFilter !== "all" ? selectedStaffFilter : undefined,
        status:
          selectedStatusFilter !== "all" ? selectedStatusFilter : undefined,
      }),
      fetchStaff(salonId),
      fetchServices(salonId),
    ])
      .then(([bList, sList, vList]) => {
        if (!isSubscribed) return;
        setBookings(bList);
        setStaffList(sList);
        setServicesList(vList.filter((v) => v.is_bookable));
        if (sList.length > 0 && sList[0] && !newStaffId)
          setNewStaffId(sList[0].id);
        const bookable = vList.filter((v) => v.is_bookable);
        if (bookable.length > 0 && bookable[0] && !newVariantId)
          setNewVariantId(bookable[0].id);
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
  }, [
    salonId,
    startDate,
    endDate,
    selectedStaffFilter,
    selectedStatusFilter,
    newStaffId,
    newVariantId,
    refreshKey,
  ]);

  const loadData = useCallback(() => {
    setLoading(true);
    setRefreshKey((k) => k + 1);
  }, []);

  const handleOpenReschedule = (b: BookingSummary) => {
    setRescheduleTarget(b);
    setRescheduleStaffId(b.resource_id);
    const datePart = b.starts_at.split("T")[0] || "";
    setRescheduleDate(datePart);
    setRescheduleError(null);
  };

  const handleExecuteReschedule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!rescheduleTarget) return;
    setIsRescheduling(true);
    setRescheduleError(null);
    try {
      const newStartsAt = new Date(
        `${rescheduleDate}T${rescheduleTime}:00Z`,
      ).toISOString();
      await rescheduleBooking(salonId, rescheduleTarget.booking_id, {
        new_starts_at: newStartsAt,
        new_resource_id: rescheduleStaffId || undefined,
      });
      setRescheduleTarget(null);
      setToastMessage("Appointment rescheduled successfully.");
      loadData();
      onRefresh();
    } catch (err: unknown) {
      setRescheduleError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsRescheduling(false);
    }
  };

  const handleOpenCancel = (b: BookingSummary) => {
    setCancelTarget(b);
    setCancelReason("Customer requested cancellation");
    setCancelError(null);
  };

  const handleExecuteCancel = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!cancelTarget) return;
    setIsCancelling(true);
    setCancelError(null);
    try {
      await cancelBooking(salonId, cancelTarget.booking_id, cancelReason);
      setCancelTarget(null);
      setToastMessage("Appointment cancelled successfully.");
      loadData();
      onRefresh();
    } catch (err: unknown) {
      setCancelError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsCancelling(false);
    }
  };

  const handleOpenNewBooking = () => {
    setNewBookingError(null);
    setShowNewBookingModal(true);
  };

  const handleCreateBookingSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newStaffId || !newVariantId) return;

    setIsCreating(true);
    setNewBookingError(null);

    const staffMember = staffList.find((s) => s.id === newStaffId);
    const locationId =
      staffMember?.location_id || (bookings[0]?.location_id ?? "");

    if (!locationId) {
      setNewBookingError(
        "No salon location found. Please configure a location in Settings.",
      );
      setIsCreating(false);
      return;
    }

    try {
      const startsAt = new Date(
        `${newBookingDate}T${newBookingTime}:00Z`,
      ).toISOString();
      await createStaffBooking(salonId, {
        location_id: locationId,
        resource_id: newStaffId,
        variant_id: newVariantId,
        starts_at: startsAt,
        customer_name: newClientName.trim(),
        customer_email: newClientEmail.trim(),
        customer_phone: newClientPhone.trim(),
      });
      setShowNewBookingModal(false);
      setNewClientName("");
      setNewClientEmail("");
      setNewClientPhone("");
      setToastMessage("Appointment booked successfully.");
      loadData();
      onRefresh();
    } catch (err: unknown) {
      setNewBookingError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div>
      {toastMessage && (
        <aside
          role="status"
          style={{
            background: "#e6f4ea",
            border: "1px solid #137333",
            color: "#137333",
            padding: "12px 18px",
            borderRadius: "6px",
            marginBottom: "20px",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>{toastMessage}</span>
          <button
            type="button"
            className="secondary mgmt-btn-small"
            onClick={() => setToastMessage(null)}
            aria-label="Dismiss message"
          >
            Dismiss
          </button>
        </aside>
      )}

      <div className="mgmt-section-header">
        <div>
          <h2>Master Bookings Register</h2>
          <p
            style={{
              color: "var(--muted)",
              margin: "4px 0 0",
              fontSize: "0.9rem",
            }}
          >
            Comprehensive appointment ledger with date range, staff, and status
            filtering.
          </p>
        </div>
        <div style={{ display: "flex", gap: "10px" }}>
          <button
            type="button"
            className="secondary mgmt-action-btn"
            onClick={loadData}
            disabled={loading}
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>
          <button
            type="button"
            className="mgmt-action-btn"
            onClick={handleOpenNewBooking}
          >
            + New Appointment
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="mgmt-filter-bar">
        <div className="mgmt-filter-group">
          <label htmlFor="start-date-filter">From:</label>
          <input
            id="start-date-filter"
            type="date"
            value={startDate}
            onChange={(e) => {
              setStartDate(e.target.value);
              setLoading(true);
            }}
          />
        </div>
        <div className="mgmt-filter-group">
          <label htmlFor="end-date-filter">To:</label>
          <input
            id="end-date-filter"
            type="date"
            value={endDate}
            onChange={(e) => {
              setEndDate(e.target.value);
              setLoading(true);
            }}
          />
        </div>
        <div className="mgmt-filter-group">
          <label htmlFor="staff-filter">Staff:</label>
          <select
            id="staff-filter"
            value={selectedStaffFilter}
            onChange={(e) => {
              setSelectedStaffFilter(e.target.value);
              setLoading(true);
            }}
          >
            <option value="all">All Technicians</option>
            {staffList.map((s) => (
              <option key={s.id} value={s.id}>
                {s.display_name}
              </option>
            ))}
          </select>
        </div>
        <div className="mgmt-filter-group">
          <label htmlFor="status-filter">Status:</label>
          <select
            id="status-filter"
            value={selectedStatusFilter}
            onChange={(e) => {
              setSelectedStatusFilter(e.target.value);
              setLoading(true);
            }}
          >
            <option value="all">All Statuses</option>
            <option value="CONFIRMED">Confirmed</option>
            <option value="HOLD">Hold</option>
            <option value="CANCELLED">Cancelled</option>
          </select>
        </div>
      </div>

      {error && (
        <div className="mgmt-error-banner" role="alert">
          <h3>Error loading bookings</h3>
          <p>{error}</p>
          <button
            type="button"
            className="secondary mgmt-btn-small"
            onClick={loadData}
          >
            Retry
          </button>
        </div>
      )}

      {loading && !error && (
        <div className="mgmt-loading" aria-live="polite">
          <p>Loading bookings from PostgreSQL 18...</p>
        </div>
      )}

      {!loading && !error && bookings.length === 0 && (
        <div className="mgmt-empty-state">
          <h3>No appointments match your filters</h3>
          <p>
            Try adjusting your date range or filters, or create a new
            appointment.
          </p>
          <button
            type="button"
            className="mgmt-action-btn"
            onClick={handleOpenNewBooking}
            style={{ marginTop: "16px" }}
          >
            + New Appointment
          </button>
        </div>
      )}

      {!loading && !error && bookings.length > 0 && (
        <div className="mgmt-table-container">
          <table className="mgmt-table">
            <thead>
              <tr>
                <th scope="col">Time & Date</th>
                <th scope="col">Status</th>
                <th scope="col">Customer</th>
                <th scope="col">Service</th>
                <th scope="col">Staff</th>
                <th scope="col" style={{ textAlign: "right" }}>
                  Total
                </th>
                <th scope="col" style={{ textAlign: "right" }}>
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {bookings.map((b) => (
                <tr key={b.booking_id}>
                  <td>
                    <strong>{formatTime(b.starts_at)}</strong>
                    <div style={{ color: "var(--muted)", fontSize: "0.8rem" }}>
                      {formatDate(b.starts_at)}
                    </div>
                  </td>
                  <td>
                    <span
                      className={`mgmt-status-pill status-${b.status.toLowerCase()}`}
                    >
                      {b.status}
                    </span>
                  </td>
                  <td>
                    <strong>{b.customer_name || "Guest"}</strong>
                    <div style={{ color: "var(--muted)", fontSize: "0.8rem" }}>
                      {b.customer_email || b.customer_phone || "—"}
                    </div>
                  </td>
                  <td>
                    <span>{b.service_name}</span>
                    {b.variant_name && (
                      <span
                        style={{
                          color: "var(--muted)",
                          marginLeft: "4px",
                          fontSize: "0.8rem",
                        }}
                      >
                        ({b.variant_name})
                      </span>
                    )}
                  </td>
                  <td>{b.resource_name || "Unassigned"}</td>
                  <td style={{ textAlign: "right", fontWeight: 600 }}>
                    {formatPrice(b.total_cents, b.currency)}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <div style={{ display: "inline-flex", gap: "6px" }}>
                      <button
                        type="button"
                        className="secondary mgmt-btn-small"
                        onClick={() => setInspectBooking(b)}
                      >
                        Inspect
                      </button>
                      {b.status === "CONFIRMED" && (
                        <>
                          <button
                            type="button"
                            className="secondary mgmt-btn-small"
                            onClick={() => handleOpenReschedule(b)}
                          >
                            Reschedule
                          </button>
                          <button
                            type="button"
                            className="secondary mgmt-btn-small"
                            onClick={() => handleOpenCancel(b)}
                          >
                            Cancel
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Inspect Modal */}
      {inspectBooking && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="inspect-booking-heading"
        >
          <div className="mgmt-modal-card">
            <h2 id="inspect-booking-heading">Appointment Details</h2>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "140px 1fr",
                gap: "10px",
                margin: "20px 0",
                fontSize: "0.9rem",
              }}
            >
              <span style={{ color: "var(--muted)" }}>Booking ID:</span>
              <code>{inspectBooking.booking_id}</code>

              <span style={{ color: "var(--muted)" }}>Status:</span>
              <div>
                <span
                  className={`mgmt-status-pill status-${inspectBooking.status.toLowerCase()}`}
                >
                  {inspectBooking.status}
                </span>
              </div>

              <span style={{ color: "var(--muted)" }}>Scheduled Time:</span>
              <span>
                {formatDate(inspectBooking.starts_at)}{" "}
                {formatTime(inspectBooking.starts_at)} –{" "}
                {formatTime(inspectBooking.ends_at)}
              </span>

              <span style={{ color: "var(--muted)" }}>Service:</span>
              <span>
                {inspectBooking.service_name} ({inspectBooking.variant_name})
              </span>

              <span style={{ color: "var(--muted)" }}>Technician:</span>
              <span>{inspectBooking.resource_name}</span>

              <span style={{ color: "var(--muted)" }}>Client Name:</span>
              <span>{inspectBooking.customer_name || "Guest"}</span>

              <span style={{ color: "var(--muted)" }}>Client Email:</span>
              <span>{inspectBooking.customer_email || "—"}</span>

              <span style={{ color: "var(--muted)" }}>Client Phone:</span>
              <span>{inspectBooking.customer_phone || "—"}</span>

              <span style={{ color: "var(--muted)" }}>Total Price:</span>
              <span>
                {formatPrice(
                  inspectBooking.total_cents,
                  inspectBooking.currency,
                )}
              </span>

              <span style={{ color: "var(--muted)" }}>Created At:</span>
              <span>{formatDate(inspectBooking.created_at)}</span>

              <span style={{ color: "var(--muted)" }}>Created By:</span>
              <span>{inspectBooking.created_by}</span>
            </div>

            <div className="mgmt-form-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => setInspectBooking(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reschedule Modal */}
      {rescheduleTarget && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="reschedule-modal-heading"
        >
          <div className="mgmt-modal-card">
            <h2 id="reschedule-modal-heading">Reschedule Appointment</h2>
            <p
              style={{
                color: "var(--muted)",
                margin: "4px 0 16px",
                fontSize: "0.9rem",
              }}
            >
              Moving {rescheduleTarget.customer_name}&apos;s appointment for{" "}
              {rescheduleTarget.service_name}.
            </p>

            {rescheduleError && (
              <div className="mgmt-error-banner" style={{ margin: "16px 0" }}>
                <p style={{ margin: 0 }}>{rescheduleError}</p>
              </div>
            )}

            <form
              onSubmit={handleExecuteReschedule}
              className="mgmt-token-form"
            >
              <label htmlFor="reschedule-date">New Date</label>
              <input
                id="reschedule-date"
                type="date"
                value={rescheduleDate}
                onChange={(e) => setRescheduleDate(e.target.value)}
                required
                style={{ width: "100%", marginBottom: "16px" }}
              />

              <label htmlFor="reschedule-time">New Start Time</label>
              <input
                id="reschedule-time"
                type="time"
                value={rescheduleTime}
                onChange={(e) => setRescheduleTime(e.target.value)}
                required
                style={{ width: "100%", marginBottom: "16px" }}
              />

              <label htmlFor="reschedule-staff">Technician / Station</label>
              <select
                id="reschedule-staff"
                value={rescheduleStaffId}
                onChange={(e) => setRescheduleStaffId(e.target.value)}
                style={{ width: "100%", marginBottom: "20px" }}
              >
                {staffList.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.display_name}
                  </option>
                ))}
              </select>

              <div className="mgmt-form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setRescheduleTarget(null)}
                  disabled={isRescheduling}
                >
                  Cancel
                </button>
                <button type="submit" disabled={isRescheduling}>
                  {isRescheduling ? "Rescheduling..." : "Confirm Reschedule"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Cancel Modal */}
      {cancelTarget && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="cancel-modal-heading"
        >
          <div className="mgmt-modal-card">
            <h2 id="cancel-modal-heading">Cancel Appointment</h2>
            <p
              style={{
                color: "var(--muted)",
                margin: "4px 0 16px",
                fontSize: "0.9rem",
              }}
            >
              Are you sure you want to cancel the booking for{" "}
              {cancelTarget.customer_name} on{" "}
              {formatDate(cancelTarget.starts_at)}?
            </p>

            {cancelError && (
              <div className="mgmt-error-banner" style={{ margin: "16px 0" }}>
                <p style={{ margin: 0 }}>{cancelError}</p>
              </div>
            )}

            <form onSubmit={handleExecuteCancel} className="mgmt-token-form">
              <label htmlFor="cancel-reason">Reason for Cancellation</label>
              <textarea
                id="cancel-reason"
                value={cancelReason}
                onChange={(e) => setCancelReason(e.target.value)}
                required
                rows={3}
                style={{ width: "100%", marginBottom: "20px", padding: "10px" }}
              />

              <div className="mgmt-form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setCancelTarget(null)}
                  disabled={isCancelling}
                >
                  Keep Appointment
                </button>
                <button
                  type="submit"
                  style={{ background: "#c5221f", borderColor: "#c5221f" }}
                  disabled={isCancelling}
                >
                  {isCancelling ? "Cancelling..." : "Confirm Cancellation"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* New Booking Modal */}
      {showNewBookingModal && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="new-booking-heading"
        >
          <div className="mgmt-modal-card">
            <h2 id="new-booking-heading">Create Direct Appointment</h2>
            <p
              style={{
                color: "var(--muted)",
                margin: "4px 0 16px",
                fontSize: "0.9rem",
              }}
            >
              Book an appointment directly on behalf of a customer.
            </p>

            {newBookingError && (
              <div className="mgmt-error-banner" style={{ margin: "16px 0" }}>
                <p style={{ margin: 0 }}>{newBookingError}</p>
              </div>
            )}

            <form
              onSubmit={handleCreateBookingSubmit}
              className="mgmt-token-form"
            >
              <label htmlFor="nb-service">Service & Variant</label>
              <select
                id="nb-service"
                value={newVariantId}
                onChange={(e) => setNewVariantId(e.target.value)}
                required
                style={{ width: "100%", marginBottom: "16px" }}
              >
                {servicesList.map((srv) => (
                  <option key={srv.id} value={srv.id}>
                    {srv.name} — {srv.booking_duration_minutes || 60}m · $
                    {(srv.price_cents / 100).toFixed(2)}
                  </option>
                ))}
              </select>

              <label htmlFor="nb-staff">Technician / Station</label>
              <select
                id="nb-staff"
                value={newStaffId}
                onChange={(e) => setNewStaffId(e.target.value)}
                required
                style={{ width: "100%", marginBottom: "16px" }}
              >
                {staffList.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.display_name}
                  </option>
                ))}
              </select>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "12px",
                  marginBottom: "16px",
                }}
              >
                <div>
                  <label htmlFor="nb-date">Date</label>
                  <input
                    id="nb-date"
                    type="date"
                    value={newBookingDate}
                    onChange={(e) => setNewBookingDate(e.target.value)}
                    required
                    style={{ width: "100%" }}
                  />
                </div>
                <div>
                  <label htmlFor="nb-time">Start Time</label>
                  <input
                    id="nb-time"
                    type="time"
                    value={newBookingTime}
                    onChange={(e) => setNewBookingTime(e.target.value)}
                    required
                    style={{ width: "100%" }}
                  />
                </div>
              </div>

              <label htmlFor="nb-name">Customer Name</label>
              <input
                id="nb-name"
                type="text"
                value={newClientName}
                onChange={(e) => setNewClientName(e.target.value)}
                placeholder="e.g. Maria Johnson"
                required
                style={{ width: "100%", marginBottom: "16px" }}
              />

              <label htmlFor="nb-email">Customer Email</label>
              <input
                id="nb-email"
                type="email"
                value={newClientEmail}
                onChange={(e) => setNewClientEmail(e.target.value)}
                placeholder="maria@example.com"
                required
                style={{ width: "100%", marginBottom: "16px" }}
              />

              <label htmlFor="nb-phone">Customer Phone</label>
              <input
                id="nb-phone"
                type="tel"
                value={newClientPhone}
                onChange={(e) => setNewClientPhone(e.target.value)}
                placeholder="+1 555 123 4567"
                required
                style={{ width: "100%", marginBottom: "20px" }}
              />

              <div className="mgmt-form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setShowNewBookingModal(false)}
                  disabled={isCreating}
                >
                  Cancel
                </button>
                <button type="submit" disabled={isCreating}>
                  {isCreating ? "Confirming..." : "Create Confirmed Booking"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
