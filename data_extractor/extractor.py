"""Multimodal extraction engine with transparent dual-model failover and telemetry."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from google import genai
from google.genai import types

from data_extractor.config import get_settings
from data_extractor.models import ExpenseReport, ExtractionMetadata
from data_extractor.prompt_loader import load_system_prompt

logger = logging.getLogger("data_extractor.extractor")


def get_gemini_client() -> genai.Client:
    """Create and return a configured Gemini client."""
    settings = get_settings()
    api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY is not set. Please set it in your .env file or environment."
        )
    return genai.Client(api_key=api_key)


def extract_from_pdf(
    pdf_source: str | Path | bytes,
    client: genai.Client | None = None,
    model: str | None = None,
    feedback_prompt: str | None = None,
) -> tuple[ExpenseReport, ExtractionMetadata]:
    """Extract structured expense report data from a PDF document with dual-model failover.

    Args:
        pdf_source: File path or raw bytes of the PDF.
        client: Optional pre-configured Gemini client.
        model: Optional model override (defaults to settings.default_model).
        feedback_prompt: Optional targeted error feedback for self-healing iteration.

    Returns:
        Tuple of (validated ExpenseReport, ExtractionMetadata telemetry).
    """
    settings = get_settings()
    if client is None:
        client = get_gemini_client()

    primary_model = model or settings.default_model
    candidate_models = [primary_model]
    if settings.fallback_model and settings.fallback_model != primary_model:
        candidate_models.append(settings.fallback_model)

    # 1. Load PDF bytes
    if isinstance(pdf_source, (str, Path)):
        pdf_path = Path(pdf_source)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found at {pdf_path}")
        pdf_bytes = pdf_path.read_bytes()
    elif isinstance(pdf_source, bytes):
        pdf_bytes = pdf_source
    else:
        raise TypeError(f"Unsupported pdf_source type: {type(pdf_source)}")

    # 2. Build multimodal contents
    pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    system_instruction = load_system_prompt()

    user_message = (
        "Please extract all categorical line items from this building expense report. "
        "Strictly adhere to the extraction rules, normalize the issue date to 01/MM/YYYY, "
        "extract clean base names, output values as integer CLP, and map 'Gas Cargo Comun al 30%' under 'Consumos'."
    )

    contents: list[types.Part | str] = [pdf_part, user_message]

    if feedback_prompt:
        contents.append(
            f"ATTENTION - PREVIOUS EXTRACTION ERRORS DETECTED:\n{feedback_prompt}"
        )

    # 3. Configure structured output schema
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ExpenseReport,
        system_instruction=system_instruction,
        temperature=0.0,
    )

    # 4. Attempt extraction across model cascade
    last_error: Exception | None = None
    fallback_triggered = False
    fallback_reason: str | None = None

    for idx, current_model in enumerate(candidate_models):
        if idx > 0:
            fallback_triggered = True
            fallback_reason = f"{type(last_error).__name__}: {last_error}"
            logger.warning(
                "Primary model '%s' failed (%s). Activating fallback model '%s'...",
                candidate_models[0],
                last_error,
                current_model,
            )

        try:
            start_time = time.perf_counter()
            response = client.models.generate_content(
                model=current_model,
                contents=contents,
                config=config,
            )
            latency = time.perf_counter() - start_time

            if not response.text:
                raise RuntimeError(
                    f"Model '{current_model}' returned empty response text."
                )

            report = ExpenseReport.model_validate_json(response.text)

            metadata = ExtractionMetadata(
                model_used=current_model,
                fallback_triggered=fallback_triggered,
                fallback_reason=fallback_reason,
                latency_seconds=round(latency, 2),
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            return report, metadata

        except Exception as exc:
            last_error = exc
            logger.warning("Attempt with model '%s' failed: %s", current_model, exc)

    raise RuntimeError(
        f"All configured extraction models failed. Attempted models: {candidate_models}. "
        f"Last error: {last_error}"
    ) from last_error
