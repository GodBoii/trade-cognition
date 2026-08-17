"""In-process broker simulator.

Purpose: run and verify the *entire* platform - risk engine, rules, order
routing, ladder execution, dashboards - without a MetaTrader terminal.  It is
used by the automated tests and by ``TC_MT5_GATEWAY=mock`` for development.

What it models faithfully (because the rest of the system depends on it):

* per-symbol lot grids, digits, tick values and minimum stop distances;
* bid/ask spread, and fills at ask for buys / bid for sells;
* partial closes that book realised profit into the balance and emit deals;
* broker-side SL/TP execution when price trades through the level;
* margin, equity and free margin arithmetic.

What it deliberately does not model: slippage, requotes, commissions, swap,
weekend gaps and session hours.  Numbers from the mock are *plausible*, never
authoritative - see ``docs/06-mt5-integration.md``.
"""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from ..domain.enums import Side
from ..domain.market import (
    AccountSnapshot,
    DealRecord,
    OrderResult,
    PositionSnapshot,
    SymbolBrief,
    SymbolSpec,
    Tick,
)
from ..domain.quant import round_volume
from ..errors import Mt5Error
from .credentials import Mt5Credentials
from .gateway import Mt5Gateway

RETCODE_DONE = 10009


def _spec(
    name: str,
    *,
    digits: int,
    point: float,
    tick_value: float,
    contract: float,
    volume_min: float = 0.01,
    volume_step: float = 0.01,
    volume_max: float = 100.0,
    stops: int = 0,
    path: str = "Forex",
    description: str = "",
    profit_currency: str = "USD",
) -> SymbolSpec:
    return SymbolSpec(
        name=name,
        digits=digits,
        point=point,
        tick_size=point,
        tick_value_loss=tick_value,
        tick_value_profit=tick_value,
        contract_size=contract,
        volume_min=volume_min,
        volume_max=volume_max,
        volume_step=volume_step,
        stops_level_points=stops,
        currency_base=name[:3],
        currency_profit=profit_currency,
        currency_margin=name[:3],
        description=description or name,
        trade_allowed=True,
    )


@dataclass
class _Instrument:
    spec: SymbolSpec
    bid: float
    spread_points: float
    volatility: float
    path: str = "Forex"
    #: Recent bid prints, so ``price_extremes`` can answer "did it trade through
    #: this level?" the way tick history does on a real terminal.
    history: deque[tuple[datetime, float]] = field(
        default_factory=lambda: deque(maxlen=4000)
    )

    @property
    def ask(self) -> float:
        return self.spec.normalise_price(self.bid + self.spread_points * self.spec.effective_point)


def _default_instruments() -> dict[str, _Instrument]:
    return {
        i.spec.name: i
        for i in (
            _Instrument(
                _spec("EURUSD", digits=5, point=1e-5, tick_value=1.0, contract=100_000,
                      description="Euro vs US Dollar"),
                bid=1.09500, spread_points=12, volatility=0.0006, path="Forex\\Majors",
            ),
            _Instrument(
                _spec("GBPUSD", digits=5, point=1e-5, tick_value=1.0, contract=100_000,
                      description="Great Britain Pound vs US Dollar"),
                bid=1.26500, spread_points=15, volatility=0.0007, path="Forex\\Majors",
            ),
            _Instrument(
                _spec("USDJPY", digits=3, point=1e-3, tick_value=0.667, contract=100_000,
                      description="US Dollar vs Japanese Yen", profit_currency="JPY"),
                bid=157.250, spread_points=14, volatility=0.0006, path="Forex\\Majors",
            ),
            _Instrument(
                _spec("XAUUSD", digits=2, point=0.01, tick_value=1.0, contract=100,
                      stops=50, description="Gold vs US Dollar", path="Metals"),
                bid=2350.00, spread_points=30, volatility=0.0055, path="Metals",
            ),
            _Instrument(
                _spec("US500", digits=1, point=0.1, tick_value=0.1, contract=1,
                      volume_min=0.1, volume_step=0.1, volume_max=200.0, stops=20,
                      description="S&P 500 Index CFD", path="Indices"),
                bid=5400.0, spread_points=6, volatility=0.0045, path="Indices",
            ),
            _Instrument(
                _spec("NAS100", digits=1, point=0.1, tick_value=0.1, contract=1,
                      volume_min=0.1, volume_step=0.1, volume_max=200.0, stops=25,
                      description="Nasdaq 100 Index CFD", path="Indices"),
                bid=19500.0, spread_points=15, volatility=0.0075, path="Indices",
            ),
            _Instrument(
                _spec("BTCUSD", digits=2, point=0.01, tick_value=0.01, contract=1,
                      volume_min=0.01, volume_step=0.01, volume_max=10.0, stops=500,
                      description="Bitcoin vs US Dollar", path="Crypto"),
                bid=64000.00, spread_points=4000, volatility=0.02, path="Crypto",
            ),
        )
    }


