"use client";
import { useEffect, useState, type FormEvent } from "react";
import { api, appointmentTime, errorMessage, money } from "../lib/api";
import {
  availabilitySchema,
  type Availability,
  type Booking,
  type Bootstrap,
  type Details,
  type Quote,
  type Selection,
  type Slot,
} from "../lib/contracts";

export function Summary({ quote }: { quote: Quote | null }) {
  return (
    <aside className="summary" aria-label="Price summary">
      <p className="eyebrow">Your moment</p>
      <h2>Appointment summary</h2>
      {quote ? (
        <>
          <ul>
            {quote.lines.map((line) => (
              <li key={line.id}>
                <span>{line.name}</span>
                <span>{money(line.price_cents, quote.currency)}</span>
              </li>
            ))}
          </ul>
          <div className="total">
            <span>Total</span>
            <strong>{money(quote.total_cents, quote.currency)}</strong>
          </div>
          <p>{quote.booking_duration_minutes} minutes</p>
        </>
      ) : (
        <p>Choose a service to see your studio’s price and duration.</p>
      )}
      <p className="fine">
        Times are subject to availability until your appointment is confirmed.
      </p>
    </aside>
  );
}

export function Times({
  selection,
  day,
  salon,
  slot,
  disabled,
  refresh,
  onSelect,
  onQuote,
}: {
  selection: Selection;
  day: string;
  salon: Bootstrap;
  slot: Slot | null;
  disabled: boolean;
  refresh: string;
  onSelect: (s: Slot) => void;
  onQuote: (q: Quote) => void;
}) {
  const [response, setResponse] = useState<{
    key: string;
    value: Availability | null;
    error: string;
  }>({ key: "", value: null, error: "" });
  const [attempt, setAttempt] = useState(0);
  const key = JSON.stringify({ ...selection, day });
  useEffect(() => {
    if (!day) return;
    const controller = new AbortController();
    api(
      "availability",
      availabilitySchema,
      JSON.parse(key) as unknown,
      undefined,
      controller.signal,
    )
      .then((value) => {
        if (!controller.signal.aborted) {
          setResponse({ key, value, error: "" });
          onQuote(value.quote);
        }
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted)
          setResponse({ key, value: null, error: errorMessage(e) });
      });
    return () => controller.abort();
  }, [key, attempt, refresh, day, onQuote]);
  if (!day) return <p role="status">Choose a date to see available times.</p>;
  if (response.key !== key)
    return (
      <p role="status" aria-live="polite">
        Finding available times…
      </p>
    );
  if (response.error)
    return (
      <div>
        <p role="alert" className="error">
          {response.error}
        </p>
        <button className="secondary" onClick={() => setAttempt((x) => x + 1)}>
          Retry availability
        </button>
      </div>
    );
  if (!response.value) return null;
  // Any Available is a server-issued artist/instant pair. Choose one per instant,
  // display that assignment, and still let the backend arbitrate the hold race.
  const unique = response.value.slots.filter(
    (s, i, all) => all.findIndex((x) => x.start_at === s.start_at) === i,
  );
  return (
    <fieldset className="times">
      <legend>Available times</legend>
      {unique.length ? (
        <div className="slot-grid">
          {unique.map((s) => (
            <button
              type="button"
              key={`${s.resource_id}:${s.start_at}`}
              className="slot"
              aria-pressed={
                slot?.start_at === s.start_at &&
                slot.resource_id === s.resource_id
              }
              disabled={disabled}
              onClick={() => onSelect(s)}
            >
              {appointmentTime(s.start_at, response.value!.timezone)}
              <small>
                {salon.artists.find((a) => a.id === s.resource_id)?.name}
              </small>
            </button>
          ))}
        </div>
      ) : (
        <p role="status">
          No times are available on this date. Try another day or artist.
        </p>
      )}
    </fieldset>
  );
}

export function HoldNotice({ remaining }: { remaining: number | null }) {
  return (
    <p className="hold-note">
      {remaining === 0
        ? "Your hold has expired. Choose a new time before confirming."
        : `Your time is held for ${Math.floor((remaining ?? 0) / 60)}:${String((remaining ?? 0) % 60).padStart(2, "0")}.`}
    </p>
  );
}
export function Appointment({
  booking,
  salon,
  timezone,
}: {
  booking: Booking;
  salon: Bootstrap;
  timezone: string;
}) {
  return (
    <div className="appointment">
      <p>
        {new Intl.DateTimeFormat("en", {
          timeZone: timezone,
          weekday: "long",
          month: "long",
          day: "numeric",
          year: "numeric",
        }).format(new Date(booking.start_at))}
      </p>
      <strong>{appointmentTime(booking.start_at, timezone)}</strong>
      <p>
        With {salon.artists.find((a) => a.id === booking.resource_id)?.name}
      </p>
      <p>
        {money(booking.quote.total_cents, booking.quote.currency)} ·{" "}
        {booking.quote.booking_duration_minutes} min
      </p>
    </div>
  );
}
export function DetailsForm({
  initial,
  cancellation,
  onSubmit,
}: {
  initial: Details | null;
  cancellation: string;
  onSubmit: (d: Details) => void;
}) {
  const [error, setError] = useState("");
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    const name = String(data.get("name") ?? "").trim(),
      email = String(data.get("email") ?? "").trim(),
      phone = String(data.get("phone") ?? "").trim();
    if (
      !name ||
      /[\x00-\x1f]/.test(name) ||
      !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ||
      !/^\+?[0-9 ()-]+$/.test(phone) ||
      (phone.match(/\d/g)?.length ?? 0) < 7 ||
      (phone.match(/\d/g)?.length ?? 0) > 15
    ) {
      setError("Please check your name, email and phone number.");
      return;
    }
    onSubmit({ name, email, phone, accept_policy: true });
  }
  return (
    <form onSubmit={submit}>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <label>
        Full name
        <input
          name="name"
          autoComplete="name"
          required
          maxLength={100}
          defaultValue={initial?.name}
        />
      </label>
      <label>
        Email
        <input
          name="email"
          type="email"
          autoComplete="email"
          required
          maxLength={254}
          defaultValue={initial?.email}
        />
      </label>
      <label>
        Phone
        <input
          name="phone"
          type="tel"
          autoComplete="tel"
          required
          minLength={7}
          maxLength={32}
          defaultValue={initial?.phone}
        />
      </label>
      <details className="policy">
        <summary>Cancellation policy</summary>
        <p>{cancellation}</p>
      </details>
      <label className="check">
        <input
          name="accept_policy"
          type="checkbox"
          required
          defaultChecked={initial?.accept_policy}
        />
        <span>I agree to the cancellation policy</span>
      </label>
      <button type="submit">Review appointment</button>
    </form>
  );
}
