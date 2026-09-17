"""Content identity: hashing, schema fingerprints, and CSV statistics.

Identity is the heart of provenance. Two files that contain the same bytes
are the same dataset version; a *schema fingerprint* additionally detects
column renames/reorders without reading the whole file again. All hashes
are SHA-256 over canonical (deterministic) byte representations.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Iterable, Sequence, Tuple

_CHUNK = 1024 * 1024


def content_hash(path: str | Path) -> str:
    """SHA-256 hex digest of a file's raw bytes (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def schema_fingerprint(columns: Sequence[str]) -> str:
    """SHA-256 hex digest of a canonical schema representation.

    Canonical form is one ``name`` per line, sorted case-sensitively, so
    that two tables with identical column sets (in any physical order)
    share a fingerprint, while any rename/add/drop changes it.
    """
    canonical = "\n".join(sorted(columns)) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def csv_stats(path: str | Path) -> Tuple[int, int, list[str]]:
    """Return ``(row_count, col_count, column_names)`` for a CSV file.

    Streams the file once; rows are data rows (header excluded). Raises
    ``ValueError`` if the file has no header row.
    """
    path = Path(path)
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            raise ValueError(f"{path}: no header row found")
        rows = sum(1 for _ in reader)
    return rows, len(header), list(header)


def file_stats(path: str | Path) -> Tuple[int | None, int | None, list[str]]:
    """Best-effort stats for any file: CSV-aware, else ``(None, None, [])``."""
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return csv_stats(path)
    return None, None, []


def fingerprint_file(path: str | Path) -> Tuple[str, str]:
    """Return ``(content_hash, schema_fingerprint)`` for *path*.

    Schema fingerprint falls back to the content hash for non-CSV files.
    """
    ch = content_hash(path)
    rows, cols, header = file_stats(path)
    fp = schema_fingerprint(header) if header else ch
    return ch, fp


def sha256_text(parts: Iterable[str]) -> str:
    """SHA-256 hex digest of ``"\\n".join(parts) + "\\n"``."""
    return hashlib.sha256(("\n".join(parts) + "\n").encode("utf-8")).hexdigest()
