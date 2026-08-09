#!/usr/bin/env python3
"""Materialize controlled-formula EvidenceSets from an immutable review bundle."""

from __future__ import annotations

import argparse
import hashlib
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
from finance_query.financial_metrics import infer_formula_spec  # noqa: E402
from finance_query.formula_evidence import formula_evidence_set  # noqa: E402
from finance_query.table_structure import validate_structure_sidecar  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-matches-per-operand", type=int, default=12)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def main() -> None:
    args = parse_args()
    if args.max_matches_per_operand < 1:
        raise ValueError("max-matches-per-operand must be positive")
    bundle = args.bundle_dir.resolve()
    structured = bundle / "tables_structured_v2.jsonl"
    contexts_path = bundle / "tables_evidence_context_v1.jsonl"
    validate_structure_sidecar(bundle, structured)
    validate_evidence_context_sidecar(bundle, structured, contexts_path)
    items = load_jsonl(bundle / "review_items.jsonl")
    tables = {str(row["internal_table_uid"]): row for row in load_jsonl(structured)}
    contexts = {str(row["internal_table_uid"]): row for row in load_jsonl(contexts_path)}
    evidence = []
    for item in items:
        formula = infer_formula_spec(str(item.get("question") or ""))
        if formula is None:
            continue
        evidence.append(
            formula_evidence_set(
                formula,
                item,
                tables,
                contexts,
                max_matches_per_operand=args.max_matches_per_operand,
            )
        )
    output = args.output.resolve()
    write_jsonl(output, evidence)
    manifest = {
        "schema_version": 1,
        "bundle_review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
        "structured_tables_sha256": sha256_file(structured),
        "evidence_context_sha256": sha256_file(contexts_path),
        "evidence_set_count": len(evidence),
        "completeness_counts": dict(Counter(row["evidence_completeness"] for row in evidence)),
        "sidecar_sha256": sha256_file(output),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "manifest": str(manifest_path), **manifest["completeness_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
