"""Typed messages crossing the Supabase worker RPC boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClaimedTradeCommand(BaseModel):
    """One row returned by ``tcq_claim_trade_commands``."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    user_id: UUID
    connection_id: UUID
    intent_id: UUID | None = None
    client_request_id: UUID
    command_type: Literal["submit_trade", "close_trade", "sync_trade", "refresh_account"]
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal[
        "pending", "claimed", "succeeded", "rejected", "failed", "cancelled", "expired"
    ] = "claimed"
    priority: int = 100
    available_at: datetime | None = None
    expires_at: datetime
    claimed_by: UUID
    claim_token: UUID
    claimed_at: datetime
    lease_expires_at: datetime
    attempts: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=5, ge=1)
    created_at: datetime | None = None


class WorkerResult(BaseModel):
    """Normalized final outcome returned by an injected command processor."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["succeeded", "rejected", "failed"]
    intent_status: Literal[
        "claimed",
        "validating",
        "rejected",
        "submitted",
        "open",
        "scaling",
        "closed",
        "failed",
        "cancelled",
    ] | None = None
    message: str = ""
    error_code: str = ""
    retryable: bool = False
    result: dict[str, Any] = Field(default_factory=dict)
