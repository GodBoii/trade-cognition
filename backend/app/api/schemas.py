"""Request and response models.

Response models are built with ``from_attributes=True`` so they can be validated
straight from domain dataclasses and ORM rows.  Keeping them explicit (rather
than returning raw dictionaries) is what gives the generated OpenAPI document at
``/docs`` real value.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.enums import (
    CapitalBasis,
    LadderPreset,
    LotRuleMode,
    OrderKind,
    Side,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class ErrorResponse(ApiModel):
    code: str = Field(examples=["rules_rejected"])
    message: str
    details: Any | None = None


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255, examples=["trader@example.com"])
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


class UserResponse(ApiModel):
    id: int
    email: str
    display_name: str
    created_at: datetime
    last_login_at: datetime | None = None


class TokenResponse(ApiModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserResponse


# ---------------------------------------------------------------------------
# MT5 accounts
# ---------------------------------------------------------------------------
class ConnectAccountRequest(BaseModel):
    login: int = Field(gt=0, examples=[123456])
    password: str = Field(min_length=1, max_length=128)
    server: str = Field(min_length=1, max_length=160, examples=["MockBroker-Demo"])
    label: str = Field(default="", max_length=120)
    terminal_path: str = Field(default="", max_length=500)
    make_default: bool = True


class Mt5AccountResponse(ApiModel):
    """A connected account.  The broker password is never serialised."""

    id: int
    label: str
    login: int
    server: str
    currency: str
    company: str
    account_name: str
    leverage: int
    is_default: bool
    is_enabled: bool
    last_balance: float
    last_equity: float
    last_verified_at: datetime | None = None
    last_error: str = ""
    created_at: datetime


class AccountSnapshotResponse(ApiModel):
    login: int
    name: str
    server: str
    currency: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    margin_level: float
    profit: float
    leverage: int
    trade_allowed: bool
    trade_expert: bool
    company: str


class AccountStateResponse(ApiModel):
    account: Mt5AccountResponse
    snapshot: AccountSnapshotResponse
    capital: float
    capital_basis: str
    prescribed_lots_hint: str


# ---------------------------------------------------------------------------
# market data
# ---------------------------------------------------------------------------
class SymbolResponse(ApiModel):
    name: str
    description: str
    path: str
    group: str
    digits: int
    trade_allowed: bool
    bid: float
    ask: float


class SymbolSpecResponse(ApiModel):
    name: str
    digits: int
    point: float
    tick_size: float
    tick_value_loss: float
    tick_value_profit: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level_points: int
    currency_base: str
    currency_profit: str
    description: str
    trade_allowed: bool
    money_per_price_unit_per_lot: float
    min_stop_distance: float


class TickResponse(ApiModel):
    symbol: str
    bid: float
    ask: float
    spread: float
    time: datetime


# ---------------------------------------------------------------------------
# risk profile
# ---------------------------------------------------------------------------
class RiskProfileResponse(ApiModel):
    lots_per_1000: float
    lot_rule_mode: LotRuleMode
    max_risk_pct: float
    capital_basis: CapitalBasis
    fixed_capital: float
    ladder_preset: LadderPreset
    max_concurrent_positions: int
    max_daily_loss_pct: float
    margin_utilisation_cap_pct: float
    require_stop_loss: bool
    min_reward_risk: float
    allow_manual_override: bool


class RiskProfileUpdate(BaseModel):
    lots_per_1000: float = Field(gt=0, le=1)
    max_risk_pct: float = Field(gt=0, le=20)
    lot_rule_mode: LotRuleMode = LotRuleMode.STRICT
    capital_basis: CapitalBasis = CapitalBasis.BALANCE
    fixed_capital: float = Field(default=0.0, ge=0)
    ladder_preset: LadderPreset = LadderPreset.STANDARD_1_2_3
    max_concurrent_positions: int = Field(default=0, ge=0, le=100)
    max_daily_loss_pct: float = Field(default=0.0, ge=0, le=100)
    margin_utilisation_cap_pct: float = Field(default=50.0, ge=0, le=100)
    require_stop_loss: bool = True
    min_reward_risk: float = Field(default=1.0, ge=0, le=100)
    allow_manual_override: bool = False


class LadderStageInfo(ApiModel):
    key: str
    r_multiple: float
    close_fraction: float
    sl_action: str
    note: str


class LadderInfo(ApiModel):
    preset: str
    label: str
    description: str
    stages: list[LadderStageInfo]


# ---------------------------------------------------------------------------
# calculator
# ---------------------------------------------------------------------------
class CalcRequest(BaseModel):
    """A proposed trade.

    Exactly one of ``stop_loss`` / ``stop_points`` is required - the stop is what
    makes risk quantifiable, so there is no default.  ``volume`` is optional and
    defaults to the Rule 2 allocation.
    """

    symbol: str = Field(min_length=1, max_length=40, examples=["EURUSD"])
    side: Side
    order_kind: OrderKind = OrderKind.MARKET
    entry_price: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    stop_points: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, gt=0)
    ladder_preset: LadderPreset | None = None
    account_id: int | None = None
    override: bool = False
    comment: str = Field(default="", max_length=48)

    @field_validator("symbol")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()

    def stop_is_defined(self) -> bool:
        return self.stop_loss is not None or self.stop_points is not None


class StopScanRequest(BaseModel):
    symbol: str
    side: Side
    stop_points: list[float] = Field(min_length=1, max_length=40)
    account_id: int | None = None

    @field_validator("symbol")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class StagePlanResponse(ApiModel):
    key: str
    r_multiple: float
    target_price: float
    target_distance: float
    target_points: float
    volume: float
    cumulative_volume: float
    remaining_volume: float
    money_profit: float
    cumulative_money: float
    sl_action: str
    sl_after: float | None
    sl_after_points_from_entry: float | None
    locked_in_money: float
    will_execute: bool
    note: str


class TradePlanResponse(ApiModel):
    symbol: str
    side: Side
    order_kind: OrderKind

    entry_price: float
    stop_loss: float
    risk_distance: float
    risk_points: float
    bid: float
    ask: float
    spread: float
    spread_points: float
    digits: int

    volume: float
    prescribed_volume: float
    volume_is_prescribed: bool
    volume_min: float
    volume_max: float
    volume_step: float

    capital: float
    capital_basis: str
    account_currency: str
    max_loss: float
    risk_pct_of_capital: float
    max_risk_pct: float
    max_risk_money: float
    risk_headroom: float

    money_per_point: float
    money_per_price_unit_per_lot: float
    spread_cost: float
    pricing_source: str

    required_margin: float
    margin_source: str
    free_margin: float
    margin_pct_of_free_margin: float
    margin_pct_of_capital: float

    stages: list[StagePlanResponse]
    expected_profit: float
    expected_profit_pct_of_capital: float
    reward_risk_final: float
    reward_risk_blended: float

    max_stop_distance: float
    max_stop_points: float
    max_stop_price: float
    volume_for_requested_stop: float
    min_stop_distance: float

    ladder_preset: str
    ladder_label: str
    warnings: list[str]


class RuleCheckResponse(ApiModel):
    code: str
    rule: str
    passed: bool
    severity: str
    message: str
    overridable: bool
    details: dict[str, Any]


class RulesReportResponse(ApiModel):
    approved: bool
    checks: list[RuleCheckResponse]
    overridden: list[str]
    violations: list[str]
    summary: str


class AssessmentResponse(ApiModel):
    plan: TradePlanResponse
    rules: RulesReportResponse
    active_symbols: list[str]
    blocking_ticket: int | None = None
    ladder: LadderInfo


class StopScanRow(ApiModel):
    stop_points: float
    stop_price: float
    loss: float
    risk_pct: float
    within_limit: bool


# ---------------------------------------------------------------------------
# trades
# ---------------------------------------------------------------------------
class TradeStageResponse(ApiModel):
    stage_key: str
    sequence: int
    r_multiple: float
    target_price: float
    planned_volume: float
    executed_volume: float
    sl_action: str
    sl_after: float | None
    planned_profit: float
    realised_pl: float
    status: str
    attempts: int
    note: str
    executed_at: datetime | None


class TradeResponse(ApiModel):
    id: int
    mt5_account_id: int
    symbol: str
    side: Side
    order_kind: str
    status: str

    entry_price: float
    requested_entry: float
    initial_stop: float
    current_stop: float
    risk_distance: float
    initial_volume: float
    remaining_volume: float
    ladder_preset: str

    capital_at_entry: float
    planned_risk: float
    planned_risk_pct: float
    planned_profit: float
    account_currency: str

    position_ticket: int | None
    realised_pl: float
    close_reason: str
    comment: str
    last_error: str

    opened_at: datetime | None
    closed_at: datetime | None
    created_at: datetime

    stages: list[TradeStageResponse] = []


class TradeDetailResponse(TradeResponse):
    plan: dict[str, Any] = {}
    rules: dict[str, Any] = {}
    events: list[TradeEventResponse] = []


class SubmissionResponse(ApiModel):
    """Result of submitting a trade.

    A rules rejection is a normal outcome, not an HTTP error: the response is
    ``200`` with ``approved=false`` and the full plan plus every rule check, so
    the client can show the user precisely why the entry was refused.
    """

    approved: bool
    executed: bool
    message: str
    plan: TradePlanResponse
    rules: RulesReportResponse
    trade: TradeResponse | None = None
    fill_plan: TradePlanResponse | None = None


class CloseTradeRequest(BaseModel):
    volume: float | None = Field(default=None, gt=0)


class TradeActionResponse(ApiModel):
    trade_id: int
    changed: bool
    closed: bool
    actions: list[str]
    error: str = ""
    trade: TradeResponse | None = None


# ---------------------------------------------------------------------------
# positions
# ---------------------------------------------------------------------------
class PositionResponse(ApiModel):
    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float
    swap: float
    commission: float
    magic: int
    comment: str
    opened_at: datetime | None


class PositionRow(ApiModel):
    position: PositionResponse
    managed: bool
    trade: TradeResponse | None = None


class PositionsOverviewResponse(ApiModel):
    account: AccountSnapshotResponse
    rows: list[PositionRow]
    orphaned_trades: list[TradeResponse]
    risk_on: float


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------
class TradeEventResponse(ApiModel):
    id: int
    trade_id: int | None
    event_type: str
    message: str
    payload: dict[str, Any]
    created_at: datetime


class DecisionResponse(ApiModel):
    id: int
    trade_id: int | None
    symbol: str
    side: Side
    approved: bool
    executed: bool
    volume: float
    entry_price: float
    stop_loss: float
    max_loss: float
    risk_pct: float
    expected_profit: float
    reward_risk: float
    violation_codes: str
    summary: str
    created_at: datetime


class DecisionDetailResponse(DecisionResponse):
    plan: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []


class PerformanceResponse(ApiModel):
    window_days: int
    closed_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    net_pl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    average_win: float
    average_loss: float
    decisions_approved: int
    decisions_rejected: int
    rule_adherence_pct: float
    top_rejections: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# system
# ---------------------------------------------------------------------------
class HealthResponse(ApiModel):
    status: str
    app: str
    version: str
    environment: str
    mt5_gateway: str
    mt5_stats: dict[str, Any]
    monitor_running: bool
    server_time: datetime


TradeDetailResponse.model_rebuild()
