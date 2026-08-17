"""Engine and session management.

Sessions are synchronous.  The API handlers are ``async`` because they await MT5
work, and they perform their (short, local) database calls inline.  With SQLite
or a local PostgreSQL that is a sub-millisecond blocking call; if you move the
database somewhere with real latency, switch to SQLAlchemy's async engine.  This
tradeoff is recorded in ``docs/12-decisions.md`` (ADR-0005).
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ..config import Settings, settings as default_settings
from ..logging_conf import get_logger
from .base import Base

log = get_logger(__name__)

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _configure_sqlite(engine: Engine) -> None:
    """Pragmas that make SQLite behave sanely for a small trading app."""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        # WAL lets the monitor read while a request writes.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        # Honour ON DELETE CASCADE.
        cursor.execute("PRAGMA foreign_keys=ON")
        # Wait rather than fail when the monitor and a request collide.
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def build_engine(settings: Settings | None = None) -> Engine:
    cfg = settings or default_settings
    url = cfg.database_url
    kwargs: dict[str, object] = {"echo": False, "future": True}

    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url.endswith("sqlite://"):
            # One shared connection so in-memory tests see the same database.
            kwargs["poolclass"] = StaticPool
        else:
            path = cfg.sqlite_path
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
    else:
        kwargs["pool_pre_ping"] = True

    engine = create_engine(url, **kwargs)  # type: ignore[arg-type]
    if url.startswith("sqlite"):
        _configure_sqlite(engine)
    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine
    if _engine is None:
        _engine = build_engine(settings)
        log.info("Database engine ready (%s)", _engine.url.render_as_string(hide_password=True))
    return _engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(settings),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    return _SessionFactory


def init_db(settings: Settings | None = None) -> None:
    """Create any missing tables.

    Deliberately simple: this project has no migration tool wired up.  For
    schema evolution in production, add Alembic (see ``docs/07-data-model.md``).
    """
    # Import for the side effect of registering mappers.
    from . import models  # noqa: F401

    engine = get_engine(settings)
    Base.metadata.create_all(bind=engine)
    _upgrade_sqlite_users_table(engine)
    log.info("Database schema verified (%s tables)", len(Base.metadata.tables))


def _upgrade_sqlite_users_table(engine: Engine) -> None:
    """Add the Supabase identity bridge to pre-existing Docker volumes."""
    if engine.dialect.name != "sqlite" or not inspect(engine).has_table("users"):
        return
    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    if "supabase_user_id" in columns:
        return
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN supabase_user_id VARCHAR(36)"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_supabase_user_id "
            "ON users (supabase_user_id)"
        )
    log.info("Added users.supabase_user_id to the existing SQLite database")


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """Transactional scope for background work and scripts."""
    session = get_session_factory(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request, rolled back on error."""
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Test hook: drop the cached engine and session factory."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
