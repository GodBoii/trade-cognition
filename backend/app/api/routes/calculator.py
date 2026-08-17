"""Pre-entry risk and profit calculation."""

from __future__ import annotations

from fastapi import APIRouter

from ...domain.ladder import LADDERS
from ...services import calculator as calculator_service
from .. import schemas, serializers
from ..deps import CurrentUser, DbSession, ResolvedAccount

router = APIRouter(prefix="/calculator", tags=["calculator"])


@router.post("/preview", response_model=schemas.AssessmentResponse)
async def preview(
    payload: schemas.CalcRequest,
    user: CurrentUser,
    session: DbSession,
    account: ResolvedAccount,
):
    """Calculate a trade and evaluate every rule, without side effects.

    Returns the entry, stop, lot size, maximum loss, expected profit at each
    target, reward-to-risk, required margin and percentage of capital at risk,
    together with the verdict of each rule.  This is the same code path the
    execute endpoint uses, so what you see here is what will be enforced.
    """
    assessment = await calculator_service.assess(
        session,
        user,
        account,
        serializers.to_intent(payload),
        override=payload.override,
    )
    return serializers.assessment(assessment)


@router.post("/stop-scan", response_model=list[schemas.StopScanRow])
async def stop_scan(
    payload: schemas.StopScanRequest,
    user: CurrentUser,
    session: DbSession,
    account: ResolvedAccount,
):
    """Monetary risk at several candidate stop distances.

    Useful for answering "how far can my stop go before Rule 3 blocks me?" in a
    single round trip.
    """
    rows = await calculator_service.what_if_stop_scan(
        session,
        user,
        account,
        symbol=payload.symbol,
        side=payload.side,
        stop_points=payload.stop_points,
    )
    return [schemas.StopScanRow.model_validate(row) for row in rows]


@router.get("/ladders", response_model=list[schemas.LadderInfo])
async def ladders(user: CurrentUser):
    """The available profit-taking ladders and what each rung does."""
    return [serializers.ladder_info(ladder) for ladder in LADDERS.values()]
