"use client";

/**
 * Session state: the signed-in user, the connected MT5 accounts and the
 * currently selected account. Every page reads from here rather than fetching
 * the account list itself.
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

import { ApiRequestError, api, setToken } from "@/lib/api/client";
import type { Mt5Account, User } from "@/lib/api/types";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export interface SignUpResult {
  requiresEmailConfirmation: boolean;
}

interface AuthState {
  ready: boolean;
  user: User | null;
  accounts: Mt5Account[];
  accountId: number | null;
  account: Mt5Account | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (
    email: string,
    password: string,
    displayName: string,
    phone: string,
  ) => Promise<SignUpResult>;
  signInWithGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
  selectAccount: (id: number) => void;
  refreshAccounts: () => Promise<Mt5Account[]>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [accounts, setAccounts] = useState<Mt5Account[]>([]);
  const [accountId, setAccountId] = useState<number | null>(null);

  const refreshAccounts = useCallback(async () => {
    const list = await api.listAccounts();
    setAccounts(list);
    setAccountId((current) => {
      if (current && list.some((a) => a.id === current)) return current;
      const preferred = list.find((a) => a.is_default) ?? list[0];
      return preferred ? preferred.id : null;
    });
    return list;
  }, []);

  const adoptSession = useCallback(async (session: Session | null) => {
    if (!session) {
      setToken(null);
      setUser(null);
      setAccounts([]);
      setAccountId(null);
      setReady(true);
      return;
    }

    setToken(session.access_token);
    try {
      setUser(await api.me());
      await refreshAccounts();
    } catch (error) {
      if (error instanceof ApiRequestError && error.isAuthFailure) {
        setToken(null);
        setUser(null);
        setAccounts([]);
        setAccountId(null);
      }
      throw error;
    } finally {
      setReady(true);
    }
  }, [refreshAccounts]);

  useEffect(() => {
    const supabase = getSupabaseBrowserClient();
    let active = true;

    void supabase.auth.getSession().then(({ data, error }) => {
      if (!active) return;
      if (error) {
        setReady(true);
        return;
      }
      void adoptSession(data.session).catch(() => undefined);
    });

    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      if (!active || event === "INITIAL_SESSION") return;
      // Defer work outside the auth callback so Supabase can release its lock.
      window.setTimeout(() => {
        if (active) void adoptSession(session).catch(() => undefined);
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
    setToken(null);
    setUser(null);
    setAccounts([]);
    setAccountId(null);
    if (error) throw error;
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      ready,
      user,
      accounts,
      accountId,
      account: accounts.find((a) => a.id === accountId) ?? null,
      signIn,
      signUp,
      signInWithGoogle,
      signOut,
      selectAccount: setAccountId,
      refreshAccounts,
    }),
    [
      ready,
      user,
      accounts,
      accountId,
      signIn,
      signUp,
      signInWithGoogle,
      signOut,
      refreshAccounts,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
