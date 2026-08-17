"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ErrorBanner, Spinner } from "@/components/ui";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export default function AuthCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let active = true;

    const finishSignIn = async () => {
      const params = new URLSearchParams(window.location.search);
      const providerError = params.get("error_description") ?? params.get("error");
      if (providerError) throw new Error(providerError);

      const code = params.get("code");
      if (code) {
        const { error: exchangeError } =
          await getSupabaseBrowserClient().auth.exchangeCodeForSession(code);
        if (exchangeError) throw exchangeError;
      }

      const { data, error: sessionError } =
        await getSupabaseBrowserClient().auth.getSession();
      if (sessionError) throw sessionError;
      if (!data.session) throw new Error("No session was returned. Please try signing in again.");
      if (active) router.replace("/");
    };

    void finishSignIn().catch((cause) => {
      if (active) setError(cause);
    });

    return () => {
      active = false;
    };
  }, [router]);

  return (
    <div className="auth-shell">
      <div className="auth-card auth-callback">
        <h1>Trade Cognition</h1>
        <ErrorBanner error={error} />
        {!error && <Spinner label="finishing secure sign in" />}
        {Boolean(error) && (
          <button className="btn btn-primary btn-block" onClick={() => router.replace("/login")}>
            Back to sign in
          </button>
        )}
      </div>
    </div>
  );
}
