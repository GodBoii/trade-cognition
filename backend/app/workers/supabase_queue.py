"""Lease-aware Supabase command polling and connection-heartbeat loop.

The worker discovers enabled connections assigned to its scoped token before
claiming commands, publishes account snapshots through an injected provider,
and then delivers fenced commands to an injected processor. It remains
disabled by default and is started by the FastAPI lifespan only when configured.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

from ..config import Settings, settings as default_settings
from ..logging_conf import get_logger
from ..supabase.client import SupabaseQueueClient
from ..supabase.schemas import ClaimedTradeCommand, WorkerResult

log = get_logger(__name__)

CommandProcessor = Callable[[ClaimedTradeCommand], Awaitable[WorkerResult]]
HeartbeatProvider = Callable[[UUID], Awaitable[Mapping[str, Any]]]


class SupabaseQueueWorker:
    """Poll, claim and deliver commands to an injected processor."""

    def __init__(
        self,
        processor: CommandProcessor | None = None,
        *,
        settings: Settings | None = None,
        client: SupabaseQueueClient | None = None,
        heartbeat_provider: HeartbeatProvider | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._processor = processor
        self._client = client
        self._owns_client = client is None
        self._heartbeat_provider = heartbeat_provider
        self._known_connections: set[UUID] = set()
        self._heartbeated_connections: set[UUID] = set()
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._last_heartbeat = 0.0
        self._consecutive_failures = 0
        self.cycles = 0
        self.claimed = 0
        self.completed = 0
        self.failed = 0
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        if not self._settings.supabase_queue_enabled:
            log.info("Supabase queue worker is disabled")
            return
        self._settings.validate_supabase_queue_configuration()
        if self._processor is None:
            raise RuntimeError(
                "Supabase queue worker requires an injected command processor before it can start."
            )
        if self._client is None:
            self._client = SupabaseQueueClient(self._settings)
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="supabase-queue-worker")
        log.info("Supabase queue worker started with scoped worker authentication")

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - best effort shutdown
                pass
        if self._client is not None and self._owns_client:
            await self._client.close()
            self._client = None
        log.info(
            "Supabase queue worker stopped after %s cycles (%s claimed, %s completed)",
            self.cycles,
            self.claimed,
            self.completed,
        )

    async def _loop(self) -> None:
        interval = max(self._settings.worker_poll_interval_seconds, 0.25)
        while not self._stopping.is_set():
            started = time.monotonic()
            try:
                await self._heartbeat_if_due()
                await self.run_once()
                self._consecutive_failures = 0
                delay = interval
            except asyncio.CancelledError:  # pragma: no cover - task shutdown
                raise
            except Exception as exc:  # pragma: no cover - loop must survive remote outages
                self._consecutive_failures += 1
                self.last_error = self._safe_error(exc)
                delay = self._backoff_delay()
                log.warning("Supabase queue cycle failed: %s", self.last_error)

            elapsed = time.monotonic() - started
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=max(0.0, delay - elapsed)
                )
            except TimeoutError:
                continue

    async def run_once(self) -> int:
        """Claim and process one batch; return the number of claimed commands."""
        if not self._settings.supabase_queue_enabled:
            return 0
        self._settings.validate_supabase_queue_configuration()
        if self._processor is None:
            raise RuntimeError("No Supabase command processor is configured.")
        client = self._require_client()

        self.cycles += 1
        commands = await client.claim_commands()
        self.claimed += len(commands)
        for command in commands:
            self._known_connections.add(command.connection_id)
            if (
                self._heartbeat_provider is not None
                and command.connection_id not in self._heartbeated_connections
            ):
                await self._heartbeat_connection(command.connection_id)
            await self._deliver(command)
        return len(commands)

    async def heartbeat_once(
        self, connection_id: UUID, snapshot: Mapping[str, Any] | None = None
    ) -> None:
        """Publish an immediate account/worker heartbeat."""
        await self._require_client().heartbeat(connection_id, snapshot)
        self._known_connections.add(connection_id)
        self._heartbeated_connections.add(connection_id)
        self._last_heartbeat = time.monotonic()

    async def discover_and_heartbeat_once(self) -> int:
        """Discover assigned connections and publish a snapshot for each one."""
        if self._heartbeat_provider is None:
            return 0
        assigned = set(await self._require_client().list_connections())
        self._known_connections = assigned
        self._heartbeated_connections.intersection_update(assigned)
        for connection_id in sorted(assigned, key=str):
            await self._heartbeat_connection(connection_id)
        self._last_heartbeat = time.monotonic()
        return len(assigned)

    async def _heartbeat_if_due(self) -> None:
        if self._heartbeat_provider is None:
            return
        interval = max(self._settings.worker_heartbeat_interval_seconds, 1.0)
        if time.monotonic() - self._last_heartbeat < interval:
            return
        await self.discover_and_heartbeat_once()

    async def _heartbeat_connection(self, connection_id: UUID) -> None:
        provider = self._heartbeat_provider
        if provider is None:  # pragma: no cover - callers guard this
            return
        snapshot = await provider(connection_id)
        await self.heartbeat_once(connection_id, snapshot)

    async def _deliver(self, command: ClaimedTradeCommand) -> None:
        try:
            outcome = await self._processor(command)  # type: ignore[misc]
        except asyncio.CancelledError:  # pragma: no cover - shutdown must interrupt promptly
            raise
        except Exception as exc:
            message = self._safe_error(exc)
            await self._require_client().fail_command(
                command,
                error_code="worker_exception",
                message=message,
                retryable=True,
            )
            self.failed += 1
            self.last_error = message
            return

        if outcome.outcome == "failed":
            await self._require_client().fail_command(
                command,
                error_code=outcome.error_code or "command_failed",
                message=outcome.message,
                retryable=outcome.retryable,
            )
            self.failed += 1
            return

        await self._require_client().complete_command(command, outcome)
        self.completed += 1

    def _require_client(self) -> SupabaseQueueClient:
        if self._client is None:
            self._client = SupabaseQueueClient(self._settings)
        return self._client

    def _backoff_delay(self) -> float:
        ceiling = max(self._settings.worker_backoff_max_seconds, 0.25)
        exponential = min(ceiling, float(2 ** min(self._consecutive_failures, 10)))
        return min(ceiling, exponential * random.uniform(0.75, 1.25))

    def _safe_error(self, exc: Exception) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        for secret in (self._settings.worker_token, self._settings.supabase_anon_key):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text[:500]

    def stats(self) -> dict[str, object]:
        return {
            "enabled": self._settings.supabase_queue_enabled,
            "running": self.running,
            "known_connections": len(self._known_connections),
            "cycles": self.cycles,
            "claimed": self.claimed,
            "completed": self.completed,
            "failed": self.failed,
            "last_error": self.last_error,
        }
