from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.config import Settings
from app.supabase.client import SupabaseQueueClient, SupabaseRequestError
from app.supabase.schemas import ClaimedTradeCommand, WorkerResult
from app.workers.supabase_queue import SupabaseQueueWorker


ANON_KEY = "public-anon-key"
WORKER_TOKEN = "tcw_super-secret-worker-token"


def queue_settings(**overrides) -> Settings:
    values: dict[str, Any] = {
        "supabase_url": "https://project.supabase.co",
        "supabase_anon_key": ANON_KEY,
        "supabase_queue_enabled": True,
        "worker_token": WORKER_TOKEN,
        "worker_batch_size": 1,
        "worker_claim_lease_seconds": 90,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def claimed() -> ClaimedTradeCommand:
    now = datetime.now(UTC)
    return ClaimedTradeCommand(
        id=uuid4(),
        user_id=uuid4(),
        connection_id=uuid4(),
        intent_id=uuid4(),
        client_request_id=uuid4(),
        command_type="submit_trade",
        payload={
            "symbol": "EURUSD",
            "side": "buy",
            "stop_points": 400,
        },
        status="claimed",
        claimed_by=uuid4(),
        claim_token=uuid4(),
        claimed_at=now,
        lease_expires_at=now + timedelta(seconds=90),
        expires_at=now + timedelta(minutes=5),
        attempts=1,
        max_attempts=5,
    )


class FakeQueueClient:
    def __init__(self, commands=None, connections=None) -> None:
        self.commands = list(commands or [])
        self.connections = list(connections or [])
        self.completed: list[tuple[ClaimedTradeCommand, WorkerResult]] = []
        self.failures: list[dict[str, Any]] = []
        self.heartbeats: list[dict[str, Any]] = []
        self.calls: list[str] = []

    async def list_connections(self):
        self.calls.append("list_connections")
        return list(self.connections)

    async def claim_commands(self):
        self.calls.append("claim_commands")
        commands, self.commands = self.commands, []
        return commands

    async def complete_command(self, command, outcome):
        self.completed.append((command, outcome))

    async def fail_command(self, command, **kwargs):
        self.failures.append({"command": command, **kwargs})

    async def heartbeat(self, connection_id, snapshot=None):
        self.heartbeats.append(
            {"connection_id": connection_id, "snapshot": dict(snapshot or {})}
        )

    async def close(self):
        return None


def test_client_claim_contract_uses_anon_header_and_scoped_token():
    command = claimed()
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["apikey"] = request.headers["apikey"]
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=[command.model_dump(mode="json")])

    async def scenario():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = SupabaseQueueClient(queue_settings(), http_client=http)
        representation = repr(client)
        rows = await client.claim_commands()
        await http.aclose()
        return representation, rows

    representation, rows = asyncio.run(scenario())
    assert rows[0].id == command.id
    assert captured["url"].endswith("/rest/v1/rpc/tcq_claim_trade_commands")
    assert captured["apikey"] == ANON_KEY
    assert captured["authorization"] == f"Bearer {ANON_KEY}"
    assert captured["body"] == {
        "p_worker_token": WORKER_TOKEN,
        "p_limit": 1,
        "p_lease_seconds": 90,
    }
    assert WORKER_TOKEN not in representation
    assert ANON_KEY not in representation


def test_client_complete_contract_uses_claim_fencing_token():
    command = claimed()
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    async def scenario():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = SupabaseQueueClient(queue_settings(), http_client=http)
        await client.complete_command(
            command,
            WorkerResult(
                outcome="succeeded",
                intent_status="open",
                message=f"filled without leaking {ANON_KEY}",
                result={"position_ticket": 123, "unsafe": WORKER_TOKEN},
            ),
        )
        await http.aclose()

    asyncio.run(scenario())
    assert captured["url"].endswith("/rest/v1/rpc/tcq_complete_trade_command")
    assert captured["body"]["p_command_id"] == str(command.id)
    assert captured["body"]["p_claim_token"] == str(command.claim_token)
    assert captured["body"]["p_outcome"] == "succeeded"
    assert captured["body"]["p_intent_status"] == "open"
    assert captured["body"]["p_error_message"] == "filled without leaking [REDACTED]"
    assert captured["body"]["p_result"]["unsafe"] == "[REDACTED]"


