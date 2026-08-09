"""Exact-row evidence collection for controlled financial formula templates.

This module deliberately does not calculate a formula or choose a final
answer.  It materializes only auditable operand candidates from raw V2 rows
and canonical source headers, so later reviewers can see whether every input
is truly present before an executor is allowed to run.
"""

from __future__ import annotations

from typing import Any, Mapping

from .financial_metrics import operand_match_score


def _column_by_index(context: Mapping[str, Any], index: int) -> Mapping[str, Any]:
    return next(
        (
            column
            for column in (context.get("canonical_headers") or {}).get("columns") or []
            if int(column.get("column_index") or -1) == index
        ),
        {},
    )


def _numeric_columns(context: Mapping[str, Any], row_index: int) -> list[int]:
    profile = next(
        (
            value
            for value in context.get("row_profiles") or []
            if int(value.get("row_index") or -1) == row_index
        ),
        {},
    )
    if profile.get("role") != "data":
        return []
    return [int(value) for value in profile.get("numeric_columns") or []]


def _allowed_table_function(operand: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    allowed = {str(value) for value in operand.get("allowed_table_functions") or []}
    if not allowed:
        return True
    function = str((context.get("table_function") or {}).get("kind") or "")
    return function in allowed


def _candidate_matches_question_plan(
    item: Mapping[str, Any], candidate: Mapping[str, Any], table: Mapping[str, Any]
) -> tuple[bool, str | None]:
    """Require resolved entity/scope metadata before formula operands can bind."""
    plan = item.get("question_plan") or {}
    expected_tickers = {str(value) for value in plan.get("tickers") or [] if str(value)}
    ticker = str(candidate.get("ticker") or table.get("ticker") or "")
    if not expected_tickers:
        return False, "question_entity_not_resolved"
    if ticker not in expected_tickers:
        return False, "candidate_ticker_mismatch"
    expected_scope = str(plan.get("scope") or "")
    scope = str(candidate.get("scope") or table.get("scope") or "")
    if expected_scope and scope != expected_scope:
        return False, "candidate_scope_mismatch"
    return True, None


def _period_labels(context: Mapping[str, Any]) -> list[str]:
    return [
        str(column.get("source_label") or "")
        for column in (context.get("canonical_headers") or {}).get("columns") or []
    ]


def bind_operand_cell(
    operand: Mapping[str, Any],
    candidate: Mapping[str, Any],
    table: Mapping[str, Any],
    context: Mapping[str, Any],
    row_index: int,
) -> dict[str, Any]:
    """Bind an operand row to one raw V2 numeric cell only when unique."""
    rows = table.get("rows") or []
    numeric_columns = _numeric_columns(context, row_index)
    years = [int(value) for value in operand.get("years") or [] if isinstance(value, int)]
    columns = set(numeric_columns)
    reason = ""
    try:
        candidate_report_year = int(candidate.get("report_year") or 0)
    except (TypeError, ValueError):
        candidate_report_year = 0
    if len(years) == 1:
        year = str(years[0])
        explicit = {
            index
            for index in columns
            if year in str(_column_by_index(context, index).get("source_label") or "")
        }
        if len(explicit) == 1:
            columns = explicit
            reason = "explicit_year_header"
        elif len(explicit) > 1:
            return {
                "status": "ambiguous_period_column",
                "numeric_columns": numeric_columns,
                "reason": "multiple raw canonical columns name the operand year",
            }
        elif candidate_report_year == years[0]:
            current = {
                index
                for index in columns
                if any(
                    marker in str(_column_by_index(context, index).get("source_label") or "").casefold()
                    for marker in ("năm nay", "kỳ này", "hiện tại", "current year", "current period")
                )
            }
            if len(current) == 1:
                columns = current
                reason = "current_period_header_matches_report_year"
            elif len(current) > 1:
                return {
                    "status": "ambiguous_period_column",
                    "numeric_columns": numeric_columns,
                    "reason": "multiple current-period raw columns",
                }
            else:
                return {
                    "status": "unbound_period_column",
                    "numeric_columns": numeric_columns,
                    "reason": "operand year has no unique raw canonical column",
                }
        else:
            return {
                "status": "unbound_period_column",
                "numeric_columns": numeric_columns,
                "reason": "operand year has no unique raw canonical column",
            }
    elif len(years) > 1:
        return {
            "status": "unbound_period_column",
            "numeric_columns": numeric_columns,
            "reason": "operand requests multiple years; it must be decomposed first",
        }
    elif len(columns) == 1:
        reason = "only_numeric_source_cell"
    else:
        return {
            "status": "unbound_period_column",
            "numeric_columns": numeric_columns,
            "reason": "operand has no uniquely bindable source numeric column",
        }

    column_index = next(iter(columns))
    if not 0 <= row_index < len(rows) or not 0 <= column_index < len(rows[row_index]):
        return {"status": "invalid_source_coordinates", "reason": "raw V2 coordinate out of bounds"}
    provenance = ((table.get("cell_provenance") or [])[row_index] or [])[column_index]
    return {
        "status": "cell_bound",
        "row_index": row_index,
        "column_index": column_index,
        "column_label": _column_by_index(context, column_index).get("source_label"),
        "raw_value": str(rows[row_index][column_index]),
        "source_cell": provenance,
        "binding_reason": reason,
    }


def operand_evidence_matches(
    operand: Mapping[str, Any],
    candidate: Mapping[str, Any],
    table: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return exact source bindings for a formula operand, never inferred cells."""
    if str((context.get("quality") or {}).get("status") or "") != "review_ready":
        return []
    if not _allowed_table_function(operand, context):
        return []
    rows = table.get("rows") or []
    output: list[dict[str, Any]] = []
    periods = _period_labels(context)
    for row_index, row in enumerate(rows):
        values = " | ".join(str(value) for value in row)
        score = operand_match_score(
            dict(operand),
            values,
            report_year=candidate.get("report_year"),
            period_labels=periods,
            ticker=str(candidate.get("ticker") or table.get("ticker") or ""),
        )
        # Formula inputs are high impact.  A partial lexical overlap is useful
        # for UI discovery, but must not become an autonomous operand binding.
        if score < 1.0:
            continue
        binding = bind_operand_cell(operand, candidate, table, context, row_index)
        if binding.get("status") != "cell_bound":
            continue
        output.append(
            {
                "internal_table_uid": str(candidate.get("internal_table_uid") or ""),
                "candidate_rank": int(candidate.get("rank") or 0),
                "document_id": table.get("document_id"),
                "ticker": candidate.get("ticker") or table.get("ticker"),
                "scope": candidate.get("scope") or table.get("scope"),
                "report_year": candidate.get("report_year"),
                "row_index": row_index,
                "source_row": [str(value) for value in row],
                "match_score": score,
                "binding": binding,
            }
        )
    return output


def formula_evidence_set(
    formula: Mapping[str, Any],
    item: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    *,
    max_matches_per_operand: int = 12,
) -> dict[str, Any]:
    """Collect source-only operand matches for one controlled formula template."""
    operands = [value for value in formula.get("operands") or [] if value.get("required", True)]
    coverage: dict[str, list[dict[str, Any]]] = {str(value["operand_id"]): [] for value in operands}
    candidate_gate_rejections: dict[str, int] = {}
    for candidate in item.get("candidates") or []:
        uid = str(candidate.get("internal_table_uid") or "")
        table, context = tables.get(uid), contexts.get(uid)
        if table is None or context is None:
            continue
        allowed, reason = _candidate_matches_question_plan(item, candidate, table)
        if not allowed:
            candidate_gate_rejections[str(reason)] = candidate_gate_rejections.get(str(reason), 0) + 1
            continue
        for operand in operands:
            coverage[str(operand["operand_id"])].extend(
                operand_evidence_matches(operand, candidate, table, context)
            )
    for operand_id, matches in coverage.items():
        unique: dict[tuple[str, int, int], dict[str, Any]] = {}
        for match in matches:
            binding = match["binding"]
            key = (str(match["internal_table_uid"]), int(match["row_index"]), int(binding["column_index"]))
            previous = unique.get(key)
            if previous is None or (
                int(match["candidate_rank"]), str(match["internal_table_uid"])
            ) < (
                int(previous["candidate_rank"]), str(previous["internal_table_uid"])
            ):
                unique[key] = match
        coverage[operand_id] = sorted(
            unique.values(),
            key=lambda value: (int(value["candidate_rank"]), str(value["internal_table_uid"]), int(value["row_index"])),
        )[:max_matches_per_operand]
    missing = [operand_id for operand_id, matches in coverage.items() if not matches]
    operand_coverage_status = "complete" if not missing else "partial"
    question_plan = item.get("question_plan") or {}
    family = str(question_plan.get("family") or item.get("weak_family") or "")
    reason_codes: list[str] = []
    if not (question_plan.get("tickers") or []):
        reason_codes.append("question_entity_not_resolved")
    if candidate_gate_rejections.get("candidate_scope_mismatch"):
        reason_codes.append("candidate_scope_mismatch")
    if str(formula.get("definition_status") or "defined") != "defined":
        reason_codes.append("formula_definition_requires_confirmation")
    if str(formula.get("execution_status") or "") == "stage_binding_required":
        reason_codes.append("formula_requires_stage_binding")
    if family in {
        "conditional_analytical",
        "cross_entity_comparison",
        "multi_entity_or_period_aggregation",
    }:
        reason_codes.append("question_family_requires_composed_execution")
    coherent_pairs: set[tuple[str, str]] | None = None
    for matches in coverage.values():
        pairs = {
            (str(value.get("ticker") or ""), str(value.get("scope") or ""))
            for value in matches
        }
        coherent_pairs = pairs if coherent_pairs is None else coherent_pairs & pairs
    selected_operand_matches: dict[str, dict[str, Any]] = {}
    if not missing:
        if not coherent_pairs:
            reason_codes.append("no_common_entity_scope_for_required_operands")
        elif len(coherent_pairs) > 1:
            reason_codes.append("ambiguous_entity_or_scope_combination")
        else:
            pair = next(iter(coherent_pairs))
            for operand_id, matches in coverage.items():
                pair_matches = [
                    value
                    for value in matches
                    if (str(value.get("ticker") or ""), str(value.get("scope") or "")) == pair
                ]
                if len(pair_matches) != 1:
                    reason_codes.append("ambiguous_operand_bindings")
                    break
                selected_operand_matches[operand_id] = pair_matches[0]
    evidence_completeness = (
        "complete"
        if operand_coverage_status == "complete" and len(selected_operand_matches) == len(operands) and not reason_codes
        else "partial"
    )
    return {
        "id": int(item["id"]),
        "question": item.get("question"),
        "formula": dict(formula),
        "operand_matches": coverage,
        "selected_operand_matches": selected_operand_matches,
        "required_operand_count": len(operands),
        "covered_operand_count": len(operands) - len(missing),
        "missing_operand_ids": missing,
        "operand_coverage_status": operand_coverage_status,
        "evidence_completeness": evidence_completeness,
        "reason_codes": reason_codes,
        "candidate_gate_rejections": candidate_gate_rejections,
        "execution_status": "not_executed_source_evidence_only",
    }
