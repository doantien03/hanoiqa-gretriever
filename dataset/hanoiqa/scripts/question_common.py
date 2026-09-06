import csv
import json
import re
import unicodedata
from pathlib import Path

JSON_COLUMNS = [
    "answers_json", "answer_node_ids_json", "acceptable_answers_json",
    "question_entity_ids_json", "relation_path_json",
    "reasoning_edge_ids_json", "evidence_ids_json", "source_ids_json"
]

QUESTION_COLUMNS = [
    "question_id", "question_key", "primary_place_id", "graph_id",
    "subject_node_id", "question", "answers_json", "answer_node_ids_json",
    "acceptable_answers_json", "question_entity_ids_json", "question_type",
    "answer_type", "hop_count", "relation_path_json",
    "reasoning_edge_ids_json", "evidence_ids_json", "source_ids_json",
    "template_id", "fact_group_id", "generation_method", "difficulty",
    "review_status", "review_note"
]


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows, columns):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load(value):
    return json.loads(value)


def pipe_ids(value):
    return [item.strip() for item in (value or "").split("|") if item.strip()]


def aliases(node):
    values = [node["canonical_name"], *pipe_ids(node.get("aliases", ""))]
    return list(dict.fromkeys(v for v in values if v))


def normalize(text):
    text = unicodedata.normalize("NFC", text).casefold().strip()
    return re.sub(r"\s+", " ", text)


def edge_evidence(edge):
    return pipe_ids(edge.get("evidence_ids", ""))


def graph_bundle(folder):
    folder = Path(folder)
    nodes = read_csv(folder / "nodes.csv")
    edges = read_csv(folder / "edges.csv")
    evidence = read_csv(folder / "evidence.csv")
    sources = read_csv(folder / "sources.csv")
    return {
        "nodes": {r["node_id"]: r for r in nodes},
        "edges": {r["edge_id"]: r for r in edges},
        "evidence": {r["evidence_id"]: r for r in evidence},
        "sources": {r["source_id"]: r for r in sources},
    }


def provenance(edges, evidence_map):
    evidence_ids = []
    for edge in edges:
        evidence_ids.extend(edge_evidence(edge))
    evidence_ids = list(dict.fromkeys(evidence_ids))
    source_ids = sorted({evidence_map[eid]["source_id"] for eid in evidence_ids})
    return evidence_ids, source_ids


def validate_row(row, bundle, ready_place_ids):
    errors = []
    for key in QUESTION_COLUMNS:
        if key not in row:
            errors.append("missing_column:" + key)
    if errors:
        return errors
    parsed = {}
    for key in JSON_COLUMNS:
        try:
            parsed[key] = load(row[key])
            if not isinstance(parsed[key], list):
                errors.append("not_json_list:" + key)
        except Exception:
            errors.append("invalid_json:" + key)
    if errors:
        return errors
    if row["graph_id"] not in ready_place_ids:
        errors.append("graph_not_ready")
    nodes, edges = bundle["nodes"], bundle["edges"]
    if row["subject_node_id"] not in nodes:
        errors.append("missing_subject")
    answer_ids = parsed["answer_node_ids_json"]
    if not answer_ids:
        errors.append("empty_answers")
    if len(answer_ids) != len(parsed["answers_json"]):
        errors.append("answer_name_id_length_mismatch")
    if len(answer_ids) != len(parsed["acceptable_answers_json"]):
        errors.append("answer_alias_length_mismatch")
    if any(node_id not in nodes for node_id in answer_ids):
        errors.append("answer_outside_graph")
    reasoning_ids = parsed["reasoning_edge_ids_json"]
    if any(edge_id not in edges for edge_id in reasoning_ids):
        errors.append("reasoning_edge_outside_graph")
        return errors
    hop = int(row["hop_count"])
    relations = parsed["relation_path_json"]
    if hop not in (1, 2) or len(relations) != hop:
        errors.append("hop_relation_length_mismatch")
    if row["question_type"] == "single_hop" and (hop != 1 or len(reasoning_ids) != 1):
        errors.append("bad_single_hop")
    if hop == 2:
        if len(reasoning_ids) < 2:
            errors.append("missing_two_hop_edges")
        else:
            # At least one valid pair must connect and follow the declared path.
            valid_pair = False
            selected = [edges[x] for x in reasoning_ids]
            for first in selected:
                for second in selected:
                    if (first["target_id"] == second["source_id"] and
                            [first["relation"], second["relation"]] == relations):
                        valid_pair = True
            if not valid_pair:
                errors.append("broken_two_hop_path")
    evidence_ids = set(parsed["evidence_ids_json"])
    source_ids = set(parsed["source_ids_json"])
    for edge_id in reasoning_ids:
        if not set(edge_evidence(edges[edge_id])) & evidence_ids:
            errors.append("missing_evidence_for_edge:" + edge_id)
    for eid in evidence_ids:
        item = bundle["evidence"].get(eid)
        if not item:
            errors.append("evidence_outside_graph:" + eid)
        elif item["source_id"] not in source_ids:
            errors.append("missing_source_for_evidence:" + eid)
    if not row["question"].strip():
        errors.append("empty_question")
    return errors
