"""Database connection, session management, and SQLite engine configuration."""

from __future__ import annotations

import logging
from collections.abc import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""
    pass


# Event listener to guarantee SQLite enforces foreign keys and uses WAL mode
@event.listens_for(Engine, "connect")
def configure_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """Enforce PRAGMA foreign_keys = ON and journal_mode = WAL on SQLite connections."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.close()


def get_engine(db_url: str | None = None) -> Engine:
    """Create or return SQLAlchemy Engine for SQLite."""
    settings = get_settings()
    url = db_url or settings.database_url
    # Ensure storage directory exists
    settings.storage_dir.mkdir(parents=True, exist_ok=True)

    return create_engine(
        url,
        connect_args={"check_same_thread": False},
        echo=settings.debug,
    )


# Default application engine and sessionmaker
engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency to yield database sessions per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_sqlite_columns(target_engine: Engine) -> None:
    """Idempotently add new columns to existing SQLite tables if upgrading in place."""
    with target_engine.begin() as conn:
        rep_cols = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(expense_reports)").fetchall()
        }
        if rep_cols:
            if "operational_total" not in rep_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE expense_reports ADD COLUMN operational_total INTEGER NOT NULL DEFAULT 0"
                )
            if "fondos_total" not in rep_cols:
                conn.exec_driver_sql(
                    "ALTER TABLE expense_reports ADD COLUMN fondos_total INTEGER NOT NULL DEFAULT 0"
                )

        sec_cols = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(expense_sections)").fetchall()
        }
        if sec_cols and "expense_type" not in sec_cols:
            conn.exec_driver_sql(
                "ALTER TABLE expense_sections ADD COLUMN expense_type VARCHAR(32) NOT NULL DEFAULT 'operational'"
            )


def init_db(target_engine: Engine | None = None) -> None:
    """Initialize database schema, tables, and indexes."""
    active_engine = target_engine or engine
    # Import models here to ensure they are registered with Base.metadata
    from backend.app import models  # noqa: F401

    logger.info("Initializing database tables...")
    Base.metadata.create_all(bind=active_engine)
    _ensure_sqlite_columns(active_engine)
    logger.info("Database initialized successfully.")
