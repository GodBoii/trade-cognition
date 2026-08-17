"""User registration, authentication and risk-profile persistence."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.security import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from ..core.supabase_auth import SupabaseIdentity
from ..db.models import RiskProfileRow, User
from ..domain.profile import RiskProfile
from ..errors import AuthError, ConflictError, ForbiddenError, ValidationError
from ..logging_conf import get_logger

log = get_logger(__name__)


def normalise_email(email: str) -> str:
    return email.strip().lower()


def get_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == normalise_email(email)))


def get_by_id(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def get_by_supabase_id(session: Session, supabase_user_id: str) -> User | None:
    return session.scalar(select(User).where(User.supabase_user_id == supabase_user_id))


def provision_from_supabase(session: Session, identity: SupabaseIdentity) -> User:
    """Resolve or create the local trading record for a verified Auth user.

    Trading data remains in the backend database because it is tightly coupled
    to the Dockerized MT5 worker. The immutable Supabase UUID is the identity
    bridge; matching an existing email once preserves pre-migration data.
    """
    user = get_by_supabase_id(session, identity.id)
    if user is None:
        user = get_by_email(session, identity.email)
        if user is not None and user.supabase_user_id not in {None, identity.id}:
            raise AuthError("This email is linked to another account.", code="identity_conflict")

    metadata = identity.metadata
    display_name = str(
        metadata.get("full_name")
        or metadata.get("name")
        or metadata.get("user_name")
        or identity.email.split("@", 1)[0]
    ).strip()[:120]

    if user is None:
        user = User(
            email=identity.email,
            supabase_user_id=identity.id,
            # Supabase owns password credentials. A sentinel keeps the legacy
            # non-null column compatible with existing SQLite installations.
            password_hash="supabase_managed",
            display_name=display_name,
        )
        session.add(user)
        session.flush()
        ensure_profile(session, user)
        log.info("Provisioned Supabase user %s (%s)", user.id, user.email)
    else:
        user.supabase_user_id = identity.id
        user.email = identity.email
        if not user.display_name:
            user.display_name = display_name

    user.last_login_at = datetime.now(UTC)
    session.flush()
    if not user.is_active:
        raise ForbiddenError("This account has been disabled.", code="account_disabled")
    return user


def register(
    session: Session, *, email: str, password: str, display_name: str = ""
) -> User:
    if not settings.allow_registration:
        raise ForbiddenError(
            "Self-service registration is disabled on this deployment.",
            code="registration_disabled",
        )

    address = normalise_email(email)
    if "@" not in address or "." not in address.split("@")[-1]:
        raise AuthError("Enter a valid email address.", code="invalid_email")
    if get_by_email(session, address) is not None:
        raise ConflictError("An account with that email already exists.", code="email_taken")

    validate_password_strength(password)

    user = User(
        email=address,
        password_hash=hash_password(password),
        display_name=display_name.strip() or address.split("@")[0],
    )
    session.add(user)
    session.flush()

    ensure_profile(session, user)
    session.flush()
    log.info("Registered user %s (%s)", user.id, user.email)
    return user


def authenticate(session: Session, *, email: str, password: str) -> User:
    user = get_by_email(session, email)
    # Same message either way so the endpoint cannot be used to enumerate users.
    invalid = AuthError("Email or password is incorrect.", code="invalid_credentials")

    if user is None or not verify_password(password, user.password_hash):
        raise invalid
    if not user.is_active:
        raise ForbiddenError("This account has been disabled.", code="account_disabled")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    session.flush()
    return user


def change_password(session: Session, user: User, *, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        raise AuthError("Current password is incorrect.", code="invalid_credentials")
    validate_password_strength(new)
    user.password_hash = hash_password(new)
    session.flush()


# ---------------------------------------------------------------------------
# risk profile
# ---------------------------------------------------------------------------
def ensure_profile(session: Session, user: User) -> RiskProfileRow:
    """Return the user's profile row, seeding defaults from settings if absent."""
    row = session.scalar(select(RiskProfileRow).where(RiskProfileRow.user_id == user.id))
    if row is not None:
        return row

    row = RiskProfileRow(
        user_id=user.id,
        lots_per_1000=settings.default_lots_per_1000,
        max_risk_pct=settings.default_max_risk_pct,
        capital_basis=settings.default_capital_basis,
        ladder_preset=settings.default_ladder,
    )
    session.add(row)
    session.flush()
    return row


def get_profile(session: Session, user: User) -> RiskProfile:
    return ensure_profile(session, user).to_domain()


def update_profile(session: Session, user: User, profile: RiskProfile) -> RiskProfile:
    _validate_profile(profile)
    row = ensure_profile(session, user)
    row.apply(profile)
    session.flush()
    log.info(
        "User %s updated risk profile: %.4f lots/1000, %.2f%% max risk, ladder %s",
        user.id,
        profile.lots_per_1000,
        profile.max_risk_pct,
        profile.ladder_preset.value,
    )
    return row.to_domain()


#: Five times the house standard of 0.02.  Beyond this the allocation is not a
#: position-sizing rule any more, it is a bet on the stop never being reached.
#: Rule 3 still caps monetary risk, so this is a secondary sanity guard.
MAX_LOTS_PER_1000 = 0.10
#: Above this a single stopped-out trade is an account event, not a trade.
MAX_RISK_PCT = 20.0


def _validate_profile(profile: RiskProfile) -> None:
    """Guard against configurations that would defeat the platform's purpose."""
    if profile.lots_per_1000 <= 0:
        raise ValidationError(
            "Lots per 1,000 of capital must be greater than zero.", code="invalid_profile"
        )
    if profile.lots_per_1000 > MAX_LOTS_PER_1000:
        raise ValidationError(
            f"{profile.lots_per_1000:g} lots per 1,000 of capital is extreme - the house standard "
            f"is 0.02 and this platform will not save an allocation above {MAX_LOTS_PER_1000:g}. "
            f"With that sizing the 2% risk ceiling would force an unworkably tight stop on most "
            f"symbols.",
            code="invalid_profile",
            details={"submitted": profile.lots_per_1000, "maximum": MAX_LOTS_PER_1000},
        )
    if not 0 < profile.max_risk_pct <= MAX_RISK_PCT:
        raise ValidationError(
            f"Maximum risk per trade must be between 0 and {MAX_RISK_PCT:g} percent of capital.",
            code="invalid_profile",
        )
    if profile.capital_basis.value == "fixed" and profile.fixed_capital <= 0:
        raise ValidationError(
            "A fixed capital basis needs a positive capital figure.", code="invalid_profile"
        )
    if not 0 <= profile.margin_utilisation_cap_pct <= 100:
        raise ValidationError(
            "The margin utilisation cap must be between 0 and 100 percent.",
            code="invalid_profile",
        )
    if profile.min_reward_risk < 0:
        raise ValidationError(
            "Minimum reward-to-risk cannot be negative.", code="invalid_profile"
        )
