# Progress log

## 2026-08-17 — Architecture reset

- Confirmed that Supabase email and Google authentication succeed in
  production.
- Confirmed the post-login failure is the required FastAPI `/api/auth/me` call,
  which Vercel rewrites to private `127.0.0.1:8000` when no external API URL is
  configured.
- Confirmed the Linux Docker backend is mock-only for MT5.
- Confirmed all 113 backend tests and the Next.js production build pass before
  the migration.
- Adopted an asynchronous Supabase command/control plane so the browser no
  longer needs a continuous or synchronous backend connection.
- Started schema, frontend dependency and worker design work in parallel.
- Decoupled application authentication from FastAPI account/profile calls.
- Removed the no-MT5-account application gate and the Sidebar health request.
- Verified the decoupled frontend with TypeScript and a production Next build.
- Changed the default strategy ladder to 50% / 25% / 25% so TP3 is real.
- Added the first asynchronous Supabase queue migration and scoped-worker-token
  design; final privilege review and browser/worker adapters remain in progress.
- Finalized the queue with RLS, narrow RPC grants, atomic leases/fencing,
  idempotency UUIDs, five-minute execution deadlines, and stale-order expiry.
- Replaced browser-side MT5 credential collection with one-time worker pairing;
  neither MT5 passwords nor worker tokens are persisted by the website.
- Migrated Dashboard, Rules, New Trade, Trades, trade details, and Journal to
  Supabase reads/RPCs with explicit unpaired/offline/stale/queued states.
- Removed the unused FastAPI account state and WebSocket/polling hook from the
  frontend path. Vercel no longer installs an implicit localhost API rewrite.
- Added a disabled-by-default typed Supabase queue client/poller to the Python
  worker with scoped-token redaction and lease-aware completion.
- Connected the queue worker to the existing mock trading engine for submit,
  account refresh, trade sync and close commands.
- Added startup and periodic paired-connection discovery so a newly assigned
  connection publishes an account heartbeat before the first trade command.
- Added deterministic mock-account mapping and duplicate-submit recovery so a
  reclaimed command does not place a second position.
- Added completion-state gating so failed/rejected control commands do not
  corrupt the parent trade lifecycle or its timestamps.
- Added a durable local Supabase-intent mapping and change-only heartbeat
  reporting for open/scaling/closed state, TP stage snapshots and management
  errors. Failed reporting is retried on the next heartbeat.
- Made queue startup fail loudly when the real MT5 gateway is selected; live
  MT5 is not claimed or enabled by this implementation.
- Verified the final frontend typecheck and production build, all 141 backend
  tests, the 170-statement PostgreSQL migration parse, and `git diff --check`.

## Current milestone

Phases 1 and 2 and the mock-only Phase 3 worker are implemented locally. The
SQL has not been executed in the production Supabase project; the user must run
the two setup files before the new pages can read the control-plane tables. An
actual Supabase-to-Docker smoke test is therefore still pending. Real MT5
remains Phase 4 and requires a Windows/EA adapter plus broker reconciliation.
