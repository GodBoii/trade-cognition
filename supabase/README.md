# Supabase setup

1. Open **Supabase Dashboard -> SQL Editor** and run `001_auth_profiles.sql`.
2. In **Authentication -> Providers**, keep Email enabled and enable Google.
3. In **Authentication -> URL Configuration** set your Vercel production URL as
   the Site URL. Add these Redirect URLs:
   - `http://localhost:3000/auth/callback`
   - `https://YOUR-VERCEL-DOMAIN/auth/callback`
4. In Google Cloud, the authorized redirect URI remains Supabase's callback:
   `https://fuobevtuecbzvqjralax.supabase.co/auth/v1/callback`.

The `profiles.phone` value is captured during email signup. It is contact data,
not a verified phone-auth identity. Google accounts may leave it empty.

No service-role key is needed by this application. Never add it to a
`NEXT_PUBLIC_*` variable or to Vercel's frontend environment.
