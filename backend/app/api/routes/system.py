"""Health and diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from ... import __version__
from ...config import settings
from ...mt5 import get_runtime
from ...workers.monitor import get_monitor
from .. import schemas

router = APIRouter(tags=["system"])


@router.get("/health", response_model=schemas.HealthResponse)
async def health() -> schemas.HealthResponse:
    """Liveness plus the two facts that matter operationally: which MT5 gateway
    is in use and whether the position monitor is running."""
    monitor = get_monitor()
    runtime = get_runtime()
    return schemas.HealthResponse(
        status="ok",
        app=settings.app_name,
        version=__version__,
        environment=settings.env,
        mt5_gateway=runtime.mode,
        mt5_stats={**runtime.stats(), "monitor": monitor.stats()},
        monitor_running=monitor.running,
        server_time=datetime.now(UTC),
    )
