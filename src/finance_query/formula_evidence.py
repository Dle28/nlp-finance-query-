"""Exact-row evidence collection for controlled financial formula templates.

This module deliberately does not calculate a formula or choose a final
answer.  It materializes only auditable operand candidates from raw V2 rows
and canonical source headers, so later reviewers can see whether every input
is truly present before an executor is allowed to run.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .corpus import infer_unit
from .binding import row_label
from .execution import parse_decimal
from .financial_metrics import fold_text, operand_match_score


FORMULA_SOURCE_DISCOVERY_POLICY = "resolved_ticker_operand_year_or_following_report_v1"
FORMULA_SOURCE_DISCOVERY_CANDIDATE_SOURCE = "formula_source_discovery_v1"
FORMULA_BUNDLE_SUPPORT_CANDIDATE_SOURCE = "formula_metadata_support_v1"
FORMULA_SOURCE_DISCOVERY_RANK_BASE = 1_000_000
# This binding policy is distinct from execution: a selected primary source
# remains evidence-only unless its formula family has an explicit executor.
EQUIVALENT_COMPARATIVE_WITNESS_FORMULAS = {
    "operating_cash_flow_argmax_period",
    "cfo_positive_multiyear_max_net_margin",
}
EXECUTION_BACKED_COMPOSED_FORMULAS = {"operating_cash_flow_argmax_period"}
SOURCE_RESOLVABLE_SINGLE_ENTITY_FORMULAS = {"percentage_change"}
FORMULA_OPERAND_ENTITY_RESOLUTION_POLICY = "resolved_single_ticker_attached_to_formula_operands_v1"
BALANCE_SHEET_STRUCTURAL_PREFIX_RE = re.compile(
    r"^\s*(?:(?:[IVXLCDM]+|\d+(?:\.\d+)*)\s*[.)]\s*)+", re.IGNORECASE
)


def attach_resolved_single_entity(
    formula: Mapping[str, Any], resolution: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Attach one already-proven ticker to controlled formula operands.

    Source completion needs the entity on each operand to locate the matching
    raw report.  We only attach it for the single-entity percentage-change
    template after a title-zone or exact-ticker resolver has already produced
    one ticker.  The source formula, metric hints, years and scope remain
    unchanged; no company name is guessed from the metric itself.
    """
    output = dict(formula)
    ticker = str((resolution or {}).get("ticker") or "")
    operands = list(formula.get("operands") or [])
    if (
        not ticker
        or str(formula.get("formula_id") or "") not in SOURCE_RESOLVABLE_SINGLE_ENTITY_FORMULAS
        or not operands
        or any(str(operand.get("entity") or "") for operand in operands)
    ):
        return output
    output["operands"] = [{**operand, "entity": ticker} for operand in operands]
    output["operand_entity_resolution"] = {
        "policy": FORMULA_OPERAND_ENTITY_RESOLUTION_POLICY,
        "ticker": ticker,
        "source_resolution_policy": str((resolution or {}).get("policy") or ""),
        "scope_inferred": False,
    }
    return output


def _is_balance_sheet_opening_header(label: str, year: int) -> bool:
    """Whether a raw header explicitly names the opening date of ``year``.

    A balance sheet often reports both 31/12/Y and 1/1/Y.  The latter is an
    opening comparative balance, not an alternative annual Y value.  This is
    deliberately a header interpretation only: no date, row or cell text is
    rewritten and any other pair of dates remains ambiguous.
    """
    return bool(
        re.search(
            rf"(?<!\d)0?1\s*[/.-]\s*0?1\s*[/.-]\s*{year}(?!\d)",
            str(label or ""),
        )
    )


