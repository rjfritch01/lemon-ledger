from fastapi import FastAPI

from lemon_ledger.api.health import router as health_router


def create_app() -> FastAPI:
    """Application factory — mount routers here as the project grows."""
    app = FastAPI(title="Lemon Ledger", version="0.1.0")
    app.include_router(health_router)
    return app
