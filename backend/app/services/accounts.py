"""MT5 account connection management."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.crypto import get_cipher
from ..db.models import ManagedTrade, Mt5AccountRow, User
from ..domain.enums import TradeStatus
from ..domain.market import AccountSnapshot
from ..errors import ConflictError, NotFoundError
from ..logging_conf import get_logger
from ..mt5 import Mt5Client, Mt5Credentials, get_runtime

log = get_logger(__name__)


def credentials_for(account: Mt5AccountRow) -> Mt5Credentials:
    """Decrypt the stored password into transient credentials."""
    return Mt5Credentials(
        login=account.login,
        password=get_cipher().decrypt(account.password_encrypted),
        server=account.server,
        terminal_path=account.terminal_path or settings.mt5_terminal_path,
    )


def client_for(account: Mt5AccountRow) -> Mt5Client:
    return get_runtime().client(credentials_for(account))


def list_accounts(session: Session, user: User) -> list[Mt5AccountRow]:
    return list(
        session.scalars(
            select(Mt5AccountRow)
            .where(Mt5AccountRow.user_id == user.id)
            .order_by(Mt5AccountRow.is_default.desc(), Mt5AccountRow.id)
        )
    )


def get_account(session: Session, user: User, account_id: int) -> Mt5AccountRow:
    account = session.scalar(
        select(Mt5AccountRow).where(
            Mt5AccountRow.id == account_id, Mt5AccountRow.user_id == user.id
        )
    )
    if account is None:
        raise NotFoundError(f"MT5 account {account_id} is not connected to your profile.")
    return account


def resolve_account(session: Session, user: User, account_id: int | None = None) -> Mt5AccountRow:
    """Explicit account when given, otherwise the default/first enabled one."""
    if account_id is not None:
        return get_account(session, user, account_id)

    accounts = [a for a in list_accounts(session, user) if a.is_enabled]
    if not accounts:
        raise ConflictError(
            "No MT5 account is connected. Connect an account before trading.",
            code="no_mt5_account",
        )
    return accounts[0]


async def verify_and_store(
    session: Session,
    user: User,
    *,
    login: int,
    password: str,
    server: str,
    label: str = "",
    terminal_path: str = "",
    make_default: bool = True,
) -> tuple[Mt5AccountRow, AccountSnapshot]:
    """Authenticate against MT5 first, then persist.

    Credentials that do not work are never stored, so the account list only ever
    contains connections known to have succeeded at least once.
    """
    credentials = Mt5Credentials(
        login=int(login),
        password=password,
        server=server,
        terminal_path=terminal_path or settings.mt5_terminal_path,
    )
    snapshot = await get_runtime().client(credentials).verify()

    existing = session.scalar(
        select(Mt5AccountRow).where(
            Mt5AccountRow.user_id == user.id,
            Mt5AccountRow.login == int(login),
            Mt5AccountRow.server == server,
        )
    )
    account = existing or Mt5AccountRow(user_id=user.id, login=int(login), server=server)

    account.password_encrypted = get_cipher().encrypt(password)
    account.terminal_path = terminal_path or ""
    account.label = label or account.label or f"{snapshot.company or server} {login}"
    account.is_enabled = True
    account.last_error = ""
    _apply_snapshot(account, snapshot)

    if make_default or existing is None:
        for other in list_accounts(session, user):
            other.is_default = False
        account.is_default = True

    session.add(account)
    session.flush()
    log.info("Stored MT5 account %s (login %s) for user %s", account.id, login, user.id)
    return account, snapshot


async def refresh(session: Session, account: Mt5AccountRow) -> AccountSnapshot:
    """Re-read the account from the terminal and cache the headline figures."""
    try:
        snapshot = await client_for(account).account()
    except Exception as exc:
        account.last_error = str(exc)[:1000]
        session.flush()
        raise
    account.last_error = ""
    _apply_snapshot(account, snapshot)
    session.flush()
    return snapshot


def set_default(session: Session, user: User, account_id: int) -> Mt5AccountRow:
    target = get_account(session, user, account_id)
    for account in list_accounts(session, user):
        account.is_default = account.id == target.id
    session.flush()
    return target


def delete_account(session: Session, user: User, account_id: int) -> None:
    account = get_account(session, user, account_id)

    live = session.scalar(
        select(ManagedTrade).where(
            ManagedTrade.mt5_account_id == account.id,
            ManagedTrade.status.in_([s.value for s in TradeStatus.active()]),
        )
    )
    if live is not None:
        raise ConflictError(
            f"Account {account.login} still has an active managed trade on {live.symbol}. "
            f"Close it before disconnecting the account.",
            code="account_has_active_trades",
        )

    session.delete(account)
    session.flush()


def _apply_snapshot(account: Mt5AccountRow, snapshot: AccountSnapshot) -> None:
    account.currency = snapshot.currency or account.currency
    account.company = snapshot.company or account.company
    account.account_name = snapshot.name or account.account_name
    account.leverage = snapshot.leverage or account.leverage
    account.last_balance = snapshot.balance
    account.last_equity = snapshot.equity
    account.last_verified_at = datetime.now(UTC)
