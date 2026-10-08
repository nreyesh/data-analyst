"""Configuration and runtime settings for backend service."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the FastAPI backend and SQLite database."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server settings
    host: str = Field(default="0.0.0.0", validation_alias="BACKEND_HOST")
    port: int = Field(default=8000, validation_alias="BACKEND_PORT")
    debug: bool = Field(default=False, validation_alias="BACKEND_DEBUG")

    # Project directories and SQLite storage
    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent
    )
    storage_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "data" / "storage"
    )
    database_filename: str = Field(
        default="expenses.db", validation_alias="DATABASE_FILENAME"
    )

    @property
    def database_path(self) -> Path:
        """Full resolved path to SQLite database file."""
        return self.storage_dir / self.database_filename

    @property
    def database_url(self) -> str:
        """SQLAlchemy SQLite database connection URL."""
        return f"sqlite:///{self.database_path.resolve()}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton application settings."""
    return Settings()
