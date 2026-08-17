"""Market data value objects.

These are the *only* shapes the domain understands.  The MT5 gateway layer is
responsible for translating terminal structures into these objects, which keeps
the risk mathematics free of any broker specific field naming.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .enums import Side
from .quant import decimals_of, round_price, safe_div


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    """Everything needed to price, size and validate an order on one symbol.

    Field names mirror MT5's ``SymbolInfo`` semantics:

    * ``point``     - smallest representable price increment (10**-digits).
    * ``tick_size`` - smallest increment an order price may use
      (``trade_tick_size``); usually equals ``point`` but not always.
    * ``tick_value_loss`` / ``tick_value_profit`` - profit currency amount
      gained/lost per ``tick_size`` move on ``1.00`` lot
      (``trade_tick_value_loss`` / ``trade_tick_value_profit``).
    * ``stops_level_points`` - minimum SL/TP distance from price, in points
      (``trade_stops_level``).  Zero means the broker publishes no minimum.
    """

    name: str
    digits: int = 5
    point: float = 0.00001
    tick_size: float = 0.00001
    tick_value_loss: float = 1.0
    tick_value_profit: float = 1.0
    contract_size: float = 100_000.0
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    stops_level_points: int = 0
    freeze_level_points: int = 0
    currency_base: str = ""
    currency_profit: str = "USD"
    currency_margin: str = ""
    description: str = ""
    trade_allowed: bool = True
    margin_rate: float = 0.0
    """Fraction of notional required as margin. 0 means "ask the terminal"."""

    # ---------------------------------------------------------------- geometry
    @property
    def effective_tick_size(self) -> float:
        """``tick_size`` with sane fallbacks, never zero."""
        for candidate in (self.tick_size, self.point):
            if candidate and candidate > 0:
                return candidate
        return 10 ** -max(self.digits, 1)

    @property
    def effective_point(self) -> float:
        if self.point and self.point > 0:
            return self.point
        return 10 ** -max(self.digits, 1)

    @property
    def volume_decimals(self) -> int:
        return decimals_of(self.volume_step or 0.01)

    # ------------------------------------------------------------- money value
    @property
    def money_per_price_unit_per_lot(self) -> float:
        """Account/profit currency per **1.0 of price movement** on 1.00 lot.

        This is the single primitive all risk math is built on.  Derived from
        the broker published tick value so exotic contract sizes, index CFDs
        and metals are handled without special cases::

            money_per_price_unit = tick_value / tick_size

        When the broker reports no tick value we fall back to the contract
        size, which is exact whenever the profit currency equals the account
        currency (the common case for USD accounts on USD-quoted symbols).
        """
        tick_value = self.tick_value_loss or self.tick_value_profit
        if tick_value and tick_value > 0:
            return safe_div(tick_value, self.effective_tick_size, 0.0)
        return float(self.contract_size or 0.0)

    def money_per_price_unit(self, volume: float) -> float:
        """Money per 1.0 of price movement for ``volume`` lots."""
        return self.money_per_price_unit_per_lot * volume

    def money_per_point(self, volume: float) -> float:
        """Money per single *point* of movement for ``volume`` lots."""
        return self.money_per_price_unit(volume) * self.effective_point

    def money_for_move(self, price_distance: float, volume: float) -> float:
        """Absolute money value of a ``price_distance`` move on ``volume`` lots."""
        return abs(price_distance) * self.money_per_price_unit(volume)

    def price_distance_for_money(self, money: float, volume: float) -> float:
        """Inverse of :meth:`money_for_move` - how far price must travel."""
        return safe_div(abs(money), self.money_per_price_unit(volume), 0.0)

    # ------------------------------------------------------------- conversions
    def price_diff(self, a: float, b: float) -> float:
        """Absolute distance between two prices, cleaned of float noise.

        Both operands are already quantised to ``digits``, so their difference
        cannot legitimately carry more precision than that.
        """
        return round(abs(a - b), self.digits)

    def to_points(self, price_distance: float) -> float:
        points = safe_div(abs(price_distance), self.effective_point, 0.0)
        # Points are integral on a well-formed grid; strip residual float dust.
        return round(points, 2)

    def from_points(self, points: float) -> float:
        return points * self.effective_point

    def normalise_price(self, price: float) -> float:
        return round_price(price, self.digits, self.effective_tick_size)

    @property
    def min_stop_distance(self) -> float:
        """Minimum SL/TP distance from price expressed in price units."""
        return self.from_points(self.stops_level_points)

    @property
    def freeze_distance(self) -> float:
        return self.from_points(self.freeze_level_points)


@dataclass(frozen=True, slots=True)
class Tick:
    """Latest quote for a symbol."""

    symbol: str
    bid: float
    ask: float
    time: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.ask + self.bid) / 2.0

    def entry_price(self, side: Side) -> float:
        """Price a market entry would execute at."""
        return self.ask if side is Side.BUY else self.bid

    def exit_price(self, side: Side) -> float:
        """Price a market exit of a ``side`` position would execute at."""
        return self.bid if side is Side.BUY else self.ask


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Point-in-time account state as reported by MT5."""

    login: int
    name: str = ""
    server: str = ""
    currency: str = "USD"
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    margin_free: float = 0.0
    margin_level: float = 0.0
    profit: float = 0.0
    leverage: int = 0
    trade_allowed: bool = True
    trade_expert: bool = True
    company: str = ""

    def capital(self, basis: str, fixed: float = 0.0) -> float:
        """Resolve "trading capital" for the configured basis."""
        if basis == "equity":
            return self.equity
        if basis == "fixed":
            return fixed
        return self.balance


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """An open MT5 position."""

    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    price_current: float
    sl: float = 0.0
    tp: float = 0.0
    profit: float = 0.0
    swap: float = 0.0
    commission: float = 0.0
    magic: int = 0
    comment: str = ""
    opened_at: datetime | None = None

    @property
    def net_profit(self) -> float:
        return self.profit + self.swap + self.commission


@dataclass(frozen=True, slots=True)
class DealRecord:
    """A completed deal from trade history (used to reconcile realised P/L)."""

    ticket: int
    order: int
    position_id: int
    symbol: str
    volume: float
    price: float
    profit: float
    swap: float = 0.0
    commission: float = 0.0
    entry: str = ""
    comment: str = ""
    time: datetime | None = None

    @property
    def net_profit(self) -> float:
        return self.profit + self.swap + self.commission


@dataclass(frozen=True, slots=True)
class OrderResult:
    """Normalised result of a trade request."""

    ok: bool
    retcode: int
    comment: str = ""
    order: int = 0
    deal: int = 0
    position: int = 0
    volume: float = 0.0
    price: float = 0.0
    request_id: int = 0

    def describe(self) -> str:
        return f"retcode={self.retcode} {self.comment}".strip()


@dataclass(frozen=True, slots=True)
class SymbolBrief:
    """Lightweight symbol entry for pickers and search results."""

    name: str
    description: str = ""
    path: str = ""
    digits: int = 5
    trade_allowed: bool = True
    bid: float = 0.0
    ask: float = 0.0

    @property
    def group(self) -> str:
        """Top level folder of the symbol path (``Forex``, ``Metals``, ...)."""
        return self.path.split("\\", 1)[0] if self.path else ""
