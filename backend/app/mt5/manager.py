"""Serialised access to the MT5 gateway.

Why this exists
---------------
The vendor ``MetaTrader5`` package is a process-wide, single-threaded singleton
bound to one terminal and one logged-in account.  A web server is neither of
those things.  :class:`Mt5Runtime` bridges the gap:

* every gateway call runs on **one dedicated worker thread**
  (``ThreadPoolExecutor(max_workers=1)``), so the package never sees concurrent
  access;
* a mutex plus :meth:`Mt5Gateway.login` before each unit of work re-asserts the
  correct account, so two users on two accounts cannot interleave;
* callers submit a **whole unit of work** (:meth:`Mt5Client.run`) rather than
  individual calls, which keeps the account/spec/tick trio consistent and costs
  a single thread hop.

The cost is throughput: all MT5 I/O is a single lane.  ``docs/06-mt5-integration.md``
explains how to scale that out (one worker process per account).
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from ..config import Settings, settings as default_settings
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
from ..errors import Mt5Error, TradeCognitionError
from ..logging_conf import get_logger
from .credentials import Mt5Credentials
from .gateway import Mt5Gateway

log = get_logger(__name__)

T = TypeVar("T")

#: How long a symbol specification is trusted before being re-read.
SPEC_CACHE_TTL_SECONDS = 30.0
#: Symbol catalogues change rarely.
SYMBOLS_CACHE_TTL_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Account, symbol spec and quote captured in one serialised visit."""

    account: AccountSnapshot
    spec: SymbolSpec
    tick: Tick
    positions: tuple[PositionSnapshot, ...] = ()


