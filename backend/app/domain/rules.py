"""The rules engine.

Every trade passes through :func:`evaluate` before it can reach the broker.  The
engine returns a *transparent* report: one entry per rule with its outcome, not
just a pass/fail flag.  That transparency is the product - the user must be able
to see exactly which discipline rule stopped them and by how much.

Rule catalogue
--------------
=========================== ==================================================
``RULE1_ONE_ACTIVE_TRADE``  One live entry per user per derivative.
``RULE2_LOT_ALLOCATION``    Volume must follow the capital-to-lot formula.
``RULE2_VOLUME_CONSTRAINTS`` Volume must satisfy broker min/max/step.
``RULE3_MAX_RISK``          Loss at the stop must not exceed the risk ceiling.
=========================== ==================================================

Everything prefixed ``GUARD_`` is a supporting safety check (margin, minimum
stop distance, market availability, portfolio limits).

Rules 1 and 3 are never overridable: they are the reason the platform exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import LotRuleMode, Severity
from .market import AccountSnapshot, SymbolSpec
from .profile import RiskProfile
from .quant import is_multiple_of, pct
from .risk import TradePlan


@dataclass(frozen=True, slots=True)
class RuleCheck:
    """Outcome of a single rule evaluation."""

    code: str
    rule: str
    passed: bool
    severity: Severity
    message: str
    overridable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return not self.passed and self.severity is Severity.BLOCK


@dataclass(frozen=True, slots=True)
class RulesReport:
    """Aggregate verdict for a proposed trade."""

    approved: bool
    checks: tuple[RuleCheck, ...]
    overridden: tuple[str, ...] = field(default_factory=tuple)

    @property
    def violations(self) -> tuple[RuleCheck, ...]:
        return tuple(c for c in self.checks if not c.passed and c.severity is Severity.BLOCK)

    @property
    def warnings(self) -> tuple[RuleCheck, ...]:
        return tuple(c for c in self.checks if not c.passed and c.severity is Severity.WARN)

    @property
    def rejection_summary(self) -> str:
        violations = self.violations
        if not violations:
            return ""
        if len(violations) == 1:
            return violations[0].message
        return " ".join(f"({i}) {v.message}" for i, v in enumerate(violations, start=1))


@dataclass(frozen=True, slots=True)
class RuleContext:
    """Everything outside the plan that the rules need to know."""

    account: AccountSnapshot
    spec: SymbolSpec
    profile: RiskProfile
    #: Symbols that already carry an active entry for this user/account.
    active_symbols: frozenset[str] = frozenset()
    #: Number of live positions across all symbols.
    open_position_count: int = 0
    #: Realised profit/loss booked today (negative when losing).
    realised_pnl_today: float = 0.0
    #: True when the user explicitly asked to override overridable rules.
    override_requested: bool = False
    #: Ticket of the blocking position, when known (for a better message).
    blocking_ticket: int | None = None


def evaluate(plan: TradePlan, context: RuleContext) -> RulesReport:
    """Run every rule against ``plan`` and return the aggregate verdict."""
    checks: list[RuleCheck] = [
        _rule1_one_active_trade(plan, context),
        _rule2_lot_allocation(plan, context),
        _rule2_volume_constraints(plan, context),
        _rule3_max_risk(plan, context),
        _guard_symbol_tradable(plan, context),
        _guard_account_trade_allowed(plan, context),
        _guard_min_stop_distance(plan, context),
        _guard_margin(plan, context),
        _guard_min_reward_risk(plan, context),
    ]

    concurrent = _guard_max_concurrent(plan, context)
    if concurrent is not None:
        checks.append(concurrent)

    daily = _guard_daily_loss(plan, context)
    if daily is not None:
        checks.append(daily)

    allow_override = context.override_requested and context.profile.allow_manual_override
    overridden: list[str] = []
    resolved: list[RuleCheck] = []

    for check in checks:
        if check.blocking and allow_override and check.overridable:
            overridden.append(check.code)
            resolved.append(
                RuleCheck(
                    code=check.code,
                    rule=check.rule,
                    passed=True,
                    severity=Severity.WARN,
                    message=f"OVERRIDDEN: {check.message}",
                    overridable=True,
                    details=check.details,
                )
            )
        else:
            resolved.append(check)

    approved = not any(c.blocking for c in resolved)
    return RulesReport(approved=approved, checks=tuple(resolved), overridden=tuple(overridden))


# ---------------------------------------------------------------------------
# Rule 1 - one active trade per user per derivative
# ---------------------------------------------------------------------------
def _rule1_one_active_trade(plan: TradePlan, ctx: RuleContext) -> RuleCheck:
    symbol = plan.symbol.upper()
    active = {s.upper() for s in ctx.active_symbols}
    conflict = symbol in active

    if conflict:
        ticket = f" (position #{ctx.blocking_ticket})" if ctx.blocking_ticket else ""
        message = (
            f"Rule 1: {plan.symbol} already has an active entry{ticket}. Only one live position "
            f"per derivative is permitted. Close or let the existing position complete before "
            f"entering again. Other symbols remain tradable."
        )
    else:
        message = f"Rule 1 satisfied: no active entry on {plan.symbol}."

    return RuleCheck(
        code="RULE1_ONE_ACTIVE_TRADE",
        rule="One active trade per user per derivative",
        passed=not conflict,
        severity=Severity.BLOCK,
        message=message,
        overridable=False,
        details={
            "symbol": plan.symbol,
            "active_symbols": sorted(active),
            "blocking_ticket": ctx.blocking_ticket,
        },
    )


# ---------------------------------------------------------------------------
# Rule 2 - fixed lot allocation
# ---------------------------------------------------------------------------
def _rule2_lot_allocation(plan: TradePlan, ctx: RuleContext) -> RuleCheck:
    profile = ctx.profile
    prescribed = plan.prescribed_volume
    tolerance = plan.volume_step / 2.0
    formula = (
        f"{plan.capital:,.2f} {plan.account_currency} / 1,000 x {profile.lots_per_1000} "
        f"= {profile.raw_prescribed_volume(plan.capital):.4f} -> {prescribed:g} lots "
        f"(floored to the {plan.volume_step:g} lot step)"
    )

    if profile.lot_rule_mode is LotRuleMode.MAX:
        passed = plan.volume <= prescribed + tolerance
        message = (
            f"Rule 2 satisfied: {plan.volume:g} lots is within the allocation ceiling of "
            f"{prescribed:g} lots. {formula}"
            if passed
            else (
                f"Rule 2: {plan.volume:g} lots exceeds the allocation ceiling of {prescribed:g} "
                f"lots. {formula}"
            )
        )
    else:
        passed = abs(plan.volume - prescribed) <= tolerance
        message = (
            f"Rule 2 satisfied: {plan.volume:g} lots matches the capital allocation. {formula}"
            if passed
            else (
                f"Rule 2: volume must be {prescribed:g} lots, not {plan.volume:g}. {formula}"
            )
        )

    return RuleCheck(
        code="RULE2_LOT_ALLOCATION",
        rule="Fixed lot allocation from capital",
        passed=passed,
        severity=Severity.BLOCK,
        # Sizing smaller than prescribed is a discipline question, not a safety
        # one, so it can be overridden when the profile allows it.
        overridable=True,
        message=message,
        details={
            "requested_volume": plan.volume,
            "prescribed_volume": prescribed,
            "lots_per_1000": profile.lots_per_1000,
            "capital": plan.capital,
            "capital_basis": plan.capital_basis,
            "mode": profile.lot_rule_mode.value,
        },
    )


def _rule2_volume_constraints(plan: TradePlan, ctx: RuleContext) -> RuleCheck:
    spec = ctx.spec
    problems: list[str] = []

    if plan.volume < spec.volume_min:
        problems.append(f"below the minimum of {spec.volume_min:g}")
    if plan.volume > spec.volume_max:
        problems.append(f"above the maximum of {spec.volume_max:g}")
    if not is_multiple_of(plan.volume, spec.volume_step, tolerance=1e-8):
        problems.append(f"not a multiple of the {spec.volume_step:g} lot step")

    passed = not problems
    message = (
        f"Broker constraints satisfied: {plan.volume:g} lots is valid for {spec.name} "
        f"(min {spec.volume_min:g}, max {spec.volume_max:g}, step {spec.volume_step:g})."
        if passed
        else f"Volume {plan.volume:g} on {spec.name} is " + " and ".join(problems) + "."
    )

    return RuleCheck(
        code="RULE2_VOLUME_CONSTRAINTS",
        rule="Broker lot constraints",
        passed=passed,
        severity=Severity.BLOCK,
        overridable=False,
        message=message,
        details={
            "volume": plan.volume,
            "volume_min": spec.volume_min,
            "volume_max": spec.volume_max,
            "volume_step": spec.volume_step,
        },
    )


# ---------------------------------------------------------------------------
# Rule 3 - maximum stop-loss risk
# ---------------------------------------------------------------------------
def _rule3_max_risk(plan: TradePlan, ctx: RuleContext) -> RuleCheck:
    passed = not plan.exceeds_risk_limit
    currency = plan.account_currency

    if passed:
        message = (
            f"Rule 3 satisfied: loss at stop is {plan.max_loss:,.2f} {currency} "
            f"({plan.risk_pct_of_capital:.2f}% of {plan.capital:,.2f}), within the "
            f"{plan.max_risk_pct:.2f}% ceiling of {plan.max_risk_money:,.2f} {currency}."
        )
    else:
        excess = plan.max_loss - plan.max_risk_money
        message = (
            f"Rule 3: loss at the proposed stop is {plan.max_loss:,.2f} {currency} "
            f"({plan.risk_pct_of_capital:.2f}% of capital), which exceeds the "
            f"{plan.max_risk_pct:.2f}% ceiling of {plan.max_risk_money:,.2f} {currency} by "
            f"{excess:,.2f}. Move the stop no further than {plan.max_stop_points:.0f} points "
            f"({plan.max_stop_price:.{plan.digits}f}) at {plan.volume:g} lots, or reduce size to "
            f"{plan.volume_for_requested_stop:g} lots."
        )

    return RuleCheck(
        code="RULE3_MAX_RISK",
        rule="Maximum stop-loss risk",
        passed=passed,
        severity=Severity.BLOCK,
        overridable=False,
        message=message,
        details={
            "max_loss": plan.max_loss,
            "max_risk_money": plan.max_risk_money,
            "max_risk_pct": plan.max_risk_pct,
            "risk_pct_of_capital": plan.risk_pct_of_capital,
            "max_stop_price": plan.max_stop_price,
            "max_stop_points": plan.max_stop_points,
            "volume_for_requested_stop": plan.volume_for_requested_stop,
        },
    )


# ---------------------------------------------------------------------------
# Supporting guards
# ---------------------------------------------------------------------------
def _guard_symbol_tradable(plan: TradePlan, ctx: RuleContext) -> RuleCheck:
    allowed = ctx.spec.trade_allowed
    return RuleCheck(
        code="GUARD_SYMBOL_TRADABLE",
        rule="Symbol is open for trading",
        passed=allowed,
        severity=Severity.BLOCK,
        overridable=False,
        message=(
            f"{plan.symbol} is open for trading."
            if allowed
            else f"{plan.symbol} is not currently tradable (market closed or trade mode disabled)."
        ),
        details={"trade_allowed": allowed},
    )


def _guard_account_trade_allowed(plan: TradePlan, ctx: RuleContext) -> RuleCheck:
    account = ctx.account
    allowed = account.trade_allowed and account.trade_expert
    reasons = []
    if not account.trade_allowed:
        reasons.append("the account is not permitted to trade (investor password or disabled)")
    if not account.trade_expert:
        reasons.append("algorithmic trading is disabled in the terminal")
    return RuleCheck(
        code="GUARD_ACCOUNT_TRADE_ALLOWED",
        rule="Account may place orders",
        passed=allowed,
        severity=Severity.BLOCK,
        overridable=False,
        message=(
            "Account is permitted to place orders."
            if allowed
            else "Cannot place orders: " + " and ".join(reasons) + "."
        ),
        details={
            "trade_allowed": account.trade_allowed,
            "trade_expert": account.trade_expert,
        },
    )


def _guard_min_stop_distance(plan: TradePlan, ctx: RuleContext) -> RuleCheck:
    spec = ctx.spec
    minimum = spec.min_stop_distance
    passed = minimum <= 0 or plan.risk_distance >= minimum
    return RuleCheck(
        code="GUARD_MIN_STOP_DISTANCE",
        rule="Broker minimum stop distance",
        passed=passed,
        severity=Severity.BLOCK,
        overridable=False,
        message=(
            f"Stop distance {plan.risk_points:.0f} points meets the broker minimum of "
            f"{spec.stops_level_points} points."
            if passed
            else (
                f"Stop is only {plan.risk_points:.0f} points from entry; {spec.name} requires at "
                f"least {spec.stops_level_points} points. The broker would reject this order."
            )
        ),
        details={
            "risk_points": plan.risk_points,
            "stops_level_points": spec.stops_level_points,
        },
    )


def _guard_margin(plan: TradePlan, ctx: RuleContext) -> RuleCheck:
    budget = ctx.profile.margin_budget(ctx.account)
    unlimited = budget == float("inf")
    passed = unlimited or plan.required_margin <= budget
    currency = plan.account_currency

    if passed:
        message = (
            f"Margin requirement {plan.required_margin:,.2f} {currency} is "
            f"{plan.margin_pct_of_free_margin:.1f}% of free margin "
            f"({ctx.account.margin_free:,.2f} {currency})."
        )
    else:
        message = (
            f"Margin requirement {plan.required_margin:,.2f} {currency} exceeds the "
            f"{ctx.profile.margin_utilisation_cap_pct:.0f}% cap on free margin "
            f"({budget:,.2f} of {ctx.account.margin_free:,.2f} {currency})."
        )

    return RuleCheck(
        code="GUARD_MARGIN",
        rule="Margin utilisation cap",
        passed=passed,
        severity=Severity.BLOCK,
        overridable=True,
        message=message,
        details={
            "required_margin": plan.required_margin,
            "free_margin": ctx.account.margin_free,
            "budget": None if unlimited else budget,
            "cap_pct": ctx.profile.margin_utilisation_cap_pct,
            "source": plan.margin_source,
        },
    )


def _guard_min_reward_risk(plan: TradePlan, ctx: RuleContext) -> RuleCheck:
    minimum = ctx.profile.min_reward_risk
    passed = minimum <= 0 or plan.reward_risk_final >= minimum - 1e-9
    return RuleCheck(
        code="GUARD_MIN_REWARD_RISK",
        rule="Minimum reward-to-risk",
        passed=passed,
        severity=Severity.BLOCK,
        overridable=True,
        message=(
            f"Final target is {plan.reward_risk_final:.2f}R with a blended plan R/R of "
            f"{plan.reward_risk_blended:.2f}."
            if passed
            else (
                f"Final target is only {plan.reward_risk_final:.2f}R, below the configured minimum "
                f"of {minimum:.2f}R."
            )
        ),
        details={
            "reward_risk_final": plan.reward_risk_final,
            "reward_risk_blended": plan.reward_risk_blended,
            "minimum": minimum,
        },
    )


def _guard_max_concurrent(plan: TradePlan, ctx: RuleContext) -> RuleCheck | None:
    limit = ctx.profile.max_concurrent_positions
    if limit <= 0:
        return None
    passed = ctx.open_position_count < limit
    return RuleCheck(
        code="GUARD_MAX_CONCURRENT",
        rule="Maximum concurrent positions",
        passed=passed,
        severity=Severity.BLOCK,
        overridable=True,
        message=(
            f"{ctx.open_position_count} of {limit} concurrent positions in use."
            if passed
            else (
                f"Already holding {ctx.open_position_count} positions, at the configured limit of "
                f"{limit}."
            )
        ),
        details={"open_positions": ctx.open_position_count, "limit": limit},
    )


def _guard_daily_loss(plan: TradePlan, ctx: RuleContext) -> RuleCheck | None:
    limit_pct = ctx.profile.max_daily_loss_pct
    if limit_pct <= 0:
        return None

    limit_money = plan.capital * (limit_pct / 100.0)
    loss_today = max(0.0, -ctx.realised_pnl_today)
    projected = loss_today + plan.max_loss
    passed = projected <= limit_money
    currency = plan.account_currency

    return RuleCheck(
        code="GUARD_DAILY_LOSS",
        rule="Daily loss limit",
        passed=passed,
        severity=Severity.BLOCK,
        overridable=False,
        message=(
            f"Realised loss today is {loss_today:,.2f} {currency}; this trade risks a further "
            f"{plan.max_loss:,.2f}, keeping the day within the {limit_pct:.2f}% limit "
            f"({limit_money:,.2f})."
            if passed
            else (
                f"Realised loss today is {loss_today:,.2f} {currency} "
                f"({pct(loss_today, plan.capital):.2f}% of capital). Risking another "
                f"{plan.max_loss:,.2f} would breach the {limit_pct:.2f}% daily loss limit of "
                f"{limit_money:,.2f}. Trading is paused until tomorrow."
            )
        ),
        details={
            "realised_pnl_today": ctx.realised_pnl_today,
            "loss_today": loss_today,
            "limit_money": limit_money,
            "limit_pct": limit_pct,
            "projected_loss": projected,
        },
    )
