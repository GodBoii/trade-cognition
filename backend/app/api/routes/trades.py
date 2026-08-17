"""Trade submission, monitoring and manual intervention."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ...services import journal as journal_service
from ...services import trades as trades_service
from .. import schemas, serializers
from ..deps import CurrentUser, DbSession, ResolvedAccount

router = APIRouter(tags=["trades"])


@router.post("/trades", response_model=schemas.SubmissionResponse)
async def submit_trade(
    payload: schemas.CalcRequest,
    user: CurrentUser,
    session: DbSession,
    account: ResolvedAccount,
):
    """Validate a trade and, if every rule passes, place it on MT5.

    Always returns ``200``.  Check ``approved`` and ``executed``:

    * ``approved=false`` - a rule blocked the entry.  ``rules.violations`` lists
      the codes and ``rules.checks`` carries the full explanation.  Nothing was
      sent to the broker.
    * ``approved=true, executed=false`` - the rules passed but the broker refused
      the order; ``message`` has the reason.
    * ``approved=true, executed=true`` - the position is open and the ladder has
      been scheduled.  ``fill_plan`` holds the plan recomputed from the actual
      fill price, which is what the position manager will follow.
    """
    result = await trades_service.submit(
        session,
        user,
        account,
        serializers.to_intent(payload),
        override=payload.override,
    )
    return serializers.submission(result)


@router.get("/trades", response_model=list[schemas.TradeResponse])
async def list_trades(
    user: CurrentUser,
    session: DbSession,
    status: Annotated[str | None, Query(description="pending|open|scaling|closed|rejected|error")] = None,
    symbol: str | None = None,
    active_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows = trades_service.list_trades(
        session,
        user,
        status=status,
        symbol=symbol,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    return [serializers.trade(row) for row in rows]


@router.get("/trades/{trade_id}", response_model=schemas.TradeDetailResponse)
async def trade_detail(trade_id: int, user: CurrentUser, session: DbSession):
    """One trade with its ladder, the approved plan, the rule report and its events."""
    row = trades_service.get_trade(session, user, trade_id)
    events = journal_service.list_events(session, user_id=user.id, trade_id=trade_id, limit=200)
    return serializers.trade_detail(row, events)


@router.post("/trades/{trade_id}/close", response_model=schemas.TradeActionResponse)
async def close_trade(
    trade_id: int,
    payload: schemas.CloseTradeRequest,
    user: CurrentUser,
    session: DbSession,
):
    """Close all or part of a managed trade at market."""
    result = await trades_service.close(session, user, trade_id, volume=payload.volume)
    return _action_response(session, user, result)


@router.post("/trades/{trade_id}/sync", response_model=schemas.TradeActionResponse)
async def sync_trade(trade_id: int, user: CurrentUser, session: DbSession):
    """Reconcile the platform's record with the broker without touching the ladder."""
    result = await trades_service.sync(session, user, trade_id)
    return _action_response(session, user, result)


@router.post("/trades/{trade_id}/manage", response_model=schemas.TradeActionResponse)
async def manage_trade(trade_id: int, user: CurrentUser, session: DbSession):
    """Run one ladder pass immediately instead of waiting for the monitor."""
    result = await trades_service.manage(session, user, trade_id)
    return _action_response(session, user, result)


@router.get("/positions", response_model=schemas.PositionsOverviewResponse)
async def positions(user: CurrentUser, session: DbSession, account: ResolvedAccount):
    """Every open MT5 position, joined to its managed trade where there is one.

    ``orphaned_trades`` are managed trades whose position is missing at the
    broker - the monitor reconciles these on its next pass.  ``risk_on`` is the
    sum of remaining planned risk across managed positions.
    """
    overview = await trades_service.positions_overview(session, user, account)
    return schemas.PositionsOverviewResponse(
        account=schemas.AccountSnapshotResponse.model_validate(overview["account"]),
        rows=[
            serializers.position_row(row["position"], row["trade"])
            for row in overview["rows"]  # type: ignore[index]
        ],
        orphaned_trades=[serializers.trade(t) for t in overview["orphaned_trades"]],  # type: ignore[union-attr]
        risk_on=overview["risk_on"],  # type: ignore[arg-type]
    )


def _action_response(session, user, result) -> schemas.TradeActionResponse:
    row = trades_service.get_trade(session, user, result.trade_id)
    return schemas.TradeActionResponse(
        trade_id=result.trade_id,
        changed=result.changed,
        closed=result.closed,
        actions=result.actions,
        error=result.error,
        trade=serializers.trade(row),
    )