def test_retryable_failure_appends_event_and_leaves_claim_unfinalized():
    command = claimed()
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    async def scenario():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = SupabaseQueueClient(queue_settings(), http_client=http)
        await client.fail_command(
            command,
            error_code="mt5_unavailable",
            message="Terminal is temporarily offline.",
            retryable=True,
        )
        await http.aclose()

    asyncio.run(scenario())
    assert captured["url"].endswith("/rest/v1/rpc/tcq_worker_append_event")
    assert captured["body"]["p_event_type"] == "command_retry_scheduled"
    assert captured["body"]["p_intent_id"] == str(command.intent_id)


def test_client_context_heartbeat_event_and_state_match_final_sql_rpc_names():
    command = claimed()
    paths = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("tcq_worker_list_connections"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": str(command.connection_id),
                        "is_enabled": True,
                    }
                ],
            )
        if request.url.path.endswith("tcq_worker_get_context"):
            return httpx.Response(200, json={"connection": {}, "rules": {}})
        return httpx.Response(200, json={})

    async def scenario():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = SupabaseQueueClient(queue_settings(), http_client=http)
        connections = await client.list_connections()
        context = await client.get_context(command.connection_id)
        await client.extend_lease(command)
        await client.heartbeat(command.connection_id, {"status": "online"})
        await client.append_event(
            connection_id=command.connection_id,
            intent_id=command.intent_id,
            event_type="validated",
        )
        await client.update_trade_state(
            intent_id=command.intent_id,
            status="open",
            event_type="order_filled",
            payload={"position_ticket": 123},
        )
        await http.aclose()
        return connections, context

    connections, context = asyncio.run(scenario())
    assert connections == [command.connection_id]
    assert context == {"connection": {}, "rules": {}}
    assert [path.rsplit("/", 1)[-1] for path in paths] == [
        "tcq_worker_list_connections",
        "tcq_worker_get_context",
        "tcq_extend_command_lease",
        "tcq_worker_heartbeat",
        "tcq_worker_append_event",
        "tcq_worker_update_trade_state",
    ]


def test_client_errors_are_sanitized_and_do_not_echo_secrets_or_body():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"reflected {WORKER_TOKEN} and {ANON_KEY}")

    async def scenario():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = SupabaseQueueClient(queue_settings(), http_client=http)
        with pytest.raises(SupabaseRequestError) as raised:
            await client.claim_commands()
        await http.aclose()
        return str(raised.value)

    message = asyncio.run(scenario())
    assert "HTTP 401" in message
    assert WORKER_TOKEN not in message
    assert ANON_KEY not in message
    assert "reflected" not in message


def test_worker_is_disabled_by_default_and_does_not_claim():
    fake = FakeQueueClient([claimed()])
    called = False

    async def processor(_):
        nonlocal called
        called = True
        return WorkerResult(outcome="succeeded")

    worker = SupabaseQueueWorker(
        processor,
        settings=queue_settings(supabase_queue_enabled=False),
        client=fake,
    )
    assert asyncio.run(worker.run_once()) == 0
    assert called is False
    assert fake.commands


def test_successful_and_rejected_commands_are_completed_once():
    first, second = claimed(), claimed()
    fake = FakeQueueClient([first, second])

    async def processor(item):
        if item.id == first.id:
            return WorkerResult(
                outcome="succeeded",
                intent_status="open",
                result={"position_ticket": 123},
            )
        return WorkerResult(
            outcome="rejected",
            intent_status="rejected",
            message="Rule 3 rejected the trade.",
        )

    worker = SupabaseQueueWorker(
        processor, settings=queue_settings(worker_batch_size=2), client=fake
    )
    assert asyncio.run(worker.run_once()) == 2
    assert [row[1].outcome for row in fake.completed] == ["succeeded", "rejected"]
    assert not fake.failures
    assert worker.completed == 2


