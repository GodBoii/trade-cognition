"""MT5 account connection endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from ...services import accounts as accounts_service
from ...services import users as users_service
from .. import schemas, serializers
from ..deps import CurrentUser, DbSession, ResolvedAccount

router = APIRouter(prefix="/mt5", tags=["mt5 accounts"])


@router.get("/accounts", response_model=list[schemas.Mt5AccountResponse])
async def list_accounts(user: CurrentUser, session: DbSession):
    return [
        schemas.Mt5AccountResponse.model_validate(a)
        for a in accounts_service.list_accounts(session, user)
    ]


@router.post(
    "/accounts",
    response_model=schemas.AccountStateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def connect_account(
    payload: schemas.ConnectAccountRequest, user: CurrentUser, session: DbSession
):
    """Connect an MT5 account.

    The credentials are verified against the terminal **before** anything is
    stored, so a saved account is always one that has authenticated at least
    once.  The broker password is encrypted at rest and never returned.
    """
    account, snapshot = await accounts_service.verify_and_store(
        session,
        user,
        login=payload.login,
        password=payload.password,
        server=payload.server,
        label=payload.label,
        terminal_path=payload.terminal_path,
        make_default=payload.make_default,
    )
    profile = users_service.get_profile(session, user)
    session.commit()
    return serializers.account_state(account, snapshot, profile)


@router.get("/accounts/state", response_model=schemas.AccountStateResponse)
async def account_state(user: CurrentUser, session: DbSession, account: ResolvedAccount):
    """Live account figures plus the capital the rules will use."""
    snapshot = await accounts_service.refresh(session, account)
    profile = users_service.get_profile(session, user)
    session.commit()
    return serializers.account_state(account, snapshot, profile)


@router.post("/accounts/{account_id}/default", response_model=schemas.Mt5AccountResponse)
async def set_default(account_id: int, user: CurrentUser, session: DbSession):
    account = accounts_service.set_default(session, user, account_id)
    session.commit()
    return schemas.Mt5AccountResponse.model_validate(account)


@router.delete(
    "/accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def disconnect(account_id: int, user: CurrentUser, session: DbSession) -> Response:
    """Disconnect an account.  Refused while it still has an active managed trade."""
    accounts_service.delete_account(session, user, account_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
