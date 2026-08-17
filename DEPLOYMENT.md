# Deployment

## 1. Supabase control plane

In Supabase SQL Editor, run these files in order:

1. `supabase/000_RUN_THIS_IN_SUPABASE.sql`
2. `supabase/002_async_trade_queue.sql`

Then apply the Auth Site URL, redirect URLs, and Google provider settings in
`supabase/README.md`. The second migration creates RLS-protected rules,
non-secret MT5 connection metadata, expiring trade intents, queue commands,
worker identities, and audit events.

## 2. Frontend on Vercel

Import the repository as one Next.js project at the repository root. Add only
the public Supabase browser values for Production and Preview:

```dotenv
NEXT_PUBLIC_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=YOUR_PUBLIC_ANON_OR_PUBLISHABLE_KEY
```

Do not set `NEXT_PUBLIC_API_URL`. Vercel does not host or proxy the local MT5
worker. Redeploy after changing a `NEXT_PUBLIC_*` build-time value.

Never put any of these in Vercel:

- `TC_WORKER_TOKEN`
- an MT5 login password
- `TC_CREDENTIAL_ENCRYPTION_KEY`
- a Supabase service-role key

## 3. Create a local worker pairing

Sign in to the deployed website, open **Accounts**, and create a worker plus MT5
connection. Copy the one-time worker token immediately. The browser does not
save it.

Put the token and public Supabase values in the local machine's `.env`:

```dotenv
TC_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
TC_SUPABASE_ANON_KEY=YOUR_PUBLIC_ANON_OR_PUBLISHABLE_KEY
TC_SUPABASE_QUEUE_ENABLED=true
TC_WORKER_TOKEN=tcw_THE_ONE_TIME_TOKEN
TC_WORKER_BATCH_SIZE=1
TC_CREDENTIAL_ENCRYPTION_KEY=YOUR_STABLE_FERNET_KEY
```

Start the development worker stack with:

```text
docker compose up -d --build
```

The queue is durable and asynchronous. The website never needs an HTTP request
to Docker. A queued instruction expires after five minutes by default, so an
offline laptop cannot later execute a stale market order.

## 4. Real MT5 limitation

The supplied Docker image is Linux and uses `TC_MT5_GATEWAY=mock`. It cannot
control the Windows MetaTrader 5 terminal through the official Python IPC
package merely because Docker Desktop is running on Windows.

Real execution requires a later production adapter:

1. run the worker natively with Windows Python beside MT5;
2. build a deliberate Windows host bridge; or
3. install an MQL5 Expert Advisor that consumes the durable queue.

Do not enable real-money trading until broker idempotency/reconciliation,
restart recovery, and demo-account ladder tests have passed.
