"""Password hashing and bearer tokens.

Passwords use PBKDF2-HMAC-SHA256 from the standard library with a 16-byte random
salt and 600,000 iterations (the OWASP 2023 recommendation for this KDF).  The
stored value is self-describing::

    pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

so the iteration count can be raised later and old hashes still verify, then be
upgraded transparently on the next successful login.

If you deploy this at scale, Argon2id is the better KDF; it is omitted here only
to keep the dependency set small.  See ``docs/09-security.md``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from ..config import Settings, settings as default_settings
from ..errors import AuthError

ALGORITHM_TAG = "pbkdf2_sha256"
SALT_BYTES = 16
HASH_BYTES = 32
MIN_PASSWORD_LENGTH = 10


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------
def hash_password(password: str, *, iterations: int | None = None) -> str:
    rounds = iterations or default_settings.password_hash_iterations
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds, dklen=HASH_BYTES)
    return "$".join(
        [
            ALGORITHM_TAG,
            str(rounds),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification.  Returns ``False`` on any malformed input."""
    try:
        tag, rounds_raw, salt_b64, hash_b64 = stored.split("$")
        if tag != ALGORITHM_TAG:
            return False
        rounds = int(rounds_raw)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, rounds, dklen=len(expected)
    )
    return hmac.compare_digest(candidate, expected)


def needs_rehash(stored: str, *, iterations: int | None = None) -> bool:
    """True when a verified hash uses fewer rounds than currently configured."""
    target = iterations or default_settings.password_hash_iterations
    try:
        tag, rounds_raw, _, _ = stored.split("$")
    except ValueError:
        return True
    return tag != ALGORITHM_TAG or int(rounds_raw) < target


def validate_password_strength(password: str) -> None:
    """Reject obviously weak passwords at registration time."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
            code="weak_password",
        )
    if password.lower() in {"password12", "1234567890", "qwertyuiop", "trading123"}:
        raise AuthError("That password is too common. Choose another.", code="weak_password")


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------
def create_access_token(
    subject: str | int,
    *,
    settings: Settings | None = None,
    expires_minutes: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    """Return ``(token, expiry)``."""
    cfg = settings or default_settings
    ttl = expires_minutes if expires_minutes is not None else cfg.access_token_ttl_minutes
    now = datetime.now(UTC)
    expiry = now + timedelta(minutes=ttl)

    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expiry.timestamp()),
        "jti": secrets.token_urlsafe(12),
        "typ": "access",
    }
    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(payload, cfg.resolved_jwt_secret(), algorithm=cfg.jwt_algorithm)
    return token, expiry


def decode_access_token(token: str, *, settings: Settings | None = None) -> dict[str, Any]:
    cfg = settings or default_settings
    try:
        payload = jwt.decode(
            token,
            cfg.resolved_jwt_secret(),
            algorithms=[cfg.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Your session has expired. Sign in again.", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid authentication token.", code="token_invalid") from exc

    if payload.get("typ") != "access":
        raise AuthError("Unexpected token type.", code="token_invalid")
    return payload
