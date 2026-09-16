"""Generate HanoiQA draft questions from ready place graphs.

Run from HanoiQA root:
    python scripts/generate_questions.py

The script refuses to overwrite an existing draft unless --force is supplied.
All generated rows remain draft/needs_revision; no row is auto-approved.
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from question_common import (
    QUESTION_COLUMNS, aliases, dump, edge_evidence, graph_bundle,
    normalize, provenance, read_csv, write_csv,
)

ROOT = Path(__file__).resolve().parents[1]

LIST_RELATIONS = {
    "HAS_COMPONENT", "HAS_ACTIVITY", "HAS_CATEGORY", "ASSOCIATED_WITH_PERSON",
    "ASSOCIATED_WITH_DYNASTY", "ASSOCIATED_WITH_PERIOD", "BUILT_WITH",
    "USED_FOR", "HONORS", "SUITABLE_FOR", "HAS_CULTURAL_LAYER", "HAS_EVENT",
    "INVOLVED_PERSON","MANAGED_BY","RECOGNIZED_AS",
}

SINGLE_RELATIONS = {
    "HAS_ADDRESS", "LOCATED_IN", "BUILT_IN", "BUILT_BY", "ESTABLISHED_IN",
    "ESTABLISHED_BY", "RENOVATED_IN", "RENOVATED_BY", "RECOGNIZED_AS",
    "RECOGNIZED_IN", "RECOGNIZED_BY", "MANAGED_BY",
    "HAS_ARCHITECTURAL_STYLE", "HAS_MEASUREMENT", "HELD_ROLE",
}


def qualifier(edge):
    try:
        return json.loads(edge.get("qualifiers_json") or "{}")
    except json.JSONDecodeError:
        return {}


def single_template(edge, subject, target):
    relation, name = edge["relation"], subject["canonical_name"]
    q = qualifier(edge)
    note, status = "", "draft"
    if relation == "HAS_ADDRESS":
        entrance = q.get("entrance")
        if entrance == "main":
            question = f"Lối vào chính của {name} có địa chỉ nào?"
        elif entrance == "rear":
            question = f"Lối vào phía sau của {name} có địa chỉ nào?"
        elif q.get("address_scope") == "official_contact_address_cong_do":
            question = f"Địa chỉ liên hệ tại Cổng Đỏ của {name} là gì?"
        elif q.get("address_precision") in {"islet_and_lake", "square_and_street", "locality_and_commune"}:
            question = f"Theo địa chỉ vị trí trong nguồn, {name} nằm ở đâu?"
            note = "Địa chỉ vị trí, không mặc định là cổng đón khách."
        else:
            question = f"{name} nằm tại địa chỉ hoặc khu vực nào?"
        if q.get("needs_address_verification"):
            status = "needs_revision"
            note += " Nguồn đang mâu thuẫn số nhà; cần xác minh trước khi duyệt."
    elif relation == "LOCATED_IN":
        level = target.get("admin_level")
        if level == "ward": question = f"{name} nằm tại phường nào?"
        elif level == "district": question = f"{name} nằm tại quận hoặc huyện nào?"
        elif level == "city": question = f"{name} nằm tại thành phố nào?"
        elif target["type"] == "Place": question = f"{name} nằm trong địa danh hoặc quần thể nào?"
        else: question = f"{name} nằm tại vị trí nào?"
    elif relation == "BUILT_IN":
        phase = q.get("construction_phase")
        if phase == "start": question = f"{name} được khởi công vào thời điểm nào?"
        elif phase == "completion": question = f"{name} được hoàn thành vào thời điểm nào?"
        elif phase == "construction_interval": question = f"{name} được xây dựng trong khoảng thời gian nào?"
        elif phase in {"initial_construction", "original_construction", "historic_foundation_not_current_fabric", "original_foundation_not_all_surviving_structures", "original_foundation_at_old_riverbank_site"}:
            question = f"{name} được khởi dựng ban đầu vào thời điểm nào?"
        else: question = f"{name} được xây dựng vào thời điểm nào?"
    elif relation == "BUILT_BY":
        if target["type"] == "Dynasty": question = f"{name} được xây dựng dưới triều đại nào?"
        elif target["type"] == "Organization": question = f"Cơ quan hoặc tổ chức nào cho xây dựng {name}?"
        else: question = f"Nhân vật nào cho xây dựng {name}?"
        note = "Kiểm tra phạm vi: cho xây dựng/khởi xướng không nhất thiết là trực tiếp thi công."
    elif relation == "ESTABLISHED_IN": question = f"{name} được thành lập vào thời điểm nào?"
    elif relation == "ESTABLISHED_BY":
        question = f"Cơ quan nào thành lập {name}?" if target["type"] == "Organization" else f"Nhân vật nào thành lập {name}"
    elif relation == "RENOVATED_IN": question = f"{name} được trùng tu hoặc xây dựng lại vào thời điểm nào?"
    elif relation == "RENOVATED_BY": question = f"Ai hoặc đơn vị nào thực hiện việc trùng tu {name}?"
    elif relation == "RECOGNIZED_AS": question = f"{name} đã được công nhận với danh hiệu hoặc di sản nào"
    elif relation == "RECOGNIZED_IN":
        designation = q.get("designation") or q.get("listed_property_name")
        if designation: question = f"{name} được công nhận là {designation} vào thời điểm nào?"
        else:
            question = f"{name} được công nhận vào thời điểm nào?"
            status = "needs_revision"
            note = "Cần kiểm tra danh hiệu tương ứng để tránh mơ hồ."
    elif relation == "RECOGNIZED_BY": question = f"Cơ quan hoạc tổ chức nào công nhận {name}?"
    elif relation == "MANAGED_BY": question = f"Cơ quan hoặc tổ chức nào chịu trách nhiệm quản lý {name}?"
    elif relation == "HAS_ARCHITECTURAL_STYLE": question = f"{name} mang phong cách kiến trúc nào?"
    elif relation == "HELD_ROLE": question = f"{name} giữ vai trò nào?"
    elif relation == "HAS_MEASUREMENT":
        kind = target.get("value_kind") or q.get("measurement_kind") or q.get("measurement_type")
        templates = {
            "area": f"{name} có diện tích bao nhiêu?",
            "buffer_area": f"Vùng đệm của {name} có diện tích bao nhiêu?",
            "height": f"{name} cao bao nhiêu?", "length": f"{name} dài bao nhiêu?",
            "width": f"{name} rộng bao nhiêu?", "depth": f"{name} sâu bao nhiêu?",
            "wall_thickness": f"Tường của {name} dày bao nhiêu?",
            "step_count": f"{name} có bao nhiêu bậc?",
        }
        question = templates.get(kind, f"Số đo {kind or 'chưa xác định'} của {name} là bao nhiêu?")
        if kind not in templates:
            status, note = "needs_revision", "Chưa có template measurement phù hợp với value_kind."
    else:
        return None
    # Known scope issue retained as a review blocker, not silently corrected.
    if edge["edge_id"] == "EDGE_000074":
        status = "needs_revision"
        note += " Evidence nói rồng đá được chạm năm 1467, chưa chứng minh toàn bộ thềm được xây năm đó."
    return question, f"T_1H_{relation}", status, note.strip()


def make_row(key, place_id, subject, question, answer_nodes, qtype, hop,
             relations, edges, evidence_map, template_id, difficulty,
             status="draft", note=""):
    ev_ids, src_ids = provenance(edges, evidence_map)
    answer_types = sorted({node["type"] for node in answer_nodes})
    return {
        "question_id": key, "question_key": key,
        "primary_place_id": place_id, "graph_id": place_id,
        "subject_node_id": subject["node_id"], "question": question,
        "answers_json": dump([n["canonical_name"] for n in answer_nodes]),
        "answer_node_ids_json": dump([n["node_id"] for n in answer_nodes]),
        "acceptable_answers_json": dump([aliases(n) for n in answer_nodes]),
        "question_entity_ids_json": dump([subject["node_id"]]),
        "question_type": qtype, "answer_type": "|".join(answer_types),
        "hop_count": str(hop), "relation_path_json": dump(relations),
        "reasoning_edge_ids_json": dump([e["edge_id"] for e in edges]),
        "evidence_ids_json": dump(ev_ids), "source_ids_json": dump(src_ids),
        "template_id": template_id, "fact_group_id": "FACT_" + key,
        "generation_method": "template", "difficulty": difficulty,
        "review_status": status, "review_note": note,
    }


def generate_place(place_id, folder, templates):
    bundle = graph_bundle(folder)
    nodes, edges = bundle["nodes"], list(bundle["edges"].values())
    evidence = bundle["evidence"]
    rows = []
    # Single-hop: suppress ambiguous duplicate question strings within a graph later.
    for edge in edges:
        if edge["relation"] not in SINGLE_RELATIONS:
            continue
        subject, target = nodes[edge["source_id"]], nodes[edge["target_id"]]
        result = single_template(edge, subject, target)
        if not result:
            continue
        question, template_id, status, note = result
        rows.append(make_row(
            f"HNQ_1H_{place_id}_{edge['edge_id']}", place_id, subject, question,
            [target], "single_hop", 1, [edge["relation"]], [edge], evidence,
            template_id, "easy", status, note,
        ))
    # List: complete with respect to this candidate graph, not necessarily the real world.
    grouped = defaultdict(list)
    for edge in edges:
        if edge["relation"] in LIST_RELATIONS:
            grouped[(edge["source_id"], edge["relation"])].append(edge)
    for (source_id, relation), group in grouped.items():
        unique = {e["target_id"]: e for e in group}
        if len(unique) < 2:
            continue
        subject = nodes[source_id]
        answer_nodes = [nodes[node_id] for node_id in sorted(unique)]
        selected_edges = [unique[node_id] for node_id in sorted(unique)]
        question = templates["list_relations"][relation].format(subject=subject["canonical_name"])
        key = f"HNQ_LS_{place_id}_{source_id}_{relation}"
        rows.append(make_row(key, place_id, subject, question, answer_nodes, "list", 1,
                             [relation], selected_edges, evidence, f"T_LS_{relation}", "easy"))
    # Two-hop: first edge starts at the root place. Group equal questions and answers.
    root = nodes.get(place_id)
    if root:
        outgoing = defaultdict(list)
        for edge in edges:
            outgoing[edge["source_id"]].append(edge)
        for spec in templates["two_hop"]:
            r1, r2 = spec["path"]
            paths = []
            for first in outgoing.get(place_id, []):
                if first["relation"] != r1:
                    continue
                for second in outgoing.get(first["target_id"], []):
                    if second["relation"] == r2:
                        paths.append((first, second))
            grouped_paths = defaultdict(list)
            for first, second in paths:
                if spec["answer_position"] == "middle":
                    clue = nodes[second["target_id"]]
                    answer = nodes[first["target_id"]]
                    group_key = clue["node_id"]
                else:
                    clue = None
                    answer = nodes[second["target_id"]]
                    group_key = "all"
                grouped_paths[group_key].append((first, second, answer, clue))
            for clue_id, group in grouped_paths.items():
                answers_by_id = {item[2]["node_id"]: item[2] for item in group}
                selected_edges = []
                for first, second, _, _ in group:
                    selected_edges.extend([first, second])
                selected_edges = list({e["edge_id"]: e for e in selected_edges}.values())
                clue = group[0][3]
                question = spec["question"].format(
                    root=root["canonical_name"],
                    clue=clue["canonical_name"] if clue else "",
                )
                key = f"HNQ_2H_{place_id}_{spec['template_id']}_{clue_id}"
                row = make_row(key, place_id, root, question,
                               [answers_by_id[x] for x in sorted(answers_by_id)],
                               "multi_hop", 2, spec["path"], selected_edges,
                               evidence, spec["template_id"], "medium")
                entities = [place_id]
                if clue:
                    entities.append(clue["node_id"])
                row["question_entity_ids_json"] = dump(entities)
                rows.append(row)
    # Same normalized question with differing answers is unsafe. Consolidate it
    # into one review-blocked list row so validation has no duplicate oracle.
    collisions = defaultdict(list)
    for row in rows:
        collisions[normalize(row["question"])].append(row)
    remove_ids, replacements = set(), []
    for group in collisions.values():
        answer_sets = {row["answer_node_ids_json"] for row in group}
        if len(answer_sets) > 1:
            base = dict(group[0])
            remove_ids.update(row["question_id"] for row in group)
            answer_nodes, alias_by_id = {}, {}
            reasoning, evidences, sources = [], [], []
            for row in group:
                ids_ = json.loads(row["answer_node_ids_json"])
                names = json.loads(row["answers_json"])
                alias_groups = json.loads(row["acceptable_answers_json"])
                for node_id, name_, alias_group in zip(ids_, names, alias_groups):
                    answer_nodes[node_id] = name_
                    alias_by_id[node_id] = alias_group
                reasoning.extend(json.loads(row["reasoning_edge_ids_json"]))
                evidences.extend(json.loads(row["evidence_ids_json"]))
                sources.extend(json.loads(row["source_ids_json"]))
            ordered = sorted(answer_nodes)
            key = f"HNQ_RV_{place_id}_{group[0]['subject_node_id']}_{json.loads(group[0]['relation_path_json'])[0]}"
            base.update({
                "question_id": key, "question_key": key,
                "answers_json": dump([answer_nodes[x] for x in ordered]),
                "answer_node_ids_json": dump(ordered),
                "acceptable_answers_json": dump([alias_by_id[x] for x in ordered]),
                "question_type": "list",
                "reasoning_edge_ids_json": dump(list(dict.fromkeys(reasoning))),
                "evidence_ids_json": dump(list(dict.fromkeys(evidences))),
                "source_ids_json": dump(sorted(set(sources))),
                "fact_group_id": "FACT_" + key,
                "review_status": "needs_revision",
                "review_note": "Nhiều edge tạo cùng cách hỏi nhưng có đáp án khác.",
            })
            replacements.append(base)
    return [row for row in rows if row["question_id"] not in remove_ids] + replacements


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifest = read_csv(ROOT / "place_graphs" / "manifest.csv")
    ready = [r for r in manifest if r["status"] == "ready"]
    templates = json.loads((ROOT / "config" / "question_templates.json").read_text(encoding="utf-8"))
    rows = []
    for place in ready:
        rows.extend(generate_place(place["place_id"], ROOT / "place_graphs" / place["place_id"], templates))
    # Stable order; IDs are deterministic keys, not sequential positions.
    rows.sort(key=lambda r: (r["primary_place_id"], r["question_type"], r["question_id"]))
    out = ROOT / "raw" / "questions_draft.csv"
    if out.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite {out}; use --force only before human review starts")
    assert len({r["question_id"] for r in rows}) == len(rows)
    write_csv(out, rows, QUESTION_COLUMNS)
    report = {
        "ready_places": len(ready), "draft_questions": len(rows),
        "by_type": dict(Counter(r["question_type"] for r in rows)),
        "by_status": dict(Counter(r["review_status"] for r in rows)),
        "by_place": dict(Counter(r["primary_place_id"] for r in rows)),
        "warning": "All generated rows require human review; needs_revision rows must not be approved unchanged."
    }
    report_path = ROOT / "reports" / "question_generation_summary.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
