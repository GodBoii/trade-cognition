"""Symbols and quotes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ...services import accounts as accounts_service
from .. import schemas
from ..deps import CurrentUser, DbSession, ResolvedAccount

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/symbols", response_model=list[schemas.SymbolResponse])
async def list_symbols(
    user: CurrentUser,
    session: DbSession,
    account: ResolvedAccount,
    q: Annotated[str | None, Query(description="Filter on name or description")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
):
    """Tradable symbols on the connected account (cached for five minutes)."""
    symbols = await accounts_service.client_for(account).symbols(q, limit)
    return [schemas.SymbolResponse.model_validate(s) for s in symbols]


@router.get("/symbols/{symbol}/spec", response_model=schemas.SymbolSpecResponse)
async def symbol_spec(
    symbol: str,
    user: CurrentUser,
    session: DbSession,
    account: ResolvedAccount,
    refresh: bool = False,
):
    """Contract specification: lot grid, tick value, minimum stop distance.

    ``money_per_price_unit_per_lot`` is the derived figure every risk number on
    the platform is built from - see ``docs/02-risk-engine.md``.
    """
    spec = await accounts_service.client_for(account).symbol_spec(symbol, refresh=refresh)
    return schemas.SymbolSpecResponse.model_validate(spec)


@router.get("/symbols/{symbol}/tick", response_model=schemas.TickResponse)
async def symbol_tick(
    symbol: str, user: CurrentUser, session: DbSession, account: ResolvedAccount
):
    tick = await accounts_service.client_for(account).tick(symbol)
    return schemas.TickResponse.model_validate(tick)
