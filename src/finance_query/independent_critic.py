"""Independent semantic/source critic for direct EvidenceSets.

Unlike the historical multi-agent replay, this critic never receives a
reviewer score, selected candidate, consensus status, or decision.  It starts
from the immutable question plan plus the source-discovery sidecar and
recomputes the semantic identity and exact V2/V3 binding.
"""
from __future__ import annotations

from decimal import Decimal
import re
from typing import Any, Mapping

from .binding import row_label
from .direct_replay import MONETARY_UNITS, canonical_unit
from .execution import convert_unit, parse_decimal
from .financial_metrics import fold_text


INDEPENDENT_CRITIC_SCHEMA_VERSION = 1
INDEPENDENT_CRITIC_PROTOCOL = "independent_semantic_source_critic_v1"
TOKEN_RE = re.compile(r"[a-z0-9%]+")
RAW_NOTE_REFERENCE_RE = re.compile(
    r"(?<!\w)[IVXLCDM]+\s*\.\s*\d+(?:\s*\([A-Za-z]\))?(?!\w)",
    re.IGNORECASE,
)
RAW_STRUCTURAL_ROW_CODE_RE = re.compile(
    r"^\s*(?:(?:[IVXLCDM]+|\d+(?:\.\d+)*|[A-Za-z])\s*(?:[.)]|>)\s*)+",
    re.IGNORECASE,
)
STRICT_FINANCIAL_TOKEN_EXPANSIONS = {
    "tndn": ("thue", "thu", "nhap", "doanh", "nghiep"),
}


def _tokens(value: object) -> list[str]:
    return [token for token in TOKEN_RE.findall(fold_text(str(value or ""))) if not token.isdecimal()]


def _identity_tokens(value: object) -> list[str]:
    """Use the same narrow source-label normalizations as the V4 contract."""
    output: list[str] = []
    for token in _tokens(value):
        expansion = STRICT_FINANCIAL_TOKEN_EXPANSIONS.get(token)
        if token == "tndn" and output[-1:] == ["thue"]:
            output.extend((expansion or ())[1:])
        else:
            output.extend(expansion or (token,))
    return output


def _normalized_raw_identity_tokens(label: str) -> list[str]:
    without_note = RAW_NOTE_REFERENCE_RE.sub("", label)
    identity_label = RAW_STRUCTURAL_ROW_CODE_RE.sub("", without_note)
    return _identity_tokens(identity_label)


def _canonical_header(context: Mapping[str, Any], column_index: int) -> str:
    columns = (context.get("canonical_headers") or {}).get("columns") or []
    matches = [
        str(column.get("source_label") or "")
        for column in columns
        if type(column.get("column_index")) is int and column["column_index"] == column_index
    ]
    return matches[0] if len(matches) == 1 else ""


def _numeric_data_cell(context: Mapping[str, Any], row_index: int, column_index: int) -> bool:
    profiles = [
        row for row in context.get("row_profiles") or [] if row.get("row_index") == row_index
    ]
    return bool(
        len(profiles) == 1
        and profiles[0].get("role") == "data"
        and column_index in profiles[0].get("numeric_columns", [])
    )


def _metric_identity(candidate: Mapping[str, Any], raw_row: list[Any]) -> bool:
    discovery = candidate.get("source_discovery") or {}
    policy = str(discovery.get("policy") or "")
    recorded_label = str(discovery.get("raw_row_label") or "")
    # The direct-evidence builder normalizes a standalone structural code
    # (for example ``06``) before it records the raw label. Reproduce that
    # exact non-numeric-label contract here rather than assuming the first
    # raw cell is always the metric. This still rejects any changed label.
    observed_label = row_label([str(value) for value in raw_row])
    if not raw_row or observed_label != recorded_label:
        return False
    metric_match = discovery.get("metric_match") or {}
    matched = str(metric_match.get("matched_metric") or "")
    if policy in {
        "exact_raw_v2_metric_token_sequence_v1",
        "exact_raw_v2_metric_context_stripped_token_sequence_v1",
    }:
        return bool(
            _identity_tokens(matched)
            and _identity_tokens(matched) == _normalized_raw_identity_tokens(observed_label)
        )
    if policy == "exact_raw_v2_source_parent_and_row_token_sequence_v1":
        context = metric_match.get("context_evidence") or discovery.get("context_evidence") or {}
        return bool(
            _tokens(context.get("parent_heading"))
            and str(context.get("row_label") or "") == observed_label
            and _identity_tokens(context.get("parent_heading"))
            + _normalized_raw_identity_tokens(observed_label)
            == _identity_tokens(matched)
        )
    return False


