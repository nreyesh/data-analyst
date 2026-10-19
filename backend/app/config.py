"""Configuration and runtime settings for backend service."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Export .env variables to os.environ
load_dotenv()


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
    output_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "data" / "output"
    )
    database_filename: str = Field(
        default="expenses.db", validation_alias="DATABASE_FILENAME"
    )

    def get_pending_dir(self, doc_type: str = "general") -> Path:
        """Return output pending spool directory for a document domain ('general' or 'unit')."""
        return self.output_dir / doc_type / "pending"

    def get_synced_dir(self, doc_type: str = "general") -> Path:
        """Return output synced archive directory for a document domain ('general' or 'unit')."""
        return self.output_dir / doc_type / "synced"

    def get_failed_dir(self, doc_type: str = "general") -> Path:
        """Return output failed dead-letter directory for a document domain ('general' or 'unit')."""
        return self.output_dir / doc_type / "failed"

    @property
    def pending_dir(self) -> Path:
        """Directory staging validated general reports awaiting database sync."""
        return self.get_pending_dir("general")

    @property
    def synced_dir(self) -> Path:
        """Directory archiving general reports successfully committed to the database."""
        return self.get_synced_dir("general")

    @property
    def failed_dir(self) -> Path:
        """Dead-letter directory isolating general reports rejected by the backend."""
        return self.get_failed_dir("general")

    @property
    def unit_pending_dir(self) -> Path:
        """Directory staging validated unit bills awaiting database sync."""
        return self.get_pending_dir("unit")

    @property
    def unit_synced_dir(self) -> Path:
        """Directory archiving unit bills successfully committed to the database."""
        return self.get_synced_dir("unit")

    @property
    def unit_failed_dir(self) -> Path:
        """Dead-letter directory isolating unit bills rejected by the backend."""
        return self.get_failed_dir("unit")

    @property
    def database_path(self) -> Path:
        """Full resolved path to SQLite database file."""
        return self.storage_dir / self.database_filename

    @property
    def database_url(self) -> str:
        """SQLAlchemy SQLite database connection URL."""
        return f"sqlite:///{self.database_path.resolve()}"

    def ensure_directories(self) -> None:
        """Ensure storage and spool directories exist for both general and unit domains."""
        for path in (
            self.storage_dir,
            self.output_dir,
            self.pending_dir,
            self.synced_dir,
            self.failed_dir,
            self.unit_pending_dir,
            self.unit_synced_dir,
            self.unit_failed_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton application settings."""
    return Settings()
