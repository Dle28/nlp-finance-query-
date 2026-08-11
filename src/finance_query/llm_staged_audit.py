"""Independent source replay for evidence-bounded staged LLM reviews.

The staged Qwen runner deliberately makes the model a source-cell selector,
not a calculator.  This module supplies the two post-review checks required
before such an output can be called ``machine_calibrated``:

* direct replay reopens each cell selected by Qwen in immutable V2/V3 data;
* an independent source critic evaluates the stage from every admissible
  packet candidate without receiving the Qwen selection.

The critic is fail-closed: a required binding with multiple admissible source
cells is ambiguous rather than an invitation to choose a convenient one.
Neither gate makes an output submission- or training-eligible.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .execution import RELIABLE_BINDING_WARNINGS, parse_decimal
from .llm_stage_execution import execute_reviewed_stage


LLM_STAGED_AUDIT_SCHEMA_VERSION = 1
LLM_STAGED_AUDIT_PROTOCOL = "independent_staged_llm_source_audit_v1"


def _packet_hash_is_valid(packet: Mapping[str, Any]) -> bool:
    expected = dict(packet)
    provided = str(expected.pop("packet_sha256", ""))
    canonical = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return provided == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _header(context: Mapping[str, Any], column_index: int) -> str:
    matches = [
        str(column.get("source_label") or "")
        for column in (context.get("canonical_headers") or {}).get("columns") or []
        if isinstance(column.get("column_index"), int)
        and not isinstance(column.get("column_index"), bool)
        and column["column_index"] == column_index
    ]
    return matches[0] if len(matches) == 1 else ""


def _numeric_data_cell(context: Mapping[str, Any], row_index: int, column_index: int) -> bool:
    profiles = [
        row for row in context.get("row_profiles") or [] if row.get("row_index") == row_index
    ]
    return bool(
        len(profiles) == 1
        and str(profiles[0].get("role") or "") == "data"
        and column_index in {int(value) for value in profiles[0].get("numeric_columns") or []}
    )


def _candidate_index(packet: Mapping[str, Any]) -> dict[tuple[str, str, str, int], Mapping[str, Any]]:
    records: dict[tuple[str, str, str, int], Mapping[str, Any]] = {}
    for candidate in packet.get("candidates") or []:
        key = (
            str(candidate.get("company") or ""),
            str(candidate.get("variable_id") or ""),
            str(candidate.get("internal_table_uid") or ""),
            candidate.get("row_index"),
        )
        if (
            not all(key[:3])
            or not isinstance(key[3], int)
            or key in records
        ):
            raise ValueError("Packet has an invalid or duplicate candidate identity")
        records[key] = candidate
    return records


def _valid_literal_cell(
    candidate: Mapping[str, Any],
    cell: Mapping[str, Any],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    contexts_by_uid: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Read a packet cell again from V2/V3 without trusting its stored copy."""
    reasons: list[str] = []
    uid = str(candidate.get("internal_table_uid") or "")
    row_index = candidate.get("row_index")
    column_index = cell.get("column_index")
    table, context = tables_by_uid.get(uid), contexts_by_uid.get(uid)
    if table is None:
        reasons.append("table_not_in_valid_v2")
    if context is None:
        reasons.append("table_not_in_canonical_v3")
    if not isinstance(row_index, int) or isinstance(row_index, bool):
        reasons.append("row_index_invalid")
    if not isinstance(column_index, int) or isinstance(column_index, bool):
        reasons.append("column_index_invalid")
    if reasons or table is None or context is None:
        return None, reasons
    rows = table.get("rows") or []
    if not 0 <= row_index < len(rows) or not 0 <= column_index < len(rows[row_index]):
        return None, ["coordinates_invalid"]
    raw_value = str(rows[row_index][column_index])
    header = _header(context, column_index)
    if raw_value != str(cell.get("raw_value") or ""):
        reasons.append("stored_value_differs_from_v2")
    if not header or header != str(cell.get("canonical_header") or ""):
        reasons.append("stored_header_differs_from_v3")
    if not _numeric_data_cell(context, row_index, column_index):
        reasons.append("cell_not_canonical_numeric_data")
    parsed = parse_decimal(raw_value)
    if parsed.value is None or any(
        warning not in RELIABLE_BINDING_WARNINGS for warning in parsed.warnings
    ):
        reasons.append("numeric_parse_unreliable")
    if reasons:
        return None, reasons
    return {
        "company": str(candidate.get("company") or ""),
        "variable_id": str(candidate.get("variable_id") or ""),
        "internal_table_uid": uid,
        "document_id": str(candidate.get("document_id") or ""),
        "report_scope": str(candidate.get("report_scope") or "unknown"),
        "row_index": row_index,
        "column_index": column_index,
        "canonical_header": header,
        "raw_value": raw_value,
        "unit_labels": list(cell.get("unit_labels") or []),
    }, []


