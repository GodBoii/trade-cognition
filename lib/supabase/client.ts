"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let browserClient: SupabaseClient | null = null;

/**
 * One browser-side Supabase client owns session persistence and token refresh.
 * Both values are deliberately public: Supabase protects data with Auth + RLS,
 * while privileged service-role credentials remain backend-only.
 */
export function getSupabaseBrowserClient(): SupabaseClient {
  if (browserClient) return browserClient;

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();

  if (!url || !anonKey) {
    throw new Error(
      "Supabase is not configured. Set NEXT_PUBLIC_SUPABASE_URL and " +
        "NEXT_PUBLIC_SUPABASE_ANON_KEY before building the frontend.",
    );
  }

  browserClient = createClient(url, anonKey, {
    auth: {
      flowType: "pkce",
      persistSession: true,
      autoRefreshToken: true,
      // A dedicated callback page performs the PKCE code exchange exactly once.
      detectSessionInUrl: false,
    },
  });
  return browserClient;
}
