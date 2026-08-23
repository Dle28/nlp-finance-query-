#!/usr/bin/env python3
"""Build source-heading layout-navigation candidates for known report documents."""
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

from finance_query.corporate_report_routes import validate_corporate_report_route_hints  # noqa: E402
from finance_query.report_layout_graph import validate_report_layout_graph  # noqa: E402
from finance_query.report_layout_routes import (  # noqa: E402
    LAYOUT_ROUTE_HINT_SOURCE_CONTRACT,
    REPORT_LAYOUT_ROUTE_HINT_PROTOCOL,
    REPORT_LAYOUT_ROUTE_HINT_VERSION,
    build_report_layout_navigation_hints,
    validate_report_layout_navigation_hints,
)
from finance_query.table_structure import sha256_file  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-items", type=Path, required=True)
    parser.add_argument("--corporate-route-hints-dir", type=Path, required=True)
    parser.add_argument("--layout-graph-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
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
    review_items, route_dir, layout_dir, output_dir = (
        args.review_items.resolve(),
        args.corporate_route_hints_dir.resolve(),
        args.layout_graph_dir.resolve(),
        args.output_dir.resolve(),
    )
    if not review_items.is_file():
        raise FileNotFoundError("Review items are missing")
    if output_dir.exists():
        raise FileExistsError("Output directory already exists; choose a new immutable output path")
    route_manifest = validate_corporate_report_route_hints(route_dir)
    layout_manifest = validate_report_layout_graph(layout_dir)
    expected_review_hash = ((route_manifest.get("inputs") or {}).get("review_items") or {}).get("sha256")
    if sha256_file(review_items) != expected_review_hash:
        raise ValueError("Review items do not match corporate route-hint lineage")
    route_path = route_dir / route_manifest["hints_file"]
    sections_path = layout_dir / layout_manifest["outputs"]["sections"]["file"]
    hints = build_report_layout_navigation_hints(load_jsonl(review_items), load_jsonl(route_path), load_jsonl(sections_path))
    output_dir.mkdir(parents=True)
    hints_path = output_dir / "report_layout_navigation_hints_v1.jsonl"
    atomic_write_jsonl(hints_path, hints)
    manifest = {
        "schema_version": REPORT_LAYOUT_ROUTE_HINT_VERSION,
        "protocol": REPORT_LAYOUT_ROUTE_HINT_PROTOCOL,
        "question_count": len(hints),
        "status_counts": dict(Counter(str(row["layout_route_hint_status"]) for row in hints)),
        "question_with_layout_candidate_count": sum(bool(row["candidate_sections"]) for row in hints),
        "hints_sha256": sha256_file(hints_path),
        "inputs": {
            "review_items": {"path": str(review_items), "sha256": sha256_file(review_items)},
            "corporate_route_hints": {"path": str(route_path), "sha256": sha256_file(route_path)},
            "corporate_route_hints_manifest": {"path": str(route_dir / "corporate_report_route_hints_v1.manifest.json"), "sha256": sha256_file(route_dir / "corporate_report_route_hints_v1.manifest.json")},
            "layout_graph_manifest": {"path": str(layout_dir / "report_layout_graph_v1.manifest.json"), "sha256": sha256_file(layout_dir / "report_layout_graph_v1.manifest.json")},
            "layout_sections": layout_manifest["outputs"]["sections"],
        },
        "source_contract": dict(LAYOUT_ROUTE_HINT_SOURCE_CONTRACT),
    }
    atomic_write_json(output_dir / "report_layout_navigation_hints_v1.manifest.json", manifest)
    validate_report_layout_navigation_hints(output_dir)
    print(json.dumps({"output_dir": str(output_dir), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
