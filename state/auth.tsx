"use client";

/**
 * Supabase session state only. MT5 connection/worker state belongs to the
 * separate TradingProvider and can never invalidate a signed-in user.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { Session } from "@supabase/supabase-js";

import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export interface AppUser {
  id: string;
  email: string;
  displayName: string;
  avatarUrl: string;
}

export interface SignUpResult {
  requiresEmailConfirmation: boolean;
}

interface AuthState {
  ready: boolean;
  user: AppUser | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (
    email: string,
    password: string,
    displayName: string,
    phone: string,
  ) => Promise<SignUpResult>;
  signInWithGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<AppUser | null>(null);
  const adoptSession = useCallback((session: Session | null) => {
    if (!session) {
      setUser(null);
      setReady(true);
      return;
    }

    const metadata = session.user.user_metadata ?? {};
    const email = session.user.email ?? "";
    setUser({
      id: session.user.id,
      email,
      displayName: String(
        metadata.full_name || metadata.name || metadata.user_name || email.split("@", 1)[0] || "Trader",
      ),
      avatarUrl: String(metadata.avatar_url || metadata.picture || ""),
    });
    setReady(true);
  }, []);

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    let active = true;

    void supabase.auth.getSession().then(({ data, error }) => {
      if (!active) return;
      if (error) {
        setReady(true);
        return;
      }
      adoptSession(data.session);
    });

    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      if (!active || event === "INITIAL_SESSION") return;
      // Defer work outside the auth callback so Supabase can release its lock.
      window.setTimeout(() => {
        if (active) adoptSession(session);
      }, 0);
    });

    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, [adoptSession]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      const { error } = await getSupabaseBrowserClient().auth.signInWithPassword({
        email: email.trim(),
        password,
      });
      if (error) throw error;
    },
    [],
  );

  const signUp = useCallback(
    async (email: string, password: string, displayName: string, phone: string) => {
      const { data, error } = await getSupabaseBrowserClient().auth.signUp({
        email: email.trim(),
        password,
        options: {
          emailRedirectTo: `${window.location.origin}/auth/callback`,
          data: {
            full_name: displayName.trim(),
            phone: phone.trim(),
          },
        },
      });
      if (error) throw error;
      return { requiresEmailConfirmation: !data.session };
    },
    [],
  );

  const signInWithGoogle = useCallback(async () => {
    const { error } = await getSupabaseBrowserClient().auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
      },
    });
    if (error) throw error;
  }, []);

  const signOut = useCallback(async () => {
    const { error } = await getSupabaseBrowserClient().auth.signOut();
    setUser(null);
    if (error) throw error;
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      ready,
      user,
      signIn,
      signUp,
      signInWithGoogle,
      signOut,
    }),
    [
      ready,
      user,
      signIn,
      signUp,
      signInWithGoogle,
      signOut,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