def _critic_candidate(
    candidate: Mapping[str, Any],
    plan: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    uid = str(candidate.get("internal_table_uid") or "")
    table, context = tables.get(uid), contexts.get(uid)
    if table is None:
        reasons.append("table_not_in_valid_v2")
    if context is None:
        reasons.append("table_not_in_canonical_v3")
    if table is None or context is None:
        return None, reasons

    tickers = {str(value) for value in plan.get("tickers") or [] if str(value)}
    years = {int(value) for value in plan.get("years") or [] if type(value) is int}
    scope = str(plan.get("scope") or "")
    if not tickers:
        reasons.append("question_entity_unresolved")
    elif str(table.get("ticker") or "") not in tickers:
        reasons.append("ticker_mismatch")
    if years and int(table.get("report_year") or 0) not in years:
        reasons.append("report_year_mismatch")
    if scope and str(table.get("scope") or "") != scope:
        reasons.append("scope_mismatch")

    discovery = candidate.get("source_discovery") or {}
    row_index = discovery.get("row_index")
    binding = discovery.get("value_binding") or {}
    column_index = binding.get("column_index")
    rows = table.get("rows") or []
    if type(row_index) is not int or type(column_index) is not int:
        reasons.append("coordinates_missing")
        return None, reasons
    if not 0 <= row_index < len(rows) or not 0 <= column_index < len(rows[row_index]):
        reasons.append("coordinates_invalid")
        return None, reasons
    raw_row = rows[row_index]
    raw_value = str(raw_row[column_index])
    if not _metric_identity(candidate, raw_row):
        reasons.append("metric_identity_not_exact")
    if binding.get("status") != "cell_bound" or binding.get("row_index") != row_index:
        reasons.append("binding_not_cell_bound")
    if raw_value != str(binding.get("value") or ""):
        reasons.append("stored_value_differs_from_v2")
    header = _canonical_header(context, column_index)
    if not header or header != str(binding.get("column_label") or ""):
        reasons.append("stored_header_differs_from_v3")
    if not _numeric_data_cell(context, row_index, column_index):
        reasons.append("cell_not_canonical_numeric_data")
    provenance = table.get("cell_provenance") or []
    if (
        row_index >= len(provenance)
        or column_index >= len(provenance[row_index] or [])
        or provenance[row_index][column_index] != binding.get("source_cell")
    ):
        reasons.append("cell_provenance_mismatch")

    parsed = parse_decimal(raw_value)
    if parsed.value is None or any(value != "percent_value_not_scaled" for value in parsed.warnings):
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
    return {
        "internal_table_uid": uid,
        "row_index": row_index,
        "column_index": column_index,
        "raw_value": raw_value,
        "parsed_value": parsed.value,
        "canonical_header": header,
        "source_unit": source_unit,
        "comparison_value": format(normalized, "f"),
        "comparison_unit": requested_unit or source_unit,
    }, []


def critique_direct_evidence(
    evidence: Mapping[str, Any],
    plan: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    base = {
        "schema_version": INDEPENDENT_CRITIC_SCHEMA_VERSION,
        "protocol": INDEPENDENT_CRITIC_PROTOCOL,
        "question_id": int(evidence["id"]),
        "reviewer_inputs_used": [],
        "submission_eligible": False,
        "training_eligible": False,
        "review_status_promotion_allowed": False,
    }
    if str(plan.get("family") or evidence.get("family") or "") != "direct_lookup":
        return {**base, "status": "not_applicable", "reason_codes": ["not_direct_lookup"]}
    valid: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    for candidate in evidence.get("candidates") or []:
        result, reasons = _critic_candidate(candidate, plan, tables, contexts)
        if result is not None:
            valid.append(result)
        for reason in set(reasons):
            rejected[reason] = rejected.get(reason, 0) + 1
    distinct = sorted({(row["comparison_value"], str(row["comparison_unit"])) for row in valid})
    if len(distinct) == 1:
        status, reasons = "independent_ready", ["unique_exact_semantic_source_value"]
        value, unit = distinct[0]
    elif len(distinct) > 1:
        status, reasons = "independent_ambiguous", ["conflicting_exact_semantic_source_values"]
        value = unit = None
    else:
        status, reasons = "independent_blocked", ["no_independently_valid_candidate"]
        value = unit = None
    return {
        **base,
        "status": status,
        "reason_codes": reasons,
        "critic_value": value,
        "critic_unit": unit,
        "valid_candidate_count": len(valid),
        "distinct_value_count": len(distinct),
        "valid_candidates": valid,
        "rejection_counts": dict(sorted(rejected.items())),
    }
