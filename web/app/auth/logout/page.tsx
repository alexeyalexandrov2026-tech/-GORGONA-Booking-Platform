"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { completeSignOut } from "../../../lib/auth";
export default function Logout() {
  const [complete, setComplete] = useState(false);
  useEffect(() => {
    void completeSignOut().finally(() => setComplete(true));
  }, []);
  return (
    <main id="main-content" className="page">
      <h1>{complete ? "You’re signed out." : "Signing out…"}</h1>
      <Link href="/overview/">Return to dashboard</Link>
    </main>
  );
}
