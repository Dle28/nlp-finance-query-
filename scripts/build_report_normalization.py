#!/usr/bin/env python3
"""Build source-preserving document/table normalization sidecars.

This is the pre-retrieval layer.  It reads immutable bundle tables, V2 and V3
sidecars and writes only navigation metadata.  It never changes report text,
raw grid coordinates, canonical V3 headers, labels, retrieval indexes or
evidence status.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.evidence_context import validate_evidence_context_sidecar  # noqa: E402
from finance_query.report_normalization import (  # noqa: E402
    REPORT_NORMALIZATION_PROTOCOL,
    REPORT_NORMALIZATION_VERSION,
    build_table_normalization_entry,
    catalog_counts,
    document_metadata_rows,
)
from finance_query.table_structure import sha256_file, validate_structure_sidecar  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--structure", type=Path, default=None)
    parser.add_argument("--evidence-context", type=Path, default=None)
    parser.add_argument("--document-output", type=Path, default=None)
    parser.add_argument("--table-output", type=Path, default=None)
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
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def build(
    bundle: Path,
    structure_path: Path,
    context_path: Path,
    document_output: Path,
    table_output: Path,
    *,
    force: bool,
) -> dict[str, Any]:
    bundle = bundle.resolve()
    structure_path = structure_path.resolve()
    context_path = context_path.resolve()
    document_output = document_output.resolve()
    table_output = table_output.resolve()
    if document_output.parent != bundle or table_output.parent != bundle:
        raise ValueError("Normalization sidecars must reside directly in bundle-dir")
    if not force and (document_output.exists() or table_output.exists()):
        raise FileExistsError("Normalization output exists; pass --force to refresh it")
    validate_structure_sidecar(bundle, structure_path)
    validate_evidence_context_sidecar(bundle, structure_path, context_path)
    raw_tables = load_jsonl(bundle / "tables.jsonl")
    structures = load_jsonl(structure_path)
    contexts = load_jsonl(context_path)
    by_raw = {str(row.get("internal_table_uid") or ""): row for row in raw_tables}
    by_structure = {str(row.get("internal_table_uid") or ""): row for row in structures}
    by_context = {str(row.get("internal_table_uid") or ""): row for row in contexts}
    if not by_raw or len(by_raw) != len(raw_tables):
        raise ValueError("Bundle tables must have unique non-empty UIDs")
    if set(by_raw) != set(by_structure) or set(by_raw) != set(by_context):
        raise ValueError("tables/V2/V3 UID coverage mismatch")
    documents = document_metadata_rows(raw_tables)
    document_by_id = {str(row["document_id"]): row for row in documents}
    table_rows = [
        build_table_normalization_entry(
            {**by_raw[uid], **by_structure[uid]},
            by_context[uid],
            document_by_id[str(by_raw[uid].get("document_id") or "")],
        )
        for uid in sorted(by_raw)
    ]
    atomic_write_jsonl(document_output, documents)
    atomic_write_jsonl(table_output, table_rows)
    manifest = {
        "schema_version": REPORT_NORMALIZATION_VERSION,
        "protocol": REPORT_NORMALIZATION_PROTOCOL,
        "input_bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
        "input_structure_sha256": sha256_file(structure_path),
        "input_evidence_context_sha256": sha256_file(context_path),
        "document_count": len(documents),
        "table_count": len(table_rows),
        "document_metadata_file": document_output.name,
        "document_metadata_sha256": sha256_file(document_output),
        "table_catalog_file": table_output.name,
        "table_catalog_sha256": sha256_file(table_output),
        **catalog_counts(table_rows),
        "source_contract": {
            "metadata_only": True,
            "evidence_eligible": False,
            "training_eligible": False,
            "may_repair_ocr": False,
            "may_select_value_cell": False,
        },
    }
    atomic_write_json(table_output.with_suffix(".manifest.json"), manifest)
    return manifest


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    structure = (args.structure or bundle / "tables_structured_v2.jsonl").resolve()
    context = (args.evidence_context or bundle / "tables_evidence_context_v3.jsonl").resolve()
    document_output = (args.document_output or bundle / "document_metadata_v1.jsonl").resolve()
    table_output = (args.table_output or bundle / "table_routing_catalog_v1.jsonl").resolve()
    manifest = build(
        bundle,
        structure,
        context,
        document_output,
        table_output,
        force=args.force,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("Raw reports, V2/V3 grids, retrieval indexes, labels and evidence were not changed.")


if __name__ == "__main__":
    main()
