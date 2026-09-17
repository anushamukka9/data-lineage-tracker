"""Core data model: dataset versions and transformation steps.

A :class:`DatasetVersion` is an immutable snapshot of a dataset — the same
name can have many versions, each pinned by a content hash.  A
:class:`Transform` is a directed edge ``parent -> child`` describing how one
dataset version was derived from another, including the operation name and
its parameters (so the lineage is reproducible, not just descriptive).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DatasetVersion:
    """An immutable, content-addressed snapshot of a dataset."""

    id: str
    name: str
    version: int
    content_hash: str
    rows: int | None
    cols: int | None
    schema_fingerprint: str
    source_uri: str = ""
    created_at: str = field(default_factory=_utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def short_id(self) -> str:
        return self.id[:8]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "content_hash": self.content_hash,
            "rows": self.rows,
            "cols": self.cols,
            "schema_fingerprint": self.schema_fingerprint,
            "source_uri": self.source_uri,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DatasetVersion":
        return cls(
            id=d["id"],
            name=d["name"],
            version=d["version"],
            content_hash=d["content_hash"],
            rows=d.get("rows"),
            cols=d.get("cols"),
            schema_fingerprint=d["schema_fingerprint"],
            source_uri=d.get("source_uri", ""),
            created_at=d.get("created_at", _utcnow()),
            metadata=d.get("metadata", {}),
        )


@dataclass
class Transform:
    """A directed parent -> child edge recording one derivation step."""

    id: str
    parent_id: str
    child_id: str
    op: str
    params: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "child_id": self.child_id,
            "op": self.op,
            "params": self.params,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Transform":
        return cls(
            id=d["id"],
            parent_id=d["parent_id"],
            child_id=d["child_id"],
            op=d["op"],
            params=d.get("params", {}),
            created_at=d.get("created_at", _utcnow()),
        )
