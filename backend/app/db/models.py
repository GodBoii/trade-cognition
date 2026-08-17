"""Database schema.

The interesting part is :class:`ManagedTrade.active_key`.  Rule 1 ("one active
entry per user per derivative") is enforced by the database, not only by
application code: ``active_key`` holds ``"<account id>:<SYMBOL>"`` while a trade
is live and ``NULL`` once it closes, under a unique constraint on
``(user_id, active_key)``.  Because SQL treats ``NULL`` values as distinct, any
number of *closed* trades on a symbol can coexist while a second *live* one is
impossible - even under a race between two concurrent requests.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..domain.enums import (
    CapitalBasis,
    CloseReason,
    LadderPreset,
    LotRuleMode,
    OrderKind,
    Side,
    StageStatus,
    TradeStatus,
)
from ..domain.profile import RiskProfile
from .base import Base, TimestampMixin, UtcDateTime, utcnow


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    supabase_user_id: Mapped[str | None] = mapped_column(
        String(36), unique=True, index=True, default=None
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    accounts: Mapped[list[Mt5AccountRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    profile: Mapped[RiskProfileRow | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.id} {self.email}>"


class Mt5AccountRow(Base, TimestampMixin):
    """A connected MT5 trading account.

    The broker password is stored as a Fernet token in ``password_encrypted``
    and is never returned by the API.
    """

    __tablename__ = "mt5_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "login", "server", name="uq_mt5_accounts_user_login_server"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    label: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    login: Mapped[int] = mapped_column(Integer, nullable=False)
    server: Mapped[str] = mapped_column(String(160), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    terminal_path: Mapped[str] = mapped_column(String(500), default="", nullable=False)

    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Last known state, refreshed on every verified connection.  Cached so the
    # UI can render an account list without waking the terminal.
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    company: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    account_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_equity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, default=None
    )
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)

    user: Mapped[User] = relationship(back_populates="accounts")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Mt5Account {self.id} login={self.login} server={self.server}>"


class RiskProfileRow(Base, TimestampMixin):
    """Persisted :class:`~app.domain.profile.RiskProfile`."""

    __tablename__ = "risk_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    lots_per_1000: Mapped[float] = mapped_column(Float, default=0.02, nullable=False)
    lot_rule_mode: Mapped[str] = mapped_column(
        String(16), default=LotRuleMode.STRICT.value, nullable=False
    )
    max_risk_pct: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    capital_basis: Mapped[str] = mapped_column(
        String(16), default=CapitalBasis.BALANCE.value, nullable=False
    )
    fixed_capital: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ladder_preset: Mapped[str] = mapped_column(
        String(32), default=LadderPreset.STANDARD_1_2_3.value, nullable=False
    )
    max_concurrent_positions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    margin_utilisation_cap_pct: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    require_stop_loss: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_reward_risk: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    allow_manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship(back_populates="profile")

    # ------------------------------------------------------------ conversions
    def to_domain(self) -> RiskProfile:
        return RiskProfile(
            lots_per_1000=self.lots_per_1000,
            lot_rule_mode=LotRuleMode(self.lot_rule_mode),
            max_risk_pct=self.max_risk_pct,
            capital_basis=CapitalBasis(self.capital_basis),
            fixed_capital=self.fixed_capital,
            ladder_preset=LadderPreset(self.ladder_preset),
            max_concurrent_positions=self.max_concurrent_positions,
            max_daily_loss_pct=self.max_daily_loss_pct,
            margin_utilisation_cap_pct=self.margin_utilisation_cap_pct,
            require_stop_loss=self.require_stop_loss,
            min_reward_risk=self.min_reward_risk,
            allow_manual_override=self.allow_manual_override,
        )

    def apply(self, profile: RiskProfile) -> None:
        self.lots_per_1000 = profile.lots_per_1000
        self.lot_rule_mode = profile.lot_rule_mode.value
        self.max_risk_pct = profile.max_risk_pct
        self.capital_basis = profile.capital_basis.value
        self.fixed_capital = profile.fixed_capital
        self.ladder_preset = profile.ladder_preset.value
        self.max_concurrent_positions = profile.max_concurrent_positions
        self.max_daily_loss_pct = profile.max_daily_loss_pct
        self.margin_utilisation_cap_pct = profile.margin_utilisation_cap_pct
        self.require_stop_loss = profile.require_stop_loss
        self.min_reward_risk = profile.min_reward_risk
        self.allow_manual_override = profile.allow_manual_override


class ManagedTrade(Base, TimestampMixin):
    """A trade whose lifecycle the platform owns end to end."""

    __tablename__ = "managed_trades"
    __table_args__ = (
        # Rule 1, enforced by the database. NULL active_key => closed trade.
        UniqueConstraint("user_id", "active_key", name="uq_managed_trades_active_symbol"),
        Index("ix_managed_trades_user_status", "user_id", "status"),
        Index("ix_managed_trades_ticket", "position_ticket"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    mt5_account_id: Mapped[int] = mapped_column(
        ForeignKey("mt5_accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )

    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_kind: Mapped[str] = mapped_column(String(12), default=OrderKind.MARKET.value)
    status: Mapped[str] = mapped_column(
        String(16), default=TradeStatus.PENDING.value, nullable=False, index=True
    )

    #: ``"<account id>:<SYMBOL>"`` while live, ``NULL`` when closed.
    active_key: Mapped[str | None] = mapped_column(String(80), default=None)

    # --- as planned ---------------------------------------------------------
    requested_entry: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    initial_stop: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    current_stop: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    risk_distance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    initial_volume: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    remaining_volume: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ladder_preset: Mapped[str] = mapped_column(
        String(32), default=LadderPreset.STANDARD_1_2_3.value, nullable=False
    )

    capital_at_entry: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    planned_risk: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    planned_risk_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    planned_profit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    account_currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)

    # --- execution ---------------------------------------------------------
    position_ticket: Mapped[int | None] = mapped_column(Integer, default=None)
    entry_deal: Mapped[int | None] = mapped_column(Integer, default=None)
    realised_pl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    close_reason: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    comment: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)

    opened_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    #: When the position manager last inspected this trade. The window between
    #: this and now is what ``price_extremes`` is queried for, so a target
    #: touched between passes is still detected.
    last_checked_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, default=None
    )

    #: Full :class:`~app.domain.risk.TradePlan` snapshot as approved.
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    #: Rules report as approved, for audit.
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    stages: Mapped[list[TradeStageRow]] = relationship(
        back_populates="trade",
        cascade="all, delete-orphan",
        order_by="TradeStageRow.sequence",
        lazy="selectin",
    )
    events: Mapped[list[TradeEventRow]] = relationship(
        back_populates="trade",
        cascade="all, delete-orphan",
        order_by="TradeEventRow.id",
        lazy="select",
    )

    # ------------------------------------------------------------- behaviour
    @staticmethod
    def build_active_key(mt5_account_id: int, symbol: str) -> str:
        """The Rule 1 lock value.

        Scoped **per user per symbol**, which is the literal reading of Rule 1
        ("a user can have only one active entry for a particular derivative").
        A user with several connected accounts therefore still gets one entry per
        symbol in total.  To scope the rule per account instead, return
        ``f"{mt5_account_id}:{symbol.upper()}"`` - the unique constraint and
        every caller already pass the account id for exactly that reason.
        """
        return symbol.upper()

    @property
    def side_enum(self) -> Side:
        return Side(self.side)

    @property
    def status_enum(self) -> TradeStatus:
        return TradeStatus(self.status)

    @property
    def is_active(self) -> bool:
        return self.status_enum in TradeStatus.active()

    def mark_closed(self, reason: CloseReason | str, *, when: datetime | None = None) -> None:
        """Close the trade and release the Rule 1 lock."""
        self.status = TradeStatus.CLOSED.value
        self.close_reason = reason.value if isinstance(reason, CloseReason) else str(reason)
        self.closed_at = when or utcnow()
        self.remaining_volume = 0.0
        self.active_key = None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ManagedTrade {self.id} {self.symbol} {self.side} {self.status} "
            f"ticket={self.position_ticket}>"
        )


class TradeStageRow(Base):
    """One rung of the ladder as scheduled and then executed."""

    __tablename__ = "trade_stages"
    __table_args__ = (
        UniqueConstraint("trade_id", "stage_key", name="uq_trade_stages_trade_stage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int] = mapped_column(
        ForeignKey("managed_trades.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage_key: Mapped[str] = mapped_column(String(8), nullable=False)
    r_multiple: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    target_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    planned_volume: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    executed_volume: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sl_action: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    sl_after: Mapped[float | None] = mapped_column(Float, default=None)
    planned_profit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    realised_pl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), default=StageStatus.PENDING.value, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    trade: Mapped[ManagedTrade] = relationship(back_populates="stages")

    @property
    def is_pending(self) -> bool:
        return self.status == StageStatus.PENDING.value

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TradeStage {self.stage_key} {self.status} vol={self.planned_volume}>"


class TradeEventRow(Base):
    """Append-only audit trail: everything the platform did and why."""

    __tablename__ = "trade_events"
    __table_args__ = (Index("ix_trade_events_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("managed_trades.id", ondelete="CASCADE"), index=True, default=None
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False, index=True
    )

    trade: Mapped[ManagedTrade | None] = relationship(back_populates="events")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TradeEvent {self.event_type} trade={self.trade_id}>"


class DecisionRow(Base):
    """Every pre-trade validation, approved or rejected.

    This is the compliance record: it proves which numbers the platform saw and
    which rule fired, including for trades that never reached the broker.
    """

    __tablename__ = "decisions"
    __table_args__ = (Index("ix_decisions_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    mt5_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("mt5_accounts.id", ondelete="SET NULL"), default=None
    )
    trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("managed_trades.id", ondelete="SET NULL"), default=None
    )

    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    executed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    volume: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_loss: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    risk_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    expected_profit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reward_risk: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    violation_codes: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        verdict = "approved" if self.approved else "rejected"
        return f"<Decision {self.id} {self.symbol} {verdict}>"
