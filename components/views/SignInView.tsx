"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/state/auth";
import { Banner, ErrorBanner, Field } from "@/components/ui";

export default function SignInView() {
  const { signIn, signUp, signInWithGoogle, user, ready } = useAuth();
  const router = useRouter();

  // Covers both a fresh sign-in and arriving here with a valid token already.
  useEffect(() => {
    if (ready && user) router.replace("/");
  }, [ready, user, router]);

  const [mode, setMode] = useState<"in" | "up">("in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice("");
    try {
      if (mode === "in") await signIn(email, password);
      else {
        const result = await signUp(email, password, displayName, phone);
        if (result.requiresEmailConfirmation) {
          setNotice("Check your email to confirm your account, then return here to sign in.");
        }
      }
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  };

  const continueWithGoogle = async () => {
    setBusy(true);
    setError(null);
    setNotice("");
    try {
      await signInWithGoogle();
    } catch (cause) {
      setError(cause);
      setBusy(false);
    }
  };

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <h1>Trade Cognition</h1>
        <p className="auth-tagline">
          Pre-trade risk calculation and disciplined execution on MetaTrader 5.
        </p>

        <ErrorBanner error={error} />
        {notice && <Banner tone="ok">{notice}</Banner>}

        <button
          className="btn btn-google btn-block"
          type="button"
          onClick={continueWithGoogle}
          disabled={busy}
        >
          <span className="google-mark" aria-hidden="true">G</span>
          Continue with Google
        </button>

        <div className="auth-divider"><span>or continue with email</span></div>

        <form onSubmit={submit}>
          <Field label="Email">
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
            />
          </Field>

          {mode === "up" && (
            <>
              <Field label="Display name" hint="Optional">
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  autoComplete="name"
                />
              </Field>
              <Field label="Phone number" hint="Include your country code, e.g. +91">
                <input
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  autoComplete="tel"
                  inputMode="tel"
                  minLength={7}
                  maxLength={24}
                  placeholder="+91 98765 43210"
                  required
                />
              </Field>
            </>
          )}

          <Field
            label="Password"
            hint={mode === "up" ? "At least 10 characters." : undefined}
          >
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "up" ? "new-password" : "current-password"}
              minLength={mode === "up" ? 10 : undefined}
              required
            />
          </Field>

          <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
            {busy ? "Working..." : mode === "in" ? "Sign in" : "Create account"}
          </button>
        </form>

        <div className="auth-switch">
          {mode === "in" ? (
            <>
              No account yet?{" "}
              <button type="button" onClick={() => setMode("up")}>
                Create one
              </button>
            </>
          ) : (
            <>
              Already registered?{" "}
              <button type="button" onClick={() => setMode("in")}>
                Sign in
              </button>
            </>
          )}
        </div>

        <p className="tiny faint mt mb-0">
          This platform stores your MT5 password encrypted so it can place and manage orders on your
          behalf. Use a demo account until you trust the behaviour.
        </p>
      </div>
    </div>
  );
}
