"""Recheck multi-operand review coordinates against immutable source tables.

This is a research-only structural audit of a completed human-review receipt.
It re-reads the original source table to verify identity, coordinate existence,
period, unit and scope.  It deliberately never emits a financial value,
evaluates an operation, or turns a reviewed selection into evidence, an answer,
training data, promotion, release, or a competition submission.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from finance_query.research.exact_cell_research import _period_unit_diagnostic


PROTOCOL = "vifinqa_multi_operand_review_source_recheck_v1"
INTAKE_PROTOCOL = "vifinqa_multi_operand_review_intake_v1"
UI_PROTOCOL = "vifinqa_multi_operand_review_ui_v2"
DECISION_PROTOCOL = "vifinqa_multi_operand_review_forms_v2"
_NUMERIC_TOKEN = re.compile(r"\d")
_FALSE_AUTHORIZATION_FIELDS = (
    "human_verified",
    "may_authorize_evidence",
    "may_authorize_answer",
    "training_eligible",
    "submission_eligible",
)
_FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "raw_source_row",
        "raw_source_cell",
        "rows",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
        raise ValueError(f"{path} contains no JSONL records")
    return records


def _require_false_authorization(value: Mapping[str, Any], *, label: str) -> None:
    for field in _FALSE_AUTHORIZATION_FIELDS:
        if value.get(field) is not False:
            raise ValueError(f"{label} incorrectly enables {field}")


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in _FORBIDDEN_KEYS or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _source_locator(asset: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_path": str(asset["source_path"]),
        "source_sha256": str(asset["source_sha256"]),
        "table_sha256": str(asset["table_sha256"]),
        "local_ordinal": int(asset["local_ordinal"]),
        "char_start": int(asset["char_start"]),
        "page_no": asset.get("page_no"),
    }


def _load_selected_assets(path: Path, selected_uids: set[str]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            asset = json.loads(line)
            if not isinstance(asset, dict):
                raise ValueError(f"{path} contains a non-object asset")
            uid = str(asset.get("internal_table_uid") or "")
            if uid in selected_uids:
                if uid in selected:
                    raise ValueError(f"full-table assets contain duplicate selected UID: {uid}")
                selected[uid] = asset
    missing = sorted(selected_uids - set(selected))
    if missing:
        raise ValueError(f"selected table UID is absent from full-table assets: {missing[:3]}")
    return selected


def _load_required_operands(plans_path: Path) -> dict[tuple[int, str], dict[str, Any]]:
    operands: dict[tuple[int, str], dict[str, Any]] = {}
    for plan in _load_jsonl(plans_path):
        question_id = plan.get("question_id")
        if not isinstance(question_id, int):
            continue
        for operand in plan.get("operands") or []:
            if not isinstance(operand, Mapping) or operand.get("required") is not True:
                continue
            operand_id = operand.get("operand_id")
            years = operand.get("years") or []
            if not isinstance(operand_id, str) or len(years) != 1 or not isinstance(years[0], int):
                raise ValueError(f"Q{question_id} has an invalid required operand plan")
            key = (question_id, operand_id)
            if key in operands:
                raise ValueError(f"duplicate required operand plan: Q{question_id} {operand_id}")
            operands[key] = {
                "ticker": str(operand.get("ticker") or ""),
                "report_year": years[0],
                "requested_scope": operand.get("scope"),
            }
    if not operands:
        raise ValueError("typed operand plans contain no required operands")
    return operands


def _validate_intake_lineage(
    *, intake_dir: Path, decision_path: Path, ui_bundle_path: Path
) -> dict[str, Any]:
    manifest_path = intake_dir / "manifest.json"
    receipt_path = intake_dir / "review_intake_receipt_v1.json"
    if not manifest_path.is_file() or not receipt_path.is_file():
        raise ValueError("review intake must contain manifest.json and review_intake_receipt_v1.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != INTAKE_PROTOCOL or receipt.get("protocol") != INTAKE_PROTOCOL:
        raise ValueError("unexpected multi-operand review intake protocol")
    if receipt.get("status") != "ACCEPTED_NON_PROMOTING":
        raise ValueError("review intake is not an accepted non-promoting receipt")
    inputs = manifest.get("inputs") or {}
    outputs = manifest.get("outputs") or {}
    if inputs.get("decisions", {}).get("sha256") != sha256_file(decision_path):
        raise ValueError("intake decisions hash mismatch")
    if inputs.get("ui_bundle", {}).get("sha256") != sha256_file(ui_bundle_path):
        raise ValueError("intake UI bundle hash mismatch")
    if outputs.get("review_intake_receipt_v1.json", {}).get("sha256") != sha256_file(receipt_path):
        raise ValueError("intake receipt hash mismatch")
    authorization = receipt.get("authorization") or {}
    if authorization.get("human_verified_count") != 0:
        raise ValueError("review intake unexpectedly claims human verification")
    for field in (
        "may_authorize_evidence",
        "may_authorize_answer",
        "training_eligible",
        "submission_eligible",
    ):
        if authorization.get(field) is not False:
            raise ValueError(f"review intake incorrectly enables {field}")
    return receipt


def _selection_cells(
    *, decision_operand: Mapping[str, Any], candidate: Mapping[str, Any], label: str
) -> list[dict[str, int]]:
    combination = decision_operand.get("cell_combination")
    selected = decision_operand.get("selected_cells")
    if combination not in {"SINGLE_CELL", "CELL_SET"} or not isinstance(selected, list):
        raise ValueError(f"{label} has an invalid cell selection")
    if (combination == "SINGLE_CELL" and len(selected) != 1) or (
        combination == "CELL_SET" and len(selected) < 2
    ):
        raise ValueError(f"{label} cell combination count is invalid")
    allowed = {
        (cell.get("rowIndex"), cell.get("columnIndex"))
        for cell in candidate.get("selectableCells") or []
        if isinstance(cell, Mapping)
    }
    if not allowed:
        raise ValueError(f"{label} candidate has no selectable cells")
    result: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for cell in selected:
        if not isinstance(cell, Mapping):
            raise ValueError(f"{label} selected cells must be objects")
        row_index = cell.get("row_index")
        column_index = cell.get("column_index")
        if not isinstance(row_index, int) or not isinstance(column_index, int):
            raise ValueError(f"{label} cell coordinates must be integers")
        coordinate = (row_index, column_index)
        if coordinate not in allowed:
            raise ValueError(f"{label} selected cell is not in the reviewed grid")
        if coordinate in seen:
            raise ValueError(f"{label} repeats a selected cell")
        seen.add(coordinate)
        result.append({"row_index": row_index, "column_index": column_index})
    return result


def _coordinate_status(
    *, asset: Mapping[str, Any], cells: list[dict[str, int]]
) -> str:
    rows = asset.get("rows") or []
    for cell in cells:
        row_index = cell["row_index"]
        column_index = cell["column_index"]
        if not 0 <= row_index < len(rows):
            return "COORDINATE_OUT_OF_RANGE"
        row = rows[row_index]
        if not isinstance(row, list) or not 0 <= column_index < len(row):
            return "COORDINATE_OUT_OF_RANGE"
        if not _NUMERIC_TOKEN.search(str(row[column_index])):
            return "SOURCE_CELL_NOT_NUMERIC"
    return "NUMERIC_SOURCE_CELLS_PRESENT"


def _period_status(diagnostic: Mapping[str, Any], cells: list[dict[str, int]]) -> str:
    matching_columns = set(diagnostic.get("matching_year_column_indices") or [])
    fallback_column = diagnostic.get("fallback_period_column_index")
    selected_columns = {cell["column_index"] for cell in cells}
    if selected_columns.issubset(matching_columns):
        return "EXPLICIT_YEAR_HEADER_MATCH"
    if fallback_column is not None and selected_columns == {fallback_column}:
        return "FALLBACK_PERIOD_COLUMN_MATCH"
    return "PERIOD_HEADER_NEEDS_HUMAN_RECHECK"


def _unit_status(diagnostic: Mapping[str, Any]) -> str:
    return (
        "HEADER_UNIT_MATCH"
        if diagnostic.get("unit_status") == "UNIQUE_HEADER_UNIT_CANDIDATE"
        else "UNIT_NEEDS_HUMAN_RECHECK"
    )


def _scope_status(diagnostic: Mapping[str, Any]) -> str:
    return (
        "SCOPE_MATCH"
        if diagnostic.get("scope_status") in {"SCOPE_MATCH", "SCOPE_NOT_REQUESTED"}
        else "SCOPE_NEEDS_HUMAN_RECHECK"
    )


def _result_status(
    *, coordinate_status: str, period_status: str, unit_status: str, scope_status: str
) -> str:
    if coordinate_status != "NUMERIC_SOURCE_CELLS_PRESENT":
        return "BLOCKED_SOURCE_COORDINATE"
    if any(
        status.endswith("NEEDS_HUMAN_RECHECK")
        for status in (period_status, unit_status, scope_status)
    ):
        return "NEEDS_HUMAN_SOURCE_RECHECK"
    return "STRUCTURALLY_RECHECKED"


def recheck_multi_operand_review(
    *,
    intake_dir: Path,
    decision_path: Path,
    ui_bundle_path: Path,
    assets_path: Path,
    plans_path: Path,
) -> dict[str, Any]:
    """Recheck the source geometry of a completed Batch 2 review receipt.

    Hash or packet-identity failures raise errors.  Source semantics that are
    still ambiguous are written as ``NEEDS_HUMAN_SOURCE_RECHECK`` and remain
    non-authorizing.
    """
    receipt = _validate_intake_lineage(
        intake_dir=intake_dir, decision_path=decision_path, ui_bundle_path=ui_bundle_path
    )
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
    items_by_packet = {str(item.get("packetId")): item for item in items}
    if len(items_by_packet) != len(items):
        raise ValueError("UI bundle contains duplicate packet IDs")

    decisions = _load_jsonl(decision_path)
    decisions_by_packet = {str(decision.get("packet_id")): decision for decision in decisions}
    if len(decisions_by_packet) != len(decisions) or set(decisions_by_packet) != set(items_by_packet):
        raise ValueError("review decisions do not match the complete UI bundle")
    receipt_by_packet = {
        str(decision.get("packet_id")): decision for decision in receipt.get("decisions") or []
    }
    if set(receipt_by_packet) != set(items_by_packet):
        raise ValueError("intake receipt does not match the complete UI bundle")
    plans = _load_required_operands(plans_path)

    selected_uids = {
        str(operand.get("selected_internal_table_uid"))
        for decision in decisions
        if decision.get("overall_decision") == "APPROVE"
        for operand in decision.get("operand_decisions") or []
        if isinstance(operand, Mapping)
    }
    if not selected_uids:
        raise ValueError("completed batch contains no approved source selections")
    assets = _load_selected_assets(assets_path, selected_uids)

    rechecks: list[dict[str, Any]] = []
    for packet_id, item in items_by_packet.items():
        decision = decisions_by_packet[packet_id]
        receipt_decision = receipt_by_packet[packet_id]
        question_id = item.get("questionId")
        if not isinstance(question_id, int) or decision.get("question_id") != question_id:
            raise ValueError(f"packet {packet_id} question identity mismatch")
        if decision.get("protocol") != DECISION_PROTOCOL:
            raise ValueError(f"Q{question_id} has an unexpected decision protocol")
        if decision.get("source_multi_operand_queue_sha256") != bundle.get("sourceQueueSha256"):
            raise ValueError(f"Q{question_id} queue SHA-256 mismatch")
        if decision.get("overall_decision") != receipt_decision.get("decision"):
            raise ValueError(f"Q{question_id} decision differs from its intake receipt")
        _require_false_authorization(decision, label=f"Q{question_id}")
        if decision.get("overall_decision") != "APPROVE":
            continue
        item_operands = {str(operand.get("operandId")): operand for operand in item.get("operands") or []}
        decision_operands = {
            str(operand.get("operand_id")): operand
            for operand in decision.get("operand_decisions") or []
            if isinstance(operand, Mapping)
        }
        receipt_operands = {
            str(operand.get("operand_id")): operand
            for operand in receipt_decision.get("selected_operands") or []
            if isinstance(operand, Mapping)
        }
        if set(item_operands) != set(decision_operands) or set(item_operands) != set(receipt_operands):
            raise ValueError(f"Q{question_id} does not retain every required operand")
        for operand_id, item_operand in item_operands.items():
            label = f"Q{question_id} {operand_id}"
            plan = plans.get((question_id, operand_id))
            if plan is None:
                raise ValueError(f"{label} is absent from typed operand plans")
            if (
                item_operand.get("ticker") != plan["ticker"]
                or item_operand.get("reportYear") != plan["report_year"]
                or item_operand.get("requestedScope") != plan["requested_scope"]
            ):
                raise ValueError(f"{label} UI packet and typed plan disagree")
            decision_operand = decision_operands[operand_id]
            if decision_operand.get("route_id") != item_operand.get("routeId"):
                raise ValueError(f"{label} route identity mismatch")
            table_uid = str(decision_operand.get("selected_internal_table_uid") or "")
            candidates = item_operand.get("candidates") or []
            candidate = next(
                (value for value in candidates if value.get("tableUid") == table_uid), None
            )
            if not isinstance(candidate, Mapping):
                raise ValueError(f"{label} selected table is not in its UI packet")
            cells = _selection_cells(
                decision_operand=decision_operand, candidate=candidate, label=label
            )
            receipt_operand = receipt_operands[operand_id]
            if (
                receipt_operand.get("cell_combination") != decision_operand.get("cell_combination")
                or receipt_operand.get("selected_cells") != cells
                or receipt_operand.get("route_id") != item_operand.get("routeId")
            ):
                raise ValueError(f"{label} differs from its intake receipt")
            asset = assets[table_uid]
            source = candidate.get("source") or {}
            locator_sha = _canonical_sha(_source_locator(asset))
            if (
                source.get("sourceSha256") != asset.get("source_sha256")
                or source.get("tableSha256") != asset.get("table_sha256")
                or source.get("locatorSha256") != locator_sha
                or decision_operand.get("selected_source_sha256") != asset.get("source_sha256")
                or decision_operand.get("selected_table_sha256") != asset.get("table_sha256")
                or decision_operand.get("selected_exact_table_locator_sha256") != locator_sha
            ):
                raise ValueError(f"{label} source identity mismatch against full-table asset")
            if decision_operand.get("review_confirmations") != {
                "table_subject": True,
                "scope": True,
                "period": True,
                "unit": True,
            }:
                raise ValueError(f"{label} lacks the original source confirmations")
            diagnostic = _period_unit_diagnostic(
                asset,
                requested_year=plan["report_year"],
                requested_scope=plan["requested_scope"],
            )
            coordinate_status = _coordinate_status(asset=asset, cells=cells)
            period_status = _period_status(diagnostic, cells)
            unit_status = _unit_status(diagnostic)
            scope_status = _scope_status(diagnostic)
            rechecks.append(
                {
                    "question_id": question_id,
                    "packet_id": packet_id,
                    "operand_id": operand_id,
                    "route_id": item_operand["routeId"],
                    "internal_table_uid": table_uid,
                    "cell_combination": decision_operand["cell_combination"],
                    "selected_cells": cells,
                    "source_recheck": {
                        "source_identity": "HASH_BOUND_FULL_TABLE_MATCH",
                        "coordinate_status": coordinate_status,
                        "period_status": period_status,
                        "unit_status": unit_status,
                        "scope_status": scope_status,
                        "semantic_conclusion": "NOT_EVALUATED",
                    },
                    "recheck_status": _result_status(
                        coordinate_status=coordinate_status,
                        period_status=period_status,
                        unit_status=unit_status,
                        scope_status=scope_status,
                    ),
                }
            )
    if not rechecks:
        raise ValueError("review batch produced no approved recheck records")
    if _contains_forbidden_key(rechecks):
        raise AssertionError("source recheck would expose a forbidden numeric-value field")
    status_counts: dict[str, int] = {}
    for record in rechecks:
        status = str(record["recheck_status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "protocol": PROTOCOL,
        "status": "RECHECKED_NON_PROMOTING",
        "review_batch": {
            "approved_question_count": len({record["question_id"] for record in rechecks}),
            "approved_operand_count": len(rechecks),
            "selected_cell_count": sum(len(record["selected_cells"]) for record in rechecks),
        },
        "recheck_status_counts": dict(sorted(status_counts.items())),
        "rechecks": rechecks,
        "authorization": {
            "human_verified_count": 0,
            "may_authorize_evidence": False,
            "may_authorize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
            "reason": (
                "machine source recheck verifies only provenance and table structure; "
                "an independent human semantic review remains required"
            ),
        },
    }


def write_multi_operand_review_recheck(
    *,
    intake_dir: Path,
    decision_path: Path,
    ui_bundle_path: Path,
    assets_path: Path,
    plans_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Write a hash-bound, non-promoting source-recheck receipt."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    receipt = recheck_multi_operand_review(
        intake_dir=intake_dir,
        decision_path=decision_path,
        ui_bundle_path=ui_bundle_path,
        assets_path=assets_path,
        plans_path=plans_path,
    )
    output_dir.mkdir(parents=True)
    receipt_path = output_dir / "source_recheck_receipt_v1.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "inputs": {
            "review_intake_manifest": {
                "path": str(intake_dir / "manifest.json"),
                "sha256": sha256_file(intake_dir / "manifest.json"),
            },
            "review_intake_receipt": {
                "path": str(intake_dir / "review_intake_receipt_v1.json"),
                "sha256": sha256_file(intake_dir / "review_intake_receipt_v1.json"),
            },
            "decisions": {"path": str(decision_path), "sha256": sha256_file(decision_path)},
            "ui_bundle": {"path": str(ui_bundle_path), "sha256": sha256_file(ui_bundle_path)},
            "full_table_assets": {"path": str(assets_path), "sha256": sha256_file(assets_path)},
            "typed_operand_plans": {"path": str(plans_path), "sha256": sha256_file(plans_path)},
        },
        "outputs": {
            "source_recheck_receipt_v1.json": {
                "sha256": sha256_file(receipt_path),
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
