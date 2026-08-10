#!/usr/bin/env python3
"""Materialize a hash-bound structural census for all review questions."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from analyze_formula_evidence import validate_manifest  # noqa: E402
from finance_query.artifact_registry import sha256_file  # noqa: E402
from finance_query.query_fingerprint import (  # noqa: E402
    QUERY_FINGERPRINT_SCHEMA_VERSION,
    build_query_fingerprint,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--formula-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def build_census(
    bundle: Path,
    output: Path,
    formula_evidence: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    review_items_path = bundle / "review_items.jsonl"
    items = load_jsonl(review_items_path)
    formulas: dict[int, dict[str, Any]] = {}
    formula_manifest_sha: str | None = None
    if formula_evidence is not None:
        validate_manifest(bundle, formula_evidence)
        formula_manifest = formula_evidence.with_suffix(".manifest.json")
        formula_manifest_sha = sha256_file(formula_manifest)
        formulas = {int(row["id"]): row for row in load_jsonl(formula_evidence)}

    ids = [int(item["id"]) for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("review_items.jsonl contains duplicate question ids")
    rows = [
        build_query_fingerprint(item, formula_record=formulas.get(int(item["id"])))
        for item in items
    ]
    route_counts = Counter(str(row["route"]) for row in rows)
    family_counts = Counter(str(row["family"]) for row in rows)
    fingerprint_counts = Counter(str(row["fingerprint"]) for row in rows)
    manifest = {
        "schema_version": QUERY_FINGERPRINT_SCHEMA_VERSION,
        "protocol": "deterministic_question_plan_fingerprint_v1",
        "question_count": len(rows),
        "unique_fingerprint_count": len(fingerprint_counts),
        "route_counts": dict(sorted(route_counts.items())),
        "family_counts": dict(sorted(family_counts.items())),
        "top_fingerprints": fingerprint_counts.most_common(25),
        "bundle_review_items_sha256": sha256_file(review_items_path),
        "formula_evidence_sha256": sha256_file(formula_evidence) if formula_evidence else None,
        "formula_evidence_manifest_sha256": formula_manifest_sha,
        "answer_eligible": False,
        "training_eligible": False,
        "provenance_promotion_allowed": False,
    }
    atomic_jsonl(output, rows)
    manifest["sidecar_sha256"] = sha256_file(output)
    atomic_json(output.with_suffix(".manifest.json"), manifest)
    return rows, manifest


def main() -> None:
    args = parse_args()
    rows, manifest = build_census(
        args.bundle_dir.resolve(),
        args.output.resolve(),
        args.formula_evidence.resolve() if args.formula_evidence else None,
    )
    print(json.dumps({"output": str(args.output), "rows": len(rows), **manifest["route_counts"]}))


if __name__ == "__main__":
    main()