def _balance_sheet_row_metric_is_exact(
    operand: Mapping[str, Any], row: list[Any]
) -> bool:
    """Require an exact account-row label for a balance-sheet operand.

    A substring is not enough for balance-sheet totals: ``Tài sản ngắn hạn``
    must not silently bind ``Tài sản ngắn hạn khác``.  Standard row formulas
    such as ``(100 = 110 + ...)`` are structural annotations, so they are
    removed only when the parenthesis begins with a three-digit account code
    and equals sign.  This does not rewrite the source row; it only narrows a
    high-impact operand-binding gate.
    """
    # Some source grids put the account code in c0 and the label in c1;
    # others use c0 for the label.  Read the first non-empty text-bearing cell
    # rather than relying on display-oriented ``row_label`` (which may retain
    # an empty structural c2 as a trailing separator).
    label = next(
        (
            str(value).strip()
            for value in row
            if str(value).strip() and any(character.isalpha() for character in str(value))
        ),
        row_label([str(value) for value in row]),
    )
    structural = re.match(r"^(.*?)(?:\(.*?\b\d{3}\s*=.*)?$", label)
    # Roman/numeric section markers (``I.``, ``1.``) are presentation-only
    # source structure, not part of the account name.  Strip only a marker at
    # the very start; a suffix such as ``khác`` remains and still fails the
    # exact-account gate.
    label_without_formula = structural.group(1) if structural else label
    normalized_label = fold_text(
        BALANCE_SHEET_STRUCTURAL_PREFIX_RE.sub("", label_without_formula)
    )
    return bool(normalized_label) and any(
        fold_text(str(hint)) == normalized_label
        for hint in operand.get("metric_hints") or []
        if fold_text(str(hint))
    )


def _income_statement_total_net_profit_row_is_exact(
    operand: Mapping[str, Any], row: list[Any]
) -> bool:
    """Keep a total net-profit operand off parent/NCI component rows.

    ``Lợi nhuận sau thuế`` occurs three times in a standard consolidated
    income statement: the total, the parent share and the non-controlling
    share.  A lexical score cannot distinguish them.  This gate is deliberately
    limited to the controlled net-margin numerator role and accepts only the
    official total labels after removing a source row number and account-code
    equation.  It does not alter the raw cell text.
    """
    if str(operand.get("role") or "") != "net_margin_numerator":
        return True
    label = next(
        (
            str(value).strip()
            for value in row
            if str(value).strip() and any(character.isalpha() for character in str(value))
        ),
        "",
    )
    label = re.sub(r"^\s*\d+(?:\.\d+)*\s*[.)]?\s*", "", label)
    # Annual statement totals use both ``(60 = 50 - 51 - 52)`` and the
    # shortened ``(50-51-52)`` notation. Strip only a parenthesized numeric
    # equation, never prose in a source row label.
    label = re.sub(r"\s*\(\s*[\d\s=+*/.-]+\)\s*$", "", label)
    normalized_label = fold_text(label)
    return normalized_label in {
        "loi nhuan sau thue",
        "loi nhuan sau thue thu nhap doanh nghiep",
        "loi nhuan sau thue tndn",
        "loi nhuan rong",
    }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def formula_operand_years(formula: Mapping[str, Any]) -> set[int]:
    """Return explicitly controlled operand years; never derive one from OCR."""
    return {
        year
        for operand in formula.get("operands") or []
        for value in operand.get("years") or []
        if (year := _int_or_none(value)) is not None
    }


