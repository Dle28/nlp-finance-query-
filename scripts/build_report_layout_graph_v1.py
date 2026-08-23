#!/usr/bin/env python3
"""Build a source-bounded document-layout graph from report segments."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.corporate_report_graph import validate_corporate_report_graph  # noqa: E402
from finance_query.report_layout_graph import (  # noqa: E402
    LAYOUT_GRAPH_SOURCE_CONTRACT,
    REPORT_LAYOUT_GRAPH_PROTOCOL,
    REPORT_LAYOUT_GRAPH_VERSION,
    build_report_layout_graph,
    validate_report_layout_graph,
)
from finance_query.report_segments import validate_report_segment_sidecar  # noqa: E402
from finance_query.table_structure import sha256_file  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--graph-dir", type=Path, required=True)
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
    bundle, graph_dir, output_dir = args.bundle_dir.resolve(), args.graph_dir.resolve(), args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError("Output directory already exists; choose a new immutable output path")
    segments_path = bundle / "report_segments_v1.jsonl"
    segment_manifest = validate_report_segment_sidecar(bundle, segments_path)
    graph_manifest = validate_corporate_report_graph(graph_dir)
    report_edges_info = graph_manifest["outputs"]["report_relationship_edges"]
    report_edges_path = graph_dir / report_edges_info["file"]
    profiles, sections, sequence_edges, cross_report_edges = build_report_layout_graph(
        load_jsonl(segments_path), load_jsonl(report_edges_path)
    )
    output_dir.mkdir(parents=True)
    files_and_rows = {
        "profiles": ("report_layout_profiles_v1.jsonl", profiles),
        "sections": ("report_layout_sections_v1.jsonl", sections),
        "sequence_edges": ("report_layout_sequence_edges_v1.jsonl", sequence_edges),
        "cross_report_edges": ("report_layout_cross_report_edges_v1.jsonl", cross_report_edges),
    }
    outputs: dict[str, dict[str, str]] = {}
    for key, (filename, rows) in files_and_rows.items():
        path = output_dir / filename
        atomic_write_jsonl(path, rows)
        outputs[key] = {"file": filename, "sha256": sha256_file(path)}
    manifest = {
        "schema_version": REPORT_LAYOUT_GRAPH_VERSION,
        "protocol": REPORT_LAYOUT_GRAPH_PROTOCOL,
        "profile_count": len(profiles),
        "section_count": len(sections),
        "sequence_edge_count": len(sequence_edges),
        "cross_report_section_edge_count": len(cross_report_edges),
        "inputs": {
            "report_segments": {"path": str(segments_path), "sha256": sha256_file(segments_path)},
            "report_segments_manifest": {
                "path": str(segments_path.with_suffix(".manifest.json")),
                "sha256": sha256_file(segments_path.with_suffix(".manifest.json")),
            },
            "report_graph_manifest": {
                "path": str(graph_dir / "corporate_report_graph_v1.manifest.json"),
                "sha256": sha256_file(graph_dir / "corporate_report_graph_v1.manifest.json"),
            },
            "report_relationship_edges": report_edges_info,
        },
        "segment_normalization_policy": segment_manifest["normalization_policy"],
        "outputs": outputs,
        "source_contract": dict(LAYOUT_GRAPH_SOURCE_CONTRACT),
    }
    atomic_write_json(output_dir / "report_layout_graph_v1.manifest.json", manifest)
    validate_report_layout_graph(output_dir)
    print(json.dumps({"output_dir": str(output_dir), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
