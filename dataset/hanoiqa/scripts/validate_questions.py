"""Validate draft or approved questions against every referenced place graph."""
import argparse
from collections import Counter, defaultdict
from pathlib import Path

from question_common import (
    QUESTION_COLUMNS, graph_bundle, normalize, read_csv, validate_row, write_csv,
)

ROOT = Path(__file__).resolve().parents[1]
ERROR_COLUMNS = ["question_id", "graph_id", "severity", "error", "question"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="raw/questions_draft.csv")
    args = parser.parse_args()
    path = ROOT / args.input
    rows = read_csv(path)
    manifest = read_csv(ROOT / "place_graphs" / "manifest.csv")
    ready = {r["place_id"] for r in manifest if r["status"] == "ready"}
    bundles = {place_id: graph_bundle(ROOT / "place_graphs" / place_id) for place_id in ready}
    errors = []
    seen_ids, seen_keys = Counter(r.get("question_id", "") for r in rows), Counter(r.get("question_key", "") for r in rows)
    normalized = defaultdict(list)
    for row in rows:
        normalized[normalize(row.get("question", ""))].append(row)
        graph_id = row.get("graph_id")
        if graph_id not in bundles:
            row_errors = ["missing_or_nonready_graph"]
        else:
            row_errors = validate_row(row, bundles[graph_id], ready)
        if seen_ids[row.get("question_id", "")] > 1:
            row_errors.append("duplicate_question_id")
        if seen_keys[row.get("question_key", "")] > 1:
            row_errors.append("duplicate_question_key")
        for error in sorted(set(row_errors)):
            errors.append({"question_id": row.get("question_id", ""), "graph_id": graph_id or "",
                           "severity": "error", "error": error, "question": row.get("question", "")})
    for group in normalized.values():
        if len(group) > 1 and len({r["answer_node_ids_json"] for r in group}) > 1:
            for row in group:
                errors.append({"question_id": row["question_id"], "graph_id": row["graph_id"],
                               "severity": "error", "error": "same_question_different_answers",
                               "question": row["question"]})
    report = ROOT / "reports" / "question_validation_errors.csv"
    write_csv(report, errors, ERROR_COLUMNS)
    print(f"Rows: {len(rows)} | Errors: {len(errors)} | Report: {report}")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
