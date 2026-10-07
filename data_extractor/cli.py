"""Command-line interface for data_extractor with telemetry and failover observability."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from data_extractor.config import get_settings
from data_extractor.workflow import run_workflow


def setup_cli_logging(verbose: bool = False) -> None:
    """Configure console logging level and format."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    """CLI entry point for extracting and validating building expense reports."""
    parser = argparse.ArgumentParser(
        prog="data-extractor",
        description="Extract and validate Chilean building expense reports (Informe Gastos Comunes) into clean JSON.",
    )

    parser.add_argument(
        "pdf_path",
        type=str,
        help="Path to the PDF expense report (e.g. examples/2026-9.pdf)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Optional destination path for the output JSON file",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=None,
        help="Maximum self-healing retry attempts (default: 2)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable detailed debug logging",
    )

    args = parser.parse_args()
    setup_cli_logging(args.verbose)
    settings = get_settings()

    input_pdf = Path(args.pdf_path).resolve()
    if not input_pdf.exists():
        print(f"Error: File not found: {input_pdf}", file=sys.stderr)
        sys.exit(1)

    print(f"📄 Processing: {input_pdf.name}")
    print(f"⚙️  Primary model: {settings.default_model}")
    print(f"🔄 Fallback model: {settings.fallback_model}")

    try:
        result = run_workflow(
            pdf_path=input_pdf,
            output_path=args.output,
            max_retries=args.retries,
        )

        dest = args.output or (settings.output_dir / f"{input_pdf.stem}.json")
        meta = result.get("_metadata", {})

        print(f"\n✅ Extraction and cross-validation complete!")
        print(f"💾 Saved to: {dest}")
        print(f"📅 Issue date: {result.get('issue_date')}")
        print(f"📊 Sections extracted: {len(result.get('sections', []))}")

        if meta:
            model_used = meta.get("model_used")
            fallback_active = meta.get("fallback_triggered", False)
            latency = meta.get("latency_seconds")
            total_duration = meta.get("total_duration_seconds")
            status = meta.get("validation_status")
            retries = meta.get("retry_count", 0)

            print(f"\n🔭 Telemetry & Observability:")
            print(f"   • Model Used: {model_used}")
            if fallback_active:
                print(f"   • ⚠️ Fallback Activated: YES (Reason: {meta.get('fallback_reason')})")
            else:
                print(f"   • Fallback Activated: NO (Primary succeeded)")
            print(f"   • LLM Latency: {latency}s (Total: {total_duration}s)")
            print(f"   • Status: {status} (Self-heal retries: {retries})")

    except Exception as e:
        print(f"\n❌ Error during workflow execution: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
