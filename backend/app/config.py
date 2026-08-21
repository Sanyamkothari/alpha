"""Application configuration — single source of truth (pydantic-settings).

Reads from environment and the repo-root ``.env``. Used by the FastAPI app, the
LLM gateway, the seed loaders, and Alembic (so the DB URL is defined in exactly
one place).
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

IS_FROZEN: bool = getattr(sys, "frozen", False)

# ---- Well-known paths (independent of the current working directory) ----
if IS_FROZEN:
    BUNDLE_DIR: Path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    REPO_ROOT: Path = BUNDLE_DIR
    USER_DATA_DIR: Path = (
        Path(os.environ["ALPHA_DATA_DIR"])
        if "ALPHA_DATA_DIR" in os.environ
        else Path.home() / ".alpha_research"
    )
    DATABASE_DIR: Path = USER_DATA_DIR / "database"
    OPERATORS_DIR: Path = (
        BUNDLE_DIR / "operators"
        if (BUNDLE_DIR / "operators").exists()
        else BUNDLE_DIR / "app" / "operators"
    )
    FIELDS_DIR: Path = (
        BUNDLE_DIR / "fields" if (BUNDLE_DIR / "fields").exists() else BUNDLE_DIR / "app" / "fields"
    )
    TEMPLATES_DIR: Path = (
        BUNDLE_DIR / "templates"
        if (BUNDLE_DIR / "templates").exists()
        else BUNDLE_DIR / "app" / "templates"
    )
    ENV_FILE_PATH: Path = (
        USER_DATA_DIR / ".env" if (USER_DATA_DIR / ".env").exists() else REPO_ROOT / ".env"
    )
else:
    BACKEND_DIR = Path(__file__).resolve().parents[1]  # .../alpha/backend
    REPO_ROOT = BACKEND_DIR.parent  # .../alpha
    USER_DATA_DIR = (
        Path(os.environ["ALPHA_DATA_DIR"]) if "ALPHA_DATA_DIR" in os.environ else REPO_ROOT
    )
    DATABASE_DIR = USER_DATA_DIR / "database"
    OPERATORS_DIR = REPO_ROOT / "operators"
    FIELDS_DIR = REPO_ROOT / "fields"
    TEMPLATES_DIR = REPO_ROOT / "templates"
    ENV_FILE_PATH = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Typed application settings loaded from the environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    app_env: str = "dev"
    log_level: str = "INFO"
    log_json: bool = False
    auto_resume_campaigns: bool = False

    # ---- Database ----
    # Blank => default SQLite file under <repo>/database/wq.db (absolute, CWD-independent).
    database_url: str = ""

    # ---- LLM gateway ----
    llm_provider: str = "fake"  # "anthropic" | "openrouter" | "fake"
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    llm_output_cache: bool = True
    llm_prompt_cache: bool = True

    # Tier -> concrete model. Call sites name a *capability*, never a model, so
    # switching provider or model is this block plus LLM_PROVIDER — no code change.
    # Defaults are Claude; override all four in .env when using OpenRouter, since
    # a Claude model id sent to OpenRouter is a 404, not a fallback.
    llm_model_opus: str = "claude-opus-4-8"
    llm_model_sonnet: str = "claude-sonnet-4-6"
    llm_model_haiku: str = "claude-haiku-4-5"
    llm_model_reserve: str = "claude-fable-5"

    # ---- WorldQuant BRAIN ----
    # Credentials for the user's own account. Used by the catalog fetcher
    # (stage 1) and the simulation runner (stage 2). Submission is never
    # automated — see docs/DECISIONS.md.
    brain_email: str = ""
    brain_password: str = ""

    brain_api_base: str = "https://api.worldquantbrain.com"
    brain_default_region: str = "USA"
    brain_default_universe: str = "TOP3000"
    brain_default_delay: int = 1

    # Politeness knobs for every BRAIN call (conservative until the real limits
    # are observed — docs/BRAIN_API.md marks them UNVERIFIED).
    brain_max_concurrency: int = 3
    brain_poll_seconds: float = 10.0

    @property
    def effective_database_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            base = (
                Path(os.environ["ALPHA_DATA_DIR"])
                if "ALPHA_DATA_DIR" in os.environ
                else Path.home() / ".alpha_research"
            )
        else:
            base = (
                Path(os.environ["ALPHA_DATA_DIR"])
                if "ALPHA_DATA_DIR" in os.environ
                else Path(__file__).resolve().parents[2]
            )
        return base / "database"

    @property
    def effective_database_url(self) -> str:
        """Resolve the DB URL, defaulting to an absolute SQLite path."""
        if self.database_url:
            return self.database_url
        db_dir = self.effective_database_dir
        db_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(db_dir / 'wq.db').as_posix()}"

    @property
    def is_sqlite(self) -> bool:
        return self.effective_database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()


# Convenience module-level singleton.
settings: Settings = get_settings()
