#!/usr/bin/env python3
"""Merge an approved incremental question file with the frozen questions.csv."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from question_common import QUESTION_COLUMNS, graph_bundle, normalize, read_csv, validate_row, write_csv


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="raw/questions.csv")
    parser.add_argument("--addition", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--place-id")
    args = parser.parse_args()

    base_path, addition_path, output_path = ROOT / args.base, ROOT / args.addition, ROOT / args.output
    if output_path.exists():
        raise SystemExit(f"Không ghi đè output đã tồn tại: {output_path}")
    base, addition = read_csv(base_path), read_csv(addition_path)
    if not base:
        raise SystemExit("File base không có câu hỏi")
    if not addition:
        raise SystemExit("File addition không có câu hỏi")
    if any(row["review_status"] != "approved" for row in base):
        raise SystemExit("File base chứa dòng chưa approved")
    if any(row["review_status"] != "approved" for row in addition):
        raise SystemExit("File addition chứa dòng chưa approved")
    if args.place_id and any(row["primary_place_id"] != args.place_id for row in addition):
        raise SystemExit(f"File addition chứa câu không thuộc {args.place_id}")

    old_places = {row["primary_place_id"] for row in base}
    new_places = {row["primary_place_id"] for row in addition}
    overlap = old_places & new_places
    if overlap:
        raise SystemExit(f"Place đã có trong questions.csv cũ: {sorted(overlap)}")

    combined = base + addition
    errors: list[str] = []
    for field in ("question_id", "question_key"):
        counts = Counter(row[field] for row in combined)
        duplicates = [value for value, count in counts.items() if not value or count > 1]
        if duplicates:
            errors.append(f"{field} trống/trùng: {duplicates[:10]}")

    manifest = read_csv(ROOT / "place_graphs" / "manifest.csv")
    ready = {row["place_id"] for row in manifest if row["status"] == "ready"}
    graph_ids = {row["graph_id"] for row in combined}
    missing_graphs = graph_ids - ready
    if missing_graphs:
        errors.append(f"Graph chưa ready: {sorted(missing_graphs)}")
    bundles = {graph_id: graph_bundle(ROOT / "place_graphs" / graph_id) for graph_id in graph_ids & ready}
    for row in combined:
        if row["graph_id"] in bundles:
            row_errors = validate_row(row, bundles[row["graph_id"]], ready)
            if row_errors:
                errors.append(f"{row['question_id']}: {row_errors}")

    normalized_groups = defaultdict(list)
    for row in combined:
        normalized_groups[normalize(row["question"])].append(row)
    for rows in normalized_groups.values():
        if len(rows) > 1 and len({row["answer_node_ids_json"] for row in rows}) > 1:
            errors.append(f"Cùng câu hỏi nhưng khác đáp án: {[row['question_id'] for row in rows]}")

    if errors:
        for error in errors[:30]:
            print("ERROR:", error)
        raise SystemExit(f"Không merge vì có {len(errors)} lỗi")

    combined.sort(key=lambda row: (row["primary_place_id"], row["question_type"], row["question_id"]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(output_path, combined, QUESTION_COLUMNS)
    report = {
        "base_questions": len(base),
        "added_questions": len(addition),
        "total_questions": len(combined),
        "added_places": sorted(new_places),
        "output": args.output,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
