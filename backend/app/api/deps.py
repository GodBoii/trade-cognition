"""FastAPI dependencies: database session, current user, resolved MT5 account."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Query
from sqlalchemy.orm import Session

from ..core.security import decode_access_token
from ..core.supabase_auth import verify_supabase_access_token
from ..config import settings
from ..db.models import Mt5AccountRow, User
from ..db.session import get_session
from ..errors import AuthError, ForbiddenError
from ..services import accounts as accounts_service
from ..services import users as users_service

DbSession = Annotated[Session, Depends(get_session)]


def _token_from_header(authorization: str | None) -> str:
    if not authorization:
        raise AuthError("Authentication required. Send an 'Authorization: Bearer <token>' header.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Authorization header must use the Bearer scheme.")
    return token.strip()


def get_current_user(
    session: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    token = _token_from_header(authorization)
    if settings.supabase_auth_enabled:
        identity = verify_supabase_access_token(token)
        user = users_service.provision_from_supabase(session, identity)
        session.commit()
    else:
        # Legacy local auth is retained for tests and offline development only.
        payload = decode_access_token(token)
        try:
            user_id = int(payload["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthError("Malformed authentication token.", code="token_invalid") from exc
        user = users_service.get_by_id(session, user_id)

    if user is None:
        raise AuthError("The account for this token no longer exists.", code="token_invalid")
    if not user.is_active:
        raise ForbiddenError("This account has been disabled.", code="account_disabled")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_account(
    session: DbSession,
    user: CurrentUser,
    account_id: Annotated[int | None, Query(description="Defaults to the account marked default")] = None,
) -> Mt5AccountRow:
    return accounts_service.resolve_account(session, user, account_id)


ResolvedAccount = Annotated[Mt5AccountRow, Depends(get_account)]
