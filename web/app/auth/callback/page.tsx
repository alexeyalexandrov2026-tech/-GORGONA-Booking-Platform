"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { completeSignIn } from "../../../lib/auth";

export default function Callback() {
  const router = useRouter();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    completeSignIn()
      .then(() => {
        if (active) router.replace("/overview/");
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [router]);
  return (
    <main id="main-content" className="page">
      <h1>
        {failed ? "We couldn’t complete sign-in." : "Completing sign-in…"}
      </h1>
      {failed ? (
        <>
          <p role="alert">
            The sign-in response expired or could not be verified. Start again
            from the dashboard.
          </p>
          <Link href="/overview/">Return to dashboard</Link>
        </>
      ) : (
        <p aria-live="polite">Verifying your account securely.</p>
      )}
    </main>
  );
}
