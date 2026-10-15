"""Configuration and settings management for analytics package."""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Export .env variables to os.environ for LangSmith / LangGraph tracing
load_dotenv()


class AnalyticsSettings(BaseSettings):
    """Runtime configuration for analytics graph and services."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 1. Credentials
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
        description="Google Gemini API Key",
    )

    # 2. Operational App Settings
    default_model: str = Field(
        default="gemini-3.5-flash-lite",
        validation_alias="GEMINI_MODEL",
        description="Default Gemini model name",
    )
    fallback_model: str = Field(
        default="gemini-3.1-flash-lite",
        validation_alias="FALLBACK_MODEL",
        description="Secondary fallback Gemini model name",
    )
    max_retries: int = Field(
        default=2,
        validation_alias="MAX_RETRIES",
        description="Maximum self-healing retry iterations",
    )

    # 3. Timeouts & Limits
    query_timeout_seconds: float = Field(
        default=3.0,
        validation_alias="QUERY_TIMEOUT_SECONDS",
        description="Data Gateway hard query execution timeout in seconds",
    )
    max_query_rows: int = Field(
        default=500,
        validation_alias="MAX_QUERY_ROWS",
        description="Hard maximum row limit returned by Data Gateway",
    )
    sandbox_timeout_seconds: int = Field(
        default=10,
        validation_alias="SANDBOX_TIMEOUT_SECONDS",
        description="Subprocess sandbox script timeout in seconds",
    )

    # 4. Service URLs & Ports
    backend_base_url: str = Field(
        default="http://localhost:8000",
        validation_alias="BACKEND_BASE_URL",
        description="Base URL for the persistence backend service",
    )

    @property
    def gateway_execute_url(self) -> str:
        """Data Gateway query execution endpoint URL."""
        return f"{self.backend_base_url.rstrip('/')}/api/v1/analytics/execute-query"

    @property
    def analytics_query_url(self) -> str:
        """High-level consumer analytics endpoint URL."""
        return f"{self.backend_base_url.rstrip('/')}/api/v1/analytics/query"

    # 5. Path Boundaries
    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent
    )
    storage_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "storage"
    )
    output_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "data" / "output"
    )
    prompts_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent / "prompts"
    )

    @property
    def database_path(self) -> Path:
        """Path to the SQLite database file."""
        return self.storage_dir / "expenses.db"

    @property
    def charts_dir(self) -> Path:
        """Directory storing permanent rendered chart image files."""
        return self.output_dir / "charts"

    @property
    def scratch_dir(self) -> Path:
        """Directory storing ephemeral script and data artifacts for sandboxing."""
        return self.charts_dir / "scratch"

    @property
    def data_dictionary_path(self) -> Path:
        """Path to YAML Data Dictionary asset."""
        return self.prompts_dir / "data_dictionary.md"

    def ensure_directories(self) -> None:
        """Ensure standard charts, scratch, and storage directories exist."""
        for path in (self.charts_dir, self.scratch_dir, self.prompts_dir):
            path.mkdir(parents=True, exist_ok=True)


_settings_instance: AnalyticsSettings | None = None


def get_analytics_settings() -> AnalyticsSettings:
    """Retrieve global AnalyticsSettings instance."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = AnalyticsSettings()
    return _settings_instance
