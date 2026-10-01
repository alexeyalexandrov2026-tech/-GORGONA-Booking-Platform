"use client";

import React, { useEffect, useState, useCallback } from "react";
import { ManagementLayout } from "../../components/management-layout";
import {
  createStaff,
  fetchServices,
  fetchSettings,
  fetchStaff,
  fetchStaffSchedule,
  ResourceHours,
  ServiceView,
  StaffSchedule,
  StaffView,
  updateStaff,
  updateStaffSchedule,
  updateStaffServices,
} from "../../lib/management-api";

const WEEKDAYS = [
  { index: 0, label: "Monday" },
  { index: 1, label: "Tuesday" },
  { index: 2, label: "Wednesday" },
  { index: 3, label: "Thursday" },
  { index: 4, label: "Friday" },
  { index: 5, label: "Saturday" },
  { index: 6, label: "Sunday" },
];

function minuteToTime(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${h.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}`;
}

function timeToMinute(timeStr: string): number {
  const [h, m] = timeStr.split(":").map((v) => parseInt(v, 10));
  return (h || 0) * 60 + (m || 0);
}

export default function StaffPage() {
  return (
    <ManagementLayout>
      {({ salonId, refresh: triggerRefresh }) => (
        <StaffContent salonId={salonId} onRefresh={triggerRefresh} />
      )}
    </ManagementLayout>
  );
}

function StaffContent({
  salonId,
  onRefresh,
}: {
  salonId: string;
  onRefresh: () => void;
}) {
  const [staffList, setStaffList] = useState<StaffView[]>([]);
  const [servicesList, setServicesList] = useState<ServiceView[]>([]);
  const [defaultLocationId, setDefaultLocationId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // Add Staff Modal
  const [showAddModal, setShowAddModal] = useState(false);
  const [newName, setNewName] = useState("");
  const [newKind, setNewKind] = useState<"artist" | "chair" | "room">("artist");
  const [isSubmittingAdd, setIsSubmittingAdd] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // Edit Staff Modal
  const [editTarget, setEditTarget] = useState<StaffView | null>(null);
  const [editName, setEditName] = useState("");
  const [editActive, setEditActive] = useState(true);
  const [isSubmittingEdit, setIsSubmittingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // Schedule Modal
  const [scheduleTarget, setScheduleTarget] = useState<StaffView | null>(null);
  const [scheduleData, setScheduleData] = useState<{
    [key: number]: { enabled: boolean; open: string; close: string };
  }>({});
  const [loadingSchedule, setLoadingSchedule] = useState(false);
  const [isSubmittingSchedule, setIsSubmittingSchedule] = useState(false);
  const [scheduleError, setScheduleError] = useState<string | null>(null);

  // Services Assignment Modal
  const [servicesTarget, setServicesTarget] = useState<StaffView | null>(null);
  const [selectedServiceIds, setSelectedServiceIds] = useState<string[]>([]);
  const [loadingServicesTarget, setLoadingServicesTarget] = useState(false);
  const [isSubmittingServices, setIsSubmittingServices] = useState(false);
  const [servicesError, setServicesError] = useState<string | null>(null);

  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let isSubscribed = true;
    Promise.all([
      fetchStaff(salonId),
      fetchServices(salonId),
      fetchSettings(salonId).catch(() => null),
    ])
      .then(([staffData, srvData, settingsData]) => {
        if (!isSubscribed) return;
        setStaffList(staffData);
        setServicesList(srvData);
        const loc0 = settingsData?.locations?.[0];
        if (loc0) {
          setDefaultLocationId(loc0.id);
        } else if (staffData.length > 0 && staffData[0]) {
          setDefaultLocationId(staffData[0].location_id);
        }
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

  // Handle Add Staff
  const handleOpenAddModal = () => {
    setNewName("");
    setNewKind("artist");
    setAddError(null);
    setShowAddModal(true);
  };

  const handleCreateStaff = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newName.trim()) {
      setAddError("Staff name is required.");
      return;
    }
    if (!defaultLocationId) {
      setAddError(
        "No salon location found. Please configure a location in Settings first.",
      );
      return;
    }
    setIsSubmittingAdd(true);
    setAddError(null);

    try {
      await createStaff(salonId, {
        display_name: newName.trim(),
        location_id: defaultLocationId,
        kind: newKind,
      });
      setShowAddModal(false);
      setToastMessage(`Staff member "${newName.trim()}" added successfully.`);
      loadData();
      onRefresh();
    } catch (err: unknown) {
      setAddError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsSubmittingAdd(false);
    }
  };

  // Handle Edit Staff
  const handleOpenEdit = (s: StaffView) => {
    setEditTarget(s);
    setEditName(s.display_name);
    setEditActive(s.is_active);
    setEditError(null);
  };

  const handleUpdateStaff = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editTarget) return;
    setIsSubmittingEdit(true);
    setEditError(null);

    try {
      await updateStaff(salonId, editTarget.id, {
        display_name: editName.trim(),
        is_active: editActive,
      });
      setEditTarget(null);
      setToastMessage(`Staff member updated successfully.`);
      loadData();
      onRefresh();
    } catch (err: unknown) {
      setEditError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsSubmittingEdit(false);
    }
  };

  // Handle Schedule Modal
  const handleOpenSchedule = async (s: StaffView) => {
    setScheduleTarget(s);
    setScheduleError(null);
    setLoadingSchedule(true);

    try {
      const schedule: StaffSchedule = await fetchStaffSchedule(salonId, s.id);
      const initSchedule: {
        [key: number]: { enabled: boolean; open: string; close: string };
      } = {};

      WEEKDAYS.forEach((w) => {
        initSchedule[w.index] = {
          enabled: false,
          open: "09:00",
          close: "18:00",
        };
      });

      schedule.hours.forEach((h: ResourceHours) => {
        initSchedule[h.weekday] = {
          enabled: true,
          open: minuteToTime(h.opens_minute),
          close: minuteToTime(h.closes_minute),
        };
      });

      setScheduleData(initSchedule);
    } catch (err: unknown) {
      setScheduleError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingSchedule(false);
    }
  };

  const handleSaveSchedule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!scheduleTarget) return;
    setIsSubmittingSchedule(true);
    setScheduleError(null);

    const hoursPayload = WEEKDAYS.filter(
      (w) => scheduleData[w.index]?.enabled,
    ).map((w) => {
      const item = scheduleData[w.index] || {
        enabled: true,
        open: "09:00",
        close: "18:00",
      };
      return {
        weekday: w.index,
        opens_minute: timeToMinute(item.open),
        closes_minute: timeToMinute(item.close),
      };
    });

    try {
      await updateStaffSchedule(salonId, scheduleTarget.id, hoursPayload);
      setScheduleTarget(null);
      setToastMessage(`Schedule updated for ${scheduleTarget.display_name}.`);
    } catch (err: unknown) {
      setScheduleError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsSubmittingSchedule(false);
    }
  };

  // Handle Services Modal
  const handleOpenServices = async (s: StaffView) => {
    setServicesTarget(s);
    setServicesError(null);
    setLoadingServicesTarget(true);

    try {
      const schedule: StaffSchedule = await fetchStaffSchedule(salonId, s.id);
      setSelectedServiceIds(schedule.service_ids || []);
    } catch (err: unknown) {
      setServicesError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingServicesTarget(false);
    }
  };

  const toggleServiceSelection = (srvId: string) => {
    setSelectedServiceIds((prev) =>
      prev.includes(srvId)
        ? prev.filter((id) => id !== srvId)
        : [...prev, srvId],
    );
  };

  const handleSaveServices = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!servicesTarget) return;
    setIsSubmittingServices(true);
    setServicesError(null);

    try {
      await updateStaffServices(salonId, servicesTarget.id, selectedServiceIds);
      setServicesTarget(null);
      setToastMessage(
        `Assigned services updated for ${servicesTarget.display_name}.`,
      );
    } catch (err: unknown) {
      setServicesError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsSubmittingServices(false);
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
          <h2>Staff & Team Management</h2>
          <p
            style={{
              color: "var(--muted)",
              margin: "4px 0 0",
              fontSize: "0.9rem",
            }}
          >
            Manage service providers, chairs, rooms, weekly working schedules,
            and service assignments.
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
            onClick={handleOpenAddModal}
          >
            + Add Staff Member
          </button>
        </div>
      </div>

      {error && (
        <div className="mgmt-error-banner" role="alert">
          <h3>Error loading staff</h3>
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
          <p>Loading staff directory from database...</p>
        </div>
      )}

      {!loading && !error && staffList.length === 0 && (
        <div className="mgmt-empty-state">
          <h3>No staff members registered</h3>
          <p>
            Add your first technician, chair, or room to begin scheduling
            appointments.
          </p>
          <button
            type="button"
            className="mgmt-action-btn"
            onClick={handleOpenAddModal}
            style={{ marginTop: "16px" }}
          >
            + Add Staff Member
          </button>
        </div>
      )}

      {!loading && !error && staffList.length > 0 && (
        <div className="mgmt-table-container">
          <table className="mgmt-table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Resource Type</th>
                <th scope="col">Status</th>
                <th scope="col">Location ID</th>
                <th scope="col" style={{ textAlign: "right" }}>
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {staffList.map((member) => (
                <tr key={member.id}>
                  <td>
                    <strong>{member.display_name}</strong>
                  </td>
                  <td>
                    <span
                      style={{
                        background: "var(--ivory)",
                        padding: "2px 8px",
                        borderRadius: "4px",
                        border: "1px solid var(--line)",
                        fontSize: "0.8rem",
                        textTransform: "capitalize",
                      }}
                    >
                      {member.kind}
                    </span>
                  </td>
                  <td>
                    <span
                      className={`mgmt-status-pill ${
                        member.is_active ? "status-active" : "status-disabled"
                      }`}
                    >
                      {member.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
                    {member.location_id.slice(0, 8)}...
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <div style={{ display: "inline-flex", gap: "8px" }}>
                      <button
                        type="button"
                        className="secondary mgmt-btn-small"
                        onClick={() => handleOpenSchedule(member)}
                      >
                        Schedule
                      </button>
                      <button
                        type="button"
                        className="secondary mgmt-btn-small"
                        onClick={() => handleOpenServices(member)}
                      >
                        Services
                      </button>
                      <button
                        type="button"
                        className="secondary mgmt-btn-small"
                        onClick={() => handleOpenEdit(member)}
                      >
                        Edit
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Staff Modal */}
      {showAddModal && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="add-staff-heading"
        >
          <div className="mgmt-modal-card">
            <h2 id="add-staff-heading">Add Staff Member</h2>
            <p
              style={{
                color: "var(--muted)",
                marginTop: "4px",
                fontSize: "0.9rem",
              }}
            >
              Add a new technician, chair, or treatment room to the salon
              roster.
            </p>

            {addError && (
              <div className="mgmt-error-banner" style={{ margin: "16px 0" }}>
                <p style={{ margin: 0 }}>{addError}</p>
              </div>
            )}

            <form onSubmit={handleCreateStaff} className="mgmt-token-form">
              <label htmlFor="staff-name">Display Name</label>
              <input
                id="staff-name"
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Elena Rostova or Chair 3"
                required
                style={{ width: "100%", marginBottom: "16px" }}
              />

              <label htmlFor="staff-kind">Resource Kind</label>
              <select
                id="staff-kind"
                value={newKind}
                onChange={(e) =>
                  setNewKind(e.target.value as "artist" | "chair" | "room")
                }
                style={{ width: "100%", marginBottom: "20px" }}
              >
                <option value="artist">Artist / Technician (Person)</option>
                <option value="chair">Chair / Station</option>
                <option value="room">Private Treatment Room</option>
              </select>

              <div className="mgmt-form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setShowAddModal(false)}
                  disabled={isSubmittingAdd}
                >
                  Cancel
                </button>
                <button type="submit" disabled={isSubmittingAdd}>
                  {isSubmittingAdd ? "Adding..." : "Add Staff Member"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Staff Modal */}
      {editTarget && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="edit-staff-heading"
        >
          <div className="mgmt-modal-card">
            <h2 id="edit-staff-heading">Edit Staff Member</h2>
            <p
              style={{
                color: "var(--muted)",
                marginTop: "4px",
                fontSize: "0.9rem",
              }}
            >
              Update staff profile details and active booking status.
            </p>

            {editError && (
              <div className="mgmt-error-banner" style={{ margin: "16px 0" }}>
                <p style={{ margin: 0 }}>{editError}</p>
              </div>
            )}

            <form onSubmit={handleUpdateStaff} className="mgmt-token-form">
              <label htmlFor="edit-staff-name">Display Name</label>
              <input
                id="edit-staff-name"
                type="text"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                required
                style={{ width: "100%", marginBottom: "16px" }}
              />

              <div style={{ margin: "16px 0" }}>
                <label
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "10px",
                    cursor: "pointer",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={editActive}
                    onChange={(e) => setEditActive(e.target.checked)}
                    style={{ width: "auto" }}
                  />
                  <span>Active for bookings</span>
                </label>
                <p
                  style={{
                    color: "var(--muted)",
                    fontSize: "0.8rem",
                    margin: "4px 0 0 24px",
                  }}
                >
                  Inactive staff members are hidden from customer booking
                  availability.
                </p>
              </div>

              <div className="mgmt-form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setEditTarget(null)}
                  disabled={isSubmittingEdit}
                >
                  Cancel
                </button>
                <button type="submit" disabled={isSubmittingEdit}>
                  {isSubmittingEdit ? "Saving..." : "Save Changes"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Staff Weekly Schedule Modal */}
      {scheduleTarget && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="schedule-modal-heading"
        >
          <div className="mgmt-modal-card" style={{ maxWidth: "680px" }}>
            <h2 id="schedule-modal-heading">
              Weekly Hours: {scheduleTarget.display_name}
            </h2>
            <p
              style={{
                color: "var(--muted)",
                marginTop: "4px",
                fontSize: "0.9rem",
              }}
            >
              Define when this staff member is available for customer
              appointments.
            </p>

            {scheduleError && (
              <div className="mgmt-error-banner" style={{ margin: "16px 0" }}>
                <p style={{ margin: 0 }}>{scheduleError}</p>
              </div>
            )}

            {loadingSchedule ? (
              <div className="mgmt-loading">
                <p>Loading weekly schedule...</p>
              </div>
            ) : (
              <form onSubmit={handleSaveSchedule} style={{ marginTop: "20px" }}>
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "12px",
                  }}
                >
                  {WEEKDAYS.map((w) => {
                    const current = scheduleData[w.index] || {
                      enabled: false,
                      open: "09:00",
                      close: "18:00",
                    };
                    return (
                      <div
                        key={w.index}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          padding: "10px 14px",
                          background: current.enabled
                            ? "var(--ivory)"
                            : "#fafafa",
                          border: "1px solid var(--line)",
                          borderRadius: "4px",
                        }}
                      >
                        <div style={{ width: "140px" }}>
                          <label
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "8px",
                              cursor: "pointer",
                              margin: 0,
                              fontWeight: current.enabled ? 600 : 400,
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={current.enabled}
                              onChange={(e) => {
                                const checked = e.target.checked;
                                setScheduleData((prev) => {
                                  const cur = prev[w.index] || {
                                    enabled: false,
                                    open: "09:00",
                                    close: "18:00",
                                  };
                                  return {
                                    ...prev,
                                    [w.index]: {
                                      ...cur,
                                      enabled: checked,
                                    },
                                  };
                                });
                              }}
                              style={{ width: "auto" }}
                            />
                            {w.label}
                          </label>
                        </div>

                        {current.enabled ? (
                          <div
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "10px",
                            }}
                          >
                            <label
                              htmlFor={`open-${w.index}`}
                              style={{ margin: 0, fontSize: "0.85rem" }}
                            >
                              From:
                            </label>
                            <input
                              id={`open-${w.index}`}
                              type="time"
                              value={current.open}
                              onChange={(e) => {
                                const val = e.target.value;
                                setScheduleData((prev) => {
                                  const cur = prev[w.index] || {
                                    enabled: true,
                                    open: "09:00",
                                    close: "18:00",
                                  };
                                  return {
                                    ...prev,
                                    [w.index]: {
                                      ...cur,
                                      open: val,
                                    },
                                  };
                                });
                              }}
                              style={{ minHeight: "34px", padding: "4px 8px" }}
                            />
                            <label
                              htmlFor={`close-${w.index}`}
                              style={{ margin: 0, fontSize: "0.85rem" }}
                            >
                              To:
                            </label>
                            <input
                              id={`close-${w.index}`}
                              type="time"
                              value={current.close}
                              onChange={(e) => {
                                const val = e.target.value;
                                setScheduleData((prev) => {
                                  const cur = prev[w.index] || {
                                    enabled: true,
                                    open: "09:00",
                                    close: "18:00",
                                  };
                                  return {
                                    ...prev,
                                    [w.index]: {
                                      ...cur,
                                      close: val,
                                    },
                                  };
                                });
                              }}
                              style={{ minHeight: "34px", padding: "4px 8px" }}
                            />
                          </div>
                        ) : (
                          <span
                            style={{
                              color: "var(--muted)",
                              fontSize: "0.85rem",
                            }}
                          >
                            Off / Closed
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>

                <div
                  className="mgmt-form-actions"
                  style={{ marginTop: "24px" }}
                >
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setScheduleTarget(null)}
                    disabled={isSubmittingSchedule}
                  >
                    Cancel
                  </button>
                  <button type="submit" disabled={isSubmittingSchedule}>
                    {isSubmittingSchedule ? "Saving..." : "Save Working Hours"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {/* Staff Assigned Services Modal */}
      {servicesTarget && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="services-modal-heading"
        >
          <div className="mgmt-modal-card" style={{ maxWidth: "600px" }}>
            <h2 id="services-modal-heading">
              Assigned Services: {servicesTarget.display_name}
            </h2>
            <p
              style={{
                color: "var(--muted)",
                marginTop: "4px",
                fontSize: "0.9rem",
              }}
            >
              Select which salon treatments this technician or station is
              qualified to deliver.
            </p>

            {servicesError && (
              <div className="mgmt-error-banner" style={{ margin: "16px 0" }}>
                <p style={{ margin: 0 }}>{servicesError}</p>
              </div>
            )}

            {loadingServicesTarget ? (
              <div className="mgmt-loading">
                <p>Loading assigned services...</p>
              </div>
            ) : (
              <form onSubmit={handleSaveServices} style={{ marginTop: "20px" }}>
                {servicesList.length === 0 ? (
                  <p style={{ color: "var(--muted)" }}>
                    No services configured in catalog yet.
                  </p>
                ) : (
                  <div
                    style={{
                      maxHeight: "300px",
                      overflowY: "auto",
                      border: "1px solid var(--line)",
                      borderRadius: "4px",
                      padding: "12px",
                      display: "flex",
                      flexDirection: "column",
                      gap: "8px",
                    }}
                  >
                    {servicesList.map((srv) => {
                      const isChecked = selectedServiceIds.includes(
                        srv.service_id,
                      );
                      return (
                        <label
                          key={srv.id}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "10px",
                            padding: "8px 12px",
                            borderRadius: "4px",
                            background: isChecked
                              ? "var(--ivory)"
                              : "transparent",
                            cursor: "pointer",
                            margin: 0,
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() =>
                              toggleServiceSelection(srv.service_id)
                            }
                            style={{ width: "auto" }}
                          />
                          <div style={{ flex: 1 }}>
                            <strong style={{ fontSize: "0.9rem" }}>
                              {srv.name}
                            </strong>
                            <div
                              style={{
                                color: "var(--muted)",
                                fontSize: "0.8rem",
                              }}
                            >
                              {srv.booking_duration_minutes || 60} mins · $
                              {(srv.price_cents / 100).toFixed(2)}
                            </div>
                          </div>
                        </label>
                      );
                    })}
                  </div>
                )}

                <div
                  className="mgmt-form-actions"
                  style={{ marginTop: "24px" }}
                >
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setServicesTarget(null)}
                    disabled={isSubmittingServices}
                  >
                    Cancel
                  </button>
                  <button type="submit" disabled={isSubmittingServices}>
                    {isSubmittingServices
                      ? "Saving..."
                      : "Save Assigned Services"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
