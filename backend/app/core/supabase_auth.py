"""Validate Supabase access tokens against the project's Auth server.

The project currently uses the legacy HS256 signing scheme. Supabase recommends
checking those tokens through ``GET /auth/v1/user`` instead of attempting local
verification without the project's JWT signing secret. Successful checks are
cached briefly to avoid a network round-trip on every trading API request.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from ..config import Settings, settings as default_settings
from ..errors import AuthError, ServiceUnavailableError


@dataclass(frozen=True, slots=True)
class SupabaseIdentity:
    id: str
    email: str
    metadata: dict[str, Any]


_cache: dict[str, tuple[float, SupabaseIdentity]] = {}
_cache_lock = threading.Lock()
_MAX_CACHE_ENTRIES = 2_048


def verify_supabase_access_token(
    token: str, *, settings: Settings | None = None
) -> SupabaseIdentity:
    """Return the verified Supabase identity represented by ``token``."""
    cfg = settings or default_settings
    if not cfg.supabase_auth_enabled:
        raise ServiceUnavailableError(
            "Supabase Auth is not configured on the API.", code="auth_not_configured"
        )

    cache_key = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

    try:
        response = httpx.get(
            cfg.supabase_auth_user_url,
            headers={
                "apikey": cfg.supabase_anon_key,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=5.0,
        )
    except httpx.RequestError as exc:
        raise ServiceUnavailableError(
            "Authentication service is temporarily unavailable.", code="auth_unavailable"
        ) from exc

    if response.status_code in {401, 403}:
        raise AuthError("Invalid or expired authentication token.", code="token_invalid")
    if response.status_code != 200:
        raise ServiceUnavailableError(
            "Authentication service returned an unexpected response.",
            code="auth_unavailable",
            details={"status": response.status_code},
        )

    try:
        payload = response.json()
        subject = str(payload["id"])
        email = str(payload["email"]).strip().lower()
        metadata = payload.get("user_metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError("Malformed identity returned by Supabase.", code="token_invalid") from exc

    if not subject or not email:
        raise AuthError("Supabase account is missing an email address.", code="email_required")

    identity = SupabaseIdentity(id=subject, email=email, metadata=metadata)
    ttl = max(0, cfg.supabase_auth_cache_seconds)
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
        token_expiry = float(claims.get("exp", now + ttl))
    except (jwt.PyJWTError, TypeError, ValueError):
        token_expiry = now + ttl
    cache_expiry = min(token_expiry, now + ttl)

    if cache_expiry > now:
        with _cache_lock:
            if len(_cache) >= _MAX_CACHE_ENTRIES:
                expired = [key for key, value in _cache.items() if value[0] <= now]
                for key in expired:
                    _cache.pop(key, None)
                if len(_cache) >= _MAX_CACHE_ENTRIES:
                    _cache.pop(next(iter(_cache)))
            _cache[cache_key] = (cache_expiry, identity)
    return identity


def clear_supabase_auth_cache() -> None:
    """Test hook and an escape hatch for operational key rotation."""
    with _cache_lock:
        _cache.clear()
