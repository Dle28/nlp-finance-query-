#!/usr/bin/env python3
"""Build a source-preserving semantic context sidecar for autonomous review.

The input is the local V2 raw-HTML grid.  This script does not rebuild lexical
or dense retrieval, alter OCR strings/numbers, or mutate the review bundle.
It only derives canonical header paths and conservative quality gates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_query.evidence_context import (  # noqa: E402
    EVIDENCE_CONTEXT_VERSION,
    build_evidence_context,
    evidence_context_manifest_path,
    recover_continuation_headers,
)
from finance_query.table_structure import sha256_file, validate_structure_sidecar  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--structure", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def build_context_sidecar(
    bundle_dir: Path,
    structure_path: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    bundle_dir = bundle_dir.resolve()
    structure_path = structure_path.resolve()
    output_path = output_path.resolve()
    if output_path.exists() and not force:
        raise FileExistsError(f"Output already exists: {output_path}; use --force")
    validate_structure_sidecar(bundle_dir, structure_path)
    structures = load_jsonl(structure_path)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for table in structures:
        uid = str(table.get("internal_table_uid") or "")
        try:
            rows.append(build_evidence_context(table))
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            errors.append({"internal_table_uid": uid, "error": str(exc)})
    if errors:
        sample = "; ".join(error["error"] for error in errors[:3])
        raise RuntimeError(
            f"Refusing partial evidence-context sidecar ({len(errors)} failures): {sample}"
        )
    continuation_recovery = recover_continuation_headers(rows)
    atomic_write_jsonl(output_path, rows)
    quality_counts = Counter(str((row.get("quality") or {}).get("status")) for row in rows)
    manifest = {
        "evidence_context_version": EVIDENCE_CONTEXT_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_bundle_tables_sha256": sha256_file(bundle_dir / "tables.jsonl"),
        "input_structure_path": str(structure_path),
        "input_structure_sha256": sha256_file(structure_path),
        "table_count": len(structures),
        "context_count": len(rows),
        "error_count": len(errors),
        "quality_status_counts": dict(sorted(quality_counts.items())),
        "numeric_binding_policy": "one_reliable_raw_v2_number_per_cell",
        "continuation_header_recovery": continuation_recovery,
        "sidecar_sha256": sha256_file(output_path),
        "errors": errors,
    }
    atomic_write_json(evidence_context_manifest_path(output_path), manifest)
    return manifest


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    structure = args.structure or bundle / "tables_structured_v2.jsonl"
    output = args.output or bundle / "tables_evidence_context_v2.jsonl"
    manifest = build_context_sidecar(bundle, structure, output, force=args.force)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("No Kaggle corpus, lexical index, dense embeddings, or FAISS index was rebuilt.")


if __name__ == "__main__":
    main()
