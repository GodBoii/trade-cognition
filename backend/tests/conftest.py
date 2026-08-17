"""Test configuration.

Environment variables are set **before** ``app`` is imported so the settings
singleton picks up the test configuration: an in-memory database, the mock MT5
gateway, and the position monitor stopped (tests drive it explicitly).
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet

# Hard assignment, not setdefault: the suite must never inherit a developer's
# TC_DATABASE_URL or TC_MT5_GATEWAY and write to a real database or a live
# terminal.  Any TC_* variable left in the shell is overridden here.
os.environ.update(
    {
        "TC_ENV": "test",
        "TC_DATABASE_URL": "sqlite://",  # in-memory, one per test
        "TC_MT5_GATEWAY": "mock",
        "TC_MT5_TERMINAL_PATH": "",
        "TC_MONITOR_ENABLED": "false",  # tests drive the monitor explicitly
        "TC_ALLOW_REGISTRATION": "true",
        "TC_JWT_SECRET": "test-secret-not-for-production-use-only",
        "TC_CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "TC_LOG_LEVEL": "WARNING",
        "TC_SUPABASE_URL": "",
        "TC_SUPABASE_ANON_KEY": "",
        "TC_DEFAULT_LOTS_PER_1000": "0.02",
        "TC_DEFAULT_MAX_RISK_PCT": "2.0",
        "TC_DEFAULT_CAPITAL_BASIS": "balance",
        "TC_DEFAULT_LADDER": "standard_1_2_3",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import get_session_factory, init_db, reset_engine  # noqa: E402
from app.domain.market import AccountSnapshot, SymbolSpec, Tick  # noqa: E402
from app.main import app  # noqa: E402
from app.mt5 import mock as mock_module  # noqa: E402
from app.mt5.manager import Mt5Client  # noqa: E402

DEMO_LOGIN = 500123
DEMO_SERVER = "MockBroker-Demo"
DEMO_PASSWORD = "demo-password"
USER_EMAIL = "trader@example.com"
USER_PASSWORD = "disciplined-trading-1"


@pytest.fixture(autouse=True)
def clean_state():
    """Fresh database, fresh simulated broker, empty caches for every test.

    Every other fixture depends on this one explicitly, so the reset is
    guaranteed to happen before a client or broker is built rather than relying
    on pytest's autouse ordering.
    """
    reset_engine()
    init_db()

    mock_module.UNIVERSE.auto_drift = False
    mock_module.UNIVERSE.balance = 10_000.0
    mock_module.UNIVERSE.reset()
    Mt5Client.clear_caches()

    yield

    reset_engine()
    mock_module.UNIVERSE.reset()
    Mt5Client.clear_caches()


@pytest.fixture
def broker(clean_state):
    """The simulated broker for the demo account (prices are fully controlled)."""
    return mock_module.UNIVERSE.broker(DEMO_LOGIN, DEMO_SERVER)


@pytest.fixture
def db(clean_state):
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(clean_state):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_client(client, broker):
    """A registered, authenticated client with the demo MT5 account connected."""
    response = client.post(
        "/api/auth/register",
        json={"email": USER_EMAIL, "password": USER_PASSWORD, "display_name": "Test Trader"},
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})

    connected = client.post(
        "/api/mt5/accounts",
        json={
            "login": DEMO_LOGIN,
            "password": DEMO_PASSWORD,
            "server": DEMO_SERVER,
            "label": "Demo",
        },
    )
    assert connected.status_code == 201, connected.text
    return client


# ---------------------------------------------------------------------------
# domain fixtures (no broker involved)
# ---------------------------------------------------------------------------
@pytest.fixture
def eurusd() -> SymbolSpec:
    """5-digit FX pair: 1.00 lot moves 1.00 USD per point."""
    return SymbolSpec(
        name="EURUSD",
        digits=5,
        point=1e-5,
        tick_size=1e-5,
        tick_value_loss=1.0,
        tick_value_profit=1.0,
        contract_size=100_000,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )


@pytest.fixture
def xauusd() -> SymbolSpec:
    """Gold: 100 oz contract, 0.01 point, 50 point minimum stop distance."""
    return SymbolSpec(
        name="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value_loss=1.0,
        tick_value_profit=1.0,
        contract_size=100,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        stops_level_points=50,
    )


@pytest.fixture
def us500() -> SymbolSpec:
    """Index CFD on a 0.1 lot grid - exercises coarse volume steps."""
    return SymbolSpec(
        name="US500",
        digits=1,
        point=0.1,
        tick_size=0.1,
        tick_value_loss=0.1,
        tick_value_profit=0.1,
        contract_size=1,
        volume_min=0.1,
        volume_max=200.0,
        volume_step=0.1,
        stops_level_points=20,
    )


@pytest.fixture
def account() -> AccountSnapshot:
    return AccountSnapshot(
        login=DEMO_LOGIN,
        currency="USD",
        balance=10_000.0,
        equity=10_000.0,
        margin=0.0,
        margin_free=10_000.0,
        leverage=100,
    )


@pytest.fixture
def eurusd_tick() -> Tick:
    return Tick(symbol="EURUSD", bid=1.09500, ask=1.09512)
