"""Async client for the scoped Supabase worker RPCs.

The public anon key authenticates PostgREST itself. A separate high-entropy
worker token authorizes only the user's paired worker operations inside the
``SECURITY DEFINER`` SQL functions. Neither credential is included in object
representations, exceptions, response-body errors, or application logs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx

from ..config import Settings, settings as default_settings
from .schemas import ClaimedTradeCommand, WorkerResult


class SupabaseRequestError(RuntimeError):
    """A sanitized Supabase transport or protocol failure."""

    def __init__(self, operation: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(f"Supabase {operation} failed: {message}")
        self.operation = operation
        self.status_code = status_code


class SupabaseQueueClient:
    """Call the ``tcq_*`` RPC contract from the local worker."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._anon_key = self._settings.supabase_anon_key
        self._worker_token = self._settings.worker_token
        self._http = http_client or httpx.AsyncClient(timeout=15.0)
        self._owns_http = http_client is None

    def __repr__(self) -> str:
        return f"SupabaseQueueClient(url={self._settings.supabase_url!r}, auth=scoped-token)"

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def claim_commands(self) -> list[ClaimedTradeCommand]:
        data = await self._rpc(
            "tcq_claim_trade_commands",
            {
                "p_worker_token": self._worker_token,
                "p_limit": self._settings.worker_batch_size,
                "p_lease_seconds": self._settings.worker_claim_lease_seconds,
            },
        )
        if data is None:
            return []
        rows = data if isinstance(data, list) else [data]
        try:
            return [ClaimedTradeCommand.model_validate(row) for row in rows]
        except Exception as exc:
            raise SupabaseRequestError(
                "tcq_claim_trade_commands", "invalid response shape"
            ) from exc

    async def list_connections(self) -> list[UUID]:
        """Return enabled connections assigned to this scoped worker."""
        data = await self._rpc(
            "tcq_worker_list_connections",
            {"p_worker_token": self._worker_token},
        )
        if data is None:
            return []
        rows = data if isinstance(data, list) else [data]
        try:
            return [UUID(str(row["id"])) for row in rows if row["is_enabled"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise SupabaseRequestError(
                "tcq_worker_list_connections", "invalid response shape"
            ) from exc

    async def complete_command(
        self, command: ClaimedTradeCommand, outcome: WorkerResult
    ) -> None:
        await self._rpc(
            "tcq_complete_trade_command",
            {
                "p_worker_token": self._worker_token,
                "p_command_id": str(command.id),
                "p_claim_token": str(command.claim_token),
                "p_outcome": outcome.outcome,
                "p_result": self._safe_json(outcome.result),
                "p_intent_status": outcome.intent_status,
                "p_error_code": outcome.error_code,
                "p_error_message": self._redact_text(outcome.message),
            },
        )

    async def extend_lease(self, command: ClaimedTradeCommand) -> None:
        """Fence and extend a still-active claim before a long broker call."""
        await self._rpc(
            "tcq_extend_command_lease",
            {
                "p_worker_token": self._worker_token,
                "p_command_id": str(command.id),
                "p_claim_token": str(command.claim_token),
                "p_lease_seconds": self._settings.worker_claim_lease_seconds,
            },
        )

    async def fail_command(
        self,
        command: ClaimedTradeCommand,
        *,
        error_code: str,
        message: str,
        retryable: bool,
    ) -> None:
        """Record a failure, preserving the lease for retryable failures.

        The SQL contract retries only by lease expiry. Therefore a retryable
        error appends an audit event and deliberately leaves the command
        claimed. A permanent error completes it with the ``failed`` outcome.
        """
        if retryable:
            await self.append_event(
                connection_id=command.connection_id,
                intent_id=command.intent_id,
                event_type="command_retry_scheduled",
                message=message,
                payload={"command_id": str(command.id), "error_code": error_code},
            )
            return
        await self.complete_command(
            command,
            WorkerResult(
                outcome="failed",
                intent_status="failed" if command.intent_id else None,
                error_code=error_code,
                message=message,
            ),
        )

    async def heartbeat(
        self, connection_id: UUID, snapshot: Mapping[str, Any] | None = None
    ) -> None:
        await self._rpc(
            "tcq_worker_heartbeat",
            {
                "p_worker_token": self._worker_token,
                "p_connection_id": str(connection_id),
                "p_snapshot": self._safe_json(dict(snapshot or {})),
            },
        )

    async def get_context(self, connection_id: UUID) -> dict[str, Any]:
        data = await self._rpc(
            "tcq_worker_get_context",
            {
                "p_worker_token": self._worker_token,
                "p_connection_id": str(connection_id),
            },
        )
        if not isinstance(data, dict):
            raise SupabaseRequestError("tcq_worker_get_context", "invalid response shape")
        return data

    async def append_event(
        self,
        *,
        connection_id: UUID,
        intent_id: UUID | None,
        event_type: str,
        message: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        await self._rpc(
            "tcq_worker_append_event",
            {
                "p_worker_token": self._worker_token,
                "p_connection_id": str(connection_id),
                "p_intent_id": str(intent_id) if intent_id else None,
                "p_event_type": event_type,
                "p_message": self._redact_text(message),
                "p_payload": self._safe_json(dict(payload or {})),
            },
        )

    async def update_trade_state(
        self,
        *,
        intent_id: UUID,
        status: str,
        event_type: str,
        message: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        await self._rpc(
            "tcq_worker_update_trade_state",
            {
                "p_worker_token": self._worker_token,
                "p_intent_id": str(intent_id),
                "p_status": status,
                "p_event_type": event_type,
                "p_message": self._redact_text(message),
                "p_payload": self._safe_json(dict(payload or {})),
            },
        )

    async def _rpc(self, function: str, payload: Mapping[str, Any]) -> Any:
        url = f"{self._settings.supabase_rest_url}/rpc/{function}"
        try:
            response = await self._http.post(
                url,
                headers={
                    "apikey": self._anon_key,
                    "authorization": f"Bearer {self._anon_key}",
                    "content-type": "application/json",
                },
                json=dict(payload),
            )
        except httpx.HTTPError as exc:
            # Transport exception request objects can contain headers; report
            # only the exception class, never the original exception string.
            raise SupabaseRequestError(function, exc.__class__.__name__) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise SupabaseRequestError(
                function, f"HTTP {response.status_code}", status_code=response.status_code
            )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise SupabaseRequestError(function, "invalid JSON response") from exc

    def _redact_text(self, value: str) -> str:
        for secret in (self._worker_token, self._anon_key):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value

    def _safe_json(self, value: Any) -> Any:
        """Recursively redact queue credentials from caller-supplied JSON."""
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, dict):
            return {key: self._safe_json(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._safe_json(item) for item in value]
        return value
