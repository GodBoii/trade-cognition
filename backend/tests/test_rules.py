"""The rules engine: what gets blocked and what the user is told."""

from __future__ import annotations

import pytest

from app.domain.enums import LotRuleMode, Severity, Side
from app.domain.market import AccountSnapshot, Tick
from app.domain.profile import RiskProfile
from app.domain.risk import TradeIntent, calculate_trade_plan
from app.domain.rules import RuleContext, evaluate


def build(spec, tick, account, profile=None, **intent_kwargs):
    intent = TradeIntent(symbol=spec.name, side=Side.BUY, **intent_kwargs)
    return calculate_trade_plan(
        intent, spec=spec, tick=tick, account=account, profile=profile or RiskProfile()
    )


def report_for(plan, spec, account, profile=None, **ctx_kwargs):
    return evaluate(
        plan,
        RuleContext(
            account=account, spec=spec, profile=profile or RiskProfile(), **ctx_kwargs
        ),
    )


def check(report, code):
    found = next((c for c in report.checks if c.code == code), None)
    assert found is not None, f"{code} was not evaluated"
    return found


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------
def test_a_compliant_trade_is_approved(eurusd, eurusd_tick, account):
    plan = build(eurusd, eurusd_tick, account, sl_points=400)
    report = report_for(plan, eurusd, account)

    assert report.approved
    assert report.violations == ()
    assert report.rejection_summary == ""
    # Every rule is reported, not just the failures.
    for code in (
        "RULE1_ONE_ACTIVE_TRADE",
        "RULE2_LOT_ALLOCATION",
        "RULE2_VOLUME_CONSTRAINTS",
        "RULE3_MAX_RISK",
    ):
        assert check(report, code).passed


# ---------------------------------------------------------------------------
# Rule 1
# ---------------------------------------------------------------------------
def test_rule1_blocks_a_second_entry_on_the_same_symbol(eurusd, eurusd_tick, account):
    plan = build(eurusd, eurusd_tick, account, sl_points=400)
    report = report_for(
        plan, eurusd, account, active_symbols=frozenset({"EURUSD"}), blocking_ticket=99
    )

    assert not report.approved
    violation = check(report, "RULE1_ONE_ACTIVE_TRADE")
    assert not violation.passed
    assert violation.severity is Severity.BLOCK
    assert "position #99" in violation.message
    assert "Other symbols remain tradable" in violation.message


def test_rule1_ignores_other_symbols(eurusd, eurusd_tick, account):
    plan = build(eurusd, eurusd_tick, account, sl_points=400)
    report = report_for(plan, eurusd, account, active_symbols=frozenset({"XAUUSD", "US500"}))
    assert report.approved


def test_rule1_is_case_insensitive(eurusd, eurusd_tick, account):
    plan = build(eurusd, eurusd_tick, account, sl_points=400)
    report = report_for(plan, eurusd, account, active_symbols=frozenset({"eurusd"}))
    assert not report.approved


def test_rule1_cannot_be_overridden(eurusd, eurusd_tick, account):
    plan = build(eurusd, eurusd_tick, account, sl_points=400)
    permissive = RiskProfile(allow_manual_override=True)
    report = report_for(
        plan,
        eurusd,
        account,
        profile=permissive,
        active_symbols=frozenset({"EURUSD"}),
        override_requested=True,
    )
    assert not report.approved
    assert not check(report, "RULE1_ONE_ACTIVE_TRADE").overridable


# ---------------------------------------------------------------------------
# Rule 2
# ---------------------------------------------------------------------------
def test_rule2_blocks_a_volume_off_the_allocation(eurusd, eurusd_tick, account):
    plan = build(eurusd, eurusd_tick, account, sl_points=400, volume=0.50)
    report = report_for(plan, eurusd, account)

    violation = check(report, "RULE2_LOT_ALLOCATION")
    assert not violation.passed
    assert "must be 0.2 lots, not 0.5" in violation.message
    assert violation.details["prescribed_volume"] == 0.2


def test_rule2_max_mode_allows_smaller_sizes(eurusd, eurusd_tick, account):
    profile = RiskProfile(lot_rule_mode=LotRuleMode.MAX)
    plan = build(eurusd, eurusd_tick, account, profile=profile, sl_points=400, volume=0.05)
    report = report_for(plan, eurusd, account, profile=profile)

    assert check(report, "RULE2_LOT_ALLOCATION").passed
    assert report.approved


def test_rule2_max_mode_still_blocks_oversizing(eurusd, eurusd_tick, account):
    profile = RiskProfile(lot_rule_mode=LotRuleMode.MAX)
    plan = build(eurusd, eurusd_tick, account, profile=profile, sl_points=400, volume=0.40)
    report = report_for(plan, eurusd, account, profile=profile)
    assert not check(report, "RULE2_LOT_ALLOCATION").passed


def test_rule2_allocation_can_be_overridden_when_permitted(eurusd, eurusd_tick, account):
    profile = RiskProfile(allow_manual_override=True)
    plan = build(eurusd, eurusd_tick, account, profile=profile, sl_points=400, volume=0.05)
    report = report_for(
        plan, eurusd, account, profile=profile, override_requested=True
    )

    assert report.approved
    assert "RULE2_LOT_ALLOCATION" in report.overridden
    assert check(report, "RULE2_LOT_ALLOCATION").message.startswith("OVERRIDDEN:")


def test_rule2_rejects_volume_off_the_broker_grid(eurusd, eurusd_tick, account):
    plan = build(eurusd, eurusd_tick, account, sl_points=400, volume=0.2)
    # Force an off-grid volume the calculator would normally have snapped.
    from dataclasses import replace

    plan = replace(plan, volume=0.205)
    report = report_for(plan, eurusd, account)
    violation = check(report, "RULE2_VOLUME_CONSTRAINTS")
    assert not violation.passed
    assert "not a multiple of the 0.01 lot step" in violation.message


