"""The pre-entry calculator: sizing, risk, targets, margin."""

from __future__ import annotations

import pytest

from app.domain.enums import LadderPreset, Side
from app.domain.market import Tick
from app.domain.profile import RiskProfile
from app.domain.risk import TradeIntent, calculate_trade_plan
from app.errors import ValidationError


def plan_for(spec, tick, account, profile=None, side: Side = Side.BUY, **intent_kwargs):
    intent = TradeIntent(symbol=spec.name, side=side, **intent_kwargs)
    return calculate_trade_plan(
        intent, spec=spec, tick=tick, account=account, profile=profile or RiskProfile()
    )


# ---------------------------------------------------------------------------
# Rule 2 sizing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("balance", "expected_lots"),
    [
        (1_000, 0.02),
        (10_000, 0.20),
        (25_000, 0.50),
        (100_000, 2.00),
        (1_500, 0.03),
        # 1,250 -> 0.025 raw, floored onto the 0.01 grid.
        (1_250, 0.02),
    ],
)
def test_lot_allocation_follows_capital(eurusd, eurusd_tick, account, balance, expected_lots):
    snapshot = account.__class__(
        login=account.login,
        currency="USD",
        balance=balance,
        equity=balance,
        margin_free=balance,
        leverage=100,
    )
    plan = plan_for(eurusd, eurusd_tick, snapshot, sl_points=300)
    assert plan.prescribed_volume == pytest.approx(expected_lots)
    assert plan.volume == pytest.approx(expected_lots)
    assert plan.volume_is_prescribed


def test_capital_too_small_for_the_symbol_is_refused(eurusd, eurusd_tick, account):
    tiny = account.__class__(
        login=1, currency="USD", balance=400, equity=400, margin_free=400, leverage=100
    )
    with pytest.raises(ValidationError) as exc:
        plan_for(eurusd, eurusd_tick, tiny, sl_points=300)
    assert exc.value.code == "volume_below_minimum"
    assert "below the broker minimum" in exc.value.message


def test_equity_basis_uses_equity_not_balance(eurusd, eurusd_tick):
    from app.domain.enums import CapitalBasis
    from app.domain.market import AccountSnapshot

    snapshot = AccountSnapshot(
        login=1, currency="USD", balance=10_000, equity=5_000, margin_free=5_000, leverage=100
    )
    profile = RiskProfile(capital_basis=CapitalBasis.EQUITY)
    plan = plan_for(eurusd, eurusd_tick, snapshot, profile=profile, sl_points=300)
    assert plan.capital == 5_000
    assert plan.volume == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Rule 3 risk arithmetic
# ---------------------------------------------------------------------------
def test_monetary_risk_and_percentage(eurusd, eurusd_tick, account):
    plan = plan_for(eurusd, eurusd_tick, account, side=Side.BUY, sl_points=500)
    # 0.20 lots x 500 points x 1.00 USD per point per lot = 100 USD.
    assert plan.volume == 0.20
    assert plan.max_loss == pytest.approx(100.0)
    assert plan.risk_pct_of_capital == pytest.approx(1.0)
    assert plan.max_risk_money == pytest.approx(200.0)
    assert not plan.exceeds_risk_limit
    assert plan.risk_headroom == pytest.approx(100.0)


def test_risk_ceiling_and_the_maximum_stop_hint(eurusd, eurusd_tick, account):
    plan = plan_for(eurusd, eurusd_tick, account, side=Side.BUY, sl_points=1500)
    assert plan.max_loss == pytest.approx(300.0)
    assert plan.exceeds_risk_limit
    # At 0.20 lots, 200 USD of risk is exactly 1000 points.
    assert plan.max_stop_points == pytest.approx(1000.0)
    assert plan.max_stop_price == pytest.approx(round(plan.entry_price - 0.01, 5))
    # Sizing that *would* respect the ceiling with this stop.
    assert plan.volume_for_requested_stop == pytest.approx(0.13)


