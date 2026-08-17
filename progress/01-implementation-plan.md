# Implementation plan

## Status at 2026-08-17

- Phases 1 and 2 are implemented and locally verified.
- Phase 3 is implemented for the mock MT5 gateway, including submit, refresh,
  sync and close commands plus heartbeat-based TP/SL lifecycle reporting.
  Production Supabase execution and a Docker smoke test still require the SQL
  migrations to be run in the target project.
- Phase 4 is intentionally not implemented. Real MT5 execution remains blocked
  until a Windows/EA runtime and durable broker reconciliation are designed and
  tested on a demo account.

## Phase 1 — Control plane and frontend independence

- Create Supabase tables for rules, connection presence, trade intents,
  managed trade state and events.
- Add ownership constraints, RLS, indexes and timestamps.
- Add worker-only claim/finalize operations.
- Make the frontend Auth provider trust the Supabase session directly.
- Stop calling FastAPI `/auth/me` and `/mt5/accounts` during login.
- Render the application shell even when no worker or MT5 account is online.
- Replace the credential form with connection/pairing status.

Exit condition: Google and email users can sign in and navigate the complete
website while the local worker is off.

## Phase 2 — Supabase-native browser data

- Persist per-user rule settings in Supabase.
- Allow submission of a trade intent into the queue.
- Display queued, claimed, rejected, executed and completed states.
- Read account snapshots and worker heartbeat data from Supabase.
- Keep preliminary browser calculations clearly labelled as estimates.

Exit condition: the frontend has no required FastAPI URL.

## Phase 3 — Local worker

- Add worker configuration and Supabase authentication.
- Poll and atomically claim pending commands.
- Map commands into the existing domain calculation and rule engine.
- Revalidate with current gateway information.
- Execute through the existing gateway abstraction.
- Persist normalized results and append-only events.
- Recover safely after restarts without executing a command twice.

Exit condition: mock-mode commands complete end to end through Supabase.

## Phase 4 — Real MT5 adapter

- Select Windows worker, Windows bridge or MQL5 EA.
- Pair a terminal/account without storing its master password in the browser.
- Publish heartbeat/account/symbol snapshots.
- Exercise demo-account order placement, partial closes and stop changes.
- Add operational recovery and reconciliation tests.

Exit condition: a demo trade completes on a real MT5 terminal with an auditable
TP1/TP2/TP3 history.

## Profit ladder assumption

The written requirement exhausts the position at TP2 but also requests TP3.
Implementation will use the coherent progression:

- TP1 at 1R: close 50% of original volume; halve the original SL distance.
- TP2 at 2R: close 25% of original volume; move SL to TP1.
- TP3 at 3R: close the final 25%.

Broker lot-step rounding may promote an earlier rung to a full close when the
remaining volume would be untradeable.
