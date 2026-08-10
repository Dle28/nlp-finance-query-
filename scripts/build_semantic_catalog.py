#!/usr/bin/env python3
"""Build a hash-bound, metadata-only table Semantic Catalog from V2/V3."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.evidence_context import validate_evidence_context_sidecar  # noqa: E402
from finance_query.report_segments import validate_report_segment_sidecar  # noqa: E402
from finance_query.semantic_catalog import (  # noqa: E402
    SEMANTIC_CATALOG_PROTOCOL,
    SEMANTIC_CATALOG_VERSION,
    build_semantic_catalog_entry,
    catalog_counts,
    semantic_catalog_manifest_path,
)
from finance_query.table_structure import sha256_file, validate_structure_sidecar  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--structure", type=Path, default=None)
    parser.add_argument("--evidence-context", type=Path, default=None)
    parser.add_argument("--report-segments", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def build_catalog(
    bundle: Path,
    structure_path: Path,
    context_path: Path,
    segment_path: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    bundle = bundle.resolve()
    structure_path = structure_path.resolve()
    context_path = context_path.resolve()
    segment_path = segment_path.resolve()
    output_path = output_path.resolve()
    if output_path.exists() and not force:
        raise FileExistsError(f"Output already exists: {output_path}; use --force")
    validate_structure_sidecar(bundle, structure_path)
    validate_evidence_context_sidecar(bundle, structure_path, context_path)
    validate_report_segment_sidecar(bundle, segment_path)
    structures = load_jsonl(structure_path)
    contexts = load_jsonl(context_path)
    segments = load_jsonl(segment_path)
    by_context = {str(row.get("internal_table_uid") or ""): row for row in contexts}
    by_segment = {str(row.get("internal_table_uid") or ""): row for row in segments}
    if len(by_context) != len(contexts) or len(by_segment) != len(segments):
        raise ValueError("V3/segment inputs must have unique table UIDs")
    if len(structures) != len(by_context) or len(structures) != len(by_segment):
        raise ValueError("V2/V3/segment inputs must each cover the same table count")
    rows = [
        build_semantic_catalog_entry(
            structure,
            by_context[str(structure.get("internal_table_uid") or "")],
            by_segment[str(structure.get("internal_table_uid") or "")],
        )
        for structure in structures
    ]
    atomic_write_jsonl(output_path, rows)
    manifest = {
        "semantic_catalog_version": SEMANTIC_CATALOG_VERSION,
        "protocol": SEMANTIC_CATALOG_PROTOCOL,
        "input_bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
        "input_structure_sha256": sha256_file(structure_path),
        "input_evidence_context_sha256": sha256_file(context_path),
        "input_report_segments_sha256": sha256_file(segment_path),
        "catalog_count": len(rows),
        **catalog_counts(rows),
        "source_contract": {
            "metadata_only": True,
            "evidence_eligible": False,
            "training_eligible": False,
            "may_repair_ocr": False,
            "may_change_candidate_rank": False,
        },
        "sidecar_sha256": sha256_file(output_path),
    }
    atomic_write_json(semantic_catalog_manifest_path(output_path), manifest)
    return manifest


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    structure = args.structure or bundle / "tables_structured_v2.jsonl"
    context = args.evidence_context or bundle / "tables_evidence_context_v3.jsonl"
    segments = args.report_segments or bundle / "report_segments_v1.jsonl"
    output = args.output or bundle / "semantic_catalog_v1.jsonl"
    print(json.dumps(build_catalog(bundle, structure, context, segments, output, force=args.force), ensure_ascii=False, indent=2))
    print("No raw OCR, V2/V3 evidence, retrieval rank, labels, Kaggle corpus, FTS, E5, or FAISS index were changed.")


if __name__ == "__main__":
    main()
