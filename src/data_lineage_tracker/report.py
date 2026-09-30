"""Lineage reports: the full story of one dataset, in one place.

A report combines a dataset's details, its full provenance chain (oldest
ancestor first), every downstream derivative, and per-node hash
verification when the caller can point each node at the file currently on
disk. Reports render as Markdown for humans and serialize as plain dicts
(and JSON) for tooling.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import graph, verify as verify_mod
from .models import DatasetVersion
from .store import LineageStore
from .verify import VerifyResult


@dataclass
class LineageReport:
    """Everything known about one dataset's lineage."""

    dataset: DatasetVersion
    provenance: List[Dict[str, Any]] = field(default_factory=list)
    downstream: List[Dict[str, Any]] = field(default_factory=list)
    verifications: List[VerifyResult] = field(default_factory=list)

    # ------------------------------------------------------------ rendering
    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset": self.dataset.to_dict(),
            "provenance": self.provenance,
            "downstream": self.downstream,
            "verifications": [v.to_dict() for v in self.verifications],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        d = self.dataset
        lines = [f"# Lineage report: {d.name}@v{d.version}", ""]
        lines.append("## Dataset")
        lines.append("")
        lines.append(f"- **id:** `{d.id}`")
        lines.append(f"- **content hash:** `{d.content_hash}`")
        shape = (
            f"{d.rows} rows x {d.cols} cols"
            if d.rows is not None
            else "unknown (non-CSV)"
        )
        lines.append(f"- **shape:** {shape}")
        lines.append(f"- **schema fingerprint:** `{d.schema_fingerprint}`")
        lines.append(f"- **source:** {d.source_uri or '(none)'}")
        lines.append(f"- **created:** {d.created_at}")
        if d.metadata:
            lines.append(f"- **metadata:** `{json.dumps(d.metadata)}`")
        lines.append("")

        lines.append("## Provenance (what produced this dataset)")
        lines.append("")
        if len(self.provenance) <= 1:
            lines.append("_No ancestors recorded: this is a root dataset._")
        else:
            lines.append("```")
            lines.append(graph.format_chain(self.provenance))
            lines.append("```")
        lines.append("")

        lines.append("## Downstream (where did it go)")
        lines.append("")
        if not self.downstream:
            lines.append("_No downstream derivatives recorded._")
        else:
            for item in self.downstream:
                dd = item["dataset"]
                lines.append(
                    f"- **{dd['name']}@v{dd['version']}** "
                    f"(`{dd['id'][:8]}`) via `{item['derived_via']}` "
                    f"from {item['derived_from']}"
                )
        lines.append("")

        lines.append("## Hash verification")
        lines.append("")
        if not self.verifications:
            lines.append(
                "_No files were provided for verification; "
                "pass a file map to check hashes._"
            )
        else:
            lines.append("| Dataset | File | Status |")
            lines.append("| --- | --- | --- |")
            for v in self.verifications:
                icon = "ok" if v.ok else "MISMATCH"
                lines.append(
                    f"| {v.dataset.name}@v{v.dataset.version} "
                    f"(`{v.dataset.id[:8]}`) | `{v.file_path}` | {icon} ({v.status}) |"
                )
        lines.append("")
        return "\n".join(lines)


def lineage_report(
    store: LineageStore,
    dataset_id: str,
    files: Optional[dict] = None,
) -> LineageReport:
    """Build the full report for *dataset_id*.

    ``files`` optionally maps a dataset reference (id, id prefix,
    ``name@version``, or bare ``name``) to the file currently on disk, for
    hash verification of the whole chain.
    """
    root = store.get(dataset_id)
    provenance = graph.provenance_chain(store, root.id)

    downstream: List[Dict[str, Any]] = []
    for desc in store.descendants(root.id):
        # The edge that brought this descendant into the tree.
        edge = next(iter(store.parents(desc.id)), None)
        parent = store.get(edge.parent_id) if edge else None
        downstream.append(
            {
                "dataset": desc.to_dict(),
                "derived_via": edge.op if edge else "(unknown)",
                "derived_from": (
                    f"{parent.name}@v{parent.version}" if parent else "(unknown)"
                ),
            }
        )

    verifications = verify_mod.verify_chain(store, root.id, files or {})
    return LineageReport(
        dataset=root,
        provenance=provenance,
        downstream=downstream,
        verifications=verifications,
    )