def test_processor_exception_is_retryable_bounded_and_redacts_tokens():
    fake = FakeQueueClient([claimed()])

    async def processor(_):
        raise RuntimeError(f"terminal error {WORKER_TOKEN} {ANON_KEY}" + "x" * 1000)

    worker = SupabaseQueueWorker(processor, settings=queue_settings(), client=fake)
    asyncio.run(worker.run_once())
    failure = fake.failures[0]
    assert failure["error_code"] == "worker_exception"
    assert failure["retryable"] is True
    assert len(failure["message"]) == 500
    assert WORKER_TOKEN not in failure["message"]
    assert ANON_KEY not in failure["message"]
    assert worker.failed == 1


def test_nonretryable_processor_failure_uses_failure_path():
    fake = FakeQueueClient([claimed()])

    async def processor(_):
        return WorkerResult(
            outcome="failed",
            intent_status="failed",
            error_code="account_not_paired",
            message="Pair this account first.",
            retryable=False,
        )

    worker = SupabaseQueueWorker(processor, settings=queue_settings(), client=fake)
    asyncio.run(worker.run_once())
    assert fake.failures[0]["error_code"] == "account_not_paired"
    assert fake.failures[0]["retryable"] is False


def test_heartbeat_requires_connection_and_sends_only_explicit_snapshot():
    fake = FakeQueueClient()
    connection_id = uuid4()
    worker = SupabaseQueueWorker(settings=queue_settings(), client=fake)
    asyncio.run(worker.heartbeat_once(connection_id, {"gateway": "mock"}))
    assert fake.heartbeats == [
        {"connection_id": connection_id, "snapshot": {"gateway": "mock"}}
    ]
    assert WORKER_TOKEN not in str(fake.heartbeats)


def test_discovery_heartbeats_assigned_connection_before_first_claim():
    row = claimed()
    events = []
    fake = FakeQueueClient([row], connections=[row.connection_id])

    async def provider(connection_id):
        events.append(("snapshot", connection_id))
        return {"status": "online", "gateway": "mock"}

    async def processor(command):
        events.append(("process", command.connection_id))
        return WorkerResult(outcome="succeeded", intent_status="open")

    async def scenario():
        worker = SupabaseQueueWorker(
            processor,
            settings=queue_settings(),
            client=fake,
            heartbeat_provider=provider,
        )
        await worker._heartbeat_if_due()
        await worker.run_once()
        return worker

    worker = asyncio.run(scenario())
    assert fake.calls[:2] == ["list_connections", "claim_commands"]
    assert events == [
        ("snapshot", row.connection_id),
        ("process", row.connection_id),
    ]
    assert fake.heartbeats[0]["snapshot"]["status"] == "online"
    assert worker.stats()["known_connections"] == 1


def test_periodic_discovery_finds_new_connections_and_forgets_disabled_ones():
    first, second = uuid4(), uuid4()
    fake = FakeQueueClient(connections=[first])

    async def provider(connection_id):
        return {"connection": str(connection_id)}

    async def scenario():
        worker = SupabaseQueueWorker(
            lambda _: None,  # processor is unused in this discovery-only scenario
            settings=queue_settings(),
            client=fake,
            heartbeat_provider=provider,
        )
        assert await worker.discover_and_heartbeat_once() == 1
        fake.connections = [second]
        assert await worker.discover_and_heartbeat_once() == 1
        return worker

    worker = asyncio.run(scenario())
    assert [item["connection_id"] for item in fake.heartbeats] == [first, second]
    assert worker._known_connections == {second}


def test_enabled_worker_requires_anon_key_token_and_processor():
    incomplete = queue_settings(worker_token="")
    with pytest.raises(RuntimeError, match="TC_WORKER_TOKEN"):
        incomplete.validate_supabase_queue_configuration()

    incomplete = queue_settings(supabase_anon_key="")
    with pytest.raises(RuntimeError, match="TC_SUPABASE_ANON_KEY"):
        incomplete.validate_supabase_queue_configuration()

    worker = SupabaseQueueWorker(settings=queue_settings(), client=FakeQueueClient())
    with pytest.raises(RuntimeError, match="command processor"):
        asyncio.run(worker.start())
