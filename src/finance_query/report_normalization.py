"""Source-preserving report normalization and stage-aware retrieval routing.

The raw report, V2 table grid and V3 evidence context remain the only source
of financial evidence.  This module builds *navigation* metadata on top of
them:

``document metadata -> table type -> canonical variables -> metric route``.

It deliberately does not repair OCR, infer a missing header, emit a number,
or promote a review label.  A table with an unsafe V3 header is visible in the
catalog, but is excluded from routing until its source structure is reviewed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from .binding import row_label
from .financial_metrics import fold_text


REPORT_NORMALIZATION_VERSION = 2
REPORT_NORMALIZATION_PROTOCOL = "source_preserving_report_normalization_v2"
STAGE_ROUTING_VERSION = 1
STAGE_ROUTING_PROTOCOL = "metric_registry_staged_retrieval_v1"


STRUCTURAL_PREFIX_RE = re.compile(
    r"^\s*(?:(?:[IVXLCDM]+|\d+(?:\.\d+)*|[A-Za-z])\s*(?:[.)]|>)\s*)+",
    re.IGNORECASE,
)
NOTE_REFERENCE_RE = re.compile(
    r"\(?(?:thuyet\s+minh\s+)?[ivxlcdm]+\s*\.\s*\d+(?:\s*\([a-z]\))?\)?",
    re.IGNORECASE,
)
ACCOUNTING_CODE_SUFFIX_RE = re.compile(
    r"\s*\(+\s*\d{1,3}(?:\s*[=+\-]\s*\d{1,3})+\s*\)+\s*$"
)
CONTINUATION_MARKER_RE = re.compile(
    r"\s*\(\s*(?:mang|chuyen)\s+(?:(?:sang|tu)\s+)?trang\s+(?:truoc|sau)\s*\)\s*$"
)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
REPORTING_PERIOD_END_RE = re.compile(
    r"(?:nam\s+ket\s+thuc\s+ngay|tai\s+ngay)\s*"
    r"(?P<day>0?[1-9]|[12]\d|3[01])\s+thang\s*"
    r"(?P<month>0?[1-9]|1[0-2])\s+nam\s*(?P<year>(?:19|20)\d{2})",
    re.IGNORECASE,
)
TICKER_RE = re.compile(r"\b[A-Z]{2,6}\b")
NON_TICKER_TOKENS = {
    "CTCP", "VND", "USD", "BCTC", "BCTC", "DVT", "DV", "HNX", "HOSE",
    "NPM", "GPM", "ICR", "EBIT", "ROA", "ROE", "EPS", "CEO", "CFO",
}


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    required_variables: tuple[str, ...]
    allowed_table_types: tuple[str, ...]
    expression: str


# Alias matching is intentionally exact after only removing source structural
# prefixes and note references.  It must not collapse similar-but-different
# financial variables (for example current assets and other current assets).
VARIABLE_ALIASES: dict[str, tuple[str, ...]] = {
    "current_assets": (
        "tai san ngan han",
        "tong tai san ngan han",
        "tong cong tai san ngan han",
    ),
    "inventory": ("hang ton kho",),
    "current_liabilities": (
        "no ngan han",
        "tong no ngan han",
        "no phai tra ngan han",
        "tong no phai tra ngan han",
    ),
    "net_revenue": (
        "doanh thu thuan",
        "doanh thu thuan ve ban hang va cung cap dich vu",
    ),
    "net_income": (
        "loi nhuan sau thue",
        "loi nhuan sau thue thu nhap doanh nghiep",
        "loi nhuan sau thue tndn",
        "loi nhuan thuan sau thue tndn",
    ),
    "gross_profit": ("loi nhuan gop",),
    "profit_before_tax": (
        "loi nhuan ke toan truoc thue",
        "loi nhuan truoc thue",
        "lo ke toan truoc thue",
    ),
    "interest_expense": (
        "chi phi lai vay",
        "chi phi lai tien vay",
        "trong do: chi phi lai vay",
    ),
    "operating_cash_flow": (
        "luu chuyen tien thuan tu hoat dong kinh doanh",
        "luu chuyen tien thuan tu hoat dong sxkd",
    ),
}
# Some primary-statement rows have an intentionally repeated text label. The
# source account code resolves only these well-known total rows: `140 Hàng tồn
# kho` is the inventory total, while `141 Hàng tồn kho` is a detail line and
# must not be promoted as the metric operand.
VARIABLE_TOTAL_ACCOUNT_CODES: dict[str, frozenset[str]] = {
    "current_assets": frozenset({"100"}),
    "inventory": frozenset({"140"}),
    "current_liabilities": frozenset({"310"}),
}
SOURCE_ACCOUNT_CODE_RE = re.compile(r"^\s*(\d{1,3})\s*$")

METRIC_REGISTRY: dict[str, MetricDefinition] = {
    "quick_ratio": MetricDefinition(
        "quick_ratio",
        ("current_assets", "inventory", "current_liabilities"),
        ("balance_sheet",),
        "(current_assets - inventory) / current_liabilities",
    ),
    "net_profit_margin": MetricDefinition(
        "net_profit_margin",
        ("net_income", "net_revenue"),
        ("income_statement",),
        "net_income / net_revenue * 100%",
    ),
    "gross_profit_margin": MetricDefinition(
        "gross_profit_margin",
        ("gross_profit", "net_revenue"),
        ("income_statement",),
        "gross_profit / net_revenue * 100%",
    ),
    "interest_coverage": MetricDefinition(
        "interest_coverage",
        ("profit_before_tax", "interest_expense"),
        ("income_statement", "notes"),
        "(profit_before_tax + abs(interest_expense)) / abs(interest_expense)",
    ),
    "operating_cash_flow": MetricDefinition(
        "operating_cash_flow",
        ("operating_cash_flow",),
        ("cash_flow_statement",),
        "operating_cash_flow",
    ),
}


def source_row_metric_label(row: Iterable[Any]) -> str:
    """Return a source label suitable for exact alias lookup, never an OCR repair."""
    # ``binding.row_label`` intentionally preserves the table's cell layout.
    # A blank text cell between a metric and its first numeric cell can thus
    # render as a trailing `` > `` in that display string.  Remove only empty
    # display segments here; all non-empty source wording and raw grid cells
    # stay untouched.
    raw = " > ".join(
        part.strip()
        for part in row_label([str(value) for value in row]).split(">")
        if part.strip()
    )
    normalized = fold_text(raw)
    # OCR/PDF extraction may keep TeX escape characters around a purely
    # structural accounting equation (for example ``(\\(100 = ...\\))``).
    # Removing the escape marker does not replace or infer source wording;
    # it only makes the following exact numeric-equation rule see its literal
    # parentheses.
    normalized = normalized.replace("\\", "")
    normalized = STRUCTURAL_PREFIX_RE.sub("", normalized)
    normalized = NOTE_REFERENCE_RE.sub("", normalized)
    # Primary statements can split one official line across pages and retain
    # a literal marker such as ``(mang sang trang sau)``.  It only describes
    # pagination, so remove that exact terminal marker before matching the
    # adjacent accounting equation.  No financial wording is rewritten.
    normalized = CONTINUATION_MARKER_RE.sub("", normalized)
    # Balance-sheet labels often append an official accounting line equation
    # such as ``(100 = 110 + 120 + 130)``. It is a source structural code,
    # not a qualifier of the metric. Remove only a fully numeric equation;
    # ordinary parenthetical words remain part of the source label.
    normalized = ACCOUNTING_CODE_SUFFIX_RE.sub("", normalized)
    return " ".join(normalized.split()).strip(" -:;,.()")


def normalize_financial_variable(row: Iterable[Any]) -> dict[str, str] | None:
    """Map one exact source row label to one canonical variable, if unambiguous."""
    normalized = source_row_metric_label(row)
    if not normalized:
        return None
    matches = [
        variable_id
        for variable_id, aliases in VARIABLE_ALIASES.items()
        if normalized in aliases
    ]
    if len(matches) != 1:
        return None
    variable_id = matches[0]
    total_codes = VARIABLE_TOTAL_ACCOUNT_CODES.get(variable_id)
    if total_codes is not None:
        source_codes = {
            match.group(1)
            for value in row
            if (match := SOURCE_ACCOUNT_CODE_RE.fullmatch(str(value))) is not None
        }
        if not source_codes.intersection(total_codes):
            return None
    return {
        "variable_id": variable_id,
        "raw_row_label": " > ".join(
            part.strip()
            for part in row_label([str(value) for value in row]).split(">")
            if part.strip()
        ),
        "normalized_row_label": normalized,
        "match_policy": "exact_source_row_alias_and_total_account_code_v2",
    }


def infer_report_type(context_before: object, table_type: str) -> tuple[str, str]:
    """Classify document kind from literal source wording or structural type."""
    context = fold_text(str(context_before or ""))
    if "bao cao thuong nien" in context:
        return "annual_report", "source_context:bao_cao_thuong_nien"
    if "bao cao quan tri" in context:
        return "management_report", "source_context:bao_cao_quan_tri"
    if "bao cao tai chinh" in context:
        return "financial_statements", "source_context:bao_cao_tai_chinh"
    if table_type in {
        "balance_sheet", "income_statement", "cash_flow_statement", "equity_change_statement",
    }:
        return "financial_statements", "source_table_type:primary_statement"
    return "unknown", "no_literal_or_structural_report_type"


def source_reporting_period_end(
    tables: Iterable[Mapping[str, Any]],
    *,
    report_year: int | None,
) -> tuple[dict[str, int] | None, str]:
    """Return one literal fiscal year-end date when document context agrees.

    A company can use a non-calendar fiscal year (for example 30 September).
    This is navigation metadata derived only from phrases such as ``cho năm
    kết thúc ngày 30 tháng 9 năm 2022``. Ambiguous or absent dates remain
    unknown; they are never defaulted to 31 December.
    """
    if not isinstance(report_year, int):
        return None, "missing_document_report_year"
    endings: Counter[tuple[int, int, int]] = Counter()
    for table in tables:
        context = fold_text(str(table.get("context_before") or ""))
        for match in REPORTING_PERIOD_END_RE.finditer(context):
            value = (int(match.group("day")), int(match.group("month")), int(match.group("year")))
            if value[2] == report_year:
                endings[value] += 1
    if len(endings) != 1:
        return None, "ambiguous_or_absent_source_reporting_period_end"
    day, month, year = next(iter(endings))
    return {"day": day, "month": month, "year": year}, "source_context_reporting_period_end"


def canonical_table_type(function_kind: object) -> tuple[str, str]:
    """Map existing V2/V3 function labels to the routing taxonomy."""
    kind = str(function_kind or "unknown")
    if kind in {
        "balance_sheet", "income_statement", "cash_flow_statement", "equity_change_statement",
    }:
        return kind, "source_table_function"
    if kind in {"financial_note", "financial_note_detail"}:
        return "notes", "source_table_function"
    if kind in {
        "related_party_schedule", "debt_schedule", "investment_schedule", "segment_reporting", "project_schedule",
    }:
        return "schedule", "source_table_function"
    return "other", "source_table_function"


def document_metadata_rows(tables: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Make one source-derived metadata record per document without guessing scope."""
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for table in tables:
        document_id = str(table.get("document_id") or "")
        if document_id:
            grouped[document_id].append(table)
    rows: list[dict[str, Any]] = []
    for document_id, members in sorted(grouped.items()):
        tickers = {str(item.get("ticker") or "") for item in members if str(item.get("ticker") or "")}
        years = {item.get("report_year") for item in members if isinstance(item.get("report_year"), int)}
        scopes = {str(item.get("scope") or "unknown") for item in members}
        function_kinds = {
            str((item.get("table_function") or {}).get("kind") or "unknown")
            for item in members
        }
        table_type = next(
            (
                candidate
                for candidate in ("balance_sheet", "income_statement", "cash_flow_statement", "equity_change_statement")
                if candidate in function_kinds
            ),
            "other",
        )
        report_type, report_type_basis = infer_report_type(
            " ".join(str(item.get("context_before") or "") for item in members[:4]),
            table_type,
        )
        report_year = next(iter(years)) if len(years) == 1 else None
        reporting_period_end, reporting_period_end_basis = source_reporting_period_end(
            members,
            report_year=report_year,
        )
        rows.append(
            {
                "document_id": document_id,
                "company": next(iter(tickers)) if len(tickers) == 1 else "",
                "report_year": report_year,
                "report_scope": next(iter(scopes)) if len(scopes) == 1 else "unknown",
                "report_type": report_type,
                "reporting_period_end": reporting_period_end,
                "metadata_status": "source_derived" if len(tickers) == len(years) == len(scopes) == 1 else "ambiguous",
                "derivation_basis": [
                    "bundle_table_metadata",
                    report_type_basis,
                    reporting_period_end_basis,
                ],
                "source_contract": {
                    "metadata_only": True,
                    "evidence_eligible": False,
                    "training_eligible": False,
                },
            }
        )
    return rows


