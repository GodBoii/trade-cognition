"""Pre-entry risk and profit calculator.

Given an intent (symbol, side, entry, stop), a symbol specification, a live
quote, an account snapshot and a risk profile, this module produces a complete
:class:`TradePlan`: lot size, monetary risk, every target with its volume and
expected profit, the stop-loss the position will carry after each scale-out,
required margin and the percentage of capital at risk.

Design notes
------------
* **No side effects.** Nothing here talks to a broker, a database or a clock.
* **Direction free.** ``Side.sign`` removes every ``if buy ... else ...`` from
  the price math, which is where sign bugs normally hide.
* **Broker truth preferred.** When the caller supplies ``money_fn`` /
  ``margin_fn`` (backed by ``order_calc_profit`` / ``order_calc_margin``) those
  values win, because they include the broker's own currency conversion.  The
  tick-value fallback keeps the calculator usable offline and in tests.
* **Structural problems raise**; *rule* breaches do not.  A missing stop or a
  stop on the wrong side of entry makes the plan meaningless, so it raises
  :class:`~app.errors.ValidationError`.  Breaching the 2% ceiling still yields a
  full plan - the user needs to see the numbers that got them rejected.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..errors import ValidationError
from .enums import LadderPreset, OrderKind, Side, SlAction
from .ladder import Ladder, allocate_stage_volumes, get_ladder
from .market import AccountSnapshot, SymbolSpec, Tick
from .profile import RiskProfile
from .quant import pct, round_volume, safe_div

#: ``(side, volume, price_open, price_close) -> money in account currency``
MoneyFn = Callable[[Side, float, float, float], float | None]
#: ``(side, volume, price) -> margin in account currency``
MarginFn = Callable[[Side, float, float], float | None]


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """What the user is asking to do, before any calculation."""

    symbol: str
    side: Side
    order_kind: OrderKind = OrderKind.MARKET
    #: ``None`` means "use the live market price for this side".
    entry_price: float | None = None
    #: Provide exactly one of ``sl_price`` or ``sl_points``.
    sl_price: float | None = None
    sl_points: float | None = None
    #: ``None`` means "use the Rule 2 prescribed volume".
    volume: float | None = None
    ladder_preset: LadderPreset | None = None
    comment: str = ""

    def with_volume(self, volume: float) -> TradeIntent:
        return TradeIntent(
            symbol=self.symbol,
            side=self.side,
            order_kind=self.order_kind,
            entry_price=self.entry_price,
            sl_price=self.sl_price,
            sl_points=self.sl_points,
            volume=volume,
            ladder_preset=self.ladder_preset,
            comment=self.comment,
        )


@dataclass(frozen=True, slots=True)
class StagePlan:
    """One planned rung of the profit ladder."""

    key: str
    r_multiple: float
    target_price: float
    target_distance: float
    target_points: float
    volume: float
    cumulative_volume: float
    remaining_volume: float
    money_profit: float
    cumulative_money: float
    sl_action: SlAction
    sl_after: float | None
    sl_after_points_from_entry: float | None
    locked_in_money: float
    will_execute: bool
    note: str = ""


@dataclass(frozen=True, slots=True)
class TradePlan:
    """The complete pre-trade picture presented to the user and the rules engine."""

    # --- identity ----------------------------------------------------------
    symbol: str
    side: Side
    order_kind: OrderKind

    # --- prices ------------------------------------------------------------
    entry_price: float
    stop_loss: float
    risk_distance: float
    risk_points: float
    bid: float
    ask: float
    spread: float
    spread_points: float
    digits: int

    # --- size --------------------------------------------------------------
    volume: float
    prescribed_volume: float
    volume_is_prescribed: bool
    volume_min: float
    volume_max: float
    volume_step: float

    # --- capital & risk ----------------------------------------------------
    capital: float
    capital_basis: str
    account_currency: str
    max_loss: float
    risk_pct_of_capital: float
    max_risk_pct: float
    max_risk_money: float
    risk_headroom: float

    # --- money geometry ----------------------------------------------------
    money_per_point: float
    money_per_price_unit_per_lot: float
    spread_cost: float
    pricing_source: str

    # --- margin ------------------------------------------------------------
    required_margin: float
    margin_source: str
    free_margin: float
    margin_pct_of_free_margin: float
    margin_pct_of_capital: float

    # --- targets -----------------------------------------------------------
    stages: tuple[StagePlan, ...]
    expected_profit: float
    expected_profit_pct_of_capital: float
    reward_risk_final: float
    reward_risk_blended: float

    # --- rule helpers ------------------------------------------------------
    max_stop_distance: float
    max_stop_points: float
    max_stop_price: float
    volume_for_requested_stop: float
    min_stop_distance: float

    # --- meta --------------------------------------------------------------
    ladder_preset: str
    ladder_label: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------- accessors
    def stage(self, key: str) -> StagePlan | None:
        for stage in self.stages:
            if stage.key == key:
                return stage
        return None

    @property
    def executing_stages(self) -> tuple[StagePlan, ...]:
        return tuple(s for s in self.stages if s.will_execute)

    @property
    def take_profit_prices(self) -> tuple[float, ...]:
        return tuple(s.target_price for s in self.stages)

    @property
    def first_target(self) -> float:
        return self.stages[0].target_price if self.stages else 0.0

    @property
    def final_target(self) -> float:
        executing = self.executing_stages
        return executing[-1].target_price if executing else self.first_target

    @property
    def exceeds_risk_limit(self) -> bool:
        # Compare in account-currency cents to avoid float-hair rejections.
        return round(self.max_loss, 2) > round(self.max_risk_money, 2)


def calculate_trade_plan(
    intent: TradeIntent,
    *,
    spec: SymbolSpec,
    tick: Tick,
    account: AccountSnapshot,
    profile: RiskProfile,
    money_fn: MoneyFn | None = None,
    margin_fn: MarginFn | None = None,
    ladder: Ladder | None = None,
) -> TradePlan:
    """Build a :class:`TradePlan`.  See module docstring for the contract."""

    warnings: list[str] = []
    side = intent.side

    # ------------------------------------------------------------------ entry
    entry = intent.entry_price if intent.entry_price is not None else tick.entry_price(side)
    if entry is None or entry <= 0:
        raise ValidationError(
            f"No usable entry price for {spec.name}. The market feed returned "
            f"bid={tick.bid} ask={tick.ask}.",
            code="no_price",
        )
    entry = spec.normalise_price(float(entry))

    # -------------------------------------------------------------- stop loss
    stop = _resolve_stop(intent, entry=entry, side=side, spec=spec)
    risk_distance = spec.price_diff(entry, stop)
    risk_points = spec.to_points(risk_distance)

    # ----------------------------------------------------------------- volume
    capital = profile.capital(account)
    prescribed = profile.prescribed_volume(capital, spec)

    if intent.volume is not None:
        volume = round_volume(float(intent.volume), spec.volume_step, "nearest")
        volume_is_prescribed = abs(volume - prescribed) < (spec.volume_step / 2)
    else:
        volume = prescribed
        volume_is_prescribed = True
        if prescribed <= 0:
            raise ValidationError(
                f"Rule 2 sizing yields {profile.raw_prescribed_volume(capital):.4f} lots for "
                f"{spec.name} on {capital:,.2f} {account.currency} of capital "
                f"({profile.lots_per_1000} lots per 1,000), which is below the broker minimum of "
                f"{spec.volume_min:g} lots. Increase capital or choose a symbol with a smaller "
                f"minimum lot.",
                code="volume_below_minimum",
                details={
                    "capital": capital,
                    "raw_volume": profile.raw_prescribed_volume(capital),
                    "volume_min": spec.volume_min,
                },
            )

    if volume <= 0:
        raise ValidationError(
            f"Volume resolved to {volume:g} lots, which cannot be traded.",
            code="invalid_volume",
        )

    # ------------------------------------------------------------ money units
    money_per_price_unit_per_lot = spec.money_per_price_unit_per_lot
    if money_per_price_unit_per_lot <= 0:
        raise ValidationError(
            f"{spec.name} does not publish a usable tick value or contract size, so risk cannot "
            f"be quantified. Refusing to size a trade blindly.",
            code="symbol_value_unknown",
        )

    pricing_source = "tick_value"
    money_per_point = spec.money_per_point(volume)

    # -------------------------------------------------------------- max loss
    fallback_loss = spec.money_for_move(risk_distance, volume)
    max_loss = fallback_loss
    if money_fn is not None:
        broker_loss = money_fn(side, volume, entry, stop)
        if broker_loss is not None and abs(broker_loss) > 0:
            max_loss = abs(broker_loss)
            pricing_source = "terminal"
            if fallback_loss > 0:
                drift = abs(max_loss - fallback_loss) / fallback_loss
                if drift > 0.05:
                    warnings.append(
                        f"Terminal-calculated risk ({max_loss:,.2f}) differs from the tick-value "
                        f"estimate ({fallback_loss:,.2f}) by {drift:.0%}; the terminal figure is "
                        f"used. This is expected when the profit currency is not "
                        f"{account.currency}."
                    )

    max_risk_money = profile.max_risk_money(capital)
    risk_pct_of_capital = pct(max_loss, capital)

    # ---------------------------------------------------------------- targets
    active_ladder = ladder or get_ladder(intent.ladder_preset or profile.ladder_preset)
    stage_volumes, alloc_warnings = allocate_stage_volumes(
        volume, active_ladder, spec.volume_step, spec.volume_min
    )
    warnings.extend(alloc_warnings)

    stages = _build_stages(
        ladder=active_ladder,
        stage_volumes=stage_volumes,
        side=side,
        entry=entry,
        stop=stop,
        risk_distance=risk_distance,
        spec=spec,
        total_volume=volume,
        money_fn=money_fn,
    )

    expected_profit = stages[-1].cumulative_money if stages else 0.0
    executing = [s for s in stages if s.will_execute]
    reward_risk_final = executing[-1].r_multiple if executing else 0.0
    reward_risk_blended = safe_div(expected_profit, max_loss, 0.0)

    # ----------------------------------------------------------------- margin
    required_margin, margin_source = _resolve_margin(
        side=side,
        volume=volume,
        entry=entry,
        spec=spec,
        account=account,
        margin_fn=margin_fn,
    )

    # ------------------------------------------------------------ rule hints
    max_stop_distance = round(
        spec.price_distance_for_money(max_risk_money, volume), spec.digits
    )
    max_stop_price = spec.normalise_price(entry - side.sign * max_stop_distance)
    volume_for_requested_stop = round_volume(
        safe_div(max_risk_money, risk_distance * money_per_price_unit_per_lot, 0.0),
        spec.volume_step,
        "floor",
    )

    if spec.min_stop_distance > 0 and risk_distance < spec.min_stop_distance:
        warnings.append(
            f"Stop is {risk_points:.0f} points from entry but {spec.name} requires at least "
            f"{spec.stops_level_points} points."
        )

    spread_cost = spec.money_for_move(tick.spread, volume)
    if max_loss > 0 and spread_cost > 0.10 * max_loss:
        warnings.append(
            f"Spread cost ({spread_cost:,.2f} {account.currency}) is "
            f"{pct(spread_cost, max_loss):.0f}% of the risked amount; the stop is tight relative "
            f"to current spread."
        )

    if not volume_is_prescribed:
        warnings.append(
            f"Requested volume {volume:g} differs from the Rule 2 allocation of "
            f"{prescribed:g} lots."
        )

    return TradePlan(
        symbol=spec.name,
        side=side,
        order_kind=intent.order_kind,
        entry_price=entry,
        stop_loss=stop,
        risk_distance=risk_distance,
        risk_points=risk_points,
        bid=tick.bid,
        ask=tick.ask,
        spread=spec.price_diff(tick.ask, tick.bid),
        spread_points=spec.to_points(spec.price_diff(tick.ask, tick.bid)),
        digits=spec.digits,
        volume=volume,
        prescribed_volume=prescribed,
        volume_is_prescribed=volume_is_prescribed,
        volume_min=spec.volume_min,
        volume_max=spec.volume_max,
        volume_step=spec.volume_step,
        capital=capital,
        capital_basis=profile.capital_basis.value,
        account_currency=account.currency,
        max_loss=max_loss,
        risk_pct_of_capital=risk_pct_of_capital,
        max_risk_pct=profile.max_risk_pct,
        max_risk_money=max_risk_money,
        risk_headroom=max_risk_money - max_loss,
        money_per_point=money_per_point,
        money_per_price_unit_per_lot=money_per_price_unit_per_lot,
        spread_cost=spread_cost,
        pricing_source=pricing_source,
        required_margin=required_margin,
        margin_source=margin_source,
        free_margin=account.margin_free,
        margin_pct_of_free_margin=pct(required_margin, account.margin_free),
        margin_pct_of_capital=pct(required_margin, capital),
        stages=stages,
        expected_profit=expected_profit,
        expected_profit_pct_of_capital=pct(expected_profit, capital),
        reward_risk_final=reward_risk_final,
        reward_risk_blended=reward_risk_blended,
        max_stop_distance=max_stop_distance,
        max_stop_points=spec.to_points(max_stop_distance),
        max_stop_price=max_stop_price,
        volume_for_requested_stop=volume_for_requested_stop,
        min_stop_distance=spec.min_stop_distance,
        ladder_preset=active_ladder.preset.value,
        ladder_label=active_ladder.label,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _resolve_stop(
    intent: TradeIntent, *, entry: float, side: Side, spec: SymbolSpec
) -> float:
    """Derive and sanity-check the stop-loss price."""
    if intent.sl_price is not None:
        stop = spec.normalise_price(float(intent.sl_price))
    elif intent.sl_points is not None:
        points = abs(float(intent.sl_points))
        if points <= 0:
            raise ValidationError(
                "Stop distance must be greater than zero points.", code="stop_required"
            )
        stop = spec.normalise_price(entry - side.sign * spec.from_points(points))
    else:
        raise ValidationError(
            "A stop-loss is mandatory. Supply either an absolute stop price or a stop distance "
            "in points.",
            code="stop_required",
        )

    if stop <= 0:
        raise ValidationError(
            f"Computed stop-loss of {stop} is not a valid price.", code="stop_invalid"
        )

    if abs(stop - entry) < spec.effective_tick_size / 2:
        raise ValidationError(
            f"Stop-loss {stop} coincides with the entry price {entry}; risk would be zero and "
            f"position sizing undefined.",
            code="stop_equals_entry",
        )

    wrong_side = (side is Side.BUY and stop > entry) or (side is Side.SELL and stop < entry)
    if wrong_side:
        direction = "below" if side is Side.BUY else "above"
        raise ValidationError(
            f"For a {side.value} on {spec.name} the stop-loss must be {direction} the entry "
            f"price. Got entry={entry} stop={stop}.",
            code="stop_wrong_side",
        )
    return stop


def _build_stages(
    *,
    ladder: Ladder,
    stage_volumes: list[float],
    side: Side,
    entry: float,
    stop: float,
    risk_distance: float,
    spec: SymbolSpec,
    total_volume: float,
    money_fn: MoneyFn | None,
) -> tuple[StagePlan, ...]:
    """Turn ladder stages into concrete prices, volumes, profits and stops."""
    stages: list[StagePlan] = []
    target_prices: dict[str, float] = {}

    cumulative_volume = 0.0
    cumulative_money = 0.0
    remaining = total_volume
    current_sl = stop
    previous_target: float | None = None

    for stage, stage_volume in zip(ladder.stages, stage_volumes, strict=True):
        target_price = spec.normalise_price(entry + side.sign * stage.r_multiple * risk_distance)
        target_distance = spec.price_diff(target_price, entry)
        target_prices[stage.key] = target_price

        money = _stage_money(
            side=side,
            volume=stage_volume,
            entry=entry,
            target=target_price,
            spec=spec,
            money_fn=money_fn,
        )

        cumulative_volume = round_volume(
            cumulative_volume + stage_volume, spec.volume_step, "nearest"
        )
        cumulative_money += money
        remaining = round_volume(total_volume - cumulative_volume, spec.volume_step, "nearest")

        sl_after = _stage_stop(
            action=stage.sl_action,
            side=side,
            entry=entry,
            original_stop=stop,
            risk_distance=risk_distance,
            spec=spec,
            targets=target_prices,
            previous_target=previous_target,
            current=current_sl,
        )
        if sl_after is not None:
            current_sl = sl_after

        # What the account keeps if the (new) stop is hit right after this rung.
        locked_in = cumulative_money
        if remaining > 0:
            locked_in += (
                (current_sl - entry) * side.sign * spec.money_per_price_unit(remaining)
            )

        stages.append(
            StagePlan(
                key=stage.key,
                r_multiple=stage.r_multiple,
                target_price=target_price,
                target_distance=target_distance,
                target_points=spec.to_points(target_distance),
                volume=stage_volume,
                cumulative_volume=cumulative_volume,
                remaining_volume=remaining,
                money_profit=money,
                cumulative_money=cumulative_money,
                sl_action=stage.sl_action,
                sl_after=current_sl if stage_volume > 0 else None,
                sl_after_points_from_entry=(
                    spec.to_points(current_sl - entry) * (1 if current_sl >= entry else -1)
                    if stage_volume > 0
                    else None
                ),
                locked_in_money=locked_in,
                will_execute=stage_volume > 0,
                note=stage.note,
            )
        )
        previous_target = target_price

    return tuple(stages)


def _stage_money(
    *,
    side: Side,
    volume: float,
    entry: float,
    target: float,
    spec: SymbolSpec,
    money_fn: MoneyFn | None,
) -> float:
    if volume <= 0:
        return 0.0
    if money_fn is not None:
        value = money_fn(side, volume, entry, target)
        if value is not None and value != 0:
            return float(value)
    return spec.money_for_move(target - entry, volume)


def _stage_stop(
    *,
    action: SlAction,
    side: Side,
    entry: float,
    original_stop: float,
    risk_distance: float,
    spec: SymbolSpec,
    targets: dict[str, float],
    previous_target: float | None,
    current: float,
) -> float | None:
    """Resolve the stop price implied by a stage's SL action."""
    if action is SlAction.NONE:
        return None
    if action is SlAction.HALVE_ORIGINAL_DISTANCE:
        return spec.normalise_price(entry - side.sign * (risk_distance / 2.0))
    if action is SlAction.BREAKEVEN:
        return spec.normalise_price(entry)
    if action is SlAction.MOVE_TO_TP1:
        return targets.get("TP1", current)
    if action is SlAction.MOVE_TO_PREVIOUS_TARGET:
        return previous_target if previous_target is not None else current
    return None  # pragma: no cover - exhaustive above


def _resolve_margin(
    *,
    side: Side,
    volume: float,
    entry: float,
    spec: SymbolSpec,
    account: AccountSnapshot,
    margin_fn: MarginFn | None,
) -> tuple[float, str]:
    """Required margin, preferring the terminal's own calculation."""
    if margin_fn is not None:
        value = margin_fn(side, volume, entry)
        if value is not None and value >= 0:
            return float(value), "terminal"

    notional = volume * spec.contract_size * entry
    if spec.margin_rate and spec.margin_rate > 0:
        return notional * spec.margin_rate, "margin_rate"
    if account.leverage and account.leverage > 0:
        return notional / account.leverage, "leverage"
    return notional, "notional"


def plan_to_dict(plan: TradePlan) -> dict[str, Any]:
    """Shallow, JSON-friendly projection used by the journal."""
    from dataclasses import asdict

    data = asdict(plan)
    data["side"] = plan.side.value
    data["order_kind"] = plan.order_kind.value
    data["stages"] = [
        {**s, "sl_action": s["sl_action"].value if hasattr(s["sl_action"], "value") else s["sl_action"]}
        for s in data["stages"]
    ]
    data["warnings"] = list(plan.warnings)
    return data
