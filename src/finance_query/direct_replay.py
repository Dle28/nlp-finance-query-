"""Independent, conflict-aware replay of direct evidence candidates.

The replay deliberately ignores the reviewer's final confidence and critic
decision.  It reopens every candidate that claimed exact evidence, checks the
stored coordinates against V2/V3, and reports whether the surviving source
cells imply one unique value or conflicting exact values.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from .corpus import infer_unit
from .execution import convert_unit, parse_decimal


DIRECT_REPLAY_SCHEMA_VERSION = 1
DIRECT_REPLAY_PROTOCOL = "independent_direct_exact_source_replay_v1"
MONETARY_UNITS = {"vnd", "thousand_vnd", "million_vnd", "billion_vnd", "trillion_vnd"}


def canonical_unit(table: Mapping[str, Any]) -> str | None:
    unit = table.get("unit_hint")
    if isinstance(unit, str) and unit:
        return unit
    context = " ".join(
        [
            str(table.get("context_before") or ""),
            str((table.get("context_trace") or {}).get("source_title") or ""),
            " ".join((table.get("context_trace") or {}).get("unit_labels") or []),
        ]
    )
    rows = table.get("rows") or []
    return infer_unit(context, "\n".join(" | ".join(map(str, row)) for row in rows))


def _header_label(context: Mapping[str, Any], column_index: int) -> str:
    columns = (context.get("canonical_headers") or {}).get("columns") or []
    return str(
        next(
            (
                column.get("source_label")
                for column in columns
                if isinstance(column.get("column_index"), int)
                and not isinstance(column.get("column_index"), bool)
                and column["column_index"] == column_index
            ),
            "",
        )
        or ""
    )


def _row_is_numeric_data(context: Mapping[str, Any], row_index: int, column_index: int) -> bool:
    profile = next(
        (
            row
            for row in context.get("row_profiles") or []
            if row.get("row_index") == row_index
        ),
        {},
    )
    return str(profile.get("role") or "") == "data" and column_index in {
        int(value) for value in profile.get("numeric_columns") or []
    }


def _revalidate_candidate(
    assessment: Mapping[str, Any],
    plan: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    uid = str(assessment.get("uid") or "")
    table = tables.get(uid)
    context = contexts.get(uid)
    if not uid or table is None:
        reasons.append("table_not_in_valid_v2")
    if not uid or context is None:
        reasons.append("table_not_in_canonical_v3")
    if not all(bool(assessment.get(name)) for name in ("source_ready", "exact_row", "row_bound")):
        reasons.append("assessment_not_exact_source_ready")
    if not bool((assessment.get("raw_metric_identity") or {}).get("exact")):
        reasons.append("metric_identity_not_exact")
    if not bool((assessment.get("grounding") or {}).get("guard_pass")):
        reasons.append("grounding_guard_failed")
    if abs(float(assessment.get("metadata_score") or 0) - 1.0) > 1e-12:
        reasons.append("metadata_score_not_exact")
    if reasons or table is None or context is None:
        return None, reasons

    tickers = {str(value) for value in plan.get("tickers") or []}
    years = {int(value) for value in plan.get("years") or [] if isinstance(value, int)}
    requested_scope = str(plan.get("scope") or "")
    if tickers and str(table.get("ticker") or "") not in tickers:
        reasons.append("ticker_mismatch")
    if years and int(table.get("report_year") or 0) not in years:
        reasons.append("report_year_mismatch")
    if requested_scope and str(table.get("scope") or "") != requested_scope:
        reasons.append("scope_mismatch")

    binding = dict(assessment.get("value_binding") or {})
    row_index, column_index = binding.get("row_index"), binding.get("column_index")
    rows = table.get("rows") or []
    if binding.get("status") != "cell_bound":
        reasons.append("binding_not_cell_bound")
    if (
        isinstance(row_index, bool)
        or not isinstance(row_index, int)
        or isinstance(column_index, bool)
        or not isinstance(column_index, int)
        or not 0 <= row_index < len(rows)
        or not 0 <= column_index < len(rows[row_index])
    ):
        reasons.append("coordinates_invalid")
        return None, reasons
    raw_value = str(rows[row_index][column_index])
    if raw_value != str(binding.get("value") or ""):
        reasons.append("stored_value_differs_from_v2")
    label = _header_label(context, column_index)
    if not label or label != str(binding.get("column_label") or ""):
        reasons.append("stored_header_differs_from_v3")
    if not _row_is_numeric_data(context, row_index, column_index):
        reasons.append("cell_not_canonical_numeric_data")
    provenance_rows = table.get("cell_provenance") or []
    if (
        row_index >= len(provenance_rows)
        or column_index >= len(provenance_rows[row_index] or [])
        or provenance_rows[row_index][column_index] != binding.get("source_cell")
    ):
        reasons.append("cell_provenance_mismatch")
    parsed = parse_decimal(raw_value)
    if parsed.value is None or any(warning != "percent_value_not_scaled" for warning in parsed.warnings):
        reasons.append("numeric_parse_unreliable")
    source_unit = canonical_unit(table)
    requested_unit = str(plan.get("requested_unit") or "") or None
    if requested_unit in MONETARY_UNITS and source_unit is None:
        reasons.append("source_monetary_unit_unresolved")
    if reasons or parsed.value is None:
        return None, reasons
    try:
        normalized = convert_unit(Decimal(parsed.value), source_unit, requested_unit)
    except ValueError:
        return None, ["source_and_requested_units_incompatible"]
    comparison_unit = requested_unit or source_unit
    return {
        "internal_table_uid": uid,
        "row_index": row_index,
        "column_index": column_index,
        "raw_value": raw_value,
        "parsed_value": parsed.value,
        "source_unit": source_unit,
        "comparison_value": format(normalized, "f"),
        "comparison_unit": comparison_unit,
    }, []


def replay_direct_review(
    review: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Revalidate all claimed-exact candidates without trusting consensus."""
    qid = int(review["id"])
    plan = dict(review.get("effective_question_plan") or {})
    family = str(plan.get("family") or review.get("family") or "")
    base = {
        "schema_version": DIRECT_REPLAY_SCHEMA_VERSION,
        "protocol": DIRECT_REPLAY_PROTOCOL,
        "question_id": qid,
        "machine_consensus_status": str(review.get("consensus_status") or "needs_human"),
        "machine_selected_uid": str(review.get("machine_candidate_uid") or "") or None,
        "submission_eligible": False,
        "training_eligible": False,
        "review_status_promotion_allowed": False,
    }
    if family != "direct_lookup":
        return {**base, "status": "not_applicable", "reason_codes": ["not_direct_lookup"]}

    self_review = dict(review.get("machine_self_review") or {})
    assessments = self_review.get("candidate_assessments") or []
    if not isinstance(assessments, list) or not assessments:
        return {
            **base,
            "status": "shadow_blocked",
            "reason_codes": ["candidate_assessments_missing"],
            "valid_exact_candidates": [],
        }

    valid: list[dict[str, Any]] = []
    rejection_counts: dict[str, int] = {}
    for assessment in assessments:
        candidate, reasons = _revalidate_candidate(assessment, plan, tables, contexts)
        if candidate is not None:
            valid.append(candidate)
        for reason in set(reasons):
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
    distinct = sorted(
        {
            (str(item.get("comparison_value")), str(item.get("comparison_unit")))
            for item in valid
        }
    )
    if len(distinct) == 1:
        status = "shadow_replay_ready"
        reasons = ["unique_exact_source_value"]
        replay_value, replay_unit = distinct[0]
    elif len(distinct) > 1:
        status = "shadow_ambiguous"
        reasons = ["conflicting_exact_source_values"]
        replay_value = replay_unit = None
    else:
        status = "shadow_blocked"
        reasons = ["no_revalidated_exact_candidate"]
        replay_value = replay_unit = None
    return {
        **base,
        "status": status,
        "reason_codes": reasons,
        "replay_value": replay_value,
        "replay_unit": replay_unit,
        "valid_exact_candidate_count": len(valid),
        "distinct_exact_value_count": len(distinct),
        "valid_exact_candidates": valid,
        "rejection_counts": dict(sorted(rejection_counts.items())),
    }

