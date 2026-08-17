"""Pre-entry assessment: calculate, then judge.

:func:`assess` is the single entry point used by both the *preview* endpoint and
the *execute* endpoint, so what the user sees before pressing the button is
computed by exactly the same code path that later authorises the order.

The whole assessment happens inside **one** serialised MT5 visit: account,
symbol spec, quote and open positions are read together, and the calculator's
``money_fn``/``margin_fn`` call straight into the terminal from that same
thread.  Two benefits: the numbers are internally consistent (no drift between
the balance and the quote), and the broker's own currency conversion is used for
profit and margin instead of an approximation.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ManagedTrade, Mt5AccountRow, User
from ..domain.enums import TradeStatus
from ..domain.ladder import Ladder, get_ladder
from ..domain.market import AccountSnapshot, PositionSnapshot, SymbolSpec, Tick
from ..domain.profile import RiskProfile
from ..domain.risk import TradeIntent, TradePlan, calculate_trade_plan
from ..domain.rules import RuleContext, RulesReport, evaluate
from ..mt5.gateway import Mt5Gateway
from . import accounts as accounts_service
from . import journal, users as users_service


@dataclass(frozen=True, slots=True)
class Assessment:
    """Everything produced by one pre-trade evaluation."""

    plan: TradePlan
    report: RulesReport
    account: AccountSnapshot
    spec: SymbolSpec
    tick: Tick
    positions: tuple[PositionSnapshot, ...]
    profile: RiskProfile
    ladder: Ladder
    active_symbols: tuple[str, ...]
    blocking_ticket: int | None

    @property
    def approved(self) -> bool:
        return self.report.approved


def active_trade_symbols(session: Session, user: User) -> dict[str, ManagedTrade]:
    """Symbols the user currently has a managed trade on, keyed by symbol."""
    rows = session.scalars(
        select(ManagedTrade).where(
            ManagedTrade.user_id == user.id,
            ManagedTrade.status.in_([s.value for s in TradeStatus.active()]),
        )
    )
    return {row.symbol.upper(): row for row in rows}


def find_active_trade(session: Session, user: User, symbol: str) -> ManagedTrade | None:
    return active_trade_symbols(session, user).get(symbol.upper())


async def assess(
    session: Session,
    user: User,
    account_row: Mt5AccountRow,
    intent: TradeIntent,
    *,
    override: bool = False,
    record: bool = False,
) -> Assessment:
    """Calculate the plan and run it through the rules engine.

    ``record=True`` writes the outcome to the decision journal (used by the
    execute path and by explicitly journalled previews).
    """
    profile = users_service.get_profile(session, user)
    ladder = get_ladder(intent.ladder_preset or profile.ladder_preset)
    symbol = intent.symbol

    client = accounts_service.client_for(account_row)

    def work(
        gw: Mt5Gateway,
    ) -> tuple[AccountSnapshot, SymbolSpec, Tick, tuple[PositionSnapshot, ...], TradePlan]:
        account = gw.account()
        spec = gw.symbol_spec(symbol)
        tick = gw.tick(spec.name)
        positions = tuple(gw.positions())

        plan = calculate_trade_plan(
            intent,
            spec=spec,
            tick=tick,
            account=account,
            profile=profile,
            money_fn=lambda side, volume, price_open, price_close: gw.calc_profit(
                side, spec.name, volume, price_open, price_close
            ),
            margin_fn=lambda side, volume, price: gw.calc_margin(
                side, spec.name, volume, price
            ),
            ladder=ladder,
        )
        return account, spec, tick, positions, plan

    account, spec, tick, positions, plan = await client.run(work, label="assess")

    # --- assemble the rule context ----------------------------------------
    managed = active_trade_symbols(session, user)
    live_symbols = {p.symbol.upper() for p in positions}
    active_symbols = sorted(set(managed) | live_symbols)

    blocking_ticket: int | None = None
    for position in positions:
        if position.symbol.upper() == spec.name.upper():
            blocking_ticket = position.ticket
            break
    if blocking_ticket is None:
        managed_row = managed.get(spec.name.upper())
        if managed_row is not None:
            blocking_ticket = managed_row.position_ticket

    context = RuleContext(
        account=account,
        spec=spec,
        profile=profile,
        active_symbols=frozenset(active_symbols),
        open_position_count=len(positions),
        realised_pnl_today=journal.realised_pnl_today(session, user_id=user.id),
        override_requested=override,
        blocking_ticket=blocking_ticket,
    )

    report = evaluate(plan, context)

    if record:
        journal.record_decision(
            session,
            user_id=user.id,
            mt5_account_id=account_row.id,
            plan=plan,
            report=report,
        )

    return Assessment(
        plan=plan,
        report=report,
        account=account,
        spec=spec,
        tick=tick,
        positions=positions,
        profile=profile,
        ladder=ladder,
        active_symbols=tuple(active_symbols),
        blocking_ticket=blocking_ticket,
    )


async def what_if_stop_scan(
    session: Session,
    user: User,
    account_row: Mt5AccountRow,
    *,
    symbol: str,
    side,
    stop_points: list[float],
) -> list[dict[str, float | bool]]:
    """Risk at several candidate stop distances, for the stop-picker UI.

    One serialised MT5 visit for the whole scan.
    """
    profile = users_service.get_profile(session, user)
    client = accounts_service.client_for(account_row)

    def work(gw: Mt5Gateway) -> list[dict[str, float | bool]]:
        account = gw.account()
        spec = gw.symbol_spec(symbol)
        tick = gw.tick(spec.name)
        capital = profile.capital(account)
        volume = profile.prescribed_volume(capital, spec)
        ceiling = profile.max_risk_money(capital)
        entry = tick.entry_price(side)

        rows: list[dict[str, float | bool]] = []
        for points in stop_points:
            distance = spec.from_points(points)
            loss = spec.money_for_move(distance, volume)
            rows.append(
                {
                    "stop_points": points,
                    "stop_price": spec.normalise_price(entry - side.sign * distance),
                    "loss": round(loss, 2),
                    "risk_pct": round((loss / capital * 100.0) if capital else 0.0, 3),
                    "within_limit": round(loss, 2) <= round(ceiling, 2),
                }
            )
        return rows

    return await client.run(work, label="stop_scan")
