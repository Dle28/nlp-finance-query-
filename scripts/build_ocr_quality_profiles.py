#!/usr/bin/env python3
"""Build a hash-bound, read-only OCR quality profile from V2/V3 sidecars."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.evidence_context import validate_evidence_context_sidecar  # noqa: E402
from finance_query.ocr_quality import (  # noqa: E402
    OCR_QUALITY_PROFILE_PROTOCOL,
    OCR_QUALITY_PROFILE_VERSION,
    ocr_quality_manifest_path,
    profile_ocr_quality,
)
from finance_query.table_structure import sha256_file, validate_structure_sidecar  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--structure", type=Path, default=None)
    parser.add_argument("--evidence-context", type=Path, default=None)
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


def build_profiles(
    bundle: Path,
    structure_path: Path,
    context_path: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    bundle = bundle.resolve()
    structure_path = structure_path.resolve()
    context_path = context_path.resolve()
    output_path = output_path.resolve()
    if output_path.exists() and not force:
        raise FileExistsError(f"Output already exists: {output_path}; use --force")
    validate_structure_sidecar(bundle, structure_path)
    validate_evidence_context_sidecar(bundle, structure_path, context_path)
    tables = load_jsonl(structure_path)
    contexts = load_jsonl(context_path)
    context_by_uid = {str(row.get("internal_table_uid") or ""): row for row in contexts}
    if len(context_by_uid) != len(contexts) or len(context_by_uid) != len(tables):
        raise ValueError("V2/V3 profile input must contain one unique context per table UID")
    profiles = [
        profile_ocr_quality(table, context_by_uid[str(table.get("internal_table_uid") or "")])
        for table in tables
    ]
    atomic_write_jsonl(output_path, profiles)
    triage_counts = Counter(str((row.get("triage") or {}).get("action") or "unknown") for row in profiles)
    manifest = {
        "ocr_quality_profile_version": OCR_QUALITY_PROFILE_VERSION,
        "protocol": OCR_QUALITY_PROFILE_PROTOCOL,
        "input_bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
        "input_structure_sha256": sha256_file(structure_path),
        "input_evidence_context_sha256": sha256_file(context_path),
        "profile_count": len(profiles),
        "triage_counts": dict(sorted(triage_counts.items())),
        "source_contract": {
            "raw_values_preserved": True,
            "evidence_eligible": False,
            "training_eligible": False,
            "may_repair_ocr": False,
            "may_change_candidate_rank": False,
        },
        "sidecar_sha256": sha256_file(output_path),
    }
    atomic_write_json(ocr_quality_manifest_path(output_path), manifest)
    return manifest


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    structure = args.structure or bundle / "tables_structured_v2.jsonl"
    context = args.evidence_context or bundle / "tables_evidence_context_v3.jsonl"
    output = args.output or bundle / "ocr_quality_profiles_v1.jsonl"
    print(json.dumps(build_profiles(bundle, structure, context, output, force=args.force), ensure_ascii=False, indent=2))
    print("No OCR strings/numbers, Kaggle corpus, lexical index, dense embeddings, or FAISS index were changed.")


if __name__ == "__main__":
    main()
