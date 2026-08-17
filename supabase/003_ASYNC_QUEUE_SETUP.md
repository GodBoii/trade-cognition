# Supabase asynchronous MT5 queue

Run `000_RUN_THIS_IN_SUPABASE.sql` first (or `001_auth_profiles.sql` on an
existing installation), then run `002_async_trade_queue.sql` in the Supabase
SQL Editor. The queue migration can be rerun safely.

## What this migration stores

- `user_trading_rules`: strict 0.02 lots / 1,000 capital, maximum 2% risk,
  single-active-symbol rule, and TP allocation. Database constraints prevent
  loosening Rules 2 and 3. The default `runner_1_2_3` ladder closes 50% at TP1,
  25% at TP2, and 25% at TP3.
- `worker_agents`: non-secret registrations for local Docker workers.
- `mt5_connections`: connection and account status reported by MT5. It never
  stores the MT5 password.
- `trade_intents`: the user's requested orders and their business lifecycle.
- `trade_commands`: the durable worker queue.
- `trade_events`: the append-only audit/journal stream.

The browser uses Supabase Auth and RLS. It may read only the current user's
rows. It cannot directly change intent results, queue claims, or audit events.

## Pair a local worker without a service-role key

After the user signs in, call the authenticated RPC:

```ts
const { data, error } = await supabase.rpc("tcq_create_worker", {
  p_name: "My Docker MT5 worker",
});
```

Show `worker_token` once and ask the user to put it in the local Docker `.env`.
Only its SHA-256 hash is stored by Supabase. The token can be rotated with
`tcq_rotate_worker_token` or permanently invalidated with `tcq_revoke_worker`.

The worker needs only:

```dotenv
TC_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
TC_SUPABASE_ANON_KEY=YOUR_PUBLIC_ANON_KEY
TC_WORKER_TOKEN=tcw_THE_ONE_TIME_SECRET_RETURNED_ABOVE
```

Never put `TC_WORKER_TOKEN` in Vercel, a `NEXT_PUBLIC_*` variable, source code,
logs, or Git. Never provide the Supabase service-role key to the browser or the
local worker.

Create `mt5_connections` from the authenticated browser with the returned
worker ID, current `auth.uid()`, and a label. The worker later fills non-secret
account metadata through `tcq_worker_heartbeat`.

## Submit from the website

Generate and retain a UUID in the browser, then call:

```ts
const requestId = crypto.randomUUID();

const { data, error } = await supabase.rpc("tcq_enqueue_trade_intent", {
  p_connection_id: connectionId,
  p_client_request_id: requestId,
  p_symbol: "EURUSD",
  p_side: "buy",
  p_order_kind: "market",
  p_stop_loss: 1.0725,
  p_stop_points: null,
  p_requested_volume: null,
  p_comment: "TC web order",
  p_metadata: {},
});
```

Retry with the same UUID after a network error. Supabase returns the original
intent and does not enqueue a second order. Reusing that UUID for different
trade parameters is rejected.

The insert trigger creates a `submit_trade` command automatically. Direct
browser inserts into the intent, command, and event tables are not granted.
The enqueue RPC defaults `p_execute_before` to five minutes from now and rejects
deadlines more than fifteen minutes away. If the computer is offline beyond
that deadline, the next worker claim marks the command and intent `expired`;
it never sends the stale market order to MT5.

## Local worker loop

1. Call `tcq_worker_list_connections(workerToken)` at startup to discover only
   the enabled connections assigned to this scoped worker.
2. Call `tcq_claim_trade_commands(workerToken, limit, leaseSeconds)`.
3. For every claimed row, retain its `id` and random `claim_token`.
4. Call `tcq_worker_get_context` to obtain the current connection metadata and
   risk rules.
5. Revalidate against live MT5 balance, quotes, symbol limits, margin, open
   positions, and the one-active-symbol rule.
6. Make the broker action idempotent. A worker can crash after MT5 accepts an
   order but before Supabase records completion, so use the intent ID in the
   MT5 magic/comment mapping and reconcile before attempting a retry.
7. If an MT5 operation is still running near the end of its lease, renew the
   active fenced claim with `tcq_extend_command_lease`.
8. Complete the leased command with `tcq_complete_trade_command`, including the
   same claim token and a structured result/rules report.
9. During subsequent TP1/TP2/TP3 monitoring, call
   `tcq_worker_update_trade_state` and/or `tcq_worker_append_event`.
10. Send account snapshots periodically through `tcq_worker_heartbeat`.

A claim lease that expires can be reclaimed. The old claim token is fenced and
can no longer complete the command. After `max_attempts`, the command and its
submit intent are marked failed instead of remaining stuck.

`execute_before` is a do-not-**start**-after deadline. A command claimed before
that time may finish under its active lease even if the wall clock crosses the
deadline during the MT5 call. If that lease also expires, the command is marked
expired rather than reclaimed after its execution window.

Completion keeps command outcome and trade lifecycle separate. A successful
`submit_trade` may publish `submitted`, `open`, `scaling`, or `closed`; rejected
and failed submits become the matching intent state. Failed/rejected
close/sync commands preserve the parent trade state. A successful close/sync
may publish the authoritative `open`, `scaling`, `closed`, or `failed` state it
read from MT5. Lifecycle timestamps are derived from that same gated state, so
a control-command error cannot accidentally mark an open trade as closed.

## Important runtime limitation

Supabase is the durable coordination layer; it does not monitor MT5. TP partial
closes and SL movements occur only while the local Docker/Windows MT5 worker is
running. Broker-hosted hard SL/final TP orders remain active when the worker is
offline, but custom staged actions do not. Continuous staging requires keeping
the computer and terminal running or moving the worker to an always-on Windows
VPS.

## Realtime subscriptions

The frontend does not need a continuous connection to the worker. It may load
tables normally whenever a page opens. Supabase Realtime subscriptions to
`mt5_connections`, `trade_intents`, and `trade_events` are optional UI
enhancements; correctness comes from durable rows and worker polling, not from
Realtime delivery.
