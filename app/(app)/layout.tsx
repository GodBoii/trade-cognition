"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import ConnectAccountView from "@/components/views/ConnectAccountView";
import { Sidebar } from "@/components/Sidebar";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/state/auth";

/**
 * Shell for every authenticated screen.
 *
 * Two gates before any page renders:
 *  1. no session -> redirect to /login;
 *  2. no MT5 account -> the connection screen replaces the app, because without
 *     a broker connection every other page has nothing to show.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  const { ready, user, accounts } = useAuth();
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

  if (accounts.length === 0) {
    return (
      <div className="app">
        <Sidebar minimal />
        <main className="main">
          <ConnectAccountView />
        </main>
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
