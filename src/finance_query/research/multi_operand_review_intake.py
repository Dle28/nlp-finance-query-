"""Validate a completed multi-operand review batch without promoting it."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "vifinqa_multi_operand_review_intake_v1"
DECISION_PROTOCOL = "vifinqa_multi_operand_review_forms_v2"
UI_PROTOCOL = "vifinqa_multi_operand_review_ui_v2"
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
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain JSON objects")
        values.append(value)
    if not values:
        raise ValueError(f"{path} contains no review decisions")
    return values


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _assert_false_authorization(value: Mapping[str, Any], *, label: str) -> None:
    for field in _FALSE_AUTHORIZATION_FIELDS:
        if value.get(field) is not False:
            raise ValueError(f"{label} incorrectly enables {field}")


def _approved_operands(item: Mapping[str, Any], decision: Mapping[str, Any]) -> list[dict[str, Any]]:
    expected = item.get("operands")
    selected = decision.get("operand_decisions")
    if not isinstance(expected, list) or not isinstance(selected, list):
        raise ValueError("APPROVE requires source decisions for every operand")
    expected_by_id = {str(operand.get("operandId")): operand for operand in expected}
    selected_by_id = {str(operand.get("operand_id")): operand for operand in selected}
    if len(expected_by_id) != len(expected) or len(selected_by_id) != len(selected):
        raise ValueError("operand IDs must be unique")
    if set(expected_by_id) != set(selected_by_id):
        raise ValueError("APPROVE must include every required operand exactly once")
    summary: list[dict[str, Any]] = []
    for operand_id, expected_operand in expected_by_id.items():
        source = selected_by_id[operand_id]
        if source.get("route_id") != expected_operand.get("routeId"):
            raise ValueError(f"operand {operand_id} route ID mismatch")
        selected_uid = _text(source.get("selected_internal_table_uid"), f"operand {operand_id} table UID")
        candidates = expected_operand.get("candidates")
        candidate = next(
            (value for value in candidates or [] if value.get("tableUid") == selected_uid),
            None,
        )
        if not isinstance(candidate, Mapping):
            raise ValueError(f"operand {operand_id} selected table is not in its review packet")
        combination = source.get("cell_combination")
        selected_cells = source.get("selected_cells")
        if combination not in {"SINGLE_CELL", "CELL_SET"} or not isinstance(selected_cells, list):
            raise ValueError(f"operand {operand_id} has an invalid multi-row selection")
        if (combination == "SINGLE_CELL" and len(selected_cells) != 1) or (
            combination == "CELL_SET" and len(selected_cells) < 2
        ):
            raise ValueError(f"operand {operand_id} cell combination count is invalid")
        allowed_coordinates = {
            (cell.get("rowIndex"), cell.get("columnIndex"))
            for cell in candidate.get("selectableCells") or []
            if isinstance(cell, Mapping)
        }
        if not allowed_coordinates:
            raise ValueError(f"operand {operand_id} candidate has no selectable cells")
        validated_cells: list[dict[str, int]] = []
        seen_coordinates: set[tuple[int, int]] = set()
        for cell in selected_cells:
            if not isinstance(cell, Mapping):
                raise ValueError(f"operand {operand_id} selected cells must be objects")
            row_index = cell.get("row_index")
            column_index = cell.get("column_index")
            if not isinstance(row_index, int) or not isinstance(column_index, int):
                raise ValueError(f"operand {operand_id} requires integer cell coordinates")
            if "coefficient" in cell:
                raise ValueError(f"operand {operand_id} cannot assign arithmetic roles to review cells")
            if (row_index, column_index) not in allowed_coordinates:
                raise ValueError(f"operand {operand_id} selected cell is not in the review grid")
            if (row_index, column_index) in seen_coordinates:
                raise ValueError(f"operand {operand_id} repeats a selected cell")
            seen_coordinates.add((row_index, column_index))
            validated_cells.append(
                {"row_index": row_index, "column_index": column_index}
            )
        source_metadata = candidate.get("source")
        if not isinstance(source_metadata, Mapping):
            raise ValueError(f"operand {operand_id} selected candidate has no source metadata")
        expected_hashes = {
            "selected_exact_table_locator_sha256": source_metadata.get("locatorSha256"),
            "selected_source_sha256": source_metadata.get("sourceSha256"),
            "selected_table_sha256": source_metadata.get("tableSha256"),
        }
        for field, expected_hash in expected_hashes.items():
            if source.get(field) != expected_hash:
                raise ValueError(f"operand {operand_id} source hash mismatch: {field}")
        if source.get("review_confirmations") != {
            "table_subject": True,
            "scope": True,
            "period": True,
            "unit": True,
        }:
            raise ValueError(f"operand {operand_id} lacks source confirmations")
        summary.append(
            {
                "operand_id": operand_id,
                "route_id": expected_operand["routeId"],
                "cell_combination": combination,
                "selected_cells": validated_cells,
                "source_binding": {
                    "internal_table_uid": selected_uid,
                    "exact_table_locator_sha256": source["selected_exact_table_locator_sha256"],
                },
            }
        )
    return summary


def validate_multi_operand_review_intake(
    *, decision_path: Path, ui_bundle_path: Path
) -> dict[str, Any]:
    """Accept a complete hash-bound batch, retaining zero answer authority."""
    bundle = json.loads(ui_bundle_path.read_text(encoding="utf-8"))
    if bundle.get("protocol") != UI_PROTOCOL:
        raise ValueError("unexpected multi-operand UI bundle protocol")
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
            raise ValueError(f"UI bundle incorrectly enables {field}")
    items = bundle.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("UI bundle has no review items")
    item_by_packet = {str(item.get("packetId")): item for item in items}
    if len(item_by_packet) != len(items):
        raise ValueError("UI bundle has duplicate packet IDs")

    decisions = _load_jsonl(decision_path)
    decision_by_packet = {str(decision.get("packet_id")): decision for decision in decisions}
    if len(decision_by_packet) != len(decisions) or set(decision_by_packet) != set(item_by_packet):
        raise ValueError("review decisions do not match the complete active Batch 2")

    counts = {decision: 0 for decision in sorted(DECISIONS)}
    reviewers: set[str] = set()
    summaries: list[dict[str, Any]] = []
    for packet_id, item in item_by_packet.items():
        decision = decision_by_packet[packet_id]
        label = f"Q{item.get('questionId')}"
        if decision.get("protocol") != DECISION_PROTOCOL:
            raise ValueError(f"{label} has an unexpected decision protocol")
        if decision.get("source_multi_operand_queue_sha256") != bundle.get("sourceQueueSha256"):
            raise ValueError(f"{label} queue SHA-256 mismatch")
        for field, expected in (("packet_id", item.get("packetId")), ("question_id", item.get("questionId"))):
            if decision.get(field) != expected:
                raise ValueError(f"{label} metadata mismatch: {field}")
        if decision.get("review_state") != "REVIEWED":
            raise ValueError(f"{label} is not marked REVIEWED")
        verdict = decision.get("overall_decision")
        if verdict not in DECISIONS:
            raise ValueError(f"{label} has an invalid decision")
        _assert_false_authorization(decision, label=label)
        reviewer_id = _text(decision.get("reviewer_id"), f"{label} reviewer ID")
        reviewed_at = _text(decision.get("reviewed_at"), f"{label} review timestamp")
        notes = decision.get("reviewer_notes")
        if not isinstance(notes, str):
            raise ValueError(f"{label} reviewer notes must be a string")
        if verdict == "APPROVE":
            selected_operands = _approved_operands(item, decision)
        else:
            if decision.get("operand_decisions") != []:
                raise ValueError(f"{label} {verdict} must not retain partial source selections")
            if not notes.strip():
                raise ValueError(f"{label} {verdict} requires reviewer notes")
            selected_operands = []
        counts[verdict] += 1
        reviewers.add(reviewer_id)
        summaries.append(
            {
                "question_id": item["questionId"],
                "packet_id": packet_id,
                "decision": verdict,
                "selected_operands": selected_operands,
                "reviewer_id": reviewer_id,
                "reviewed_at": reviewed_at,
                "reviewer_notes": notes,
            }
        )
    return {
        "protocol": PROTOCOL,
        "status": "ACCEPTED_NON_PROMOTING",
        "review_batch": {
            "active_question_count": len(items),
            "decision_count": len(decisions),
            "source_multi_operand_queue_sha256": bundle["sourceQueueSha256"],
            "required_operand_count": bundle.get("requiredOperandCount"),
        },
        "decision_counts": counts,
        "reviewer_ids": sorted(reviewers),
        "decisions": summaries,
        "authorization": {
            "human_verified_count": 0,
            "may_authorize_evidence": False,
            "may_authorize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
            "reason": "multi-operand selections remain a review receipt pending an independent promotion gate",
        },
    }


def write_multi_operand_review_intake(
    *, decision_path: Path, ui_bundle_path: Path, output_dir: Path
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    receipt = validate_multi_operand_review_intake(
        decision_path=decision_path, ui_bundle_path=ui_bundle_path
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
