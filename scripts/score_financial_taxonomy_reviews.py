#!/usr/bin/env python3
"""Validate blind reviews and build an independent adjudication queue."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.build_financial_taxonomy_review_assignments import (
    immutable_review_sha256,
)


PROTOCOL = "financial_taxonomy_interreview_agreement_v1"
ALLOWED_DECISIONS = {"accept", "reject", "uncertain"}
BOOLEAN_AXES = {
    "semantic_correct",
    "table_role_correct",
    "navigation_eligible",
    "source_coordinates_valid",
    "abstain_required",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def by_id(rows: list[dict[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    values = [str(row.get("calibration_item_id") or "") for row in rows]
    if "" in values or len(values) != len(set(values)):
        raise ValueError(f"{label} contains missing or duplicate calibration item IDs")
    return dict(zip(values, rows))


def required_axes(review_type: str) -> tuple[str, ...]:
    common = ("navigation_eligible", "source_coordinates_valid", "abstain_required")
    if review_type == "table_role":
        return ("table_role_correct", *common)
    return ("semantic_correct", *common)


def labels_complete(record: dict[str, Any]) -> bool:
    labels = record.get("calibration_labels") or {}
    decision = labels.get("decision")
    if decision not in ALLOWED_DECISIONS:
        return False
    for axis in required_axes(str(record.get("review_type") or "")):
        if labels.get(axis) not in {True, False}:
            return False
    return True


def validate_review_record(
    record: dict[str, Any],
    *,
    original: dict[str, Any],
    expected_slot: str,
) -> None:
    if str(record.get("reviewer_slot") or "") != expected_slot:
        raise ValueError(f"Unexpected reviewer slot for {record.get('calibration_item_id')}")
    expected_hash = immutable_review_sha256(original)
    if record.get("immutable_review_payload_sha256") != expected_hash:
        raise ValueError(f"Immutable payload hash field mismatch for {record.get('calibration_item_id')}")
    if immutable_review_sha256(record) != expected_hash:
        raise ValueError(f"Reviewer changed immutable source payload for {record.get('calibration_item_id')}")
    labels = record.get("calibration_labels") or {}
    for axis in BOOLEAN_AXES:
        if labels.get(axis) not in {True, False, None}:
            raise ValueError(f"Invalid boolean calibration axis {axis}")
    if labels.get("decision") not in {*ALLOWED_DECISIONS, None}:
        raise ValueError("Invalid calibration decision")
    if not isinstance(labels.get("reason_codes") or [], list):
        raise ValueError("Calibration reason_codes must be a list")


def score_reviews(
    *,
    calibration_set: Path,
    reviewer_a: Path,
    reviewer_b: Path,
    output_summary: Path,
    output_adjudication: Path,
) -> dict[str, Any]:
    original_rows = load_jsonl(calibration_set)
    a_rows = load_jsonl(reviewer_a)
    b_rows = load_jsonl(reviewer_b)
    originals = by_id(original_rows, label="calibration set")
    a_by_id = by_id(a_rows, label="reviewer A")
    b_by_id = by_id(b_rows, label="reviewer B")
    if set(a_by_id) != set(originals) or set(b_by_id) != set(originals):
        raise ValueError("Reviewer files must cover the complete calibration set")
    for item_id, original in originals.items():
        validate_review_record(a_by_id[item_id], original=original, expected_slot="reviewer_a")
        validate_review_record(b_by_id[item_id], original=original, expected_slot="reviewer_b")

    axis_totals: Counter[str] = Counter()
    axis_agreements: Counter[str] = Counter()
    decision_confusion: Counter[str] = Counter()
    completion_counts: Counter[str] = Counter()
    exact_agreement_count = 0
    adjudication: list[dict[str, Any]] = []
    for item_id in sorted(originals):
        original = originals[item_id]
        a = a_by_id[item_id]
        b = b_by_id[item_id]
        a_complete, b_complete = labels_complete(a), labels_complete(b)
        if not a_complete or not b_complete:
            completion_counts[
                "both_incomplete"
                if not a_complete and not b_complete
                else "reviewer_a_incomplete"
                if not a_complete
                else "reviewer_b_incomplete"
            ] += 1
            disagreement_reasons = ["incomplete_independent_review"]
        else:
            completion_counts["both_complete"] += 1
            axes = required_axes(str(original.get("review_type") or ""))
            axis_equal = []
            for axis in axes:
                axis_totals[axis] += 1
                equal = (
                    a["calibration_labels"].get(axis)
                    == b["calibration_labels"].get(axis)
                )
                axis_equal.append(equal)
                if equal:
                    axis_agreements[axis] += 1
            decision_a = str(a["calibration_labels"]["decision"])
            decision_b = str(b["calibration_labels"]["decision"])
            decision_confusion[f"{decision_a}|{decision_b}"] += 1
            decision_equal = decision_a == decision_b
            if all(axis_equal) and decision_equal:
                exact_agreement_count += 1
                disagreement_reasons = []
            else:
                disagreement_reasons = [
                    *(
                        f"axis_disagreement:{axis}"
                        for axis, equal in zip(axes, axis_equal)
                        if not equal
                    ),
                    *([] if decision_equal else ["decision_disagreement"]),
                ]
        if disagreement_reasons:
            adjudication.append(
                {
                    "schema_version": 1,
                    "protocol": PROTOCOL,
                    "calibration_item_id": item_id,
                    "review_type": original.get("review_type"),
                    "stratum": original.get("stratum"),
                    "risk_band": original.get("risk_band"),
                    "candidate": original.get("candidate"),
                    "source_excerpt": original.get("source_excerpt"),
                    "disagreement_reason_codes": disagreement_reasons,
                    "reviewer_a": {
                        "labels": a.get("calibration_labels"),
                        "provenance": a.get("review_provenance"),
                    },
                    "reviewer_b": {
                        "labels": b.get("calibration_labels"),
                        "provenance": b.get("review_provenance"),
                    },
                    "adjudicated_labels": None,
                    "adjudicator_provenance": None,
                    "source_contract": {
                        "evidence_eligible": False,
                        "training_eligible": False,
                        "submission_eligible": False,
                        "promotion_allowed": False,
                    },
                }
            )

    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "calibration_count": len(originals),
        "completion_counts": dict(sorted(completion_counts.items())),
        "axis_agreement": {
            axis: {
                "agree": axis_agreements[axis],
                "total": total,
                "rate": axis_agreements[axis] / total if total else None,
            }
            for axis, total in sorted(axis_totals.items())
        },
        "decision_confusion": dict(sorted(decision_confusion.items())),
        "exact_complete_agreement_count": exact_agreement_count,
        "adjudication_count": len(adjudication),
        "inputs": {
            "calibration_set": {"path": str(calibration_set), "sha256": sha256_file(calibration_set)},
            "reviewer_a": {"path": str(reviewer_a), "sha256": sha256_file(reviewer_a)},
            "reviewer_b": {"path": str(reviewer_b), "sha256": sha256_file(reviewer_b)},
        },
        "calibration_truth_eligible": False,
        "reason": "Inter-review agreement is diagnostic; disagreements require explicit adjudication.",
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }
    output_summary.parent.mkdir(parents=True, exist_ok=True)
    output_adjudication.parent.mkdir(parents=True, exist_ok=True)
    output_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with output_adjudication.open("w", encoding="utf-8") as handle:
        for record in adjudication:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    output_manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "outputs": {
            "summary": {"path": str(output_summary), "sha256": sha256_file(output_summary)},
            "adjudication": {
                "path": str(output_adjudication),
                "sha256": sha256_file(output_adjudication),
            },
        },
        "calibration_truth_eligible": False,
        "promotion_allowed": False,
    }
    output_summary.with_suffix(".manifest.json").write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration-set", type=Path, required=True)
    parser.add_argument("--reviewer-a", type=Path, required=True)
    parser.add_argument("--reviewer-b", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--output-adjudication", type=Path, required=True)
    args = parser.parse_args()
    summary = score_reviews(
        calibration_set=args.calibration_set.resolve(),
        reviewer_a=args.reviewer_a.resolve(),
        reviewer_b=args.reviewer_b.resolve(),
        output_summary=args.output_summary.resolve(),
        output_adjudication=args.output_adjudication.resolve(),
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
