#!/usr/bin/env python3
"""Audit autonomous-review provenance and training readiness.

This script does not create labels or alter review status.  It proves that
every row currently called ``machine_calibrated`` still has a raw V2 metric
identity and a uniquely bound source cell, then reports how far the reviewed
set is from the configured training-pair gate.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
ALLOWED_STATUSES = {
    "machine_calibrated",
    "machine_provisional",
    "needs_human",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine-reviews", type=Path, required=True)
    parser.add_argument(
        "--baseline-machine-reviews",
        type=Path,
        default=None,
        help="Optional prior autonomous-review output for an ID-level delta.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-machine-pairs", type=int, default=200)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL: {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected object: {path}:{line_number}")
            rows.append(row)
    return rows


def rows_by_id(rows: Iterable[dict[str, Any]], source: str) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for row in rows:
        qid = row.get("id")
        if not isinstance(qid, int) or isinstance(qid, bool):
            raise ValueError(f"{source}: autonomous review lacks integer id")
        if qid in output:
            raise ValueError(f"{source}: duplicate Q{qid}")
        status = str(row.get("consensus_status") or "")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"{source}: Q{qid} has unsupported consensus status {status!r}")
        output[qid] = row
    return output


def calibrated_provenance(row: dict[str, Any]) -> dict[str, Any]:
    """Fail closed unless a V4 silver record remains exact-cell grounded."""
    qid = int(row["id"])
    if str(row.get("consensus_status") or "") != "machine_calibrated":
        return {"id": qid, "valid": True, "reason": "not_machine_calibrated"}
    self_review = row.get("machine_self_review") or {}
    selected = self_review.get("selected_assessment") or {}
    identity = selected.get("raw_metric_identity") or {}
    binding = self_review.get("selected_value_binding") or selected.get("value_binding") or {}
    checks = {
        "candidate_uid": bool(row.get("machine_candidate_uid")),
        "training_eligible": bool(self_review.get("training_eligible")),
        "structure_validated": bool((row.get("structure_validation") or {}).get("validated")),
        "exact_raw_metric_identity": bool(identity.get("exact")),
        "cell_bound": str(binding.get("status") or "") == "cell_bound",
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "id": qid,
        "valid": not failed,
        "failed_checks": failed,
        "machine_candidate_uid": row.get("machine_candidate_uid"),
        "family": row.get("family"),
        "candidate_source": row.get("machine_candidate_source"),
        "selection_policy": self_review.get("selection_policy"),
        "value_binding_reason": binding.get("binding_reason"),
    }


def audit_reviews(
    rows: Iterable[dict[str, Any]],
    *,
    baseline_rows: Iterable[dict[str, Any]] | None = None,
    min_machine_pairs: int = 200,
) -> dict[str, Any]:
    if min_machine_pairs < 1:
        raise ValueError("min_machine_pairs must be positive")
    reviewed = rows_by_id(rows, "machine reviews")
    if not reviewed:
        raise ValueError("machine reviews is empty")
    baseline = (
        {} if baseline_rows is None else rows_by_id(baseline_rows, "baseline machine reviews")
    )
    if baseline and set(baseline) != set(reviewed):
        raise ValueError("baseline and current machine-review IDs differ")

    statuses = Counter(str(row["consensus_status"]) for row in reviewed.values())
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    calibrated_sources: Counter[str] = Counter()
    calibrated_policies: Counter[str] = Counter()
    calibrated_proofs: list[dict[str, Any]] = []
    for row in reviewed.values():
        family = str(row.get("family") or "unknown")
        status = str(row["consensus_status"])
        by_family[family][status] += 1
        if status == "machine_calibrated":
            proof = calibrated_provenance(row)
            calibrated_proofs.append(proof)
            calibrated_sources[str(row.get("machine_candidate_source") or "unknown")] += 1
            calibrated_policies[
                str((row.get("machine_self_review") or {}).get("selection_policy") or "unknown")
            ] += 1
    invalid_proofs = [proof for proof in calibrated_proofs if not proof["valid"]]
    if invalid_proofs:
        ids = ", ".join(str(proof["id"]) for proof in invalid_proofs[:12])
        raise ValueError(f"machine_calibrated provenance validation failed for Q{ids}")

    newly_calibrated: list[dict[str, Any]] = []
    status_changes: Counter[str] = Counter()
    if baseline:
        for qid in sorted(reviewed):
            before = str(baseline[qid]["consensus_status"])
            after = str(reviewed[qid]["consensus_status"])
            if before != after:
                status_changes[f"{before}->{after}"] += 1
            if before != "machine_calibrated" and after == "machine_calibrated":
                proof = calibrated_provenance(reviewed[qid])
                newly_calibrated.append(
                    {
                        "id": qid,
                        "family": proof["family"],
                        "candidate_source": proof["candidate_source"],
                        "selection_policy": proof["selection_policy"],
                        "value_binding_reason": proof["value_binding_reason"],
                    }
                )

    calibrated_count = statuses["machine_calibrated"]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "autonomous_review_readiness_audit",
        "question_count": len(reviewed),
        "status_counts": dict(statuses),
        "status_counts_by_family": {
            family: dict(counts) for family, counts in sorted(by_family.items())
        },
        "machine_calibrated_provenance": {
            "validated_count": len(calibrated_proofs),
            "invalid_count": len(invalid_proofs),
            "candidate_source_counts": dict(calibrated_sources),
            "selection_policy_counts": dict(calibrated_policies),
        },
        "training_readiness": {
            "minimum_machine_pairs": min_machine_pairs,
            "available_machine_calibrated_pairs": calibrated_count,
            "remaining_pairs": max(0, min_machine_pairs - calibrated_count),
            "ready": calibrated_count >= min_machine_pairs,
            "reason": (
                "Machine-calibrated pairs meet the configured minimum."
                if calibrated_count >= min_machine_pairs
                else "Do not start dense retriever training from machine silver yet."
            ),
        },
        "comparison": {
            "baseline_supplied": bool(baseline),
            "status_change_counts": dict(status_changes),
            "new_machine_calibrated_count": len(newly_calibrated),
            "new_machine_calibrated": newly_calibrated,
        },
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> None:
    args = parse_args()
    report = audit_reviews(
        load_jsonl(args.machine_reviews.resolve()),
        baseline_rows=(
            None
            if args.baseline_machine_reviews is None
            else load_jsonl(args.baseline_machine_reviews.resolve())
        ),
        min_machine_pairs=args.min_machine_pairs,
    )
    write_json(args.output.resolve(), report)
    print(json.dumps({"output": str(args.output.resolve()), **report["training_readiness"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
