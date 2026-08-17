"use client";

import { useEffect } from "react";

/**
 * Last-resort boundary. Recoverable failures (a rejected request, an
 * unreachable API) are handled in-page with `ErrorBanner`; this only catches
 * render errors, so it offers a retry rather than pretending to explain.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Unhandled render error:", error);
  }, [error]);

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <h1>Something broke</h1>
        <p className="auth-tagline">
          The interface hit an unexpected error. Your positions are unaffected: stops and
          take-profits live on the broker, not in this page.
        </p>
        <div className="banner banner-error">
          {error.message}
          {error.digest && <div className="tiny mono">digest {error.digest}</div>}
        </div>
        <button className="btn btn-primary btn-block" onClick={reset}>
          Try again
        </button>
      </div>
    </div>
  );
}
