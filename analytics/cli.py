"""Command-line interface for running expense analytics queries and generating visual charts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from analytics.workflow import run_analysis


def main(argv: list[str] | None = None) -> int:
    """Execute analytics CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="python -m analytics.cli",
        description="Query Chilean building expense reports and generate analytical charts.",
    )
    parser.add_argument(
        "query",
        type=str,
        help="Natural language financial or analytical query.",
    )
    parser.add_argument(
        "--chart-dir",
        type=Path,
        default=None,
        help="Optional destination directory for rendered chart PNGs.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Maximum self-healing attempts for SQL or chart errors (default: 2).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw structured JSON payload instead of formatted markdown.",
    )

    args = parser.parse_args(argv)

    try:
        result = run_analysis(
            query=args.query,
            chart_dir=args.chart_dir,
            max_retries=args.max_retries,
        )

        # Interactive clarification loop if paused and not in raw JSON output mode
        if result.needs_clarification and not args.json:
            print(f"\n🔍 Aclaración requerida:\n{result.clarification_prompt}\n")
            for idx, option in enumerate(result.candidate_options, start=1):
                print(f"  [{idx}] {option}")

            user_input = input("\nSelecciona una opción (número) o escribe tu aclaración: ").strip()
            if user_input:
                result = run_analysis(
                    query=args.query,
                    chart_dir=args.chart_dir,
                    max_retries=args.max_retries,
                    thread_id=result.thread_id,
                    resume_choice=user_input,
                )

        if args.json:
            print(result.model_dump_json(indent=2))
            return 0

        print(result.summary)
        if result.chart_file_path:
            print(f"\n[Chart Saved]: {result.chart_file_path}")
        print(f"\n[Telemetry]: latency: {result.latency_seconds}s | errors: {len(result.error_history)}")

        return 0

    except Exception as exc:
        print(f"Error executing analytics query: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
