"""Tests for hash verification, lineage reports, and JSON dump/load."""

import json
import subprocess
import sys

import pytest

from data_lineage_tracker import (
    LineageReport,
    LineageStore,
    lineage_report,
    verify_chain,
    verify_dataset,
)


@pytest.fixture
def store():
    with LineageStore(":memory:") as s:
        yield s


@pytest.fixture
def raw_csv(tmp_path):
    p = tmp_path / "raw.csv"
    p.write_text("id,value\n1,10\n2,20\n")
    return p


@pytest.fixture
def clean_csv(tmp_path):
    p = tmp_path / "clean.csv"
    p.write_text("id,value\n1,10\n")
    return p


@pytest.fixture
def chain(store, raw_csv, clean_csv):
    raw = store.register_file("raw", raw_csv)
    clean = store.register_file("clean", clean_csv)
    store.record_transform(raw.id, clean.id, "filter", {"value": ">=10"})
    return raw, clean


# ------------------------------------------------------------------ verify
def test_verify_dataset_ok(store, chain, raw_csv):
    raw, _ = chain
    res = verify_dataset(store, raw.id, raw_csv)
    assert res.ok and res.status == "ok"
    assert res.content_ok and res.schema_ok is True
    assert res.actual_hash == res.expected_hash


def test_verify_detects_hash_mismatch(store, chain, raw_csv):
    raw, _ = chain
    raw_csv.write_text("id,value\n1,10\n2,20\n3,999\n")  # modified after registering
    res = verify_dataset(store, raw.id, raw_csv)
    assert not res.ok
    assert res.status == "hash_mismatch"


def test_verify_detects_schema_change(store, chain, tmp_path, raw_csv):
    raw, _ = chain
    renamed = tmp_path / "renamed.csv"
    renamed.write_text("id,amount\n1,10\n2,20\n")  # same rows, renamed column
    res = verify_dataset(store, raw.id, renamed)
    assert res.content_ok is False  # content changed too
    assert res.status == "hash_mismatch"


def test_verify_missing_file_is_a_result_not_an_error(store, chain, tmp_path):
    raw, _ = chain
    res = verify_dataset(store, raw.id, tmp_path / "gone.csv")
    assert not res.ok
    assert res.status == "file_missing"
    assert res.actual_hash is None


def test_verify_schema_only_mismatch(store, tmp_path):
    # A file with identical bytes except a reordered header? Reordering
    # changes the bytes, so instead check a CSV whose content changed but
    # keeps the same columns: schema matches, content does not.
    a = tmp_path / "a.csv"
    a.write_text("x,y\n1,2\n")
    ds = store.register_file("d", a)
    a.write_text("x,y\n1,3\n")
    res = verify_dataset(store, ds.id, a)
    assert res.status == "hash_mismatch"  # content checked first
    assert res.schema_ok is True  # schema still matches


def test_verify_non_csv_has_no_schema_check(store, tmp_path):
    p = tmp_path / "data.bin"
    p.write_bytes(b"\x00\x01\x02\x03")
    ds = store.register_file("blob", p)
    res = verify_dataset(store, ds.id, p)
    assert res.ok and res.schema_ok is None


def test_verify_unknown_dataset_raises(store, tmp_path):
    from data_lineage_tracker.store import LineageError

    with pytest.raises(LineageError):
        verify_dataset(store, "deadbeef", tmp_path / "x.csv")


def test_verify_chain_covers_ancestors(store, chain, raw_csv, clean_csv):
    _, clean = chain
    results = verify_chain(
        store, clean.id, {"raw": str(raw_csv), "clean": str(clean_csv)}
    )
    assert {r.dataset.name for r in results} == {"raw", "clean"}
    assert all(r.ok for r in results)


# ------------------------------------------------------------------ report
def test_lineage_report_structure(store, chain, raw_csv, clean_csv):
    _, clean = chain
    rep = lineage_report(
        store, clean.id, {"raw": str(raw_csv), "clean": str(clean_csv)}
    )
    assert isinstance(rep, LineageReport)
    assert rep.dataset.name == "clean"
    assert [h["dataset"]["name"] for h in rep.provenance] == ["raw", "clean"]
    assert len(rep.downstream) == 0
    assert all(v.ok for v in rep.verifications)


def test_lineage_report_includes_downstream(store, chain):
    raw, clean = chain
    rep = lineage_report(store, raw.id)
    assert len(rep.downstream) == 1
    assert rep.downstream[0]["dataset"]["name"] == "clean"
    assert rep.downstream[0]["derived_via"] == "filter"
    assert rep.downstream[0]["derived_from"] == "raw@v1"


