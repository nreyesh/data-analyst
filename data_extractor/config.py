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

    def get_input_domain_dir(self, doc_type: str = "general") -> Path:
        """Return input drop directory for a document domain ('general' or 'unit')."""
        return self.input_dir / doc_type

    def get_input_processed_dir(self, doc_type: str = "general") -> Path:
        """Return input processed archive directory for a document domain."""
        return self.input_dir / doc_type / "processed"

    def get_input_failed_dir(self, doc_type: str = "general") -> Path:
        """Return input failed quarantine directory for a document domain."""
        return self.input_dir / doc_type / "failed"

    def get_pending_dir(self, doc_type: str = "general") -> Path:
        """Return output pending spool directory for a document domain."""
        return self.output_dir / doc_type / "pending"

    def get_synced_dir(self, doc_type: str = "general") -> Path:
        """Return output synced archive directory for a document domain."""
        return self.output_dir / doc_type / "synced"

    def get_failed_dir(self, doc_type: str = "general") -> Path:
        """Return output failed dead-letter directory for a document domain."""
        return self.output_dir / doc_type / "failed"

    @property
    def general_input_dir(self) -> Path:
        """Incoming drop directory for general building expense reports."""
        return self.get_input_domain_dir("general")

    @property
    def unit_input_dir(self) -> Path:
        """Incoming drop directory for individual unit billing statements."""
        return self.get_input_domain_dir("unit")

    @property
    def input_processed_dir(self) -> Path:
        """Directory archiving raw general PDFs that passed extraction and validation."""
        return self.get_input_processed_dir("general")

    @property
    def input_failed_dir(self) -> Path:
        """Quarantine directory for raw general PDFs that failed extraction or validation."""
        return self.get_input_failed_dir("general")

    @property
    def unit_input_processed_dir(self) -> Path:
        """Directory archiving raw unit PDFs that passed extraction and validation."""
        return self.get_input_processed_dir("unit")

    @property
    def unit_input_failed_dir(self) -> Path:
        """Quarantine directory for raw unit PDFs that failed extraction or validation."""
        return self.get_input_failed_dir("unit")

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
    def backend_reports_url(self) -> str:
        """Direct general report ingestion endpoint URL."""
        return f"{self.backend_base_url.rstrip('/')}/api/v1/reports"

    @property
    def backend_unit_bills_url(self) -> str:
        """Direct unit bill ingestion endpoint URL."""
        return f"{self.backend_base_url.rstrip('/')}/api/v1/unit-bills"

    @property
    def backend_sync_url(self) -> str:
        """Batch queue reconciliation endpoint URL."""
        return f"{self.backend_base_url.rstrip('/')}/api/v1/sync/pending"

    @property
    def backend_status_url(self) -> str:
        """Queue status telemetry endpoint URL."""
        return f"{self.backend_base_url.rstrip('/')}/api/v1/sync/status"

    def ensure_directories(self) -> None:
        """Create standard data, storage, input lifecycle, and output spool directories for general and unit domains."""
        for path in (
            self.input_dir,
            self.output_dir,
            self.storage_dir,
            self.prompts_dir,
            self.general_input_dir,
            self.input_processed_dir,
            self.input_failed_dir,
            self.unit_input_dir,
            self.unit_input_processed_dir,
            self.unit_input_failed_dir,
            self.pending_dir,
            self.synced_dir,
            self.failed_dir,
            self.unit_pending_dir,
            self.unit_synced_dir,
            self.unit_failed_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """Retrieve application settings instance."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance
