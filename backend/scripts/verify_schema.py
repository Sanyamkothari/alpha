"""Quick Phase 0 schema verification (run after `alembic upgrade head`)."""

from __future__ import annotations

import sqlite3
import sys

from app.config import settings


def main() -> int:
    url = settings.effective_database_url
    if not url.startswith("sqlite"):
        print(f"DB is not sqlite ({url}); skipping sqlite-specific checks.")
        return 0
    path = url.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    tables = [
        r[0]
        for r in conn.execute(
            "select name from sqlite_master where type='table' "
            "and name not like 'sqlite_%' and name != 'alembic_version'"
        )
    ]
    print(f"tables in db: {len(tables)}")

    ddl = conn.execute("select sql from sqlite_master where name='brain_fetch_log'").fetchone()[0]
    has_get_check = "http_method = 'GET'" in ddl
    print(f"brain_fetch_log read-only GET check present: {has_get_check}")

    # The GET-only constraint must actually reject a non-GET insert.
    rejected = False
    try:
        conn.execute(
            "insert into brain_fetch_log (http_method, url, ok, created_at, updated_at) "
            "values ('POST', 'x', 1, '2026-01-01', '2026-01-01')"
        )
        conn.commit()
    except sqlite3.IntegrityError:
        rejected = True
    print(f"non-GET insert rejected by CHECK: {rejected}")

    # 16 business tables after the prune to the alpha-generation core
    # (STRATEGY.md §7); alembic_version makes 17.
    ok = len(tables) >= 16 and has_get_check and rejected
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
