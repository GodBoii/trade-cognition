# Supabase setup

1. Open **Supabase Dashboard -> SQL Editor** and run the complete
   `000_RUN_THIS_IN_SUPABASE.sql` file. It creates `public.profiles`, installs
   the Auth synchronization trigger and RLS policies, and backfills users who
   already signed up. `001_auth_profiles.sql` is retained as the original
   migration for existing installations.
2. Run `002_async_trade_queue.sql`. It creates the browser/worker control plane,
   including rules, non-secret MT5 connection metadata, expiring trade intents,
   the durable queue, scoped worker credentials, audit events, RLS, and RPCs.
   Read `003_ASYNC_QUEUE_SETUP.md` for the pairing and worker flow.
3. In **Authentication -> Providers**, keep Email enabled and enable Google.
4. In **Authentication -> URL Configuration** set your Vercel production URL as
   the Site URL. Add these Redirect URLs:
   - `http://localhost:3000/auth/callback`
   - `https://trade-cognition.vercel.app/auth/callback`
5. In Google Cloud, the authorized redirect URI remains Supabase's callback:
   `https://fuobevtuecbzvqjralax.supabase.co/auth/v1/callback`.

The `profiles.phone` value is captured during email signup. It is contact data,
not a verified phone-auth identity. Google accounts may leave it empty.

No service-role key is needed. The browser uses the public anon/publishable key
with RLS. The local worker uses that same public key plus a one-time scoped
worker token created from the Accounts screen. Never put the worker token, an
MT5 password, or a service-role key in Vercel or a `NEXT_PUBLIC_*` variable.

## Which tables live where?

- Supabase Auth users are stored under `auth.users`, which does not appear as a
  normal public table in Table Editor.
- `public.profiles` mirrors user-facing Auth metadata.
- `public.user_trading_rules` stores the authoritative per-user strategy.
- `public.worker_agents` and `public.mt5_connections` store worker/account
  presence and snapshots, never MT5 credentials.
- `public.trade_intents`, `public.trade_commands`, and `public.trade_events`
  provide durable submission, execution status, and audit history.
- The worker keeps its encrypted MT5 credentials and recovery data locally.

Vercel does not need `NEXT_PUBLIC_API_URL`. Authentication and navigation do not
depend on Docker being online; an offline worker appears as status in the UI,
and unclaimed trade commands remain in Supabase until their short execution
deadline expires.