class Mt5Runtime:
    """Owns the gateway instance and the single worker thread."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or default_settings
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5-worker")
        self._mutex = threading.Lock()
        self._gateway: Mt5Gateway | None = None
        self._closed = False
        self._calls = 0
        self._failures = 0

    # ------------------------------------------------------------ gateway mgmt
    @property
    def mode(self) -> str:
        return self._settings.mt5_gateway

    def _build_gateway(self) -> Mt5Gateway:
        if self._settings.mt5_gateway == "real":
            from .real import RealMt5Gateway

            log.info("Creating live MetaTrader5 gateway")
            return RealMt5Gateway(timeout_ms=self._settings.mt5_timeout_ms)

        from .mock import MockMt5Gateway

        log.info("Creating mock MT5 gateway (no terminal required)")
        return MockMt5Gateway()

    def _gateway_instance(self) -> Mt5Gateway:
        if self._gateway is None:
            self._gateway = self._build_gateway()
        return self._gateway

    # -------------------------------------------------------------- execution
    def _run_sync(self, credentials: Mt5Credentials, work: Callable[[Mt5Gateway], T]) -> T:
        """Executed on the worker thread only."""
        with self._mutex:
            if self._closed:
                raise Mt5Error("The MT5 runtime is shutting down.", code="mt5_shutdown")
            gateway = self._gateway_instance()
            # Idempotent for the active account; switches the terminal otherwise.
            gateway.login(credentials)
            return work(gateway)

    async def call(
        self,
        credentials: Mt5Credentials,
        work: Callable[[Mt5Gateway], T],
        *,
        label: str = "mt5",
    ) -> T:
        """Run ``work`` against the gateway on the worker thread."""
        loop = asyncio.get_running_loop()
        self._calls += 1
        started = time.perf_counter()
        future = loop.run_in_executor(self._pool, self._run_sync, credentials, work)
        try:
            result = await asyncio.wait_for(
                future, timeout=self._settings.mt5_call_timeout_seconds
            )
        except TimeoutError:
            self._failures += 1
            raise Mt5Error(
                f"The MT5 operation '{label}' did not complete within "
                f"{self._settings.mt5_call_timeout_seconds:g}s. The terminal may be busy, "
                f"disconnected, or showing a modal dialog.",
                code="mt5_timeout",
            ) from None
        except TradeCognitionError:
            # Domain errors (validation, rule problems) carry precise messages
            # for the user; never rewrap them as transport failures.
            self._failures += 1
            raise
        except Exception as exc:
            self._failures += 1
            raise Mt5Error(
                f"MT5 operation '{label}' failed: {exc}", code="mt5_call_failed"
            ) from exc

        elapsed = (time.perf_counter() - started) * 1000
        if elapsed > 1_000:
            log.warning("Slow MT5 call '%s' took %.0fms", label, elapsed)
        return result

    def client(self, credentials: Mt5Credentials) -> Mt5Client:
        return Mt5Client(self, credentials)

    # ---------------------------------------------------------------- teardown
    async def shutdown(self) -> None:
        self._closed = True
        gateway = self._gateway
        if gateway is not None:
            try:
                await asyncio.get_running_loop().run_in_executor(self._pool, gateway.shutdown)
            except Exception as exc:  # pragma: no cover - best effort
                log.warning("MT5 gateway shutdown raised: %s", exc)
        self._pool.shutdown(wait=False, cancel_futures=True)
        log.info("MT5 runtime stopped after %s calls (%s failures)", self._calls, self._failures)

    def stats(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "calls": self._calls,
            "failures": self._failures,
            "connected_login": self._gateway.current_login() if self._gateway else None,
        }


class Mt5Client:
    """Account-scoped async facade over the runtime.

    Prefer :meth:`run` for anything that needs more than one gateway call: it
    keeps the data consistent and avoids repeated thread hops.
    """

    _spec_cache: dict[tuple[int, str], tuple[float, SymbolSpec]] = {}
    _symbols_cache: dict[tuple[int, str], tuple[float, list[SymbolBrief]]] = {}

    def __init__(self, runtime: Mt5Runtime, credentials: Mt5Credentials) -> None:
        self._runtime = runtime
        self._credentials = credentials

    @property
    def login(self) -> int:
        return self._credentials.login

    async def run(self, work: Callable[[Mt5Gateway], T], *, label: str = "mt5") -> T:
        return await self._runtime.call(self._credentials, work, label=label)

    # ------------------------------------------------------------------ reads
    async def verify(self) -> AccountSnapshot:
        """Authenticate and return the account, used by the connect flow."""
        return await self.run(lambda gw: gw.login(self._credentials), label="login")

    async def account(self) -> AccountSnapshot:
        return await self.run(lambda gw: gw.account(), label="account")

    async def symbols(self, search: str | None = None, limit: int = 200) -> list[SymbolBrief]:
        key = (self.login, (search or "").lower())
        cached = self._symbols_cache.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < SYMBOLS_CACHE_TTL_SECONDS:
            return cached[1][:limit]
        result = await self.run(lambda gw: gw.symbols(search, limit), label="symbols")
        self._symbols_cache[key] = (now, result)
        return result

    async def symbol_spec(self, symbol: str, *, refresh: bool = False) -> SymbolSpec:
        key = (self.login, symbol.upper())
        now = time.monotonic()
        cached = self._spec_cache.get(key)
        if cached and not refresh and now - cached[0] < SPEC_CACHE_TTL_SECONDS:
            return cached[1]
        spec = await self.run(lambda gw: gw.symbol_spec(symbol), label="symbol_spec")
        self._spec_cache[key] = (now, spec)
        return spec

    async def tick(self, symbol: str) -> Tick:
        return await self.run(lambda gw: gw.tick(symbol), label="tick")

    async def positions(self, symbol: str | None = None) -> list[PositionSnapshot]:
        return await self.run(lambda gw: gw.positions(symbol), label="positions")

    async def position(self, ticket: int) -> PositionSnapshot | None:
        return await self.run(lambda gw: gw.position(ticket), label="position")

    async def deals(
        self, date_from: datetime, date_to: datetime, position_id: int | None = None
    ) -> list[DealRecord]:
        return await self.run(
            lambda gw: gw.deals(date_from, date_to, position_id), label="deals"
        )

    async def market_context(self, symbol: str, *, with_positions: bool = True) -> MarketContext:
        """Account + spec + quote (+ positions) captured atomically."""

        def work(gw: Mt5Gateway) -> MarketContext:
            return MarketContext(
                account=gw.account(),
                spec=gw.symbol_spec(symbol),
                tick=gw.tick(symbol),
                positions=tuple(gw.positions()) if with_positions else (),
            )

        return await self.run(work, label="market_context")

    # --------------------------------------------------------------- trading
    async def open_position(
        self,
        *,
        symbol: str,
        side: Side,
        volume: float,
        sl: float | None = None,
        tp: float | None = None,
        deviation: int = 20,
        magic: int = 0,
        comment: str = "",
    ) -> OrderResult:
        return await self.run(
            lambda gw: gw.open_position(
                symbol=symbol,
                side=side,
                volume=volume,
                sl=sl,
                tp=tp,
                deviation=deviation,
                magic=magic,
                comment=comment,
            ),
            label="open_position",
        )

    async def close_position(
        self,
        *,
        ticket: int,
        volume: float | None = None,
        deviation: int = 20,
        magic: int = 0,
        comment: str = "",
    ) -> OrderResult:
        return await self.run(
            lambda gw: gw.close_position(
                ticket=ticket,
                volume=volume,
                deviation=deviation,
                magic=magic,
                comment=comment,
            ),
            label="close_position",
        )

    async def modify_stops(
        self, *, ticket: int, sl: float | None = None, tp: float | None = None
    ) -> OrderResult:
        return await self.run(
            lambda gw: gw.modify_stops(ticket=ticket, sl=sl, tp=tp), label="modify_stops"
        )

    @classmethod
    def clear_caches(cls) -> None:
        cls._spec_cache.clear()
        cls._symbols_cache.clear()


# ---------------------------------------------------------------------------
# process-wide runtime
# ---------------------------------------------------------------------------
_runtime: Mt5Runtime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> Mt5Runtime:
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = Mt5Runtime()
    return _runtime


async def shutdown_runtime() -> None:
    global _runtime
    if _runtime is not None:
        await _runtime.shutdown()
        _runtime = None
    Mt5Client.clear_caches()
