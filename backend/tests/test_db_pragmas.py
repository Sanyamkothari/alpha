"""SQLite must be configured for concurrent use.

This process runs uvicorn's request handlers, a background job thread writing
simulation results, and a progress poller reading counts — all against one file.
Without these pragmas that combination fails intermittently and unhelpfully:
SQLite's default journal mode takes an EXCLUSIVE lock for every write and its
default busy timeout is zero, so a concurrent read raises "database is locked"
rather than waiting a few milliseconds.

Pinned as a test because the failure only appears under load, which is exactly
when nobody is looking.
"""

from __future__ import annotations

import threading

from sqlalchemy import func, select, text

from app.db import session_scope
from app.models.alphas import Alpha


def _pragma(name: str):
    with session_scope() as db:
        return db.execute(text(f"PRAGMA {name}")).scalar()


def test_wal_mode_enabled() -> None:
    """Readers must not block on writers."""
    assert str(_pragma("journal_mode")).lower() == "wal"


def test_busy_timeout_is_not_zero() -> None:
    """A contended lock should wait, not fail instantly."""
    assert int(_pragma("busy_timeout")) >= 1000


def test_foreign_keys_enforced() -> None:
    """SQLite ignores FK constraints entirely without this."""
    assert int(_pragma("foreign_keys")) == 1


def test_concurrent_readers_and_writers_do_not_lock() -> None:
    """The real shape: several threads writing while others read."""
    from app.db import engine
    from app.models import Base
    Base.metadata.create_all(engine)

    errors: list[str] = []

    def writer() -> None:
        try:
            for i in range(15):
                with session_scope() as db:
                    alpha = db.execute(select(Alpha).limit(1)).scalars().first()
                    if alpha is not None:
                        alpha.comments = f"pragma-test {i}"
        except Exception as exc:  # noqa: BLE001 - the point is to record it
            errors.append(f"writer: {exc}")

    def reader() -> None:
        try:
            for _ in range(60):
                with session_scope() as db:
                    db.scalar(select(func.count(Alpha.id)))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"reader: {exc}")

    threads = [threading.Thread(target=writer) for _ in range(2)]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"lock contention under concurrent access: {errors[:3]}"
