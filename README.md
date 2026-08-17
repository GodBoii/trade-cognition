# Trade Cognition ⚡

> **Discipline-Enforcing Execution & Risk Management Engine for MetaTrader 5**

Trade Cognition is a systematic execution layer built between the trader and MetaTrader 5. It transforms discretionary trading into a mathematically bounded, rules-governed execution model. By calculating exact risk parameters, verifying non-negotiable discipline rules before sending orders, and automating multi-stage partial scale-outs with progressive stop adjustments, Trade Cognition prevents overtrading, oversized positions, and emotional exits.

---

## 🌟 Core Pillars & Key Features

### 1. Pure Domain Mathematics & Risk Engine
- **Grid-Safe Decimal Quantisation (`quant.py`)**: All broker volume steps, minimums, maximums, and price tick sizes are quantised via `Decimal` to completely eliminate IEEE 754 floating-point drift that causes MT5 `invalid volume` or `invalid price` order rejections.
- **Universal Asset Normalisation (`market.py`)**: Unified mathematical primitive `money_per_price_unit_per_lot = tick_value / tick_size` seamlessly handles Forex pairs, Precious Metals, Index CFDs, and Crypto without hardcoded symbol branches.
- **Pre-Entry Plan Calculator (`risk.py`)**: Computes exact lot sizing based on user equity, strict stop loss distance, gross risk capital, required broker margin, and expected profit at every target rung before an order is placed.
- **Direction-Agnostic Arithmetic**: Uses `Side.sign` (+1 for BUY, -1 for SELL) across calculations to eliminate sign inversion bugs.

---

### 2. Transparent Rules Engine
Before any order reaches the broker, it must pass through the rules evaluation pipeline:

| Rule Code | Description | Overridable |
| :--- | :--- | :---: |
| `RULE1_ONE_ACTIVE_TRADE` | **One Live Entry per Derivative**: Prevents averaging down and revenge-trading duplicate positions. Guaranteed by database unique constraints `(user_id, active_key)`. | ❌ **No** |
| `RULE2_LOT_ALLOCATION` | **Strict Formula Allocation**: Computes size as `(Trading Capital / 1,000) * 0.02 lots`. | ❌ **No** |
| `RULE2_VOLUME_CONSTRAINTS`| **Broker Compliance**: Enforces broker minimum, maximum, and lot step boundaries. | ❌ **No** |
| `RULE3_MAX_RISK` | **Strict 2% Risk Ceiling**: Monetary loss at the hard stop-loss cannot exceed 2% of user balance. | ❌ **No** |
| `GUARD_MARGIN_CAP` | **Margin Utilization Cap**: Ensures margin requirements never exceed account safety thresholds. | ❌ **No** |
| `GUARD_MIN_STOP_DISTANCE` | **Broker Stop-Level Compliance**: Stop-loss cannot be placed inside broker freeze/stop levels. | ❌ **No** |
| `GUARD_DAILY_LOSS_LIMIT` | **Daily Drawdown Circuit Breaker**: Locks execution if daily cumulative losses reach profile limit. | ❌ **No** |

---

### 3. Automated 3-Stage Profit Ladder & Trailing Management
Trade Cognition manages active positions across an automated multi-rung execution ladder:

```
[Entry Price] ──────► [1:1 Rung (TP1)] ──────► [1:2 Rung (TP2)] ──────► [1:3 Rung (TP3)]
  Initial SL           Close 50% Volume          Close 25% Volume          Close final 25%
                      Move SL to 0.5R Risk      Move SL to Breakeven/TP1   Final Target
```

- **Broker-Side Failsafe Protection**: Every order is placed with hard broker-side SL and TP at the final rung. Even during complete network loss or server downtime, positions remain protected.
- **Dynamic Re-Anchoring**: When filled, the ladder is dynamically reconstructed from the actual execution fill price rather than the requested quote.
- **Monotonic Stop Protection**: Trailing logic is strictly one-way — the position manager can only ever tighten risk, never widen a stop.

---

### 4. Dual MT5 Gateway Architecture
- **Simulated In-Memory Broker (`mock.py`)**: Full high-fidelity broker emulator with order filling, spread simulation, tick generation, and position management for fast CI test suites and offline local development.
- **Native MetaTrader 5 Terminal Driver (`real.py`)**: Windows-native IPC integration directly interacting with the official MT5 client terminal, supporting multi-account management with encrypted credential storage (Fernet / AES-CBC).

---

### 5. Backend-Independent Next.js 16 Dashboard
- **Supabase Auth**: Email and Google sessions remain valid even when Docker is stopped.
- **Durable Trade Ticket**: Instructions use an idempotency UUID and short execution deadline.
- **Honest Offline State**: Missing/stale snapshots are never rendered as fake zero balances or successful orders.
- **Queue & Audit Views**: Queued, claimed, validating, submitted, rejected, failed, expired, open, and closed remain distinct states.

