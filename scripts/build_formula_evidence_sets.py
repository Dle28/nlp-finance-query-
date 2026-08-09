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
from finance_query.formula_evidence import (  # noqa: E402
    FORMULA_SOURCE_DISCOVERY_POLICY,
    formula_evidence_set,
    source_discovery_candidates,
)
from finance_query.table_structure import validate_structure_sidecar  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--evidence-context",
        type=Path,
        default=None,
        help="Canonical context sidecar; defaults to tables_evidence_context_v2.jsonl in the bundle.",
    )
    parser.add_argument("--max-matches-per-operand", type=int, default=12)
    parser.add_argument(
        "--discover-source-operands",
        action="store_true",
        help=(
            "Expand formula EvidenceSet candidates through immutable tables.jsonl "
            "metadata for resolved ticker(s) and operand years. It never produces answers or labels."
        ),
    )
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
    contexts_path = (args.evidence_context or bundle / "tables_evidence_context_v2.jsonl").resolve()
    if contexts_path.parent != bundle:
        raise ValueError("Formula context sidecar must reside in the review bundle")
    validate_structure_sidecar(bundle, structured)
    validate_evidence_context_sidecar(bundle, structured, contexts_path)
    items = load_jsonl(bundle / "review_items.jsonl")
    source_tables = {
        str(row["internal_table_uid"]): row for row in load_jsonl(bundle / "tables.jsonl")
    }
    structured_tables = {
        str(row["internal_table_uid"]): row for row in load_jsonl(structured)
    }
    if set(source_tables) != set(structured_tables):
        raise ValueError("V2 structured sidecar UID set differs from immutable bundle tables")
    # V2 gives the immutable raw grid/provenance. tables.jsonl contributes only
    # existing document metadata (ticker, scope, report year) for the optional
    # source-discovery join; no OCR content is replaced or inferred.
    tables = {
        uid: {**source_tables[uid], **structured_table}
        for uid, structured_table in structured_tables.items()
    }
    contexts = {str(row["internal_table_uid"]): row for row in load_jsonl(contexts_path)}
    evidence = []
    source_discovery_candidate_count = 0
    for item in items:
        formula = infer_formula_spec(str(item.get("question") or ""))
        if formula is None:
            continue
        candidate_item = item
        if args.discover_source_operands:
            discovered = source_discovery_candidates(item, formula, tables)
            source_discovery_candidate_count += len(discovered)
            candidate_item = {
                **item,
                "candidates": [*(item.get("candidates") or []), *discovered],
                "_formula_source_discovery": {
                    "enabled": True,
                    "policy": FORMULA_SOURCE_DISCOVERY_POLICY,
                    "candidate_count": len(discovered),
                },
            }
        evidence.append(
            formula_evidence_set(
                formula,
                candidate_item,
                tables,
                contexts,
                max_matches_per_operand=args.max_matches_per_operand,
            )
        )
    output = args.output.resolve()
    write_jsonl(output, evidence)
    manifest = {
        "schema_version": 3 if args.discover_source_operands else 2,
        "bundle_review_items_sha256": sha256_file(bundle / "review_items.jsonl"),
        "bundle_tables_sha256": sha256_file(bundle / "tables.jsonl"),
        "structured_tables_sha256": sha256_file(structured),
        "evidence_context_sha256": sha256_file(contexts_path),
        "evidence_context_file": contexts_path.name,
        "evidence_set_count": len(evidence),
        "numeric_binding_policy": "one_reliable_raw_v2_number_per_operand",
        "source_discovery": {
            "enabled": bool(args.discover_source_operands),
            "policy": FORMULA_SOURCE_DISCOVERY_POLICY if args.discover_source_operands else None,
            "candidate_count": source_discovery_candidate_count,
            "source_metadata": "immutable_tables_jsonl_uid_join",
        },
        "completeness_counts": dict(Counter(row["evidence_completeness"] for row in evidence)),
        "sidecar_sha256": sha256_file(output),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "manifest": str(manifest_path),
                **manifest["completeness_counts"],
                "source_discovery_candidates": source_discovery_candidate_count,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