def source_discovery_candidates(
    item: Mapping[str, Any],
    formula: Mapping[str, Any],
    tables: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Find additional source tables through validated metadata only.

    This is deliberately narrower than generic retrieval: a formula question
    must have resolved ticker(s) and explicit operand years; candidate tables
    must share the ticker, any resolved scope, and a report year that is the
    operand year or the immediately following comparative report.  It creates
    only discovery candidates for exact-row/cell collection and never an
    answer, label or synthetic table. A source-completion table can join only
    after its separate raw-source sidecar has been revalidated; its candidate
    source remains explicit in the EvidenceSet.
    """
    plan = item.get("question_plan") or {}
    tickers = {str(value) for value in plan.get("tickers") or [] if str(value)}
    years = formula_operand_years(formula)
    if not tickers or not years:
        return []
    allowed_report_years = years | {year + 1 for year in years}
    expected_scope = str(plan.get("scope") or "")
    existing = {
        str(candidate.get("internal_table_uid") or "")
        for candidate in item.get("candidates") or []
    }
    selected: list[Mapping[str, Any]] = []
    for uid, table in tables.items():
        if uid in existing or str(table.get("ticker") or "") not in tickers:
            continue
        if expected_scope and str(table.get("scope") or "") != expected_scope:
            continue
        report_year = _int_or_none(table.get("report_year"))
        if report_year not in allowed_report_years:
            continue
        selected.append(table)
    selected.sort(
        key=lambda table: (
            str(table.get("ticker") or ""),
            str(table.get("scope") or ""),
            _int_or_none(table.get("report_year")) or 0,
            str(table.get("document_id") or ""),
            _int_or_none(table.get("local_ordinal")) or 0,
            str(table.get("internal_table_uid") or ""),
        )
    )
    def candidate_source(table: Mapping[str, Any]) -> str:
        completion_source = (table.get("source_completion") or {}).get("candidate_source")
        if completion_source:
            return str(completion_source)
        bundle_support = (table.get("bundle_inclusion") or {}).get(
            "formula_metadata_support"
        )
        if bundle_support:
            return FORMULA_BUNDLE_SUPPORT_CANDIDATE_SOURCE
        return FORMULA_SOURCE_DISCOVERY_CANDIDATE_SOURCE

    return [
        {
            "internal_table_uid": str(table["internal_table_uid"]),
            "rank": FORMULA_SOURCE_DISCOVERY_RANK_BASE + index,
            "ticker": table.get("ticker"),
            "scope": table.get("scope"),
            "report_year": table.get("report_year"),
            "candidate_source": candidate_source(table),
        }
        for index, table in enumerate(selected)
    ]


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


def _source_unit(table: Mapping[str, Any]) -> str | None:
    """Return only an explicitly declared source unit for cross-report checks."""
    unit_hint = table.get("unit_hint")
    if isinstance(unit_hint, str) and unit_hint:
        return unit_hint
    context = " ".join(
        [
            str(table.get("context_before") or ""),
            str((table.get("context_trace") or {}).get("source_title") or ""),
            " ".join((table.get("context_trace") or {}).get("unit_labels") or []),
        ]
    )
    rows = table.get("rows") or []
    return infer_unit(context, "\n".join(" | ".join(map(str, row)) for row in rows))


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
            # A primary balance sheet can present its closing date alongside
            # `01/01/<year>`.  That opening balance belongs to the preceding
            # period. Select only when one and exactly one non-opening raw
            # header remains; multiple closing/interim headers still fail
            # closed rather than guessing a period.
            if str((context.get("table_function") or {}).get("kind") or "") == "balance_sheet":
                closing = {
                    index
                    for index in explicit
                    if not _is_balance_sheet_opening_header(
                        str(_column_by_index(context, index).get("source_label") or ""),
                        years[0],
                    )
                }
                if len(closing) == 1 and closing != explicit:
                    columns = closing
                    reason = "balance_sheet_closing_header_excludes_opening_date"
                else:
                    return {
                        "status": "ambiguous_period_column",
                        "numeric_columns": numeric_columns,
                        "reason": "multiple raw canonical columns name the operand year",
                    }
            else:
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
            elif str((context.get("table_function") or {}).get("kind") or "") == "balance_sheet":
                # Balance sheets commonly use source headers ``Số cuối năm`` /
                # ``Số đầu năm`` rather than printing the date in every table.
                # The closing balance is attributable to the document report
                # year only when that metadata already equals the requested
                # operand year and exactly one raw canonical numeric column
                # says ``Số cuối năm``.  We never apply this rule to an
                # income/cash-flow statement, an adjacent comparative report,
                # or a duplicate closing column.
                closing_balance = {
                    index
                    for index in columns
                    if "so cuoi nam" in fold_text(
                        str(_column_by_index(context, index).get("source_label") or "")
                    )
                }
                if len(closing_balance) == 1:
                    columns = closing_balance
                    reason = "balance_sheet_closing_balance_matches_report_year"
                else:
                    return {
                        "status": "unbound_period_column",
                        "numeric_columns": numeric_columns,
                        "reason": "operand year has no unique raw canonical closing-balance column",
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
    raw_value = str(rows[row_index][column_index])
    parsed = parse_decimal(raw_value)
    # A formula composes numbers, so it must use the same fail-closed numeric
    # contract as direct execution. In particular, a malformed OCR cell that
    # concatenates adjacent numbers is evidence of neither operand.
    if parsed.value is None or any(
        warning != "percent_value_not_scaled" for warning in parsed.warnings
    ):
        return {
            "status": "unreliable_source_number",
            "raw_value": raw_value,
            "parse_warnings": list(parsed.warnings),
            "reason": "raw V2 source cell is not one reliable numeric value",
        }
    provenance = ((table.get("cell_provenance") or [])[row_index] or [])[column_index]
    return {
        "status": "cell_bound",
        "row_index": row_index,
        "column_index": column_index,
        "column_label": _column_by_index(context, column_index).get("source_label"),
        "raw_value": raw_value,
        "parsed_value": parsed.value,
        "parse_warnings": list(parsed.warnings),
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
    is_balance_sheet_operand = (
        str((context.get("table_function") or {}).get("kind") or "") == "balance_sheet"
    )
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
        if is_balance_sheet_operand and not _balance_sheet_row_metric_is_exact(operand, row):
            continue
        if (
            str((context.get("table_function") or {}).get("kind") or "") == "income_statement"
            and not _income_statement_total_net_profit_row_is_exact(operand, row)
        ):
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
                "candidate_source": candidate.get("candidate_source") or "retrieved",
                "source_unit": _source_unit(table),
                "row_index": row_index,
                "source_row": [str(value) for value in row],
                "match_score": score,
                "binding": binding,
            }
        )
    return output


def multi_entity_scope_diagnostics(
    coverage: Mapping[str, list[dict[str, Any]]],
    operands: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Describe, without selecting, the common-scope gate for many entities.

    The returned intersections explain whether a block comes from raw-report
    coverage or from an ambiguous retrieval set.  It is audit metadata only:
    callers must still fail closed unless one global non-empty scope exists.
    """
    by_entity: dict[str, list[Mapping[str, Any]]] = {}
    display_names: dict[str, str] = {}
    for operand in operands:
        entity = str(operand.get("entity") or "").strip()
        if not entity:
            return {}
        key = entity.casefold()
        by_entity.setdefault(key, []).append(operand)
        display_names.setdefault(key, entity.upper())
    if not by_entity:
        return {}

    entity_common_scopes: dict[str, list[str]] = {}
    operand_scope_sets: dict[str, dict[str, list[str]]] = {}
    scope_sets: list[set[str]] = []
    for entity in sorted(by_entity):
        scopes: set[str] | None = None
        per_operand: dict[str, list[str]] = {}
        for operand in by_entity[entity]:
            operand_id = str(operand["operand_id"])
            available = {
                str(match.get("scope") or "")
                for match in coverage.get(operand_id) or []
                if str(match.get("ticker") or "").casefold() == entity
                and str(match.get("scope") or "")
            }
            per_operand[operand_id] = sorted(available)
            scopes = available if scopes is None else scopes & available
        shared = scopes or set()
        name = display_names[entity]
        entity_common_scopes[name] = sorted(shared)
        operand_scope_sets[name] = per_operand
        scope_sets.append(shared)

    global_common = set.intersection(*scope_sets) if scope_sets else set()
    return {
        "policy": "one_nonempty_scope_per_entity_and_global_common_scope_v1",
        "entity_common_scopes": entity_common_scopes,
        "global_common_scopes": sorted(global_common),
        "operand_scope_sets": operand_scope_sets,
    }


def select_coherent_operand_matches(
    coverage: Mapping[str, list[dict[str, Any]]],
    operands: list[Mapping[str, Any]],
    *,
    allow_equivalent_comparative_witnesses: bool = False,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Select only a unique source-consistent binding for each operand.

    Single-entity formulas require one common ``(ticker, scope)`` pair. A
    multi-entity stage program instead requires one *common non-empty scope*
    across entities and exactly one source binding for each entity-specific
    operand in that scope. Treating all operands as one ticker was impossible
    for a genuine group question; selecting a scope per entity independently
    would be equally unsafe. Both ambiguity modes fail closed.
    """
    entities = {
        str(operand.get("entity") or "").casefold()
        for operand in operands
    }
    entity_scoped = bool(entities and "" not in entities)
    selected: dict[str, dict[str, Any]] = {}
    reason_codes: list[str] = []

    def equivalent_comparative_primary(
        operand: Mapping[str, Any], matches: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Choose a current-year witness only when every duplicate is identical.

        A comparative report can repeat an earlier period exactly. It is not a
        second value. This exception is deliberately available only to a
        controlled allowlisted formula and still requires an explicit source
        unit, a same-year primary report, same ticker/scope, same parsed/raw
        value, and report years limited to the source year or its following
        comparative report.
        """
        years = [value for value in operand.get("years") or [] if _int_or_none(value) is not None]
        if not allow_equivalent_comparative_witnesses or len(years) != 1 or len(matches) < 2:
            return None
        year = int(years[0])
        primary = [match for match in matches if _int_or_none(match.get("report_year")) == year]
        if len(primary) != 1:
            return None
        candidate = primary[0]
        ticker = str(candidate.get("ticker") or "")
        scope = str(candidate.get("scope") or "")
        raw_value = str((candidate.get("binding") or {}).get("raw_value") or "")
        parsed_value = str((candidate.get("binding") or {}).get("parsed_value") or "")
        source_unit = str(candidate.get("source_unit") or "")
        if not ticker or not scope or not raw_value or not parsed_value or not source_unit:
            return None
        if any(
            str(match.get("ticker") or "") != ticker
            or str(match.get("scope") or "") != scope
            or str((match.get("binding") or {}).get("raw_value") or "") != raw_value
            or str((match.get("binding") or {}).get("parsed_value") or "") != parsed_value
            or str(match.get("source_unit") or "") != source_unit
            or _int_or_none(match.get("report_year")) not in {year, year + 1}
            for match in matches
        ):
            return None
        witnesses = [
            {
                "internal_table_uid": str(match.get("internal_table_uid") or ""),
                "document_id": match.get("document_id"),
                "report_year": match.get("report_year"),
                "row_index": match.get("row_index"),
                "column_index": (match.get("binding") or {}).get("column_index"),
                "raw_value": (match.get("binding") or {}).get("raw_value"),
                "source_unit": match.get("source_unit"),
            }
            for match in matches
            if match is not candidate
        ]
        return {
            **candidate,
            "equivalent_comparative_witnesses": witnesses,
            "selection_policy": "same_year_primary_with_identical_comparative_witnesses_v1",
        }

    if entity_scoped:
        diagnostics = multi_entity_scope_diagnostics(coverage, operands)
        common_scopes = set(diagnostics.get("global_common_scopes") or [])
        if not common_scopes:
            return {}, ["no_common_scope_across_entity_operands"]
        if len(common_scopes) > 1:
            return {}, ["ambiguous_scope_across_entities"]
        scope = next(iter(common_scopes))
        for operand in operands:
            operand_id = str(operand["operand_id"])
            entity = str(operand.get("entity") or "").casefold()
            matches = [
                match
                for match in coverage.get(operand_id) or []
                if str(match.get("ticker") or "").casefold() == entity
                and str(match.get("scope") or "") == scope
            ]
            selected_match = matches[0] if len(matches) == 1 else equivalent_comparative_primary(operand, matches)
            if selected_match is None:
                reason_codes.append("ambiguous_operand_bindings")
                break
            selected[operand_id] = selected_match
        return (selected if not reason_codes else {}), reason_codes

    coherent_pairs: set[tuple[str, str]] | None = None
    for operand in operands:
        operand_id = str(operand["operand_id"])
        pairs = {
            (str(value.get("ticker") or ""), str(value.get("scope") or ""))
            for value in coverage.get(operand_id) or []
        }
        coherent_pairs = pairs if coherent_pairs is None else coherent_pairs & pairs
    if not coherent_pairs:
        return {}, ["no_common_entity_scope_for_required_operands"]
    if len(coherent_pairs) > 1:
        return {}, ["ambiguous_entity_or_scope_combination"]
    pair = next(iter(coherent_pairs))
    for operand in operands:
        operand_id = str(operand["operand_id"])
        pair_matches = [
            value
            for value in coverage.get(operand_id) or []
            if (str(value.get("ticker") or ""), str(value.get("scope") or ""))
            == pair
        ]
        if len(pair_matches) != 1:
            reason_codes.append("ambiguous_operand_bindings")
            break
        selected[operand_id] = pair_matches[0]
    return (selected if not reason_codes else {}), reason_codes


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
    } and str(formula.get("formula_id") or "") not in EXECUTION_BACKED_COMPOSED_FORMULAS:
        reason_codes.append("question_family_requires_composed_execution")
    selected_operand_matches: dict[str, dict[str, Any]] = {}
    scope_diagnostics = multi_entity_scope_diagnostics(coverage, operands)
    if not missing:
        selected_operand_matches, selection_reasons = select_coherent_operand_matches(
            coverage,
            operands,
            allow_equivalent_comparative_witnesses=(
                str(formula.get("formula_id") or "")
                in EQUIVALENT_COMPARATIVE_WITNESS_FORMULAS
            ),
        )
        reason_codes.extend(selection_reasons)
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
        "scope_diagnostics": scope_diagnostics,
        "source_discovery": dict(item.get("_formula_source_discovery") or {}),
        "execution_status": "not_executed_source_evidence_only",
    }
