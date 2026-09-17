# Usage guide

This guide walks through `data-lineage-tracker` (`dlt`) from installation
to answering real provenance questions about a data pipeline.

## Install

```bash
pip install data-lineage-tracker
# or from source:
git clone https://github.com/anushamukka9/data-lineage-tracker
cd data-lineage-tracker
pip install -e .
```

`dlt` is also available as `python -m data_lineage_tracker`.

## Concepts

- **Dataset version** — an immutable snapshot of a dataset, pinned by a
  SHA-256 *content hash*. Registering the same bytes twice is idempotent:
  you get the existing version back.
- **Schema fingerprint** — SHA-256 over the sorted column names. It tells
  you whether two versions have the same columns without re-reading data.
- **Transform** — a directed edge `parent -> child` with an operation name
  (`filter`, `join`, `sample`, ...) and its parameters. Cycles and
  self-loops are rejected.
- **References** — most commands accept a dataset reference: a full id, an
  id prefix, `name@version`, or a bare `name` (latest version).

## Workflow: track a pipeline

```bash
# 1. Register raw input
dlt register raw_events --file data/events.csv --source s3://lake/raw/events.csv

# 2. Record a derivation, registering the child at the same time
dlt transform --parent raw_events \
  --child-name events_clean --child-file data/events_clean.csv \
  --op dedupe --param strategy=drop_exact_dupes

# 3. Or link two already-registered datasets
dlt transform --parent events_clean --child-id events_model_ready \
  --op select --param cols=id,user,action

# 4. Ask "what produced this dataset?"
dlt lineage events_model_ready

# 5. Ask "where did this dataset go?"
dlt downstream raw_events

# 6. Export the graph
dlt export --format dot --out lineage.dot      # render with: dot -Tpng lineage.dot -o lineage.png
dlt export events_model_ready --format json    # subgraph as JSON
```

Typical `dlt lineage` output:

```
# provenance of events_model_ready@v1
raw_events@v1 (a1b2c3d4) [1200000 rows x 8 cols]
  events_clean@v1 (e5f6a7b8) [1198743 rows x 8 cols]  <- dedupe(strategy=drop_exact_dupes)
    events_model_ready@v1 (c9d0e1f2) [1198743 rows x 3 cols]  <- select(cols=id,user,action)
```

## The database

State lives in one SQLite file: `$LINEAGE_DB`, `--db <path>`, or
`~/.data-lineage-tracker/lineage.db` by default. Copy the file to share a
lineage log; it has no server and no dependencies beyond the standard
library.

## Python API

```python
from data_lineage_tracker import LineageStore, graph

with LineageStore("lineage.db") as store:
    raw = store.register_file("raw_events", "data/events.csv")
    clean = store.register_file("events_clean", "data/events_clean.csv")
    store.record_transform(raw.id, clean.id, "dedupe", {"strategy": "drop_exact_dupes"})

    chain = graph.provenance_chain(store, clean.id)   # oldest ancestor first
    print(graph.format_chain(chain))
    dot = graph.to_dot(store)                          # GraphViz
    js = graph.to_json(store, clean.id)                # JSON subgraph
```

## Good practices

- Register a dataset **before** the job that consumes it runs, so the
  content hash reflects exactly what was read.
- Put the real transformation parameters (`threshold`, `seed`,
  `commit sha` of the job) in `--param` — lineage should be reproducible,
  not just descriptive.
- Use `--source` to record where a raw dataset came from (`s3://`,
  `https://`, a database DSN). That URI is what an auditor will ask for.
