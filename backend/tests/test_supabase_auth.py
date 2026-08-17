from __future__ import annotations

import httpx

from app.config import Settings
from app.core.supabase_auth import (
    SupabaseIdentity,
    clear_supabase_auth_cache,
    verify_supabase_access_token,
)
from app.services import users as users_service


def test_verifies_access_token_with_supabase_auth_server(monkeypatch):
    captured: dict[str, object] = {}

    def fake_get(url, *, headers, timeout):
        captured.update(url=url, headers=headers, timeout=timeout)
        return httpx.Response(
            200,
            json={
                "id": "f95578df-bd97-43b4-ac90-72890c555fa7",
                "email": "Trader@Example.com",
                "user_metadata": {"full_name": "Supabase Trader", "phone": "+919876543210"},
            },
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    clear_supabase_auth_cache()
    settings = Settings(
        _env_file=None,
        supabase_url="https://project.supabase.co",
        supabase_anon_key="public-anon-key",
    )

    identity = verify_supabase_access_token("opaque-test-token", settings=settings)

    assert identity.email == "trader@example.com"
    assert identity.metadata["phone"] == "+919876543210"
    assert captured["url"] == "https://project.supabase.co/auth/v1/user"
    assert captured["headers"] == {
        "apikey": "public-anon-key",
        "Authorization": "Bearer opaque-test-token",
        "Accept": "application/json",
    }


def test_provisions_local_trading_user_and_profile(db):
    identity = SupabaseIdentity(
        id="f95578df-bd97-43b4-ac90-72890c555fa7",
        email="new@example.com",
        metadata={"full_name": "New Trader"},
    )

    user = users_service.provision_from_supabase(db, identity)
    db.commit()

    assert user.supabase_user_id == identity.id
    assert user.display_name == "New Trader"
    assert user.password_hash == "supabase_managed"
    assert users_service.get_profile(db, user).max_risk_pct == 2.0
    assert users_service.provision_from_supabase(db, identity).id == user.id
