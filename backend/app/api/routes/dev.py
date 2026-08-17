"""Development-only controls for the simulated broker.

These endpoints let you drive the mock market - move a price, step the random
walk - so the whole workflow (TP1 partial exit, stop tightening, TP2, TP3) can be
demonstrated and tested over HTTP without waiting for a real market.

They are mounted **only** when both conditions hold:

* ``TC_MT5_GATEWAY=mock`` - there is no simulator to drive otherwise, and
* ``TC_ENV`` is not ``production``.

Because the router is not included at all in other configurations, the routes are
absent from the OpenAPI document as well as unreachable.  See
:func:`app.api.router.dev_routes_enabled`.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...errors import NotFoundError
from ...services import accounts as accounts_service
from ..deps import CurrentUser, DbSession, ResolvedAccount

router = APIRouter(prefix="/dev/mock", tags=["development"])


class PriceRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    bid: float = Field(gt=0, description="New bid; the ask follows the symbol's spread")


class AdvanceRequest(BaseModel):
    steps: int = Field(default=1, ge=1, le=1000)


def _broker(account):
    from ...mt5.mock import UNIVERSE

    broker = UNIVERSE._brokers.get(account.login)  # noqa: SLF001 - dev hook
    if broker is None:
        raise NotFoundError(
            f"No simulated broker for login {account.login} yet. Call an endpoint that reads the "
            f"account first so the session is created."
        )
    return broker


@router.post("/price")
async def set_price(
    payload: PriceRequest,
    user: CurrentUser,
    session: DbSession,
    account: ResolvedAccount,
) -> dict[str, object]:
    """Force a symbol's bid, then run the simulator's stop/target processing.

    Setting a price through a target is how you make the position manager act on
    demand instead of waiting for the random walk.
    """
    broker = _broker(account)
    broker.set_price(payload.symbol, payload.bid)
    instrument = broker.instrument(payload.symbol)
    return {
        "symbol": instrument.spec.name,
        "bid": instrument.bid,
        "ask": instrument.ask,
        "open_positions": len(broker.positions),
        "balance": broker.balance,
    }


@router.post("/advance")
async def advance(
    payload: AdvanceRequest,
    user: CurrentUser,
    session: DbSession,
    account: ResolvedAccount,
) -> dict[str, object]:
    """Step the random walk, useful for watching the dashboard move."""
    broker = _broker(account)
    broker.advance(payload.steps)
    return {
        "steps": payload.steps,
        "prices": {
            name: instrument.bid for name, instrument in broker.instruments.items()
        },
    }


@router.get("/state")
async def state(
    user: CurrentUser, session: DbSession, account: ResolvedAccount
) -> dict[str, object]:
    """Everything the simulator currently holds for this account."""
    broker = _broker(account)
    snapshot = broker.snapshot()
    return {
        "login": broker.login,
        "balance": broker.balance,
        "equity": snapshot.equity,
        "auto_drift": broker.auto_drift,
        "prices": {
            name: {"bid": inst.bid, "ask": inst.ask}
            for name, inst in broker.instruments.items()
        },
        "positions": [
            {
                "ticket": p.ticket,
                "symbol": p.symbol,
                "side": p.side.value,
                "volume": p.volume,
                "price_open": p.price_open,
                "sl": p.sl,
                "tp": p.tp,
            }
            for p in broker.positions.values()
        ],
        "deals": len(broker.deals),
        "credentials_ok": accounts_service.credentials_for(account).login == broker.login,
    }