def test_lineage_report_markdown_sections(store, chain, raw_csv, clean_csv):
    _, clean = chain
    md = lineage_report(
        store, clean.id, {"raw": str(raw_csv), "clean": str(clean_csv)}
    ).to_markdown()
    assert md.startswith("# Lineage report: clean@v1")
    assert "## Provenance (what produced this dataset)" in md
    assert "## Downstream (where did it go)" in md
    assert "## Hash verification" in md
    assert "raw@v1" in md and "filter" in md


def test_lineage_report_json_roundtrip(store, chain):
    _, clean = chain
    payload = json.loads(lineage_report(store, clean.id).to_json())
    assert payload["dataset"]["name"] == "clean"
    assert payload["provenance"][0]["dataset"]["name"] == "raw"


# -------------------------------------------------------------- dump / load
def test_dump_load_roundtrip(store, chain, tmp_path):
    dump = tmp_path / "lineage.json"
    summary = store.export_json(dump)
    assert summary == {"datasets": 2, "transforms": 1}

    with LineageStore(":memory:") as fresh:
        loaded = fresh.import_json(dump)
        assert loaded == {"datasets": 2, "transforms": 1, "skipped": 0}
        assert fresh.stats() == {"datasets": 2, "transforms": 1}
        # Provenance survives the round trip.
        clean = fresh.resolve("clean")
        ancestors = fresh.ancestors(clean.id)
        assert [d.name for d in ancestors] == ["raw"]


def test_import_is_idempotent(store, chain, tmp_path):
    dump = tmp_path / "lineage.json"
    store.export_json(dump)
    loaded = store.import_json(dump)
    assert loaded["skipped"] == 3  # 2 datasets + 1 transform
    assert store.stats() == {"datasets": 2, "transforms": 1}


def test_import_conflict_raises(store, chain, tmp_path):
    from data_lineage_tracker.store import LineageError

    dump = tmp_path / "lineage.json"
    store.export_json(dump)
    with LineageStore(":memory:") as fresh:
        fresh.import_json(dump)
        # Re-import the same ids with tampered content: honest conflict.
        data = json.loads(dump.read_text())
        data["datasets"][0]["content_hash"] = "0" * 64
        tampered = tmp_path / "tampered.json"
        tampered.write_text(json.dumps(data))
        with pytest.raises(LineageError):
            fresh.import_json(tampered)


# --------------------------------------------------------------------- cli
def test_cli_verify_report_dump_load(tmp_path, raw_csv, clean_csv):
    db = tmp_path / "lineage.db"
    base = [sys.executable, "-m", "data_lineage_tracker", "--db", str(db)]

    def run(*args):
        r = subprocess.run(base + list(args), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout

    run("register", "raw", "--file", str(raw_csv))
    run("register", "clean", "--file", str(clean_csv))
    run("transform", "--parent", "raw", "--child-id", "clean", "--op", "filter")

    out = run("verify", "clean", "--file", str(clean_csv))
    assert "status:      ok" in out

    file_map = tmp_path / "files.json"
    file_map.write_text(json.dumps({"raw": str(raw_csv), "clean": str(clean_csv)}))
    report_path = tmp_path / "report.md"
    out = run("report", "clean", "--file-map", str(file_map), "--out", str(report_path))
    assert "wrote" in out
    assert "# Lineage report: clean@v1" in report_path.read_text()

    dump_path = tmp_path / "dump.json"
    run("dump", "--out", str(dump_path))
    db2 = tmp_path / "lineage2.db"
    base2 = [sys.executable, "-m", "data_lineage_tracker", "--db", str(db2)]
    r = subprocess.run(
        base2 + ["load", "--in", str(dump_path)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    assert "2 datasets" in r.stdout


def test_cli_verify_fails_on_tampered_file(tmp_path, raw_csv):
    db = tmp_path / "lineage.db"
    base = [sys.executable, "-m", "data_lineage_tracker", "--db", str(db)]
    subprocess.run(
        base + ["register", "raw", "--file", str(raw_csv)],
        capture_output=True, text=True, check=True,
    )
    raw_csv.write_text("id,value\n1,10\n2,20\n3,30\n")  # tamper after registering
    r = subprocess.run(
        base + ["verify", "raw", "--file", str(raw_csv)],
        capture_output=True, text=True,
    )
    assert r.returncode == 1
    assert "hash_mismatch" in r.stdout
