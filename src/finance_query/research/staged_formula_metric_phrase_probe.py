"""Value-blind phrase recheck for staged-formula operands with weak row match.

This probe evaluates a small, explicit set of Vietnamese financial phrases.
It repairs neither OCR nor semantics generically: each rule names required and
forbidden compact fragments.  Rows are checked again against table type, V2/V3
provenance, period, unit, scope and row uniqueness.  The artifact is strictly
navigation research and cannot create evidence, answers, training data or a
submission.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.core.financial_taxonomy import normalize_label
from finance_query.research.composition_operand_source_gap_audit import _row_candidates, _subplan
from finance_query.research.full_corpus_candidate_retrieval import NUMERIC_CELL_RE, validate_candidate_artifact
from finance_query.research.hydrated_source_gate_probe import _hydrate, _require_asset_manifest
from finance_query.research.machine_exact_cell_proposals import sha256_file
from finance_query.research.machine_direct_lookup_route_materialization import _source_aligned
from finance_query.research.staged_formula_current_period_probe import (
    _allowed_table_function,
    _current_header_row_margin_candidate,
    _current_period_status,
    _header_coordinates_valid,
    _locator,
)
from finance_query.research.staged_formula_hydrated_current_period_probe import validate_staged_formula_hydrated_current_period_probe
from finance_query.research.staged_formula_hydrated_source_probe import _strict_status, validate_staged_formula_hydrated_source_probe
from finance_query.research.source_title_period_recheck import _dates_in_text
from finance_query.e2e.core.exact_cell_bindings_v2 import resolve_source_unit


PROTOCOL = "vifinqa_staged_formula_metric_phrase_probe_v1"
CONTRACT = {
    "research_only": True,
    "navigation_metadata_only": True,
    "in_memory_sidecar_probe_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset(
    {
        "answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query",
        "human_verified", "raw_source_cell", "raw_source_row", "rows", "cell_provenance",
        "context_before", "source_label", "row_label", "row_index", "column_index",
        "exact_table_locator",
    }
)
_COMPACT_RE = re.compile(r"[^a-z0-9]+")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must contain JSON objects")
    return rows


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _compact(value: object) -> str:
    return _COMPACT_RE.sub("", normalize_label(value))


def _rules(path: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(path)
    if payload.get("protocol") != PROTOCOL or int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unexpected metric phrase probe config")
    result: dict[str, dict[str, Any]] = {}
    for raw in payload.get("rules") or []:
        if not isinstance(raw, Mapping):
            raise ValueError("metric phrase rule must be an object")
        metric = normalize_label(raw.get("metric_hint"))
        rule_id = str(raw.get("rule_id") or "")
        required = [str(value) for value in raw.get("required_compact_fragments") or []]
        forbidden = [str(value) for value in raw.get("forbidden_compact_fragments") or []]
        account_codes = [str(value) for value in raw.get("required_account_codes") or []]
        if (
            not metric
            or not rule_id
            or not required
            or any(not value for value in [*required, *forbidden, *account_codes])
            or any(not value.isdigit() for value in account_codes)
        ):
            raise ValueError("metric phrase rule is incomplete")
        if metric in result:
            raise ValueError("metric phrase rules must have one rule per metric")
        result[metric] = {
            "rule_id": rule_id,
            "required": tuple(required),
            "forbidden": tuple(forbidden),
            "required_account_codes": tuple(account_codes),
            "primary_statement_only": bool(raw.get("primary_statement_only", False)),
        }
    if not result:
        raise ValueError("metric phrase config contains no rules")
    return result


def _matches_rule(label: object, rule: Mapping[str, Any]) -> bool:
    compact = _compact(label)
    return bool(compact) and all(fragment in compact for fragment in rule["required"]) and not any(
        fragment in compact for fragment in rule["forbidden"]
    )


def _row_has_required_account_code(cells: Iterable[object], rule: Mapping[str, Any]) -> bool:
    """Require a standard statement line code only when the rule declares one."""
    required = {str(value) for value in rule.get("required_account_codes") or []}
    return not required or bool(required & {str(cell).strip() for cell in cells})


def _is_primary_statement(asset: Mapping[str, Any]) -> bool:
    """Keep a report's main statement distinct from segment/detail schedules.

    The corpus can classify both a principal balance sheet and a segment note
    as ``balance_sheet``.  Only the former has the period-comparison purpose
    needed for a headline stock/flow metric.  This is a schema constraint, not
    a score threshold or a source-binding shortcut.
    """
    function = str((asset.get("table_function") or {}).get("kind") or "")
    purpose = str((asset.get("table_purpose") or {}).get("kind") or "")
    return function in {"balance_sheet", "income_statement", "cash_flow_statement"} and purpose == "period_comparison"


def _continuation_key(
    *,
    source_row: Mapping[str, Any],
    table: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
) -> tuple[tuple[str, str, str, str, str], str] | None:
    """Identify only an OCR row duplicated across two consecutive pages.

    A continuation is never assumed from label similarity alone.  It needs a
    complementary page marker and a hash of the complete numeric-cell vector
    from the two parsed rows; no raw value is emitted or persisted.
    """
    if table is None or context is None:
        return None
    compact = _compact(source_row.get("row_label"))
    marker = (
        "next" if "chuyensangtrangsau" in compact else
        "previous" if "mangsangtutrangtruoc" in compact else
        ""
    )
    if not marker:
        return None
    label = compact.replace("chuyensangtrangsau", "").replace("mangsangtutrangtruoc", "")
    row_index = int(source_row.get("row_index", -1))
    rows = table.get("rows") or []
    numeric_columns = [int(value) for value in source_row.get("numeric_cell_indices") or []]
    if not (0 <= row_index < len(rows)) or not numeric_columns or any(
        column < 0 or column >= len(rows[row_index]) for column in numeric_columns
    ):
        return None
    vector_sha = hashlib.sha256(
        "\u241f".join(str(rows[row_index][column]) for column in numeric_columns).encode("utf-8")
    ).hexdigest()
    key = (
        str(table.get("document_id") or ""),
        str(source_row.get("observed_scope") or ""),
        str(((context.get("table_function") or {}).get("kind")) or ""),
        label,
        vector_sha,
    )
    return key, marker


def _collapse_verified_page_continuations(
    *,
    source_rows: Iterable[Mapping[str, Any]],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse a proven two-page OCR duplicate to its first page navigation.

    The exact page-level source remains unresolved evidence.  This only avoids
    counting a duplicate continuation twice during research navigation.
    """
    rows = [dict(row) for row in source_rows]
    grouped: defaultdict[tuple[str, str, str, str, str], list[tuple[int, str, int]]] = defaultdict(list)
    for index, row in enumerate(rows):
        uid = str(row.get("internal_table_uid") or "")
        table, context = tables.get(uid), contexts.get(uid)
        candidate = _continuation_key(source_row=row, table=table, context=context)
        if candidate is None or table is None:
            continue
        key, marker = candidate
        try:
            ordinal = int(table.get("local_ordinal") or -1)
        except (TypeError, ValueError):
            continue
        grouped[key].append((index, marker, ordinal))
    remove: set[int] = set()
    for group in grouped.values():
        next_rows = [item for item in group if item[1] == "next"]
        previous_rows = [item for item in group if item[1] == "previous"]
        if len(next_rows) != 1 or len(previous_rows) != 1:
            continue
        first, second = next_rows[0], previous_rows[0]
        if abs(first[2] - second[2]) != 1:
            continue
        # Keep the earlier physical page deterministically.  The data vector
        # equality is already part of the grouping key.
        remove.add(second[0] if first[2] < second[2] else first[0])
    return [row for index, row in enumerate(rows) if index not in remove], len(remove)


