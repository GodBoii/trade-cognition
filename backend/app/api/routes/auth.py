"""Registration, login and the current user."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from ...config import settings
from ...core.security import create_access_token
from ...errors import ForbiddenError
from ...services import users as users_service
from .. import schemas
from ..deps import CurrentUser, DbSession

router = APIRouter(prefix="/auth", tags=["auth"])


def _require_legacy_auth() -> None:
    if settings.supabase_auth_enabled:
        raise ForbiddenError(
            "Password authentication is managed by Supabase. Use the frontend sign-in flow.",
            code="supabase_auth_required",
        )


def _token_for(user) -> schemas.TokenResponse:
    token, expiry = create_access_token(user.id)
    return schemas.TokenResponse(
        access_token=token,
        expires_at=expiry,
        user=schemas.UserResponse.model_validate(user),
    )


@router.post("/register", response_model=schemas.TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: schemas.RegisterRequest, session: DbSession) -> schemas.TokenResponse:
    """Create a platform account and return an access token.

    A default risk profile (0.02 lots per 1,000, 2% maximum risk) is created
    alongside the user, so the rules are active from the first trade.
    """
    _require_legacy_auth()
    user = users_service.register(
        session,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
    )
    session.commit()
    return _token_for(user)


@router.post("/login", response_model=schemas.TokenResponse)
async def login(payload: schemas.LoginRequest, session: DbSession) -> schemas.TokenResponse:
    _require_legacy_auth()
    user = users_service.authenticate(
        session, email=payload.email, password=payload.password
    )
    session.commit()
    return _token_for(user)


@router.get("/me", response_model=schemas.UserResponse)
async def me(user: CurrentUser) -> schemas.UserResponse:
    return schemas.UserResponse.model_validate(user)


@router.post(
    "/password", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def change_password(
    payload: schemas.ChangePasswordRequest, user: CurrentUser, session: DbSession
) -> Response:
    _require_legacy_auth()
    users_service.change_password(
        session, user, current=payload.current_password, new=payload.new_password
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
