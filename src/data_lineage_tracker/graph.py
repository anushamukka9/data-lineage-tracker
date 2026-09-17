"""Lineage graph queries and exports.

Answers the two core questions of provenance:

* "what produced this dataset?" — the :func:`provenance_chain` of
  ancestors, each annotated with the transform that created it.
* "where did this dataset go?" — the symmetric :func:`downstream_chain`.

Both can be rendered as a GraphViz DOT graph (:func:`to_dot`) or as
plain JSON for downstream tooling.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .models import DatasetVersion
from .store import LineageStore


def provenance_chain(
    store: LineageStore, dataset_id: str, depth: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Ordered steps that produced *dataset_id*, oldest ancestor first.

    Each entry carries the ``dataset``, the ``transform`` that derived the
    next step, and the ``level`` (0 = the queried dataset).
    """
    root = store.get(dataset_id)
    # Walk parents back to the roots, tracking the edge used at each hop.
    hops: List[Dict[str, Any]] = []
    current = root
    level = 0
    while True:
        incoming = store.parents(current.id)
        if not incoming or (depth is not None and level >= depth):
            hops.append({"dataset": current.to_dict(),
                         "transform": None, "level": level})
            break
        edge = incoming[0]  # primary parent edge
        hops.append({"dataset": current.to_dict(),
                     "transform": edge.to_dict(), "level": level})
        current = store.get(edge.parent_id)
        level += 1
    # Reverse: oldest ancestor first, levels re-numbered 0..N.
    hops.reverse()
    for i, hop in enumerate(hops):
        hop["level"] = i
    return hops


def downstream_chain(
    store: LineageStore, dataset_id: str, depth: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Datasets derived from *dataset_id*, following the first-child path
    at each step (level 0 = the queried dataset)."""
    root = store.get(dataset_id)
    chain = [{"dataset": root.to_dict(), "transform": None, "level": 0}]
    current = root
    level = 1
    while depth is None or level <= depth:
        outgoing = store.children(current.id)
        if not outgoing:
            break
        edge = outgoing[0]
        nxt = store.get(edge.child_id)
        chain.append({"dataset": nxt.to_dict(),
                      "transform": edge.to_dict(), "level": level})
        current = nxt
        level += 1
    return chain


def format_chain(chain: List[Dict[str, Any]]) -> str:
    """Human-readable rendering of a provenance/downstream chain."""
    lines = []
    for hop in chain:
        d = hop["dataset"]
        t = hop["transform"]
        indent = "  " * hop["level"]
        line = f"{indent}{d['name']}@v{d['version']} ({d['id'][:8]})"
        if d["rows"] is not None:
            line += f" [{d['rows']} rows x {d['cols']} cols]"
        if t:
            line += f"  <- {t['op']}({', '.join(f'{k}={v}' for k, v in t['params'].items())})"
        lines.append(line)
    return "\n".join(lines)


def _dot_label(ds: Dict[str, Any]) -> str:
    rows = f"{ds['rows']}r" if ds["rows"] is not None else "?"
    return f"{ds['name']}\\nv{ds['version']} · {rows} · {ds['content_hash'][:8]}"


def to_dot(store: LineageStore, dataset_id: Optional[str] = None) -> str:
    """GraphViz DOT of the lineage graph (whole store or one subgraph)."""
    if dataset_id is not None:
        sub = store.lineage_subgraph(dataset_id)
        nodes = sub["datasets"]
        edges = sub["transforms"]
    else:
        nodes = {d.id: d.to_dict() for d in store.list_datasets()}
        seen = set()
        edges = []
        for ds_id in nodes:
            for t in store.children(ds_id):
                if t.id not in seen:
                    seen.add(t.id)
                    edges.append(t.to_dict())

    lines = ["digraph lineage {", '  rankdir="LR";', '  node [shape=box, style=rounded];']
    for ds_id, d in sorted(nodes.items(), key=lambda kv: (kv[1]["name"], kv[1]["version"])):
        color = "lightblue" if (dataset_id and ds_id == dataset_id) else "white"
        lines.append(
            f'  "{ds_id[:8]}" [label="{_dot_label(d)}", fillcolor={color}, style="rounded,filled"];'
        )
    for e in edges:
        param_str = ", ".join(f"{k}={v}" for k, v in e["params"].items())
        label = f"{e['op']}({param_str})" if param_str else e["op"]
        lines.append(
            f'  "{e["parent_id"][:8]}" -> "{e["child_id"][:8]}" [label="{label}"];'
        )
    lines.append("}")
    return "\n".join(lines)


def to_json(store: LineageStore, dataset_id: Optional[str] = None) -> str:
    """JSON export of the lineage graph (whole store or one subgraph)."""
    if dataset_id is not None:
        return json.dumps(store.lineage_subgraph(dataset_id), indent=2)
    datasets = [d.to_dict() for d in store.list_datasets()]
    transforms: List[Dict[str, Any]] = []
    seen = set()
    for d in store.list_datasets():
        for t in store.children(d.id):
            if t.id not in seen:
                seen.add(t.id)
                transforms.append(t.to_dict())
    return json.dumps({"datasets": datasets, "transforms": transforms}, indent=2)
