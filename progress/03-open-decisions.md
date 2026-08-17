# Open decisions and risks

## Real MT5 runtime

Linux Docker cannot load the official Windows MT5 terminal integration. Before
real-money testing, select exactly one runtime:

- Windows Python worker outside Docker;
- Docker orchestrator plus a Windows host bridge; or
- MQL5 Expert Advisor as the execution adapter.

Recommendation: use an Expert Advisor for a multi-user product, or a Windows
Python worker for a single-owner installation.

## Worker authentication

The queue migration uses a narrowly scoped, per-user worker token. Supabase
returns the plaintext once and stores only its hash in a private schema. This is
preferred to giving a local worker the project-wide service-role key. The token
must remain only in the local worker environment; it must never be put in
Vercel, returned after initial setup, logged, or committed to Git.

## Offline execution

Queued commands must have an expiry and an explicit stale-price policy. A trade
must not be executed minutes later merely because the worker came back online.
The worker will reject expired intents and revalidate price, margin, open
positions and all risk rules before any broker request.

## Account model

The current Python MT5 runtime serializes access through one process-wide
terminal session. Multi-user scale requires one isolated terminal/runtime per
account or an EA installed per user. The Supabase schema must not imply that one
Linux container can safely switch among unlimited live accounts.

## Pending-order support

The safe mock queue adapter currently executes market orders only. The website
labels limit and stop orders as pending real-MT5-adapter work instead of
pretending they are supported. Their broker-specific placement, expiry and
reconciliation semantics must be implemented and demo-tested in Phase 4.
