"""Risk profile: the configuration the rules engine enforces."""

from __future__ import annotations

from fastapi import APIRouter

from ...domain.enums import CapitalBasis, LadderPreset, LotRuleMode
from ...domain.profile import RiskProfile
from ...services import users as users_service
from .. import schemas
from ..deps import CurrentUser, DbSession

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("/profile", response_model=schemas.RiskProfileResponse)
async def get_profile(user: CurrentUser, session: DbSession):
    return schemas.RiskProfileResponse.model_validate(users_service.get_profile(session, user))


@router.put("/profile", response_model=schemas.RiskProfileResponse)
async def update_profile(
    payload: schemas.RiskProfileUpdate, user: CurrentUser, session: DbSession
):
    """Replace the risk profile.

    Values that would defeat the platform's purpose are refused: more than 1 lot
    per 1,000 of capital, a risk ceiling above 20%, or a fixed capital basis
    without a capital figure.
    """
    profile = RiskProfile(
        lots_per_1000=payload.lots_per_1000,
        lot_rule_mode=LotRuleMode(payload.lot_rule_mode),
        max_risk_pct=payload.max_risk_pct,
        capital_basis=CapitalBasis(payload.capital_basis),
        fixed_capital=payload.fixed_capital,
        ladder_preset=LadderPreset(payload.ladder_preset),
        max_concurrent_positions=payload.max_concurrent_positions,
        max_daily_loss_pct=payload.max_daily_loss_pct,
        margin_utilisation_cap_pct=payload.margin_utilisation_cap_pct,
        require_stop_loss=payload.require_stop_loss,
        min_reward_risk=payload.min_reward_risk,
        allow_manual_override=payload.allow_manual_override,
    )
    saved = users_service.update_profile(session, user, profile)
    session.commit()
    return schemas.RiskProfileResponse.model_validate(saved)
