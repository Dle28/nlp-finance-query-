#!/usr/bin/env python3
"""Build fail-closed graph-aware report/table navigation hints for review items."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.corporate_report_graph import validate_corporate_report_graph  # noqa: E402
from finance_query.corporate_report_routes import (  # noqa: E402
    CORPORATE_REPORT_ROUTE_HINT_PROTOCOL,
    CORPORATE_REPORT_ROUTE_HINT_VERSION,
    ROUTE_HINT_SOURCE_CONTRACT,
    build_corporate_report_route_hints,
    validate_corporate_report_route_hints,
)
from finance_query.table_structure import sha256_file  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-items", type=Path, required=True)
    parser.add_argument("--graph-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush(); os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush(); os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> None:
    args = parse_args()
    review_items, graph_dir, output_dir = (
        args.review_items.resolve(), args.graph_dir.resolve(), args.output_dir.resolve()
    )
    if not review_items.is_file():
        raise FileNotFoundError("Review items are missing")
    if output_dir.exists():
        raise FileExistsError("Output directory already exists; choose a new immutable output path")
    graph_manifest = validate_corporate_report_graph(graph_dir)
    outputs = graph_manifest["outputs"]
    families = load_jsonl(graph_dir / outputs["report_families"]["file"])
    report_edges = load_jsonl(graph_dir / outputs["report_relationship_edges"]["file"])
    corporate_edges = load_jsonl(graph_dir / outputs["corporate_relationship_edges"]["file"])
    reviews = load_jsonl(review_items)
    if len({row.get("id") for row in reviews}) != len(reviews):
        raise ValueError("Review items require unique IDs")
    hints = build_corporate_report_route_hints(reviews, families, report_edges, corporate_edges)
    output_dir.mkdir(parents=True)
    hints_path = output_dir / "corporate_report_route_hints_v1.jsonl"
    atomic_write_jsonl(hints_path, hints)
    manifest = {
        "schema_version": CORPORATE_REPORT_ROUTE_HINT_VERSION,
        "protocol": CORPORATE_REPORT_ROUTE_HINT_PROTOCOL,
        "inputs": {
            "review_items": {"path": str(review_items), "sha256": sha256_file(review_items)},
            "graph_manifest": {
                "path": str(graph_dir / "corporate_report_graph_v1.manifest.json"),
                "sha256": sha256_file(graph_dir / "corporate_report_graph_v1.manifest.json"),
            },
            "report_families": outputs["report_families"],
            "report_relationship_edges": outputs["report_relationship_edges"],
            "corporate_relationship_edges": outputs["corporate_relationship_edges"],
        },
        "question_count": len(hints),
        "status_counts": dict(Counter(str(row["route_hint_status"]) for row in hints)),
        "relationship_table_hint_question_count": sum(bool(row["relationship_table_uids"]) for row in hints),
        "hints_file": hints_path.name,
        "hints_sha256": sha256_file(hints_path),
        "source_contract": dict(ROUTE_HINT_SOURCE_CONTRACT),
    }
    atomic_write_json(output_dir / "corporate_report_route_hints_v1.manifest.json", manifest)
    validate_corporate_report_route_hints(output_dir)
    print(json.dumps({"output_dir": str(output_dir), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
