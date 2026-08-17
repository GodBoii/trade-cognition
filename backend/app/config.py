"""Application configuration.

All settings are read from environment variables (prefix ``TC_``) or a ``.env``
file located at the repository root.  See ``.env.example`` for documentation of
every key.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TC_",
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    app_name: str = "Trade Cognition"
    env: Literal["development", "staging", "production", "test"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api"

    #: ``NoDecode`` is required: without it pydantic-settings tries to JSON-parse
    #: the environment value before any validator runs, so the natural
    #: ``TC_CORS_ORIGINS=http://a,http://b`` raises instead of being split.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # --- Database ----------------------------------------------------------
    database_url: str = "sqlite:///./data/trade_cognition.db"

    # --- Security ----------------------------------------------------------
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 720
    credential_encryption_key: str = ""
    allow_registration: bool = True
    password_hash_iterations: int = 600_000

    # --- Supabase Auth -----------------------------------------------------
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_auth_cache_seconds: int = 30

    # --- Supabase worker ---------------------------------------------------
    # The queue is deliberately opt-in. Worker RPCs use the public anon API key
    # plus a high-entropy, per-worker token whose hash is stored in Supabase.
    # The worker therefore does not need the broad service-role credential.
    supabase_queue_enabled: bool = False
    worker_token: str = ""
    worker_poll_interval_seconds: float = 2.0
    worker_heartbeat_interval_seconds: float = 15.0
    worker_claim_lease_seconds: int = 90
    worker_batch_size: int = 1
    worker_backoff_max_seconds: float = 60.0

    # --- MT5 ---------------------------------------------------------------
    mt5_gateway: Literal["real", "mock"] = "mock"
    mt5_terminal_path: str = ""
    mt5_timeout_ms: int = 60_000
    mt5_call_timeout_seconds: float = 30.0
    mt5_deviation_points: int = 20
    mt5_magic: int = 770_425

    # --- Monitor / streaming ----------------------------------------------
    monitor_enabled: bool = True
    monitor_interval_seconds: float = 2.0
    stream_interval_seconds: float = 2.0

    # --- Risk defaults -----------------------------------------------------
    default_lots_per_1000: float = 0.02
    default_max_risk_pct: float = 2.0
    default_capital_basis: Literal["balance", "equity", "fixed"] = "balance"
    default_ladder: str = "runner_1_2_3"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma separated list, a JSON array, or an actual list."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                try:
                    return json.loads(text)
                except ValueError:
                    pass
            return [origin.strip() for origin in text.split(",") if origin.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def supabase_auth_enabled(self) -> bool:
        return bool(self.supabase_url.strip() and self.supabase_anon_key.strip())

    @property
    def supabase_auth_user_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/user"

    @property
    def supabase_rest_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/rest/v1"

    def validate_auth_configuration(self) -> None:
        if self.is_production and not self.supabase_auth_enabled:
            raise RuntimeError(
                "TC_SUPABASE_URL and TC_SUPABASE_ANON_KEY are required in production."
            )

    def validate_supabase_queue_configuration(self) -> None:
        """Fail closed when the privileged queue worker is misconfigured."""
        if not self.supabase_queue_enabled:
            return
        missing: list[str] = []
        if not self.supabase_url.strip():
            missing.append("TC_SUPABASE_URL")
        if not self.supabase_anon_key.strip():
            missing.append("TC_SUPABASE_ANON_KEY")
        if not self.worker_token.strip():
            missing.append("TC_WORKER_TOKEN")
        if missing:
            raise RuntimeError(
                "Supabase queue worker is enabled but required settings are missing: "
                + ", ".join(missing)
            )
        if not 1 <= self.worker_batch_size <= 50:
            raise RuntimeError("TC_WORKER_BATCH_SIZE must be between 1 and 50.")
        if not 30 <= self.worker_claim_lease_seconds <= 900:
            raise RuntimeError(
                "TC_WORKER_CLAIM_LEASE_SECONDS must be between 30 and 900."
            )

    @property
    def sqlite_path(self) -> Path | None:
        """Filesystem path of the SQLite database, if SQLite is in use."""
        if not self.database_url.startswith("sqlite"):
            return None
        raw = self.database_url.split("///", 1)[-1]
        path = Path(raw)
        return path if path.is_absolute() else (BACKEND_DIR / raw).resolve()

    def resolved_jwt_secret(self) -> str:
        """Return the JWT secret, refusing to run insecurely in production."""
        placeholder = not self.jwt_secret or self.jwt_secret.startswith("CHANGE_ME")
        if placeholder:
            if self.is_production:
                raise RuntimeError(
                    "TC_JWT_SECRET must be set to a strong random value in production."
                )
            # Ephemeral development secret: tokens die with the process, which is
            # the safe default for a machine without configuration.
            return _EPHEMERAL_JWT_SECRET
        return self.jwt_secret


_EPHEMERAL_JWT_SECRET = secrets.token_urlsafe(64)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
