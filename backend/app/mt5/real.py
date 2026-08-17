"""Live MetaTrader 5 gateway.

Wraps the official ``MetaTrader5`` package.  Everything the terminal exposes as
a namedtuple is converted into a domain value object here, so field naming
quirks (``trade_tick_value_loss``, ``trade_stops_level``, ...) stay contained.

Constraints inherited from the vendor package - documented, not worked around:

* It is a **process-wide singleton**.  ``initialize()``/``login()`` switch the
  account for the whole process, so calls must be serialised and the active
  login re-asserted before every operation (:meth:`_ensure_login`).
* It is **not thread safe**.  :class:`~app.mt5.manager.Mt5Runtime` guarantees a
  single calling thread.
* It requires a **running terminal on the same machine** with algorithmic
  trading enabled.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

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
from ..errors import Mt5Error, Mt5NotConnectedError
from ..logging_conf import get_logger
from .credentials import Mt5Credentials
from .gateway import Mt5Gateway

log = get_logger(__name__)

try:  # pragma: no cover - import guarded for non-Windows environments
    import MetaTrader5 as mt5  # type: ignore

    MT5_AVAILABLE = True
    MT5_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover
    mt5 = None  # type: ignore[assignment]
    MT5_AVAILABLE = False
    MT5_IMPORT_ERROR = str(exc)


# --- terminal constants (mirrored so the module imports without the package) --
TRADE_ACTION_DEAL = 1
TRADE_ACTION_SLTP = 6
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TIME_GTC = 0
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2
SYMBOL_TRADE_MODE_DISABLED = 0
POSITION_TYPE_BUY = 0
COPY_TICKS_INFO = 1
TIMEFRAME_M1 = 1
RETCODE_DONE = 10009
RETCODE_PLACED = 10008
RETCODE_DONE_PARTIAL = 10010
RETCODE_INVALID_FILL = 10030

#: Human explanations for the retcodes users actually hit.
RETCODE_MESSAGES: dict[int, str] = {
    10004: "Requote - the price moved before the order was accepted.",
    10006: "Request rejected by the dealer.",
    10007: "Request cancelled by the trader.",
    10008: "Order placed.",
    10009: "Request completed.",
    10010: "Only part of the requested volume was filled.",
    10013: "Invalid request.",
    10014: "Invalid volume for this symbol.",
    10015: "Invalid price.",
    10016: "Invalid stops - SL/TP is too close to price or on the wrong side.",
    10017: "Trading is disabled for this account.",
    10018: "Market is closed.",
    10019: "Not enough money to complete the request.",
    10020: "Prices changed.",
    10021: "No quotes to process the request.",
    10024: "Too many requests - the terminal is rate limiting.",
    10026: "Autotrading is disabled by the server.",
    10027: "Autotrading is disabled in the terminal (enable Algo Trading).",
    10030: "Unsupported order filling mode.",
    10031: "No connection to the trade server.",
    10034: "Order or position volume limit reached.",
    10036: "Position is already closed.",
    10038: "Requested close volume exceeds the position volume.",
    10041: "Order was rejected; the request will be handled by the dealer.",
}

_ENTRY_KINDS = {0: "in", 1: "out", 2: "inout", 3: "out_by"}


def describe_retcode(retcode: int, comment: str = "") -> str:
    base = RETCODE_MESSAGES.get(retcode, f"Broker returned retcode {retcode}.")
    if comment and comment.lower() not in base.lower():
        return f"{base} ({comment})"
    return base


def _naive_utc(moment: datetime) -> datetime:
    """The MetaTrader5 package expects naive datetimes; normalise to naive UTC."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class RealMt5Gateway(Mt5Gateway):
    """Gateway backed by a locally installed MetaTrader 5 terminal."""

    name = "real"

    def __init__(self, *, timeout_ms: int = 60_000) -> None:
        if not MT5_AVAILABLE:  # pragma: no cover
            raise Mt5Error(
                "The MetaTrader5 package is unavailable on this machine "
                f"({MT5_IMPORT_ERROR}). It requires 64-bit Windows and a running MT5 terminal. "
                "Set TC_MT5_GATEWAY=mock to run without a terminal.",
                code="mt5_unavailable",
            )
        self._timeout_ms = timeout_ms
        self._initialised = False
        self._login: int | None = None
        self._server: str | None = None
        self._selected: set[str] = set()

    # ------------------------------------------------------------- lifecycle
    def login(self, credentials: Mt5Credentials) -> AccountSnapshot:
        self._ensure_login(credentials)
        return self.account()

    def _ensure_login(self, credentials: Mt5Credentials) -> None:
        """Attach the terminal to ``credentials``, switching accounts if needed."""
        if not self._initialised:
            kwargs: dict[str, Any] = {
                "login": int(credentials.login),
                "password": credentials.password,
                "server": credentials.server,
                "timeout": self._timeout_ms,
            }
            if credentials.terminal_path:
                kwargs["path"] = credentials.terminal_path
            if not mt5.initialize(**kwargs):
                raise self._error(
                    "Could not initialise the MetaTrader 5 terminal. Check that the terminal is "
                    "installed and running, that TC_MT5_TERMINAL_PATH points at terminal64.exe, "
                    "and that the login/server are correct."
                )
            self._initialised = True
            self._login = int(credentials.login)
            self._server = credentials.server
            self._selected.clear()
            log.info("MT5 terminal initialised for login %s on %s", self._login, self._server)
            return

        if self._login == int(credentials.login) and self._server == credentials.server:
            return

        # Switching the terminal to a different account.
        if not mt5.login(
            int(credentials.login),
            password=credentials.password,
            server=credentials.server,
            timeout=self._timeout_ms,
        ):
            raise self._error(
                f"Login to account {credentials.login} on {credentials.server} was rejected."
            )
        self._login = int(credentials.login)
        self._server = credentials.server
        self._selected.clear()
        log.info("MT5 terminal switched to login %s on %s", self._login, self._server)

    def shutdown(self) -> None:
        if self._initialised:
            try:
                mt5.shutdown()
            finally:
                self._initialised = False
                self._login = None
                self._server = None
                self._selected.clear()

    def current_login(self) -> int | None:
        return self._login

    def _error(self, message: str) -> Mt5Error:
        try:
            code, text = mt5.last_error()
        except Exception:  # pragma: no cover
            code, text = (0, "")
        return Mt5Error(f"{message} Terminal error {code}: {text}", details={"terminal_code": code})

    def _require_connection(self) -> None:
        if not self._initialised:
            raise Mt5NotConnectedError(
                "No MetaTrader 5 session. Connect an account first.",
            )

    # ----------------------------------------------------------------- state
    def account(self) -> AccountSnapshot:
        self._require_connection()
        info = mt5.account_info()
        if info is None:
            raise self._error("The terminal returned no account information.")
        return AccountSnapshot(
            login=int(info.login),
            name=getattr(info, "name", "") or "",
            server=getattr(info, "server", "") or "",
            currency=getattr(info, "currency", "USD") or "USD",
            balance=_num(info.balance),
            equity=_num(info.equity),
            margin=_num(info.margin),
            margin_free=_num(info.margin_free),
            margin_level=_num(getattr(info, "margin_level", 0.0)),
            profit=_num(info.profit),
            leverage=int(getattr(info, "leverage", 0) or 0),
            trade_allowed=bool(getattr(info, "trade_allowed", True)),
            trade_expert=bool(getattr(info, "trade_expert", True)),
            company=getattr(info, "company", "") or "",
        )

    def _select(self, symbol: str) -> None:
        """Ensure the symbol is in Market Watch, otherwise info/ticks are empty."""
        if symbol in self._selected:
            return
        if not mt5.symbol_select(symbol, True):
            raise self._error(f"Symbol {symbol} could not be selected in Market Watch.")
        self._selected.add(symbol)

    def symbols(self, search: str | None = None, limit: int = 200) -> list[SymbolBrief]:
        self._require_connection()
        raw = mt5.symbols_get()
        if raw is None:
            raise self._error("The terminal returned no symbol list.")

        needle = (search or "").strip().lower()
        out: list[SymbolBrief] = []
        for info in raw:
            name = info.name
            if needle and needle not in name.lower() and needle not in (
                getattr(info, "description", "") or ""
            ).lower():
                continue
            out.append(
                SymbolBrief(
                    name=name,
                    description=getattr(info, "description", "") or "",
                    path=getattr(info, "path", "") or "",
                    digits=int(getattr(info, "digits", 5) or 5),
                    trade_allowed=int(getattr(info, "trade_mode", 4) or 0)
                    != SYMBOL_TRADE_MODE_DISABLED,
                    bid=_num(getattr(info, "bid", 0.0)),
                    ask=_num(getattr(info, "ask", 0.0)),
                )
            )
            if len(out) >= limit:
                break
        out.sort(key=lambda s: s.name)
        return out

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        self._require_connection()
        self._select(symbol)
        info = mt5.symbol_info(symbol)
        if info is None:
            raise Mt5Error(
                f"{symbol} is not available on this account.", code="symbol_not_found"
            )

        digits = int(getattr(info, "digits", 5) or 5)
        point = _num(getattr(info, "point", 0.0)) or 10**-digits
        tick_size = _num(getattr(info, "trade_tick_size", 0.0)) or point
        tick_value = _num(getattr(info, "trade_tick_value", 0.0))
        tick_loss = _num(getattr(info, "trade_tick_value_loss", 0.0)) or tick_value
        tick_profit = _num(getattr(info, "trade_tick_value_profit", 0.0)) or tick_value

        return SymbolSpec(
            name=info.name,
            digits=digits,
            point=point,
            tick_size=tick_size,
            tick_value_loss=tick_loss,
            tick_value_profit=tick_profit,
            contract_size=_num(getattr(info, "trade_contract_size", 0.0)),
            volume_min=_num(getattr(info, "volume_min", 0.01)) or 0.01,
            volume_max=_num(getattr(info, "volume_max", 100.0)) or 100.0,
            volume_step=_num(getattr(info, "volume_step", 0.01)) or 0.01,
            stops_level_points=int(getattr(info, "trade_stops_level", 0) or 0),
            freeze_level_points=int(getattr(info, "trade_freeze_level", 0) or 0),
            currency_base=getattr(info, "currency_base", "") or "",
            currency_profit=getattr(info, "currency_profit", "") or "",
            currency_margin=getattr(info, "currency_margin", "") or "",
            description=getattr(info, "description", "") or "",
            trade_allowed=int(getattr(info, "trade_mode", 4) or 0) != SYMBOL_TRADE_MODE_DISABLED,
        )

    def tick(self, symbol: str) -> Tick:
        self._require_connection()
        self._select(symbol)
        t = mt5.symbol_info_tick(symbol)
        if t is None or (not t.bid and not t.ask):
            raise Mt5Error(
                f"No live quote for {symbol}. The market may be closed or the symbol not "
                f"subscribed.",
                code="no_quote",
            )
        stamp = getattr(t, "time_msc", 0)
        moment = (
            datetime.fromtimestamp(stamp / 1000.0, tz=UTC)
            if stamp
            else datetime.fromtimestamp(getattr(t, "time", 0) or 0, tz=UTC)
        )
        return Tick(symbol=symbol, bid=_num(t.bid), ask=_num(t.ask), time=moment)

    def positions(self, symbol: str | None = None) -> list[PositionSnapshot]:
        self._require_connection()
        raw = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if raw is None:
            return []
        return [self._to_position(p) for p in raw]

    def position(self, ticket: int) -> PositionSnapshot | None:
        self._require_connection()
        raw = mt5.positions_get(ticket=int(ticket))
        if not raw:
            return None
        return self._to_position(raw[0])

    @staticmethod
    def _to_position(p: Any) -> PositionSnapshot:
        opened = getattr(p, "time", 0)
        return PositionSnapshot(
            ticket=int(p.ticket),
            symbol=p.symbol,
            side=Side.BUY if int(p.type) == POSITION_TYPE_BUY else Side.SELL,
            volume=_num(p.volume),
            price_open=_num(p.price_open),
            price_current=_num(p.price_current),
            sl=_num(getattr(p, "sl", 0.0)),
            tp=_num(getattr(p, "tp", 0.0)),
            profit=_num(getattr(p, "profit", 0.0)),
            swap=_num(getattr(p, "swap", 0.0)),
            commission=_num(getattr(p, "commission", 0.0)),
            magic=int(getattr(p, "magic", 0) or 0),
            comment=getattr(p, "comment", "") or "",
            opened_at=datetime.fromtimestamp(opened, tz=UTC) if opened else None,
        )

    def deals(
        self,
        date_from: datetime,
        date_to: datetime,
        position_id: int | None = None,
    ) -> list[DealRecord]:
        self._require_connection()
        raw = (
            mt5.history_deals_get(position=int(position_id))
            if position_id is not None
            else mt5.history_deals_get(date_from, date_to)
        )
        if raw is None:
            return []
        out: list[DealRecord] = []
        for d in raw:
            stamp = getattr(d, "time", 0)
            out.append(
                DealRecord(
                    ticket=int(d.ticket),
                    order=int(getattr(d, "order", 0) or 0),
                    position_id=int(getattr(d, "position_id", 0) or 0),
                    symbol=getattr(d, "symbol", "") or "",
                    volume=_num(getattr(d, "volume", 0.0)),
                    price=_num(getattr(d, "price", 0.0)),
                    profit=_num(getattr(d, "profit", 0.0)),
                    swap=_num(getattr(d, "swap", 0.0)),
                    commission=_num(getattr(d, "commission", 0.0)),
                    entry=_ENTRY_KINDS.get(int(getattr(d, "entry", 0) or 0), ""),
                    comment=getattr(d, "comment", "") or "",
                    time=datetime.fromtimestamp(stamp, tz=UTC) if stamp else None,
                )
            )
        return out

    def price_extremes(self, symbol: str, since: datetime) -> tuple[float, float] | None:
        """Bid range since ``since``, from tick history with an M1 bar fallback.

        Caveat: the terminal reports tick and bar timestamps in *server* time,
        which is usually not UTC.  A mismatch makes the query return nothing
        rather than something wrong, and ``None`` degrades the caller to a
        current-price comparison, so the failure mode is a missed touch and never
        a phantom one.  The window is widened by a minute on each side to absorb
        small clock differences.
        """
        self._require_connection()
        self._select(symbol)

        start = _naive_utc(since) - timedelta(minutes=1)
        end = _naive_utc(datetime.now(UTC)) + timedelta(minutes=1)

        try:
            ticks = mt5.copy_ticks_range(symbol, start, end, COPY_TICKS_INFO)
        except Exception:  # pragma: no cover - terminal quirk
            ticks = None
        if ticks is not None and len(ticks) > 0:
            bids = [float(tick["bid"]) for tick in ticks if float(tick["bid"]) > 0]
            if bids:
                return (min(bids), max(bids))

        try:
            rates = mt5.copy_rates_range(symbol, TIMEFRAME_M1, start, end)
        except Exception:  # pragma: no cover - terminal quirk
            rates = None
        if rates is not None and len(rates) > 0:
            lows = [float(rate["low"]) for rate in rates if float(rate["low"]) > 0]
            highs = [float(rate["high"]) for rate in rates if float(rate["high"]) > 0]
            if lows and highs:
                return (min(lows), max(highs))

        return None

    # ------------------------------------------------------------ arithmetic
    def calc_margin(self, side: Side, symbol: str, volume: float, price: float) -> float | None:
        self._require_connection()
        self._select(symbol)
        value = mt5.order_calc_margin(self._order_type(side), symbol, volume, price)
        return None if value is None else float(value)

    def calc_profit(
        self, side: Side, symbol: str, volume: float, price_open: float, price_close: float
    ) -> float | None:
        self._require_connection()
        self._select(symbol)
        value = mt5.order_calc_profit(
            self._order_type(side), symbol, volume, price_open, price_close
        )
        return None if value is None else float(value)

    @staticmethod
    def _order_type(side: Side) -> int:
        return ORDER_TYPE_BUY if side is Side.BUY else ORDER_TYPE_SELL

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
        self._require_connection()
        self._select(symbol)
        quote = self.tick(symbol)
        request: dict[str, Any] = {
            "action": TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": self._order_type(side),
            "price": float(price if price is not None else quote.entry_price(side)),
            "deviation": int(deviation),
            "magic": int(magic),
            "comment": (comment or "")[:31],
            "type_time": ORDER_TIME_GTC,
        }
        if sl:
            request["sl"] = float(sl)
        if tp:
            request["tp"] = float(tp)
        return self._send(request, symbol)

    def close_position(
        self,
        *,
        ticket: int,
        volume: float | None = None,
        deviation: int = 20,
        magic: int = 0,
        comment: str = "",
    ) -> OrderResult:
        self._require_connection()
        pos = self.position(ticket)
        if pos is None:
            raise Mt5Error(
                f"Position #{ticket} is no longer open; it may have been closed already.",
                code="position_not_found",
            )
        self._select(pos.symbol)
        quote = self.tick(pos.symbol)
        close_volume = float(pos.volume if volume is None else min(volume, pos.volume))
        request: dict[str, Any] = {
            "action": TRADE_ACTION_DEAL,
            "position": int(ticket),
            "symbol": pos.symbol,
            "volume": close_volume,
            "type": self._order_type(pos.side.opposite),
            "price": quote.exit_price(pos.side),
            "deviation": int(deviation),
            "magic": int(magic),
            "comment": (comment or "")[:31],
            "type_time": ORDER_TIME_GTC,
        }
        return self._send(request, pos.symbol)

    def modify_stops(
        self,
        *,
        ticket: int,
        sl: float | None = None,
        tp: float | None = None,
    ) -> OrderResult:
        self._require_connection()
        pos = self.position(ticket)
        if pos is None:
            raise Mt5Error(
                f"Position #{ticket} is no longer open, so its stops cannot be modified.",
                code="position_not_found",
            )
        request: dict[str, Any] = {
            "action": TRADE_ACTION_SLTP,
            "position": int(ticket),
            "symbol": pos.symbol,
            "sl": float(sl if sl is not None else pos.sl),
            "tp": float(tp if tp is not None else pos.tp),
        }
        return self._send(request, pos.symbol, with_filling=False)

    # ------------------------------------------------------------- internals
    def _filling_modes(self, symbol: str) -> list[int]:
        """Candidate filling modes, best first, based on the symbol's bitmask."""
        info = mt5.symbol_info(symbol)
        mask = int(getattr(info, "filling_mode", 0) or 0) if info else 0
        ordered: list[int] = []
        if mask & SYMBOL_FILLING_FOK:
            ordered.append(ORDER_FILLING_FOK)
        if mask & SYMBOL_FILLING_IOC:
            ordered.append(ORDER_FILLING_IOC)
        for fallback in (ORDER_FILLING_IOC, ORDER_FILLING_FOK, ORDER_FILLING_RETURN):
            if fallback not in ordered:
                ordered.append(fallback)
        return ordered

    def _send(
        self, request: dict[str, Any], symbol: str, *, with_filling: bool = True
    ) -> OrderResult:
        """Dispatch a request, retrying once per filling mode on retcode 10030."""
        attempts = self._filling_modes(symbol) if with_filling else [None]
        last: OrderResult | None = None

        for filling in attempts:
            payload = dict(request)
            if filling is not None:
                payload["type_filling"] = filling

            check = mt5.order_send(payload)
            if check is None:
                raise self._error("order_send returned no result.")

            result = OrderResult(
                ok=int(check.retcode) in (RETCODE_DONE, RETCODE_PLACED, RETCODE_DONE_PARTIAL),
                retcode=int(check.retcode),
                comment=describe_retcode(int(check.retcode), getattr(check, "comment", "") or ""),
                order=int(getattr(check, "order", 0) or 0),
                deal=int(getattr(check, "deal", 0) or 0),
                position=int(payload.get("position", 0) or 0),
                volume=_num(getattr(check, "volume", payload.get("volume", 0.0))),
                price=_num(getattr(check, "price", payload.get("price", 0.0))),
                request_id=int(getattr(check, "request_id", 0) or 0),
            )
            if result.ok or result.retcode != RETCODE_INVALID_FILL:
                return result
            last = result
            log.warning("Filling mode %s rejected for %s; retrying", filling, symbol)

        return last or OrderResult(ok=False, retcode=0, comment="No order attempt was made.")
