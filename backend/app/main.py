"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app import __version__
from app.config import settings
from app.logging_config import configure_logging
from app.routers import alphas, fields, health, operators, system, ui, validate

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: check for interrupted campaigns
    try:
        import threading
        from app.services.campaign_runner import auto_resume_interrupted_campaigns
        # Run campaign resume in background thread to avoid blocking server boot
        threading.Thread(target=auto_resume_interrupted_campaigns, daemon=True).start()
    except Exception:
        pass
    yield


def create_app() -> FastAPI:
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title="WorldQuant Alpha Research Assistant",
        version=__version__,
        description=(
            "Local alpha-research tool for WorldQuant BRAIN. Simulations run on the "
            "user's own account; submissions are always a manual human decision."
        ),
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(system.router, prefix="/api")
    app.include_router(fields.router, prefix="/api")
    app.include_router(operators.router, prefix="/api")
    app.include_router(validate.router, prefix="/api")
    app.include_router(alphas.router, prefix="/api")
    app.include_router(ui.router, prefix="/api")

    # The UI is one self-contained file — no build step, so there is never a
    # stale bundle and `git clone && run` just works. Served last so it cannot
    # shadow an /api route.
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