def _semantic_rows(
    *,
    table_candidates: Iterable[Mapping[str, Any]],
    assets: Mapping[str, Mapping[str, Any]],
    rule: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if rule is None:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for candidate in table_candidates:
        table_uid = str(candidate.get("internal_table_uid") or "")
        asset = assets.get(table_uid)
        if asset is None:
            raise ValueError("metric phrase candidate table is missing from immutable assets")
        if bool(rule.get("primary_statement_only")) and not _is_primary_statement(asset):
            continue
        for row_index, raw_row in enumerate(asset.get("rows") or []):
            cells = [str(cell) for cell in raw_row]
            if not _row_has_required_account_code(cells, rule):
                continue
            numeric_indices = [
                index for index, cell in enumerate(cells[1:], start=1)
                if NUMERIC_CELL_RE.search(cell)
            ]
            if not numeric_indices:
                continue
            matches = [
                (column_index, cell)
                for column_index, cell in enumerate(cells)
                if _matches_rule(cell, rule)
            ]
            for column_index, label in matches:
                key = (table_uid, row_index, column_index)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "question_id": int(candidate["question_id"]),
                        "operand_id": str(candidate["operand_id"]),
                        "ticker": str(candidate["ticker"]),
                        "report_year": int(candidate["report_year"]),
                        # Table-candidate packets retain the corpus field as
                        # ``scope``; review-row packets call it
                        # ``observed_scope``.  Preserve either spelling so a
                        # phrase recheck cannot erase the separate/consolidated
                        # distinction before the source gates inspect it.
                        "observed_scope": candidate.get("observed_scope", candidate.get("scope")),
                        "internal_table_uid": table_uid,
                        "row_index": row_index,
                        "row_rank": len(rows) + 1,
                        "row_label": label,
                        "row_label_column_index": column_index,
                        "row_token_jaccard": 1.0,
                        "numeric_cell_indices": numeric_indices,
                    }
                )
    return rows


def _combined_status(*statuses: Mapping[str, Any]) -> str:
    """Combine independent period interpretations without granting authority."""
    if any(
        status.get("status") == "UNIQUE_STRICT_SOURCE_ROW_RESOLVED"
        or status.get("operand_status") in {
            "UNIQUE_CURRENT_PERIOD_RECHECK_CANDIDATE",
            "UNIQUE_EXPLICIT_YEAR_END_HEADER_CANDIDATE",
        }
        for status in statuses
    ):
        return "UNIQUE_SEMANTIC_NAVIGATION_CANDIDATE"
    if any(
        status.get("status") == "MULTIPLE_STRICT_SOURCE_CANDIDATES"
        or status.get("operand_status") in {
            "MULTIPLE_CURRENT_PERIOD_RECHECK_CANDIDATES",
            "MULTIPLE_EXPLICIT_YEAR_END_HEADER_CANDIDATES",
        }
        for status in statuses
    ):
        return "MULTIPLE_SEMANTIC_NAVIGATION_CANDIDATES"
    return "SEMANTIC_SOURCE_GATES_INCOMPLETE"


