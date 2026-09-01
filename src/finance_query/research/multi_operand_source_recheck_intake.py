"""Validate an independent human source-recheck draft without promotion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "vifinqa_multi_operand_source_recheck_intake_v1"
UI_PROTOCOL = "vifinqa_multi_operand_source_recheck_ui_v1"
DECISION_PROTOCOL = "vifinqa_multi_operand_source_recheck_forms_v1"
DECISIONS = frozenset({"APPROVE", "REJECT", "UNCERTAIN"})
_FALSE_AUTHORIZATION_FIELDS = (
    "human_verified",
    "may_authorize_evidence",
    "may_authorize_answer",
    "training_eligible",
    "submission_eligible",
)
_FORBIDDEN_KEYS = frozenset(
    {"answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query", "rows"}
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number} must contain JSON objects")
        records.append(record)
    if not records:
        raise ValueError(f"{path} contains no source-recheck decisions")
    return records


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _normalized_id(value: str) -> str:
    return value.strip().casefold()


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(key in _FORBIDDEN_KEYS or _contains_forbidden_key(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _assert_false_authorization(value: Mapping[str, Any], *, label: str) -> None:
    for field in _FALSE_AUTHORIZATION_FIELDS:
        if value.get(field) is not False:
            raise ValueError(f"{label} incorrectly enables {field}")


def _expected_cells(item: Mapping[str, Any]) -> list[dict[str, int]]:
    cells = item.get("selectedCells")
    if not isinstance(cells, list) or not cells:
        raise ValueError("source-recheck item has no frozen cells")
    result: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for cell in cells:
        if not isinstance(cell, Mapping):
            raise ValueError("source-recheck item cells must be objects")
        row_index = cell.get("rowIndex")
        column_index = cell.get("columnIndex")
        if not isinstance(row_index, int) or not isinstance(column_index, int):
            raise ValueError("source-recheck item cells must use integer coordinates")
        coordinate = (row_index, column_index)
        if coordinate in seen:
            raise ValueError("source-recheck item repeats a frozen cell")
        seen.add(coordinate)
        result.append({"row_index": row_index, "column_index": column_index})
    return result


def validate_multi_operand_source_recheck_intake(
    *, decision_path: Path, ui_bundle_path: Path
) -> dict[str, Any]:
    """Accept independent-review drafts while retaining zero answer authority."""
    bundle = json.loads(ui_bundle_path.read_text(encoding="utf-8"))
    if bundle.get("protocol") != UI_PROTOCOL:
        raise ValueError("unexpected source-recheck UI bundle protocol")
    contract = bundle.get("sourceContract") or {}
    for field in (
        "numericFinancialValuesExposed",
        "maySelectValue",
        "mayExecuteFormula",
        "mayAuthorizeEvidence",
        "mayAuthorizeAnswer",
        "trainingEligible",
        "submissionEligible",
    ):
        if contract.get(field) is not False:
            raise ValueError(f"source-recheck UI bundle incorrectly enables {field}")
    prior_reviewers = bundle.get("priorReviewerIds")
    if not isinstance(prior_reviewers, list) or not prior_reviewers:
        raise ValueError("source-recheck UI bundle has no prior reviewer identity")
    prior_reviewer_ids = {_normalized_id(_text(value, "prior reviewer ID")) for value in prior_reviewers}
    items = bundle.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("source-recheck UI bundle has no unresolved items")
    item_by_recheck = {str(item.get("recheckId")): item for item in items}
    if len(item_by_recheck) != len(items):
        raise ValueError("source-recheck UI bundle has duplicate recheck IDs")

    decisions = _load_jsonl(decision_path)
    decision_by_recheck = {str(decision.get("recheck_id")): decision for decision in decisions}
    if len(decision_by_recheck) != len(decisions) or set(decision_by_recheck) != set(item_by_recheck):
        raise ValueError("source-recheck decisions do not match the complete active batch")
    counts = {decision: 0 for decision in sorted(DECISIONS)}
    reviewer_ids: set[str] = set()
    summaries: list[dict[str, Any]] = []
    for recheck_id, item in item_by_recheck.items():
        decision = decision_by_recheck[recheck_id]
        question_id = item.get("questionId")
        operand = item.get("operand") or {}
        candidate = item.get("candidate") or {}
        label = f"Q{question_id} {operand.get('operandId')}"
        if decision.get("protocol") != DECISION_PROTOCOL:
            raise ValueError(f"{label} has an unexpected decision protocol")
        if decision.get("source_recheck_receipt_sha256") != bundle.get("sourceRecheckReceiptSha256"):
            raise ValueError(f"{label} source-recheck receipt SHA-256 mismatch")
        if decision.get("source_multi_operand_bundle_sha256") != bundle.get("sourceMultiOperandBundleSha256"):
            raise ValueError(f"{label} source bundle SHA-256 mismatch")
        expected_metadata = {
            "recheck_id": item.get("recheckId"),
            "packet_id": item.get("packetId"),
            "question_id": question_id,
            "operand_id": operand.get("operandId"),
            "route_id": operand.get("routeId"),
            "selected_internal_table_uid": candidate.get("tableUid"),
        }
        for field, expected in expected_metadata.items():
            if decision.get(field) != expected:
                raise ValueError(f"{label} metadata mismatch: {field}")
        if decision.get("review_state") != "REVIEWED":
            raise ValueError(f"{label} is not marked REVIEWED")
        verdict = decision.get("overall_decision")
        if verdict not in DECISIONS:
            raise ValueError(f"{label} has an invalid decision")
        _assert_false_authorization(decision, label=label)
        reviewer_id = _text(decision.get("reviewer_id"), f"{label} reviewer ID")
        if _normalized_id(reviewer_id) in prior_reviewer_ids or decision.get("independent_from_prior_reviewer") is not True:
            raise ValueError(f"{label} reviewer is not independent from the prior review")
        reviewed_at = _text(decision.get("reviewed_at"), f"{label} review timestamp")
        notes = decision.get("reviewer_notes")
        if not isinstance(notes, str):
            raise ValueError(f"{label} reviewer notes must be a string")
        selected_cells = decision.get("selected_cells")
        if selected_cells != _expected_cells(item):
            raise ValueError(f"{label} must retain the exact frozen cell coordinates")
        if verdict == "APPROVE":
            if decision.get("review_confirmations") != {
                "table_subject": True,
                "scope": True,
                "period": True,
                "unit": True,
            }:
                raise ValueError(f"{label} approval lacks a source confirmation")
        else:
            if decision.get("review_confirmations") != {} or not notes.strip():
                raise ValueError(f"{label} {verdict} requires notes and no approval confirmation")
        counts[verdict] += 1
        reviewer_ids.add(reviewer_id)
        summaries.append(
            {
                "question_id": question_id,
                "packet_id": item["packetId"],
                "operand_id": operand["operandId"],
                "recheck_id": recheck_id,
                "decision": verdict,
                "selected_cells": _expected_cells(item),
                "reviewer_id": reviewer_id,
                "reviewed_at": reviewed_at,
                "reviewer_notes": notes,
            }
        )
    receipt = {
        "protocol": PROTOCOL,
        "status": "ACCEPTED_NON_PROMOTING",
        "review_batch": {
            "unresolved_source_count": len(items),
            "decision_count": len(decisions),
            "source_recheck_receipt_sha256": bundle["sourceRecheckReceiptSha256"],
        },
        "decision_counts": counts,
        "independent_reviewer_ids": sorted(reviewer_ids),
        "decisions": summaries,
        "authorization": {
            "human_verified_count": 0,
            "may_authorize_evidence": False,
            "may_authorize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
            "reason": "independent source-recheck drafts remain review-only pending the canonical E2E authorization path",
        },
    }
    if _contains_forbidden_key(receipt):
        raise AssertionError("source-recheck intake receipt exposes a forbidden field")
    return receipt


def write_multi_operand_source_recheck_intake(
    *, decision_path: Path, ui_bundle_path: Path, output_dir: Path
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    receipt = validate_multi_operand_source_recheck_intake(
        decision_path=decision_path, ui_bundle_path=ui_bundle_path
    )
    output_dir.mkdir(parents=True)
    receipt_path = output_dir / "source_recheck_intake_receipt_v1.json"
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
            "source_recheck_intake_receipt_v1.json": {
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
