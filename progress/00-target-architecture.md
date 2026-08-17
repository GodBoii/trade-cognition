# Target architecture

## Product boundary

Trade Cognition has three independent responsibilities:

1. The website authenticates users, explains the rules, collects settings and
   trade intents, and displays state. It must remain usable when the local
   worker is offline.
2. Supabase is the durable control plane. It stores user-owned settings,
   connection metadata, queued trade intents, execution state and audit events.
3. A trusted local worker validates commands and performs broker operations.
   The current Python domain engine and mock MT5 gateway are retained here.

```text
Vercel / browser
    |
    | Supabase Auth + row-level secured reads/writes
    v
Supabase
    |-- profiles and rules
    |-- MT5 connection presence (never an MT5 password)
    |-- queued trade intents
    |-- trade state and events
    ^
    | worker-only claim/complete operations
    |
Local worker
    |-- domain risk/rule engine
    |-- mock gateway in Linux Docker
    `-- real gateway only through a Windows MT5 runtime or future EA bridge
```

There is no synchronous browser-to-worker request path. The frontend submits a
durable intent and observes status changes. An offline worker therefore delays
execution without breaking authentication or the rest of the website.

## Security invariants

- MT5 master passwords are never stored in browser-visible Supabase tables.
- The Supabase service-role key is never exposed through `NEXT_PUBLIC_*`.
- Browser operations are restricted by RLS to `auth.uid()` ownership.
- Only the worker can claim, execute or finalize queued work.
- Every execution is idempotent and identified by a stable command UUID.
- The worker revalidates every risk calculation against current account,
  symbol and price data immediately before execution.
- A broker-side hard stop and failsafe take-profit are attached at entry.
- The local worker being offline must be visible in the UI through a stale
  heartbeat, not misreported as a disconnected Supabase session.

## Real MT5 constraint

The official `MetaTrader5` Python package talks to a locally installed Windows
terminal. The supplied Linux Docker image can only use the mock gateway. Real
execution therefore needs one of these later adapters:

1. run the worker with Windows Python outside the Linux container;
2. add a small Windows host bridge that the Docker worker can call; or
3. move execution/monitoring into an MQL5 Expert Advisor paired through
   Supabase.

The asynchronous Supabase control plane is compatible with all three choices.

