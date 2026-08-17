# Worker and MT5 boundary

## What MT5 does by itself

After an order is accepted, the broker/MT5 side can hold the position and
enforce the hard stop-loss and take-profit attached to it. The website does not
need to stay open for those broker-side protections.

## What still needs active automation

The requested strategy is not a single static order. It requires software to
observe price/position changes and submit later broker requests:

- close 50% at TP1;
- change the remaining stop after TP1;
- close 25% at TP2;
- move the stop to TP1 after TP2;
- close the final 25% at TP3;
- prevent another active entry for the same user and symbol.

MT5 will not infer those custom actions from the initial position. They must be
performed by either the Python worker or an MQL5 Expert Advisor. If that
automation is offline, the hard broker stop/final take-profit remain, but
staged partial exits and stop changes can be delayed or missed.

## Current reusable engine

The Python backend already contains the pieces that should be reused by the
queue worker:

- live account, symbol, quote and position collection;
- capital, volume, stop risk, margin and target calculation;
- one-position-per-symbol, strict lot and maximum-risk checks;
- broker entry submission with a hard stop;
- managed-trade stages and position monitoring;
- serialization of terminal calls through one execution lane.

The Supabase worker is therefore an orchestrator around the existing engine,
not a second implementation of the strategy.

## Docker limitation

The repository's Docker image is Linux and intentionally selects the mock MT5
gateway. Docker Desktop running on Windows does not give a Linux container the
Windows terminal's IPC channel used by the official MetaTrader5 Python package.

The queue and rules can be tested end to end in Docker mock mode. Real demo/live
execution must later run as one of:

1. the Python worker natively on Windows next to the logged-in MT5 terminal;
2. a Linux orchestrator calling a deliberately implemented Windows bridge; or
3. an MQL5 Expert Advisor that consumes commands and reports state.

The system must never silently label Linux mock execution as live MT5.

## Mock reporting behavior

The mock queue worker durably maps each local managed trade to its Supabase
intent. On each connection heartbeat it publishes changed lifecycle and ladder
state after the local database transaction has committed. Reporting latency is
therefore at most the heartbeat interval. A restart can add a duplicate
reconciliation event, and several stage transitions between heartbeats can be
collapsed into one complete final snapshot; the resulting trade state remains
idempotent and authoritative.
