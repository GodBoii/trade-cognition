"""Domain object -> response model conversions.

Most schemas validate directly from attributes; the ones here need a computed
projection (flattening a rules report, describing a ladder, joining a live
position to its managed trade).
"""

from __future__ import annotations

from ..db.models import ManagedTrade, Mt5AccountRow
from ..domain.ladder import Ladder
from ..domain.market import AccountSnapshot
from ..domain.profile import RiskProfile
from ..domain.risk import TradePlan
from ..domain.rules import RulesReport
from ..services.calculator import Assessment
from ..services.trades import SubmissionResult
from . import schemas


def ladder_info(ladder: Ladder) -> schemas.LadderInfo:
    return schemas.LadderInfo(
        preset=ladder.preset.value,
        label=ladder.label,
        description=ladder.description,
        stages=[
            schemas.LadderStageInfo(
                key=stage.key,
                r_multiple=stage.r_multiple,
                close_fraction=stage.close_fraction,
                sl_action=stage.sl_action.value,
                note=stage.note,
            )
            for stage in ladder.stages
        ],
    )


def trade_plan(plan: TradePlan) -> schemas.TradePlanResponse:
    return schemas.TradePlanResponse.model_validate(plan)


def rules_report(report: RulesReport) -> schemas.RulesReportResponse:
    return schemas.RulesReportResponse(
        approved=report.approved,
        checks=[schemas.RuleCheckResponse.model_validate(c) for c in report.checks],
        overridden=list(report.overridden),
        violations=[v.code for v in report.violations],
        summary=report.rejection_summary or "All rules satisfied.",
    )


def assessment(result: Assessment) -> schemas.AssessmentResponse:
    return schemas.AssessmentResponse(
        plan=trade_plan(result.plan),
        rules=rules_report(result.report),
        active_symbols=list(result.active_symbols),
        blocking_ticket=result.blocking_ticket,
        ladder=ladder_info(result.ladder),
    )


def trade(row: ManagedTrade) -> schemas.TradeResponse:
    return schemas.TradeResponse.model_validate(row)


def trade_detail(row: ManagedTrade, events: list) -> schemas.TradeDetailResponse:
    payload = schemas.TradeDetailResponse.model_validate(row)
    payload.events = [schemas.TradeEventResponse.model_validate(e) for e in events]
    return payload


def submission(result: SubmissionResult) -> schemas.SubmissionResponse:
    return schemas.SubmissionResponse(
        approved=result.approved,
        executed=result.executed,
        message=result.message,
        plan=trade_plan(result.assessment.plan),
        rules=rules_report(result.assessment.report),
        trade=trade(result.trade) if result.trade is not None else None,
        fill_plan=trade_plan(result.fill_plan) if result.fill_plan is not None else None,
    )


def account_state(
    account_row: Mt5AccountRow,
    snapshot: AccountSnapshot,
    profile: RiskProfile,
) -> schemas.AccountStateResponse:
    capital = profile.capital(snapshot)
    raw = profile.raw_prescribed_volume(capital)
    return schemas.AccountStateResponse(
        account=schemas.Mt5AccountResponse.model_validate(account_row),
        snapshot=schemas.AccountSnapshotResponse.model_validate(snapshot),
        capital=round(capital, 2),
        capital_basis=profile.capital_basis.value,
        prescribed_lots_hint=(
            f"{capital:,.2f} {snapshot.currency} / 1,000 x {profile.lots_per_1000} = "
            f"{raw:.4f} lots before the symbol lot step is applied"
        ),
    )


def to_intent(request: schemas.CalcRequest) -> "TradeIntent":
    """Map an API request onto the domain intent.

    The stop is validated here rather than in the domain so the caller gets a
    precise 422 instead of a generic one.
    """
    from ..domain.risk import TradeIntent
    from ..errors import ValidationError

    if not request.stop_is_defined():
        raise ValidationError(
            "A stop-loss is required. Send either 'stop_loss' (absolute price) or 'stop_points' "
            "(distance in points).",
            code="stop_required",
        )
    if request.stop_loss is not None and request.stop_points is not None:
        raise ValidationError(
            "Send either 'stop_loss' or 'stop_points', not both.", code="stop_ambiguous"
        )

    return TradeIntent(
        symbol=request.symbol,
        side=request.side,
        order_kind=request.order_kind,
        entry_price=request.entry_price,
        sl_price=request.stop_loss,
        sl_points=request.stop_points,
        volume=request.volume,
        ladder_preset=request.ladder_preset,
        comment=request.comment,
    )


def position_row(position, trade_row: ManagedTrade | None) -> schemas.PositionRow:
    return schemas.PositionRow(
        position=schemas.PositionResponse.model_validate(position),
        managed=trade_row is not None,
        trade=trade(trade_row) if trade_row is not None else None,
    )
