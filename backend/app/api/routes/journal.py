"""Audit trail and performance statistics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ...errors import NotFoundError
from ...db.models import DecisionRow
from ...services import journal as journal_service
from .. import schemas
from ..deps import CurrentUser, DbSession

router = APIRouter(prefix="/journal", tags=["journal"])


@router.get("/events", response_model=list[schemas.TradeEventResponse])
async def events(
    user: CurrentUser,
    session: DbSession,
    trade_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Everything the platform did, newest first."""
    rows = journal_service.list_events(
        session, user_id=user.id, trade_id=trade_id, limit=limit, offset=offset
    )
    return [schemas.TradeEventResponse.model_validate(r) for r in rows]


@router.get("/decisions", response_model=list[schemas.DecisionResponse])
async def decisions(
    user: CurrentUser,
    session: DbSession,
    approved: bool | None = None,
    symbol: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Every pre-trade validation, including rejections that never reached the broker."""
    rows = journal_service.list_decisions(
        session,
        user_id=user.id,
        approved=approved,
        symbol=symbol,
        limit=limit,
        offset=offset,
    )
    return [schemas.DecisionResponse.model_validate(r) for r in rows]


@router.get("/decisions/{decision_id}", response_model=schemas.DecisionDetailResponse)
async def decision_detail(decision_id: int, user: CurrentUser, session: DbSession):
    """The full stored plan and every rule check for one decision."""
    row = session.get(DecisionRow, decision_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError(f"Decision {decision_id} was not found.")
    return schemas.DecisionDetailResponse.model_validate(row)


@router.get("/performance", response_model=schemas.PerformanceResponse)
async def performance(
    user: CurrentUser,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=3650)] = 30,
):
    """Headline statistics, including how often the rules had to intervene."""
    return schemas.PerformanceResponse.model_validate(
        journal_service.performance_summary(session, user_id=user.id, days=days)
    )
