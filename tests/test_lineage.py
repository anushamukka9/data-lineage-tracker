"""Tests for data-lineage-tracker: hashing, store, graph queries, CLI."""

import json
import sqlite3
import subprocess
import sys

import pytest

from data_lineage_tracker import LineageStore
from data_lineage_tracker import graph as g
from data_lineage_tracker import hashing
from data_lineage_tracker.store import LineageError


@pytest.fixture
def store():
    with LineageStore(":memory:") as s:
        yield s


@pytest.fixture
def csv_a(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("id,name,score\n1,amy,0.9\n2,bo,0.7\n")
    return p


@pytest.fixture
def csv_b(tmp_path):
    p = tmp_path / "b.csv"
    p.write_text("id,name,score\n1,amy,0.9\n")
    return p


# ------------------------------------------------------------------- hashing
def test_content_hash_is_deterministic_and_sensitive(csv_a):
    h1 = hashing.content_hash(csv_a)
    h2 = hashing.content_hash(csv_a)
    assert h1 == h2 and len(h1) == 64
    csv_a.write_text(csv_a.read_text() + "3,cy,0.1\n")
    assert hashing.content_hash(csv_a) != h1


def test_schema_fingerprint_ignores_column_order():
    f1 = hashing.schema_fingerprint(["b", "a", "c"])
    f2 = hashing.schema_fingerprint(["a", "c", "b"])
    assert f1 == f2
    assert hashing.schema_fingerprint(["a", "c"]) != f1


def test_csv_stats(csv_a):
    rows, cols, header = hashing.csv_stats(csv_a)
    assert (rows, cols, header) == (2, 3, ["id", "name", "score"])


def test_csv_stats_rejects_headerless(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("")
    with pytest.raises(ValueError):
        hashing.csv_stats(p)


# --------------------------------------------------------------------- store
def test_register_assigns_versions_and_is_idempotent(store, csv_a, csv_b):
    ds1 = store.register_file("events", csv_a, source_uri="s3://bucket/raw")
    ds2 = store.register_file("events", csv_b)
    assert ds1.version == 1 and ds2.version == 2
    assert ds1.source_uri == "s3://bucket/raw"
    # Same file again -> same version, no duplicate.
    ds1_again = store.register_file("events", csv_a)
    assert ds1_again.id == ds1.id
    assert len(store.list_datasets("events")) == 2


def test_register_missing_file_raises(store, tmp_path):
    with pytest.raises(LineageError):
        store.register_file("nope", tmp_path / "missing.csv")


def test_resolve_refs(store, csv_a):
    ds = store.register_file("events", csv_a)
    assert store.resolve(ds.id).id == ds.id
    assert store.resolve(ds.id[:6]).id == ds.id
    assert store.resolve("events@1").id == ds.id
    assert store.resolve("events").id == ds.id
    with pytest.raises(LineageError):
        store.resolve("does-not-exist")


def test_transform_chain_and_cycle_prevention(store, csv_a, csv_b):
    parent = store.register_file("raw", csv_a)
    child = store.register_file("filtered", csv_b)
    t = store.record_transform(parent.id, child.id, "filter", {"score": ">=0.9"})
    assert t.op == "filter" and t.params == {"score": ">=0.9"}

    ancestors = store.ancestors(child.id)
    assert [d.id for d in ancestors] == [parent.id]
    descendants = store.descendants(parent.id)
    assert [d.id for d in descendants] == [child.id]

    # Cycles and self-loops are refused.
    with pytest.raises(LineageError):
        store.record_transform(child.id, parent.id, "undo")
    with pytest.raises(LineageError):
        store.record_transform(parent.id, parent.id, "identity")


def test_unknown_dataset_in_transform_raises(store, csv_a):
    ds = store.register_file("raw", csv_a)
    with pytest.raises(LineageError):
        store.record_transform(ds.id, "deadbeef", "filter")


# --------------------------------------------------------------------- graph
def test_provenance_chain_ordering(store, csv_a, csv_b, tmp_path):
    csv_c = tmp_path / "c.csv"
    csv_c.write_text("id\n1\n")
    raw = store.register_file("raw", csv_a)
    mid = store.register_file("deduped", csv_b)
    final = store.register_file("final", csv_c)
    store.record_transform(raw.id, mid.id, "dedupe", {})
    store.record_transform(mid.id, final.id, "select", {"cols": "id"})

    chain = g.provenance_chain(store, final.id)
    names = [(h["dataset"]["name"], h["level"]) for h in chain]
    assert names == [("raw", 0), ("deduped", 1), ("final", 2)]
    # The transform that produced each step is attached.
    assert chain[1]["transform"]["op"] == "dedupe"
    assert chain[2]["transform"]["op"] == "select"
    assert chain[0]["transform"] is None

    rendered = g.format_chain(chain)
    assert "raw@v1" in rendered and "dedupe" in rendered


def test_downstream_chain(store, csv_a, csv_b):
    raw = store.register_file("raw", csv_a)
    child = store.register_file("filtered", csv_b)
    store.record_transform(raw.id, child.id, "filter", {})
    chain = g.downstream_chain(store, raw.id)
    assert [h["dataset"]["name"] for h in chain] == ["raw", "filtered"]


def test_dot_export_contains_nodes_and_edges(store, csv_a, csv_b):
    raw = store.register_file("raw", csv_a)
    child = store.register_file("filtered", csv_b)
    store.record_transform(raw.id, child.id, "filter", {"score": ">=0.9"})
    dot = g.to_dot(store)
    assert dot.startswith("digraph lineage {")
    assert raw.id[:8] in dot and child.id[:8] in dot
    assert "filter" in dot and "score=>=0.9" in dot


def test_json_export_roundtrip(store, csv_a, csv_b):
    raw = store.register_file("raw", csv_a)
    child = store.register_file("filtered", csv_b)
    store.record_transform(raw.id, child.id, "filter", {})
    payload = json.loads(g.to_json(store))
    assert len(payload["datasets"]) == 2
    assert len(payload["transforms"]) == 1
    assert payload["transforms"][0]["parent_id"] == raw.id


# ----------------------------------------------------------------------- cli
def test_cli_end_to_end(tmp_path, csv_a, csv_b):
    db = tmp_path / "lineage.db"
    base = [sys.executable, "-m", "data_lineage_tracker", "--db", str(db)]

    def run(*args):
        r = subprocess.run(base + list(args), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout

    run("register", "raw", "--file", str(csv_a), "--source", "s3://bucket/raw")
    run("register", "filtered", "--file", str(csv_b))
    out = run("transform", "--parent", "raw", "--child-id", "filtered",
              "--op", "filter", "--param", "min_score=0.9")
    assert "filter" in out
    out = run("lineage", "filtered")
    assert "raw@v1" in out and "filtered@v1" in out
    out = run("export", "--format", "json")
    assert json.loads(out)["transforms"]
    out = run("stats")
    assert "datasets:   2" in out
    # The database file persists on disk.
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 2
