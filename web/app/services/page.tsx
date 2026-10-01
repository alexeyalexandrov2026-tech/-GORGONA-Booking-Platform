"use client";

import React, { useEffect, useState, useCallback } from "react";
import { ManagementLayout } from "../../components/management-layout";
import {
  createService,
  fetchServices,
  formatPrice,
  publishService,
  ServiceView,
  unpublishService,
  updateService,
} from "../../lib/management-api";

export default function ServicesPage() {
  return (
    <ManagementLayout>
      {({ salonId, refresh: triggerRefresh }) => (
        <ServicesContent salonId={salonId} onRefresh={triggerRefresh} />
      )}
    </ManagementLayout>
  );
}

function ServicesContent({
  salonId,
  onRefresh,
}: {
  salonId: string;
  onRefresh: () => void;
}) {
  const [services, setServices] = useState<ServiceView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  // New Service Modal
  const [showAddModal, setShowAddModal] = useState(false);
  const [serviceCode, setServiceCode] = useState("");
  const [serviceName, setServiceName] = useState("");
  const [variantCode, setVariantCode] = useState("");
  const [variantName, setVariantName] = useState("");
  const [priceDollars, setPriceDollars] = useState("45");
  const [durationMinutes, setDurationMinutes] = useState("60");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // Edit Service Modal
  const [editTarget, setEditTarget] = useState<ServiceView | null>(null);
  const [editName, setEditName] = useState("");
  const [editPriceDollars, setEditPriceDollars] = useState("");
  const [editDuration, setEditDuration] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let isSubscribed = true;
    fetchServices(salonId)
      .then((data) => {
        if (!isSubscribed) return;
        setServices(data);
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

  const loadServices = useCallback(() => {
    setLoading(true);
    setRefreshKey((k) => k + 1);
  }, []);

  const handleOpenAddModal = () => {
    setServiceCode("");
    setServiceName("");
    setVariantCode("");
    setVariantName("");
    setPriceDollars("45");
    setDurationMinutes("60");
    setAddError(null);
    setShowAddModal(true);
  };

  const handleCreateService = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setAddError(null);

    const priceCents = Math.round(parseFloat(priceDollars) * 100);
    const duration = parseInt(durationMinutes, 10);

    try {
      await createService(salonId, {
        service_code: serviceCode.trim().toUpperCase(),
        service_name: serviceName.trim(),
        code: variantCode.trim().toUpperCase(),
        name: variantName.trim(),
        price_cents: priceCents,
        currency: "USD",
        booking_duration_minutes: duration,
      });
      setShowAddModal(false);
      setToastMessage("Service created successfully!");
      loadServices();
      onRefresh();
    } catch (err: unknown) {
      setAddError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleOpenEdit = (s: ServiceView) => {
    setEditTarget(s);
    setEditName(s.name);
    setEditPriceDollars((s.price_cents / 100).toFixed(2));
    setEditDuration(String(s.booking_duration_minutes || 60));
    setEditError(null);
  };

  const handleExecuteEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editTarget) return;
    setIsEditing(true);
    setEditError(null);

    const priceCents = Math.round(parseFloat(editPriceDollars) * 100);
    const duration = parseInt(editDuration, 10);

    try {
      await updateService(salonId, editTarget.id, {
        name: editName.trim(),
        price_cents: priceCents,
        booking_duration_minutes: duration,
      });
      setEditTarget(null);
      setToastMessage("Service updated successfully!");
      loadServices();
      onRefresh();
    } catch (err: unknown) {
      setEditError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsEditing(false);
    }
  };

  const handleTogglePublish = async (s: ServiceView) => {
    try {
      if (s.is_bookable) {
        await unpublishService(salonId, s.id);
        setToastMessage(`Service "${s.name}" unpublished.`);
      } else {
        await publishService(salonId, s.id);
        setToastMessage(`Service "${s.name}" is now live and bookable!`);
      }
      loadServices();
      onRefresh();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div>
      {toastMessage && (
        <div className="hold-note" style={{ marginBottom: "20px" }}>
          <strong>Notice:</strong> {toastMessage}
        </div>
      )}

      <div className="mgmt-section-header">
        <div>
          <h2>Service Catalog</h2>
          <p className="muted">
            Manage bookable services, pricing, durations, and status
          </p>
        </div>
        <button type="button" onClick={handleOpenAddModal}>
          + Add New Service
        </button>
      </div>

      {loading ? (
        <p className="muted">Loading service catalog...</p>
      ) : error ? (
        <div className="mgmt-error-banner" role="alert">
          <h3>Error loading services</h3>
          <p>{error}</p>
          <button type="button" onClick={loadServices}>
            Retry
          </button>
        </div>
      ) : services.length === 0 ? (
        <div className="mgmt-empty-state">
          <h3>No Services Found</h3>
          <p className="muted">
            Get started by defining your first service variant.
          </p>
          <button type="button" onClick={handleOpenAddModal}>
            Add Service
          </button>
        </div>
      ) : (
        <div className="mgmt-table-container">
          <table className="mgmt-table" aria-label="Services Catalog">
            <thead>
              <tr>
                <th scope="col">Code</th>
                <th scope="col">Name</th>
                <th scope="col">Price</th>
                <th scope="col">Duration</th>
                <th scope="col">Status</th>
                <th scope="col">Publicly Bookable</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {services.map((s) => (
                <tr key={s.id}>
                  <td>
                    <code>{s.code}</code>
                  </td>
                  <td>
                    <strong>{s.name}</strong>
                  </td>
                  <td>{formatPrice(s.price_cents, s.currency)}</td>
                  <td>
                    {s.booking_duration_minutes
                      ? `${s.booking_duration_minutes} min`
                      : "Not set"}
                  </td>
                  <td>
                    <span className={`mgmt-status-pill status-${s.status}`}>
                      {s.status}
                    </span>
                  </td>
                  <td>
                    {s.is_bookable ? (
                      <span className="mgmt-status-pill status-published">
                        Bookable
                      </span>
                    ) : (
                      <span className="mgmt-status-pill status-draft">
                        Draft
                      </span>
                    )}
                  </td>
                  <td>
                    <div style={{ display: "flex", gap: "8px" }}>
                      <button
                        type="button"
                        className="secondary mgmt-btn-small"
                        onClick={() => handleOpenEdit(s)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="secondary mgmt-btn-small"
                        onClick={() => handleTogglePublish(s)}
                      >
                        {s.is_bookable ? "Unpublish" : "Publish"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Modal */}
      {showAddModal && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="add-service-title"
        >
          <div className="mgmt-modal-card">
            <h2 id="add-service-title">Add Service Variant</h2>
            <p className="muted">
              Create a new service family and bookable variant.
            </p>

            {addError && (
              <div className="error" style={{ marginBottom: "16px" }}>
                {addError}
              </div>
            )}

            <form onSubmit={handleCreateService}>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "16px",
                }}
              >
                <label htmlFor="service-code">
                  Family Code (e.g. NAILS_GEL):
                  <input
                    id="service-code"
                    type="text"
                    placeholder="CATEGORY_CODE"
                    value={serviceCode}
                    onChange={(e) =>
                      setServiceCode(e.target.value.toUpperCase())
                    }
                    required
                  />
                </label>
                <label htmlFor="service-name">
                  Family Name:
                  <input
                    id="service-name"
                    type="text"
                    placeholder="Gel Manicure Family"
                    value={serviceName}
                    onChange={(e) => setServiceName(e.target.value)}
                    required
                  />
                </label>
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "16px",
                }}
              >
                <label htmlFor="variant-code">
                  Variant Code:
                  <input
                    id="variant-code"
                    type="text"
                    placeholder="GEL_LUXURY"
                    value={variantCode}
                    onChange={(e) =>
                      setVariantCode(e.target.value.toUpperCase())
                    }
                    required
                  />
                </label>
                <label htmlFor="variant-name">
                  Variant Name:
                  <input
                    id="variant-name"
                    type="text"
                    placeholder="Gel Luxury Care"
                    value={variantName}
                    onChange={(e) => setVariantName(e.target.value)}
                    required
                  />
                </label>
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "16px",
                }}
              >
                <label htmlFor="service-price">
                  Price (USD):
                  <input
                    id="service-price"
                    type="number"
                    step="0.01"
                    min="0"
                    value={priceDollars}
                    onChange={(e) => setPriceDollars(e.target.value)}
                    required
                  />
                </label>
                <label htmlFor="service-duration">
                  Duration (Minutes):
                  <input
                    id="service-duration"
                    type="number"
                    min="1"
                    max="720"
                    value={durationMinutes}
                    onChange={(e) => setDurationMinutes(e.target.value)}
                    required
                  />
                </label>
              </div>

              <div className="mgmt-form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setShowAddModal(false)}
                  disabled={isSubmitting}
                >
                  Cancel
                </button>
                <button type="submit" disabled={isSubmitting}>
                  {isSubmitting ? "Creating..." : "Save Service"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {editTarget && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="edit-service-title"
        >
          <div className="mgmt-modal-card">
            <h2 id="edit-service-title">Edit Service Variant</h2>
            <p className="muted">
              Updating <code>{editTarget.code}</code>
            </p>

            {editError && (
              <div className="error" style={{ marginBottom: "16px" }}>
                {editError}
              </div>
            )}

            <form onSubmit={handleExecuteEdit}>
              <label htmlFor="edit-variant-name">
                Variant Name:
                <input
                  id="edit-variant-name"
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
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
                <label htmlFor="edit-variant-price">
                  Price (USD):
                  <input
                    id="edit-variant-price"
                    type="number"
                    step="0.01"
                    min="0"
                    value={editPriceDollars}
                    onChange={(e) => setEditPriceDollars(e.target.value)}
                    required
                  />
                </label>
                <label htmlFor="edit-variant-duration">
                  Duration (Minutes):
                  <input
                    id="edit-variant-duration"
                    type="number"
                    min="1"
                    max="720"
                    value={editDuration}
                    onChange={(e) => setEditDuration(e.target.value)}
                    required
                  />
                </label>
              </div>

              <div className="mgmt-form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setEditTarget(null)}
                  disabled={isEditing}
                >
                  Cancel
                </button>
                <button type="submit" disabled={isEditing}>
                  {isEditing ? "Saving..." : "Update Service"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
