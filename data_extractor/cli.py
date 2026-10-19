"""Command-line interface for data_extractor with telemetry, failover, cloud spool sync, and input folder watcher."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path
import requests

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


def handle_status_command() -> int:
    """Query and display spool queue status from backend or local directories."""
    settings = get_settings()
    settings.ensure_directories()
    print("📊 Checking Queue Status...")

    # Try backend HTTP endpoint first
    try:
        resp = requests.get(settings.backend_status_url, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"🌐 Backend Service: {settings.backend_base_url} (CONNECTED)")
            print(f"   • ⏳ Pending: {data.get('pending_count', 0)} file(s) {data.get('pending', [])}")
            print(f"   • ✅ Synced:  {data.get('synced_count', 0)} file(s) {data.get('synced', [])}")
            print(f"   • ❌ Failed:  {data.get('failed_count', 0)} file(s) {data.get('failed', [])}")
            return 0
    except Exception:
        pass

    # Fallback to local directory scan if backend is offline
    pending_files = [f.name for f in settings.pending_dir.glob("*.json")]
    synced_files = [f.name for f in settings.synced_dir.glob("*.json")]
    failed_files = [f.name for f in settings.failed_dir.glob("*.json")]

    print(f"⚠️  Backend Service: {settings.backend_base_url} (OFFLINE - Local Spool Scan)")
    print(f"   • ⏳ Pending: {len(pending_files)} file(s) {pending_files}")
    print(f"   • ✅ Synced:  {len(synced_files)} file(s) {synced_files}")
    print(f"   • ❌ Failed:  {len(failed_files)} file(s) {failed_files}")
    return 0


def handle_sync_command() -> int:
    """Trigger batch reconciliation of pending queue via backend HTTP endpoint."""
    settings = get_settings()
    settings.ensure_directories()
    print(f"🔄 Triggering batch sync with backend: {settings.backend_sync_url} ...")

    try:
        resp = requests.post(settings.backend_sync_url, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✅ Sync complete: {data.get('message', '')}")
            if data.get("synced_files"):
                print(f"   • Synced: {data.get('synced_files')}")
            if data.get("failed_files"):
                print(f"   • Failed: {data.get('failed_files')}")
            return 0
        else:
            print(f"❌ Backend returned error {resp.status_code}: {resp.text}", file=sys.stderr)
            return 1
    except requests.exceptions.ConnectionError:
        print(
            f"⚠️  Backend at {settings.backend_base_url} is unreachable. "
            "Please ensure the backend service is running (e.g. 'python -m backend.app.main').",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        print(f"❌ Error during sync: {e}", file=sys.stderr)
        return 1


def _wait_for_file_stability(file_path: Path, poll_interval: float = 0.1) -> bool:
    """Verify file exists, is non-empty, and size is stable across two checks."""
    try:
        if not file_path.is_file():
            return False
        size1 = file_path.stat().st_size
        if size1 == 0:
            return False
        if poll_interval > 0:
            time.sleep(poll_interval)
        if not file_path.exists():
            return False
        size2 = file_path.stat().st_size
        return size1 == size2 and size2 > 0
    except OSError:
        return False


def _discover_incoming_pdfs(input_dir: Path) -> list[Path]:
    """List top-level PDF files in input_dir, excluding hidden/temporary files and subdirectories."""
    if not input_dir.exists():
        return []
    pdfs: list[Path] = []
    for entry in sorted(input_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name.startswith(".") or entry.name.endswith((".tmp", ".part")):
            continue
        if entry.suffix.lower() == ".pdf":
            pdfs.append(entry)
    return pdfs


def process_single_input_pdf(
    pdf_path: Path,
    max_retries: int | None = None,
) -> bool:
    """Process a single PDF from data/input/ and move it to input/processed/ or input/failed/.

    Returns:
        bool: True if extraction passed and PDF moved to processed/, False if moved to failed/.
    """
    settings = get_settings()
    settings.ensure_directories()

    print(f"\n📥 Ingesting incoming PDF: {pdf_path.name}")
    try:
        result = run_workflow(
            pdf_path=pdf_path,
            max_retries=max_retries,
        )
        spool_status = result.get("_spool_status", "unknown")
        validation_failed = (
            result.get("_status") in ("FAILED_VALIDATION", "FAILED")
            or result.get("_sync", {}).get("status") == "FAILED_VALIDATION"
            or spool_status == "failed"
        )

        if not validation_failed and spool_status in ("synced", "pending"):
            target_pdf = settings.input_processed_dir / pdf_path.name
            shutil.move(str(pdf_path), str(target_pdf))
            print(
                f"✅ Processed '{pdf_path.name}' (spool={spool_status}) -> moved to {target_pdf}"
            )
            return True
        else:
            target_pdf = settings.input_failed_dir / pdf_path.name
            shutil.move(str(pdf_path), str(target_pdf))
            print(
                f"❌ Failed '{pdf_path.name}' (spool={spool_status}) -> moved to {target_pdf}",
                file=sys.stderr,
            )
            return False

    except Exception as exc:
        print(
            f"❌ Exception processing '{pdf_path.name}': {exc}",
            file=sys.stderr,
        )
        try:
            if pdf_path.exists():
                target_pdf = settings.input_failed_dir / pdf_path.name
                shutil.move(str(pdf_path), str(target_pdf))
                print(f"📁 Quarantined '{pdf_path.name}' -> {target_pdf}", file=sys.stderr)
        except Exception as move_exc:
            print(f"⚠️ Could not move failed PDF '{pdf_path.name}': {move_exc}", file=sys.stderr)
        return False


def handle_process_input_command(max_retries: int | None = None) -> int:
    """Scan data/input/*.pdf once and process all discovered PDFs."""
    settings = get_settings()
    settings.ensure_directories()
    incoming = _discover_incoming_pdfs(settings.input_dir)

    if not incoming:
        print(f"📭 No incoming PDF files found in {settings.input_dir}")
        return 0

    print(f"📂 Found {len(incoming)} PDF(s) in {settings.input_dir}...")
    succeeded = 0
    failed = 0
    for pdf_path in incoming:
        if process_single_input_pdf(pdf_path, max_retries=max_retries):
            succeeded += 1
        else:
            failed += 1

    print(
        f"\n🏁 Batch input processing complete: {succeeded} succeeded, {failed} failed."
    )
    return 0 if failed == 0 else 1


def handle_watch_command(
    max_retries: int | None = None,
    poll_seconds: float = 2.0,
) -> int:
    """Continuously monitor data/input/ for new PDF files and process them automatically."""
    settings = get_settings()
    settings.ensure_directories()
    print(f"👀 Watching {settings.input_dir} for incoming PDFs (Ctrl+C to stop)...")

    try:
        while True:
            incoming = _discover_incoming_pdfs(settings.input_dir)
            for pdf_path in incoming:
                if _wait_for_file_stability(pdf_path):
                    process_single_input_pdf(pdf_path, max_retries=max_retries)
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        print("\n🛑 Input folder watcher stopped.")
        return 0


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for extracting, validating, and synchronizing expense reports."""
    parser = argparse.ArgumentParser(
        prog="data-extractor",
        description="Extract and validate Chilean building expense reports (Informe Gastos Comunes) with cloud spool sync.",
    )

    parser.add_argument(
        "pdf_path",
        type=str,
        nargs="?",
        default=None,
        help="Path to the PDF expense report (e.g. examples/2026-9.pdf)",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Trigger batch reconciliation of all pending reports via backend service",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check current queue status (pending, synced, failed)",
    )
    parser.add_argument(
        "--process-input",
        action="store_true",
        help="Batch process all PDF files currently in data/input/ and move them to processed/ or failed/",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously watch data/input/ for new PDF files and process them automatically",
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

    args = parser.parse_args(argv)
    setup_cli_logging(args.verbose)
    settings = get_settings()

    # Route subcommands
    if args.status:
        sys.exit(handle_status_command())

    if args.sync:
        sys.exit(handle_sync_command())

    if args.process_input:
        sys.exit(handle_process_input_command(max_retries=args.retries))

    if args.watch:
        sys.exit(handle_watch_command(max_retries=args.retries))

    if not args.pdf_path:
        parser.print_help()
        sys.exit(1)

    input_pdf = Path(args.pdf_path).resolve()
    if not input_pdf.exists():
        print(f"Error: File not found: {input_pdf}", file=sys.stderr)
        sys.exit(1)

    print(f"📄 Processing: {input_pdf.name}")
    print(f"⚙️  Primary model: {settings.default_model}")
    print(f"🔄 Fallback models: {', '.join(settings.fallback_models)}")

    try:
        result = run_workflow(
            pdf_path=input_pdf,
            output_path=args.output,
            max_retries=args.retries,
        )

        dest = result.get("_destination_path") or (args.output or (settings.output_dir / f"{input_pdf.stem}.json"))
        spool_status = result.get("_spool_status", "unknown")
        meta = result.get("_metadata", {})

        validation_failed = (
            result.get("_status") in ("FAILED_VALIDATION", "FAILED")
            or result.get("_sync", {}).get("status") == "FAILED_VALIDATION"
        )

        if validation_failed:
            print(f"\n❌ Extraction finished, but cross-validation FAILED!")
            print(f"📁 Stored in failed spool: {dest}")
            errors = result.get("_errors") or result.get("_sync", {}).get("errors", [])
            if errors:
                print("\n🔍 Validation Discrepancy Breakdown:")
                for err in errors:
                    print(f"   • {err}")
        else:
            print(f"\n✅ Extraction and cross-validation complete!")
            if spool_status == "synced":
                print(f"💾 Synced & loaded directly to database: {dest}")
            elif spool_status == "pending":
                print(f"⚠️  Backend offline. Staged in pending spool: {dest}")
                print(f"   (Run 'data-extractor --sync' when backend is online)")
            elif spool_status == "failed":
                print(f"❌ Ingestion rejected by backend. Stored in failed spool: {dest}")
            else:
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
