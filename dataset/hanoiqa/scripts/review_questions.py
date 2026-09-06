"""Interactive human review for questions_draft.csv.

Commands: a=approve, r=reject, n=needs_revision, s=skip, q=save and quit.
Rows already marked needs_revision cannot be approved here; edit their question/
scope first, change them to draft, validate, then review again.
"""
import argparse
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

from question_common import QUESTION_COLUMNS, graph_bundle, read_csv, write_csv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="raw/questions_draft.csv")
    parser.add_argument("--place-id")
    parser.add_argument("--type", choices=["single_hop", "list", "multi_hop"])
    args = parser.parse_args()
    path = ROOT / args.input
    rows = read_csv(path)
    candidates = [r for r in rows if r["review_status"] in {"draft", "needs_revision"}]
    if args.place_id:
        candidates = [r for r in candidates if r["primary_place_id"] == args.place_id]
    if args.type:
        candidates = [r for r in candidates if r["question_type"] == args.type]
    bundles = {}
    changed = 0
    for position, row in enumerate(candidates, 1):
        graph_id = row["graph_id"]
        if graph_id not in bundles:
            bundles[graph_id] = graph_bundle(ROOT / "place_graphs" / graph_id)
        bundle = bundles[graph_id]
        evidence_ids = json.loads(row["evidence_ids_json"])
        print("\n" + "=" * 88)
        print(f"[{position}/{len(candidates)}] {row['question_id']} | {graph_id} | {row['question_type']} | {row['review_status']}")
        print("Q:", row["question"])
        print("A:", " | ".join(json.loads(row["answers_json"])))
        print("PATH:", " -> ".join(json.loads(row["relation_path_json"])))
        print("EDGES:", row["reasoning_edge_ids_json"])
        if row["review_note"]:
            print("NOTE:", row["review_note"])
        print("EVIDENCE:")
        for eid in evidence_ids:
            item = bundle["evidence"].get(eid)
            if not item:
                print(" -", eid, "[missing]")
                continue
            source = bundle["sources"].get(item["source_id"], {})
            print(" -", eid, item["normalized_text"])
            print("   ", source.get("url", "[missing source URL]"))
        allowed = "[r]eject [s]kip [q]uit" if row["review_status"] == "needs_revision" else "[a]pprove [r]eject [n]eeds revision [s]kip [q]uit"
        while True:
            choice = input(allowed + ": ").strip().lower()
            if choice in {"a", "r", "n", "s", "q"}:
                if choice == "a" and row["review_status"] == "needs_revision":
                    print("Blocked: edit/fix this row and validate it before approval.")
                    continue
                break
        if choice == "q":
            break
        if choice == "s":
            continue
        status = {"a": "approved", "r": "rejected", "n": "needs_revision"}[choice]
        note = input("Review note (optional): ").strip()
        row["review_status"] = status
        if note:
            row["review_note"] = (row["review_note"] + " " + note).strip()
        changed += 1
    if not changed:
        print("No changes.")
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(path.stem + f".backup_{timestamp}" + path.suffix)
    shutil.copy2(path, backup)
    temp = path.with_suffix(path.suffix + ".tmp")
    write_csv(temp, rows, QUESTION_COLUMNS)
    temp.replace(path)
    print(f"Saved {changed} decisions. Backup: {backup}")


if __name__ == "__main__":
    main()
