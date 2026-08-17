"""Mock-only adapter from Supabase queue commands to the trading engine.

This is intentionally not a live-MT5 implementation. The current distributed
delivery path does not yet persist an external command receipt or reconcile an
ambiguous broker response after a crash. Application startup therefore refuses
to enable this processor with ``TC_MT5_GATEWAY=real``.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from sqlalchemy import select

from ..config import Settings, settings as default_settings
from ..db.models import ManagedTrade, Mt5AccountRow, User
from ..db.session import session_scope
from ..domain.enums import CapitalBasis, LadderPreset, LotRuleMode, OrderKind, Side
from ..domain.profile import RiskProfile
from ..domain.risk import TradeIntent, plan_to_dict
from ..errors import Mt5Error, ServiceUnavailableError, TradeCognitionError
from ..logging_conf import get_logger
from ..supabase.client import SupabaseQueueClient
from ..supabase.schemas import ClaimedTradeCommand, WorkerResult
from . import accounts as accounts_service
from . import journal, trades as trades_service, users as users_service

log = get_logger(__name__)

MOCK_SERVER = "MockBroker-Demo"


class RemoteCommandProcessor:
    """Process scoped Supabase commands through the existing mock engine."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: SupabaseQueueClient | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._client = client or SupabaseQueueClient(self._settings)
        self._owns_client = client is None
        self._reported_trade_fingerprints: dict[UUID, str] = {}

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def __call__(self, command: ClaimedTradeCommand) -> WorkerResult:
        """Resolve remote context and execute one supported command."""
        context = await self._client.get_context(command.connection_id)
        try:
            connection, rules = _validate_context(command, context)
            if command.command_type == "submit_trade":
                return await self._submit(command, connection, rules)
            if command.command_type == "refresh_account":
                return await self._refresh(command, connection, rules)
            if command.command_type == "close_trade":
                return await self._close_trade(command, connection, rules)
            if command.command_type == "sync_trade":
                return await self._sync_trade(command, connection, rules)
            raise ValueError(f"unsupported command type {command.command_type}")
        except (Mt5Error, ServiceUnavailableError) as exc:
            return WorkerResult(
                outcome="failed",
                error_code=exc.code,
                message=exc.message,
                retryable=True,
            )
        except TradeCognitionError as exc:
            if command.command_type in {"close_trade", "sync_trade"}:
                return _control_command_error(command, exc.code, exc.message)
            return WorkerResult(
                outcome="rejected",
                intent_status="rejected" if command.intent_id else None,
                error_code=exc.code,
                message=exc.message,
                result=exc.to_payload(),
            )
        except (TypeError, ValueError, KeyError) as exc:
            if command.command_type in {"close_trade", "sync_trade"}:
                return _control_command_error(command, "invalid_command", str(exc))
            return WorkerResult(
                outcome="rejected",
                intent_status="rejected" if command.intent_id else None,
                error_code="invalid_command",
                message=f"The queued command is invalid: {exc}",
            )

    async def heartbeat_snapshot(self, connection_id: UUID) -> dict[str, object]:
        """Build an account snapshot and report committed mock trade changes."""
        context = await self._client.get_context(connection_id)
        command_user = UUID(str(context["connection"]["user_id"]))
        with session_scope() as session:
            user = _resolve_user(session, command_user)
            _sync_profile(session, user, context["rules"])
            account = await _resolve_mock_account(
                session, user, connection_id, context["connection"]
            )
            snapshot = await accounts_service.refresh(session, account)
            trade_updates = _mapped_trade_updates(session, account)
            snapshot_payload = _snapshot_payload(snapshot)

        # Network writes happen only after the local session commits. A failed
        # RPC does not advance the cache, so the next heartbeat retries it.
        if self._settings.supabase_queue_enabled and self._settings.mt5_gateway == "mock":
            for intent_id, status, event_type, message, payload, fingerprint in trade_updates:
                if self._reported_trade_fingerprints.get(intent_id) == fingerprint:
                    continue
                await self._client.update_trade_state(
                    intent_id=intent_id,
                    status=status,
                    event_type=event_type,
                    message=message,
                    payload=payload,
                )
                self._reported_trade_fingerprints[intent_id] = fingerprint
        return snapshot_payload

    async def _submit(
        self,
        command: ClaimedTradeCommand,
        connection: dict[str, object],
        rules: dict[str, object],
    ) -> WorkerResult:
        if command.intent_id is None:
            raise ValueError("submit_trade requires an intent_id")
        intent = _trade_intent(command)

        with session_scope() as session:
            user = _resolve_user(session, command.user_id)
            _sync_profile(session, user, rules)
            account = await _resolve_mock_account(
                session, user, command.connection_id, connection
            )

            existing = _existing_trade(session, user, account, intent.comment)
            if existing is not None:
                _bind_external_intent(existing, command.intent_id)
                session.flush()
                return _existing_trade_result(existing)

            result = await trades_service.submit(
                session,
                user,
                account,
                intent,
                override=False,
            )
            if result.trade is not None:
                _bind_external_intent(result.trade, command.intent_id)
                session.flush()
            return _submission_result(result)

    async def _refresh(
        self,
        command: ClaimedTradeCommand,
        connection: dict[str, object],
        rules: dict[str, object],
    ) -> WorkerResult:
        with session_scope() as session:
            user = _resolve_user(session, command.user_id)
            _sync_profile(session, user, rules)
            account = await _resolve_mock_account(
                session, user, command.connection_id, connection
            )
            snapshot = await accounts_service.refresh(session, account)
            payload = _snapshot_payload(snapshot)

        await self._client.heartbeat(command.connection_id, payload)
        return WorkerResult(
            outcome="succeeded",
            message="Mock MT5 account snapshot refreshed.",
            result={"account": payload},
        )

    async def _close_trade(
        self,
        command: ClaimedTradeCommand,
        connection: dict[str, object],
        rules: dict[str, object],
    ) -> WorkerResult:
        with session_scope() as session:
            user, _account, trade = await _resolve_managed_trade(
                session, command, connection, rules
            )
            if trade is None:
                return _missing_managed_trade(command)
            if not trade.is_active:
                return WorkerResult(
                    outcome="succeeded",
                    intent_status=_intent_status(trade.status),
                    message="The local mock trade was already closed; no broker action was sent.",
                    result={"applied": False, "trade": _trade_payload(trade)},
                )
            volume = _optional_float(command.payload.get("volume"))
            action = await trades_service.close(
                session, user, trade.id, volume=volume
            )
            refreshed = trades_service.get_trade(session, user, trade.id)
            return WorkerResult(
                outcome="succeeded",
                intent_status=_intent_status(refreshed.status),
                message="Mock close command applied.",
                result={
                    "applied": action.changed,
                    "actions": action.actions,
                    "error": action.error,
                    "trade": _trade_payload(refreshed),
                },
            )

    async def _sync_trade(
        self,
        command: ClaimedTradeCommand,
        connection: dict[str, object],
        rules: dict[str, object],
    ) -> WorkerResult:
        with session_scope() as session:
            user, _account, trade = await _resolve_managed_trade(
                session, command, connection, rules
            )
            if trade is None:
                return _missing_managed_trade(command)
            action = await trades_service.sync(session, user, trade.id)
            refreshed = trades_service.get_trade(session, user, trade.id)
            return WorkerResult(
                outcome="succeeded",
                intent_status=_intent_status(refreshed.status),
                message="Mock trade state synchronized.",
                result={
                    "applied": action.changed,
                    "actions": action.actions,
                    "error": action.error,
                    "trade": _trade_payload(refreshed),
                },
            )


