#!/usr/bin/env python3
"""Audit missing formula operands against raw reports without mutating a bundle.

The output is an audit-only sidecar.  It can prove that a structurally matched
raw source table exists outside a review bundle, but it is never evidence for
an answer, does not create a review candidate, and cannot enter silver labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from finance_query.source_completion import (  # noqa: E402
    operand_is_missing,
    raw_source_candidates,
    source_report_index,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--formula-evidence", type=Path, required=True)
    parser.add_argument(
        "--reports-root",
        type=Path,
        default=ROOT / "data/ViFinQA/financial_statements",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> None:
    args = parse_args()
    bundle = args.bundle_dir.resolve()
    reports_root = args.reports_root.resolve()
    formula_path = args.formula_evidence.resolve()
    output = args.output.resolve()
    tables_path = bundle / "tables.jsonl"
    if not tables_path.is_file():
        raise FileNotFoundError(tables_path)
    if not reports_root.is_dir():
        raise FileNotFoundError(reports_root)

    formulas = load_jsonl(formula_path)
    bundled_uids = {
        str(row["internal_table_uid"])
        for row in load_jsonl(tables_path)
        if row.get("internal_table_uid")
    }
    report_index = source_report_index(reports_root)
    findings: list[dict[str, Any]] = []
    for evidence_set in formulas:
        formula = evidence_set.get("formula") or {}
        for operand in formula.get("operands") or []:
            operand_id = str(operand.get("operand_id") or "")
            if not operand_id or not bool(operand.get("required", True)):
                continue
            if not operand_is_missing(evidence_set, operand_id):
                continue
            finding, candidates = raw_source_candidates(
                operand,
                reports_root=reports_root,
                reports_by_ticker_year=report_index,
                bundled_uids=bundled_uids,
            )
            findings.append(
                {
                    "id": int(evidence_set["id"]),
                    "formula_id": str(formula.get("formula_id") or ""),
                    "operand_id": operand_id,
                    "operand": operand,
                    "audit_finding": finding,
                    "raw_source_candidates": candidates,
                    "audit_only": True,
                    "promotion_allowed": False,
                    "reason": (
                        "Raw-source discovery is not an exact bundle binding and cannot "
                        "produce an answer, review status, or training label."
                    ),
                }
            )

    finding_counts = Counter(str(row["audit_finding"]) for row in findings)
    payload = {
        "schema_version": 1,
        "protocol": "raw_source_completion_audit_v1",
        "audit_only": True,
        "promotion_allowed": False,
        "bundle_tables_sha256": sha256_file(tables_path),
        "formula_evidence_sha256": sha256_file(formula_path),
        "reports_root": str(reports_root),
        "finding_count": len(findings),
        "finding_counts": dict(sorted(finding_counts.items())),
        "findings": findings,
    }
    atomic_json(output, payload)
    print(json.dumps({"output": str(output), **{key: payload[key] for key in ("finding_count", "finding_counts")}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
