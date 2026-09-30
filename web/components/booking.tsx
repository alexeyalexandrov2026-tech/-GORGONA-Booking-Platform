"use client";
import { useEffect, useState } from "react";
import { api, ApiError, errorMessage } from "../lib/api";
import { bootstrapSchema, type Bootstrap } from "../lib/contracts";
import { Journey } from "./journey";

export function BookingEntry() {
  const [result, setResult] = useState<Bootstrap | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api("bootstrap", bootstrapSchema, undefined, undefined, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          setResult(data);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted)
          setError(
            e instanceof ApiError
              ? e
              : new ApiError("UNKNOWN", errorMessage(e)),
          );
      });
    return () => controller.abort();
  }, [attempt]);
  if (error)
    return (
      <main className="welcome">
        <p className="eyebrow">Studio appointments</p>
        <h1>
          {error.code === "TENANT_NOT_FOUND"
            ? "Online booking is not available yet."
            : "We can’t arrange an appointment right now."}
        </h1>
        <p>
          {error.code === "TENANT_NOT_FOUND"
            ? "Please check back or contact the studio directly."
            : error.message}
        </p>
        <button
          onClick={() => {
            setError(null);
            setAttempt((x) => x + 1);
          }}
        >
          Try again
        </button>
      </main>
    );
  if (!result)
    return (
      <main className="welcome" aria-busy="true">
        <p role="status">Loading the studio…</p>
      </main>
    );
  if (!result.variants.length || !result.locations.length)
    return (
      <main className="welcome">
        <h1>Appointments are coming soon.</h1>
        <p>The studio’s bookable services are still being prepared.</p>
      </main>
    );
  return <Journey salon={result} />;
}
