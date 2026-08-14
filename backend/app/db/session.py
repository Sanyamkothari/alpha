"""Engine + session management (dual-engine ready).

SQLite in dev (with foreign-keys enforced); a one-line URL change moves to
Postgres in Phase 7. ``get_db`` is the FastAPI dependency; ``session_scope`` is
a transactional context manager for scripts/seeds.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


def _make_engine() -> Engine:
    url = settings.effective_database_url
    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        # FastAPI may touch the session across threads with --reload.
        connect_args["check_same_thread"] = False
    return create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)


engine: Engine = _make_engine()


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Per-connection SQLite settings. All three matter under threads.

    This process is genuinely concurrent: uvicorn serves requests while a
    background job thread writes simulation results and a progress poller reads
    counts — all against one file.

    * ``foreign_keys=ON`` — SQLite ignores FKs entirely without it, which is how
      96 orphaned history rows once survived a delete.
    * ``journal_mode=WAL`` — the default (DELETE) takes an EXCLUSIVE lock for
      every write, so a 90-second batch writing results would intermittently
      fail every concurrent read with "database is locked". WAL lets readers
      proceed during a write. It is persistent, so it is set once and sticks.
    * ``busy_timeout`` — when a lock IS contended, wait for it instead of raising
      immediately. The default is 0: fail instantly. Five seconds is far longer
      than any write here takes.

    ``synchronous=NORMAL`` is the documented-safe pairing with WAL: durable
    against process crash (which is what matters), trading only durability
    against OS-level power loss for a large write speedup.
    """
    if not settings.is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: a request-scoped session (unit of work).

    Commits on a successful request and rolls back on any exception (including a
    raised ``HTTPException``, which is thrown back into this generator), so write
    endpoints persist atomically and error responses never leave partial writes.
    Read endpoints simply have nothing to commit.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and seed loaders."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
