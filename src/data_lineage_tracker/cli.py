"""Command-line interface for data-lineage-tracker.

The database lives at ``$LINEAGE_DB`` or ``~/.data-lineage-tracker/lineage.db``
by default; pass ``--db`` to override per-invocation.

Dataset references accept: full id, id prefix, ``name@version``, or a bare
``name`` (latest version).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from . import graph
from .store import LineageError, LineageStore

DEFAULT_DB = Path.home() / ".data-lineage-tracker" / "lineage.db"


def db_path(args: argparse.Namespace) -> Path:
    if getattr(args, "db", None):
        return Path(args.db)
    env = os.environ.get("LINEAGE_DB")
    if env:
        return Path(env)
    return DEFAULT_DB


def open_store(args: argparse.Namespace) -> LineageStore:
    path = db_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    return LineageStore(path)


def parse_kv(pairs: Optional[list]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise LineageError(f"expected KEY=VALUE, got {pair!r}")
        k, v = pair.split("=", 1)
        out[k] = v
    return out


# ------------------------------------------------------------------ commands
def cmd_register(args: argparse.Namespace) -> int:
    store = open_store(args)
    try:
        ds = store.register_file(
            args.name,
            args.file,
            source_uri=args.source or "",
            metadata=parse_kv(args.meta),
        )
    except LineageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"registered {ds.name}@v{ds.version}")
    print(f"  id:      {ds.id}")
    print(f"  hash:    {ds.content_hash}")
    print(f"  shape:   {ds.rows} rows x {ds.cols} cols" if ds.rows is not None else "  shape:   unknown")
    print(f"  schema:  {ds.schema_fingerprint[:16]}...")
    return 0


def cmd_transform(args: argparse.Namespace) -> int:
    store = open_store(args)
    try:
        if args.child_name and not args.child_file:
            raise LineageError("--child-file is required when using --child-name")
        parent = store.resolve(args.parent)
        if args.child_id:
            child = store.resolve(args.child_id)
        else:
            child = store.register_file(
                args.child_name,
                args.child_file,
                source_uri=args.source or "",
                metadata=parse_kv(args.meta),
            )
            print(f"registered {child.name}@v{child.version} ({child.id[:8]})")
        t = store.record_transform(
            parent.id, child.id, args.op, parse_kv(args.param)
        )
    except LineageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"recorded: {parent.name}@v{parent.version} -> {child.name}@v{child.version} [{t.op}]")
    return 0


def cmd_lineage(args: argparse.Namespace) -> int:
    store = open_store(args)
    try:
        ds = store.resolve(args.ref)
    except LineageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    chain = graph.provenance_chain(store, ds.id, depth=args.depth)
    print(f"# provenance of {ds.name}@v{ds.version}")
    print(graph.format_chain(chain))
    return 0


def cmd_downstream(args: argparse.Namespace) -> int:
    store = open_store(args)
    try:
        ds = store.resolve(args.ref)
    except LineageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    chain = graph.downstream_chain(store, ds.id, depth=args.depth)
    print(f"# downstream of {ds.name}@v{ds.version}")
    print(graph.format_chain(chain))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = open_store(args)
    try:
        ds = store.resolve(args.ref)
    except LineageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{ds.name}@v{ds.version}")
    print(f"  id:          {ds.id}")
    print(f"  content_hash: {ds.content_hash}")
    print(f"  rows/cols:    {ds.rows} / {ds.cols}")
    print(f"  schema_fp:    {ds.schema_fingerprint}")
    print(f"  source:       {ds.source_uri or '(none)'}")
    print(f"  created:      {ds.created_at}")
    if ds.metadata:
        print(f"  metadata:     {ds.metadata}")
    parents = store.parents(ds.id)
    children = store.children(ds.id)
    if parents:
        print("  derived from:")
        for t in parents:
            p = store.get(t.parent_id)
            print(f"    - {p.name}@v{p.version} via {t.op}")
    if children:
        print("  derived into:")
        for t in children:
            c = store.get(t.child_id)
            print(f"    - {c.name}@v{c.version} via {t.op}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = open_store(args)
    datasets = store.list_datasets(name=args.name)
    if not datasets:
        print("(no datasets registered)")
        return 0
    for ds in datasets:
        shape = f"{ds.rows}x{ds.cols}" if ds.rows is not None else "?"
        print(f"{ds.name}@v{ds.version}  {ds.id[:8]}  {shape}  {ds.content_hash[:12]}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    store = open_store(args)
    ds_id: Optional[str] = None
    if args.ref:
        try:
            ds_id = store.resolve(args.ref).id
        except LineageError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    if args.format == "dot":
        text = graph.to_dot(store, ds_id)
    else:
        text = graph.to_json(store, ds_id)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    store = open_store(args)
    s = store.stats()
    print(f"datasets:   {s['datasets']}")
    print(f"transforms: {s['transforms']}")
    return 0


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dlt",
        description="Dataset provenance tracking: content-hashed versions, "
                    "transform lineage graph, SQLite store.",
    )
    p.add_argument("--db", help="path to lineage database "
                   "(default: $LINEAGE_DB or ~/.data-lineage-tracker/lineage.db)")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("register", help="register a file as a dataset version")
    r.add_argument("name")
    r.add_argument("--file", required=True, help="path to the dataset file")
    r.add_argument("--source", default="", help="source URI (s3://, https://, ...)")
    r.add_argument("--meta", action="append", default=[], metavar="K=V")
    r.set_defaults(func=cmd_register)

    t = sub.add_parser("transform", help="record a parent -> child derivation")
    t.add_argument("--parent", required=True, help="parent dataset ref")
    child = t.add_mutually_exclusive_group(required=True)
    child.add_argument("--child-id", help="existing child dataset ref")
    child.add_argument("--child-name", help="register a new child dataset under this name")
    t.add_argument("--child-file", help="file for the new child dataset")
    t.add_argument("--op", required=True, help="operation name, e.g. filter, join, sample")
    t.add_argument("--param", action="append", default=[], metavar="K=V")
    t.add_argument("--source", default="")
    t.add_argument("--meta", action="append", default=[], metavar="K=V")
    t.set_defaults(func=cmd_transform)

    l = sub.add_parser("lineage", help='answer "what produced this dataset?"')
    l.add_argument("ref", help="dataset ref")
    l.add_argument("--depth", type=int, default=None)
    l.set_defaults(func=cmd_lineage)

    d = sub.add_parser("downstream", help='answer "where did this dataset go?"')
    d.add_argument("ref", help="dataset ref")
    d.add_argument("--depth", type=int, default=None)
    d.set_defaults(func=cmd_downstream)

    s = sub.add_parser("show", help="show dataset details and direct edges")
    s.add_argument("ref", help="dataset ref")
    s.set_defaults(func=cmd_show)

    li = sub.add_parser("list", help="list registered datasets")
    li.add_argument("--name", default=None, help="filter by dataset name")
    li.set_defaults(func=cmd_list)

    e = sub.add_parser("export", help="export the lineage graph")
    e.add_argument("ref", nargs="?", default=None, help="dataset ref (default: whole graph)")
    e.add_argument("--format", choices=["dot", "json"], default="dot")
    e.add_argument("--out", default=None, help="write to file instead of stdout")
    e.set_defaults(func=cmd_export)

    st = sub.add_parser("stats", help="show store statistics")
    st.set_defaults(func=cmd_stats)
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LineageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
