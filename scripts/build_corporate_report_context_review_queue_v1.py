#!/usr/bin/env python3
"""Build a blank hash-bound human-review queue for Top-5 metadata consensus."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.corporate_report_context_review import (  # noqa: E402
    CONTEXT_REVIEW_SOURCE_CONTRACT,
    CORPORATE_REPORT_CONTEXT_REVIEW_PROTOCOL,
    CORPORATE_REPORT_CONTEXT_REVIEW_VERSION,
    build_corporate_report_context_review_packets,
    load_jsonl,
    require_hash,
    validate_corporate_report_context_review_queue,
)
from finance_query.corporate_report_graph import validate_corporate_report_graph  # noqa: E402
from finance_query.corporate_report_routes import validate_corporate_report_route_hints  # noqa: E402
from finance_query.table_structure import sha256_file  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-items", type=Path, required=True)
    parser.add_argument("--graph-dir", type=Path, required=True)
    parser.add_argument("--route-hints-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


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
    review_items, graph_dir, route_hints_dir, output_dir = (
        args.review_items.resolve(),
        args.graph_dir.resolve(),
        args.route_hints_dir.resolve(),
        args.output_dir.resolve(),
    )
    if not review_items.is_file():
        raise FileNotFoundError("Review items are missing")
    if output_dir.exists():
        raise FileExistsError("Output directory already exists; choose a new immutable output path")
    graph_manifest = validate_corporate_report_graph(graph_dir)
    route_manifest = validate_corporate_report_route_hints(route_hints_dir)
    route_inputs = route_manifest.get("inputs") or {}
    require_hash(
        review_items,
        (route_inputs.get("review_items") or {}).get("sha256"),
        "review items bound by route hints",
    )
    require_hash(
        graph_dir / "corporate_report_graph_v1.manifest.json",
        (route_inputs.get("graph_manifest") or {}).get("sha256"),
        "graph manifest bound by route hints",
    )
    graph_outputs = graph_manifest["outputs"]
    route_hints_path = route_hints_dir / route_manifest["hints_file"]
    packets, exclusions = build_corporate_report_context_review_packets(
        load_jsonl(review_items),
        load_jsonl(graph_dir / graph_outputs["report_families"]["file"]),
        load_jsonl(route_hints_path),
    )
    if not packets:
        raise ValueError("No source-family-confirmed Top-5 metadata-consensus packets found")
    output_dir.mkdir(parents=True)
    queue_path = output_dir / "corporate_report_context_review_queue_v1.jsonl"
    atomic_write_jsonl(queue_path, packets)
    manifest = {
        "schema_version": CORPORATE_REPORT_CONTEXT_REVIEW_VERSION,
        "protocol": CORPORATE_REPORT_CONTEXT_REVIEW_PROTOCOL,
        "queue_status": "blank_human_question_context_review",
        "question_count": len(packets),
        "question_ids": [packet["question_id"] for packet in packets],
        "selection_rule": "top_five_same_ticker_year_scope_and_unique_source_family_scope_member_v1",
        "exclusion_counts": dict(sorted(exclusions.items())),
        "labels_prepopulated": False,
        "materialization_allowed": False,
        "inputs": {
            "review_items": {"path": str(review_items), "sha256": sha256_file(review_items)},
            "graph_manifest": {
                "path": str(graph_dir / "corporate_report_graph_v1.manifest.json"),
                "sha256": sha256_file(graph_dir / "corporate_report_graph_v1.manifest.json"),
            },
            "report_families": graph_outputs["report_families"],
            "route_hints": {"path": str(route_hints_path), "sha256": sha256_file(route_hints_path)},
            "route_hints_manifest": {
                "path": str(route_hints_dir / "corporate_report_route_hints_v1.manifest.json"),
                "sha256": sha256_file(route_hints_dir / "corporate_report_route_hints_v1.manifest.json"),
            },
        },
        "outputs": {"queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)}},
        "source_contract": dict(CONTEXT_REVIEW_SOURCE_CONTRACT),
    }
    atomic_write_json(output_dir / "corporate_report_context_review_queue_v1.manifest.json", manifest)
    validate_corporate_report_context_review_queue(output_dir)
    print(json.dumps({"output_dir": str(output_dir), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
