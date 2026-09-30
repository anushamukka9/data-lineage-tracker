"""Runnable example: verify hashes and write a full lineage report.

Builds a tiny three-step pipeline (raw -> filtered -> features) in a
throwaway directory, verifies every file against its registered hash,
prints the Markdown report, and dumps the store to JSON for backup.

Run:  python examples/verify_and_report.py
"""

import json
import tempfile
from pathlib import Path

from data_lineage_tracker import (
    LineageStore,
    lineage_report,
    verify_dataset,
)

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

FEATURES = """user,total_spend,n_purchases
u2,250,1
u4,900,1
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        paths = {}
        for name, body in [
            ("events_raw.csv", RAW),
            ("events_filtered.csv", FILTERED),
            ("features.csv", FEATURES),
        ]:
            p = tmpdir / name
            p.write_text(body)
            paths[name] = p

        with LineageStore(tmpdir / "lineage.db") as store:
            raw = store.register_file("events", paths["events_raw.csv"])
            filtered = store.register_file("events_filtered", paths["events_filtered.csv"])
            features = store.register_file("features", paths["features.csv"])
            store.record_transform(raw.id, filtered.id, "filter", {"amount": ">=100"})
            store.record_transform(
                filtered.id, features.id, "aggregate", {"by": "user"}
            )

            print("=== hash verification ===")
            for ds, p in [(raw, paths["events_raw.csv"]),
                          (filtered, paths["events_filtered.csv"]),
                          (features, paths["features.csv"])]:
                res = verify_dataset(store, ds.id, p)
                print(f"{ds.name}@v{ds.version}: {res.status}")

            print()
            print("=== full lineage report (Markdown) ===")
            rep = lineage_report(
                store,
                features.id,
                files={
                    "events": str(paths["events_raw.csv"]),
                    "events_filtered": str(paths["events_filtered.csv"]),
                    "features": str(paths["features.csv"]),
                },
            )
            print(rep.to_markdown())

            dump = tmpdir / "lineage-backup.json"
            summary = store.export_json(dump)
            print(f"=== JSON backup: {dump.name} "
                  f"({summary['datasets']} datasets, {summary['transforms']} transforms) ===")
            print(json.dumps(json.loads(dump.read_text())["datasets"][0], indent=2)[:400] + "...")


if __name__ == "__main__":
    main()
