"""Configuration and settings management for data_extractor."""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Export .env variables to os.environ for LangSmith / LangGraph tracing
load_dotenv()


class Settings(BaseSettings):
    """Application runtime configuration and secrets."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 1. Secrets (strictly loaded from environment or .env)
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
        description="Google Gemini API Key",
    )

    # 2. Operational App Settings
    default_model: str = Field(
        default="gemini-3.5-flash-lite",
        validation_alias="DEFAULT_MODEL",
        description="Default Gemini multimodal model name",
    )
    fallback_models: list[str] = Field(
        default_factory=lambda: [
            "gemini-3.1-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.7-flash",
            "gemini-3.8-flash",
        ],
        validation_alias="FALLBACK_MODELS",
        description="Ordered list of backup Gemini 3.1+ Flash models",
    )
    max_retries: int = Field(
        default=2,
        validation_alias="MAX_RETRIES",
        description="Maximum self-healing retry iterations",
    )

    def get_candidate_models(self, primary_override: str | None = None) -> list[str]:
        """Return deduplicated ordered list of primary and fallback models."""
        primary = primary_override or self.default_model
        candidates: list[str] = []
        for model_name in [primary, *self.fallback_models]:
            if model_name and model_name not in candidates:
                candidates.append(model_name)
        return candidates

    # 3. Path & Storage Boundaries
    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent
    )
    input_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "input"
    )
    output_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "output"
    )
    storage_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "storage"
    )
    prompts_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent / "prompts"
    )

    # 4. Backend Service URLs
    backend_base_url: str = Field(
        default="http://localhost:8000",
        validation_alias="BACKEND_BASE_URL",
        description="Base URL for the persistence backend service",
    )

    @property
    def input_processed_dir(self) -> Path:
        """Directory archiving raw PDFs that passed extraction and validation."""
        return self.input_dir / "processed"

    @property
    def input_failed_dir(self) -> Path:
        """Quarantine directory for raw PDFs that failed extraction or validation."""
        return self.input_dir / "failed"

    @property
    def pending_dir(self) -> Path:
        """Directory staging validated reports awaiting database sync."""
        return self.output_dir / "pending"

    @property
    def synced_dir(self) -> Path:
        """Directory archiving reports successfully committed to the database."""
        return self.output_dir / "synced"

    @property
    def failed_dir(self) -> Path:
        """Dead-letter directory isolating reports rejected by the backend."""
        return self.output_dir / "failed"

    @property
    def backend_reports_url(self) -> str:
        """Direct report ingestion endpoint URL."""
        return f"{self.backend_base_url.rstrip('/')}/api/v1/reports"

    @property
    def backend_sync_url(self) -> str:
        """Batch queue reconciliation endpoint URL."""
        return f"{self.backend_base_url.rstrip('/')}/api/v1/sync/pending"

    @property
    def backend_status_url(self) -> str:
        """Queue status telemetry endpoint URL."""
        return f"{self.backend_base_url.rstrip('/')}/api/v1/sync/status"

    def ensure_directories(self) -> None:
        """Create standard data, storage, input lifecycle, and output spool directories if they do not exist."""
        for path in (
            self.input_dir,
            self.input_processed_dir,
            self.input_failed_dir,
            self.output_dir,
            self.storage_dir,
            self.prompts_dir,
            self.pending_dir,
            self.synced_dir,
            self.failed_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """Retrieve application settings instance."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance
