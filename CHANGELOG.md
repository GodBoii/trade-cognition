# Changelog

All notable changes to Trade Cognition are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0-dev] — 2026-08-15

First implementation pass. **Development is paused here at the user's request**;
see [Not finished](#not-finished) for what remains before this is deployable.

### At a glance

| Area | State |
| --- | --- |
| Risk engine, rules engine, position manager | Implemented, 109 tests passing |
| MT5 integration (simulated broker) | Implemented and exercised end to end |
| MT5 integration (live terminal) | Implemented, **never run against a real terminal** |
| REST API (26 endpoints + WebSocket) | Implemented, exercised over HTTP |
| Next.js web app (9 routes) | Implemented, builds clean, no automated tests |
| Docker images | Both build; `docker compose up` **not verified** |
| Documentation set (`docs/`) | **Not started** |

---

## Added

### Domain core — the trading mathematics

Pure, dependency-free and unit-tested. Nothing in `backend/app/domain/` imports
FastAPI, SQLAlchemy or MetaTrader5.

- **`quant.py`** — price and volume quantisation through `Decimal`. Broker
  instruments are quantised (prices to `tick_size`, volumes to `volume_step`);
  doing that arithmetic in binary floats produces values MT5 rejects as *invalid
  volume*. Every quantisation is grid-safe.
- **`market.py`** — `SymbolSpec`, `Tick`, `AccountSnapshot`, `PositionSnapshot`,
  `DealRecord`, `OrderResult`, `SymbolBrief`. All risk maths is built on one
  derived primitive, `money_per_price_unit_per_lot = tick_value / tick_size`,
  which handles FX, metals, index CFDs and crypto without special cases.
- **`risk.py`** — the pre-entry calculator. Produces entry, stop, lot size,
  maximum loss, expected profit per target, reward-to-risk (both final and
  scale-out weighted), required margin, and percentage of capital at risk.
  Direction is handled by `Side.sign` so there are no `if buy … else …` branches
  in the price maths.
- **`ladder.py`** — the 1:1 → 1:2 → 1:3 progression as declarative data, plus
  `allocate_stage_volumes`, which splits a position across rungs while
  respecting the broker's lot grid and never stranding a residual below
  `volume_min` (which could not be closed later).
- **`rules.py`** — the rules engine. Returns a **transparent report**: one entry
  per rule with its verdict and an actionable message, not a pass/fail flag.
- **`profile.py`** — the per-user risk configuration the engine enforces.

### Trading rules

| Code | Rule | Overridable |
| --- | --- | --- |
| `RULE1_ONE_ACTIVE_TRADE` | One live entry per user per derivative | No |
| `RULE2_LOT_ALLOCATION` | Volume follows the capital formula (0.02 lots / 1,000) | Yes, if enabled |
| `RULE2_VOLUME_CONSTRAINTS` | Volume satisfies broker min/max/step | No |
| `RULE3_MAX_RISK` | Loss at the stop ≤ 2% of capital | No |

Supporting guards: minimum stop distance, margin utilisation cap, symbol
tradability, account trade permission, minimum reward-to-risk, maximum
concurrent positions, daily loss limit.

Rules 1 and 3 are deliberately not overridable — they are the reason the
platform exists.

**Rule 1 is enforced by the database, not only by application code.**
`managed_trades.active_key` holds the symbol while a trade is live and `NULL`
once it closes, under a unique constraint on `(user_id, active_key)`. Because SQL
treats `NULL`s as distinct, any number of closed trades on a symbol can coexist
while a second live one is impossible — even under a race between two concurrent
requests. The row is inserted *before* the order is sent, so winning that insert
is the permission slip to trade.

### Position management

- Ladder execution in three phases per pass: **plan** (read pending rungs from
  the database), **act** (one serialised MT5 visit performs the closes and stop
  moves), **record** (apply the result to the database and audit trail).
- Stops are only ever moved in the risk-reducing direction. The manager cannot
  widen a stop, whatever the ladder says.
- A rung is marked filled only after the broker acknowledges it.
- Volume drift (someone closed part of a position in the terminal) is detected
  and reconciled rather than fought.
- Rungs whose share rounds to zero volume still move the stop when their target
  trades, so a position too small to scale out still has its risk reduced.
- Orders carry a **broker-side stop-loss and a take-profit at the final rung**,
  so an outage of this process cannot leave a position unprotected.
- The ladder is rebuilt from the **actual fill price**, so every R multiple is
  measured from where the trade really started. A fill that pushes risk past the
  ceiling is recorded as a warning event.

### MT5 integration

- `Mt5Gateway` abstraction with two implementations. The rest of the application
  never imports `MetaTrader5`.
- **`real.py`** — live terminal adapter: account switching, Market Watch
  selection, retry across filling modes on retcode 10030, and a retcode →
  plain-English message map.
- **`mock.py`** — deterministic in-process broker with seven instruments
  (EURUSD, GBPUSD, USDJPY, XAUUSD, US500, NAS100, BTCUSD), realistic contract
  specifications, bid/ask spread, random-walk prices, partial closes that book
  realised P/L, broker-side SL/TP execution, and stop validation that mirrors
  retcode 10016. This is what makes the whole platform testable without a
  terminal.
- **`manager.py`** — `Mt5Runtime` serialises every gateway call onto a single
  worker thread, because the vendor package is a process-wide singleton bound to
  one terminal and one logged-in account. Callers submit a *whole unit of work*
  so the account/spec/quote trio stays consistent and costs one thread hop.

### Persistence and security

- SQLAlchemy 2.0 models: `users`, `mt5_accounts`, `risk_profiles`,
  `managed_trades`, `trade_stages`, `trade_events`, `decisions`.
- MT5 passwords encrypted at rest with Fernet; decrypted only for the duration of
  a single gateway call. `Mt5Credentials.__repr__` redacts the secret so it
  cannot leak into logs or tracebacks.
- Passwords hashed with PBKDF2-HMAC-SHA256, 600,000 iterations (OWASP 2023), in a
  self-describing format that supports transparent upgrades.
- JWT bearer tokens; production refuses to start on a placeholder secret.

### API

26 REST endpoints plus `WS /api/ws/stream`, documented at `/docs`.

A **rules rejection returns HTTP 200 with `approved: false`** and the full plan
plus every rule check. It is a normal domain outcome, not a transport error, and
the client needs the numbers to explain the refusal. Structural problems (missing
stop, unknown symbol) do raise, with a specific error `code`.

- Pre-trade: `POST /api/calculator/preview`, `POST /api/calculator/stop-scan`,
  `GET /api/calculator/ladders`
- Execution: `POST /api/trades`, `GET /api/trades`, `GET /api/trades/{id}`,
  `POST /api/trades/{id}/{close,sync,manage}`, `GET /api/positions`
- Configuration: `GET|PUT /api/rules/profile`
- Audit: `GET /api/journal/{events,decisions,decisions/{id},performance}`
- Simulator controls at `/api/dev/mock/*`, mounted **only** when
  `TC_MT5_GATEWAY=mock` and `TC_ENV != production`, so they are absent from the
  OpenAPI document as well as unreachable elsewhere.

### Background monitor

Single asyncio task advancing every active trade on an interval, with failure
isolation per trade and exponential backoff so one broken symbol cannot
monopolise the cycle.

### Web application (Next.js 16, App Router)

- Routes: `/login`, `/` (dashboard), `/trade`, `/trades`, `/trades/[id]`,
  `/rules`, `/journal`, `/accounts`.
- **Trade ticket** — every parameter change re-runs the preview (debounced), so
  the figures and rule verdict on screen come from the same code path that will
  authorise the order. Submit is disabled while any rule blocks the entry.
- **Dashboard** — live account state and positions over WebSocket, with a REST
  polling fallback for hosts that cannot proxy an upgrade.
- **Trade detail** — ladder execution, the rule report captured at approval, and
  the full event log.
- **Journal** — every decision including refusals, with rule adherence statistics.
- Hand-written CSS with design tokens; no utility framework, so the build has no
  CSS toolchain to keep pinned alongside the trading logic.

### Repository layout and tooling

- Next.js app at the repository root (`app/`, `components/`, `lib/`, `state/`);
  Python backend in `backend/`. Root `npm run dev`, `npm run build` and
  `npm install` work, and static hosts need no custom root directory.
- `npm run dev:all` runs both halves; `dev:api`, `test:api`, `smoke` and
  `check:stack` delegate to the backend.
- `backend/scripts/smoke.py` — end-to-end workflow over real HTTP against the
  simulator, printing a readable transcript.
- `backend/scripts/check_dev_stack.py` — verifies the Next server renders, routes
  resolve, and `/api` proxies through to the backend.
- Docker: `backend/Dockerfile`, root `Dockerfile` (Next standalone output), and
  `docker-compose.yml` wiring both with a persistent volume for backend state.
  Compose fails fast with a clear message when `TC_JWT_SECRET` or
  `TC_CREDENTIAL_ENCRYPTION_KEY` is missing, rather than generating throwaway
  values that would orphan every stored MT5 password on restart.

---

## Fixed

Three real defects found during verification. Each is covered by a regression
test.

### 1. The position monitor missed targets touched between polls

The monitor compared only the *current* price against each rung. Because it
polls, a target could be touched and retrace before the next pass, and the rung
was silently skipped — TP1 never executed and the position ran to the broker's
take-profit at full size.

Found by the HTTP smoke test against a *drifting* simulated market; the unit
tests missed it because they set a price and immediately ran one pass.

Fixed by adding `Mt5Gateway.price_extremes(symbol, since)` — tick history with an
M1 bar fallback on the real terminal, recorded bid history in the simulator — and
triggering on the price *range* since the previous pass. `ManagedTrade.
last_checked_at` records the window.

Execution quality is reported honestly: when a target was touched and price had
retraced by the time the order went out, the fill is worse than the target level
and the journal entry says so rather than implying an exit at the target.

### 2. SQLite returned naive datetimes, and a bare `except` hid the consequence

SQLite has no timezone type: it returns naive `datetime` values. Comparing those
with the application's timezone-aware values raises `TypeError` — and the
comparison sat inside a `try` block that swallowed it, so the monitor degraded
silently and stopped seeing touched targets.

Fixed with a `UtcDateTime` `TypeDecorator` that normalises on the way in and out,
so every timestamp in the application is aware UTC regardless of backend. The
`except` now logs a warning: degrading is acceptable, doing it silently is not.

### 3. `TC_CORS_ORIGINS` crashed the process at import time

pydantic-settings JSON-decodes complex fields from the environment *before*
validators run, so the natural `TC_CORS_ORIGINS=http://a,http://b` raised
`SettingsError` and the backend would not start. It went unnoticed because the
variable was only ever documented, never set — the first Docker run exposed it.

Fixed with `Annotated[list[str], NoDecode]`; the field now accepts a comma
separated list, a JSON array, or a real list.

---

## Changed

- Frontend migrated from a Vite SPA to **Next.js 16 App Router**, and moved from
  `frontend/` to the repository root.
- TypeScript pinned to 5.9.3. TypeScript 7.0 is the current `latest` tag but does
  not resolve Next's type declarations, so `next` and `next/link` typed as
  `any` or failed outright.
- Dependencies raised to patched versions after `npm audit` reported high and
  critical advisories in the first pinned set (`react-router-dom`, `vite`,
  `concurrently` → `shell-quote`). Current dependency tree: **0 vulnerabilities**.
- Profile validation now returns HTTP 422 (`ValidationError`) rather than 401,
  and the sanity ceiling on lot allocation was tightened from 1.0 to 0.10 lots
  per 1,000 of capital — five times the house standard, and still leaves Rule 3
  as the binding constraint.
- Test suite made hermetic: `tests/conftest.py` hard-sets `TC_*` environment
  variables instead of using `setdefault`, so the suite can never inherit a
  developer's `TC_DATABASE_URL` and write to a real database or a live terminal.

---

## Design decisions worth your review

1. **The specification is ambiguous at TP2.** It says TP2 closes "the remaining
   50% of the original position" *and* describes a TP3 at 1:3 — but if TP2 closes
   the remainder, nothing survives to reach TP3.

   Resolved by making the ladder configurable rather than silently choosing.
   Two presets ship:

   - `standard_1_2_3` (**default**, the literal reading): 50% out at 1R with the
     stop tightened to half the original distance; the remaining 50% at 2R with
     the stop at TP1. TP3 executes only on volume that survives lot-step
     rounding. Blended outcome ≈ 1.5R.
   - `runner_1_2_3`: 50% at 1R, 25% at 2R, and a 25% runner carried to 1:3.
     Blended outcome ≈ 1.75R.

   **This is the one decision most worth confirming against your intent.**

2. **Rule 1 is scoped per user per symbol**, across all connected accounts — the
   literal reading of "a user can have only one active entry for a particular
   derivative". Scoping it per account instead is a one-line change in
   `ManagedTrade.build_active_key`, which already receives the account id.

3. **Live positions opened manually in the terminal block new entries** on that
   symbol under Rule 1, but are not adopted into ladder management.

4. **Single MT5 lane.** All broker I/O is serialised onto one thread, so
   throughput is limited by design. Scaling means one worker process per MT5
   account, not more threads.

5. **Synchronous database sessions inside async handlers.** Fine for SQLite or a
   local PostgreSQL (sub-millisecond); switch to the async engine if the database
   moves somewhere with real latency.

---

## Not finished

- **`docs/` documentation set — not started.** Planned: overview, architecture,
  risk-engine specification, trading rules, position management, API reference,
  MT5 integration guide, data model, security, setup and deployment, testing,
  operations runbook, and an ADR log. `README.md` has also not been written.
- **`docker compose up` is unverified.** Both images build (backend 204 MB, web
  230 MB) and `docker compose config` validates, but the stack was never
  confirmed healthy end to end — the first attempt hit a host port conflict and
  development stopped before a clean run.
- **The live MT5 gateway has never run against a real terminal.** Every
  verification used the simulator. Retcode handling, filling-mode retries, tick
  history timestamps (server time vs UTC) and account switching are all written
  from the documented API and remain untested against a broker.
- **No frontend tests.** No component, integration or end-to-end browser tests.
- **The WebSocket stream is not covered by automated tests**, though the REST
  polling fallback is exercised.
- **No database migrations.** Schema is created with `create_all`, so the
  `last_checked_at` column added in this pass will not appear in a database
  created before it. Alembic is needed before any schema change reaches a
  database you care about.
- **Rate limiting, refresh tokens and account lockout** are absent from the auth
  layer.

---

## Verification performed

Everything below was run and passed on this machine.

- **`backend`: 109 tests passing** — `test_quant.py` 13, `test_ladder.py` 9,
  `test_risk_calculator.py` 24, `test_rules.py` 22, `test_config.py` 9,
  `test_workflow.py` 32.
- **Domain arithmetic checked against hand calculations.** $10,000 capital →
  0.20 lots; a 500-point stop on EURUSD → $100 risk (1.00%); TP1 1.10012 /
  TP2 1.10512 / TP3 1.11012; TP1 closes 0.10 lots for $50 and moves the stop to
  1.09262, which converts an open risk into a locked-in +$25; plan total $150 for
  a blended 1.5R. Gold verified separately for contract geometry (100 oz per lot →
  $100 per 1.00 move per lot).
- **Rule 1's database guard proven**: a second live trade on the same symbol
  raises `IntegrityError`; closing the first releases the lock and re-entry
  succeeds.
- **`backend/scripts/smoke.py`: all checks passed** over real HTTP against a
  running server — connect, reject on Rule 3, execute, block the duplicate on
  Rule 1, confirm another derivative stays tradable, drive the market to TP1
  (0.10 lots closed, stop tightened), then to TP2 (closed, realised $150.00
  against a planned $150.00), confirm the Rule 1 lock released, and check the
  journal contains `validated`, `order_filled`, `partial_close`, `sl_modified`
  and `position_closed`.
- **`backend/scripts/check_dev_stack.py`: 15/15 checks passed** — Next renders,
  all eight routes resolve, unknown paths 404, `robots.txt` served, `/api`
  proxies to the backend, and the OpenAPI docs are served.
- **`npm run build`**: TypeScript clean under `strict` plus `noUnusedLocals`,
  `noUnusedParameters` and `noUncheckedIndexedAccess`; 9 routes generated.
- **`npm audit`: 0 vulnerabilities.**
- **`docker build`**: both images build successfully.
- **`docker compose config`**: valid.

---

## Notes

- A local `.env` was created during verification and contains a **throwaway**
  JWT secret and Fernet key. It is git-ignored. Regenerate both before any real
  deployment:

  ```bash
  python -c "import secrets;print(secrets.token_urlsafe(64))"
  python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
  ```

- `backend/data/credential.key` is a development encryption key generated
  automatically because `TC_CREDENTIAL_ENCRYPTION_KEY` was unset. Production
  refuses to start without an explicit key.
- The default gateway is `mock`. **No real orders have been placed at any point.**
- Nothing in this project has been reviewed by anyone qualified to assess trading
  risk. Use a demo account.
