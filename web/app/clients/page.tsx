"use client";

import React, { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { ManagementLayout } from "../../components/management-layout";
import {
  BookingSummary,
  ClientItem,
  fetchClientHistory,
  fetchClients,
  formatDate,
  formatPrice,
  formatTime,
} from "../../lib/management-api";

export default function ClientsPage() {
  return (
    <ManagementLayout>
      {({ salonId, refresh: triggerRefresh }) => (
        <ClientsContent salonId={salonId} onRefresh={triggerRefresh} />
      )}
    </ManagementLayout>
  );
}

function ClientsContent({
  salonId,
}: {
  salonId: string;
  onRefresh: () => void;
}) {
  const [clients, setClients] = useState<ClientItem[]>([]);
  const [searchTerm, setSearchTerm] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Client History Modal
  const [selectedClient, setSelectedClient] = useState<ClientItem | null>(null);
  const [historyBookings, setHistoryBookings] = useState<BookingSummary[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const [activeSearch, setActiveSearch] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let isSubscribed = true;
    fetchClients(salonId, activeSearch)
      .then((data) => {
        if (!isSubscribed) return;
        setClients(data);
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
  }, [salonId, activeSearch, refreshKey]);

  const loadClients = useCallback(() => {
    setLoading(true);
    setRefreshKey((k) => k + 1);
  }, []);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setActiveSearch(searchTerm);
  };

  const handleClearSearch = () => {
    setSearchTerm("");
    setLoading(true);
    setActiveSearch("");
  };

  const handleOpenHistory = async (client: ClientItem) => {
    setSelectedClient(client);
    setHistoryError(null);
    setLoadingHistory(true);
    setHistoryBookings([]);

    try {
      const data = await fetchClientHistory(salonId, {
        email: client.email || undefined,
        phone: client.phone || undefined,
      });
      setHistoryBookings(data);
    } catch (err: unknown) {
      setHistoryError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingHistory(false);
    }
  };

  return (
    <div>
      <div className="mgmt-section-header">
        <div>
          <h2>Clients Directory</h2>
          <p
            style={{
              color: "var(--muted)",
              margin: "4px 0 0",
              fontSize: "0.9rem",
            }}
          >
            Customer records aggregated from verified appointments and booking
            allocations.
          </p>
        </div>
        <div>
          <button
            type="button"
            className="secondary mgmt-action-btn"
            onClick={loadClients}
            disabled={loading}
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {/* Filter / Search Bar */}
      <form onSubmit={handleSearchSubmit} className="mgmt-filter-bar">
        <div
          className="mgmt-filter-group"
          style={{ flex: 1, minWidth: "240px" }}
        >
          <label htmlFor="client-search">Search:</label>
          <input
            id="client-search"
            type="search"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search by name, email, or phone..."
            style={{ width: "100%" }}
          />
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button type="submit" className="mgmt-btn-small" disabled={loading}>
            Search
          </button>
          {searchTerm && (
            <button
              type="button"
              className="secondary mgmt-btn-small"
              onClick={handleClearSearch}
            >
              Clear
            </button>
          )}
        </div>
      </form>

      {error && (
        <div className="mgmt-error-banner" role="alert">
          <h3>Error loading clients</h3>
          <p>{error}</p>
          <button
            type="button"
            className="secondary mgmt-btn-small"
            onClick={loadClients}
          >
            Retry
          </button>
        </div>
      )}

      {loading && !error && (
        <div className="mgmt-loading" aria-live="polite">
          <p>Loading client directory from database...</p>
        </div>
      )}

      {!loading && !error && clients.length === 0 && (
        <div className="mgmt-empty-state">
          <h3>No clients found</h3>
          <p>
            {searchTerm
              ? `No clients matched your search query "${searchTerm}".`
              : "As appointments are booked online or through the calendar, customers will automatically appear here."}
          </p>
          {searchTerm && (
            <button
              type="button"
              className="secondary mgmt-btn-small"
              onClick={handleClearSearch}
              style={{ marginTop: "12px" }}
            >
              Clear search filter
            </button>
          )}
        </div>
      )}

      {!loading && !error && clients.length > 0 && (
        <div className="mgmt-table-container">
          <table className="mgmt-table">
            <thead>
              <tr>
                <th scope="col">Customer Name</th>
                <th scope="col">Email</th>
                <th scope="col">Phone</th>
                <th scope="col" style={{ textAlign: "center" }}>
                  Total Bookings
                </th>
                <th scope="col" style={{ textAlign: "center" }}>
                  Confirmed
                </th>
                <th scope="col">Last Booking</th>
                <th scope="col" style={{ textAlign: "right" }}>
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {clients.map((client, idx) => (
                <tr key={`${client.email || client.phone || idx}`}>
                  <td>
                    <strong>{client.customer_name || "Guest Customer"}</strong>
                  </td>
                  <td>
                    {client.email ? (
                      <a
                        href={`mailto:${client.email}`}
                        style={{ color: "inherit" }}
                      >
                        {client.email}
                      </a>
                    ) : (
                      <span style={{ color: "var(--muted)" }}>—</span>
                    )}
                  </td>
                  <td>
                    {client.phone ? (
                      <a
                        href={`tel:${client.phone}`}
                        style={{ color: "inherit" }}
                      >
                        {client.phone}
                      </a>
                    ) : (
                      <span style={{ color: "var(--muted)" }}>—</span>
                    )}
                  </td>
                  <td style={{ textAlign: "center", fontWeight: 600 }}>
                    {client.total_bookings}
                  </td>
                  <td
                    style={{
                      textAlign: "center",
                      color: "#137333",
                      fontWeight: 600,
                    }}
                  >
                    {client.confirmed_bookings}
                  </td>
                  <td style={{ color: "var(--muted)", fontSize: "0.85rem" }}>
                    {client.last_booking_at
                      ? formatDate(client.last_booking_at)
                      : "—"}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      type="button"
                      className="secondary mgmt-btn-small"
                      onClick={() => handleOpenHistory(client)}
                    >
                      History ({client.total_bookings})
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Client History Modal */}
      {selectedClient && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="client-history-heading"
        >
          <div className="mgmt-modal-card" style={{ maxWidth: "720px" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
              }}
            >
              <div>
                <h2 id="client-history-heading" style={{ margin: 0 }}>
                  {selectedClient.customer_name || "Guest"}
                </h2>
                <p
                  style={{
                    color: "var(--muted)",
                    margin: "4px 0 0",
                    fontSize: "0.9rem",
                  }}
                >
                  {selectedClient.email || "No email"} ·{" "}
                  {selectedClient.phone || "No phone"}
                </p>
              </div>
              <Link
                href="/calendar/"
                className="button mgmt-btn-small"
                style={{ textDecoration: "none" }}
              >
                + New Booking
              </Link>
            </div>

            {historyError && (
              <div className="mgmt-error-banner" style={{ margin: "16px 0" }}>
                <p style={{ margin: 0 }}>{historyError}</p>
              </div>
            )}

            {loadingHistory ? (
              <div className="mgmt-loading" style={{ margin: "24px 0" }}>
                <p>Loading booking history...</p>
              </div>
            ) : historyBookings.length === 0 ? (
              <div
                className="mgmt-empty-state"
                style={{ margin: "24px 0", padding: "24px" }}
              >
                <p>No booking records found for this customer.</p>
              </div>
            ) : (
              <div style={{ marginTop: "20px" }}>
                <h3
                  style={{
                    fontSize: "1rem",
                    margin: "0 0 12px",
                    color: "var(--muted)",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                  }}
                >
                  Past & Upcoming Appointments ({historyBookings.length})
                </h3>
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "10px",
                    maxHeight: "400px",
                    overflowY: "auto",
                  }}
                >
                  {historyBookings.map((b) => (
                    <div
                      key={b.booking_id}
                      className={`mgmt-appointment-card ${
                        b.status === "cancelled" ? "cancelled" : ""
                      }`}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                        }}
                      >
                        <div>
                          <strong>{b.service_name || "Service"}</strong>
                          {b.variant_name && (
                            <span
                              style={{
                                color: "var(--muted)",
                                marginLeft: "6px",
                                fontSize: "0.85rem",
                              }}
                            >
                              ({b.variant_name})
                            </span>
                          )}
                        </div>
                        <span
                          className={`mgmt-status-pill status-${b.status.toLowerCase()}`}
                        >
                          {b.status}
                        </span>
                      </div>

                      <div
                        style={{
                          display: "flex",
                          flexWrap: "wrap",
                          gap: "16px",
                          marginTop: "8px",
                          fontSize: "0.85rem",
                          color: "var(--muted)",
                        }}
                      >
                        <span>
                          📅 {formatDate(b.starts_at)} {formatTime(b.starts_at)}{" "}
                          - {formatTime(b.ends_at)}
                        </span>
                        <span>
                          👤 Technician: {b.resource_name || "Unassigned"}
                        </span>
                        <span>💰 {formatPrice(b.total_cents, b.currency)}</span>
                      </div>

                      <div
                        style={{
                          marginTop: "6px",
                          fontSize: "0.75rem",
                          color: "var(--muted)",
                        }}
                      >
                        Reference: <code>{b.booking_id}</code> · Booked:{" "}
                        {formatDate(b.created_at)}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="mgmt-form-actions" style={{ marginTop: "24px" }}>
              <button
                type="button"
                className="secondary"
                onClick={() => setSelectedClient(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