@dataclass
class _Position:
    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    sl: float = 0.0
    tp: float = 0.0
    magic: int = 0
    comment: str = ""
    opened_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MockBroker:
    """Simulated account state for a single login."""

    #: Simulated seconds between random-walk updates.
    TICK_INTERVAL = 0.4

    def __init__(
        self,
        login: int,
        *,
        server: str = "MockBroker-Demo",
        balance: float = 10_000.0,
        currency: str = "USD",
        leverage: int = 100,
        seed: int | None = None,
        auto_drift: bool = True,
    ) -> None:
        self.login = login
        self.server = server
        self.currency = currency
        self.leverage = leverage
        self.balance = balance
        self.auto_drift = auto_drift

        self.instruments = _default_instruments()
        self.positions: dict[int, _Position] = {}
        self.deals: list[DealRecord] = []
        self.closed_positions: list[dict[str, object]] = []

        self._rng = random.Random(seed if seed is not None else login)
        self._next_ticket = 500_000_001
        self._next_deal = 900_000_001
        self._last_move = time.monotonic()
        self._drifting = False
        self._lock = threading.RLock()

    # ------------------------------------------------------------ price feed
    def instrument(self, symbol: str) -> _Instrument:
        key = symbol.upper()
        if key not in self.instruments:
            raise Mt5Error(
                f"{symbol} is not offered by this account. Available symbols: "
                f"{', '.join(sorted(self.instruments))}.",
                code="symbol_not_found",
            )
        return self.instruments[key]

    def set_price(self, symbol: str, bid: float) -> None:
        """Force a price (tests drive the market through this)."""
        with self._lock:
            self._set_bid(self.instrument(symbol), bid)
            self._process_stops()

    def _set_bid(self, inst: _Instrument, value: float) -> None:
        inst.bid = inst.spec.normalise_price(max(inst.spec.effective_point, value))
        inst.history.append((datetime.now(UTC), inst.bid))

    def extremes(self, symbol: str, since: datetime) -> tuple[float, float]:
        """Lowest and highest bid printed since ``since`` (current bid included)."""
        with self._lock:
            inst = self.instrument(symbol)
            values = [bid for stamp, bid in inst.history if stamp >= since]
            values.append(inst.bid)
            return (min(values), max(values))

    def advance(self, steps: int = 1) -> None:
        """Apply ``steps`` random-walk updates regardless of the clock."""
        with self._lock:
            for _ in range(steps):
                self._walk()
            self._process_stops()

    def _maybe_drift(self) -> None:
        # ``_process_stops`` closes positions, which re-enters the read paths;
        # ``_drifting`` keeps that from recursing back into the price walk.
        if not self.auto_drift or self._drifting:
            return
        now = time.monotonic()
        if now - self._last_move < self.TICK_INTERVAL:
            return
        with self._lock:
            self._drifting = True
            try:
                self._last_move = now
                self._walk()
                self._process_stops()
            finally:
                self._drifting = False

    def _walk(self) -> None:
        for inst in self.instruments.values():
            shock = self._rng.gauss(0.0, 1.0) * inst.volatility / 40.0
            self._set_bid(inst, inst.bid * (1 + shock))

    # -------------------------------------------------------------- accounting
    def _position_profit(self, pos: _Position, price: float | None = None) -> float:
        inst = self.instrument(pos.symbol)
        current = price if price is not None else self._exit_price(inst, pos.side)
        return (current - pos.price_open) * pos.side.sign * inst.spec.money_per_price_unit(pos.volume)

    @staticmethod
    def _exit_price(inst: _Instrument, side: Side) -> float:
        return inst.bid if side is Side.BUY else inst.ask

    @staticmethod
    def _entry_price(inst: _Instrument, side: Side) -> float:
        return inst.ask if side is Side.BUY else inst.bid

    def position_margin(self, pos: _Position) -> float:
        inst = self.instrument(pos.symbol)
        notional = pos.volume * inst.spec.contract_size * pos.price_open
        return notional / max(self.leverage, 1)

    def snapshot(self) -> AccountSnapshot:
        self._maybe_drift()
        with self._lock:
            floating = sum(self._position_profit(p) for p in self.positions.values())
            margin = sum(self.position_margin(p) for p in self.positions.values())
            equity = self.balance + floating
            return AccountSnapshot(
                login=self.login,
                name=f"Mock Account {self.login}",
                server=self.server,
                currency=self.currency,
                balance=round(self.balance, 2),
                equity=round(equity, 2),
                margin=round(margin, 2),
                margin_free=round(equity - margin, 2),
                margin_level=round((equity / margin * 100.0) if margin else 0.0, 2),
                profit=round(floating, 2),
                leverage=self.leverage,
                trade_allowed=True,
                trade_expert=True,
                company="Mock Broker Ltd (simulated)",
            )

    # ----------------------------------------------------------------- trading
    def open(
        self,
        *,
        symbol: str,
        side: Side,
        volume: float,
        sl: float,
        tp: float,
        magic: int,
        comment: str,
    ) -> OrderResult:
        with self._lock:
            inst = self.instrument(symbol)
            spec = inst.spec
            volume = round_volume(volume, spec.volume_step, "nearest")

            if volume < spec.volume_min or volume > spec.volume_max:
                return OrderResult(
                    ok=False, retcode=10014,
                    comment=f"Invalid volume {volume:g} for {spec.name}.",
                )

            price = self._entry_price(inst, side)
            snapshot = self.snapshot()
            probe = _Position(0, spec.name, side, volume, price)
            if self.position_margin(probe) > snapshot.margin_free:
                return OrderResult(ok=False, retcode=10019, comment="Not enough money.")

            if sl and spec.min_stop_distance and abs(price - sl) < spec.min_stop_distance:
                return OrderResult(ok=False, retcode=10016, comment="Invalid stops.")

            ticket = self._next_ticket
            self._next_ticket += 1
            self.positions[ticket] = _Position(
                ticket=ticket,
                symbol=spec.name,
                side=side,
                volume=volume,
                price_open=price,
                sl=spec.normalise_price(sl) if sl else 0.0,
                tp=spec.normalise_price(tp) if tp else 0.0,
                magic=magic,
                comment=comment,
            )
            deal = self._book_deal(ticket, spec.name, volume, price, 0.0, "in", comment)
            return OrderResult(
                ok=True, retcode=RETCODE_DONE, comment="Request completed.",
                order=ticket, deal=deal, position=ticket, volume=volume, price=price,
            )

    def close(self, ticket: int, volume: float | None, comment: str = "") -> OrderResult:
        with self._lock:
            pos = self.positions.get(ticket)
            if pos is None:
                return OrderResult(ok=False, retcode=10036, comment="Position already closed.")

            inst = self.instrument(pos.symbol)
            spec = inst.spec
            requested = pos.volume if volume is None else round_volume(
                min(volume, pos.volume), spec.volume_step, "nearest"
            )
            if requested <= 0:
                return OrderResult(ok=False, retcode=10014, comment="Invalid close volume.")
            if requested > pos.volume + 1e-9:
                return OrderResult(ok=False, retcode=10038, comment="Close volume too large.")

            price = self._exit_price(inst, pos.side)
            realised = (
                (price - pos.price_open) * pos.side.sign * spec.money_per_price_unit(requested)
            )
            self.balance = round(self.balance + realised, 2)
            deal = self._book_deal(
                ticket, spec.name, requested, price, realised, "out", comment
            )

            remaining = round_volume(pos.volume - requested, spec.volume_step, "nearest")
            if remaining <= 0:
                self.closed_positions.append(
                    {"ticket": ticket, "symbol": spec.name, "closed_at": datetime.now(UTC)}
                )
                del self.positions[ticket]
            else:
                self.positions[ticket] = replace(pos, volume=remaining)

            return OrderResult(
                ok=True, retcode=RETCODE_DONE, comment="Request completed.",
                order=ticket, deal=deal, position=ticket, volume=requested, price=price,
            )

    def modify(self, ticket: int, sl: float | None, tp: float | None) -> OrderResult:
        with self._lock:
            pos = self.positions.get(ticket)
            if pos is None:
                return OrderResult(ok=False, retcode=10036, comment="Position already closed.")
            inst = self.instrument(pos.symbol)
            spec = inst.spec

            # Real servers reject stops on the wrong side of the market or inside
            # the stops level; mirror that so our own logic gets caught here.
            market = self._exit_price(inst, pos.side)
            if sl:
                wrong = (pos.side is Side.BUY and sl >= market) or (
                    pos.side is Side.SELL and sl <= market
                )
                too_close = spec.min_stop_distance and abs(market - sl) < spec.min_stop_distance
                if wrong or too_close:
                    return OrderResult(
                        ok=False,
                        retcode=10016,
                        comment=(
                            f"Invalid stops - SL {sl:.{spec.digits}f} against market "
                            f"{market:.{spec.digits}f} for a {pos.side.value}."
                        ),
                    )

            self.positions[ticket] = replace(
                pos,
                sl=spec.normalise_price(sl) if sl else pos.sl,
                tp=spec.normalise_price(tp) if tp else pos.tp,
            )
            return OrderResult(
                ok=True, retcode=RETCODE_DONE, comment="Request completed.",
                order=ticket, position=ticket,
            )

    def _book_deal(
        self,
        position: int,
        symbol: str,
        volume: float,
        price: float,
        profit: float,
        entry: str,
        comment: str,
    ) -> int:
        deal_id = self._next_deal
        self._next_deal += 1
        self.deals.append(
            DealRecord(
                ticket=deal_id,
                order=position,
                position_id=position,
                symbol=symbol,
                volume=volume,
                price=price,
                profit=round(profit, 2),
                entry=entry,
                comment=comment,
                time=datetime.now(UTC),
            )
        )
        return deal_id

    def _process_stops(self) -> None:
        """Broker-side SL/TP execution: close anything price has traded through."""
        for ticket in list(self.positions):
            pos = self.positions.get(ticket)
            if pos is None:
                continue
            inst = self.instrument(pos.symbol)
            price = self._exit_price(inst, pos.side)
            hit_sl = pos.sl and (
                (pos.side is Side.BUY and price <= pos.sl)
                or (pos.side is Side.SELL and price >= pos.sl)
            )
            hit_tp = pos.tp and (
                (pos.side is Side.BUY and price >= pos.tp)
                or (pos.side is Side.SELL and price <= pos.tp)
            )
            if hit_sl:
                self.close(ticket, None, comment="[sl]")
            elif hit_tp:
                self.close(ticket, None, comment="[tp]")

    # ------------------------------------------------------------------ views
    def position_snapshots(self, symbol: str | None = None) -> list[PositionSnapshot]:
        self._maybe_drift()
        with self._lock:
            out: list[PositionSnapshot] = []
            for pos in self.positions.values():
                if symbol and pos.symbol.upper() != symbol.upper():
                    continue
                inst = self.instrument(pos.symbol)
                current = self._exit_price(inst, pos.side)
                out.append(
                    PositionSnapshot(
                        ticket=pos.ticket,
                        symbol=pos.symbol,
                        side=pos.side,
                        volume=pos.volume,
                        price_open=pos.price_open,
                        price_current=current,
                        sl=pos.sl,
                        tp=pos.tp,
                        profit=round(self._position_profit(pos, current), 2),
                        magic=pos.magic,
                        comment=pos.comment,
                        opened_at=pos.opened_at,
                    )
                )
            return sorted(out, key=lambda p: p.ticket)


