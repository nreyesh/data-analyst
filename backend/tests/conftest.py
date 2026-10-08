"""Test suite configuration and shared fixtures for backend tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Generator
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.database import Base, get_db
from backend.app.main import app


# Ensure SQLite test engine enforces foreign keys
@event.listens_for(Engine, "connect")
def configure_test_sqlite(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.close()


from sqlalchemy.pool import StaticPool


@pytest.fixture
def test_engine():
    """Create a temporary in-memory SQLite engine for isolated tests."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(test_engine) -> Generator[Session, None, None]:
    """Yield an isolated test session."""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """Yield a FastAPI TestClient with database session overridden."""
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def sample_payload_2026_9() -> dict:
    """Load sample JSON payload from data/output/2026-9.json."""
    fixture_path = Path(__file__).resolve().parent.parent.parent / "data" / "output" / "2026-9.json"
    with open(fixture_path, encoding="utf-8") as f:
        return json.load(f)
