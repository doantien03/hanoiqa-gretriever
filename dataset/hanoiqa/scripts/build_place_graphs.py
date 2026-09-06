import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "master"
OUTPUT = ROOT / "place_graphs"


def read_csv(name):
    path = MASTER / f"{name}.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), reader.fieldnames


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def approved(row):
    return row["review_status"].strip().lower() == "approved"


def split_ids(value):
    return [
        item.strip()
        for item in value.split("|")
        if item.strip()
    ]


def check_connected(root_id, selected_edges, node_ids):
    adjacency = {node_id: set() for node_id in node_ids}

    for edge in selected_edges:
        source = edge["source_id"]
        target = edge["target_id"]
        adjacency[source].add(target)
        adjacency[target].add(source)

    visited = {root_id}
    pending = [root_id]

    while pending:
        current = pending.pop()
        for neighbor in adjacency[current] - visited:
            visited.add(neighbor)
            pending.append(neighbor)

    return len(node_ids - visited)


nodes, node_columns = read_csv("nodes")
edges, edge_columns = read_csv("edges")
evidence, evidence_columns = read_csv("evidence")
sources, source_columns = read_csv("sources")

node_map = {row["node_id"]: row for row in nodes}
evidence_map = {row["evidence_id"]: row for row in evidence}
source_map = {row["source_id"]: row for row in sources}

# Kiểm tra ID bị trùng.
assert len(node_map) == len(nodes), "Trùng node_id"
assert len(evidence_map) == len(evidence), "Trùng evidence_id"
assert len(source_map) == len(sources), "Trùng source_id"
assert len({e["edge_id"] for e in edges}) == len(edges), "Trùng edge_id"

# Kiểm tra tham chiếu.
for edge in edges:
    assert edge["source_id"] in node_map, edge["edge_id"]
    assert edge["target_id"] in node_map, edge["edge_id"]

    for evidence_id in split_ids(edge["evidence_ids"]):
        assert evidence_id in evidence_map, evidence_id

for item in evidence:
    assert item["source_id"] in source_map, item["evidence_id"]
    assert item["primary_place_id"] in node_map, item["evidence_id"]

places = sorted(
    [
        node for node in nodes
        if node["type"] == "Place" and approved(node)
    ],
    key=lambda row: row["node_id"]
)

# Dừng nếu đã có output, tránh ghi đè graph cũ.
if OUTPUT.exists():
    raise SystemExit(
        f"Đã tồn tại {OUTPUT}. "
        "Hãy đổi tên thư mục này để giữ bản cũ rồi chạy lại."
    )

OUTPUT.mkdir(parents=True)
manifest = []

for place in places:
    place_id = place["node_id"]

    local_evidence_ids = {
        item["evidence_id"]
        for item in evidence
        if item["primary_place_id"] == place_id
        and approved(item)
        and approved(source_map[item["source_id"]])
    }

    selected_edges = []
    used_evidence_ids = set()

    for edge in edges:
        if not approved(edge):
            continue

        if not (
            approved(node_map[edge["source_id"]])
            and approved(node_map[edge["target_id"]])
        ):
            continue

        supporting_ids = [
            evidence_id
            for evidence_id in split_ids(edge["evidence_ids"])
            if evidence_id in local_evidence_ids
        ]

        if not supporting_ids:
            continue

        # Sao chép, không thay đổi bản ghi master.
        exported_edge = dict(edge)

        # Chỉ giữ evidence được xuất cùng graph này.
        exported_edge["evidence_ids"] = "|".join(supporting_ids)

        selected_edges.append(exported_edge)
        used_evidence_ids.update(supporting_ids)

    if not selected_edges:
        manifest.append({
            "place_id": place_id,
            "place_name": place["canonical_name"],
            "node_count": 0,
            "edge_count": 0,
            "evidence_count": 0,
            "source_count": 0,
            "disconnected_nodes": 0,
            "status": "no_place_evidence",
        })
        continue

    selected_node_ids = {place_id}

    for edge in selected_edges:
        selected_node_ids.update([
            edge["source_id"],
            edge["target_id"]
        ])

    selected_nodes = [
        node_map[node_id]
        for node_id in sorted(selected_node_ids)
    ]

    selected_evidence = [
        evidence_map[evidence_id]
        for evidence_id in sorted(used_evidence_ids)
    ]

    used_source_ids = {
        item["source_id"] for item in selected_evidence
    }

    selected_sources = [
        source_map[source_id]
        for source_id in sorted(used_source_ids)
    ]

    disconnected = check_connected(
        place_id, selected_edges, selected_node_ids
    )

    folder = OUTPUT / place_id
    folder.mkdir()

    write_csv(folder / "nodes.csv", selected_nodes, node_columns)
    write_csv(folder / "edges.csv", selected_edges, edge_columns)
    write_csv(
        folder / "evidence.csv",
        selected_evidence,
        evidence_columns
    )
    write_csv(
        folder / "sources.csv",
        selected_sources,
        source_columns
    )

    manifest.append({
        "place_id": place_id,
        "place_name": place["canonical_name"],
        "node_count": len(selected_nodes),
        "edge_count": len(selected_edges),
        "evidence_count": len(selected_evidence),
        "source_count": len(selected_sources),
        "disconnected_nodes": disconnected,
        "status": "ready" if disconnected == 0 else "review_connectivity",
    })

manifest_columns = [
    "place_id", "place_name",
    "node_count", "edge_count",
    "evidence_count", "source_count",
    "disconnected_nodes", "status",
]

write_csv(OUTPUT / "manifest.csv", manifest, manifest_columns)

for row in manifest:
    print(
        row["place_id"],
        row["place_name"],
        f'nodes={row["node_count"]}',
        f'edges={row["edge_count"]}',
        row["status"]
    )

print("\nHoàn thành:", OUTPUT)