def validate_mock_queue_runtime(settings: Settings) -> None:
    """Refuse live trading until durable broker idempotency is implemented."""
    if settings.supabase_queue_enabled and settings.mt5_gateway != "mock":
        raise RuntimeError(
            "The Supabase command worker is currently mock-only. Set TC_MT5_GATEWAY=mock. "
            "Real MT5 queue execution requires persistent command receipts and broker "
            "reconciliation before it can be enabled safely."
        )


def _validate_context(
    command: ClaimedTradeCommand, context: dict[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    connection = context.get("connection")
    rules = context.get("rules")
    if not isinstance(connection, dict) or not isinstance(rules, dict):
        raise ValueError("worker context must contain connection and rules objects")
    if UUID(str(connection.get("id"))) != command.connection_id:
        raise ValueError("worker context connection does not match the claimed command")
    if UUID(str(connection.get("user_id"))) != command.user_id:
        raise ValueError("worker context user does not match the claimed command")
    return connection, rules


def _resolve_user(session, supabase_user_id: UUID) -> User:
    user = users_service.get_by_supabase_id(session, str(supabase_user_id))
    if user is None:
        # The worker RPC is already scoped to this authenticated Supabase user.
        # Email is not returned in the worker context, so use a collision-free
        # local placeholder until the user next reaches the legacy API bridge.
        user = User(
            email=f"worker+{supabase_user_id}@local.invalid",
            supabase_user_id=str(supabase_user_id),
            password_hash="supabase_managed",
            display_name="Supabase trader",
        )
        session.add(user)
        session.flush()
        users_service.ensure_profile(session, user)
    if not user.is_active:
        raise ValueError("the local trading user is disabled")
    return user


def _sync_profile(session, user: User, raw: dict[str, object]) -> RiskProfile:
    max_risk = float(raw.get("max_risk_pct", 2.0))
    if max_risk <= 0:
        raise ValueError("max_risk_pct must be greater than zero")
    capital_basis = CapitalBasis(str(raw.get("capital_basis", "balance")))
    fixed_capital = float(raw.get("fixed_capital", 0.0))

    profile = RiskProfile(
        lots_per_1000=0.02,
        lot_rule_mode=LotRuleMode.STRICT,
        max_risk_pct=min(max_risk, 2.0),
        capital_basis=capital_basis,
        fixed_capital=fixed_capital,
        ladder_preset=LadderPreset.RUNNER_1_2_3,
        max_concurrent_positions=int(raw.get("max_concurrent_positions", 0)),
        max_daily_loss_pct=float(raw.get("max_daily_loss_pct", 0.0)),
        margin_utilisation_cap_pct=float(raw.get("margin_utilisation_cap_pct", 50.0)),
        require_stop_loss=True,
        min_reward_risk=float(raw.get("min_reward_risk", 1.0)),
        allow_manual_override=False,
    )
    return users_service.update_profile(session, user, profile)


async def _resolve_mock_account(
    session,
    user: User,
    connection_id: UUID,
    connection: dict[str, object],
) -> Mt5AccountRow:
    external_id = str(connection_id)
    account = session.scalar(
        select(Mt5AccountRow).where(Mt5AccountRow.external_connection_id == external_id)
    )
    if account is not None:
        if account.user_id != user.id:
            raise ValueError("the Supabase MT5 connection is linked to another local user")
        return account

    # This credential is accepted only by MockMt5Gateway. It is deterministic
    # local simulator state, not a broker password supplied through Supabase.
    login = 10_000_000_000 + (connection_id.int % 900_000_000_000_000)
    account, _ = await accounts_service.verify_and_store(
        session,
        user,
        login=login,
        password=f"mock-{connection_id.hex}",
        server=MOCK_SERVER,
        label=str(connection.get("label") or "Supabase mock account")[:120],
        make_default=True,
    )
    account.external_connection_id = external_id
    session.flush()
    return account


async def _resolve_managed_trade(
    session,
    command: ClaimedTradeCommand,
    connection: dict[str, object],
    rules: dict[str, object],
) -> tuple[User, Mt5AccountRow, ManagedTrade | None]:
    user = _resolve_user(session, command.user_id)
    _sync_profile(session, user, rules)
    account = await _resolve_mock_account(
        session, user, command.connection_id, connection
    )
    trade = None
    if command.intent_id is not None:
        trade = _existing_trade(
            session, user, account, _intent_comment(command.intent_id)
        )
    return user, account, trade


def _trade_intent(command: ClaimedTradeCommand) -> TradeIntent:
    if command.intent_id is None:
        raise ValueError("submit_trade requires an intent_id")
    payload = command.payload
    order_kind = OrderKind(str(payload.get("order_kind", "market")).lower())
    if order_kind is not OrderKind.MARKET:
        raise ValueError("only market orders are integrated with the mock worker")
    symbol = str(payload.get("symbol", "")).strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    stop_loss = _optional_float(payload.get("stop_loss"))
    stop_points = _optional_float(payload.get("stop_points"))
    if (stop_loss is None) == (stop_points is None):
        raise ValueError("exactly one of stop_loss or stop_points is required")

    return TradeIntent(
        symbol=symbol,
        side=Side(str(payload.get("side", "")).lower()),
        order_kind=order_kind,
        entry_price=_optional_float(payload.get("requested_entry")),
        sl_price=stop_loss,
        sl_points=stop_points,
        volume=_optional_float(payload.get("requested_volume")),
        ladder_preset=LadderPreset.RUNNER_1_2_3,
        comment=_intent_comment(command.intent_id),
    )


def _intent_comment(intent_id: UUID) -> str:
    """Stable 96-bit mock idempotency fingerprint, within MT5's comment limit."""
    return f"TC-{intent_id.hex[:24]}"


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _existing_trade(session, user: User, account: Mt5AccountRow, comment: str):
    return session.scalar(
        select(ManagedTrade)
        .where(
            ManagedTrade.user_id == user.id,
            ManagedTrade.mt5_account_id == account.id,
            ManagedTrade.comment == comment,
        )
        .order_by(ManagedTrade.id.desc())
    )


def _existing_trade_result(trade: ManagedTrade) -> WorkerResult:
    if trade.status == "error":
        return WorkerResult(
            outcome="failed",
            intent_status="failed",
            error_code="broker_rejected",
            message=trade.last_error or "The mock broker rejected this trade.",
            result=_trade_payload(trade),
        )
    return WorkerResult(
        outcome="succeeded",
        intent_status=_intent_status(trade.status),
        message="Existing mock execution recovered without placing a duplicate order.",
        result=_trade_payload(trade),
    )


def _bind_external_intent(trade: ManagedTrade, intent_id: UUID) -> None:
    """Attach the full Supabase UUID without permitting a remap."""
    external_id = str(intent_id)
    if trade.external_intent_id not in (None, external_id):
        raise ValueError("the local trade is already mapped to another Supabase intent")
    trade.external_intent_id = external_id


def _mapped_trade_updates(session, account: Mt5AccountRow):
    """Build sanitized, detached lifecycle reports for one mapped account."""
    trades = list(
        session.scalars(
            select(ManagedTrade)
            .where(
                ManagedTrade.mt5_account_id == account.id,
                ManagedTrade.external_intent_id.is_not(None),
            )
            .order_by(ManagedTrade.id)
        )
    )
    updates = []
    for trade in trades:
        intent_id = UUID(str(trade.external_intent_id))
        stages = [
            {
                "stage": stage.stage_key,
                "status": stage.status,
                "target_price": stage.target_price,
                "planned_volume": stage.planned_volume,
                "executed_volume": stage.executed_volume,
                "realised_pl": stage.realised_pl,
                "sl_after": stage.sl_after,
                "executed_at": (
                    stage.executed_at.isoformat() if stage.executed_at is not None else None
                ),
            }
            for stage in trade.stages
        ]
        payload = {
            "position_ticket": trade.position_ticket,
            "trade": {
                "local_trade_id": trade.id,
                "status": trade.status,
                "symbol": trade.symbol,
                "side": trade.side,
                "remaining_volume": trade.remaining_volume,
                "initial_volume": trade.initial_volume,
                "current_stop": trade.current_stop,
                "realised_pl": trade.realised_pl,
                "close_reason": trade.close_reason,
                "last_error": trade.last_error,
            },
            "stages": stages,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        status = _intent_status(trade.status)
        event_type, message = _report_event(trade, stages, status)
        updates.append((intent_id, status, event_type, message, payload, fingerprint))
    return updates


def _report_event(trade: ManagedTrade, stages: list[dict[str, object]], status: str):
    """Name the most useful event represented by the latest state snapshot."""
    if status == "closed":
        return (
            "position_closed",
            f"{trade.symbol} closed ({trade.close_reason or 'completed'}); "
            f"realised P/L {trade.realised_pl:.2f} {trade.account_currency}.",
        )
    filled = [stage for stage in stages if stage["status"] == "filled"]
    if filled:
        latest = filled[-1]
        return (
            f"{str(latest['stage']).lower()}_filled",
            f"{latest['stage']} completed for {trade.symbol}; "
            f"{trade.remaining_volume:g} lots remain.",
        )
    if status == "failed" or trade.last_error:
        return (
            "management_error",
            trade.last_error or f"Management failed for {trade.symbol}.",
        )
    return (
        "trade_state_reconciled",
        f"Worker reconciled the {trade.symbol} trade as {status}.",
    )


def _missing_managed_trade(command: ClaimedTradeCommand) -> WorkerResult:
    """Reject a control command without changing its parent trade lifecycle."""
    return WorkerResult(
        outcome="rejected",
        intent_status=None,
        message="No mapped local mock trade was found; no broker action was sent.",
        result={
            "applied": False,
            "error_code": "managed_trade_not_found",
            "command_type": command.command_type,
        },
    )


def _control_command_error(
    command: ClaimedTradeCommand, error_code: str, message: str
) -> WorkerResult:
    """Reject invalid control input while preserving the parent trade state."""
    return WorkerResult(
        outcome="rejected",
        intent_status=None,
        message="The control command was not applied; the trade state was preserved.",
        result={
            "applied": False,
            "error_code": error_code,
            "error_message": message,
            "command_type": command.command_type,
        },
    )


def _intent_status(local_status: str):
    return {
        "pending": "submitted",
        "open": "open",
        "scaling": "scaling",
        "closed": "closed",
        "error": "failed",
        "rejected": "rejected",
    }.get(local_status, "failed")


def _submission_result(result) -> WorkerResult:
    plan = result.fill_plan or result.assessment.plan
    payload = {
        "approved": result.approved,
        "executed": result.executed,
        "message": result.message,
        "plan": journal.jsonable(plan_to_dict(plan)),
        "rules": _rules_payload(result.assessment.report),
        "order_ticket": result.order.order if result.order else None,
        "position_ticket": result.trade.position_ticket if result.trade else None,
        "trade": _trade_payload(result.trade) if result.trade else None,
    }
    if not result.approved:
        return WorkerResult(
            outcome="rejected",
            intent_status="rejected",
            error_code="rules_rejected",
            message=result.message,
            result=payload,
        )
    if not result.executed:
        return WorkerResult(
            outcome="failed",
            intent_status="failed",
            error_code="broker_rejected",
            message=result.message,
            result=payload,
        )
    return WorkerResult(
        outcome="succeeded",
        intent_status="open",
        message=result.message,
        result=payload,
    )


def _rules_payload(report) -> dict[str, object]:
    return {
        "approved": report.approved,
        "overridden": list(report.overridden),
        "violations": [item.code for item in report.violations],
        "summary": report.rejection_summary or "All rules satisfied.",
        "checks": [journal.jsonable(check) for check in report.checks],
    }


def _trade_payload(trade: ManagedTrade) -> dict[str, object]:
    return {
        "local_trade_id": trade.id,
        "status": trade.status,
        "symbol": trade.symbol,
        "side": trade.side,
        "position_ticket": trade.position_ticket,
        "entry_price": trade.entry_price,
        "initial_stop": trade.initial_stop,
        "current_stop": trade.current_stop,
        "initial_volume": trade.initial_volume,
        "remaining_volume": trade.remaining_volume,
        "realised_pl": trade.realised_pl,
        "plan": trade.plan,
        "rules": trade.rules,
    }


def _snapshot_payload(snapshot) -> dict[str, object]:
    return {
        "login": snapshot.login,
        "server": snapshot.server,
        "company": snapshot.company,
        "account_name": snapshot.name,
        "currency": snapshot.currency,
        "leverage": snapshot.leverage,
        "status": "online",
        "trade_allowed": snapshot.trade_allowed,
        "expert_allowed": snapshot.trade_expert,
        "balance": snapshot.balance,
        "equity": snapshot.equity,
        "margin": snapshot.margin,
        "free_margin": snapshot.margin_free,
    }