def build_table_normalization_entry(
    table: Mapping[str, Any],
    context: Mapping[str, Any],
    document_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a routing record while retaining all source-level uncertainty."""
    uid = str(table.get("internal_table_uid") or "")
    if not uid or uid != str(context.get("internal_table_uid") or ""):
        raise ValueError("Table normalization requires matching non-empty table/context UIDs")
    table_type, type_basis = canonical_table_type(
        (context.get("table_function") or table.get("table_function") or {}).get("kind")
    )
    quality = dict(context.get("quality") or {})
    canonical_headers = dict(context.get("canonical_headers") or {})
    available_period_years = sorted(
        {
            int(year)
            for column in canonical_headers.get("columns") or []
            for year in YEAR_RE.findall(str(column.get("source_label") or ""))
        }
    )
    profiles = {
        int(profile.get("row_index"))
        for profile in context.get("row_profiles") or []
        if str(profile.get("role") or "") == "data" and isinstance(profile.get("row_index"), int)
    }
    variables: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for row_index, row in enumerate(table.get("rows") or []):
        if row_index not in profiles:
            continue
        match = normalize_financial_variable(row)
        if match is None:
            continue
        key = (match["variable_id"], row_index)
        if key in seen:
            continue
        seen.add(key)
        variables.append({"row_index": row_index, **match})
    header_status = str(quality.get("status") or "needs_processing")
    function = dict(context.get("table_function") or table.get("table_function") or {})
    structural = str(function.get("specificity") or "") == "structural"
    # A routing candidate must first have a canonical V3 header and an
    # observed structural table type.  This only narrows navigation; it never
    # certifies a selected value cell.
    routing_eligible = header_status == "review_ready" and structural and table_type != "other"
    return {
        "internal_table_uid": uid,
        "schema_version": REPORT_NORMALIZATION_VERSION,
        "protocol": REPORT_NORMALIZATION_PROTOCOL,
        "document_id": str(table.get("document_id") or ""),
        "company": document_metadata.get("company") or str(table.get("ticker") or ""),
        "report_year": document_metadata.get("report_year", table.get("report_year")),
        "report_scope": document_metadata.get("report_scope") or str(table.get("scope") or "unknown"),
        "report_type": document_metadata.get("report_type") or "unknown",
        "reporting_period_end": document_metadata.get("reporting_period_end"),
        # Explicit header years are preferred when routing. The document year
        # remains navigation metadata for relative headers such as "năm nay";
        # final evidence binding still verifies the exact V3/V2 header.
        "available_period_years": available_period_years,
        "table_type": table_type,
        "table_type_basis": type_basis,
        "table_type_status": "source_structural" if structural else "metadata_provisional",
        "header_integrity": {
            "status": header_status,
            "reason_codes": list(quality.get("reason_codes") or []),
            "raw_header_row_indices": list(canonical_headers.get("raw_header_row_indices") or []),
            "canonical_header_row_indices": list(canonical_headers.get("header_row_indices") or []),
            "excluded_header_row_indices": list(canonical_headers.get("excluded_header_row_indices") or []),
        },
        "canonical_variables": variables,
        "routing_eligible": routing_eligible,
        "source_contract": {
            "metadata_only": True,
            "evidence_eligible": False,
            "training_eligible": False,
            "may_repair_ocr": False,
            "may_select_value_cell": False,
        },
    }


def catalog_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    records = list(rows)
    return {
        "table_type_counts": dict(sorted(Counter(str(row.get("table_type") or "unknown") for row in records).items())),
        "header_integrity_counts": dict(sorted(Counter(str((row.get("header_integrity") or {}).get("status") or "unknown") for row in records).items())),
        "routing_eligibility_counts": dict(sorted(Counter("eligible" if bool(row.get("routing_eligible")) else "blocked" for row in records).items())),
        "variable_counts": dict(sorted(Counter(str(variable.get("variable_id") or "unknown") for row in records for variable in row.get("canonical_variables") or []).items())),
    }


def metric_definition(metric_id: str) -> MetricDefinition:
    try:
        return METRIC_REGISTRY[metric_id]
    except KeyError as error:
        raise ValueError(f"Unknown metric registry entry: {metric_id}") from error


def _question_tickers(question: str) -> list[str]:
    return [
        token
        for token in TICKER_RE.findall(question)
        if token not in NON_TICKER_TOKENS
    ]


def _question_years(question: str) -> list[int]:
    return list(dict.fromkeys(int(value) for value in YEAR_RE.findall(question)))


def _has_any(text: str, values: Iterable[str]) -> bool:
    return any(value in text for value in values)


def _route_stage(
    stage_id: str,
    metric_id: str,
    *,
    entities: list[str] | None,
    year: int,
    entity_source: str | None = None,
    aggregate: str | None = None,
) -> dict[str, Any]:
    metric = metric_definition(metric_id)
    return {
        "stage_id": stage_id,
        "metric_id": metric.metric_id,
        "required_variables": list(metric.required_variables),
        "table_types": list(metric.allowed_table_types),
        "entities": list(entities or []),
        "year": year,
        "entity_source": entity_source,
        "calculation": metric.expression,
        "aggregate": aggregate,
        "candidate_policy": "each_required_variable_must_have_an_eligible_source_table",
    }


def _deterministic_transition_stage(
    stage_id: str,
    *,
    entities_source: str,
    old_stage_id: str,
    new_stage_id: str,
) -> dict[str, Any]:
    """Describe a source-free transition between two reviewed metric stages."""
    return {
        "stage_id": stage_id,
        "metric_id": "gross_profit_margin_change",
        "required_variables": [],
        "table_types": [],
        "entities": [],
        "entity_source": entities_source,
        "year": None,
        "calculation": "gross_profit_margin_new - gross_profit_margin_old",
        "aggregate": "argmax_unique_signed_change",
        "candidate_policy": "no_retrieval; deterministic transition over prior source-reviewed stages",
        "requires_llm_review": False,
        "state_inputs": {"old": old_stage_id, "new": new_stage_id},
    }


def build_staged_route_plan(question: str, *, scope: str | None = None) -> dict[str, Any]:
    """Compile only recognised multi-stage wording to retrieval constraints.

    The plan is intentionally a routing plan, not an answer program: later
    stages name state produced by the executor but do not guess that state.
    Unknown wording is returned as ``abstain`` rather than being decomposed by
    semantic similarity.
    """
    folded = fold_text(question)
    entities = _question_tickers(question)
    years = _question_years(question)
    quick = _has_any(folded, ("he so thanh toan nhanh", "quick ratio"))
    median = "trung vi" in folded
    net_margin = _has_any(
        folded,
        ("net profit margin", "bien loi nhuan rong", "ty le loi nhuan sau thue tren doanh thu thuan"),
    )
    gross_margin = _has_any(folded, ("bien loi nhuan gop", "gross profit margin"))
    interest_coverage = _has_any(folded, ("kha nang thanh toan lai vay", "interest coverage"))
    if len(entities) < 2 or not years:
        return {
            "schema_version": STAGE_ROUTING_VERSION,
            "protocol": STAGE_ROUTING_PROTOCOL,
            "routing_status": "abstain",
            "reason_codes": ["missing_explicit_entities_or_year"],
            "stages": [],
            "submission_eligible": False,
        }
    base_year, target_year = years[0], years[-1]
    if quick and median and net_margin:
        return {
            "schema_version": STAGE_ROUTING_VERSION,
            "protocol": STAGE_ROUTING_PROTOCOL,
            "routing_status": "planned",
            "family": "quick_ratio_median_then_net_profit_margin",
            "entities": entities,
            "scope": scope,
            "stages": [
                _route_stage("quick_ratio_screen", "quick_ratio", entities=entities, year=base_year),
                _route_stage(
                    "net_profit_margin_after_screen",
                    "net_profit_margin",
                    entities=None,
                    entity_source="@eligible_entities",
                    year=target_year,
                    aggregate="average",
                ),
            ],
            "submission_eligible": False,
        }
    if quick and median and gross_margin and interest_coverage and len(years) >= 2:
        return {
            "schema_version": STAGE_ROUTING_VERSION,
            "protocol": STAGE_ROUTING_PROTOCOL,
            "routing_status": "planned",
            "family": "quick_ratio_gpm_interest_coverage_selection",
            "entities": entities,
            "scope": scope,
            "stages": [
                _route_stage("quick_ratio_screen", "quick_ratio", entities=entities, year=base_year),
                _route_stage(
                    "gross_profit_margin_old",
                    "gross_profit_margin",
                    entities=None,
                    entity_source="@eligible_entities",
                    year=base_year,
                ),
                _route_stage(
                    "gross_profit_margin_new",
                    "gross_profit_margin",
                    entities=None,
                    entity_source="@eligible_entities",
                    year=target_year,
                ),
                _deterministic_transition_stage(
                    "gross_profit_margin_change_rank",
                    entities_source="@eligible_entities",
                    old_stage_id="gross_profit_margin_old",
                    new_stage_id="gross_profit_margin_new",
                ),
                _route_stage(
                    "interest_coverage_lookup",
                    "interest_coverage",
                    entities=None,
                    entity_source="@winning_entity",
                    year=target_year,
                    aggregate="selected",
                ),
            ],
            "submission_eligible": False,
        }
    return {
        "schema_version": STAGE_ROUTING_VERSION,
        "protocol": STAGE_ROUTING_PROTOCOL,
        "routing_status": "abstain",
        "reason_codes": ["unrecognised_or_incomplete_multistage_metric_contract"],
        "stages": [],
        "submission_eligible": False,
    }


def route_stage_candidates(
    catalog_rows: Iterable[Mapping[str, Any]],
    stage: Mapping[str, Any],
    *,
    resolved_entities: Iterable[str] | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Filter a stage to eligible catalog tables and explain empty results."""
    rows = list(catalog_rows)
    if (
        resolved_entities is None
        and not list(stage.get("entities") or [])
        and stage.get("entity_source")
    ):
        return {
            "status": "awaiting_prior_stage",
            "candidate_table_uids": [],
            "feedback": {
                "reason_code": "prior_stage_entity_selection_not_available",
                "stage_id": str(stage.get("stage_id") or ""),
                "entity_source": stage.get("entity_source"),
            },
        }
    requested_entities = list(resolved_entities or stage.get("entities") or [])
    requested_year = stage.get("year")
    required_variables = {str(value) for value in stage.get("required_variables") or []}
    table_types = {str(value) for value in stage.get("table_types") or []}
    eligible = [row for row in rows if bool(row.get("routing_eligible"))]
    entity_rows = [row for row in eligible if not requested_entities or str(row.get("company") or "") in requested_entities]
    year_rows = [
        row
        for row in entity_rows
        if requested_year is None
        or requested_year in {int(value) for value in row.get("available_period_years") or []}
        or row.get("report_year") == requested_year
    ]
    scope_rows = [row for row in year_rows if not scope or str(row.get("report_scope") or "") == scope]
    type_rows = [row for row in scope_rows if str(row.get("table_type") or "") in table_types]
    entities_to_check = requested_entities or sorted(
        {str(row.get("company") or "") for row in type_rows if str(row.get("company") or "")}
    )
    bindings_by_entity: dict[str, dict[str, list[str]]] = {}
    missing_by_entity: dict[str, list[str]] = {}
    for entity in entities_to_check:
        per_entity_rows = [row for row in type_rows if str(row.get("company") or "") == entity]
        variable_bindings: dict[str, list[str]] = {}
        for variable_id in sorted(required_variables):
            uids = [
                str(row["internal_table_uid"])
                for row in per_entity_rows
                if variable_id
                in {
                    str(value.get("variable_id") or "")
                    for value in row.get("canonical_variables") or []
                }
            ]
            if uids:
                variable_bindings[variable_id] = sorted(set(uids))
        missing = sorted(required_variables - set(variable_bindings))
        if missing:
            missing_by_entity[entity] = missing
        else:
            bindings_by_entity[entity] = variable_bindings
    if bindings_by_entity and not missing_by_entity:
        candidates = sorted(
            {
                uid
                for entity_bindings in bindings_by_entity.values()
                for uids in entity_bindings.values()
                for uid in uids
            }
        )
        return {
            "status": "candidate_tables_found",
            "candidate_table_uids": candidates,
            "candidate_table_uids_by_entity_variable": bindings_by_entity,
            "feedback": None,
        }
    if not eligible:
        reason = "all_tables_blocked_by_header_or_table_type_quality"
    elif not entity_rows:
        reason = "no_eligible_table_for_requested_entities"
    elif not year_rows:
        reason = "no_eligible_table_for_requested_year"
    elif not scope_rows:
        reason = "no_eligible_table_for_requested_scope"
    elif not type_rows:
        reason = "no_eligible_table_for_required_table_type"
    else:
        reason = "required_variables_missing_from_eligible_source_tables"
    return {
        "status": "no_candidate",
        "candidate_table_uids": [],
        "feedback": {
            "reason_code": reason,
            "stage_id": str(stage.get("stage_id") or ""),
            "required_variables": sorted(required_variables),
            "requested_entities": requested_entities,
            "requested_year": requested_year,
            "missing_variables_by_entity": missing_by_entity,
            "next_action": "retrieve_or_materialize additional source tables; never infer a missing operand",
        },
    }
