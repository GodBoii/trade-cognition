"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Sidebar } from "@/components/Sidebar";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/state/auth";

/**
 * Shell for every authenticated screen.
 *
 * Supabase authentication is the only application-level gate. MT5/worker
 * connectivity is feature state: an offline worker must never make the website
 * unusable or turn a valid Supabase session into a logged-out user.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  const { ready, user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (ready && !user) router.replace("/login");
  }, [ready, user, router]);

  if (!ready || !user) {
    return (
      <div className="auth-shell">
        <Spinner label={ready ? "redirecting to sign in" : "starting Trade Cognition"} />
      </div>
    );
  }

  return (
    <div className="app">
      <Sidebar />
      <main className="main">{children}</main>
    </div>
  );
}
