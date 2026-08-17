# Acceptance checklist

## Website independence

- [x] A Supabase session is the authentication authority.
- [x] Login no longer waits for `/api/auth/me` or `/api/mt5/accounts`.
- [x] A user without an MT5 connection can enter the application shell.
- [x] Local Next.js typecheck and production build pass after auth decoupling.
- [x] Every data page reads Supabase or shows an explicit unpaired/offline state.
- [x] No authenticated route makes a required FastAPI request.
- [x] Worker downtime never logs out a Supabase user.
- [x] Stale/missing snapshots are not displayed as genuine zero balances.

## Supabase control plane

- [ ] Run the auth/profile migration in the target Supabase project.
- [ ] Run the asynchronous queue migration in the target Supabase project.
- [ ] Verify users can read only their own rows.
- [ ] Verify browsers cannot mutate claim/result/audit columns.
- [ ] Verify two workers cannot claim one command concurrently.
- [ ] Verify an expired lease fences the earlier claim token.
- [x] Queue RPC implements idempotent browser request UUIDs.

## Strategy correctness

- [x] Strict default allocation is 0.02 lots per 1,000 capital.
- [x] Maximum stop risk is 2% of selected capital.
- [x] One active trade per user and symbol is non-overridable.
- [x] Default ladder is coherent: TP1 50%, TP2 25%, TP3 25%.
- [x] Mock worker revalidates quote, symbol limits, margin and positions.
- [x] Mock TP1/TP2/TP3 changes are reported to Supabase on worker heartbeat.

## Execution environments

- [x] Queue-to-engine mock submit/sync/close paths pass automated tests.
- [ ] Run a real Supabase-to-Docker mock smoke test after applying the SQL.
- [x] Unsupported real MT5 queue mode fails loudly in Linux.
- [ ] Select Windows worker, Windows bridge or MQL5 EA for real MT5.
- [ ] Complete demo-account reconciliation and restart tests.
- [ ] Perform a separate review before any real-money enablement.
