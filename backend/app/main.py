"""ASGI application factory.

Run with::

    uvicorn app.main:app --reload --port 8000

Startup creates the schema, opens the MT5 runtime and starts the position
monitor.  Shutdown stops the monitor first, then the MT5 worker thread, so no
management pass is interrupted mid-order.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .api.router import api_router
from .config import settings
from .db.session import init_db
from .errors import TradeCognitionError
from .logging_conf import configure_logging, get_logger
from .mt5.manager import shutdown_runtime
from .workers.monitor import get_monitor

configure_logging(settings.log_level)
log = get_logger(__name__)

DESCRIPTION = """
**Trade Cognition** standardises and enforces disciplined trade execution on a
MetaTrader 5 account.

Every entry passes through the same pipeline:

1. `POST /api/calculator/preview` - entry, stop, lot size, maximum loss,
   expected profit at each target, reward-to-risk, required margin and the
   percentage of capital at risk.
2. `POST /api/trades` - the same calculation, then the rules:
   * **Rule 1** one active entry per derivative,
   * **Rule 2** lot size from the capital formula (0.02 lots per 1,000 by default),
   * **Rule 3** loss at the stop no greater than 2% of capital.
3. Approved orders are placed on MT5 with the stop attached and a failsafe
   take-profit at the final rung.
4. The position monitor then executes the ladder: 50% out at 1R with the stop
   halved, the remainder at 2R with the stop at TP1, and the 1:3 target for any
   residual volume.

A rules rejection returns HTTP 200 with `approved: false` and the full
explanation - it is a normal outcome, not a transport error.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_auth_configuration()
    log.info(
        "Starting %s %s (env=%s, mt5=%s)",
        settings.app_name,
        __version__,
        settings.env,
        settings.mt5_gateway,
    )
    init_db()

    monitor = get_monitor()
    await monitor.start()
    try:
        yield
    finally:
        await monitor.stop()
        await shutdown_runtime()
        log.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_prefix)

    # ----------------------------------------------------------- error shape
    @app.exception_handler(TradeCognitionError)
    async def _domain_error(_: Request, exc: TradeCognitionError) -> JSONResponse:
        # Rule and validation failures are expected traffic, not incidents.
        log.info("%s -> %s: %s", exc.__class__.__name__, exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_request",
                "message": "The request body or query string is invalid.",
                "details": _clean_validation_errors(exc.errors()),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": f"http_{exc.status_code}", "message": str(exc.detail)},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "An unexpected error occurred. The incident has been logged.",
            },
        )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "api": settings.api_prefix,
        }

    return app


def _clean_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip the noisy parts of pydantic errors before returning them."""
    cleaned: list[dict[str, Any]] = []
    for error in errors:
        cleaned.append(
            {
                "field": ".".join(str(p) for p in error.get("loc", ()) if p != "body"),
                "message": error.get("msg", "invalid value"),
                "type": error.get("type", ""),
            }
        )
    return cleaned


app = create_app()
