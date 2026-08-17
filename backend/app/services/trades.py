"""Trade submission and lifecycle.

Ordering matters here, so the sequence is spelled out:

1. **Assess** - one serialised MT5 visit produces the plan and the rules verdict.
2. **Journal the decision** - approved or not, before anything is sent.
3. **Claim the Rule 1 lock** - insert the ``ManagedTrade`` row *before* the order
   goes out.  The unique constraint on ``(user_id, active_key)`` is what makes
   "one active trade per derivative" hold under concurrent requests; winning the
   insert is the permission slip to trade.
4. **Send the order** - with the stop attached to the entry request, so the
   position is never naked, plus a take-profit at the final rung as a failsafe
   in case this process dies.
5. **Re-plan at the fill** - the ladder is rebuilt from the *actual* fill price
   so every R multiple is measured from where the trade really started.
6. **Persist the rungs** and record what happened.

If the order is rejected the lock is released immediately, so a broker refusal
does not leave a symbol permanently blocked.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..db.models import DecisionRow, ManagedTrade, Mt5AccountRow, TradeStageRow, User
from ..domain.enums import EventType, Severity, StageStatus, TradeStatus
from ..domain.market import OrderResult, PositionSnapshot
from ..domain.risk import TradeIntent, TradePlan, plan_to_dict
from ..domain.rules import RuleCheck, RulesReport
from ..errors import ConflictError, NotFoundError
from ..logging_conf import get_logger
from ..mt5.gateway import Mt5Gateway
from . import accounts as accounts_service
from . import calculator, journal, position_manager

log = get_logger(__name__)


@dataclass(slots=True)
class SubmissionResult:
    """Outcome of ``POST /api/trades``."""

    approved: bool
    executed: bool
    message: str
    assessment: calculator.Assessment
    trade: ManagedTrade | None = None
    #: Plan rebuilt from the actual fill price (``None`` when nothing executed).
    fill_plan: TradePlan | None = None
    order: OrderResult | None = None


async def submit(
    session: Session,
    user: User,
    account_row: Mt5AccountRow,
    intent: TradeIntent,
    *,
    override: bool = False,
) -> SubmissionResult:
    """Validate and, if the rules allow, execute a trade."""
    assessment = await calculator.assess(
        session, user, account_row, intent, override=override, record=False
    )
    plan = assessment.plan
    report = assessment.report

    decision = journal.record_decision(
        session,
        user_id=user.id,
        mt5_account_id=account_row.id,
        plan=plan,
        report=report,
    )
    session.flush()

    # ---------------------------------------------------------------- rejected
    if not report.approved:
        journal.record_event(
            session,
            user_id=user.id,
            event_type=EventType.REJECTED,
            message=f"{plan.symbol} {plan.side.value} rejected: {report.rejection_summary}",
            payload={
                "symbol": plan.symbol,
                "violations": [v.code for v in report.violations],
                "decision_id": decision.id,
            },
        )
        session.commit()
        return SubmissionResult(
            approved=False,
            executed=False,
            message=report.rejection_summary,
            assessment=assessment,
        )

    # ------------------------------------------------- claim the Rule 1 lock
    trade = _create_trade_row(session, user, account_row, plan, report, intent)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = calculator.find_active_trade(session, user, plan.symbol)
        raise ConflictError(
            f"Rule 1: another active entry for {plan.symbol} was created a moment ago"
            + (f" (trade #{existing.id})." if existing else ".")
            + " Only one live position per derivative is permitted.",
            code="rule1_conflict",
        ) from None

    journal.record_event(
        session,
        user_id=user.id,
        trade_id=trade.id,
        event_type=EventType.VALIDATED,
        message=(
            f"Approved {plan.symbol} {plan.side.value} {plan.volume:g} lots. Risk "
            f"{plan.max_loss:,.2f} {plan.account_currency} ({plan.risk_pct_of_capital:.2f}% of "
            f"{plan.capital:,.2f}), targets "
            + " / ".join(
                f"{s.key} {s.target_price:.{plan.digits}f}" for s in plan.executing_stages
            )
            + "."
        ),
        payload={"decision_id": decision.id, "plan": journal.jsonable(plan_to_dict(plan))},
    )
    decision.trade_id = trade.id
    session.flush()

    # -------------------------------------------------------------- execution
    failsafe_tp = plan.final_target
    client = accounts_service.client_for(account_row)

    def work(gw: Mt5Gateway):
        order = gw.open_position(
            symbol=plan.symbol,
            side=plan.side,
            volume=plan.volume,
            sl=plan.stop_loss,
            tp=failsafe_tp,
            deviation=settings.mt5_deviation_points,
            magic=settings.mt5_magic,
            comment=(intent.comment or "TradeCognition")[:31],
        )
        if not order.ok:
            return order, None, None

        position = gw.position(order.position or order.order)
        fill_plan: TradePlan | None = None

        if position is not None:
            filled_intent = replace(
                intent,
                entry_price=position.price_open,
                sl_price=plan.stop_loss,
                sl_points=None,
                volume=position.volume,
            )
            try:
                fill_plan = _recalculate_at_fill(gw, filled_intent, assessment)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("Could not re-plan at fill price: %s", exc)

            if fill_plan is not None:
                gw.modify_stops(
                    ticket=position.ticket,
                    sl=fill_plan.stop_loss,
                    tp=fill_plan.final_target,
                )

        return order, position, fill_plan

    order, position, fill_plan = await client.run(work, label="open_position")

    # ------------------------------------------------------- order rejected
    if not order.ok:
        trade.status = TradeStatus.ERROR.value
        trade.active_key = None  # release the lock: nothing is open
        trade.last_error = order.comment[:1000]
        journal.record_event(
            session,
            user_id=user.id,
            trade_id=trade.id,
            event_type=EventType.ORDER_FAILED,
            message=f"The broker rejected the order: {order.comment}",
            payload={"retcode": order.retcode},
        )
        session.commit()
        return SubmissionResult(
            approved=True,
            executed=False,
            message=f"Trade approved by the rules but rejected by the broker: {order.comment}",
            assessment=assessment,
            trade=trade,
            order=order,
        )

    # ------------------------------------------------------------- filled
    effective_plan = fill_plan or plan
    _apply_fill(trade, plan=effective_plan, order=order, position=position)
    _create_stage_rows(session, trade, effective_plan)

    decision.executed = True
    journal.record_event(
        session,
        user_id=user.id,
        trade_id=trade.id,
        event_type=EventType.ORDER_FILLED,
        message=(
            f"Filled {trade.initial_volume:g} lots of {trade.symbol} at "
            f"{trade.entry_price:.{effective_plan.digits}f} (position #{trade.position_ticket}). "
            f"Stop {trade.initial_stop:.{effective_plan.digits}f}, failsafe take-profit "
            f"{effective_plan.final_target:.{effective_plan.digits}f}."
        ),
        payload={
            "ticket": trade.position_ticket,
            "requested_entry": plan.entry_price,
            "fill_price": trade.entry_price,
            "slippage_points": _slippage_points(plan, trade.entry_price),
        },
    )

    _warn_on_slippage(session, trade, requested=plan, filled=effective_plan)
    session.commit()

    return SubmissionResult(
        approved=True,
        executed=True,
        message=(
            f"{trade.symbol} {trade.side} {trade.initial_volume:g} lots filled at "
            f"{trade.entry_price:.{effective_plan.digits}f}."
        ),
        assessment=assessment,
        trade=trade,
        fill_plan=fill_plan,
        order=order,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _slippage_points(plan: TradePlan, fill_price: float) -> float:
    """Difference between the quoted entry and the fill, in symbol points."""
    if not plan.risk_points:
        return 0.0
    point = plan.risk_distance / plan.risk_points
    if point <= 0:
        return 0.0
    return round(abs(fill_price - plan.entry_price) / point, 1)


def _recalculate_at_fill(
    gw: Mt5Gateway, filled_intent: TradeIntent, assessment: calculator.Assessment
) -> TradePlan:
    """Rebuild the plan from the real fill price (runs on the MT5 thread)."""
    from ..domain.risk import calculate_trade_plan

    symbol = filled_intent.symbol
    spec = gw.symbol_spec(symbol)
    return calculate_trade_plan(
        filled_intent,
        spec=spec,
        tick=gw.tick(spec.name),
        account=gw.account(),
        profile=assessment.profile,
        money_fn=lambda side, volume, o, c: gw.calc_profit(side, spec.name, volume, o, c),
        margin_fn=lambda side, volume, p: gw.calc_margin(side, spec.name, volume, p),
        ladder=assessment.ladder,
    )


def _create_trade_row(
    session: Session,
    user: User,
    account_row: Mt5AccountRow,
    plan: TradePlan,
    report: RulesReport,
    intent: TradeIntent,
) -> ManagedTrade:
    trade = ManagedTrade(
        user_id=user.id,
        mt5_account_id=account_row.id,
        symbol=plan.symbol,
        side=plan.side.value,
        order_kind=plan.order_kind.value,
        status=TradeStatus.PENDING.value,
        active_key=ManagedTrade.build_active_key(account_row.id, plan.symbol),
        requested_entry=plan.entry_price,
        initial_stop=plan.stop_loss,
        current_stop=plan.stop_loss,
        risk_distance=plan.risk_distance,
        initial_volume=plan.volume,
        remaining_volume=plan.volume,
        ladder_preset=plan.ladder_preset,
        capital_at_entry=plan.capital,
        planned_risk=round(plan.max_loss, 2),
        planned_risk_pct=round(plan.risk_pct_of_capital, 4),
        planned_profit=round(plan.expected_profit, 2),
        account_currency=plan.account_currency,
        comment=(intent.comment or "")[:64],
        plan=journal.jsonable(plan_to_dict(plan)),
        rules={
            "approved": report.approved,
            "overridden": list(report.overridden),
            "checks": [journal.jsonable(_check_dict(c)) for c in report.checks],
        },
    )
    session.add(trade)
    return trade


def _check_dict(check: RuleCheck) -> dict[str, object]:
    return {
        "code": check.code,
        "rule": check.rule,
        "passed": check.passed,
        "severity": check.severity.value
        if isinstance(check.severity, Severity)
        else check.severity,
        "message": check.message,
        "overridable": check.overridable,
        "details": check.details,
    }


def _apply_fill(
    trade: ManagedTrade,
    *,
    plan: TradePlan,
    order: OrderResult,
    position: PositionSnapshot | None,
) -> None:
    trade.status = TradeStatus.OPEN.value
    trade.position_ticket = position.ticket if position else (order.position or order.order)
    trade.entry_deal = order.deal or None
    trade.entry_price = position.price_open if position else (order.price or plan.entry_price)
    trade.initial_volume = position.volume if position else (order.volume or plan.volume)
    trade.remaining_volume = trade.initial_volume
    trade.initial_stop = plan.stop_loss
    trade.current_stop = plan.stop_loss
    trade.risk_distance = plan.risk_distance
    trade.planned_risk = round(plan.max_loss, 2)
    trade.planned_risk_pct = round(plan.risk_pct_of_capital, 4)
    trade.planned_profit = round(plan.expected_profit, 2)
    trade.plan = journal.jsonable(plan_to_dict(plan))
    trade.opened_at = position.opened_at if (position and position.opened_at) else datetime.now(UTC)


def _create_stage_rows(session: Session, trade: ManagedTrade, plan: TradePlan) -> None:
    """Materialise the ladder.

    Every rung gets a row, including rungs with zero volume: reaching their
    target still triggers the stop-management action, which is how a position too
    small to scale out still has its risk reduced.
    """
    for index, stage in enumerate(plan.stages):
        session.add(
            TradeStageRow(
                trade_id=trade.id,
                sequence=index,
                stage_key=stage.key,
                r_multiple=stage.r_multiple,
                target_price=stage.target_price,
                planned_volume=stage.volume,
                sl_action=stage.sl_action.value,
                sl_after=stage.sl_after,
                planned_profit=round(stage.money_profit, 2),
                status=StageStatus.PENDING.value,
                note=stage.note,
            )
        )
    session.flush()


def _warn_on_slippage(
    session: Session, trade: ManagedTrade, *, requested: TradePlan, filled: TradePlan
) -> None:
    """Record a warning when the fill moved risk outside the configured ceiling."""
    if filled.max_loss <= filled.max_risk_money + 0.005:
        return
    journal.record_event(
        session,
        user_id=trade.user_id,
        trade_id=trade.id,
        event_type=EventType.ERROR,
        message=(
            f"Fill slippage widened the stop distance: risk at the stop is now "
            f"{filled.max_loss:,.2f} {filled.account_currency} "
            f"({filled.risk_pct_of_capital:.2f}% of capital) against the "
            f"{filled.max_risk_pct:.2f}% ceiling of {filled.max_risk_money:,.2f}. The position "
            f"was accepted at {requested.max_loss:,.2f}. Consider closing and re-entering."
        ),
        payload={
            "approved_risk": round(requested.max_loss, 2),
            "actual_risk": round(filled.max_loss, 2),
            "ceiling": round(filled.max_risk_money, 2),
        },
    )


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------
def list_trades(
    session: Session,
    user: User,
    *,
    status: str | None = None,
    symbol: str | None = None,
    active_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[ManagedTrade]:
    stmt = select(ManagedTrade).where(ManagedTrade.user_id == user.id)
    if active_only:
        stmt = stmt.where(ManagedTrade.status.in_([s.value for s in TradeStatus.active()]))
    elif status:
        stmt = stmt.where(ManagedTrade.status == status)
    if symbol:
        stmt = stmt.where(ManagedTrade.symbol == symbol.upper())
    stmt = stmt.order_by(ManagedTrade.id.desc()).limit(min(limit, 500)).offset(max(offset, 0))
    return list(session.scalars(stmt))


def get_trade(session: Session, user: User, trade_id: int) -> ManagedTrade:
    trade = session.scalar(
        select(ManagedTrade).where(
            ManagedTrade.id == trade_id, ManagedTrade.user_id == user.id
        )
    )
    if trade is None:
        raise NotFoundError(f"Trade {trade_id} was not found.")
    return trade


def active_trades(session: Session, user: User) -> list[ManagedTrade]:
    return list_trades(session, user, active_only=True, limit=500)


def decisions_for_trade(session: Session, trade: ManagedTrade) -> list[DecisionRow]:
    return list(
        session.scalars(select(DecisionRow).where(DecisionRow.trade_id == trade.id))
    )


async def close(
    session: Session,
    user: User,
    trade_id: int,
    *,
    volume: float | None = None,
) -> position_manager.ProcessResult:
    trade = get_trade(session, user, trade_id)
    result = await position_manager.close_trade(session, trade, volume=volume)
    session.commit()
    return result


async def sync(session: Session, user: User, trade_id: int) -> position_manager.ProcessResult:
    trade = get_trade(session, user, trade_id)
    result = await position_manager.sync_trade(session, trade)
    session.commit()
    return result


async def manage(session: Session, user: User, trade_id: int) -> position_manager.ProcessResult:
    """Force a management pass (the monitor does this automatically)."""
    trade = get_trade(session, user, trade_id)
    result = await position_manager.process_trade(session, trade)
    session.commit()
    return result


async def positions_overview(
    session: Session, user: User, account_row: Mt5AccountRow
) -> dict[str, object]:
    """Account snapshot + live positions joined to their managed trades."""
    client = accounts_service.client_for(account_row)

    def work(gw: Mt5Gateway):
        return gw.account(), tuple(gw.positions())

    account, positions = await client.run(work, label="positions_overview")

    managed = {t.position_ticket: t for t in active_trades(session, user) if t.position_ticket}
    rows: list[dict[str, object]] = []

    for position in positions:
        trade = managed.get(position.ticket)
        rows.append(
            {
                "position": position,
                "trade": trade,
                "managed": trade is not None,
            }
        )

    unmatched = [t for t in managed.values() if all(p.ticket != t.position_ticket for p in positions)]
    return {
        "account": account,
        "rows": rows,
        "orphaned_trades": unmatched,
        "risk_on": round(
            sum(
                t.remaining_volume / t.initial_volume * t.planned_risk
                for t in managed.values()
                if t.initial_volume
            ),
            2,
        ),
    }
