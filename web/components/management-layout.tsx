"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import React, { useEffect, useState, useCallback } from "react";
import {
  fetchMe,
  getActiveSalonId,
  getStaffToken,
  MeView,
  setActiveSalonId,
  setStaffToken,
} from "../lib/management-api";

interface ManagementLayoutProps {
  children: (props: {
    salonId: string;
    token: string | null;
    me: MeView | null;
    refresh: () => void;
  }) => React.ReactNode;
}

const NAV_ITEMS = [
  { href: "/overview/", label: "Overview" },
  { href: "/calendar/", label: "Calendar" },
  { href: "/bookings/", label: "Bookings" },
  { href: "/services/", label: "Services" },
  { href: "/staff/", label: "Staff" },
  { href: "/clients/", label: "Clients" },
  { href: "/settings/", label: "Settings" },
];

export function ManagementLayout({ children }: ManagementLayoutProps) {
  const pathname = usePathname();
  const [token, setToken] = useState<string | null>(() => getStaffToken());
  const [salonId, setSalonId] = useState<string | null>(() =>
    getActiveSalonId(),
  );
  const [me, setMe] = useState<MeView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tokenInput, setTokenInput] = useState("");
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    setTick((t) => t + 1);
  }, []);

  useEffect(() => {
    const storedToken = getStaffToken();
    const storedSalonId = getActiveSalonId();

    if (!storedToken) {
      Promise.resolve().then(() => setLoading(false));
      return;
    }

    fetchMe()
      .then((data) => {
        setToken(storedToken);
        setMe(data);
        if (data.memberships.length > 0) {
          const currentValid = data.memberships.some(
            (m) => m.salon_id === storedSalonId,
          );
          if (!currentValid || !storedSalonId) {
            const firstSalon = data.memberships[0]?.salon_id;
            if (firstSalon) {
              setSalonId(firstSalon);
              setActiveSalonId(firstSalon);
            }
          } else {
            setSalonId(storedSalonId);
          }
        }
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        setError(msg);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [tick]);

  const handleSaveToken = (e: React.FormEvent) => {
    e.preventDefault();
    if (!tokenInput.trim()) return;
    setStaffToken(tokenInput.trim());
    setToken(tokenInput.trim());
    setShowAuthModal(false);
    refresh();
  };

  const handleClearToken = () => {
    setStaffToken(null);
    setActiveSalonId(null);
    setToken(null);
    setSalonId(null);
    setMe(null);
    refresh();
  };

  const handleSalonChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newId = e.target.value;
    setSalonId(newId);
    setActiveSalonId(newId);
    refresh();
  };

  const currentMembership = me?.memberships.find((m) => m.salon_id === salonId);

  return (
    <div className="management-shell">
      <header className="mgmt-header">
        <div className="mgmt-brand-area">
          <Link href="/overview/" className="mgmt-brand">
            GORGONA <span>Studio Manager</span>
          </Link>
          {me && me.memberships.length > 1 ? (
            <select
              className="mgmt-salon-select"
              value={salonId || ""}
              onChange={handleSalonChange}
              aria-label="Active Salon"
            >
              {me.memberships.map((m) => (
                <option key={m.salon_id} value={m.salon_id}>
                  {m.salon_name || m.salon_id} ({m.role})
                </option>
              ))}
            </select>
          ) : currentMembership ? (
            <div className="mgmt-salon-badge">
              <strong>{currentMembership.salon_name || "Active Salon"}</strong>
              <span className="mgmt-role-tag">{currentMembership.role}</span>
            </div>
          ) : null}
        </div>

        <div className="mgmt-header-actions">
          <Link
            href="/book/"
            target="_blank"
            className="mgmt-action-btn secondary"
            title="Open customer booking wizard in a new tab"
          >
            Customer Booking ↗
          </Link>
          <button
            type="button"
            className="mgmt-action-btn secondary"
            onClick={() => setShowAuthModal(true)}
          >
            {token ? `Signed in (${me?.display_name || "Staff"})` : "Sign In"}
          </button>
        </div>
      </header>

      <nav className="mgmt-nav" aria-label="Management Navigation">
        <ul className="mgmt-nav-list">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href;
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className={`mgmt-nav-link ${isActive ? "active" : ""}`}
                  aria-current={isActive ? "page" : undefined}
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <main className="mgmt-main" id="main-content">
        {loading ? (
          <div className="mgmt-loading" aria-live="polite">
            <p>Loading salon management data...</p>
          </div>
        ) : !token ? (
          <section className="mgmt-auth-gate">
            <h2>Staff Authentication Required</h2>
            <p className="muted">
              Connect to the live GORGONA backend with your staff credentials to
              manage appointments, availability, and clients.
            </p>
            <form onSubmit={handleSaveToken} className="mgmt-token-form">
              <label htmlFor="staff-token-input">
                Staff Bearer Access Token:
                <input
                  id="staff-token-input"
                  type="text"
                  placeholder="Paste OIDC or test bearer token"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  required
                />
              </label>
              <div className="mgmt-form-actions">
                <button type="submit">Sign In to Dashboard</button>
              </div>
            </form>
          </section>
        ) : error ? (
          <div className="mgmt-error-banner" role="alert">
            <h3>Authentication Error</h3>
            <p>{error}</p>
            <div className="mgmt-form-actions">
              <button
                type="button"
                className="secondary"
                onClick={handleClearToken}
              >
                Clear Token
              </button>
              <button type="button" onClick={() => setShowAuthModal(true)}>
                Change Token
              </button>
            </div>
          </div>
        ) : !salonId ? (
          <div className="mgmt-empty-state">
            <h2>No Salons Found</h2>
            <p>Your user profile has no active salon memberships.</p>
          </div>
        ) : (
          children({ salonId, token, me, refresh })
        )}
      </main>

      {showAuthModal && (
        <div
          className="mgmt-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="auth-modal-title"
        >
          <div className="mgmt-modal-card">
            <h2 id="auth-modal-title">Staff Credentials</h2>
            <p className="muted">
              Signed in as: <strong>{me?.display_name || "Unverified"}</strong>
            </p>
            <form onSubmit={handleSaveToken}>
              <label htmlFor="auth-modal-token">
                Update Bearer Token:
                <input
                  id="auth-modal-token"
                  type="text"
                  placeholder="JWT token"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  required
                />
              </label>
              <div className="mgmt-form-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setShowAuthModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={handleClearToken}
                >
                  Sign Out
                </button>
                <button type="submit">Save & Reconnect</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
