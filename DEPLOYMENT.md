# Deployment

## 1. Supabase

Run [`supabase/001_auth_profiles.sql`](supabase/001_auth_profiles.sql) and apply
the Auth URL settings in [`supabase/README.md`](supabase/README.md).

## 2. Python backend (Docker)

Set these values in the backend host's `.env`, then run `docker compose up -d
--build`:

```dotenv
TC_ENV=production
TC_SUPABASE_URL=https://fuobevtuecbzvqjralax.supabase.co
TC_SUPABASE_ANON_KEY=your-anon-or-publishable-key
TC_CREDENTIAL_ENCRYPTION_KEY=your-stable-fernet-key
TC_CORS_ORIGINS=https://YOUR-VERCEL-DOMAIN
```

Expose the backend through HTTPS. Keep one backend worker because the MT5
position monitor is process-wide. The supplied Linux image uses the mock MT5
gateway; live MetaTrader 5 needs the backend/terminal on a compatible Windows
host.

## 3. Frontend (Vercel)

Import the repository into Vercel as a Next.js project. Add these build-time
environment variables for Production and Preview as needed:

```dotenv
NEXT_PUBLIC_SUPABASE_URL=https://fuobevtuecbzvqjralax.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-or-publishable-key
NEXT_PUBLIC_API_URL=https://YOUR-BACKEND-DOMAIN/api
```

Redeploy after changing any `NEXT_PUBLIC_*` value. Add every Vercel domain that
will call the API to `TC_CORS_ORIGINS` and every OAuth callback URL to
Supabase's Redirect URLs allow list.
