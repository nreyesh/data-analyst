"""Utility functions for UUID generation, date parsing, and text normalization."""

from __future__ import annotations

import os
import re
import time
import uuid

try:
    import uuid_utils

    def generate_uuid7() -> str:
        """Generate a time-ordered UUIDv7 compliant with RFC 9562."""
        return str(uuid_utils.uuid7())

except ImportError:
    # Pure Python fallback for RFC 9562 UUIDv7
    def generate_uuid7() -> str:
        """Generate a time-ordered UUIDv7 (pure Python fallback)."""
        # 48-bit timestamp in milliseconds
        ts_ms = int(time.time() * 1000)
        rand_bytes = os.urandom(10)

        # Build 16 bytes: 6 bytes ts, 2 bytes rand (with ver 7), 8 bytes rand (with var 2)
        uuid_bytes = bytearray(16)
        uuid_bytes[0:6] = ts_ms.to_bytes(6, byteorder="big")
        uuid_bytes[6:8] = rand_bytes[0:2]
        uuid_bytes[6] = (uuid_bytes[6] & 0x0F) | 0x70  # Version 7
        uuid_bytes[8:16] = rand_bytes[2:10]
        uuid_bytes[8] = (uuid_bytes[8] & 0x3F) | 0x80  # Variant 10xx (RFC 4122/9562)

        return str(uuid.UUID(bytes=bytes(uuid_bytes)))


def parse_issue_date(issue_date: str) -> tuple[str, int, int]:
    """Parse '01/MM/YYYY' into (period_date 'YYYY-MM-01', period_year, period_month).

    Raises:
        ValueError: If issue_date format does not match '01/MM/YYYY'.
    """
    match = re.match(r"^01/(\d{2})/(\d{4})$", issue_date.strip())
    if not match:
        raise ValueError(
            f"Invalid issue_date format: {issue_date!r}. Must strictly be '01/MM/YYYY'."
        )
    month_str, year_str = match.groups()
    month = int(month_str)
    year = int(year_str)
    if not (1 <= month <= 12):
        raise ValueError(f"Month must be between 1 and 12, got: {month}")

    period_date = f"{year:04d}-{month:02d}-01"
    return period_date, year, month


def normalize_item_name(name: str) -> str:
    """Normalize item name to stripped lowercase for cross-month analytical queries."""
    return name.strip().lower()