def direct_replay_selected_stage(
    packet: Mapping[str, Any],
    review: Mapping[str, Any],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    contexts_by_uid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Replay exact LLM-selected bindings against source V2/V3 artifacts."""
    base = {
        "schema_version": LLM_STAGED_AUDIT_SCHEMA_VERSION,
        "protocol": LLM_STAGED_AUDIT_PROTOCOL,
        "question_id": int(packet.get("question_id") or review.get("id") or 0),
        "stage_id": str((packet.get("stage") or {}).get("stage_id") or ""),
        "reviewer_inputs_used": ["bounded_llm_selected_bindings"],
        "submission_eligible": False,
        "training_eligible": False,
        "review_status_promotion_allowed": False,
    }
    if not _packet_hash_is_valid(packet):
        return {**base, "status": "direct_replay_blocked", "reason_codes": ["packet_hash_mismatch"]}
    if str(review.get("annotation_status") or "") != "machine_provisional":
        return {
            **base,
            "status": "direct_replay_blocked",
            "reason_codes": ["review_not_machine_provisional"],
        }
    if str(review.get("packet_sha256") or "") != str(packet.get("packet_sha256") or ""):
        return {
            **base,
            "status": "direct_replay_blocked",
            "reason_codes": ["review_packet_hash_mismatch"],
        }
    try:
        candidates = _candidate_index(packet)
    except ValueError as error:
        return {**base, "status": "direct_replay_blocked", "reason_codes": [str(error)]}
    required = {
        (str(binding.get("company") or ""), str(binding.get("variable_id") or ""))
        for binding in packet.get("required_bindings") or []
    }
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    rejection_counts: dict[str, int] = {}
    for binding in review.get("selected_bindings") or []:
        key = (
            str(binding.get("company") or ""),
            str(binding.get("variable_id") or ""),
            str(binding.get("internal_table_uid") or ""),
            binding.get("row_index"),
        )
        candidate = candidates.get(key)
        binding_key = key[:2]
        if candidate is None:
            rejection_counts["selected_binding_not_in_packet"] = (
                rejection_counts.get("selected_binding_not_in_packet", 0) + 1
            )
            continue
        if binding_key not in required or binding_key in seen:
            rejection_counts["duplicate_or_unrequired_binding"] = (
                rejection_counts.get("duplicate_or_unrequired_binding", 0) + 1
            )
            continue
        cells = [
            cell
            for cell in candidate.get("available_value_cells") or []
            if cell.get("column_index") == binding.get("column_index")
            and str(cell.get("canonical_header") or "")
            == str(binding.get("canonical_header") or "")
            and str(cell.get("raw_value") or "") == str(binding.get("raw_value") or "")
        ]
        if len(cells) != 1:
            rejection_counts["selected_cell_not_literal_packet_cell"] = (
                rejection_counts.get("selected_cell_not_literal_packet_cell", 0) + 1
            )
            continue
        literal, reasons = _valid_literal_cell(candidate, cells[0], tables_by_uid, contexts_by_uid)
        if literal is None:
            for reason in set(reasons):
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            continue
        selected.append(literal)
        seen.add(binding_key)
    if seen != required:
        missing = sorted(required - seen)
        rejection_counts["required_binding_not_replayed"] = len(missing)
    if rejection_counts:
        return {
            **base,
            "status": "direct_replay_blocked",
            "reason_codes": sorted(rejection_counts),
            "rejection_counts": dict(sorted(rejection_counts.items())),
            "replayed_bindings": selected,
        }
    return {
        **base,
        "status": "direct_replay_ready",
        "reason_codes": ["all_qwen_selected_cells_match_immutable_v2_v3"],
        "replayed_binding_count": len(selected),
        "replayed_bindings": selected,
    }


def independently_execute_packet_stage(
    packet: Mapping[str, Any],
    stage: Mapping[str, Any],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    contexts_by_uid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Execute a stage from all source candidates, without a model decision.

    Exactly one physically valid candidate cell must remain per required
    company/variable.  A choice between source alternatives is intentionally
    reported as ambiguous rather than made by this critic.
    """
    base = {
        "schema_version": LLM_STAGED_AUDIT_SCHEMA_VERSION,
        "protocol": LLM_STAGED_AUDIT_PROTOCOL,
        "question_id": int(packet.get("question_id") or 0),
        "stage_id": str((packet.get("stage") or {}).get("stage_id") or ""),
        "reviewer_inputs_used": [],
        "submission_eligible": False,
        "training_eligible": False,
        "review_status_promotion_allowed": False,
    }
    if not _packet_hash_is_valid(packet):
        return {**base, "status": "independent_critic_blocked", "reason_codes": ["packet_hash_mismatch"]}
    if str((packet.get("scope_contract") or {}).get("status") or "") != "resolved":
        return {
            **base,
            "status": "independent_critic_blocked",
            "reason_codes": ["report_scope_not_resolved"],
        }
    if str((packet.get("document_contract") or {}).get("status") or "") != "resolved":
        return {
            **base,
            "status": "independent_critic_blocked",
            "reason_codes": ["source_document_not_resolved"],
        }
    required = {
        (str(binding.get("company") or ""), str(binding.get("variable_id") or ""))
        for binding in packet.get("required_bindings") or []
    }
    candidates_by_binding: dict[tuple[str, str], list[dict[str, Any]]] = {
        key: [] for key in required
    }
    rejection_counts: dict[str, int] = {}
    for candidate in packet.get("candidates") or []:
        key = (str(candidate.get("company") or ""), str(candidate.get("variable_id") or ""))
        if key not in candidates_by_binding:
            continue
        for cell in candidate.get("available_value_cells") or []:
            literal, reasons = _valid_literal_cell(candidate, cell, tables_by_uid, contexts_by_uid)
            if literal is not None:
                candidates_by_binding[key].append(literal)
            for reason in set(reasons):
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
    missing = sorted(key for key, values in candidates_by_binding.items() if not values)
    ambiguous = sorted(key for key, values in candidates_by_binding.items() if len(values) > 1)
    if missing or ambiguous:
        reasons = []
        if missing:
            reasons.append("no_independently_replayed_source_cell")
        if ambiguous:
            reasons.append("multiple_independently_replayed_source_cells")
        return {
            **base,
            "status": "independent_critic_blocked",
            "reason_codes": reasons,
            "missing_bindings": [
                {"company": company, "variable_id": variable} for company, variable in missing
            ],
            "ambiguous_bindings": [
                {"company": company, "variable_id": variable} for company, variable in ambiguous
            ],
            "rejection_counts": dict(sorted(rejection_counts.items())),
        }
    synthetic_review = {
        "annotation_status": "machine_provisional",
        "selected_bindings": [
            candidate
            for _key, values in sorted(candidates_by_binding.items())
            for candidate in values
        ],
    }
    execution = execute_reviewed_stage(stage, synthetic_review)
    if str(execution.get("status") or "") != "stage_complete":
        return {
            **base,
            "status": "independent_critic_blocked",
            "reason_codes": list(execution.get("reason_codes") or ["independent_stage_execution_failed"]),
            "deterministic_stage_execution": execution,
            "rejection_counts": dict(sorted(rejection_counts.items())),
        }
    return {
        **base,
        "status": "independent_critic_ready",
        "reason_codes": ["unique_source_cell_per_required_binding"],
        "deterministic_stage_execution": execution,
        "rejection_counts": dict(sorted(rejection_counts.items())),
    }


def execution_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Compare the result-relevant execution fields, not incidental metadata."""
    keys = {
        "status",
        "stage_id",
        "metric_id",
        "metric_values",
        "aggregate",
        "aggregate_value",
        "eligible_entities",
        "winning_entity",
        "result_unit",
        "final_stage",
    }
    return all(left.get(key) == right.get(key) for key in keys)
