# Command lifecycle and security

## Durable lifecycle

```text
browser creates intent
        |
        v
Supabase: queued command -- worker offline --> remains queued until expiry
        |
        v atomic claim + lease
worker validates current MT5 state and all rules
        |
        +--> rejected/expired/failed (durable reason)
        |
        `--> submitted/open --> TP events --> closed
```

The browser must show the recorded status and must never describe `queued` as
`executed`. Closing the browser cannot lose an intent because the source of
truth is Supabase, not React state or a connection to the worker.

## Ownership and privileges

- Browser requests use the signed-in user's Supabase JWT and RLS.
- User IDs are always derived from `auth.uid()` inside security-definer RPCs.
- Browsers can enqueue and read their own work but cannot claim it, write broker
  tickets, forge results, or append worker audit events.
- Each local worker receives a high-entropy scoped worker token. Supabase stores
  only its SHA-256 hash in a private schema.
- Neither the worker token nor an MT5 password belongs in Vercel,
  `NEXT_PUBLIC_*`, source control, logs, or browser-readable tables.
- MT5 credentials stay encrypted in the local worker database or inside the
  logged-in terminal. Supabase holds only non-secret connection metadata.

## Claim safety

Claims use PostgreSQL row locks with `SKIP LOCKED`, a lease deadline, and a
random claim token. Completion must present the matching worker and claim
token, preventing an old worker from overwriting a command that was reclaimed
after its lease expired.

## Broker idempotency

No transaction can atomically cover Supabase, local SQLite and an MT5 broker.
Practical duplicate prevention therefore needs all of:

- a stable browser-generated request UUID;
- a persistent local receipt before order submission;
- the intent fingerprint in MT5 magic/comment metadata;
- reconciliation against positions/recent deals after an uncertain response;
- no blind retry after a broker submission timeout.

The initial worker slice implements durable claim/complete mechanics. Broker
reconciliation is a required gate before enabling real-money execution.
