"""Validate a completed exact-cell human-review batch without promoting it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "vifinqa_exact_cell_human_review_intake_v1"
DECISION_PROTOCOL = "vifinqa_exact_cell_human_review_forms_v1"
UI_PROTOCOL = "vifinqa_exact_cell_human_review_ui_v2"
DECISIONS = frozenset({"APPROVE", "REJECT", "UNCERTAIN"})
_FALSE_AUTHORIZATION_FIELDS = (
    "human_verified",
    "may_authorize_evidence",
    "may_authorize_answer",
    "training_eligible",
    "submission_eligible",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain JSON objects")
        rows.append(value)
    if not rows:
        raise ValueError(f"{path} contains no review decisions")
    return rows


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _assert_false_authorization(decision: Mapping[str, Any], *, label: str) -> None:
    for field in _FALSE_AUTHORIZATION_FIELDS:
        if decision.get(field) is not False:
            raise ValueError(f"{label} incorrectly enables {field}")


def _approval_candidate(item: Mapping[str, Any], decision: Mapping[str, Any]) -> Mapping[str, Any]:
    selected_uid = _text(decision.get("selected_internal_table_uid"), "selected table UID")
    candidates = item.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("UI item has no candidates")
    candidate = next((value for value in candidates if value.get("tableUid") == selected_uid), None)
    if not isinstance(candidate, Mapping):
        raise ValueError(f"selected table UID is not in the review packet: {selected_uid}")
    row_index = decision.get("selected_row_index")
    column_index = decision.get("selected_column_index")
    if not isinstance(row_index, int) or not isinstance(column_index, int):
        raise ValueError("APPROVE requires integer row and column indices")
    if row_index not in {value.get("rowIndex") for value in candidate.get("rowCandidates") or []}:
        raise ValueError("selected row is not a review-packet row candidate")
    if column_index not in {value.get("columnIndex") for value in candidate.get("columnHeaders") or []}:
        raise ValueError("selected column is not a review-packet column candidate")
    source = candidate.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("selected candidate has no source coordinates")
    expected_hashes = {
        "selected_exact_table_locator_sha256": source.get("locatorSha256"),
        "selected_source_sha256": source.get("sourceSha256"),
        "selected_table_sha256": source.get("tableSha256"),
    }
    for field, expected in expected_hashes.items():
        if decision.get(field) != expected:
            raise ValueError(f"APPROVE source hash mismatch: {field}")
    if decision.get("review_confirmations") != {
        "table_subject": True,
        "scope": True,
        "period": True,
        "unit": True,
    }:
        raise ValueError("APPROVE is missing a source confirmation")
    return candidate


def validate_exact_cell_review_intake(
    *,
    decision_path: Path,
    ui_bundle_path: Path,
) -> dict[str, Any]:
    """Accept a complete, hash-bound review batch without granting any authority."""
    bundle = json.loads(ui_bundle_path.read_text(encoding="utf-8"))
    if bundle.get("protocol") != UI_PROTOCOL:
        raise ValueError("unexpected exact-cell UI bundle protocol")
    if bundle.get("sourceContract", {}).get("numericFinancialValuesExposed") is not False:
        raise ValueError("UI bundle incorrectly exposes financial values")
    if bundle.get("sourceContract", {}).get("mayAuthorizeAnswer") is not False:
        raise ValueError("UI bundle incorrectly authorizes answers")
    items = bundle.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("UI bundle has no active review items")
    item_by_sample = {str(item.get("sampleId")): item for item in items}
    if len(item_by_sample) != len(items):
        raise ValueError("UI bundle has duplicate sample IDs")

    decisions = _load_jsonl(decision_path)
    decision_by_sample = {str(decision.get("sample_id")): decision for decision in decisions}
    if len(decision_by_sample) != len(decisions):
        raise ValueError("review decisions have duplicate sample IDs")
    expected_samples = set(item_by_sample)
    actual_samples = set(decision_by_sample)
    if actual_samples != expected_samples:
        missing = sorted(expected_samples - actual_samples)
        unexpected = sorted(actual_samples - expected_samples)
        raise ValueError(
            "review batch does not match the active UI batch: "
            f"missing={len(missing)}, unexpected={len(unexpected)}"
        )

    counts = {decision: 0 for decision in sorted(DECISIONS)}
    reviewer_ids: set[str] = set()
    summaries: list[dict[str, Any]] = []
    for sample_id, item in item_by_sample.items():
        decision = decision_by_sample[sample_id]
        question_id = item.get("questionId")
        label = f"Q{question_id}"
        if decision.get("protocol") != DECISION_PROTOCOL:
            raise ValueError(f"{label} has an unexpected decision protocol")
        if decision.get("source_review_sample_sha256") != bundle.get("sourceReviewSampleSha256"):
            raise ValueError(f"{label} review sample SHA-256 mismatch")
        if decision.get("typed_operand_plans_sha256") != bundle.get("typedOperandPlansSha256"):
            raise ValueError(f"{label} typed plans SHA-256 mismatch")
        for field, expected in (
            ("review_packet_id", item.get("reviewPacketId")),
            ("route_id", item.get("routeId")),
            ("question_id", item.get("questionId")),
            ("operand_id", item.get("operandId")),
        ):
            if decision.get(field) != expected:
                raise ValueError(f"{label} metadata mismatch: {field}")
        if decision.get("review_state") != "REVIEWED":
            raise ValueError(f"{label} is not marked REVIEWED")
        verdict = decision.get("overall_decision")
        if verdict not in DECISIONS:
            raise ValueError(f"{label} has an invalid review decision")
        _assert_false_authorization(decision, label=label)
        reviewer_id = _text(decision.get("reviewer_id"), f"{label} reviewer ID")
        reviewed_at = _text(decision.get("reviewed_at"), f"{label} review timestamp")
        notes = decision.get("reviewer_notes")
        if not isinstance(notes, str):
            raise ValueError(f"{label} notes must be a string")
        reviewer_ids.add(reviewer_id)
        counts[verdict] += 1

        if verdict == "APPROVE":
            candidate = _approval_candidate(item, decision)
            selected = {
                "internal_table_uid": candidate["tableUid"],
                "row_index": decision["selected_row_index"],
                "column_index": decision["selected_column_index"],
                "exact_table_locator_sha256": decision["selected_exact_table_locator_sha256"],
            }
        else:
            if any(
                decision.get(field) is not None
                for field in (
                    "selected_internal_table_uid",
                    "selected_row_index",
                    "selected_column_index",
                    "selected_exact_table_locator_sha256",
                    "selected_source_sha256",
                    "selected_table_sha256",
                    "review_confirmations",
                )
            ):
                raise ValueError(f"{label} {verdict} must not select a source cell")
            if not notes.strip():
                raise ValueError(f"{label} {verdict} requires reviewer notes")
            selected = None
        summaries.append(
            {
                "question_id": question_id,
                "operand_id": item.get("operandId"),
                "sample_id": sample_id,
                "decision": verdict,
                "selected_cell": selected,
                "reviewer_id": reviewer_id,
                "reviewed_at": reviewed_at,
                "reviewer_notes": notes,
            }
        )

    return {
        "protocol": PROTOCOL,
        "status": "ACCEPTED_NON_PROMOTING",
        "review_batch": {
            "active_item_count": len(items),
            "decision_count": len(decisions),
            "source_review_sample_sha256": bundle["sourceReviewSampleSha256"],
            "typed_operand_plans_sha256": bundle["typedOperandPlansSha256"],
            "source_structured_tables_sha256": bundle.get("sourceStructuredTablesSha256"),
        },
        "decision_counts": counts,
        "reviewer_ids": sorted(reviewer_ids),
        "decisions": summaries,
        "authorization": {
            "human_verified_count": 0,
            "may_authorize_evidence": False,
            "may_authorize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
            "reason": "reviewer identity and source bindings remain subject to an independent promotion gate",
        },
    }


def write_exact_cell_review_intake(
    *,
    decision_path: Path,
    ui_bundle_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Write a hash-bound, non-promoting intake receipt for a completed batch."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    receipt = validate_exact_cell_review_intake(
        decision_path=decision_path,
        ui_bundle_path=ui_bundle_path,
    )
    output_dir.mkdir(parents=True)
    receipt_path = output_dir / "review_intake_receipt_v1.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "inputs": {
            "decisions": {"path": str(decision_path), "sha256": _sha256_file(decision_path)},
            "ui_bundle": {"path": str(ui_bundle_path), "sha256": _sha256_file(ui_bundle_path)},
        },
        "outputs": {
            "review_intake_receipt_v1.json": {
                "sha256": _sha256_file(receipt_path),
                "size_bytes": receipt_path.stat().st_size,
            }
        },
        "authorization": receipt["authorization"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt
