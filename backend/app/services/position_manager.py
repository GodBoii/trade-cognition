"""Post-entry position management: the TP1 -> TP2 -> TP3 engine.

Shape of the solution
---------------------
Deciding *what* to do needs database state (which rungs are still pending).
*Doing* it needs the broker.  Mixing the two produces either a chatty,
inconsistent conversation with the terminal or database writes that do not match
reality.  So each management pass is three phases:

1. **Plan** - read pending rungs from the database into immutable
   :class:`StageOrder` values.
2. **Act** - hand those orders to a single serialised MT5 visit which compares
   them against the live price, performs the partial closes and stop moves, and
   returns a :class:`ManageReport` describing exactly what happened.
3. **Record** - apply the report to the database and the audit trail.

Safety properties
-----------------
* Stops are only ever moved in the risk-reducing direction.  A stop can never be
  widened by the manager, whatever the ladder says.
* A rung is marked filled only after the broker acknowledges it.
* Volume drift (someone closed part of the position in the terminal) is detected
  and reconciled instead of being fought.
* Rungs whose share rounds to zero volume still move the stop when their target
  trades, so a position too small to scale out still gets its risk reduced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ..domain.enums import (
    CloseReason,
    EventType,
    Side,
    StageStatus,
    TradeStatus,
)
from ..domain.market import SymbolSpec
from ..db.models import ManagedTrade, Mt5AccountRow
from ..errors import NotFoundError
from ..logging_conf import get_logger
from ..mt5.gateway import Mt5Gateway
from . import accounts as accounts_service
from . import journal

log = get_logger(__name__)

#: Tolerance for volume comparisons (lots).
VOLUME_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class StageOrder:
    """A pending rung, as instruction for the broker visit."""

    key: str
    r_multiple: float
    target_price: float
    volume: float
    sl_after: float | None
    sequence: int


@dataclass(slots=True)
class StageOutcome:
    """What the broker visit actually managed to do for one rung."""

    key: str
    closed_volume: float = 0.0
    close_price: float = 0.0
    sl_applied: float | None = None
    ok: bool = True
    retcode: int = 0
    message: str = ""
    #: The target was touched between passes but price had retraced by the time
    #: the order went out, so the fill is worse than the target level.
    retraced: bool = False


@dataclass(slots=True)
class ManageReport:
    position_found: bool
    trigger_price: float = 0.0
    position_volume: float = 0.0
    position_sl: float = 0.0
    last_deal_price: float = 0.0
    realised_total: float = 0.0
    outcomes: list[StageOutcome] = field(default_factory=list)

    @property
    def acted(self) -> bool:
        return bool(self.outcomes)


@dataclass(slots=True)
class ProcessResult:
    trade_id: int
    changed: bool = False
    closed: bool = False
    actions: list[str] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def target_reached(side: Side, price: float, target: float) -> bool:
    return price >= target if side is Side.BUY else price <= target


def range_reached(side: Side, low: float, high: float, target: float) -> bool:
    """True when the price *range* traded through ``target``.

    A monitor that only samples the current price misses a target that was
    touched and retraced between two passes.  Comparing against the extreme in
    the relevant direction closes that gap.
    """
    return high >= target if side is Side.BUY else low <= target


def is_tighter_stop(side: Side, new_stop: float, current_stop: float) -> bool:
    """True when ``new_stop`` reduces risk relative to ``current_stop``."""
    if not current_stop:
        return True
    return new_stop > current_stop if side is Side.BUY else new_stop < current_stop


def pending_stage_orders(trade: ManagedTrade) -> list[StageOrder]:
    return [
        StageOrder(
            key=row.stage_key,
            r_multiple=row.r_multiple,
            target_price=row.target_price,
            volume=row.planned_volume,
            sl_after=row.sl_after,
            sequence=row.sequence,
        )
        for row in sorted(trade.stages, key=lambda s: s.sequence)
        if row.status == StageStatus.PENDING.value
    ]


# ---------------------------------------------------------------------------
# phase 2: the broker visit
# ---------------------------------------------------------------------------
def _execute_orders(
    gw: Mt5Gateway,
    *,
    ticket: int,
    side: Side,
    orders: list[StageOrder],
    magic: int,
    deviation: int,
    since: datetime | None = None,
) -> ManageReport:
    """Runs on the MT5 worker thread.  Returns a description of what happened."""
    position = gw.position(ticket)
    now = datetime.now(UTC)

    if position is None:
        deals = gw.deals(now - timedelta(days=30), now + timedelta(minutes=1), position_id=ticket)
        exits = [d for d in deals if d.entry != "in"]
        return ManageReport(
            position_found=False,
            realised_total=round(sum(d.net_profit for d in exits), 2),
            last_deal_price=exits[-1].price if exits else 0.0,
        )

    spec: SymbolSpec = gw.symbol_spec(position.symbol)
    remaining = position.volume
    current_sl = position.sl
    outcomes: list[StageOutcome] = []
    trigger_price = position.price_current

    # What did price do since the previous pass?  Used so a rung touched between
    # polls is not silently skipped.  ``None`` means the terminal could not tell
    # us, in which case only the current price counts.
    extremes: tuple[float, float] | None = None
    if since is not None and orders:
        try:
            extremes = gw.price_extremes(position.symbol, since)
        except Exception as exc:
            # Degrading to current-price comparison is acceptable; doing it
            # silently is not - that hides a monitor that has stopped seeing
            # touched targets.
            log.warning(
                "Could not read the price range for %s since %s: %s",
                position.symbol,
                since,
                exc,
            )
            extremes = None

    for order in orders:
        quote = gw.tick(position.symbol)
        trigger_price = quote.exit_price(side)

        at_target = target_reached(side, trigger_price, order.target_price)
        touched = at_target or (
            extremes is not None
            and range_reached(side, extremes[0], extremes[1], order.target_price)
        )
        if not touched:
            # Rungs are ordered by R multiple; nothing beyond this one can be hit.
            break

        outcome = StageOutcome(key=order.key, retraced=not at_target)

        # --- partial (or final) close -------------------------------------
        wanted = min(order.volume, remaining) if order.volume > 0 else 0.0
        if wanted > VOLUME_EPSILON:
            close_all = wanted >= remaining - VOLUME_EPSILON
            result = gw.close_position(
                ticket=ticket,
                volume=None if close_all else wanted,
                deviation=deviation,
                magic=magic,
                comment=f"TC {order.key}",
            )
            outcome.ok = result.ok
            outcome.retcode = result.retcode
            outcome.message = result.comment
            if result.ok:
                outcome.closed_volume = result.volume or wanted
                outcome.close_price = result.price or trigger_price
                remaining = max(0.0, remaining - outcome.closed_volume)
            else:
                outcomes.append(outcome)
                break  # do not move stops on a failed close
        else:
            outcome.message = (
                f"{order.key} target reached with no volume scheduled; stop management only."
            )

        # --- stop management ----------------------------------------------
        if remaining > VOLUME_EPSILON and order.sl_after is not None:
            proposed = spec.normalise_price(order.sl_after)
            if is_tighter_stop(side, proposed, current_sl):
                modify = gw.modify_stops(ticket=ticket, sl=proposed)
                if modify.ok:
                    outcome.sl_applied = proposed
                    current_sl = proposed
                else:
                    outcome.message = (
                        f"{outcome.message} Stop move to {proposed} was rejected: "
                        f"{modify.comment}"
                    ).strip()
            else:
                outcome.message = (
                    f"{outcome.message} Stop left at {current_sl}; the ladder value "
                    f"{proposed} would have widened risk."
                ).strip()

        outcomes.append(outcome)

        if remaining <= VOLUME_EPSILON:
            break

    deals = gw.deals(now - timedelta(days=30), now + timedelta(minutes=1), position_id=ticket)
    exits = [d for d in deals if d.entry != "in"]

    return ManageReport(
        position_found=True,
        trigger_price=trigger_price,
        position_volume=remaining,
        position_sl=current_sl,
        realised_total=round(sum(d.net_profit for d in exits), 2),
        last_deal_price=exits[-1].price if exits else 0.0,
        outcomes=outcomes,
    )


# ---------------------------------------------------------------------------
# phase 1 + 3
# ---------------------------------------------------------------------------
async def process_trade(
    session: Session,
    trade: ManagedTrade,
    *,
    account_row: Mt5AccountRow | None = None,
) -> ProcessResult:
    """Advance one managed trade by at most one full ladder pass."""
    result = ProcessResult(trade_id=trade.id)

    if not trade.is_active:
        return result
    if trade.position_ticket is None:
        return result

    account = account_row or session.get(Mt5AccountRow, trade.mt5_account_id)
    if account is None:
        trade.last_error = "The MT5 account for this trade has been disconnected."
        journal.record_event(
            session,
            user_id=trade.user_id,
            trade_id=trade.id,
            event_type=EventType.ERROR,
            message=trade.last_error,
        )
        result.error = trade.last_error
        return result

    from ..config import settings

    orders = pending_stage_orders(trade)
    client = accounts_service.client_for(account)

    # Look back to the previous pass (or the fill, on the first pass) so a target
    # touched between polls still counts.
    since = trade.last_checked_at or trade.opened_at
    checked_at = datetime.now(UTC)

    report = await client.run(
        lambda gw: _execute_orders(
            gw,
            ticket=int(trade.position_ticket),
            side=trade.side_enum,
            orders=orders,
            magic=settings.mt5_magic,
            deviation=settings.mt5_deviation_points,
            since=since,
        ),
        label="manage_position",
    )
    trade.last_checked_at = checked_at

    if not report.position_found:
        _handle_disappeared(session, trade, report)
        result.changed = True
        result.closed = True
        result.actions.append("position closed at broker; trade reconciled")
        return result

    previous_realised = trade.realised_pl
    stages_by_key = {row.stage_key: row for row in trade.stages}
    closed_total = sum(o.closed_volume for o in report.outcomes if o.ok)
    realised_delta = report.realised_total - previous_realised

    for outcome in report.outcomes:
        row = stages_by_key.get(outcome.key)
        if row is None:  # pragma: no cover - stages always exist
            continue
        row.attempts += 1

        if not outcome.ok:
            row.status = StageStatus.FAILED.value
            row.note = outcome.message[:1000]
            trade.last_error = f"{outcome.key}: {outcome.message}"[:1000]
            journal.record_event(
                session,
                user_id=trade.user_id,
                trade_id=trade.id,
                event_type=EventType.ERROR,
                message=f"{outcome.key} could not be executed: {outcome.message}",
                payload={"retcode": outcome.retcode, "stage": outcome.key},
            )
            result.actions.append(f"{outcome.key} failed: {outcome.message}")
            continue

        share = (outcome.closed_volume / closed_total) if closed_total > 0 else 0.0
        row.status = StageStatus.FILLED.value
        row.executed_volume = outcome.closed_volume
        row.realised_pl = round(realised_delta * share, 2)
        row.executed_at = datetime.now(UTC)
        if outcome.sl_applied is not None:
            row.sl_after = outcome.sl_applied

        result.changed = True

        if outcome.closed_volume > 0:
            retrace_note = (
                f" The target was touched between monitoring passes and price had "
                f"retraced to {outcome.close_price:g} by the time the order went out, "
                f"so this fill is worse than the {row.target_price:g} level."
                if outcome.retraced
                else ""
            )
            journal.record_event(
                session,
                user_id=trade.user_id,
                trade_id=trade.id,
                event_type=EventType.PARTIAL_CLOSE,
                message=(
                    f"{outcome.key} hit at {outcome.close_price:g}: closed "
                    f"{outcome.closed_volume:g} lots ({row.r_multiple:g}R), booking "
                    f"{row.realised_pl:,.2f} {trade.account_currency}.{retrace_note}"
                ),
                payload={
                    "stage": outcome.key,
                    "volume": outcome.closed_volume,
                    "price": outcome.close_price,
                    "target_price": row.target_price,
                    "retraced": outcome.retraced,
                    "realised": row.realised_pl,
                    "r_multiple": row.r_multiple,
                },
            )
            result.actions.append(
                f"{outcome.key}: closed {outcome.closed_volume:g} lots at {outcome.close_price:g}"
            )

        if outcome.sl_applied is not None:
            journal.record_event(
                session,
                user_id=trade.user_id,
                trade_id=trade.id,
                event_type=EventType.SL_MODIFIED,
                message=(
                    f"Stop moved to {outcome.sl_applied:g} after {outcome.key} "
                    f"({_describe_stop(trade, outcome.sl_applied)})."
                ),
                payload={"stage": outcome.key, "stop": outcome.sl_applied},
            )
            result.actions.append(f"{outcome.key}: stop moved to {outcome.sl_applied:g}")

        if outcome.message and outcome.closed_volume <= 0 and outcome.sl_applied is None:
            row.note = outcome.message[:1000]

    # --- reconcile position state -----------------------------------------
    drift = trade.remaining_volume - closed_total - report.position_volume
    if abs(drift) > VOLUME_EPSILON:
        journal.record_event(
            session,
            user_id=trade.user_id,
            trade_id=trade.id,
            event_type=EventType.SYNC,
            message=(
                f"Position volume differs from the platform's record by {drift:+.2f} lots; "
                f"adopting the broker figure of {report.position_volume:g} lots."
            ),
            payload={"expected": trade.remaining_volume - closed_total, "actual": report.position_volume},
        )
        result.changed = True

    trade.remaining_volume = report.position_volume
    trade.realised_pl = report.realised_total
    if report.position_sl:
        trade.current_stop = report.position_sl

    if report.position_volume <= VOLUME_EPSILON:
        _finalise(session, trade, CloseReason.LADDER_COMPLETE)
        result.closed = True
        result.changed = True
    elif any(row.status == StageStatus.FILLED.value for row in trade.stages):
        trade.status = TradeStatus.SCALING.value

    session.flush()
    return result


def _describe_stop(trade: ManagedTrade, stop: float) -> str:
    """Human phrasing for where a new stop sits relative to entry."""
    if not trade.entry_price:
        return "new level"
    sign = trade.side_enum.sign
    delta = (stop - trade.entry_price) * sign
    if abs(delta) < 1e-12:
        return "breakeven"
    if delta > 0:
        return f"{delta / trade.risk_distance:.2f}R in profit" if trade.risk_distance else "in profit"
    return (
        f"{abs(delta) / trade.risk_distance:.2f}R of risk remaining"
        if trade.risk_distance
        else "still at risk"
    )


def _handle_disappeared(session: Session, trade: ManagedTrade, report: ManageReport) -> None:
    """The broker has no such position any more: work out why and close the record."""
    reason = _infer_close_reason(trade, report.last_deal_price)
    already_booked = sum(
        row.realised_pl for row in trade.stages if row.status == StageStatus.FILLED.value
    )
    trade.realised_pl = report.realised_total

    # The broker-side take-profit sits at the final rung, so it often fires
    # before the monitor's next pass.  When the exit price matches a rung that
    # was still pending, credit that rung rather than recording it as skipped -
    # the target genuinely was reached.
    matched = (
        _match_pending_stage(trade, report.last_deal_price)
        if reason is CloseReason.TAKE_PROFIT
        else None
    )

    for row in trade.stages:
        if row.status != StageStatus.PENDING.value:
            continue
        if matched is not None and row.stage_key == matched.stage_key:
            row.status = StageStatus.FILLED.value
            row.executed_volume = trade.remaining_volume
            row.realised_pl = round(report.realised_total - already_booked, 2)
            row.executed_at = datetime.now(UTC)
            row.note = "Filled by the broker-side take-profit at this level."
        else:
            row.status = StageStatus.SKIPPED.value
            row.note = "Position closed before this target was reached."

    journal.record_event(
        session,
        user_id=trade.user_id,
        trade_id=trade.id,
        event_type=(
            EventType.STOP_HIT if reason is CloseReason.STOP_LOSS else EventType.POSITION_CLOSED
        ),
        message=(
            f"Position #{trade.position_ticket} on {trade.symbol} is closed at the broker "
            f"({reason.value.replace('_', ' ')}). Realised {report.realised_total:,.2f} "
            f"{trade.account_currency}."
        ),
        payload={
            "reason": reason.value,
            "realised": report.realised_total,
            "last_price": report.last_deal_price,
        },
    )
    _finalise(session, trade, reason)
    session.flush()


def _match_pending_stage(trade: ManagedTrade, price: float):
    """The pending rung whose target matches ``price``, if any."""
    if not price:
        return None
    tolerance = max(abs(trade.risk_distance) * 0.05, 1e-9)
    for row in sorted(trade.stages, key=lambda s: s.sequence):
        if row.status != StageStatus.PENDING.value:
            continue
        if row.target_price and abs(price - row.target_price) <= tolerance:
            return row
    return None


def _infer_close_reason(trade: ManagedTrade, last_price: float) -> CloseReason:
    """Best-effort attribution of a close we did not perform ourselves.

    The broker does not tell us *why* a position vanished, so the exit price is
    matched against the levels we know about.  ``EXTERNAL`` is the honest answer
    when nothing matches - it means the user or the broker acted outside the
    platform.
    """
    pending = [s for s in trade.stages if s.status == StageStatus.PENDING.value]
    if not pending:
        return CloseReason.LADDER_COMPLETE

    tolerance = max(abs(trade.risk_distance) * 0.05, 1e-9)

    if last_price and trade.current_stop and abs(last_price - trade.current_stop) <= tolerance:
        return CloseReason.STOP_LOSS

    if last_price:
        for stage in trade.stages:
            if stage.target_price and abs(last_price - stage.target_price) <= tolerance:
                return CloseReason.TAKE_PROFIT

    return CloseReason.EXTERNAL


def _finalise(session: Session, trade: ManagedTrade, reason: CloseReason) -> None:
    trade.mark_closed(reason)
    journal.record_event(
        session,
        user_id=trade.user_id,
        trade_id=trade.id,
        event_type=EventType.POSITION_CLOSED,
        message=(
            f"Trade on {trade.symbol} completed ({reason.value.replace('_', ' ')}). "
            f"Realised {trade.realised_pl:,.2f} {trade.account_currency} against a planned risk "
            f"of {trade.planned_risk:,.2f}."
        ),
        payload={
            "reason": reason.value,
            "realised": trade.realised_pl,
            "planned_risk": trade.planned_risk,
            "r_multiple": (
                round(trade.realised_pl / trade.planned_risk, 3) if trade.planned_risk else None
            ),
        },
    )
    session.flush()


# ---------------------------------------------------------------------------
# manual operations
# ---------------------------------------------------------------------------
async def close_trade(
    session: Session,
    trade: ManagedTrade,
    *,
    volume: float | None = None,
    reason: CloseReason = CloseReason.MANUAL,
) -> ProcessResult:
    """Close all or part of a managed trade at market, on the user's instruction."""
    result = ProcessResult(trade_id=trade.id)

    if not trade.is_active or trade.position_ticket is None:
        raise NotFoundError("This trade has no open position to close.")

    account = session.get(Mt5AccountRow, trade.mt5_account_id)
    if account is None:
        raise NotFoundError("The MT5 account for this trade is no longer connected.")

    from ..config import settings

    ticket = int(trade.position_ticket)
    client = accounts_service.client_for(account)

    def work(gw: Mt5Gateway):
        order = gw.close_position(
            ticket=ticket,
            volume=volume,
            deviation=settings.mt5_deviation_points,
            magic=settings.mt5_magic,
            comment="TC manual close",
        )
        now = datetime.now(UTC)
        deals = gw.deals(now - timedelta(days=30), now + timedelta(minutes=1), position_id=ticket)
        remaining = gw.position(ticket)
        return order, [d for d in deals if d.entry != "in"], remaining

    order, exits, remaining_position = await client.run(work, label="close_trade")

    if not order.ok:
        trade.last_error = order.comment[:1000]
        journal.record_event(
            session,
            user_id=trade.user_id,
            trade_id=trade.id,
            event_type=EventType.ERROR,
            message=f"Manual close was rejected: {order.comment}",
            payload={"retcode": order.retcode},
        )
        result.error = order.comment
        session.flush()
        return result

    trade.realised_pl = round(sum(d.net_profit for d in exits), 2)
    trade.remaining_volume = remaining_position.volume if remaining_position else 0.0
    result.changed = True
    result.actions.append(f"closed {order.volume:g} lots at {order.price:g}")

    journal.record_event(
        session,
        user_id=trade.user_id,
        trade_id=trade.id,
        event_type=EventType.MANUAL_CLOSE,
        message=(
            f"Manually closed {order.volume:g} lots of {trade.symbol} at {order.price:g}. "
            f"Realised to date {trade.realised_pl:,.2f} {trade.account_currency}."
        ),
        payload={"volume": order.volume, "price": order.price},
    )

    if trade.remaining_volume <= VOLUME_EPSILON:
        for row in trade.stages:
            if row.status == StageStatus.PENDING.value:
                row.status = StageStatus.SKIPPED.value
                row.note = "Position closed manually before this target."
        _finalise(session, trade, reason)
        result.closed = True

    session.flush()
    return result


async def sync_trade(session: Session, trade: ManagedTrade) -> ProcessResult:
    """Reconcile a trade against the broker without executing any ladder rung."""
    result = ProcessResult(trade_id=trade.id)
    if trade.position_ticket is None:
        return result

    account = session.get(Mt5AccountRow, trade.mt5_account_id)
    if account is None:
        raise NotFoundError("The MT5 account for this trade is no longer connected.")

    client = accounts_service.client_for(account)
    ticket = int(trade.position_ticket)

    report = await client.run(
        lambda gw: _execute_orders(
            gw, ticket=ticket, side=trade.side_enum, orders=[], magic=0, deviation=0
        ),
        label="sync_trade",
    )

    if not report.position_found:
        if trade.is_active:
            _handle_disappeared(session, trade, report)
            result.closed = True
            result.changed = True
        return result

    if abs(report.position_volume - trade.remaining_volume) > VOLUME_EPSILON:
        result.actions.append(
            f"volume {trade.remaining_volume:g} -> {report.position_volume:g}"
        )
        trade.remaining_volume = report.position_volume
        result.changed = True

    if report.position_sl and abs(report.position_sl - trade.current_stop) > 1e-12:
        result.actions.append(f"stop {trade.current_stop:g} -> {report.position_sl:g}")
        trade.current_stop = report.position_sl
        result.changed = True

    if abs(report.realised_total - trade.realised_pl) > 0.005:
        trade.realised_pl = report.realised_total
        result.changed = True

    if result.changed:
        journal.record_event(
            session,
            user_id=trade.user_id,
            trade_id=trade.id,
            event_type=EventType.SYNC,
            message="Reconciled with the broker: " + "; ".join(result.actions),
            payload={"actions": result.actions},
        )
    session.flush()
    return result
