#!/usr/bin/env python3
"""Build a non-materializable source-heading supplement for context review."""
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

from finance_query.corporate_report_context_layout import (  # noqa: E402
    CONTEXT_LAYOUT_SUPPLEMENT_SOURCE_CONTRACT,
    CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_PROTOCOL,
    CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_VERSION,
    build_corporate_report_context_layout_supplement,
    validate_corporate_report_context_layout_supplement,
)
from finance_query.corporate_report_context_review import (  # noqa: E402
    load_jsonl,
    validate_corporate_report_context_review_queue,
)
from finance_query.report_layout_graph import validate_report_layout_graph  # noqa: E402
from finance_query.table_structure import sha256_file  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-dir", type=Path, required=True)
    parser.add_argument("--layout-graph-dir", type=Path, required=True)
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
    queue_dir, layout_dir, output_dir = args.queue_dir.resolve(), args.layout_graph_dir.resolve(), args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError("Output directory already exists; choose a new immutable output path")
    queue_manifest = validate_corporate_report_context_review_queue(queue_dir)
    layout_manifest = validate_report_layout_graph(layout_dir)
    queue_path = queue_dir / "corporate_report_context_review_queue_v1.jsonl"
    sections_path = layout_dir / layout_manifest["outputs"]["sections"]["file"]
    supplements = build_corporate_report_context_layout_supplement(load_jsonl(queue_path), load_jsonl(sections_path))
    output_dir.mkdir(parents=True)
    supplement_path = output_dir / "corporate_report_context_layout_supplement_v1.jsonl"
    atomic_write_jsonl(supplement_path, supplements)
    manifest = {
        "schema_version": CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_VERSION,
        "protocol": CORPORATE_REPORT_CONTEXT_LAYOUT_SUPPLEMENT_PROTOCOL,
        "question_count": len(supplements),
        "status_counts": dict(Counter(str(row["layout_supplement_status"]) for row in supplements)),
        "question_with_source_heading_candidate_count": sum(bool(row["source_heading_candidates"]) for row in supplements),
        "materialization_allowed": False,
        "supplement_sha256": sha256_file(supplement_path),
        "inputs": {
            "context_review_queue": {"path": str(queue_path), "sha256": queue_manifest["queue_sha256"]},
            "context_review_queue_manifest": {"path": str(queue_dir / "corporate_report_context_review_queue_v1.manifest.json"), "sha256": sha256_file(queue_dir / "corporate_report_context_review_queue_v1.manifest.json")},
            "layout_graph_manifest": {"path": str(layout_dir / "report_layout_graph_v1.manifest.json"), "sha256": sha256_file(layout_dir / "report_layout_graph_v1.manifest.json")},
            "layout_sections": layout_manifest["outputs"]["sections"],
        },
        "source_contract": dict(CONTEXT_LAYOUT_SUPPLEMENT_SOURCE_CONTRACT),
    }
    atomic_write_json(output_dir / "corporate_report_context_layout_supplement_v1.manifest.json", manifest)
    validate_corporate_report_context_layout_supplement(output_dir)
    print(json.dumps({"output_dir": str(output_dir), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
