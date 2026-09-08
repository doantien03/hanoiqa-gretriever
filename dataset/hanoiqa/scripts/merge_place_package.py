#!/usr/bin/env python3
"""Validate and atomically append one compiled place package to master/."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


FILES = {
    "nodes": ("node_id", "nodes_new.csv"),
    "edges": ("edge_id", "edges_new.csv"),
    "evidence": ("evidence_id", "evidence_new.csv"),
    "sources": ("source_id", "sources_new.csv"),
}


def read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def split_ids(value: str):
    return [x.strip() for x in (value or "").split("|") if x.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    package = args.package.resolve()
    master = root / "master"

    if (package / "MERGED.json").exists():
        raise SystemExit(f"Package đã merge trước đó: {package}")

    old, new, fields = {}, {}, {}
    errors = []
    for name, (id_field, new_name) in FILES.items():
        old[name], fields[name] = read_csv(master / f"{name}.csv")
        new[name], new_fields = read_csv(package / new_name)
        if new_fields != fields[name]:
            errors.append(f"{new_name}: header không khớp master/{name}.csv")
        old_ids = {row[id_field] for row in old[name]}
        new_ids = [row[id_field] for row in new[name]]
        collisions = old_ids.intersection(new_ids)
        if collisions:
            errors.append(f"{new_name}: ID đã tồn tại: {sorted(collisions)[:5]}")
        if len(new_ids) != len(set(new_ids)):
            errors.append(f"{new_name}: có ID trùng trong package")
        not_approved = [row[id_field] for row in new[name] if row.get("review_status") != "approved"]
        if not_approved:
            errors.append(f"{new_name}: chưa approved: {not_approved[:5]}")

    node_rows = old["nodes"] + new["nodes"]
    evidence_rows = old["evidence"] + new["evidence"]
    source_rows = old["sources"] + new["sources"]
    node_map = {row["node_id"]: row for row in node_rows}
    evidence_map = {row["evidence_id"]: row for row in evidence_rows}
    source_map = {row["source_id"]: row for row in source_rows}

    relation_data = json.loads((root / "config" / "relation_schema.json").read_text(encoding="utf-8-sig"))
    relations = {row["relation"]: row for row in relation_data}
    for edge in new["edges"]:
        edge_id = edge["edge_id"]
        if edge["source_id"] not in node_map or edge["target_id"] not in node_map:
            errors.append(f"{edge_id}: source_id/target_id không tồn tại")
            continue
        spec = relations.get(edge["relation"])
        if not spec:
            errors.append(f"{edge_id}: relation không hợp lệ: {edge['relation']}")
            continue
        st, tt = node_map[edge["source_id"]]["type"], node_map[edge["target_id"]]["type"]
        if st not in spec["source_types"] or tt not in spec["target_types"]:
            errors.append(f"{edge_id}: sai domain/range {st}-[{edge['relation']}]->{tt}")
        ev_ids = split_ids(edge["evidence_ids"])
        if spec.get("evidence_required") and not ev_ids:
            errors.append(f"{edge_id}: thiếu evidence")
        for ev_id in ev_ids:
            if ev_id not in evidence_map:
                errors.append(f"{edge_id}: evidence không tồn tại: {ev_id}")

    for ev in new["evidence"]:
        if ev["source_id"] not in source_map:
            errors.append(f"{ev['evidence_id']}: source không tồn tại: {ev['source_id']}")
        place = node_map.get(ev["primary_place_id"])
        if not place or place["type"] != "Place":
            errors.append(f"{ev['evidence_id']}: primary_place_id không phải Place hợp lệ")

    old_urls = {row["url"].strip().lower().rstrip("/") for row in old["sources"]}
    duplicate_urls = [row["url"] for row in new["sources"] if row["url"].strip().lower().rstrip("/") in old_urls]
    if duplicate_urls:
        errors.append(f"sources_new.csv: URL đã có trong master: {duplicate_urls[:5]}")

    if errors:
        print("\n".join(f"ERROR: {x}" for x in errors))
        raise SystemExit(f"Không merge vì có {len(errors)} lỗi.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / "backups" / f"master_before_{package.name}_{stamp}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(master, backup)

    # Write all four complete files to a temporary directory before replacing master files.
    with tempfile.TemporaryDirectory(dir=root) as temp_name:
        temp = Path(temp_name)
        for name in FILES:
            write_csv(temp / f"{name}.csv", old[name] + new[name], fields[name])
        for name in FILES:
            (temp / f"{name}.csv").replace(master / f"{name}.csv")

    receipt = {
        "place_id": package.name,
        "merged_at": datetime.now().isoformat(timespec="seconds"),
        "backup": str(backup),
        "counts": {name: len(new[name]) for name in FILES},
    }
    (package / "MERGED.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
