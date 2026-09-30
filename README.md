# data-lineage-tracker

**What produced this dataset?** A lightweight provenance tracker for data
pipelines: content-hashed dataset versions, a transform lineage graph, and
a SQLite-backed CLI — no servers, no dependencies beyond the standard
library.

```bash
pip install -e .
dlt register raw_events --file data/events.csv --source s3://lake/raw/events.csv
dlt transform --parent raw_events --child-name events_clean \
    --child-file data/events_clean.csv --op dedupe --param strategy=drop_exact_dupes
dlt lineage events_clean
```

```
# provenance of events_clean@v1
raw_events@v1 (a1b2c3d4) [1200000 rows x 8 cols]
  events_clean@v1 (e5f6a7b8) [1198743 rows x 8 cols]  <- dedupe(strategy=drop_exact_dupes)
```

## Why

In ML and data engineering, the hardest debugging question is provenance:
*which exact input, transformed how, produced this training set?* Notebooks
and pipeline logs answer it implicitly and ephemerally. `dlt` answers it
explicitly and durably:

- **Content-addressed versions** — each registration stores a SHA-256 hash,
  row/column counts, and a schema fingerprint. Re-registering identical
  bytes is idempotent: you get the existing version back, never a duplicate.
- **Reproducible transforms** — every `parent -> child` edge records the
  operation *and its parameters* (thresholds, seeds, job commits), with
  cycle and self-loop detection.
- **Hash verification** — `dlt verify` re-hashes a file on disk and compares
  it with the recorded hash, catching modified or swapped dataset files
  before a pipeline run.
- **Query, don't grep** — `dlt lineage` walks ancestors oldest-first;
  `dlt downstream` walks derivatives; `dlt report` bundles provenance,
  downstream, and hash verification into one Markdown or JSON report;
  exports go to GraphViz DOT and JSON.
- **One SQLite file** — the whole lineage log is portable, diffable, and
  auditable; `dlt dump` / `dlt load` back it up as JSON. No daemon, no cloud account.

## Install

```bash
git clone https://github.com/anushamukka9/data-lineage-tracker
cd data-lineage-tracker
pip install -e .
```

Requires Python 3.10+. The only runtime dependency is the standard library.

## Quickstart

Run the bundled example (uses a throwaway temp database):

```bash
python examples/quickstart.py
python examples/verify_and_report.py   # verification + full report
```

Or by hand:

```bash
dlt register raw_events --file data/events.csv --source s3://lake/raw/events.csv
dlt transform --parent raw_events --child-name events_clean \
    --child-file data/events_clean.csv --op filter --param amount=">=100"
dlt lineage events_clean          # what produced it?
dlt verify events_clean --file data/events_clean.csv   # hashes still match?
dlt report events_clean --file-map files.json --out report.md   # full audit report
dlt export --format dot --out lineage.dot   # render with graphviz
dlt dump --out lineage-backup.json          # JSON backup of the whole store
```

Full walkthrough: [`docs/usage.md`](docs/usage.md).

## API

```python
from data_lineage_tracker import (
    LineageStore, graph, lineage_report, verify_dataset,
)

with LineageStore("lineage.db") as store:
    raw = store.register_file("raw_events", "data/events.csv")
    clean = store.register_file("events_clean", "data/events_clean.csv")
    store.record_transform(raw.id, clean.id, "dedupe", {"strategy": "drop_exact_dupes"})

    print(graph.format_chain(graph.provenance_chain(store, clean.id)))
    dot = graph.to_dot(store)          # GraphViz DOT
    js = graph.to_json(store, clean.id)  # JSON subgraph

    res = verify_dataset(store, clean.id, "data/events_clean.csv")
    print(res.status)  # "ok", "hash_mismatch", "schema_mismatch", "file_missing"

    report = lineage_report(store, clean.id,
                            files={"raw_events": "data/events.csv",
                                   "events_clean": "data/events_clean.csv"})
    print(report.to_markdown())  # provenance + downstream + verification
```

Dataset references (`dlt lineage <ref>`) accept a full id, an id prefix,
`name@version`, or a bare `name` (latest version).

## Architecture

```
src/data_lineage_tracker/
  hashing.py   content SHA-256, schema fingerprints, CSV stats
  models.py    DatasetVersion / Transform dataclasses
  store.py     LineageStore — SQLite schema, registration, cycle-safe edges,
               ancestor/descendant walks, JSON dump/load
  graph.py     provenance_chain / downstream_chain, DOT + JSON export
  verify.py    verify_dataset / verify_chain — re-hash files on disk
  report.py    lineage_report — full report (provenance, downstream,
               verification) as Markdown or JSON
  cli.py       `dlt` argparse CLI (register, transform, lineage, downstream,
               show, list, export, stats, verify, report, dump, load)
```

`hashing` and `models` are pure (no I/O beyond reading files for hashes);
`store` owns all persistence and integrity rules; `graph` owns queries and
rendering; `verify` and `report` compose them into audits; `cli` is a thin
adapter over the rest.

## Backup and restore

```bash
dlt dump --out lineage-backup.json        # whole store as JSON
dlt --db /tmp/restore.db load --in lineage-backup.json
```

Loading is idempotent: identical entries are skipped, and an id that
already exists with *different* content raises an error instead of
silently overwriting — a backup restore never corrupts the store.

## Development

```bash
pip install -e . pytest
pytest
```

CI runs the suite on Python 3.10–3.12 via
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## License

MIT — see [LICENSE](LICENSE). Copyright 2026 Anusha Mukka
([anushamukka.com](https://anushamukka.com)).
