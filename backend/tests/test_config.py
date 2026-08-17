"""Settings parsing.

Regression coverage for a failure that only appeared once the app was given a
real environment: pydantic-settings JSON-decodes complex fields from the
environment *before* validators run, so a comma separated `TC_CORS_ORIGINS`
crashed the process at import time.
"""

from __future__ import annotations

import pytest

from app.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "http://localhost:3000",
            ["http://localhost:3000"],
        ),
        (
            "http://localhost:3000,https://app.example.com",
            ["http://localhost:3000", "https://app.example.com"],
        ),
        (
            "  http://a.test , http://b.test ,, ",
            ["http://a.test", "http://b.test"],
        ),
        (
            '["http://a.test", "http://b.test"]',
            ["http://a.test", "http://b.test"],
        ),
    ],
)
def test_cors_origins_accepts_comma_separated_and_json(monkeypatch, raw, expected):
    monkeypatch.setenv("TC_CORS_ORIGINS", raw)
    settings = Settings(_env_file=None)
    assert settings.cors_origins == expected


def test_cors_origins_has_a_usable_default(monkeypatch):
    monkeypatch.delenv("TC_CORS_ORIGINS", raising=False)
    settings = Settings(_env_file=None)
    assert any("3000" in origin for origin in settings.cors_origins)


def test_environment_variables_drive_the_gateway_and_limits(monkeypatch):
    monkeypatch.setenv("TC_MT5_GATEWAY", "mock")
    monkeypatch.setenv("TC_DEFAULT_MAX_RISK_PCT", "1.5")
    monkeypatch.setenv("TC_LOG_LEVEL", "debug")
    settings = Settings(_env_file=None)

    assert settings.mt5_gateway == "mock"
    assert settings.default_max_risk_pct == 1.5
    assert settings.log_level == "DEBUG"  # normalised


def test_supabase_auth_requires_url_and_key(monkeypatch):
    monkeypatch.setenv("TC_SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("TC_SUPABASE_ANON_KEY", "publishable-key")
    settings = Settings(_env_file=None)

    assert settings.supabase_auth_enabled is True
    assert settings.supabase_auth_user_url == "https://project.supabase.co/auth/v1/user"

    monkeypatch.setenv("TC_SUPABASE_ANON_KEY", "")
    assert Settings(_env_file=None).supabase_auth_enabled is False


def test_production_requires_supabase_auth(monkeypatch):
    monkeypatch.setenv("TC_ENV", "production")
    monkeypatch.setenv("TC_SUPABASE_URL", "")
    monkeypatch.setenv("TC_SUPABASE_ANON_KEY", "")
    settings = Settings(_env_file=None)

    with pytest.raises(RuntimeError, match="TC_SUPABASE_URL"):
        settings.validate_auth_configuration()


def test_production_refuses_a_placeholder_jwt_secret(monkeypatch):
    monkeypatch.setenv("TC_ENV", "production")
    monkeypatch.setenv("TC_JWT_SECRET", "CHANGE_ME_generate_a_long_random_value")
    settings = Settings(_env_file=None)

    with pytest.raises(RuntimeError, match="TC_JWT_SECRET"):
        settings.resolved_jwt_secret()


def test_development_falls_back_to_an_ephemeral_secret(monkeypatch):
    monkeypatch.setenv("TC_ENV", "development")
    monkeypatch.delenv("TC_JWT_SECRET", raising=False)
    settings = Settings(_env_file=None)

    secret = settings.resolved_jwt_secret()
    assert secret and not secret.startswith("CHANGE_ME")


def test_sqlite_path_resolution(monkeypatch):
    monkeypatch.setenv("TC_DATABASE_URL", "sqlite:////data/trade_cognition.db")
    settings = Settings(_env_file=None)
    path = settings.sqlite_path
    assert path is not None
    assert path.as_posix().endswith("/data/trade_cognition.db")

    monkeypatch.setenv("TC_DATABASE_URL", "postgresql+psycopg://u:p@host/db")
    assert Settings(_env_file=None).sqlite_path is None
