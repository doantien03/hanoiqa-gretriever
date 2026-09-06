"""Export only human-approved and structurally valid rows to raw/questions.csv."""
import argparse
from collections import Counter
from pathlib import Path

from question_common import QUESTION_COLUMNS, graph_bundle, read_csv, validate_row, write_csv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="raw/questions_draft.csv")
    parser.add_argument("--output", default="raw/questions.csv")
    args = parser.parse_args()
    rows = read_csv(ROOT / args.input)
    manifest = read_csv(ROOT / "place_graphs" / "manifest.csv")
    ready = {r["place_id"] for r in manifest if r["status"] == "ready"}
    bundles = {pid: graph_bundle(ROOT / "place_graphs" / pid) for pid in ready}
    approved = [r for r in rows if r["review_status"] == "approved"]
    if not approved:
        raise SystemExit("No approved rows. Human review is required before export.")
    failures = []
    for row in approved:
        if row["graph_id"] not in bundles:
            failures.append((row["question_id"], ["graph_not_ready"]))
            continue
        errors = validate_row(row, bundles[row["graph_id"]], ready)
        if errors:
            failures.append((row["question_id"], errors))
    if failures:
        for qid, errors in failures[:20]:
            print(qid, errors)
        raise SystemExit(f"Refusing export: {len(failures)} approved rows are invalid")
    out = ROOT / args.output
    if out.exists():
        raise SystemExit(f"Refusing to overwrite {out}; archive/version the previous approved file first")
    write_csv(out, approved, QUESTION_COLUMNS)
    stats = Counter((r["primary_place_id"], r["question_type"]) for r in approved)
    stat_rows = []
    for place_id in sorted({r["primary_place_id"] for r in approved}):
        counts = {kind: stats[(place_id, kind)] for kind in ["single_hop", "list", "multi_hop"]}
        stat_rows.append({"place_id": place_id, **counts, "total": sum(counts.values())})
    write_csv(ROOT / "reports" / "question_statistics.csv", stat_rows,
              ["place_id", "single_hop", "list", "multi_hop", "total"])
    print(f"Exported {len(approved)} approved questions to {out}")


if __name__ == "__main__":
    main()
