"""Hash verification: prove the files on disk still match the store.

Registration records a SHA-256 content hash (and, for CSV files, a schema
fingerprint). Verification re-computes those values from a file on disk and
compares them against what the store recorded, catching modified, swapped,
or misplaced dataset files. Verification never mutates the store.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import hashing
from .models import DatasetVersion
from .store import LineageStore


@dataclass
class VerifyResult:
    """Outcome of verifying one registered dataset against a file on disk."""

    dataset: DatasetVersion
    file_path: str
    content_ok: bool
    schema_ok: Optional[bool]  # None when no schema was recorded
    expected_hash: str
    actual_hash: Optional[str]  # None when the file is missing
    expected_schema: Optional[str]
    actual_schema: Optional[str]

    @property
    def ok(self) -> bool:
        """True only when the file exists and every recorded value matches."""
        return self.actual_hash is not None and self.content_ok and (
            self.schema_ok is not False
        )

    @property
    def status(self) -> str:
        if self.actual_hash is None:
            return "file_missing"
        if not self.content_ok:
            return "hash_mismatch"
        if self.schema_ok is False:
            return "schema_mismatch"
        return "ok"

    def to_dict(self) -> dict:
        return {
            "dataset": f"{self.dataset.name}@v{self.dataset.version}",
            "dataset_id": self.dataset.id,
            "file_path": self.file_path,
            "status": self.status,
            "content_ok": self.content_ok,
            "schema_ok": self.schema_ok,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
        }


def verify_dataset(
    store: LineageStore, dataset_id: str, file_path: str | Path
) -> VerifyResult:
    """Verify the file at *file_path* against the registered dataset.

    Raises :class:`LineageError` for an unknown dataset id. A missing file
    is reported as a ``file_missing`` result, not an exception, so callers
    can verify a whole chain and show per-node status.
    """
    ds = store.get(dataset_id)
    path = Path(file_path)
    if not path.is_file():
        return VerifyResult(
            dataset=ds,
            file_path=str(path),
            content_ok=False,
            schema_ok=None,
            expected_hash=ds.content_hash,
            actual_hash=None,
            expected_schema=ds.schema_fingerprint,
            actual_schema=None,
        )

    actual_hash = hashing.content_hash(path)
    content_ok = actual_hash == ds.content_hash

    # The schema fingerprint is only meaningful for CSV-registered datasets
    # (rows/cols are recorded for those); for other files the content hash
    # is the whole identity and there is nothing more to check.
    schema_ok: Optional[bool] = None
    actual_schema: Optional[str] = None
    if ds.rows is not None:
        rows, cols, header = hashing.file_stats(path)
        actual_schema = hashing.schema_fingerprint(header) if header else actual_hash
        schema_ok = actual_schema == ds.schema_fingerprint

    return VerifyResult(
        dataset=ds,
        file_path=str(path),
        content_ok=content_ok,
        schema_ok=schema_ok,
        expected_hash=ds.content_hash,
        actual_hash=actual_hash,
        expected_schema=ds.schema_fingerprint,
        actual_schema=actual_schema,
    )


def verify_chain(
    store: LineageStore,
    dataset_id: str,
    files: dict,
    include_downstream: bool = True,
) -> list[VerifyResult]:
    """Verify every node around *dataset_id* that has a file in *files*.

    ``files`` maps a dataset reference (id, id prefix, ``name@version``, or
    bare ``name``) to a file path. Nodes without a mapped file are skipped.
    """
    root = store.get(dataset_id)
    nodes = [root] + store.ancestors(dataset_id)
    if include_downstream:
        nodes += store.descendants(dataset_id)
    results = []
    for ds in nodes:
        for ref, path in files.items():
            try:
                if store.resolve(ref).id == ds.id:
                    results.append(verify_dataset(store, ds.id, path))
                    break
            except Exception:
                continue
    return results
