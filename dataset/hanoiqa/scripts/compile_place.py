#!/usr/bin/env python3
"""Compile one human-friendly place_input.json into four master additions CSV files."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


NODE_FIELDS = [
    "node_id", "canonical_name", "type", "admin_level", "aliases",
    "normalized_key", "numeric_value", "unit", "value_kind", "review_status",
]
EDGE_FIELDS = [
    "edge_id", "source_id", "relation", "target_id", "evidence_ids",
    "confidence", "valid_from", "valid_to", "qualifiers_json", "review_status",
]
EVIDENCE_FIELDS = [
    "evidence_id", "source_id", "primary_place_id", "topic",
    "normalized_text", "text_kind", "review_status",
]
SOURCE_FIELDS = [
    "source_id", "title", "url", "source_type", "retrieved_at",
    "license_note", "review_status",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalized_key(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def list_value(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split("|") if item.strip()]


def reserve_ids(root: Path) -> dict[str, set[int]]:
    """Reserve IDs in master and every compiled package, including unmerged ones."""
    result: dict[str, set[int]] = defaultdict(set)
    paths = list((root / "master").glob("*.csv"))
    paths += list((root / "staging" / "compiled").glob("*/**/*_new.csv"))
    pattern = re.compile(r"^([A-Z]+)_(\d+)$")
    for path in paths:
        try:
            rows = read_csv(path)
        except (OSError, UnicodeError, csv.Error):
            continue
        for row in rows:
            for field in ("node_id", "edge_id", "evidence_id", "source_id"):
                match = pattern.match((row.get(field) or "").strip())
                if match:
                    result[match.group(1)].add(int(match.group(2)))
    return result


def next_id(prefix: str, reserved: dict[str, set[int]]) -> str:
    number = max(reserved[prefix], default=0) + 1
    reserved[prefix].add(number)
    return f"{prefix}_{number:06d}"


def fail(errors: list[str]) -> None:
    if errors:
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
        raise SystemExit(f"Không tạo package vì có {len(errors)} lỗi.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--root", type=Path, help="Thư mục HanoiQA; mặc định là cha của scripts/")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    output_root = (args.output_root or root / "staging" / "compiled").resolve()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))

    # A safe --force recompiles the package previously produced from this exact input.
    # Removing it before ID reservation lets the same IDs be allocated again.
    input_identity = str(args.input.resolve())
    prior_packages = []
    if output_root.exists():
        for meta_path in output_root.glob("*/compile_meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if meta.get("input_path") == input_identity:
                prior_packages.append(meta_path.parent)
    if prior_packages and not args.force:
        raise SystemExit(
            f"Input này đã được compile tại {prior_packages[0]}. "
            "Dùng --force để tạo lại trước khi merge."
        )
    if args.force:
        for old_package in prior_packages:
            if (old_package / "MERGED.json").exists():
                raise SystemExit("Không được --force package đã merge; hãy tạo input cho địa danh mới.")
            shutil.rmtree(old_package)

    entity_schema = json.loads((root / "config" / "entity_types.json").read_text(encoding="utf-8-sig"))
    relation_schema = json.loads((root / "config" / "relation_schema.json").read_text(encoding="utf-8-sig"))
    type_prefix = {item["type"]: item["prefix"] for item in entity_schema}
    relations = {item["relation"]: item for item in relation_schema}

    master_nodes = read_csv(root / "master" / "nodes.csv")
    master_sources = read_csv(root / "master" / "sources.csv")
    master_node_by_id = {row["node_id"]: row for row in master_nodes}
    master_source_by_id = {row["source_id"]: row for row in master_sources}

    node_matches: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in master_nodes:
        keys = {row.get("normalized_key", ""), normalized_key(row["canonical_name"])}
        keys.update(normalized_key(x) for x in list_value(row.get("aliases")))
        for key in keys - {""}:
            node_matches[(row["type"], key)].add(row["node_id"])

    source_matches: dict[str, str] = {}
    for row in master_sources:
        url = canonical_url(row.get("url", ""))
        if url:
            source_matches[url] = row["source_id"]

    errors: list[str] = []
    status = data.get("review_status", "draft").strip().lower()
    if status not in {"draft", "approved", "needs_revision"}:
        errors.append(f"review_status không hợp lệ: {status}")

    raw_nodes = data.get("nodes", [])
    raw_sources = data.get("sources", [])
    raw_evidence = data.get("evidence", [])
    raw_facts = data.get("facts", [])
    place_key = data.get("place_key", "place")

    for collection_name, collection in (
        ("nodes", raw_nodes), ("sources", raw_sources), ("evidence", raw_evidence)
    ):
        keys = [item.get("key", "") for item in collection]
        if "" in keys or len(keys) != len(set(keys)):
            errors.append(f"{collection_name}: key trống hoặc bị trùng")

    reserved = reserve_ids(root)
    node_key_to_id: dict[str, str] = {}
    node_rows: list[dict[str, str]] = []

    # Resolve nodes first, so facts may refer to any local key.
    for item in raw_nodes:
        key = item.get("key", "")
        node_type = item.get("type", "")
        name = str(item.get("canonical_name", "")).strip()
        if node_type not in type_prefix:
            errors.append(f"node {key}: type không có trong entity_types.json: {node_type}")
            continue
        if not name:
            errors.append(f"node {key}: thiếu canonical_name")
            continue

        mode = item.get("match", "new" if key == place_key else "auto")
        explicit_id = item.get("existing_id")
        matches = set()
        if explicit_id:
            row = master_node_by_id.get(explicit_id)
            if not row:
                errors.append(f"node {key}: existing_id không tồn tại: {explicit_id}")
                continue
            if row["type"] != node_type:
                errors.append(f"node {key}: existing_id có type {row['type']}, không phải {node_type}")
                continue
            matches = {explicit_id}
        else:
            candidates = {normalized_key(name)}
            candidates.update(normalized_key(x) for x in list_value(item.get("aliases")))
            for candidate in candidates:
                matches.update(node_matches.get((node_type, candidate), set()))

        if mode == "new":
            if matches:
                errors.append(f"node {key}: yêu cầu new nhưng trùng entity cũ {sorted(matches)}")
                continue
            node_id = next_id(type_prefix[node_type], reserved)
        elif mode in {"auto", "existing"}:
            if len(matches) > 1:
                errors.append(f"node {key}: khớp mơ hồ với {sorted(matches)}; hãy dùng existing_id")
                continue
            if matches:
                node_id = next(iter(matches))
            elif mode == "existing":
                errors.append(f"node {key}: không tìm thấy entity cũ")
                continue
            else:
                node_id = next_id(type_prefix[node_type], reserved)
        else:
            errors.append(f"node {key}: match phải là new, auto hoặc existing")
            continue

        node_key_to_id[key] = node_id
        if node_id not in master_node_by_id:
            node_rows.append({
                "node_id": node_id,
                "canonical_name": name,
                "type": node_type,
                "admin_level": str(item.get("admin_level", "")),
                "aliases": "|".join(list_value(item.get("aliases"))),
                "normalized_key": item.get("normalized_key") or normalized_key(name),
                "numeric_value": str(item.get("numeric_value", "")),
                "unit": str(item.get("unit", "")),
                "value_kind": str(item.get("value_kind", "")),
                "review_status": item.get("review_status", status),
            })

    if place_key not in node_key_to_id:
        errors.append(f"place_key không trỏ tới node hợp lệ: {place_key}")
        place_id = "UNKNOWN"
    else:
        place_id = node_key_to_id[place_key]
        place_type = next((x.get("type") for x in raw_nodes if x.get("key") == place_key), None)
        if place_type != "Place":
            errors.append("node được place_key trỏ tới phải có type=Place")

    # Resolve sources; identical normalized URLs reuse the master source.
    source_key_to_id: dict[str, str] = {}
    source_rows: list[dict[str, str]] = []
    for item in raw_sources:
        key = item.get("key", "")
        url = str(item.get("url", "")).strip()
        if not url.startswith(("https://", "http://")):
            errors.append(f"source {key}: URL không hợp lệ: {url}")
            continue
        explicit_id = item.get("existing_id")
        if explicit_id:
            if explicit_id not in master_source_by_id:
                errors.append(f"source {key}: existing_id không tồn tại: {explicit_id}")
                continue
            source_id = explicit_id
        else:
            source_id = source_matches.get(canonical_url(url), "")
        if not source_id:
            source_id = next_id("SRC", reserved)
            source_rows.append({
                "source_id": source_id,
                "title": str(item.get("title", "")).strip(),
                "url": url,
                "source_type": str(item.get("source_type", "web_page")),
                "retrieved_at": str(item.get("retrieved_at", "")),
                "license_note": str(item.get("license_note", "Bản quyền thuộc nguồn; chỉ lưu bản tóm tắt chuẩn hóa và URL nguồn")),
                "review_status": item.get("review_status", status),
            })
        source_key_to_id[key] = source_id

    # Evidence always gets a fresh ID; it belongs to this place graph.
    evidence_key_to_id: dict[str, str] = {}
    evidence_rows: list[dict[str, str]] = []
    for item in raw_evidence:
        key = item.get("key", "")
        source_ref = item.get("source", "")
        text = str(item.get("normalized_text", "")).strip()
        if source_ref not in source_key_to_id:
            errors.append(f"evidence {key}: source key không tồn tại: {source_ref}")
            continue
        if not text:
            errors.append(f"evidence {key}: normalized_text trống")
            continue
        evidence_id = next_id("EVD", reserved)
        evidence_key_to_id[key] = evidence_id
        evidence_rows.append({
            "evidence_id": evidence_id,
            "source_id": source_key_to_id[source_ref],
            "primary_place_id": place_id,
            "topic": str(item.get("topic", "overview")),
            "normalized_text": text,
            "text_kind": str(item.get("text_kind", "normalized_summary")),
            "review_status": item.get("review_status", status),
        })

    # Build facts after IDs are resolved and check schema domain/range.
    node_type_by_id = {row["node_id"]: row["type"] for row in master_nodes + node_rows}
    edge_rows: list[dict[str, str]] = []
    for index, item in enumerate(raw_facts, start=1):
        source_ref, target_ref = item.get("source"), item.get("target")
        relation = item.get("relation", "")
        if source_ref not in node_key_to_id or target_ref not in node_key_to_id:
            errors.append(f"fact #{index}: source/target key không tồn tại: {source_ref} -> {target_ref}")
            continue
        if relation not in relations:
            errors.append(f"fact #{index}: relation không có trong relation_schema.json: {relation}")
            continue
        source_id, target_id = node_key_to_id[source_ref], node_key_to_id[target_ref]
        source_type, target_type = node_type_by_id[source_id], node_type_by_id[target_id]
        spec = relations[relation]
        if source_type not in spec["source_types"] or target_type not in spec["target_types"]:
            errors.append(
                f"fact #{index}: {source_type}-[{relation}]->{target_type} sai domain/range"
            )
            continue
        evidence_refs = list_value(item.get("evidence"))
        unknown = [key for key in evidence_refs if key not in evidence_key_to_id]
        if unknown:
            errors.append(f"fact #{index}: evidence key không tồn tại: {unknown}")
            continue
        if spec.get("evidence_required") and not evidence_refs:
            errors.append(f"fact #{index}: {relation} bắt buộc có evidence")
            continue
        qualifiers = item.get("qualifiers", {})
        edge_rows.append({
            "edge_id": next_id("EDGE", reserved),
            "source_id": source_id,
            "relation": relation,
            "target_id": target_id,
            "evidence_ids": "|".join(evidence_key_to_id[x] for x in evidence_refs),
            "confidence": f"{float(item.get('confidence', 1.0)):.2f}",
            "valid_from": str(item.get("valid_from", "")),
            "valid_to": str(item.get("valid_to", "")),
            "qualifiers_json": json.dumps(qualifiers, ensure_ascii=False, separators=(",", ":")),
            "review_status": item.get("review_status", status),
        })

    fail(errors)
    package = output_root / place_id
    if package.exists():
        if not args.force:
            raise SystemExit(f"Package đã tồn tại: {package}. Dùng --force nếu muốn tạo lại.")
        shutil.rmtree(package)
    package.mkdir(parents=True)
    write_csv(package / "nodes_new.csv", node_rows, NODE_FIELDS)
    write_csv(package / "edges_new.csv", edge_rows, EDGE_FIELDS)
    write_csv(package / "evidence_new.csv", evidence_rows, EVIDENCE_FIELDS)
    write_csv(package / "sources_new.csv", source_rows, SOURCE_FIELDS)

    id_map = {
        "place_id": place_id,
        "nodes": node_key_to_id,
        "sources": source_key_to_id,
        "evidence": evidence_key_to_id,
    }
    (package / "id_map.json").write_text(
        json.dumps(id_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = {
        "status": "valid",
        "review_status": status,
        "place_id": place_id,
        "new_nodes": len(node_rows),
        "reused_nodes": len(node_key_to_id) - len(node_rows),
        "new_edges": len(edge_rows),
        "new_evidence": len(evidence_rows),
        "new_sources": len(source_rows),
        "reused_sources": len(source_key_to_id) - len(source_rows),
    }
    (package / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (package / "compile_meta.json").write_text(
        json.dumps({"input_path": input_identity, "place_id": place_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Package: {package}")


if __name__ == "__main__":
    main()
