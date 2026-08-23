#!/usr/bin/env python3
"""Build a deterministic, multi-axis calibration set from source-grounded audits."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


PROTOCOL = "financial_taxonomy_independent_calibration_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def stable_key(record: dict[str, Any]) -> str:
    candidate = record.get("candidate") or {}
    identity = "|".join(
        str(candidate.get(key) or "")
        for key in ("record_kind", "document_id", "internal_table_uid", "row_index")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def risk_band(record: dict[str, Any]) -> str:
    score = int(record.get("risk_score") or 0)
    return "critical" if score >= 150 else "high" if score >= 100 else "standard"


def calibration_stratum(record: dict[str, Any]) -> str:
    candidate = record.get("candidate") or {}
    review_type = str(record.get("review_type") or "unknown")
    if review_type == "row_concept":
        concept_id = str(
            ((candidate.get("concept_candidates") or [{}])[0]).get("concept_id") or "none"
        )
        return "|".join(
            (
                review_type,
                concept_id,
                str(candidate.get("table_type") or "unknown"),
                str(candidate.get("navigation_gate_status") or "unknown"),
            )
        )
    if review_type == "table_role":
        return "|".join(
            (
                review_type,
                str(candidate.get("existing_table_type") or "unknown"),
                str(candidate.get("proposed_table_type") or "none"),
            )
        )
    return "|".join(
        (
            review_type,
            str(candidate.get("sector") or "unknown"),
            str(candidate.get("status") or "unknown"),
        )
    )


def round_robin_strata(
    records: Iterable[dict[str, Any]], *, limit: int
) -> list[dict[str, Any]]:
    groups: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for record in sorted(records, key=stable_key):
        groups[calibration_stratum(record)].append(record)
    selected: list[dict[str, Any]] = []
    active = deque(sorted(groups))
    while active and len(selected) < limit:
        stratum = active.popleft()
        selected.append(groups[stratum].popleft())
        if groups[stratum]:
            active.append(stratum)
    return selected


def review_instructions(review_type: str) -> list[str]:
    common = [
        "Chỉ dùng source_excerpt trong record; không dùng candidate text làm ground truth.",
        "Nếu nguồn không đủ, chọn uncertain/abstain_required thay vì suy đoán.",
    ]
    if review_type == "row_concept":
        return common + [
            "Đánh giá concept semantics độc lập với navigation eligibility.",
            "Kiểm tra raw label, account code, table context, sector và period type.",
        ]
    if review_type == "table_role":
        return common + [
            "Phân biệt primary statement với note, schedule, appendix và restatement.",
            "Concept labels trùng khớp không đủ để xác nhận table role.",
        ]
    return common + [
        "Kiểm tra các tín hiệu có thuộc chính reporting entity hay chỉ là counterparty/note.",
        "Không suy ra sector từ một tín hiệu đơn lẻ.",
    ]


def build_calibration_set(
    *,
    audit_examples: Path,
    audit_manifest: Path,
    output: Path,
    target_count: int = 300,
) -> dict[str, Any]:
    if target_count < 1:
        raise ValueError("target_count must be positive")
    audit_rows = load_jsonl(audit_examples)
    if len({stable_key(row) for row in audit_rows}) != len(audit_rows):
        raise ValueError("Audit examples contain duplicate source identities")
    manifest = json.loads(audit_manifest.read_text(encoding="utf-8"))
    expected = ((manifest.get("outputs") or {}).get("examples") or {}).get("sha256")
    actual = sha256_file(audit_examples)
    if expected != actual:
        raise ValueError("Audit example hash does not match audit manifest")
    if target_count > len(audit_rows):
        raise ValueError("target_count exceeds available source-grounded audit rows")

    mandatory = [row for row in audit_rows if risk_band(row) in {"critical", "high"}]
    mandatory.sort(key=lambda row: (-int(row.get("risk_score") or 0), stable_key(row)))
    if len(mandatory) > target_count:
        raise ValueError("target_count is too small to retain all critical/high records")
    mandatory_ids = {stable_key(row) for row in mandatory}
    standard = [row for row in audit_rows if stable_key(row) not in mandatory_ids]
    selected = mandatory + round_robin_strata(
        standard, limit=target_count - len(mandatory)
    )

    output_rows: list[dict[str, Any]] = []
    for index, record in enumerate(selected, start=1):
        review_type = str(record.get("review_type") or "unknown")
        output_rows.append(
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "calibration_item_id": f"cal-{index:04d}",
                "risk_band": risk_band(record),
                "stratum": calibration_stratum(record),
                "review_type": review_type,
                "risk_score": int(record.get("risk_score") or 0),
                "risk_reason_codes": list(record.get("risk_reason_codes") or []),
                "candidate": record.get("candidate"),
                "source_excerpt": record.get("source_excerpt"),
                "review_instructions": review_instructions(review_type),
                "calibration_labels": {
                    "semantic_correct": None,
                    "table_role_correct": None,
                    "navigation_eligible": None,
                    "source_coordinates_valid": None,
                    "abstain_required": None,
                    "decision": None,
                    "reason_codes": [],
                    "reviewer_notes": None,
                },
                "review_contract": {
                    "allowed_decisions": ["accept", "reject", "uncertain"],
                    "independent_reviews_required": 2,
                    "adjudication_required_on_disagreement": True,
                    "machine_output_may_not_self_calibrate": True,
                },
                "source_contract": {
                    "evidence_eligible": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                    "promotion_allowed": False,
                },
            }
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    risk_counts = Counter(str(row["risk_band"]) for row in output_rows)
    type_counts = Counter(str(row["review_type"]) for row in output_rows)
    stratum_counts = Counter(str(row["stratum"]) for row in output_rows)
    output_manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "selection_policy": "all critical/high then deterministic round-robin standard strata",
        "available_audit_count": len(audit_rows),
        "calibration_count": len(output_rows),
        "risk_band_counts": dict(sorted(risk_counts.items())),
        "review_type_counts": dict(sorted(type_counts.items())),
        "stratum_count": len(stratum_counts),
        "stratum_counts": dict(sorted(stratum_counts.items())),
        "inputs": {
            "audit_examples": {"path": str(audit_examples), "sha256": actual},
            "audit_manifest": {
                "path": str(audit_manifest),
                "sha256": sha256_file(audit_manifest),
            },
        },
        "output": {"path": str(output), "sha256": sha256_file(output)},
        "labels_prepopulated": False,
        "independent_reviews_required": 2,
        "answer_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-examples", type=Path, required=True)
    parser.add_argument("--audit-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-count", type=int, default=300)
    args = parser.parse_args()
    manifest = build_calibration_set(
        audit_examples=args.audit_examples.resolve(),
        audit_manifest=args.audit_manifest.resolve(),
        output=args.output.resolve(),
        target_count=args.target_count,
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
