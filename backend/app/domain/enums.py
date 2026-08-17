"""Enumerations shared across the domain, persistence and API layers."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """``str`` backed enum that serialises to its value."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class Side(StrEnum):
    """Direction of a position."""

    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        """+1 for long, -1 for short.  Used to make price math direction free."""
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderKind(StrEnum):
    """How the entry is submitted."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class CapitalBasis(StrEnum):
    """Which account figure counts as "trading capital" for the rules."""

    BALANCE = "balance"
    EQUITY = "equity"
    FIXED = "fixed"


class LotRuleMode(StrEnum):
    """How strictly Rule 2 (fixed lot allocation) is applied."""

    #: The submitted volume must equal the prescribed volume.
    STRICT = "strict"
    #: The prescribed volume is a ceiling; smaller volumes are allowed.
    MAX = "max"


class LadderPreset(StrEnum):
    """Named profit-taking ladders."""

    #: Literal reading of the house rules: 50% out at 1R, remaining 50% at 2R.
    STANDARD_1_2_3 = "standard_1_2_3"
    #: 50% at 1R, 25% at 2R, final 25% runs to 3R.
    RUNNER_1_2_3 = "runner_1_2_3"


class SlAction(StrEnum):
    """Stop-loss management action attached to a ladder stage."""

    NONE = "none"
    #: Halve the original stop distance measured from the entry price.
    HALVE_ORIGINAL_DISTANCE = "halve_original_distance"
    #: Move the stop to the entry price.
    BREAKEVEN = "breakeven"
    #: Move the stop to the first target (TP1) price.
    MOVE_TO_TP1 = "move_to_tp1"
    #: Move the stop to the target of the preceding stage.
    MOVE_TO_PREVIOUS_TARGET = "move_to_previous_target"


class TradeStatus(StrEnum):
    """Lifecycle of a managed trade."""

    #: Validated and accepted, order submitted, fill not yet confirmed.
    PENDING = "pending"
    #: Position live, no ladder stage executed yet.
    OPEN = "open"
    #: At least one ladder stage executed, volume remains.
    SCALING = "scaling"
    #: Fully closed (ladder complete, stop hit, or manual close).
    CLOSED = "closed"
    #: Rejected before reaching the broker.
    REJECTED = "rejected"
    #: Submitted but the broker refused it, or management failed irrecoverably.
    ERROR = "error"

    @classmethod
    def active(cls) -> tuple[TradeStatus, ...]:
        return (cls.PENDING, cls.OPEN, cls.SCALING)


class StageStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    SKIPPED = "skipped"
    FAILED = "failed"


class Severity(StrEnum):
    BLOCK = "block"
    WARN = "warn"
    INFO = "info"


class EventType(StrEnum):
    VALIDATED = "validated"
    REJECTED = "rejected"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_FILLED = "order_filled"
    ORDER_FAILED = "order_failed"
    STAGE_TRIGGERED = "stage_triggered"
    PARTIAL_CLOSE = "partial_close"
    SL_MODIFIED = "sl_modified"
    TP_MODIFIED = "tp_modified"
    POSITION_CLOSED = "position_closed"
    STOP_HIT = "stop_hit"
    MANUAL_CLOSE = "manual_close"
    SYNC = "sync"
    ERROR = "error"


class CloseReason(StrEnum):
    LADDER_COMPLETE = "ladder_complete"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    MANUAL = "manual"
    EXTERNAL = "external"
    UNKNOWN = "unknown"
