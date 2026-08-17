"""The position monitor.

A single asyncio task that, every ``TC_MONITOR_INTERVAL_SECONDS``, walks every
active managed trade and asks the position manager to advance it.  This is what
turns the plan into executed behaviour: TP1 partial exit and stop tightening,
TP2 exit with the stop at TP1, TP3 final exit, plus reconciliation when a
position is closed by the broker or by hand.

Operational properties
----------------------
* **One trade at a time.**  All MT5 work is serialised anyway (single worker
  thread), so processing sequentially avoids queueing storms and keeps log order
  meaningful.
* **Failure isolation.**  An exception on one trade is logged against that trade
  and never stops the loop.
* **Backoff.**  A trade that fails repeatedly is skipped for an increasing
  cooldown so a broken symbol cannot monopolise the cycle.
* **Not a substitute for broker stops.**  Every managed position carries a real
  stop-loss and a take-profit at the final rung on the server, so an outage of
  this process cannot leave a position unprotected.
"""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import select

from ..config import Settings, settings as default_settings
from ..db.models import ManagedTrade, Mt5AccountRow
from ..db.session import session_scope
from ..domain.enums import EventType, TradeStatus
from ..logging_conf import get_logger
from ..services import journal, position_manager

log = get_logger(__name__)

#: Cooldown after consecutive failures, in seconds, by failure count.
BACKOFF_LADDER = (0.0, 5.0, 15.0, 60.0, 300.0)


class PositionMonitor:
    """Owns the management loop."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or default_settings
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._failures: dict[int, int] = {}
        self._cooldown_until: dict[int, float] = {}
        self.cycles = 0
        self.actions = 0
        self.last_error = ""

    # ------------------------------------------------------------- lifecycle
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        if not self._settings.monitor_enabled:
            log.warning("Position monitor is disabled (TC_MONITOR_ENABLED=false)")
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="position-monitor")
        log.info(
            "Position monitor started (interval %.1fs)", self._settings.monitor_interval_seconds
        )

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: B014 - shutdown is best effort
            pass
        log.info("Position monitor stopped after %s cycles, %s actions", self.cycles, self.actions)

    # ------------------------------------------------------------------ loop
    async def _loop(self) -> None:
        interval = max(self._settings.monitor_interval_seconds, 0.25)
        while not self._stopping.is_set():
            started = time.perf_counter()
            try:
                await self.run_once()
            except asyncio.CancelledError:  # pragma: no cover
                raise
            except Exception as exc:  # pragma: no cover - loop must survive
                self.last_error = str(exc)
                log.exception("Monitor cycle failed: %s", exc)

            elapsed = time.perf_counter() - started
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=max(0.0, interval - elapsed))
            except TimeoutError:
                continue

    async def run_once(self) -> int:
        """One pass over all active trades.  Returns the number of actions taken."""
        self.cycles += 1
        actions = 0

        with session_scope() as session:
            trades = list(
                session.scalars(
                    select(ManagedTrade)
                    .where(
                        ManagedTrade.status.in_([s.value for s in TradeStatus.active()]),
                        ManagedTrade.position_ticket.is_not(None),
                    )
                    .order_by(ManagedTrade.id)
                )
            )
            accounts: dict[int, Mt5AccountRow | None] = {}

            for trade in trades:
                if self._in_cooldown(trade.id):
                    continue

                if trade.mt5_account_id not in accounts:
                    accounts[trade.mt5_account_id] = session.get(
                        Mt5AccountRow, trade.mt5_account_id
                    )
                account = accounts[trade.mt5_account_id]
                if account is None or not account.is_enabled:
                    continue

                try:
                    result = await position_manager.process_trade(
                        session, trade, account_row=account
                    )
                except asyncio.CancelledError:  # pragma: no cover
                    raise
                except Exception as exc:
                    self._record_failure(session, trade, exc)
                    continue

                self._failures.pop(trade.id, None)
                self._cooldown_until.pop(trade.id, None)

                if result.actions:
                    actions += len(result.actions)
                    log.info(
                        "Trade %s (%s): %s", trade.id, trade.symbol, "; ".join(result.actions)
                    )

        self.actions += actions
        return actions

    # -------------------------------------------------------------- failures
    def _in_cooldown(self, trade_id: int) -> bool:
        until = self._cooldown_until.get(trade_id)
        return until is not None and time.monotonic() < until

    def _record_failure(self, session, trade: ManagedTrade, exc: Exception) -> None:
        count = self._failures.get(trade.id, 0) + 1
        self._failures[trade.id] = count
        delay = BACKOFF_LADDER[min(count, len(BACKOFF_LADDER) - 1)]
        self._cooldown_until[trade.id] = time.monotonic() + delay
        self.last_error = str(exc)

        message = (
            f"Management pass failed ({count} consecutive): {exc}. "
            f"Retrying in {delay:.0f}s. The broker-side stop-loss remains in place."
        )
        trade.last_error = message[:1000]
        try:
            journal.record_event(
                session,
                user_id=trade.user_id,
                trade_id=trade.id,
                event_type=EventType.ERROR,
                message=message,
                payload={"failures": count, "cooldown_seconds": delay},
            )
            session.flush()
        except Exception:  # pragma: no cover - never let auditing break the loop
            session.rollback()
        log.warning("Trade %s management failed (%s): %s", trade.id, count, exc)

    def stats(self) -> dict[str, object]:
        return {
            "running": self.running,
            "cycles": self.cycles,
            "actions": self.actions,
            "trades_in_cooldown": len(self._cooldown_until),
            "last_error": self.last_error,
        }


_monitor: PositionMonitor | None = None


def get_monitor() -> PositionMonitor:
    global _monitor
    if _monitor is None:
        _monitor = PositionMonitor()
    return _monitor
