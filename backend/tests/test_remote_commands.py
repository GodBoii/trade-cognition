from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select

import app.main as main_module
from app.config import Settings
from app.db.models import ManagedTrade, Mt5AccountRow, RiskProfileRow, User
from app.db.session import (
    _upgrade_sqlite_managed_trades_table,
    _upgrade_sqlite_mt5_accounts_table,
)
from app.domain.enums import LadderPreset, LotRuleMode
from app.mt5.mock import UNIVERSE
from app.services.remote_commands import RemoteCommandProcessor, validate_mock_queue_runtime
from app.supabase.schemas import ClaimedTradeCommand
from app.workers.monitor import PositionMonitor


class FakeControlClient:
    def __init__(self, context):
        self.context = context
        self.heartbeats = []
        self.trade_updates = []

    async def get_context(self, _connection_id):
        return self.context

    async def heartbeat(self, connection_id, snapshot):
        self.heartbeats.append((connection_id, snapshot))

    async def update_trade_state(self, **kwargs):
        self.trade_updates.append(kwargs)


def worker_settings(**overrides) -> Settings:
    values = {
        "env": "test",
        "mt5_gateway": "mock",
        "supabase_url": "https://project.supabase.co",
        "supabase_anon_key": "public-anon-key",
        "supabase_queue_enabled": True,
        "worker_token": "tcw_test-worker-token",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def command(*, command_type="submit_trade", payload=None, intent_id=None):
    now = datetime.now(UTC)
    user_id = uuid4()
    connection_id = uuid4()
    row = ClaimedTradeCommand(
        id=uuid4(),
        user_id=user_id,
        connection_id=connection_id,
        intent_id=intent_id if intent_id is not None else uuid4(),
        client_request_id=uuid4(),
        command_type=command_type,
        payload=payload
        or {
            "intent_id": str(uuid4()),
            "symbol": "EURUSD",
            "side": "buy",
            "order_kind": "market",
            "requested_entry": None,
            "stop_loss": None,
            "stop_points": 400,
            "requested_volume": None,
        },
        status="claimed",
        available_at=now,
        expires_at=now + timedelta(minutes=5),
        claimed_by=uuid4(),
        claim_token=uuid4(),
        claimed_at=now,
        lease_expires_at=now + timedelta(seconds=90),
        attempts=1,
        max_attempts=5,
        created_at=now,
    )
    return row


def context_for(row):
    return {
        "connection": {
            "id": str(row.connection_id),
            "user_id": str(row.user_id),
            "label": "Supabase demo",
            "status": "pending",
        },
        "rules": {
            "lots_per_1000": 0.02,
            "max_risk_pct": 2.0,
            "capital_basis": "balance",
            "fixed_capital": 0,
            "ladder_preset": "runner_1_2_3",
            "tp1_close_fraction": 0.50,
            "tp2_close_fraction": 0.25,
            "tp3_close_fraction": 0.25,
            "one_active_trade_per_symbol": True,
            "require_stop_loss": True,
            "max_concurrent_positions": 0,
            "max_daily_loss_pct": 0,
            "margin_utilisation_cap_pct": 50,
            "min_reward_risk": 1,
        },
    }


def all_mock_positions():
    return [
        position
        for broker in UNIVERSE._brokers.values()  # noqa: SLF001 - test inspection
        for position in broker.position_snapshots()
    ]


def test_submit_command_provisions_user_account_rules_and_executes(db):
    row = command()
    fake = FakeControlClient(context_for(row))
    processor = RemoteCommandProcessor(worker_settings(), client=fake)

    outcome = asyncio.run(processor(row))

    assert outcome.outcome == "succeeded"
    assert outcome.intent_status == "open"
    assert outcome.result["approved"] is True
    assert outcome.result["executed"] is True
    assert outcome.result["position_ticket"]
    assert len(all_mock_positions()) == 1

    user = db.scalar(select(User).where(User.supabase_user_id == str(row.user_id)))
    assert user is not None
    account = db.scalar(
        select(Mt5AccountRow).where(
            Mt5AccountRow.external_connection_id == str(row.connection_id)
        )
    )
    assert account is not None
    assert account.user_id == user.id
    assert account.server == "MockBroker-Demo"
    trade = db.scalar(
        select(ManagedTrade).where(
            ManagedTrade.external_intent_id == str(row.intent_id)
        )
    )
    assert trade is not None

    profile = db.scalar(select(RiskProfileRow).where(RiskProfileRow.user_id == user.id))
    assert profile is not None
    assert profile.lots_per_1000 == pytest.approx(0.02)
    assert profile.max_risk_pct == pytest.approx(2.0)
    assert profile.lot_rule_mode == LotRuleMode.STRICT.value
    assert profile.ladder_preset == LadderPreset.RUNNER_1_2_3.value

    stages = outcome.result["plan"]["stages"]
    assert [stage["volume"] for stage in stages] == pytest.approx([0.1, 0.05, 0.05])


def test_reclaimed_submit_recovers_existing_mock_trade_without_duplicate(db):
    row = command()
    processor = RemoteCommandProcessor(
        worker_settings(), client=FakeControlClient(context_for(row))
    )

    first = asyncio.run(processor(row))
    second = asyncio.run(processor(row))

    assert first.outcome == "succeeded"
    assert second.outcome == "succeeded"
    assert "without placing a duplicate" in second.message
    assert len(all_mock_positions()) == 1


def test_rule_rejection_is_normalized_and_places_no_order():
    row = command(
        payload={
            "symbol": "EURUSD",
            "side": "buy",
            "order_kind": "market",
            "stop_loss": None,
            "stop_points": 1500,
            "requested_volume": None,
        }
    )
    processor = RemoteCommandProcessor(
        worker_settings(), client=FakeControlClient(context_for(row))
    )

    outcome = asyncio.run(processor(row))

    assert outcome.outcome == "rejected"
    assert outcome.intent_status == "rejected"
    assert outcome.error_code == "rules_rejected"
    assert outcome.result["rules"]["approved"] is False
    assert not all_mock_positions()


def test_refresh_account_publishes_safe_mock_snapshot():
    row = command(command_type="refresh_account", intent_id=None)
    row.intent_id = None
    fake = FakeControlClient(context_for(row))
    processor = RemoteCommandProcessor(worker_settings(), client=fake)

    outcome = asyncio.run(processor(row))

    assert outcome.outcome == "succeeded"
    assert outcome.intent_status is None
    assert fake.heartbeats[0][0] == row.connection_id
    snapshot = fake.heartbeats[0][1]
    assert snapshot["status"] == "online"
    assert snapshot["company"] == "Mock Broker Ltd (simulated)"
    assert "password" not in snapshot


def test_heartbeat_reports_monitor_tp_progression_once_per_changed_state():
    row = command()
    fake = FakeControlClient(context_for(row))
    processor = RemoteCommandProcessor(worker_settings(), client=fake)
    opened = asyncio.run(processor(row))
    targets = [stage["target_price"] for stage in opened.result["plan"]["stages"]]
    broker = next(iter(UNIVERSE._brokers.values()))  # noqa: SLF001 - test inspection
    monitor = PositionMonitor(worker_settings())

    # First heartbeat reconciles the already-committed open state.
    asyncio.run(processor.heartbeat_snapshot(row.connection_id))
    assert fake.trade_updates[-1]["status"] == "open"

    broker.set_price("EURUSD", targets[0])
    asyncio.run(monitor.run_once())
    asyncio.run(processor.heartbeat_snapshot(row.connection_id))
    tp1 = fake.trade_updates[-1]
    assert tp1["status"] == "scaling"
    assert tp1["event_type"] == "tp1_filled"
    assert tp1["payload"]["stages"][0]["status"] == "filled"

    count = len(fake.trade_updates)
    asyncio.run(processor.heartbeat_snapshot(row.connection_id))
    assert len(fake.trade_updates) == count

    broker.set_price("EURUSD", targets[1])
    asyncio.run(monitor.run_once())
    asyncio.run(processor.heartbeat_snapshot(row.connection_id))
    assert fake.trade_updates[-1]["event_type"] == "tp2_filled"

    broker.set_price("EURUSD", targets[2])
    asyncio.run(monitor.run_once())
    asyncio.run(processor.heartbeat_snapshot(row.connection_id))
    closed = fake.trade_updates[-1]
    assert closed["status"] == "closed"
    assert closed["event_type"] == "position_closed"
    assert [stage["status"] for stage in closed["payload"]["stages"]] == [
        "filled",
        "filled",
        "filled",
    ]


def test_heartbeat_reports_monitor_error_without_failing_open_trade(monkeypatch):
    row = command()
    fake = FakeControlClient(context_for(row))
    processor = RemoteCommandProcessor(worker_settings(), client=fake)
    asyncio.run(processor(row))
    asyncio.run(processor.heartbeat_snapshot(row.connection_id))

    async def broken_management(*_args, **_kwargs):
        raise RuntimeError("simulated monitor failure")

    monkeypatch.setattr(
        "app.workers.monitor.position_manager.process_trade", broken_management
    )
    asyncio.run(PositionMonitor(worker_settings()).run_once())
    asyncio.run(processor.heartbeat_snapshot(row.connection_id))

    update = fake.trade_updates[-1]
    assert update["status"] == "open"
    assert update["event_type"] == "management_error"
    assert "simulated monitor failure" in update["message"]
    assert "worker_token" not in str(update).lower()


def test_failed_trade_report_is_retried_on_next_heartbeat():
    row = command()

    class FlakyControlClient(FakeControlClient):
        def __init__(self, context):
            super().__init__(context)
            self.fail_once = True

        async def update_trade_state(self, **kwargs):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("temporary Supabase outage")
            await super().update_trade_state(**kwargs)

    fake = FlakyControlClient(context_for(row))
    processor = RemoteCommandProcessor(worker_settings(), client=fake)
    asyncio.run(processor(row))

    with pytest.raises(RuntimeError, match="temporary Supabase outage"):
        asyncio.run(processor.heartbeat_snapshot(row.connection_id))
    assert fake.trade_updates == []

    asyncio.run(processor.heartbeat_snapshot(row.connection_id))
    assert len(fake.trade_updates) == 1
    assert fake.trade_updates[0]["intent_id"] == row.intent_id


def control_command(parent, command_type, payload=None):
    return parent.model_copy(
        update={
            "id": uuid4(),
            "client_request_id": uuid4(),
            "command_type": command_type,
            "payload": payload or {},
            "claim_token": uuid4(),
        }
    )


def test_sync_and_close_preserve_and_then_complete_open_intent(db):
    submit = command()
    processor = RemoteCommandProcessor(
        worker_settings(), client=FakeControlClient(context_for(submit))
    )

    opened = asyncio.run(processor(submit))
    synced = asyncio.run(processor(control_command(submit, "sync_trade")))
    closed = asyncio.run(processor(control_command(submit, "close_trade")))

    assert opened.intent_status == "open"
    assert synced.outcome == "succeeded"
    assert synced.intent_status == "open"
    assert closed.outcome == "succeeded"
    assert closed.intent_status == "closed"
    assert not all_mock_positions()


@pytest.mark.parametrize("command_type", ["close_trade", "sync_trade"])
def test_missing_control_target_is_safe_noop_that_preserves_remote_state(command_type):
    row = command(command_type=command_type)
    processor = RemoteCommandProcessor(
        worker_settings(), client=FakeControlClient(context_for(row))
    )

    outcome = asyncio.run(processor(row))

    # Rejection is command-local; a null intent status tells the completion RPC
    # to preserve the existing parent trade state.
    assert outcome.outcome == "rejected"
    assert outcome.intent_status is None
    assert outcome.result["applied"] is False
    assert outcome.result["error_code"] == "managed_trade_not_found"


def test_invalid_close_preserves_open_parent_intent_state():
    submit = command()
    processor = RemoteCommandProcessor(
        worker_settings(), client=FakeControlClient(context_for(submit))
    )
    assert asyncio.run(processor(submit)).intent_status == "open"

    invalid = control_command(submit, "close_trade", {"volume": "not-a-number"})
    outcome = asyncio.run(processor(invalid))

    # A malformed action must be rejected without turning an open trade into a
    # rejected trade intent. SQL gates non-submit state changes accordingly.
    assert outcome.outcome == "rejected"
    assert outcome.intent_status is None
    assert outcome.result["applied"] is False
    assert outcome.result["error_code"] == "invalid_command"
    assert len(all_mock_positions()) == 1


def test_queue_startup_refuses_real_gateway():
    with pytest.raises(RuntimeError, match="mock-only"):
        validate_mock_queue_runtime(worker_settings(mt5_gateway="real"))

    validate_mock_queue_runtime(worker_settings(mt5_gateway="mock"))
    validate_mock_queue_runtime(
        worker_settings(mt5_gateway="real", supabase_queue_enabled=False)
    )


def test_lifespan_starts_queue_after_db_and_monitor_and_stops_it_first(monkeypatch):
    events = []

    class FakeMonitor:
        async def start(self):
            events.append("monitor_start")

        async def stop(self):
            events.append("monitor_stop")

    class FakeProcessor:
        def __init__(self, _settings):
            events.append("processor_init")

        async def heartbeat_snapshot(self, _connection_id):
            return {}

        async def close(self):
            events.append("processor_close")

    class FakeQueue:
        def __init__(self, _processor, **_kwargs):
            events.append("queue_init")

        async def start(self):
            events.append("queue_start")

        async def stop(self):
            events.append("queue_stop")

    async def fake_shutdown():
        events.append("runtime_stop")

    monkeypatch.setattr(main_module, "settings", worker_settings())
    monkeypatch.setattr(main_module, "init_db", lambda: events.append("db_init"))
    monkeypatch.setattr(main_module, "get_monitor", lambda: FakeMonitor())
    monkeypatch.setattr(main_module, "RemoteCommandProcessor", FakeProcessor)
    monkeypatch.setattr(main_module, "SupabaseQueueWorker", FakeQueue)
    monkeypatch.setattr(main_module, "shutdown_runtime", fake_shutdown)

    async def scenario():
        async with main_module.lifespan(None):
            events.append("running")

    asyncio.run(scenario())
    assert events == [
        "db_init",
        "monitor_start",
        "processor_init",
        "queue_init",
        "queue_start",
        "running",
        "queue_stop",
        "processor_close",
        "monitor_stop",
        "runtime_stop",
    ]


def test_sqlite_upgrader_adds_external_connection_bridge():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE mt5_accounts (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)"
        )

    _upgrade_sqlite_mt5_accounts_table(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("mt5_accounts")}
    indexes = {index["name"] for index in inspect(engine).get_indexes("mt5_accounts")}
    assert "external_connection_id" in columns
    assert "ix_mt5_accounts_external_connection_id" in indexes


def test_sqlite_upgrader_adds_external_intent_bridge():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE managed_trades (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)"
        )

    _upgrade_sqlite_managed_trades_table(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("managed_trades")}
    indexes = {index["name"] for index in inspect(engine).get_indexes("managed_trades")}
    assert "external_intent_id" in columns
    assert "ix_managed_trades_external_intent_id" in indexes
