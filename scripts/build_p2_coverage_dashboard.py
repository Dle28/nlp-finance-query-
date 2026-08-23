#!/usr/bin/env python3
"""Build a read-only P2 coverage dashboard across grounding stages."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any


PROTOCOL = "p2_coverage_bottleneck_dashboard_v1"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_status(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(field) or "missing") for row in rows).items()))


def count_ocr_triage(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Support both the legacy flat field and the V1 profile's nested triage."""
    return dict(
        sorted(
            Counter(
                str((row.get("triage") or {}).get("action") or row.get("triage_action") or "missing")
                for row in rows
            ).items()
        )
    )


def top_reasons(rows: list[dict[str, Any]], field: str = "reason_codes", limit: int = 20) -> list[dict[str, Any]]:
    counts = Counter(reason for row in rows for reason in row.get(field) or [])
    return [{"reason": reason, "count": count} for reason, count in counts.most_common(limit)]


def audit_blocker_matrix(rows: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    """Expose reviewable blocked cohorts without changing their provenance."""
    fields = (
        "family",
        "typed_plan_status",
        "formula_evidence_status",
        "independent_critic_status",
        "query_program_status",
    )
    counts = Counter(
        tuple(str(row.get(field) or "missing") for field in fields)
        for row in rows
        if str(row.get("independent_audit_status") or "") == "blocked"
    )
    return [
        {**dict(zip(fields, values)), "count": count}
        for values, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def formula_partial_matrix(rows: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    """Group missing-operand work by an explicit formula contract."""
    counts = Counter(
        (
            str((row.get("formula") or {}).get("formula_id") or "missing"),
            len(row.get("missing_operand_ids") or []),
        )
        for row in rows
        if str(row.get("evidence_completeness") or "") == "partial"
    )
    return [
        {"formula_id": formula_id, "missing_operand_count": missing, "count": count}
        for (formula_id, missing), count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def build_dashboard(
    *,
    typed: Path,
    formula: Path,
    audit: Path,
    ledger: Path,
    census: Path,
    canaries: Path,
    ocr: Path,
    output: Path,
) -> dict[str, Any]:
    typed_rows = load_jsonl(typed)
    formula_rows = load_jsonl(formula)
    audit_rows = load_jsonl(audit)
    ledger_rows = load_jsonl(ledger)
    census_rows = load_jsonl(census)
    canary_rows = load_jsonl(canaries)
    ocr_rows = load_jsonl(ocr)
    by_id = lambda rows, key="id": {int(row[key]): row for row in rows if row.get(key) is not None}
    typed_by_id, formula_by_id, audit_by_id, ledger_by_id = (
        by_id(typed_rows, "question_id"),
        by_id(formula_rows),
        by_id(audit_rows, "question_id"),
        by_id(ledger_rows),
    )
    canary_by_fp = {str(row["fingerprint"]): row for row in canary_rows}
    fp_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in census_rows:
        fp_groups[str(row["fingerprint"])].append(row)
    fingerprint_coverage = []
    for fingerprint, members in sorted(fp_groups.items(), key=lambda item: (-len(item[1]), item[0])):
        ids = [int(row["question_id"]) for row in members]
        fingerprint_coverage.append(
            {
                "fingerprint": fingerprint,
                "question_count": len(ids),
                "typed_complete": sum(typed_by_id.get(q, {}).get("decomposition_status") == "complete" for q in ids),
                "formula_complete": sum(formula_by_id.get(q, {}).get("evidence_completeness") == "complete" for q in ids),
                "audit_passed": sum(audit_by_id.get(q, {}).get("independent_audit_status") == "passed" for q in ids),
                "ledger_grounded": sum(ledger_by_id.get(q, {}).get("execution_status") == "grounded" for q in ids),
                "canary_verdict": canary_by_fp.get(fingerprint, {}).get("verdict"),
            }
        )
    dashboard = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_count": len(typed_rows),
        "typed_plan_counts": count_status(typed_rows, "decomposition_status"),
        "formula_counts": count_status(formula_rows, "evidence_completeness"),
        "audit_counts": count_status(audit_rows, "independent_audit_status"),
        "ledger_counts": count_status(ledger_rows, "execution_status"),
        "ocr_triage_counts": count_ocr_triage(ocr_rows),
        "canary_counts": count_status(canary_rows, "verdict"),
        "top_typed_reasons": top_reasons(typed_rows),
        "top_formula_reasons": top_reasons(formula_rows),
        "top_audit_reasons": top_reasons(audit_rows),
        "audit_blocker_matrix": audit_blocker_matrix(audit_rows),
        "formula_partial_matrix": formula_partial_matrix(formula_rows),
        "fingerprint_count": len(fingerprint_coverage),
        "fingerprint_coverage": fingerprint_coverage,
        "source_contract": {
            "read_only": True,
            "may_change_review_status": False,
            "may_promote_provenance": False,
            "raw_values_preserved": True,
        },
        "inputs": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in {
                "typed": typed,
                "formula": formula,
                "audit": audit,
                "ledger": ledger,
                "census": census,
                "canaries": canaries,
                "ocr": ocr,
            }.items()
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dashboard["sidecar_sha256"] = sha256_file(output)
    output.with_suffix(".manifest.json").write_text(json.dumps({"schema_version": 1, "protocol": PROTOCOL, "sidecar_sha256": dashboard["sidecar_sha256"], "question_count": dashboard["question_count"], "answer_eligible": False, "training_eligible": False, "submission_eligible": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dashboard


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("typed", "formula", "audit", "ledger", "census", "canaries", "ocr"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dashboard = build_dashboard(**{name: getattr(args, name).resolve() for name in ("typed", "formula", "audit", "ledger", "census", "canaries", "ocr")}, output=args.output.resolve())
    print(json.dumps({"output": str(args.output), "question_count": dashboard["question_count"], "fingerprint_count": dashboard["fingerprint_count"], "typed_plan_counts": dashboard["typed_plan_counts"], "audit_counts": dashboard["audit_counts"], "ledger_counts": dashboard["ledger_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
