"""Desktop application entry point for Alpha Research.

Handles environment initialization, programmatic database migrations,
automatic seed loading, FastAPI application hosting, and launching the user's
default web browser.
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config

from app.config import IS_FROZEN, USER_DATA_DIR, settings
from app.main import app
from app.seeds import seed_all

logger = logging.getLogger("alpha.desktop")


def get_migrations_dir() -> Path:
    """Resolve the location of Alembic migrations directory."""
    if IS_FROZEN:
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        candidate = bundle_dir / "migrations"
        if candidate.exists():
            return candidate
        return bundle_dir / "app" / "migrations"
    return Path(__file__).resolve().parents[1] / "migrations"


def ensure_user_directories() -> None:
    """Ensure user config and database directories exist."""
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("User data directory: %s", USER_DATA_DIR)


def run_database_migrations() -> None:
    """Run Alembic database migrations programmatically to head."""
    migrations_dir = get_migrations_dir()
    logger.info("Running database migrations from: %s", migrations_dir)

    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(migrations_dir))
    alembic_cfg.set_main_option("sqlalchemy.url", settings.effective_database_url)

    try:
        command.upgrade(alembic_cfg, "head")
        logger.info("Database schema upgraded to head successfully.")
    except Exception as exc:
        logger.warning("Alembic upgrade warning (will fallback if tables missing): %s", exc)
        from app.db.session import engine
        from app.models import Base

        Base.metadata.create_all(bind=engine)
        logger.info("Database tables initialized via SQLAlchemy Base.metadata.")


def run_seed_data() -> None:
    """Load default seed data (operators, lookups, fields) idempotently."""
    try:
        summary = seed_all.run()
        logger.info("Seeding completed: %s", summary)
    except Exception as exc:
        logger.error("Failed to seed initial data: %s", exc)


def find_available_port(default_port: int = 8000) -> int:
    """Find an open port, starting from default_port."""
    for port in range(default_port, default_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return default_port


def open_browser_delayed(url: str, delay_sec: float = 1.2) -> None:
    """Open default web browser after a brief server startup delay."""

    def _open():
        time.sleep(delay_sec)
        logger.info("Opening web browser at %s", url)
        webbrowser.open(url)

    threading.Thread(target=_open, daemon=True).start()


def main() -> None:
    """Main desktop application entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger.info("Starting Alpha Research Desktop Application...")

    ensure_user_directories()
    run_database_migrations()
    run_seed_data()

    port = find_available_port(8000)
    server_url = f"http://127.0.0.1:{port}"
    logger.info("Web interface ready at %s", server_url)

    open_browser_delayed(server_url)

    # Run Uvicorn server synchronously
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