def test_short_side_mirrors_the_long_side(eurusd, eurusd_tick, account):
    long_plan = plan_for(eurusd, eurusd_tick, account, side=Side.BUY, sl_points=400)
    short_plan = plan_for(eurusd, eurusd_tick, account, side=Side.SELL, sl_points=400)

    assert long_plan.entry_price == eurusd_tick.ask
    assert short_plan.entry_price == eurusd_tick.bid
    assert long_plan.stop_loss < long_plan.entry_price
    assert short_plan.stop_loss > short_plan.entry_price
    assert long_plan.stages[0].target_price > long_plan.entry_price
    assert long_plan.max_loss == pytest.approx(short_plan.max_loss)
    assert short_plan.stages[0].target_price < short_plan.entry_price


# ---------------------------------------------------------------------------
# stop validation
# ---------------------------------------------------------------------------
def test_stop_is_mandatory(eurusd, eurusd_tick, account):
    with pytest.raises(ValidationError) as exc:
        plan_for(eurusd, eurusd_tick, account, side=Side.BUY)
    assert exc.value.code == "stop_required"


def test_stop_on_the_wrong_side_is_refused(eurusd, eurusd_tick, account):
    with pytest.raises(ValidationError) as exc:
        plan_for(eurusd, eurusd_tick, account, side=Side.BUY, sl_price=1.20000)
    assert exc.value.code == "stop_wrong_side"
    assert "must be below the entry" in exc.value.message


def test_stop_at_entry_is_refused(eurusd, eurusd_tick, account):
    with pytest.raises(ValidationError) as exc:
        plan_for(eurusd, eurusd_tick, account, side=Side.BUY, sl_price=eurusd_tick.ask)
    assert exc.value.code == "stop_equals_entry"


# ---------------------------------------------------------------------------
# targets and the ladder
# ---------------------------------------------------------------------------
def test_targets_sit_at_one_two_and_three_r(eurusd, eurusd_tick, account):
    plan = plan_for(eurusd, eurusd_tick, account, side=Side.BUY, sl_points=500)
    entry, r = plan.entry_price, plan.risk_distance

    tp1, tp2, tp3 = plan.stages
    assert tp1.target_price == pytest.approx(round(entry + r, 5))
    assert tp2.target_price == pytest.approx(round(entry + 2 * r, 5))
    assert tp3.target_price == pytest.approx(round(entry + 3 * r, 5))
    assert [s.r_multiple for s in plan.stages] == [1.0, 2.0, 3.0]


def test_expected_profit_and_blended_reward_risk(eurusd, eurusd_tick, account):
    plan = plan_for(eurusd, eurusd_tick, account, side=Side.BUY, sl_points=500)
    tp1, tp2, _ = plan.stages

    assert tp1.volume == pytest.approx(0.1)
    assert tp1.money_profit == pytest.approx(50.0)
    assert tp2.volume == pytest.approx(0.1)
    assert tp2.money_profit == pytest.approx(100.0)
    assert plan.expected_profit == pytest.approx(150.0)
    # Half out at 1R and half at 2R is a 1.5R plan, not a 3R plan.
    assert plan.reward_risk_blended == pytest.approx(1.5)
    assert plan.reward_risk_final == pytest.approx(2.0)


def test_stop_management_halves_risk_then_locks_in_profit(eurusd, eurusd_tick, account):
    plan = plan_for(eurusd, eurusd_tick, account, side=Side.BUY, sl_points=500)
    entry, r = plan.entry_price, plan.risk_distance
    tp1, tp2, _ = plan.stages

    # TP1: stop to half the original distance -> worst case is now a profit.
    assert tp1.sl_after == pytest.approx(round(entry - r / 2, 5))
    assert tp1.locked_in_money == pytest.approx(25.0)
    assert tp1.remaining_volume == pytest.approx(0.1)

    # TP2: stop to the TP1 price, and the position is flat afterwards.
    assert tp2.sl_after == pytest.approx(tp1.target_price)
    assert tp2.remaining_volume == pytest.approx(0.0)
    assert tp2.locked_in_money == pytest.approx(150.0)


