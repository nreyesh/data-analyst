"""Prompt templates and YAML Data Dictionary loader for analytics graph."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
import yaml

from analytics.config import get_analytics_settings

logger = logging.getLogger("analytics.prompt_loader")


def get_prompts_dir() -> Path:
    """Retrieve prompts asset directory."""
    return get_analytics_settings().prompts_dir


def load_data_dictionary() -> dict[str, Any]:
    """Parse and return the YAML Data Dictionary describing database schema and domain rules."""
    settings = get_analytics_settings()
    dict_path = settings.data_dictionary_path
    if not dict_path.exists():
        raise FileNotFoundError(
            f"YAML Data Dictionary not found at {dict_path}. Please initialize the prompts asset."
        )

    with open(dict_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Data dictionary at {dict_path} must be a YAML mapping.")

    return data


def load_data_dictionary_str() -> str:
    """Return raw YAML content of Data Dictionary for prompt injection."""
    settings = get_analytics_settings()
    dict_path = settings.data_dictionary_path
    if not dict_path.exists():
        raise FileNotFoundError(f"YAML Data Dictionary not found at {dict_path}.")
    return dict_path.read_text(encoding="utf-8")


def load_prompt_template(name: str) -> str:
    """Load prompt template text from prompts directory.

    Args:
        name: Template filename (e.g. 'planner_system.md', 'sql_coder_system.md').

    Returns:
        Stripped prompt string.
    """
    prompts_dir = get_prompts_dir()
    file_path = prompts_dir / name
    if not file_path.exists():
        raise FileNotFoundError(f"Prompt template '{name}' not found at {file_path}")
    return file_path.read_text(encoding="utf-8").strip()
