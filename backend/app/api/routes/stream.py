"""Live dashboard stream.

Browsers cannot attach an ``Authorization`` header to a WebSocket handshake, so
the access token is passed as a query parameter.  It is the same short-lived JWT
used elsewhere; the connection is closed immediately if it does not validate.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ...config import settings
from ...core.security import decode_access_token
from ...core.supabase_auth import verify_supabase_access_token
from ...db.session import session_scope
from ...errors import TradeCognitionError
from ...logging_conf import get_logger
from ...services import accounts as accounts_service
from ...services import trades as trades_service
from ...services import users as users_service
from .. import schemas, serializers

log = get_logger(__name__)

router = APIRouter(tags=["stream"])

WS_POLICY_VIOLATION = 1008
WS_INTERNAL_ERROR = 1011


@router.websocket("/ws/stream")
async def stream(
    websocket: WebSocket,
    token: str = Query(..., description="Access token from /api/auth/login"),
    account_id: int | None = Query(default=None),
) -> None:
    """Push account, position and managed-trade snapshots every few seconds."""
    await websocket.accept()

    try:
        if settings.supabase_auth_enabled:
            identity = await asyncio.to_thread(verify_supabase_access_token, token)
            with session_scope() as session:
                user_id = users_service.provision_from_supabase(session, identity).id
        else:
            payload = decode_access_token(token)
            user_id = int(payload["sub"])
    except Exception:
        await websocket.close(code=WS_POLICY_VIOLATION, reason="Invalid or expired token")
        return

    interval = max(settings.stream_interval_seconds, 0.5)

    try:
        while True:
            try:
                snapshot = await _build_snapshot(user_id, account_id)
            except TradeCognitionError as exc:
                snapshot = {
                    "type": "error",
                    "code": exc.code,
                    "message": exc.message,
                    "server_time": datetime.now(UTC).isoformat(),
                }
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("Stream snapshot failed: %s", exc)
                snapshot = {
                    "type": "error",
                    "code": "stream_error",
                    "message": "Could not build a snapshot.",
                    "server_time": datetime.now(UTC).isoformat(),
                }

            await websocket.send_json(snapshot)
            await asyncio.sleep(interval)

    except WebSocketDisconnect:
        return
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Stream closed unexpectedly: %s", exc)
        with contextlib.suppress(Exception):
            await websocket.close(code=WS_INTERNAL_ERROR)


async def _build_snapshot(user_id: int, account_id: int | None) -> dict:
    with session_scope() as session:
        user = users_service.get_by_id(session, user_id)
        if user is None:
            raise TradeCognitionError("Account no longer exists.", code="user_missing")

        account_row = accounts_service.resolve_account(session, user, account_id)
        overview = await trades_service.positions_overview(session, user, account_row)
        profile = users_service.get_profile(session, user)
        account_snapshot = overview["account"]

        active = trades_service.active_trades(session, user)

        return {
            "type": "snapshot",
            "server_time": datetime.now(UTC).isoformat(),
            "account": schemas.AccountSnapshotResponse.model_validate(
                account_snapshot
            ).model_dump(mode="json"),
            "capital": round(profile.capital(account_snapshot), 2),
            "max_risk_money": round(
                profile.max_risk_money(profile.capital(account_snapshot)), 2
            ),
            "risk_on": overview["risk_on"],
            "positions": [
                serializers.position_row(row["position"], row["trade"]).model_dump(mode="json")
                for row in overview["rows"]  # type: ignore[index]
            ],
            "active_trades": [
                serializers.trade(trade).model_dump(mode="json") for trade in active
            ],
        }
