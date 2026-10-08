"""Main FastAPI application entrypoint with lifespan events and routes."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from backend.app.config import get_settings
from backend.app.database import init_db
from backend.app.routes.reports import router as reports_router

logger = logging.getLogger("backend.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for application startup and shutdown."""
    settings = get_settings()
    logger.info(f"Starting backend service... Database: {settings.database_path}")
    # Initialize SQLite database and tables
    init_db()
    logger.info("Database initialized successfully.")
    yield
    logger.info("Shutting down backend service.")


def create_app() -> FastAPI:
    """Application factory for FastAPI service."""
    settings = get_settings()

    app = FastAPI(
        title="Expense Reports Storage API",
        version="1.0.0",
        description="Persistent backend storage and ingestion API for Chilean condominium expense reports.",
        lifespan=lifespan,
    )

    # Health check endpoint for service monitoring and agent verification
    @app.get(
        "/health",
        tags=["system"],
        summary="Service health check",
        status_code=status.HTTP_200_OK,
    )
    def health_check() -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "healthy",
                "database": "connected",
                "database_path": str(settings.database_path),
            },
        )

    # Mount API routers
    app.include_router(reports_router, prefix="/api/v1")

    return app


app = create_app()


def run() -> None:
    """CLI runner to launch backend service programmatically."""
    settings = get_settings()
    uvicorn.run(
        "backend.app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    run()