# ---------------------------------------------------------------------------
# Rule 3
# ---------------------------------------------------------------------------
def test_rule3_blocks_a_stop_that_risks_more_than_two_percent(eurusd, eurusd_tick, account):
    plan = build(eurusd, eurusd_tick, account, sl_points=1500)
    report = report_for(plan, eurusd, account)

    violation = check(report, "RULE3_MAX_RISK")
    assert not violation.passed
    assert "exceeds the 2.00% ceiling" in violation.message
    # The message must be actionable: it states both remedies.
    assert "Move the stop no further than 1000 points" in violation.message
    assert "reduce size to 0.13 lots" in violation.message


def test_rule3_allows_a_stop_exactly_at_the_ceiling(xauusd, account):
    tick = Tick(symbol="XAUUSD", bid=2350.00, ask=2350.30)
    plan = build(xauusd, tick, account, sl_points=1000)
    assert plan.max_loss == pytest.approx(200.0)
    assert report_for(plan, xauusd, account).approved


def test_rule3_cannot_be_overridden(eurusd, eurusd_tick, account):
    profile = RiskProfile(allow_manual_override=True)
    plan = build(eurusd, eurusd_tick, account, profile=profile, sl_points=1500)
    report = report_for(
        plan, eurusd, account, profile=profile, override_requested=True
    )
    assert not report.approved
    assert not check(report, "RULE3_MAX_RISK").overridable


def test_rule3_scales_with_a_configured_ceiling(eurusd, eurusd_tick, account):
    profile = RiskProfile(max_risk_pct=1.0)
    plan = build(eurusd, eurusd_tick, account, profile=profile, sl_points=600)
    report = report_for(plan, eurusd, account, profile=profile)
    # 0.20 lots x 600 points = 120 USD > 1% of 10,000.
    assert plan.max_loss == pytest.approx(120.0)
    assert not check(report, "RULE3_MAX_RISK").passed


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------
def test_minimum_stop_distance_guard(xauusd, account):
    tick = Tick(symbol="XAUUSD", bid=2350.00, ask=2350.30)
    plan = build(xauusd, tick, account, sl_points=20)  # broker minimum is 50
    report = report_for(plan, xauusd, account)

    violation = check(report, "GUARD_MIN_STOP_DISTANCE")
    assert not violation.passed
    assert "requires at least 50 points" in violation.message


def test_margin_cap_guard(eurusd, eurusd_tick):
    thin = AccountSnapshot(
        login=1, currency="USD", balance=10_000, equity=10_000, margin_free=300, leverage=100
    )
    plan = build(eurusd, eurusd_tick, thin, sl_points=400)
    report = report_for(plan, eurusd, thin)

    violation = check(report, "GUARD_MARGIN")
    assert not violation.passed
    assert "exceeds the 50% cap on free margin" in violation.message


def test_symbol_closed_guard(eurusd, eurusd_tick, account):
    from dataclasses import replace

    closed = replace(eurusd, trade_allowed=False)
    plan = build(closed, eurusd_tick, account, sl_points=400)
    report = report_for(plan, closed, account)
    assert not check(report, "GUARD_SYMBOL_TRADABLE").passed


def test_account_with_investor_password_cannot_trade(eurusd, eurusd_tick):
    from dataclasses import replace

    account = AccountSnapshot(
        login=1, currency="USD", balance=10_000, equity=10_000, margin_free=10_000, leverage=100
    )
    read_only = replace(account, trade_allowed=False)
    plan = build(eurusd, eurusd_tick, read_only, sl_points=400)
    report = report_for(plan, eurusd, read_only)

    violation = check(report, "GUARD_ACCOUNT_TRADE_ALLOWED")
    assert not violation.passed
    assert "investor password" in violation.message


def test_daily_loss_limit_pauses_trading(eurusd, eurusd_tick, account):
    profile = RiskProfile(max_daily_loss_pct=4.0)
    plan = build(eurusd, eurusd_tick, account, profile=profile, sl_points=400)
    report = report_for(
        plan, eurusd, account, profile=profile, realised_pnl_today=-380.0
    )

    violation = check(report, "GUARD_DAILY_LOSS")
    assert not violation.passed
    assert "Trading is paused until tomorrow" in violation.message


def test_daily_loss_guard_is_absent_when_disabled(eurusd, eurusd_tick, account):
    plan = build(eurusd, eurusd_tick, account, sl_points=400)
    report = report_for(plan, eurusd, account, realised_pnl_today=-9_000.0)
    assert all(c.code != "GUARD_DAILY_LOSS" for c in report.checks)


def test_max_concurrent_positions_guard(eurusd, eurusd_tick, account):
    profile = RiskProfile(max_concurrent_positions=2)
    plan = build(eurusd, eurusd_tick, account, profile=profile, sl_points=400)
    report = report_for(plan, eurusd, account, profile=profile, open_position_count=2)
    assert not check(report, "GUARD_MAX_CONCURRENT").passed


def test_multiple_violations_are_all_reported(eurusd, eurusd_tick, account):
    plan = build(eurusd, eurusd_tick, account, sl_points=1500, volume=0.9)
    report = report_for(plan, eurusd, account, active_symbols=frozenset({"EURUSD"}))

    codes = {v.code for v in report.violations}
    assert {"RULE1_ONE_ACTIVE_TRADE", "RULE2_LOT_ALLOCATION", "RULE3_MAX_RISK"} <= codes
    assert report.rejection_summary.startswith("(1)")
