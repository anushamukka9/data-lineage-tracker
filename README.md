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
- **Query, don't grep** — `dlt lineage` walks ancestors oldest-first;
  `dlt downstream` walks derivatives; exports go to GraphViz DOT and JSON.
- **One SQLite file** — the whole lineage log is portable, diffable, and
  auditable. No daemon, no cloud account.

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
```

Or by hand:

```bash
dlt register raw_events --file data/events.csv --source s3://lake/raw/events.csv
dlt transform --parent raw_events --child-name events_clean \
    --child-file data/events_clean.csv --op filter --param amount=">=100"
dlt lineage events_clean          # what produced it?
dlt export --format dot --out lineage.dot   # render with graphviz
```

Full walkthrough: [`docs/usage.md`](docs/usage.md).

## API

```python
from data_lineage_tracker import LineageStore, graph

with LineageStore("lineage.db") as store:
    raw = store.register_file("raw_events", "data/events.csv")
    clean = store.register_file("events_clean", "data/events_clean.csv")
    store.record_transform(raw.id, clean.id, "dedupe", {"strategy": "drop_exact_dupes"})

    print(graph.format_chain(graph.provenance_chain(store, clean.id)))
    dot = graph.to_dot(store)          # GraphViz DOT
    js = graph.to_json(store, clean.id)  # JSON subgraph
```

Dataset references (`dlt lineage <ref>`) accept a full id, an id prefix,
`name@version`, or a bare `name` (latest version).

## Architecture

```
src/data_lineage_tracker/
  hashing.py   content SHA-256, schema fingerprints, CSV stats
  models.py    DatasetVersion / Transform dataclasses
  store.py     LineageStore — SQLite schema, registration, cycle-safe edges,
               ancestor/descendant walks
  graph.py     provenance_chain / downstream_chain, DOT + JSON export
  cli.py       `dlt` argparse CLI (register, transform, lineage, downstream,
               show, list, export, stats)
```

`hashing` and `models` are pure (no I/O beyond reading files for hashes);
`store` owns all persistence and integrity rules; `graph` owns queries and
rendering; `cli` is a thin adapter over the other three.

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
