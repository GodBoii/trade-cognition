"""Audit trail: trade events and pre-trade decisions.

Two append-only records:

* :class:`~app.db.models.TradeEventRow` - what the platform *did* (order sent,
  stage filled, stop moved, position closed).
* :class:`~app.db.models.DecisionRow` - what the platform *decided* before
  acting, including every rejected attempt and the numbers behind it.

Together they answer "why is this position the size it is, and why was that
other trade refused?" months after the fact.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.enums import EventType, TradeStatus
from ..domain.risk import TradePlan, plan_to_dict
from ..domain.rules import RulesReport
from ..db.models import DecisionRow, ManagedTrade, TradeEventRow
from ..logging_conf import get_logger

log = get_logger(__name__)

MAX_JOURNAL_PAGE = 500


def record_event(
    session: Session,
    *,
    user_id: int,
    event_type: EventType | str,
    message: str,
    trade_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> TradeEventRow:
    """Append an event.  Never raises on payload problems - audit must not break flow."""
    kind = event_type.value if isinstance(event_type, EventType) else str(event_type)
    try:
        safe_payload = _jsonable(payload or {})
    except Exception:  # pragma: no cover - defensive
        safe_payload = {"unserialisable": True}

    row = TradeEventRow(
        user_id=user_id,
        trade_id=trade_id,
        event_type=kind,
        message=message[:2000],
        payload=safe_payload,
    )
    session.add(row)
    log.info("[user %s][trade %s] %s: %s", user_id, trade_id, kind, message)
    return row


def record_decision(
    session: Session,
    *,
    user_id: int,
    mt5_account_id: int | None,
    plan: TradePlan,
    report: RulesReport,
    executed: bool = False,
    trade_id: int | None = None,
) -> DecisionRow:
    """Persist a pre-trade validation, approved or not."""
    row = DecisionRow(
        user_id=user_id,
        mt5_account_id=mt5_account_id,
        trade_id=trade_id,
        symbol=plan.symbol,
        side=plan.side.value,
        approved=report.approved,
        executed=executed,
        volume=plan.volume,
        entry_price=plan.entry_price,
        stop_loss=plan.stop_loss,
        max_loss=round(plan.max_loss, 2),
        risk_pct=round(plan.risk_pct_of_capital, 4),
        expected_profit=round(plan.expected_profit, 2),
        reward_risk=round(plan.reward_risk_blended, 4),
        plan=_jsonable(plan_to_dict(plan)),
        checks=[_jsonable(asdict(c)) for c in report.checks],
        violation_codes=",".join(v.code for v in report.violations)[:255],
        summary=(report.rejection_summary or "Approved")[:2000],
    )
    session.add(row)
    return row


def list_events(
    session: Session,
    *,
    user_id: int,
    trade_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[TradeEventRow]:
    stmt = select(TradeEventRow).where(TradeEventRow.user_id == user_id)
    if trade_id is not None:
        stmt = stmt.where(TradeEventRow.trade_id == trade_id)
    stmt = (
        stmt.order_by(TradeEventRow.id.desc())
        .limit(min(limit, MAX_JOURNAL_PAGE))
        .offset(max(offset, 0))
    )
    return list(session.scalars(stmt))


def list_decisions(
    session: Session,
    *,
    user_id: int,
    approved: bool | None = None,
    symbol: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[DecisionRow]:
    stmt = select(DecisionRow).where(DecisionRow.user_id == user_id)
    if approved is not None:
        stmt = stmt.where(DecisionRow.approved == approved)
    if symbol:
        stmt = stmt.where(DecisionRow.symbol == symbol.upper())
    stmt = (
        stmt.order_by(DecisionRow.id.desc())
        .limit(min(limit, MAX_JOURNAL_PAGE))
        .offset(max(offset, 0))
    )
    return list(session.scalars(stmt))


def realised_pnl_today(session: Session, *, user_id: int, account_id: int | None = None) -> float:
    """Sum of realised P/L on trades closed since midnight UTC."""
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = select(func.coalesce(func.sum(ManagedTrade.realised_pl), 0.0)).where(
        ManagedTrade.user_id == user_id,
        ManagedTrade.closed_at.is_not(None),
        ManagedTrade.closed_at >= start,
    )
    if account_id is not None:
        stmt = stmt.where(ManagedTrade.mt5_account_id == account_id)
    return float(session.scalar(stmt) or 0.0)


def performance_summary(
    session: Session, *, user_id: int, days: int = 30
) -> dict[str, Any]:
    """Headline statistics for the dashboard."""
    since = datetime.now(UTC) - timedelta(days=max(days, 1))

    closed = list(
        session.scalars(
            select(ManagedTrade).where(
                ManagedTrade.user_id == user_id,
                ManagedTrade.status == TradeStatus.CLOSED.value,
                ManagedTrade.closed_at.is_not(None),
                ManagedTrade.closed_at >= since,
            )
        )
    )
    wins = [t for t in closed if t.realised_pl > 0]
    losses = [t for t in closed if t.realised_pl < 0]
    gross_win = sum(t.realised_pl for t in wins)
    gross_loss = -sum(t.realised_pl for t in losses)

    decisions = session.execute(
        select(DecisionRow.approved, func.count())
        .where(DecisionRow.user_id == user_id, DecisionRow.created_at >= since)
        .group_by(DecisionRow.approved)
    ).all()
    approved_count = next((int(c) for a, c in decisions if a), 0)
    rejected_count = next((int(c) for a, c in decisions if not a), 0)

    top_violations = session.execute(
        select(DecisionRow.violation_codes, func.count())
        .where(
            DecisionRow.user_id == user_id,
            DecisionRow.approved.is_(False),
            DecisionRow.created_at >= since,
        )
        .group_by(DecisionRow.violation_codes)
        .order_by(func.count().desc())
        .limit(5)
    ).all()

    return {
        "window_days": days,
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
        "net_pl": round(sum(t.realised_pl for t in closed), 2),
        "gross_profit": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "average_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "average_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
        "decisions_approved": approved_count,
        "decisions_rejected": rejected_count,
        "rule_adherence_pct": (
            round(approved_count / (approved_count + rejected_count) * 100, 2)
            if (approved_count + rejected_count)
            else 100.0
        ),
        "top_rejections": [
            {"codes": codes or "unknown", "count": int(count)} for codes, count in top_violations
        ],
    }


def _jsonable(value: Any) -> Any:
    """Recursively coerce dataclasses / enums / datetimes into JSON primitives."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value") and hasattr(value, "name"):  # Enum
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    return str(value)


#: Public alias - other services serialise domain objects for JSON columns.
jsonable = _jsonable