---

## 🏛 System Architecture

```text
Vercel / Next.js website
        |
        | Supabase Auth + RLS-protected reads/RPCs
        v
Supabase control plane
  rules · connections · expiring intents · commands · audit events
        ^
        | scoped worker token + atomic claim leases
        |
Local execution worker
  existing calculator · rules engine · trade service · monitor
        |
        +-- Linux Docker: mock broker only
        `-- real MT5: Windows worker/bridge or future MQL5 EA
```

There is no required browser-to-worker HTTP connection. MT5 credentials stay on
the local machine and never enter browser-readable Supabase tables.

---

## 📁 Repository Structure

```
.
├── app/                        # Next.js 16 frontend (App Router)
│   ├── (app)/                  # Authenticated application views
│   │   ├── accounts/           # MT5 Account management & connections
│   │   ├── journal/            # Trade reflection & psychology journal
│   │   ├── rules/              # Trading rules & risk profile settings
│   │   ├── trade/              # Trade preparation & entry ticket
│   │   └── trades/             # Active & historical trade details
│   ├── auth/                   # Supabase authentication callback
│   ├── globals.css             # Design tokens and visual styling
│   └── layout.tsx              # Root shell layout
├── backend/                    # Python 3.11+ FastAPI service
│   ├── app/
│   │   ├── api/                # REST endpoints, schemas & dependencies
│   │   ├── core/               # Security, crypto & Supabase JWT auth
│   │   ├── db/                 # SQLAlchemy models, sessions & migrations
│   │   ├── domain/             # Pure trading mathematics & rules engine
│   │   │   ├── ladder.py       # 1:1 -> 1:2 -> 1:3 profit ladder calculations
│   │   │   ├── market.py       # Tick, symbol and market data primitives
│   │   │   ├── quant.py        # Decimal-safe precision & lot quantisation
│   │   │   ├── risk.py         # Pre-entry calculator & trade plan model
│   │   │   └── rules.py        # Transparent 3-rule verification engine
│   │   ├── mt5/                # MetaTrader 5 gateway & mock broker
│   │   ├── services/           # Accounts, trades, calculator & journal services
│   │   └── workers/            # Position monitor + Supabase queue worker
│   └── tests/                  # Domain, workflow, auth and queue tests
├── components/                 # Reusable React components & UI primitives
├── lib/                        # Supabase control-plane helpers & formatting
├── state/                      # Independent Auth and trading-control contexts
├── supabase/                   # PostgreSQL schema definitions & migrations
├── docker-compose.yml          # Local containerized orchestration
└── Dockerfile                  # Container build definitions
```

---

## 🚀 Quick Start

### Prerequisites
- **Python**: 3.11 or 3.12 (with `pip` and virtual environment support)
- **Node.js**: 20+ and `npm`
- **MetaTrader 5 Terminal** *(Optional, for real broker connectivity on Windows)*

---

### Backend Setup

1. **Navigate to the backend directory and create a virtual environment**:
   ```bash
   cd backend
   python -m venv .venv
   ```

2. **Activate the virtual environment**:
   - **Windows (PowerShell)**:
     ```powershell
     .\.venv\Scripts\Activate.ps1
     ```
   - **Linux / macOS**:
     ```bash
     source .venv/bin/activate
     ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**:
   ```bash
   cp .env.example .env
   ```

5. **Run backend unit and integration tests**:
   ```bash
   pytest backend/tests
   ```

6. **Start the FastAPI backend server**:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```
   API interactive documentation will be live at `http://localhost:8000/docs`.

---

### Frontend Setup

1. **Install Node.js dependencies**:
   ```bash
   npm install
   ```

2. **Build and start Next.js dev server**:
   ```bash
   npm run dev
   ```
   The web application will be accessible at `http://localhost:3000`.

---

## 🧪 Testing & Verification

The test suite covers every domain calculation, lot sizing boundary condition, rule failure state, and multi-rung order lifecycle:

```bash
# Run full backend test suite
pytest backend/tests -v
```

Output:
```
backend/tests/test_config.py ..........                                  [  8%]
backend/tests/test_ladder.py ....................                        [ 26%]
backend/tests/test_quant.py ...................................          [ 57%]
backend/tests/test_risk_calculator.py .................                  [ 72%]
backend/tests/test_rules.py ...................                          [ 89%]
backend/tests/test_supabase_auth.py .                                    [ 90%]
backend/tests/test_workflow.py ...........                               [100%]

All backend tests should pass; the exact count grows as queue/recovery coverage is added.
```

---

## 📜 License

Distributed under the MIT License.
