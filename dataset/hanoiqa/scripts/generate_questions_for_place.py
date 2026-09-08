from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from generate_questions import generate_place
from question_common import QUESTION_COLUMNS, read_csv, write_csv


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--place-id", required=True)
    parser.add_argument("--output")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    place_id = args.place_id.strip()
    manifest = read_csv(ROOT / "place_graphs" / "manifest.csv")
    matches = [row for row in manifest if row["place_id"] == place_id]
    if not matches:
        raise SystemExit(f"Không tìm thấy {place_id} trong place_graphs/manifest.csv")
    if matches[0]["status"] != "ready":
        raise SystemExit(f"Graph {place_id} chưa ready: {matches[0]['status']}")

    graph_dir = ROOT / "place_graphs" / place_id
    for name in ("nodes.csv", "edges.csv", "evidence.csv", "sources.csv"):
        if not (graph_dir / name).exists():
            raise SystemExit(f"Thiếu {graph_dir / name}")

    template_path = ROOT / "config" / "question_templates.json"
    templates = json.loads(template_path.read_text(encoding="utf-8-sig"))
    rows = generate_place(place_id, graph_dir, templates)
    rows.sort(key=lambda row: (row["question_type"], row["question_id"]))
    if not rows:
        raise SystemExit(f"Không sinh được câu hỏi cho {place_id}")
    if len({row["question_id"] for row in rows}) != len(rows):
        raise SystemExit("Generator tạo question_id trùng")
    if any(row["primary_place_id"] != place_id or row["graph_id"] != place_id for row in rows):
        raise SystemExit("Generator tạo dòng không thuộc place được yêu cầu")

    relative_output = args.output or f"raw/questions_draft_{place_id.lower()}.csv"
    output = ROOT / relative_output
    if output.exists() and not args.force:
        raise SystemExit(f"Không ghi đè {output}; dùng --force chỉ khi chưa bắt đầu review")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_csv(output, rows, QUESTION_COLUMNS)

    report = {
        "place_id": place_id,
        "place_name": matches[0]["place_name"],
        "draft_questions": len(rows),
        "by_type": dict(Counter(row["question_type"] for row in rows)),
        "by_status": dict(Counter(row["review_status"] for row in rows)),
        "output": relative_output,
        "warning": "Mọi câu hỏi phải được kiểm duyệt trước khi approve.",
    }
    report_path = ROOT / "reports" / f"question_generation_{place_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