class MockUniverse:
    """Registry of simulated accounts, keyed by login."""

    def __init__(self, *, auto_drift: bool = True, balance: float = 10_000.0) -> None:
        self.auto_drift = auto_drift
        self.balance = balance
        self._brokers: dict[int, MockBroker] = {}
        self._lock = threading.Lock()

    def broker(self, login: int, server: str = "MockBroker-Demo") -> MockBroker:
        with self._lock:
            broker = self._brokers.get(login)
            if broker is None:
                broker = MockBroker(
                    login,
                    server=server,
                    balance=self.balance,
                    auto_drift=self.auto_drift,
                )
                self._brokers[login] = broker
            return broker

    def reset(self) -> None:
        with self._lock:
            self._brokers.clear()


#: Process-wide universe used when ``TC_MT5_GATEWAY=mock``.
UNIVERSE = MockUniverse()


class MockMt5Gateway(Mt5Gateway):
    """Gateway implementation backed by :class:`MockBroker`."""

    name = "mock"

    #: Any password of this length or longer is accepted; shorter ones are
    #: rejected so the "bad credentials" path stays exercisable.
    MIN_PASSWORD_LENGTH = 4

    def __init__(self, universe: MockUniverse | None = None) -> None:
        self._universe = universe or UNIVERSE
        self._broker: MockBroker | None = None

    # ------------------------------------------------------------- lifecycle
    def login(self, credentials: Mt5Credentials) -> AccountSnapshot:
        if len(credentials.password or "") < self.MIN_PASSWORD_LENGTH:
            raise Mt5Error(
                "Login rejected by the simulated broker: the password is too short "
                f"(minimum {self.MIN_PASSWORD_LENGTH} characters).",
                code="invalid_credentials",
                details={"login": credentials.login},
            )
        if credentials.login <= 0:
            raise Mt5Error("Login rejected: account number must be positive.",
                           code="invalid_credentials")
        self._broker = self._universe.broker(
            int(credentials.login), credentials.server or "MockBroker-Demo"
        )
        return self._broker.snapshot()

    def shutdown(self) -> None:
        self._broker = None

    def current_login(self) -> int | None:
        return self._broker.login if self._broker else None

    @property
    def broker(self) -> MockBroker:
        if self._broker is None:
            raise Mt5Error("No simulated session; connect an account first.",
                           code="mt5_not_connected")
        return self._broker

    # ----------------------------------------------------------------- state
    def account(self) -> AccountSnapshot:
        return self.broker.snapshot()

    def symbols(self, search: str | None = None, limit: int = 200) -> list[SymbolBrief]:
        needle = (search or "").strip().lower()
        self.broker.snapshot()  # nudge the price feed
        out: list[SymbolBrief] = []
        for inst in self.broker.instruments.values():
            if needle and needle not in inst.spec.name.lower() and (
                needle not in inst.spec.description.lower()
            ):
                continue
            out.append(
                SymbolBrief(
                    name=inst.spec.name,
                    description=inst.spec.description,
                    path=inst.path,
                    digits=inst.spec.digits,
                    trade_allowed=inst.spec.trade_allowed,
                    bid=inst.bid,
                    ask=inst.ask,
                )
            )
        out.sort(key=lambda s: s.name)
        return out[:limit]

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        return self.broker.instrument(symbol).spec

    def tick(self, symbol: str) -> Tick:
        self.broker.snapshot()
        inst = self.broker.instrument(symbol)
        return Tick(symbol=inst.spec.name, bid=inst.bid, ask=inst.ask, time=datetime.now(UTC))

    def positions(self, symbol: str | None = None) -> list[PositionSnapshot]:
        return self.broker.position_snapshots(symbol)

    def position(self, ticket: int) -> PositionSnapshot | None:
        for pos in self.broker.position_snapshots():
            if pos.ticket == ticket:
                return pos
        return None

    def deals(
        self,
        date_from: datetime,
        date_to: datetime,
        position_id: int | None = None,
    ) -> list[DealRecord]:
        deals = self.broker.deals
        if position_id is not None:
            return [d for d in deals if d.position_id == position_id]
        return [
            d
            for d in deals
            if d.time is not None and date_from <= d.time <= date_to
        ]

    def price_extremes(self, symbol: str, since: datetime) -> tuple[float, float] | None:
        self.broker.snapshot()  # nudge the feed so history is current
        return self.broker.extremes(symbol, since)

    # ------------------------------------------------------------ arithmetic
    def calc_margin(self, side: Side, symbol: str, volume: float, price: float) -> float | None:
        spec = self.symbol_spec(symbol)
        notional = volume * spec.contract_size * price
        return round(notional / max(self.broker.leverage, 1), 2)

    def calc_profit(
        self, side: Side, symbol: str, volume: float, price_open: float, price_close: float
    ) -> float | None:
        spec = self.symbol_spec(symbol)
        return round(
            (price_close - price_open) * side.sign * spec.money_per_price_unit(volume), 2
        )

    # --------------------------------------------------------------- trading
    def open_position(
        self,
        *,
        symbol: str,
        side: Side,
        volume: float,
        sl: float | None = None,
        tp: float | None = None,
        price: float | None = None,
        deviation: int = 20,
        magic: int = 0,
        comment: str = "",
    ) -> OrderResult:
        return self.broker.open(
            symbol=symbol,
            side=side,
            volume=volume,
            sl=sl or 0.0,
            tp=tp or 0.0,
            magic=magic,
            comment=comment,
        )

    def close_position(
        self,
        *,
        ticket: int,
        volume: float | None = None,
        deviation: int = 20,
        magic: int = 0,
        comment: str = "",
    ) -> OrderResult:
        return self.broker.close(ticket, volume, comment=comment)

    def modify_stops(
        self,
        *,
        ticket: int,
        sl: float | None = None,
        tp: float | None = None,
    ) -> OrderResult:
        return self.broker.modify(ticket, sl, tp)