def _explicit_year_end_header_candidate(
    *,
    row: Mapping[str, Any],
    operand: Mapping[str, Any],
    table: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    minimum_row_jaccard: float,
) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    """Recheck a year-end date header for a typed stock-value table.

    This is deliberately narrower than a generic date matcher.  It accepts
    only one ``31/12/YYYY`` header on a typed balance sheet, or on an explicit
    ``financial_note_detail`` contract, and takes the unit from that same
    hash-bound header.  A date alone never bypasses source, scope, row-profile,
    provenance, or row-margin gates.
    """
    requested_year = int((operand.get("years") or [0])[0])
    row_index = int(row["row_index"]) if row.get("row_index") is not None else -1
    checks: dict[str, bool] = {
        "row_score_threshold": float(row.get("row_token_jaccard") or 0.0) >= minimum_row_jaccard,
        "v2_v3_table_present": table is not None and context is not None,
        "review_year_matches_operand": int(row.get("report_year") or 0) == requested_year,
        "review_ticker_matches_operand": str(row.get("ticker") or "").upper() == str(operand.get("ticker") or "").upper(),
    }
    if not all(checks.values()) or table is None or context is None:
        return None, checks
    checks["v2_v3_source_aligned"] = _source_aligned(table, context)
    requested_scope = operand.get("scope")
    checks["scope_compatible"] = requested_scope is None or row.get("observed_scope") == requested_scope
    kind = str(((context.get("table_function") or {}).get("kind")) or "")
    checks["table_function_matches_operand"] = _allowed_table_function(operand, kind)
    checks["year_end_table_semantics_compatible"] = (
        kind == "balance_sheet"
        or (kind == "financial_note" and "financial_note_detail" in {str(value) for value in operand.get("allowed_table_functions") or []})
    )
    profiles = [profile for profile in context.get("row_profiles") or [] if profile.get("row_index") == row_index]
    numeric_columns = set(profiles[0].get("numeric_columns") or []) if len(profiles) == 1 else set()
    unreliable_columns = set(profiles[0].get("unreliable_numeric_columns") or []) if len(profiles) == 1 else set()
    checks["selected_row_has_v3_profile"] = len(profiles) == 1
    headers = [
        header
        for header in ((context.get("canonical_headers") or {}).get("columns") or [])
        if isinstance(header, Mapping) and int(header.get("column_index") or -1) in numeric_columns
    ]
    matches = []
    for header in headers:
        dates = _dates_in_text(header.get("source_label"))
        if len(dates) == 1 and dates[0].year == requested_year and dates[0].month == 12 and dates[0].day == 31:
            matches.append(header)
    checks["one_explicit_year_end_header"] = len(matches) == 1
    if not all(checks.values()):
        return None, checks
    header = matches[0]
    column_index = int(header.get("column_index") or -1)
    unit, _, multiplier = resolve_source_unit([{"raw_source_cell": str(header.get("source_label") or "")}])
    checks["unique_unit_in_year_end_header"] = unit is not None and multiplier is not None
    checks["selected_cell_reliable_numeric"] = column_index in numeric_columns and column_index not in unreliable_columns
    coordinates, header_texts = _header_coordinates_valid(table=table, header=header)
    checks["header_coordinates_valid"] = bool(coordinates)
    rows, provenance = table.get("rows") or [], table.get("cell_provenance") or []
    checks["value_coordinate_has_v2_provenance"] = bool(
        0 <= row_index < len(rows)
        and 0 <= column_index < len(rows[row_index])
        and row_index < len(provenance)
        and column_index < len(provenance[row_index])
        and isinstance(provenance[row_index][column_index], Mapping)
    )
    locator = _locator(table)
    checks["exact_table_locator_available"] = locator is not None
    if not all(checks.values()) or locator is None:
        return None, checks
    return {
        "question_id": int(row["question_id"]),
        "internal_table_uid": str(table["internal_table_uid"]),
        "document_id": str(table["document_id"]),
        "requested_year": requested_year,
        "scope_status": "SCOPE_NOT_REQUESTED" if requested_scope is None else "SCOPE_MATCH",
        "exact_table_locator": locator,
        "matching_year_column_indices": [column_index],
        "column_headers": [{"column_index": column_index, "header_sha256": _sha_header_text(header_texts)}],
    }, checks


