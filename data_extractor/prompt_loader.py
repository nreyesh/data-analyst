"""Prompt template loading and formatting utilities."""

from __future__ import annotations

from pathlib import Path
from data_extractor.config import get_settings


def get_prompts_dir() -> Path:
    """Return the resolved prompts directory."""
    return get_settings().prompts_dir


def load_system_prompt() -> str:
    """Load the primary document extractor system prompt."""
    prompt_file = get_prompts_dir() / "extractor_system.txt"
    if not prompt_file.exists():
        raise FileNotFoundError(f"System prompt template not found at {prompt_file}")
    return prompt_file.read_text(encoding="utf-8").strip()


def build_self_heal_prompt(validation_errors: list[str]) -> str:
    """Build a targeted feedback prompt for self-healing refinement."""
    template_file = get_prompts_dir() / "self_heal_feedback.txt"
    if not template_file.exists():
        raise FileNotFoundError(
            f"Self-healing prompt template not found at {template_file}"
        )

    formatted_errors = "\n".join(f"- {err}" for err in validation_errors)
    template = template_file.read_text(encoding="utf-8")
    return template.replace("{validation_errors}", formatted_errors).strip()
