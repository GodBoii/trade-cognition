"""The gateway contract.

Every method is **synchronous and blocking** - implementations are only ever
invoked from the single MT5 worker thread owned by
:class:`~app.mt5.manager.Mt5Runtime`.  Implementations translate broker
structures into the domain value objects from :mod:`app.domain.market` and raise
:class:`~app.errors.Mt5Error` for transport failures.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

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
from .credentials import Mt5Credentials


class Mt5Gateway(ABC):
    """Broker operations the platform depends on."""

    name: str = "gateway"

    # ------------------------------------------------------------- lifecycle
    @abstractmethod
    def login(self, credentials: Mt5Credentials) -> AccountSnapshot:
        """Attach to the terminal and authenticate.  Idempotent per account."""

    @abstractmethod
    def shutdown(self) -> None:
        """Release the terminal connection."""

    @abstractmethod
    def current_login(self) -> int | None:
        """Login currently authenticated, or ``None``."""

    # ----------------------------------------------------------------- state
    @abstractmethod
    def account(self) -> AccountSnapshot:
        ...

    @abstractmethod
    def symbols(self, search: str | None = None, limit: int = 200) -> list[SymbolBrief]:
        ...

    @abstractmethod
    def symbol_spec(self, symbol: str) -> SymbolSpec:
        ...

    @abstractmethod
    def tick(self, symbol: str) -> Tick:
        ...

    @abstractmethod
    def positions(self, symbol: str | None = None) -> list[PositionSnapshot]:
        ...

    @abstractmethod
    def position(self, ticket: int) -> PositionSnapshot | None:
        ...

    @abstractmethod
    def deals(
        self,
        date_from: datetime,
        date_to: datetime,
        position_id: int | None = None,
    ) -> list[DealRecord]:
        ...

    @abstractmethod
    def price_extremes(self, symbol: str, since: datetime) -> tuple[float, float] | None:
        """Lowest and highest bid traded since ``since``, or ``None`` if unknown.

        A polling position manager that only looks at the *current* price will
        miss a target that was touched and retraced between two passes.  This
        gives it the range instead, so a rung is not skipped just because the
        poll landed at the wrong moment.  Implementations should degrade
        gracefully - returning ``None`` simply falls back to current-price
        comparison.
        """

    # ------------------------------------------------------------ arithmetic
    @abstractmethod
    def calc_margin(self, side: Side, symbol: str, volume: float, price: float) -> float | None:
        """Terminal-computed margin requirement, or ``None`` if unavailable."""

    @abstractmethod
    def calc_profit(
        self, side: Side, symbol: str, volume: float, price_open: float, price_close: float
    ) -> float | None:
        """Terminal-computed profit in account currency, or ``None``."""

    # --------------------------------------------------------------- trading
    @abstractmethod
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
        """Send a market order.  ``price`` is a hint only; MT5 fills at market."""

    @abstractmethod
    def close_position(
        self,
        *,
        ticket: int,
        volume: float | None = None,
        deviation: int = 20,
        magic: int = 0,
        comment: str = "",
    ) -> OrderResult:
        """Close ``volume`` lots of a position (all of it when ``volume`` is None)."""

    @abstractmethod
    def modify_stops(
        self,
        *,
        ticket: int,
        sl: float | None = None,
        tp: float | None = None,
    ) -> OrderResult:
        """Set the stop-loss and/or take-profit of an open position."""