def test_runner_ladder_carries_volume_to_three_r(eurusd, eurusd_tick, account):
    plan = plan_for(
        eurusd,
        eurusd_tick,
        account,
        side=Side.BUY,
        sl_points=500,
        ladder_preset=LadderPreset.RUNNER_1_2_3,
    )
    tp1, tp2, tp3 = plan.stages
    assert (tp1.volume, tp2.volume, tp3.volume) == (0.1, 0.05, 0.05)
    assert tp3.will_execute
    assert plan.reward_risk_final == pytest.approx(3.0)
    # 0.5x1R + 0.25x2R + 0.25x3R = 1.75R
    assert plan.reward_risk_blended == pytest.approx(1.75)


# ---------------------------------------------------------------------------
# instrument variety
# ---------------------------------------------------------------------------
def test_gold_uses_contract_geometry_not_fx_assumptions(xauusd, account):
    tick = Tick(symbol="XAUUSD", bid=2350.00, ask=2350.30)
    plan = plan_for(xauusd, tick, account, side=Side.BUY, sl_points=1000)

    # 100 oz per lot: a 1.00 price move is 100 USD per lot.
    assert plan.money_per_price_unit_per_lot == pytest.approx(100.0)
    # 0.20 lots, stop 10.00 away -> 200 USD, exactly at the 2% ceiling.
    assert plan.risk_distance == pytest.approx(10.0)
    assert plan.max_loss == pytest.approx(200.0)
    assert not plan.exceeds_risk_limit
    assert plan.risk_pct_of_capital == pytest.approx(2.0)


def test_coarse_lot_grid_is_respected(us500, account):
    tick = Tick(symbol="US500", bid=5400.0, ask=5400.6)
    plan = plan_for(us500, tick, account, side=Side.BUY, sl_points=300)

    assert plan.volume == pytest.approx(0.2)  # 0.20 raw, already on the 0.1 grid
    assert plan.stages[0].volume == pytest.approx(0.1)
    assert plan.stages[1].volume == pytest.approx(0.1)


def test_symbol_without_value_information_is_refused(account, eurusd_tick):
    from app.domain.market import SymbolSpec

    broken = SymbolSpec(
        name="MYSTERY",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value_loss=0.0,
        tick_value_profit=0.0,
        contract_size=0.0,
    )
    with pytest.raises(ValidationError) as exc:
        plan_for(broken, Tick(symbol="MYSTERY", bid=100.0, ask=100.1), account, sl_points=100)
    assert exc.value.code == "symbol_value_unknown"


# ---------------------------------------------------------------------------
# margin and broker-supplied figures
# ---------------------------------------------------------------------------
def test_margin_falls_back_to_leverage_when_the_terminal_is_silent(
    eurusd, eurusd_tick, account
):
    plan = plan_for(eurusd, eurusd_tick, account, side=Side.BUY, sl_points=300)
    expected = 0.2 * 100_000 * plan.entry_price / 100
    assert plan.required_margin == pytest.approx(expected)
    assert plan.margin_source == "leverage"


def test_terminal_supplied_values_win(eurusd, eurusd_tick, account):
    intent = TradeIntent(symbol="EURUSD", side=Side.BUY, sl_points=500)
    plan = calculate_trade_plan(
        intent,
        spec=eurusd,
        tick=eurusd_tick,
        account=account,
        profile=RiskProfile(),
        money_fn=lambda side, volume, o, c: -95.5 if c < o else 47.75,
        margin_fn=lambda side, volume, price: 111.0,
    )
    assert plan.pricing_source == "terminal"
    assert plan.max_loss == pytest.approx(95.5)
    assert plan.required_margin == pytest.approx(111.0)
    assert plan.margin_source == "terminal"


def test_explicit_volume_is_flagged_as_off_allocation(eurusd, eurusd_tick, account):
    plan = plan_for(eurusd, eurusd_tick, account, side=Side.BUY, sl_points=300, volume=0.05)
    assert plan.volume == 0.05
    assert not plan.volume_is_prescribed
    assert any("differs from the Rule 2 allocation" in w for w in plan.warnings)