def _sha_header_text(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _explicit_year_end_header_status(
    *,
    source_rows: Iterable[Mapping[str, Any]],
    operand: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    minimum_row_jaccard: float,
    minimum_row_margin: float,
) -> dict[str, Any]:
    rows = list(source_rows)
    all_rows_by_uid: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for source_row in rows:
        all_rows_by_uid[str(source_row.get("internal_table_uid") or "")].append(source_row)
    first_stage: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
    failed = Counter()
    for source_row in rows:
        uid = str(source_row.get("internal_table_uid") or "")
        candidate, checks = _explicit_year_end_header_candidate(
            row=source_row,
            operand=operand,
            table=tables.get(uid),
            context=contexts.get(uid),
            minimum_row_jaccard=minimum_row_jaccard,
        )
        if candidate is None:
            failed.update(name for name, passed in checks.items() if passed is False)
        else:
            first_stage.append((candidate, source_row))
    accepted: dict[tuple[str, int, int], dict[str, Any]] = {}
    second_failed = Counter()
    for candidate, source_row in first_stage:
        uid = str(candidate["internal_table_uid"])
        final, checks = _current_header_row_margin_candidate(
            candidate=candidate,
            rows=_row_candidates(all_rows_by_uid[uid], table_uid=uid),
            table=tables.get(uid),
            context=contexts.get(uid),
            minimum_row_jaccard=minimum_row_jaccard,
            minimum_row_margin=minimum_row_margin,
        )
        if final is None:
            second_failed.update(name for name, passed in checks.items() if passed is False)
        else:
            table_uid, row_index, column_index = final
            identity = f"{table_uid}:{row_index}:{column_index}"
            accepted[final] = {
                "navigation_candidate_sha256": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                "document_identity_sha256": hashlib.sha256(str(candidate.get("document_id") or "").encode("utf-8")).hexdigest(),
                "observed_scope": source_row.get("observed_scope"),
                "table_function": str(((contexts[uid].get("table_function") or {}).get("kind")) or ""),
                "period_resolution": "explicit_year_end_header",
            }
    status = (
        "UNIQUE_EXPLICIT_YEAR_END_HEADER_CANDIDATE"
        if len(accepted) == 1
        else "MULTIPLE_EXPLICIT_YEAR_END_HEADER_CANDIDATES"
        if len(accepted) > 1
        else "EXPLICIT_YEAR_END_HEADER_RECHECK_INCOMPLETE"
    )
    return {
        "operand_status": status,
        "first_stage_explicit_date_header_candidate_count": len(first_stage),
        "strict_source_candidate_count": len(accepted),
        "navigation_candidates": [accepted[key] for key in sorted(accepted)],
        "first_stage_failed_gate_counts": dict(sorted(failed.items())),
        "second_stage_failed_gate_counts": dict(sorted(second_failed.items())),
    }


def _header_role_matches(
    *,
    source_label: object,
    requested_year: int,
    period_role: str,
    required_compact_fragments: Iterable[str],
) -> bool:
    """Match a financial-note column by its complete semantic role.

    Some OCR tables place several named values on one visual row.  A calendar
    year alone then cannot distinguish ``Số phải nộp đầu năm`` from ``Số phải
    nộp cuối năm`` (or from ``Số phải thu``).  This helper needs both the
    declared role and the requested account fragments.  It is value-blind and
    never interprets a numeric token as an answer.
    """
    label = str(source_label or "")
    compact = _compact(label)
    required = tuple(str(value) for value in required_compact_fragments)
    if not compact or not required or not all(value in compact for value in required):
        return False
    dates = _dates_in_text(label)
    relevant_dates = [value for value in dates if value.year == requested_year]
    if dates and not relevant_dates:
        return False
    # A carrying date such as ``1/1/YYYY`` can appear before every movement
    # column in a compact OCR row.  ``trong năm`` is a flow, not an opening
    # balance, so it must override that otherwise tempting date signal.
    in_year_movement = "trongnam" in compact
    opening = ("daunam" in compact or any(value.month == 1 and value.day == 1 for value in relevant_dates)) and not in_year_movement
    closing = ("cuoinam" in compact or any(value.month == 12 and value.day == 31 for value in relevant_dates)) and not in_year_movement
    return (period_role == "opening" and opening and not closing) or (period_role == "closing" and closing and not opening)


def _role_header_candidate(
    *,
    row: Mapping[str, Any],
    operand: Mapping[str, Any],
    table: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    period_role: str,
    required_header_compact_fragments: Iterable[str],
    minimum_row_jaccard: float,
) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    """Select one source-backed column by opening/closing and account role.

    This remains a navigation-only check.  The exact value coordinate is
    checked for V2 provenance but no value is read, written, or used to form
    an answer.
    """
    requested_year = int((operand.get("years") or [0])[0])
    row_index = int(row["row_index"]) if row.get("row_index") is not None else -1
    required = tuple(str(value) for value in required_header_compact_fragments)
    checks: dict[str, bool] = {
        "known_period_role": period_role in {"opening", "closing"},
        "nonempty_compact_header_role": bool(required) and all(_compact(value) == value and bool(value) for value in required),
        "row_score_threshold": float(row.get("row_token_jaccard") or 0.0) >= minimum_row_jaccard,
        "v2_v3_table_present": table is not None and context is not None,
        "review_year_matches_operand": int(row.get("report_year") or 0) == requested_year,
        "review_ticker_matches_operand": str(row.get("ticker") or "").upper() == str(operand.get("ticker") or "").upper(),
    }
    if not all(checks.values()) or table is None or context is None:
        return None, checks
    checks["v2_v3_source_aligned"] = _source_aligned(table, context)
    requested_scope = operand.get("scope")
    checks["scope_compatible"] = requested_scope is None or row.get("observed_scope") == requested_scope
    kind = str(((context.get("table_function") or {}).get("kind")) or "")
    checks["table_function_matches_operand"] = _allowed_table_function(operand, kind)
    checks["role_header_table_semantics_compatible"] = kind == "financial_note"
    profiles = [profile for profile in context.get("row_profiles") or [] if profile.get("row_index") == row_index]
    checks["selected_row_has_v3_profile"] = len(profiles) == 1
    all_headers = [
        header
        for header in ((context.get("canonical_headers") or {}).get("columns") or [])
        if isinstance(header, Mapping)
    ]
    role_headers_before_numeric_gate = [
        header
        for header in all_headers
        if _header_role_matches(
            source_label=header.get("source_label"),
            requested_year=requested_year,
            period_role=period_role,
            required_compact_fragments=required,
        )
    ]
    checks["role_header_exists_before_row_numeric_gate"] = bool(role_headers_before_numeric_gate)
    matches = role_headers_before_numeric_gate
    checks["one_complete_role_header"] = len(matches) == 1
    if not all(checks.values()):
        return None, checks
    header = matches[0]
    column_index = int(header.get("column_index") or -1)
    unit, _, multiplier = resolve_source_unit([{"raw_source_cell": str(header.get("source_label") or "")}])
    checks["unique_unit_in_role_header"] = unit is not None and multiplier is not None
    rows, provenance = table.get("rows") or [], table.get("cell_provenance") or []
    checks["selected_cell_has_v2_numeric_token"] = bool(
        0 <= row_index < len(rows)
        and 0 <= column_index < len(rows[row_index])
        and NUMERIC_CELL_RE.search(str(rows[row_index][column_index]))
    )
    coordinates, header_texts = _header_coordinates_valid(table=table, header=header)
    checks["header_coordinates_valid"] = bool(coordinates)
    checks["header_source_is_distinct_or_exact_value_cell"] = bool(coordinates) and all(
        int(coordinate["row_index"]) != row_index or int(coordinate["column_index"]) == column_index
        for coordinate in coordinates
    )
    checks["value_coordinate_has_v2_provenance"] = bool(
        0 <= row_index < len(rows)
        and 0 <= column_index < len(rows[row_index])
        and row_index < len(provenance)
        and column_index < len(provenance[row_index])
        and isinstance(provenance[row_index][column_index], Mapping)
    )
    locator = _locator(table)
    checks["exact_table_locator_available"] = locator is not None
    if not all(checks.values()) or locator is None:
        return None, checks
    return {
        "question_id": int(row["question_id"]),
        "internal_table_uid": str(table["internal_table_uid"]),
        "document_id": str(table["document_id"]),
        "requested_year": requested_year,
        "scope_status": "SCOPE_NOT_REQUESTED" if requested_scope is None else "SCOPE_MATCH",
        "exact_table_locator": locator,
        "matching_year_column_indices": [column_index],
        "column_headers": [{"column_index": column_index, "header_sha256": _sha_header_text(header_texts)}],
    }, checks


def _self_describing_role_cell_candidate(
    *,
    row: Mapping[str, Any],
    operand: Mapping[str, Any],
    table: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    period_role: str,
    required_header_compact_fragments: Iterable[str],
    minimum_row_jaccard: float,
) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    """Check an OCR cell which carries its own role, unit, and numeric text.

    This is not a loose fallback for an absent header.  It covers the narrow
    layout where a financial-note row embeds several labelled values directly
    in its cells (for example opening/payable and closing/payable).  The role
    and the numeric token must be in the *same* V2-provenanced cell.
    """
    requested_year = int((operand.get("years") or [0])[0])
    row_index = int(row["row_index"]) if row.get("row_index") is not None else -1
    required = tuple(str(value) for value in required_header_compact_fragments)
    checks: dict[str, bool] = {
        "known_period_role": period_role in {"opening", "closing"},
        "nonempty_compact_header_role": bool(required) and all(_compact(value) == value and bool(value) for value in required),
        "row_score_threshold": float(row.get("row_token_jaccard") or 0.0) >= minimum_row_jaccard,
        "v2_v3_table_present": table is not None and context is not None,
        "review_year_matches_operand": int(row.get("report_year") or 0) == requested_year,
        "review_ticker_matches_operand": str(row.get("ticker") or "").upper() == str(operand.get("ticker") or "").upper(),
    }
    if not all(checks.values()) or table is None or context is None:
        return None, checks
    checks["v2_v3_source_aligned"] = _source_aligned(table, context)
    requested_scope = operand.get("scope")
    checks["scope_compatible"] = requested_scope is None or row.get("observed_scope") == requested_scope
    kind = str(((context.get("table_function") or {}).get("kind")) or "")
    checks["table_function_matches_operand"] = _allowed_table_function(operand, kind)
    checks["self_describing_role_cell_table_semantics_compatible"] = kind == "financial_note"
    profiles = [profile for profile in context.get("row_profiles") or [] if profile.get("row_index") == row_index]
    checks["selected_row_has_v3_profile"] = len(profiles) == 1
    rows, provenance = table.get("rows") or [], table.get("cell_provenance") or []
    source_row = rows[row_index] if 0 <= row_index < len(rows) else []
    matches = [
        (column_index, str(cell))
        for column_index, cell in enumerate(source_row)
        if NUMERIC_CELL_RE.search(str(cell))
        and _header_role_matches(
            source_label=cell,
            requested_year=requested_year,
            period_role=period_role,
            required_compact_fragments=required,
        )
    ]
    checks["one_self_describing_role_value_cell"] = len(matches) == 1
    if not all(checks.values()):
        return None, checks
    column_index, source_text = matches[0]
    unit, _, multiplier = resolve_source_unit([{"raw_source_cell": source_text}])
    checks["unique_unit_in_self_describing_role_cell"] = unit is not None and multiplier is not None
    checks["value_coordinate_has_v2_provenance"] = bool(
        row_index < len(provenance)
        and column_index < len(provenance[row_index])
        and isinstance(provenance[row_index][column_index], Mapping)
    )
    locator = _locator(table)
    checks["exact_table_locator_available"] = locator is not None
    if not all(checks.values()) or locator is None:
        return None, checks
    return {
        "question_id": int(row["question_id"]),
        "internal_table_uid": str(table["internal_table_uid"]),
        "document_id": str(table["document_id"]),
        "requested_year": requested_year,
        "scope_status": "SCOPE_NOT_REQUESTED" if requested_scope is None else "SCOPE_MATCH",
        "exact_table_locator": locator,
        "matching_year_column_indices": [column_index],
        "column_headers": [{"column_index": column_index, "header_sha256": _sha_header_text([source_text])}],
    }, checks


def _role_header_status(
    *,
    source_rows: Iterable[Mapping[str, Any]],
    operand: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
    period_role: str,
    required_header_compact_fragments: Iterable[str],
    minimum_row_jaccard: float,
    minimum_row_margin: float,
) -> dict[str, Any]:
    """Apply the complete header-role gate without choosing an unasked scope."""
    rows = list(source_rows)
    all_rows_by_uid: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for source_row in rows:
        all_rows_by_uid[str(source_row.get("internal_table_uid") or "")].append(source_row)
    first_stage: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
    failed = Counter()
    for source_row in rows:
        uid = str(source_row.get("internal_table_uid") or "")
        candidate, checks = _role_header_candidate(
            row=source_row,
            operand=operand,
            table=tables.get(uid),
            context=contexts.get(uid),
            period_role=period_role,
            required_header_compact_fragments=required_header_compact_fragments,
            minimum_row_jaccard=minimum_row_jaccard,
        )
        if candidate is None:
            failed.update(f"header_{name}" for name, passed in checks.items() if passed is False)
        else:
            first_stage.append((candidate, source_row))
        self_candidate, self_checks = _self_describing_role_cell_candidate(
            row=source_row,
            operand=operand,
            table=tables.get(uid),
            context=contexts.get(uid),
            period_role=period_role,
            required_header_compact_fragments=required_header_compact_fragments,
            minimum_row_jaccard=minimum_row_jaccard,
        )
        if self_candidate is None:
            failed.update(f"self_cell_{name}" for name, passed in self_checks.items() if passed is False)
        else:
            first_stage.append((self_candidate, source_row))
    accepted: dict[tuple[str, int, int], dict[str, Any]] = {}
    second_failed = Counter()
    for candidate, source_row in first_stage:
        uid = str(candidate["internal_table_uid"])
        final, checks = _current_header_row_margin_candidate(
            candidate=candidate,
            rows=_row_candidates(all_rows_by_uid[uid], table_uid=uid),
            table=tables.get(uid),
            context=contexts.get(uid),
            minimum_row_jaccard=minimum_row_jaccard,
            minimum_row_margin=minimum_row_margin,
        )
        if final is None:
            second_failed.update(name for name, passed in checks.items() if passed is False)
            continue
        table_uid, row_index, column_index = final
        identity = f"{table_uid}:{row_index}:{column_index}"
        accepted[final] = {
            "navigation_candidate_sha256": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            "document_identity_sha256": hashlib.sha256(str(candidate.get("document_id") or "").encode("utf-8")).hexdigest(),
            "observed_scope": source_row.get("observed_scope"),
            "table_function": str(((contexts[uid].get("table_function") or {}).get("kind")) or ""),
            "period_resolution": f"complete_{period_role}_header_role",
        }
    observed_scopes = sorted({str(row.get("observed_scope")) for row in rows if row.get("observed_scope")})
    scope_unresolved = operand.get("scope") is None and len(observed_scopes) > 1
    status = (
        "MULTIPLE_SCOPE_UNRESOLVED_ROLE_HEADER_CANDIDATES"
        if scope_unresolved and accepted
        else "UNIQUE_ROLE_HEADER_CANDIDATE"
        if len(accepted) == 1
        else "MULTIPLE_ROLE_HEADER_CANDIDATES"
        if len(accepted) > 1
        else "ROLE_HEADER_RECHECK_INCOMPLETE"
    )
    return {
        "operand_status": status,
        "first_stage_role_header_candidate_count": len(first_stage),
        "strict_source_candidate_count": len(accepted),
        "navigation_candidates": [accepted[key] for key in sorted(accepted)],
        "observed_scope_count": len(observed_scopes),
        "scope_unresolved": scope_unresolved,
        "first_stage_failed_gate_counts": dict(sorted(failed.items())),
        "second_stage_failed_gate_counts": dict(sorted(second_failed.items())),
    }


def build_staged_formula_metric_phrase_probe(
    *,
    config_path: Path,
    plans_path: Path,
    candidate_artifact_dir: Path,
    baseline_literal_artifact_dir: Path,
    baseline_current_artifact_dir: Path,
    full_assets_path: Path,
    full_assets_manifest_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_row_jaccard: float = 0.8,
    minimum_row_margin: float = 0.1,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    if not 0.0 <= minimum_row_jaccard <= 1.0 or not 0.0 <= minimum_row_margin <= 1.0:
        raise ValueError("source thresholds must be between zero and one")
    rules = _rules(config_path)
    validate_candidate_artifact(candidate_artifact_dir, expected_question_count=expected_question_count)
    validate_staged_formula_hydrated_source_probe(baseline_literal_artifact_dir, expected_question_count=expected_question_count)
    validate_staged_formula_hydrated_current_period_probe(baseline_current_artifact_dir, expected_question_count=expected_question_count)
    candidate_manifest = _read_json(candidate_artifact_dir / "manifest.json")
    table_candidate_path = candidate_artifact_dir / "table_candidates_v1.jsonl"
    table_descriptor = (candidate_manifest.get("outputs") or {}).get(table_candidate_path.name) or {}
    if sha256_file(table_candidate_path) != table_descriptor.get("sha256"):
        raise ValueError("candidate table list does not match its manifest")
    _require_asset_manifest(full_assets_path, full_assets_manifest_path)
    context_manifest = _read_json(evidence_context_manifest_path)
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 evidence context hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(plans_path)}
    if set(plans) != set(range(1, expected_question_count + 1)):
        raise ValueError("plans do not cover the expected population")
    literal_baseline = {
        (int(row["question_id"]), str(row["operand_id"])): row
        for row in _read_jsonl(baseline_literal_artifact_dir / "staged_formula_hydrated_operand_probe_v1.jsonl")
    }
    current_baseline = {
        (int(row["question_id"]), str(row["operand_id"])): row
        for row in _read_jsonl(baseline_current_artifact_dir / "staged_formula_hydrated_current_period_operand_probe_v1.jsonl")
    }
    target_keys = {
        key for key, literal in literal_baseline.items()
        if literal.get("hydrated", {}).get("status") == "STRICT_SOURCE_GATES_INCOMPLETE"
        and current_baseline.get(key, {}).get("hydrated", {}).get("operand_status") == "CURRENT_PERIOD_RECHECK_INCOMPLETE"
    }
    if not target_keys:
        raise ValueError("metric phrase probe has no baseline-incomplete operands")
    target_question_ids = {question_id for question_id, _ in target_keys}
    if any(plans[question_id].get("decomposition_status") != "typed_non_executable" for question_id in target_question_ids):
        raise ValueError("metric phrase probe targets must be typed staged plans")
    candidates_by_key: defaultdict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    requested_uids: set[str] = set()
    for candidate in _read_jsonl(table_candidate_path):
        key = (int(candidate.get("question_id") or 0), str(candidate.get("operand_id") or ""))
        if key not in target_keys:
            continue
        candidates_by_key[key].append(candidate)
        requested_uids.add(str(candidate.get("internal_table_uid") or ""))
    if not requested_uids or "" in requested_uids:
        raise ValueError("metric phrase probe has no usable candidate tables")
    assets = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(full_assets_path)
        if str(row.get("internal_table_uid") or "") in requested_uids
    }
    if requested_uids - set(assets):
        raise ValueError("metric phrase candidate table is absent from immutable assets")
    current_tables = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(structured_tables_path)
        if str(row.get("internal_table_uid") or "") in requested_uids
    }
    current_contexts = {
        str(row.get("internal_table_uid") or ""): row
        for row in _read_jsonl(evidence_context_path)
        if str(row.get("internal_table_uid") or "") in requested_uids
    }
    complete_ids = set(current_tables) & set(current_contexts)
    hydrate_ids = requested_uids - complete_ids
    hydrate_assets = {uid: assets[uid] for uid in hydrate_ids}
    source_cache: dict[Path, tuple[str, str]] = {}
    hydrated_tables: dict[str, dict[str, Any]] = {}
    hydrated_contexts: dict[str, dict[str, Any]] = {}
    failures = Counter()
    for table_uid in sorted(hydrate_ids):
        try:
            table, context = _hydrate(hydrate_assets[table_uid], source_cache=source_cache)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            failures[str(error)] += 1
            continue
        hydrated_tables[table_uid] = table
        hydrated_contexts[table_uid] = context
    tables = {**current_tables, **hydrated_tables}
    contexts = {**current_contexts, **hydrated_contexts}

    audit: list[dict[str, Any]] = []
    for question_id, operand_id in sorted(target_keys):
        operand = next(
            (row for row in plans[question_id].get("operands") or [] if str(row.get("operand_id") or "") == operand_id),
            None,
        )
        if not isinstance(operand, Mapping):
            raise ValueError("baseline target is absent from typed plan")
        metric_hints = operand.get("metric_hints") or []
        metric = normalize_label(metric_hints[1] if len(metric_hints) > 1 else metric_hints[0] if metric_hints else "")
        rule = rules.get(metric)
        raw_semantic_rows = _semantic_rows(
            table_candidates=candidates_by_key[(question_id, operand_id)],
            assets=assets,
            rule=rule,
        )
        semantic_rows, page_continuation_collapsed_row_count = _collapse_verified_page_continuations(
            source_rows=raw_semantic_rows,
            tables=tables,
            contexts=contexts,
        )
        subplan = _subplan(question_id=question_id, operand=operand)
        if subplan is None:
            literal = {
                "status": "OPERAND_PLAN_NOT_SINGLE_ENTITY_AND_YEAR",
                "strict_source_candidate_count": 0,
                "navigation_candidates": [],
                "first_stage_failed_gate_counts": {},
                "second_stage_failed_gate_counts": {},
            }
        else:
            literal = _strict_status(
                source_rows=semantic_rows,
                plan=subplan,
                tables=tables,
                contexts=contexts,
                minimum_row_jaccard=minimum_row_jaccard,
                minimum_row_margin=minimum_row_margin,
            )
        current = _current_period_status(
            source_rows=semantic_rows,
            operand=operand,
            tables=tables,
            contexts=contexts,
            minimum_row_jaccard=minimum_row_jaccard,
            minimum_row_margin=minimum_row_margin,
        )
        explicit_date = _explicit_year_end_header_status(
            source_rows=semantic_rows,
            operand=operand,
            tables=tables,
            contexts=contexts,
            minimum_row_jaccard=minimum_row_jaccard,
            minimum_row_margin=minimum_row_margin,
        )
        record = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "operand_id": operand_id,
            "rule_id": None if rule is None else rule["rule_id"],
            "semantic_match_row_count": len(semantic_rows),
            "raw_semantic_match_row_count": len(raw_semantic_rows),
            "page_continuation_collapsed_row_count": page_continuation_collapsed_row_count,
            "literal": literal,
            "current": current,
            "explicit_date": explicit_date,
            "combined_status": _combined_status(literal, current, explicit_date),
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(record):
            raise ValueError("metric phrase probe leaked a forbidden field")
        audit.append(record)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "baseline_incomplete_operand_count": len(target_keys),
        "semantic_rule_match_operand_count": sum(row["semantic_match_row_count"] > 0 for row in audit),
        "combined_status_counts": dict(sorted(Counter(row["combined_status"] for row in audit).items())),
        "rule_counts": dict(sorted(Counter(str(row["rule_id"] or "NO_SEMANTIC_RULE") for row in audit).items())),
        "requested_table_count": len(requested_uids),
        "hydrated_table_count": len(hydrated_tables),
        "hydration_failure_counts": dict(sorted(failures.items())),
        "thresholds": {"minimum_row_jaccard": minimum_row_jaccard, "minimum_row_margin": minimum_row_margin},
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "staged_formula_metric_phrase_probe_v1.jsonl"
        summary_path = temporary / "staged_formula_metric_phrase_probe_summary_v1.json"
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {
            "config": config_path,
            "plans": plans_path,
            "candidate_manifest": candidate_artifact_dir / "manifest.json",
            "baseline_literal_manifest": baseline_literal_artifact_dir / "manifest.json",
            "baseline_current_manifest": baseline_current_artifact_dir / "manifest.json",
            "full_assets": full_assets_path,
            "full_assets_manifest": full_assets_manifest_path,
            "structured_tables_v2": structured_tables_path,
            "evidence_context_v3": evidence_context_path,
            "evidence_context_manifest_v3": evidence_context_manifest_path,
        }
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
                "outputs": {
                    "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                    "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
                },
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_staged_formula_metric_phrase_probe(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected metric phrase probe contract")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("metric phrase probe hash mismatch")
    audit = _read_jsonl(artifact_dir / "staged_formula_metric_phrase_probe_v1.jsonl")
    if not audit or not {int(row["question_id"]) for row in audit} <= set(range(1, expected_question_count + 1)):
        raise ValueError("metric phrase probe question coverage is invalid")
    if any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        for row in audit
    ):
        raise ValueError("metric phrase probe lost its non-authorizing boundary")
    summary = _read_json(artifact_dir / "staged_formula_metric_phrase_probe_summary_v1.json")
    counts = dict(sorted(Counter(row["combined_status"] for row in audit).items()))
    if summary.get("baseline_incomplete_operand_count") != len(audit) or summary.get("combined_status_counts") != counts:
        raise ValueError("metric phrase probe summary mismatch")
    return {
        "status": "PASS",
        "baseline_incomplete_operand_count": len(audit),
        "answer_eligible": False,
        "submission_eligible": False,
    }
