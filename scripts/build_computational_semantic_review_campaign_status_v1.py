#!/usr/bin/env python3
"""Build a read-only receipt status for a semantic single-reviewer campaign."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


CAMPAIGN_PROTOCOL = "computational_semantic_single_reviewer_campaign_v1"
RECEIPT_PROTOCOL = "computational_semantic_human_review_receipt_v1"
PROTOCOL = "computational_semantic_single_reviewer_campaign_status_v1"
SOURCE_CONTRACT = {
    "candidate_only": True,
    "evidence_eligible": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
    "materialization_allowed": False,
    "may_compute_answer": False,
    "may_select_value_cell": False,
}
PHASES = (
    ("p1_direct_source_coordinates", "source_coordinate", "source_receipt"),
    ("p2_scope_resolution", "scope", "scope_receipt"),
    ("p3_component_source_coordinates", "source_coordinate", "component_receipt"),
    ("p4_dimension_contracts", "dimension", "dimension_receipt"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSON objects in {path}")
    return rows


def _validate_campaign(campaign: Path, campaign_manifest: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = load_json(campaign_manifest)
    if (
        manifest.get("protocol") != CAMPAIGN_PROTOCOL
        or manifest.get("campaign_status") != "blank_single_reviewer_worklist"
        or manifest.get("review_decisions_prepopulated") is not False
        or manifest.get("requires_human_decisions") is not True
        or manifest.get("materialization_allowed") is not False
        or manifest.get("source_contract") != SOURCE_CONTRACT
        or ((manifest.get("outputs") or {}).get("worklist") or {}).get("sha256") != sha256_file(campaign)
    ):
        raise ValueError("Campaign manifest is not a blank non-materializable campaign")
    rows = load_jsonl(campaign)
    if manifest.get("work_item_count") != len(rows) or not rows:
        raise ValueError("Campaign manifest work-item coverage is invalid")
    expected_by_phase: dict[str, dict[str, Any]] = {}
    for phase, _, _ in PHASES:
        input_record = (manifest.get("inputs") or {}).get(phase)
        if not isinstance(input_record, Mapping):
            raise ValueError(f"Campaign manifest is missing phase {phase}")
        expected_by_phase[phase] = dict(input_record)
    phase_counts = Counter(str(row.get("phase") or "") for row in rows)
    if manifest.get("phase_counts") != dict(sorted(phase_counts.items())):
        raise ValueError("Campaign phase counts are invalid")
    for row in rows:
        phase = str(row.get("phase") or "")
        if (
            phase not in expected_by_phase
            or row.get("review_decision") is not None
            or row.get("decision_recorded_in") is not None
            or row.get("materialization_allowed") is not False
            or row.get("source_contract") != SOURCE_CONTRACT
        ):
            raise ValueError("Campaign contains an invalid work item")
    return manifest, expected_by_phase


def _validate_receipt(
    *,
    receipt_path: Path,
    expected_phase: str,
    expected_review_kind: str,
    campaign_phase: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = load_json(receipt_path)
    expected_queue = campaign_phase.get("queue") or {}
    responses = (receipt.get("inputs") or {}).get("completed_responses") or {}
    decision_counts = (receipt.get("counts") or {}).get("decision_counts")
    response_count = (receipt.get("counts") or {}).get("response_count")
    if (
        receipt.get("protocol") != RECEIPT_PROTOCOL
        or receipt.get("review_kind") != expected_review_kind
        or not isinstance(receipt.get("reviewer_id"), str)
        or not receipt["reviewer_id"].strip()
        or ((receipt.get("inputs") or {}).get("queue") or {}).get("sha256") != expected_queue.get("sha256")
        or not isinstance(responses.get("path"), str)
        or not isinstance(responses.get("sha256"), str)
        or not isinstance(response_count, int)
        or response_count != campaign_phase.get("question_count")
        or not isinstance(decision_counts, Mapping)
        or sum(value for value in decision_counts.values() if isinstance(value, int)) != response_count
        or any(key not in {"accept", "reject", "abstain"} or type(value) is not int for key, value in decision_counts.items())
        or receipt.get("semantic_amendment_application_allowed") is not False
        or receipt.get("materialization_allowed") is not False
        or receipt.get("evidence_eligible") is not False
        or receipt.get("training_eligible") is not False
        or receipt.get("submission_eligible") is not False
        or receipt.get("promotion_allowed") is not False
    ):
        raise ValueError(f"{expected_phase}: review receipt does not bind the immutable campaign phase")
    responses_path = Path(responses["path"])
    if not responses_path.is_file() or sha256_file(responses_path) != responses["sha256"]:
        raise ValueError(f"{expected_phase}: completed response file does not match receipt")
    return {
        "status": "receipt_verified_non_materializable",
        "reviewer_id": receipt["reviewer_id"],
        "receipt": {"path": str(receipt_path), "sha256": sha256_file(receipt_path)},
        "completed_responses": {"path": str(responses_path), "sha256": responses["sha256"]},
        "decision_counts": dict(sorted((str(key), value) for key, value in decision_counts.items())),
        "accepted_count": int(decision_counts.get("accept", 0)),
        "next_gate": "Independent semantic/source/taxonomy validation; no application or materialization is allowed.",
        "materialization_allowed": False,
    }


def build(
    *,
    campaign: Path,
    campaign_manifest: Path,
    output: Path,
    source_receipt: Path | None = None,
    scope_receipt: Path | None = None,
    component_receipt: Path | None = None,
    dimension_receipt: Path | None = None,
) -> dict[str, Any]:
    """Record phase receipt availability without applying any review decision."""
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite semantic campaign status: {output}")
    campaign_meta, campaign_phases = _validate_campaign(campaign, campaign_manifest)
    supplied = {
        "source_receipt": source_receipt,
        "scope_receipt": scope_receipt,
        "component_receipt": component_receipt,
        "dimension_receipt": dimension_receipt,
    }
    phase_rows: list[dict[str, Any]] = []
    for phase, review_kind, receipt_key in PHASES:
        receipt_path = supplied[receipt_key]
        phase_meta = campaign_phases[phase]
        if receipt_path is None:
            phase_rows.append(
                {
                    "phase": phase,
                    "question_count": phase_meta["question_count"],
                    "status": "awaiting_human_response_receipt",
                    "receipt": None,
                    "accepted_count": 0,
                    "next_gate": "Create a separate completed response file and verify it against the immutable queue.",
                    "materialization_allowed": False,
                }
            )
        else:
            phase_rows.append(
                {
                    "phase": phase,
                    "question_count": phase_meta["question_count"],
                    **_validate_receipt(
                        receipt_path=receipt_path,
                        expected_phase=phase,
                        expected_review_kind=review_kind,
                        campaign_phase=phase_meta,
                    ),
                }
            )
    waiting_count = sum(row["question_count"] for row in phase_rows if row["status"] != "receipt_verified_non_materializable")
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "campaign_status": (
            "all_phase_receipts_verified_non_materializable"
            if waiting_count == 0
            else "blocked_awaiting_human_response_receipts"
        ),
        "production_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "materialization_allowed": False,
        "inputs": {
            "campaign": {"path": str(campaign), "sha256": sha256_file(campaign)},
            "campaign_manifest": {"path": str(campaign_manifest), "sha256": sha256_file(campaign_manifest)},
        },
        "counts": {
            "campaign_work_item_count": campaign_meta["work_item_count"],
            "awaiting_human_response_count": waiting_count,
            "phase_receipt_count": len(phase_rows) - sum(row["status"] == "awaiting_human_response_receipt" for row in phase_rows),
            "accepted_count": sum(row["accepted_count"] for row in phase_rows),
        },
        "phases": phase_rows,
        "source_contract": SOURCE_CONTRACT,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--campaign-manifest", type=Path, required=True)
    parser.add_argument("--source-receipt", type=Path)
    parser.add_argument("--scope-receipt", type=Path)
    parser.add_argument("--component-receipt", type=Path)
    parser.add_argument("--dimension-receipt", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(**{name: value.resolve() if isinstance(value, Path) else value for name, value in vars(args).items()})
    print(json.dumps(result["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
