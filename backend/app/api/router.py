"""Aggregate router mounted under ``TC_API_PREFIX`` (default ``/api``)."""

from __future__ import annotations

from fastapi import APIRouter

from ..config import Settings, settings as default_settings
from ..logging_conf import get_logger
from .routes import (
    accounts,
    auth,
    calculator,
    dev,
    journal,
    market,
    rules,
    stream,
    system,
    trades,
)

log = get_logger(__name__)


def dev_routes_enabled(settings: Settings | None = None) -> bool:
    """Simulator controls exist only outside production, and only for the mock."""
    cfg = settings or default_settings
    return cfg.mt5_gateway == "mock" and not cfg.is_production


def build_api_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter()

    router.include_router(system.router)
    router.include_router(auth.router)
    router.include_router(accounts.router)
    router.include_router(market.router)
    router.include_router(rules.router)
    router.include_router(calculator.router)
    router.include_router(trades.router)
    router.include_router(journal.router)
    router.include_router(stream.router)

    if dev_routes_enabled(settings):
        router.include_router(dev.router)
        log.warning(
            "Simulator control endpoints are mounted at /dev/mock (mock gateway, non-production)."
        )

    return router


api_router = build_api_router()
