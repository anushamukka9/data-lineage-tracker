"""Runnable quickstart: track the provenance of a tiny data pipeline.

Creates two toy CSVs (raw events -> filtered events), registers both,
records the transform, then prints the provenance chain and a GraphViz
DOT rendering of the lineage graph.

Run:  python examples/quickstart.py
"""

import tempfile
from pathlib import Path

from data_lineage_tracker import LineageStore, graph

RAW = """event_id,user,action,amount
1,u1,click,10
2,u2,purchase,250
3,u3,click,5
4,u4,purchase,900
"""

FILTERED = """event_id,user,action,amount
2,u2,purchase,250
4,u4,purchase,900
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        raw_path = tmpdir / "events_raw.csv"
        filtered_path = tmpdir / "events_high_value.csv"
        raw_path.write_text(RAW)
        filtered_path.write_text(FILTERED)

        with LineageStore(tmpdir / "lineage.db") as store:
            raw = store.register_file(
                "events", raw_path, source_uri="s3://demo/raw/events.csv"
            )
            filtered = store.register_file(
                "events_high_value", filtered_path
            )
            store.record_transform(
                raw.id, filtered.id, "filter", {"amount": ">=100"}
            )

            print("=== what produced events_high_value? ===")
            print(graph.format_chain(graph.provenance_chain(store, filtered.id)))
            print()
            print("=== lineage graph (GraphViz DOT) ===")
            print(graph.to_dot(store))
            print()
            print("=== JSON export of the subgraph ===")
            print(graph.to_json(store, filtered.id))


if __name__ == "__main__":
    main()
