"use client";

import React, { useEffect, useState, useCallback } from "react";
import { ManagementLayout } from "../../components/management-layout";
import {
  fetchSettings,
  formatDate,
  SalonSettings,
} from "../../lib/management-api";

const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

function formatMinute(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  const ampm = h >= 12 ? "PM" : "AM";
  const displayHour = h % 12 === 0 ? 12 : h % 12;
  return `${displayHour}:${m.toString().padStart(2, "0")} ${ampm}`;
}

export default function SettingsPage() {
  return (
    <ManagementLayout>
      {({ salonId, refresh: triggerRefresh }) => (
        <SettingsContent salonId={salonId} onRefresh={triggerRefresh} />
      )}
    </ManagementLayout>
  );
}

function SettingsContent({
  salonId,
}: {
  salonId: string;
  onRefresh: () => void;
}) {
  const [settings, setSettings] = useState<SalonSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let isSubscribed = true;
    fetchSettings(salonId)
      .then((data) => {
        if (!isSubscribed) return;
        setSettings(data);
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

  const loadSettings = useCallback(() => {
    setLoading(true);
    setRefreshKey((k) => k + 1);
  }, []);

  return (
    <div>
      <div className="mgmt-section-header">
        <div>
          <h2>Salon Profile & Settings</h2>
          <p
            style={{
              color: "var(--muted)",
              margin: "4px 0 0",
              fontSize: "0.9rem",
            }}
          >
            Operational policies, authorized embed origins, confirmed business
            facts, and salon metadata.
          </p>
        </div>
        <div>
          <button
            type="button"
            className="secondary mgmt-action-btn"
            onClick={loadSettings}
            disabled={loading}
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mgmt-error-banner" role="alert">
          <h3>Error loading salon settings</h3>
          <p>{error}</p>
          <button
            type="button"
            className="secondary mgmt-btn-small"
            onClick={loadSettings}
          >
            Retry
          </button>
        </div>
      )}

      {loading && !error && (
        <div className="mgmt-loading" aria-live="polite">
          <p>Loading salon configuration from database...</p>
        </div>
      )}

      {!loading && !error && settings && (
        <div style={{ display: "flex", flexDirection: "column", gap: "28px" }}>
          {/* Section 1: Business Identity */}
          <div className="mgmt-kpi-card" style={{ padding: "24px" }}>
            <h3 style={{ margin: "0 0 16px", fontSize: "1.1rem" }}>
              Salon Identity
            </h3>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                gap: "16px",
              }}
            >
              <div>
                <span className="mgmt-kpi-label">Business Name</span>
                <div style={{ fontSize: "1.2rem", fontWeight: 600 }}>
                  {settings.display_name}
                </div>
              </div>
              <div>
                <span className="mgmt-kpi-label">URL Slug</span>
                <div>
                  <code>{settings.slug}</code>
                </div>
              </div>
              <div>
                <span className="mgmt-kpi-label">Account Status</span>
                <div>
                  <span
                    className={`mgmt-status-pill status-${settings.status.toLowerCase()}`}
                  >
                    {settings.status}
                  </span>
                </div>
              </div>
              <div>
                <span className="mgmt-kpi-label">Booking Engine State</span>
                <div>
                  <span
                    className={`mgmt-status-pill status-${settings.booking_state.toLowerCase()}`}
                  >
                    {settings.booking_state}
                  </span>
                </div>
              </div>
            </div>

            <div
              style={{
                marginTop: "16px",
                paddingTop: "16px",
                borderTop: "1px solid var(--line)",
              }}
            >
              <span className="mgmt-kpi-label">Unique Salon Tenant ID</span>
              <div>
                <code style={{ fontSize: "0.85rem" }}>{settings.salon_id}</code>
              </div>
            </div>
          </div>

          {/* Section 2: Operating Locations */}
          <div>
            <h3 style={{ margin: "0 0 12px", fontSize: "1.1rem" }}>
              Physical Locations ({settings.locations.length})
            </h3>
            <div className="mgmt-table-container">
              <table className="mgmt-table">
                <thead>
                  <tr>
                    <th scope="col">Location Name</th>
                    <th scope="col">Timezone</th>
                    <th scope="col">Location ID</th>
                  </tr>
                </thead>
                <tbody>
                  {settings.locations.map((loc) => (
                    <tr key={loc.id}>
                      <td>
                        <strong>{loc.name}</strong>
                      </td>
                      <td>{loc.timezone}</td>
                      <td>
                        <code style={{ fontSize: "0.85rem" }}>{loc.id}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Section 3: Salon Business Hours */}
          <div>
            <h3 style={{ margin: "0 0 12px", fontSize: "1.1rem" }}>
              Salon Business Hours
            </h3>
            <div className="mgmt-table-container">
              <table className="mgmt-table">
                <thead>
                  <tr>
                    <th scope="col">Day of Week</th>
                    <th scope="col">Operating Hours</th>
                  </tr>
                </thead>
                <tbody>
                  {WEEKDAYS.map((dayName, idx) => {
                    const found = settings.business_hours.find(
                      (h) => h.weekday === idx,
                    );
                    return (
                      <tr key={dayName}>
                        <td style={{ fontWeight: 500 }}>{dayName}</td>
                        <td>
                          {found ? (
                            <span>
                              {formatMinute(found.opens_minute)} –{" "}
                              {formatMinute(found.closes_minute)}
                            </span>
                          ) : (
                            <span style={{ color: "var(--muted)" }}>
                              Closed
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Section 4: Authorized Embed Origins */}
          <div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "12px",
              }}
            >
              <h3 style={{ margin: 0, fontSize: "1.1rem" }}>
                Authorized Embed Origins ({settings.embed_origins.length})
              </h3>
            </div>
            <p
              style={{
                color: "var(--muted)",
                margin: "0 0 12px",
                fontSize: "0.85rem",
              }}
            >
              Origins permitted by CORS and CSP to embed this salon&apos;s
              hosted booking widget and make public booking API calls.
            </p>
            <div className="mgmt-table-container">
              <table className="mgmt-table">
                <thead>
                  <tr>
                    <th scope="col">Allowed Origin URL</th>
                    <th scope="col">Status</th>
                    <th scope="col">Last Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {settings.embed_origins.length === 0 ? (
                    <tr>
                      <td
                        colSpan={3}
                        style={{ textAlign: "center", color: "var(--muted)" }}
                      >
                        No external origins registered. Booking engine allows
                        direct domain access only.
                      </td>
                    </tr>
                  ) : (
                    settings.embed_origins.map((origin) => (
                      <tr key={origin.id}>
                        <td>
                          <strong>{origin.origin}</strong>
                        </td>
                        <td>
                          <span
                            className={`mgmt-status-pill status-${origin.status.toLowerCase()}`}
                          >
                            {origin.status}
                          </span>
                        </td>
                        <td
                          style={{ color: "var(--muted)", fontSize: "0.85rem" }}
                        >
                          {formatDate(origin.updated_at)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Section 5: Confirmed Business Facts */}
          <div>
            <h3 style={{ margin: "0 0 12px", fontSize: "1.1rem" }}>
              Verified Business Fact Confirmations (
              {settings.fact_confirmations.length})
            </h3>
            <p
              style={{
                color: "var(--muted)",
                margin: "0 0 12px",
                fontSize: "0.85rem",
              }}
            >
              Audit confirmations of tenant business licenses, pricing parity,
              brand assets, and operating policies.
            </p>
            <div className="mgmt-table-container">
              <table className="mgmt-table">
                <thead>
                  <tr>
                    <th scope="col">Fact Key</th>
                    <th scope="col">Status</th>
                    <th scope="col">Source Note</th>
                    <th scope="col">Recorded By</th>
                    <th scope="col">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {settings.fact_confirmations.length === 0 ? (
                    <tr>
                      <td
                        colSpan={5}
                        style={{ textAlign: "center", color: "var(--muted)" }}
                      >
                        No business fact confirmations recorded yet.
                      </td>
                    </tr>
                  ) : (
                    settings.fact_confirmations.map((fact) => (
                      <tr key={fact.fact_key}>
                        <td>
                          <code>{fact.fact_key}</code>
                        </td>
                        <td>
                          <span
                            className={`mgmt-status-pill ${
                              fact.status === "confirmed"
                                ? "status-confirmed"
                                : "status-draft"
                            }`}
                          >
                            {fact.status}
                          </span>
                        </td>
                        <td style={{ fontSize: "0.85rem" }}>
                          {fact.source_note || "—"}
                        </td>
                        <td
                          style={{ color: "var(--muted)", fontSize: "0.85rem" }}
                        >
                          {fact.recorded_by}
                        </td>
                        <td
                          style={{ color: "var(--muted)", fontSize: "0.85rem" }}
                        >
                          {formatDate(fact.recorded_at)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Section 6: Salon Policies */}
          <div>
            <h3 style={{ margin: "0 0 12px", fontSize: "1.1rem" }}>
              Booking & Cancellation Policies
            </h3>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                gap: "16px",
              }}
            >
              <div className="mgmt-kpi-card">
                <span className="mgmt-kpi-label">Cancellation Policy</span>
                <pre
                  style={{
                    background: "var(--ivory)",
                    padding: "12px",
                    borderRadius: "4px",
                    fontSize: "0.8rem",
                    overflowX: "auto",
                    margin: "8px 0 0",
                  }}
                >
                  {JSON.stringify(
                    settings.policies.cancellation_policy || {
                      rules: "Standard 24h notice",
                    },
                    null,
                    2,
                  )}
                </pre>
              </div>

              <div className="mgmt-kpi-card">
                <span className="mgmt-kpi-label">Deposit Policy</span>
                <pre
                  style={{
                    background: "var(--ivory)",
                    padding: "12px",
                    borderRadius: "4px",
                    fontSize: "0.8rem",
                    overflowX: "auto",
                    margin: "8px 0 0",
                  }}
                >
                  {JSON.stringify(
                    settings.policies.deposit_policy || { required: false },
                    null,
                    2,
                  )}
                </pre>
              </div>

              <div className="mgmt-kpi-card">
                <span className="mgmt-kpi-label">Booking Rules</span>
                <pre
                  style={{
                    background: "var(--ivory)",
                    padding: "12px",
                    borderRadius: "4px",
                    fontSize: "0.8rem",
                    overflowX: "auto",
                    margin: "8px 0 0",
                  }}
                >
                  {JSON.stringify(
                    settings.policies.booking_rules || { min_advance_hours: 1 },
                    null,
                    2,
                  )}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
