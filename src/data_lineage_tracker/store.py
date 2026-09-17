"""SQLite-backed lineage store.

Two tables: ``datasets`` (immutable, content-addressed versions) and
``transforms`` (directed parent -> child edges). The store owns the schema
(migrations are unnecessary — ``CREATE TABLE IF NOT EXISTS``) and enforces
basic integrity: no self-loops and no cycles in the lineage graph.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import hashing
from .models import DatasetVersion, Transform

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    rows INTEGER,
    cols INTEGER,
    schema_fingerprint TEXT NOT NULL,
    source_uri TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE(name, version)
);
CREATE TABLE IF NOT EXISTS transforms (
    id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    op TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(parent_id) REFERENCES datasets(id),
    FOREIGN KEY(child_id) REFERENCES datasets(id),
    UNIQUE(parent_id, child_id, op)
);
CREATE INDEX IF NOT EXISTS idx_datasets_name ON datasets(name);
CREATE INDEX IF NOT EXISTS idx_transforms_parent ON transforms(parent_id);
CREATE INDEX IF NOT EXISTS idx_transforms_child ON transforms(child_id);
"""


class LineageError(Exception):
    """Raised for invalid lineage operations (missing datasets, cycles...)."""


class LineageStore:
    """A SQLite database of dataset versions and their transform edges."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = Path(path)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ utils
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "LineageStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _row_to_dataset(self, row: sqlite3.Row) -> DatasetVersion:
        return DatasetVersion(
            id=row["id"],
            name=row["name"],
            version=row["version"],
            content_hash=row["content_hash"],
            rows=row["rows"],
            cols=row["cols"],
            schema_fingerprint=row["schema_fingerprint"],
            source_uri=row["source_uri"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    def _row_to_transform(self, row: sqlite3.Row) -> Transform:
        return Transform(
            id=row["id"],
            parent_id=row["parent_id"],
            child_id=row["child_id"],
            op=row["op"],
            params=json.loads(row["params"] or "{}"),
            created_at=row["created_at"],
        )

    # --------------------------------------------------------------- datasets
    def register_file(
        self,
        name: str,
        file_path: str | Path,
        source_uri: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DatasetVersion:
        """Fingerprint *file_path* and store it as the next version of *name*.

        Raises :class:`LineageError` if the file does not exist. If an
        identical content hash is already registered under *name*, the
        existing version is returned unchanged (registration is idempotent).
        """
        file_path = Path(file_path)
        if not file_path.is_file():
            raise LineageError(f"file not found: {file_path}")

        content = hashing.content_hash(file_path)
        rows, cols, header = hashing.file_stats(file_path)
        schema_fp = hashing.schema_fingerprint(header) if header else content

        existing = self._conn.execute(
            "SELECT * FROM datasets WHERE name = ? AND content_hash = ?",
            (name, content),
        ).fetchone()
        if existing:
            return self._row_to_dataset(existing)

        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM datasets WHERE name = ?",
            (name,),
        ).fetchone()
        version = int(row[0]) + 1

        ds = DatasetVersion(
            id=uuid.uuid4().hex,
            name=name,
            version=version,
            content_hash=content,
            rows=rows,
            cols=cols,
            schema_fingerprint=schema_fp,
            source_uri=source_uri,
            metadata=metadata or {},
        )
        self._conn.execute(
            """INSERT INTO datasets
               (id, name, version, content_hash, rows, cols,
                schema_fingerprint, source_uri, created_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ds.id, ds.name, ds.version, ds.content_hash, ds.rows,
                ds.cols, ds.schema_fingerprint, ds.source_uri,
                ds.created_at, json.dumps(ds.metadata),
            ),
        )
        self._conn.commit()
        return ds

    def get(self, dataset_id: str) -> DatasetVersion:
        row = self._conn.execute(
            "SELECT * FROM datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        if row is None:
            raise LineageError(f"unknown dataset id: {dataset_id}")
        return self._row_to_dataset(row)

    def resolve(self, ref: str) -> DatasetVersion:
        """Resolve ``ref``: a full id, an id prefix, or ``name@version`` / latest ``name``."""
        # Exact id match first.
        try:
            return self.get(ref)
        except LineageError:
            pass
        # Id prefix match.
        row = self._conn.execute(
            "SELECT * FROM datasets WHERE id LIKE ?",
            (ref + "%",),
        ).fetchone()
        if row is not None:
            return self._row_to_dataset(row)
        # name@version or bare name (latest version).
        if "@" in ref:
            name, _, ver = ref.partition("@")
            row = self._conn.execute(
                "SELECT * FROM datasets WHERE name = ? AND version = ?",
                (name, int(ver)),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM datasets WHERE name = ? ORDER BY version DESC LIMIT 1",
                (ref,),
            ).fetchone()
        if row is None:
            raise LineageError(f"cannot resolve dataset reference: {ref!r}")
        return self._row_to_dataset(row)

    def list_datasets(self, name: Optional[str] = None) -> List[DatasetVersion]:
        if name:
            rows = self._conn.execute(
                "SELECT * FROM datasets WHERE name = ? ORDER BY version", (name,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM datasets ORDER BY name, version"
            ).fetchall()
        return [self._row_to_dataset(r) for r in rows]

    # -------------------------------------------------------------- transforms
    def record_transform(
        self,
        parent_id: str,
        child_id: str,
        op: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Transform:
        """Record a ``parent -> child`` derivation step.

        Raises :class:`LineageError` for unknown datasets, self-loops, and
        edges that would create a cycle (the child must not already be an
        ancestor of the parent).
        """
        if parent_id == child_id:
            raise LineageError("a dataset cannot be derived from itself")
        parent = self.get(parent_id)
        child = self.get(child_id)

        ancestors = {d.id for d in self.descendants(child_id)}
        if parent.id in ancestors:
            raise LineageError(
                f"refusing cycle: {parent.name} is already downstream of {child.name}"
            )

        t = Transform(
            id=uuid.uuid4().hex,
            parent_id=parent.id,
            child_id=child.id,
            op=op,
            params=params or {},
        )
        try:
            self._conn.execute(
                """INSERT INTO transforms
                   (id, parent_id, child_id, op, params, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (t.id, t.parent_id, t.child_id, t.op,
                 json.dumps(t.params), t.created_at),
            )
        except sqlite3.IntegrityError as e:
            raise LineageError(f"duplicate transform edge: {e}") from e
        self._conn.commit()
        return t

    def children(self, dataset_id: str) -> List[Transform]:
        rows = self._conn.execute(
            "SELECT * FROM transforms WHERE parent_id = ? ORDER BY created_at",
            (dataset_id,),
        ).fetchall()
        return [self._row_to_transform(r) for r in rows]

    def parents(self, dataset_id: str) -> List[Transform]:
        rows = self._conn.execute(
            "SELECT * FROM transforms WHERE child_id = ? ORDER BY created_at",
            (dataset_id,),
        ).fetchall()
        return [self._row_to_transform(r) for r in rows]

    # ------------------------------------------------------------------ graph
    def ancestors(self, dataset_id: str, depth: Optional[int] = None) -> List[DatasetVersion]:
        """All datasets *dataset_id* was derived from (BFS, nearest first)."""
        return self._walk(dataset_id, "parents", depth)

    def descendants(self, dataset_id: str, depth: Optional[int] = None) -> List[DatasetVersion]:
        """All datasets derived from *dataset_id* (BFS, nearest first)."""
        return self._walk(dataset_id, "children", depth)

    def _walk(self, start_id: str, direction: str, depth: Optional[int]) -> List[DatasetVersion]:
        self.get(start_id)  # validate
        seen = {start_id}
        ordered: List[DatasetVersion] = []
        frontier = [start_id]
        level = 0
        while frontier and (depth is None or level < depth):
            nxt: List[str] = []
            for node in frontier:
                edges = self.parents(node) if direction == "parents" else self.children(node)
                for e in edges:
                    other = e.parent_id if direction == "parents" else e.child_id
                    if other not in seen:
                        seen.add(other)
                        nxt.append(other)
                        ordered.append(self.get(other))
            frontier = nxt
            level += 1
        return ordered

    def lineage_subgraph(self, dataset_id: str) -> Dict[str, Any]:
        """Full graph around *dataset_id*: ancestors, itself, descendants."""
        root = self.get(dataset_id)
        nodes = {root.id: root.to_dict()}
        for d in self.ancestors(dataset_id) + self.descendants(dataset_id):
            nodes[d.id] = d.to_dict()
        edges = []
        seen_edges = set()
        for node_id in nodes:
            for t in self.parents(node_id) + self.children(node_id):
                if t.id not in seen_edges:
                    seen_edges.add(t.id)
                    edges.append(t.to_dict())
        return {"root": root.id, "datasets": nodes, "transforms": edges}

    def stats(self) -> Dict[str, int]:
        d = self._conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        t = self._conn.execute("SELECT COUNT(*) FROM transforms").fetchone()[0]
        return {"datasets": d, "transforms": t}
