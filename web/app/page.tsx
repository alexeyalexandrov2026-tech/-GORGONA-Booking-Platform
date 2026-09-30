import Link from "next/link";
export default function HomePage() {
  return (
    <main className="welcome">
      <p className="eyebrow">Your studio appointment</p>
      <h1>A little time for yourself.</h1>
      <Link className="button" href="/book/">
        Book an appointment
      </Link>
    </main>
  );
}
