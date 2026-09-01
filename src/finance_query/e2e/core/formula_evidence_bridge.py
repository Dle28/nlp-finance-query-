"""Source-replayed candidates for a small, fully-bound formula evidence lane.

The formula evidence JSONL is a research sidecar.  This adapter deliberately
does not trust its stored result (or its parsed numeric value).  It admits a
row only when the current structured table asset proves every selected
coordinate, the current question still matches the sidecar question, and a
known Decimal AST can be replayed from the hydrated cells.

The returned records are prediction candidates.  They are not certificates,
human approvals, training labels, or release authority.  Ambiguous formulas,
missing selected bindings, mixed scope, malformed multi-number cells, and
unsupported formula families fail closed.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from itertools import product
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from finance_query.e2e.decimal_executor import execute_ast, validate_operation_ast

from .currency_units import is_fixed_vnd_scale, vnd_scale


FORMULA_EVIDENCE_BRIDGE_PROTOCOL = "formula_evidence_replay_v1"
SUPPORTED_FORMULA_IDS = frozenset(
    {
        "current_liabilities_to_equity",
        "explicit_stated_fraction",
        "loan_to_deposit",
        "long_term_investment_to_equity",
        "net_other_income",
        "percentage_change",
        "net_finance_result",
        "net_service_result",
        "operating_cash_flow_argmax_period",
        "ppe_cost_to_assets",
        "ppe_to_assets",
        "product_revenue_share",
        "trade_payables_share_current_liabilities",
    }
)

_RATIO_FORMULA_IDS = frozenset(
    {
        "current_liabilities_to_equity",
        "explicit_stated_fraction",
        "loan_to_deposit",
        "long_term_investment_to_equity",
        "ppe_cost_to_assets",
        "ppe_to_assets",
        "product_revenue_share",
        "trade_payables_share_current_liabilities",
    }
)
_NET_FORMULA_IDS = frozenset(
    {"net_finance_result", "net_other_income", "net_service_result"}
)
_RESELECTABLE_FORMULA_IDS = _RATIO_FORMULA_IDS | _NET_FORMULA_IDS

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_NUMERIC_TOKEN_RE = re.compile(
    r"(?:\(\s*[+-]?\d[\d.,]*\s*\)|[+-]?\d[\d.,]*)"
)
_WHITESPACE_RE = re.compile(r"\s+")
_TICKER_RE = re.compile(
    r"^([A-Za-z0-9]{2,6})_financial_statements(?:_|$)",
    re.IGNORECASE,
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFD", _text(value).lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return _WHITESPACE_RE.sub(" ", text.replace("đ", "d")).strip()


def _document_year(document_id: object) -> int | None:
    years = _YEAR_RE.findall(_text(document_id))
    return int(years[-1]) if years else None


def _table_ticker(table: Mapping[str, Any]) -> str:
    explicit = _text(table.get("ticker")).upper()
    if explicit:
        return explicit
    match = _TICKER_RE.match(_text(table.get("document_id")))
    return match.group(1).upper() if match else ""


def _table_scope(table: Mapping[str, Any]) -> str:
    explicit = _text(table.get("scope")).casefold()
    if explicit:
        return explicit
    document_id = _text(table.get("document_id")).casefold()
    match = re.search(r"_(separate|consolidated|aggregated)(?:_|$)", document_id)
    return match.group(1) if match else ""


def _table_year(table: Mapping[str, Any]) -> int | None:
    raw_year = table.get("report_year")
    if isinstance(raw_year, int) and not isinstance(raw_year, bool):
        return raw_year
    return _document_year(table.get("document_id"))


def _table_source_hash(table: Mapping[str, Any]) -> str | None:
    provenance = table.get("source_provenance")
    if not isinstance(provenance, Mapping):
        provenance = table
    for key in ("source_sha256", "table_sha256"):
        value = _text(provenance.get(key))
        if value:
            return value
    return None


def _strict_decimal(token: object) -> Decimal | None:
    raw = _text(token).replace("\u00a0", "").replace(" ", "")
    if not raw or raw in {"-", "+"}:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1]
    raw = raw.rstrip("%")
    if raw.startswith("+"):
        raw = raw[1:]
    elif raw.startswith("-"):
        negative = True
        raw = raw[1:]
    if not raw:
        return None
    if "." in raw and "," in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    elif raw.count(",") > 1:
        raw = raw.replace(",", "")
    elif "." in raw:
        before, after = raw.split(".", 1)
        raw = before + after if len(after) == 3 else before + "." + after
    elif "," in raw:
        before, after = raw.split(",", 1)
        raw = before + after if len(after) == 3 else before + "." + after
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return -value if negative else value


def _numeric_tokens(raw: object) -> list[str]:
    text = _text(raw)
    if not text:
        return []
    return [match.group(0).strip() for match in _NUMERIC_TOKEN_RE.finditer(text)]


def _same_raw(left: object, right: object) -> bool:
    return _WHITESPACE_RE.sub(" ", _text(left)).strip() == _WHITESPACE_RE.sub(
        " ", _text(right)
    ).strip()


def _row_label(row: list[Any], binding: Mapping[str, Any]) -> str:
    source_row = binding.get("source_row")
    if isinstance(source_row, list):
        values = [_text(value) for value in source_row[:3] if _text(value)]
        textual = [value for value in values if re.search(r"[A-Za-zÀ-ỹĐđ]", value)]
        if textual:
            return max(textual, key=len)
        if values:
            return max(values, key=len)
    values = [_text(value) for value in row[:3] if _text(value)]
    textual = [value for value in values if re.search(r"[A-Za-zÀ-ỹĐđ]", value)]
    if textual:
        return max(textual, key=len)
    if values:
        return max(values, key=len)
    return ""


def _metric_label_core(value: object) -> str:
    """Return a conservative row-label form for exact metric comparison.

    Financial statements commonly prefix a row with an account code or a
    section marker (``310.``, ``I.``) and sometimes append the code relation
    ``(400 = 410)``.  Those decorations do not change the metric identity;
    all other suffixes remain significant and therefore do not match.
    """

    label = _normalized(value)
    while True:
        stripped = re.sub(
            r"^(?:(?:\d{1,3}|[ivxlcdm]+)\s*[.)])\s*",
            "",
            label,
            flags=re.IGNORECASE,
        )
        if stripped == label:
            break
        label = stripped
    label = re.sub(r"\s*\([^)]*\)\s*$", "", label)
    return label.strip(" -:;,.()")


def _question_scope(question: str) -> str | None:
    """Extract only an explicit reporting scope from the question."""

    normalized = _normalized(question)
    separate = "cong ty me" in normalized
    consolidated = "hop nhat" in normalized or "consolidated" in normalized
    if separate == consolidated:
        return None
    return "separate" if separate else "consolidated"


def _candidate_matches_metric(
    match: Mapping[str, Any], operand: Mapping[str, Any]
) -> bool:
    source_row = match.get("source_row")
    if not isinstance(source_row, list):
        return False
    label = _metric_label_core(_row_label(source_row, match.get("binding") or {}))
    if not label:
        return False
    hints = {
        _metric_label_core(value)
        for value in operand.get("metric_hints") or []
        if _metric_label_core(value)
    }
    return bool(hints) and label in hints


def _requested_divisor(question: str) -> Decimal:
    q = _normalized(question)
    if "nghin ty dong" in q or "ngan ty dong" in q:
        return Decimal("1000000000000")
    if "tram ty dong" in q:
        return Decimal("100000000000")
    if "ty dong" in q or "ti dong" in q:
        return Decimal("1000000000")
    if "trieu dong" in q or "trieu vnd" in q:
        return Decimal("1000000")
    if "nghin dong" in q or "ngan dong" in q:
        return Decimal("1000")
    return Decimal(1)


def _formula_ast(formula_id: str, operand_ids: list[str]) -> dict[str, Any] | None:
    if formula_id == "percentage_change" and operand_ids == ["x_old", "x_new"]:
        return {"op": "percentage_change", "args": ["x_new", "x_old"]}
    if formula_id in _NET_FORMULA_IDS and len(operand_ids) == 2:
        if formula_id == "net_finance_result" and operand_ids != [
            "finance_income",
            "finance_expense",
        ]:
            return None
        if formula_id == "net_other_income" and operand_ids != [
            "other_income",
            "other_expense",
        ]:
            return None
        if formula_id == "net_service_result" and operand_ids != [
            "service_income",
            "service_expense",
        ]:
            return None
        return {"op": "subtract", "args": list(operand_ids)}
    if formula_id in _RATIO_FORMULA_IDS and operand_ids == [
        "numerator",
        "denominator",
    ]:
        return {
            "op": "ratio_to_percent",
            "args": [{"op": "divide", "args": ["numerator", "denominator"]}],
        }
    if formula_id == "operating_cash_flow_argmax_period" and len(operand_ids) >= 2:
        return {
            "op": "arg_extreme_period",
            "direction": "max",
            "args": list(operand_ids),
        }
    return None


def _component_value(
    *,
    formula_id: str,
    operand_id: str,
    row_label: str,
    raw_cell: object,
) -> tuple[Decimal | None, str | None]:
    tokens = _numeric_tokens(raw_cell)
    if len(tokens) == 1:
        value = _strict_decimal(tokens[0])
        return value, None if value is not None else "INVALID_NUMERIC_CELL"

    # Some Vietnamese income statements put the main expense and the nested
    # interest expense in the same cell.  The controlled net-finance formula
    # asks for the main ``Chi phí tài chính`` value, which is the first exact
    # component.  This exception is constrained by formula family, operand
    # role, row label, and the observed two-number cell shape.
    if (
        formula_id == "net_finance_result"
        and operand_id == "finance_expense"
        and len(tokens) == 2
        and "chi phi tai chinh" in _normalized(row_label)
        and "trong do" in _normalized(row_label)
    ):
        value = _strict_decimal(tokens[0])
        return value, None if value is not None else "INVALID_NUMERIC_COMPONENT"
    return None, "MULTIPLE_NUMERIC_COMPONENTS"


def _selected_match(
    *,
    record: Mapping[str, Any],
    operand: Mapping[str, Any],
    selected: Mapping[str, Any],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, Decimal | None, str | None]:
    operand_id = _text(operand.get("operand_id"))
    uid = _text(selected.get("internal_table_uid"))
    table = tables_by_uid.get(uid)
    if not isinstance(table, Mapping):
        return None, None, "TABLE_NOT_HYDRATED"
    document_id = _text(selected.get("document_id"))
    if document_id and _text(table.get("document_id")) != document_id:
        return None, None, "DOCUMENT_MISMATCH"
    binding = selected.get("binding")
    if not isinstance(binding, Mapping) or binding.get("status") != "cell_bound":
        return None, None, "BINDING_NOT_CELL_BOUND"
    row_index = binding.get("row_index")
    column_index = binding.get("column_index")
    if (
        isinstance(row_index, bool)
        or not isinstance(row_index, int)
        or isinstance(column_index, bool)
        or not isinstance(column_index, int)
        or row_index < 0
        or column_index < 0
    ):
        return None, None, "INVALID_COORDINATE"
    if selected.get("row_index") is not None and selected.get("row_index") != row_index:
        return None, None, "ROW_COORDINATE_MISMATCH"
    rows = table.get("rows")
    if (
        not isinstance(rows, list)
        or row_index >= len(rows)
        or not isinstance(rows[row_index], list)
        or column_index >= len(rows[row_index])
    ):
        return None, None, "CELL_NOT_FOUND"
    row = rows[row_index]
    raw_cell = row[column_index]
    claimed_raw = binding.get("raw_value")
    if claimed_raw is None or not _same_raw(raw_cell, claimed_raw):
        return None, None, "RAW_CELL_MISMATCH"
    source_cell = binding.get("source_cell")
    if isinstance(source_cell, Mapping):
        if source_cell.get("source_row") != row_index or source_cell.get("source_cell") != column_index:
            return None, None, "SOURCE_CELL_PROVENANCE_MISMATCH"
    ticker = _table_ticker(table)
    expected_ticker = _text(operand.get("entity")).upper()
    if expected_ticker and ticker != expected_ticker:
        return None, None, "TICKER_MISMATCH"
    table_year = _table_year(table)
    years = [int(value) for value in operand.get("years") or [] if str(value).isdigit()]
    if years and table_year not in years:
        return None, None, "REPORT_YEAR_MISMATCH"
    scope = _table_scope(table)
    declared_scope = _text(selected.get("scope")).casefold()
    if declared_scope and scope != declared_scope:
        return None, None, "SCOPE_MISMATCH"
    source_unit = _text(selected.get("source_unit")).casefold()
    if not source_unit or not is_fixed_vnd_scale(source_unit):
        return None, None, "SOURCE_UNIT_UNSUPPORTED"
    row_label = _row_label(row, binding)
    value, value_error = _component_value(
        formula_id=_text((record.get("formula") or {}).get("formula_id")),
        operand_id=operand_id,
        row_label=row_label,
        raw_cell=raw_cell,
    )
    if value is None:
        return None, None, value_error or "NUMERIC_CELL_UNRESOLVED"
    # The sidecar's parsed value is a diagnostic hint only.  In particular,
    # concatenated nested values in one OCR cell must never override the
    # independently parsed current cell.
    return (
        {
            "role": operand_id,
            "formula_operand_id": operand_id,
            "ticker": ticker,
            "scope": scope,
            "report_year": table_year,
            "source_unit": source_unit,
            "requested_output_unit": None,
            "requested_output_divisor": None,
            "internal_table_uid": uid,
            "document_id": _text(table.get("document_id")),
            "row_index": row_index,
            "column_index": column_index,
            "row_label": row_label,
            # ``raw_value`` is the exact source cell for the proposal
            # verifier.  ``value`` is the independently parsed component used
            # by the formula replay and CSV operand column.
            "raw_value": _text(raw_cell),
            "raw_value_decimal": _text(raw_cell),
            "value": value,
            "source_to_vnd_multiplier": format(vnd_scale(source_unit), "f"),
            "source_cell_sha256": _table_source_hash(table),
            "candidate_source": FORMULA_EVIDENCE_BRIDGE_PROTOCOL,
        },
        value,
        None,
    )


def _candidate_rank(value: Mapping[str, Any]) -> int:
    raw = value.get("candidate_rank")
    if isinstance(raw, bool):
        return 1_000_000
    try:
        rank = int(raw)
    except (TypeError, ValueError):
        return 1_000_000
    return rank if rank >= 0 else 1_000_000


def _reselect_operand_matches(
    record: Mapping[str, Any],
    *,
    operands: list[Mapping[str, Any]],
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]] | None, dict[str, Any], str | None]:
    """Recover a binding only from exact, independently rechecked operands.

    The research sidecar intentionally leaves some rows ``partial`` when its
    own scope/ranking gate cannot choose a binding.  This second-stage bridge
    can resolve such a row only when the current question and source cells
    supply a deterministic answer: exact normalized row labels, a coherent
    document/scope/year/unit identity, and either an explicit scope or one
    surviving scope.  Ranking alone is never used as an answer selector.
    """

    formula = record.get("formula") or {}
    formula_id = _text(formula.get("formula_id"))
    if formula_id not in _RESELECTABLE_FORMULA_IDS:
        return None, {}, "FORMULA_RESELECTION_UNSUPPORTED"
    explicit_scope = _question_scope(_text(record.get("question")))
    raw_by_operand = record.get("operand_matches")
    if not isinstance(raw_by_operand, Mapping):
        return None, {}, "OPERAND_MATCHES_MISSING"

    valid_by_operand: dict[str, list[tuple[Mapping[str, Any], dict[str, Any], Decimal]]] = {}
    for operand in operands:
        operand_id = _text(operand.get("operand_id"))
        raw_matches = raw_by_operand.get(operand_id)
        if not isinstance(raw_matches, list):
            return None, {}, "OPERAND_MATCHES_INVALID"
        valid: list[tuple[Mapping[str, Any], dict[str, Any], Decimal]] = []
        for raw_match in raw_matches:
            if not isinstance(raw_match, Mapping):
                continue
            if not _candidate_matches_metric(raw_match, operand):
                continue
            if explicit_scope and _text(raw_match.get("scope")).casefold() != explicit_scope:
                continue
            source, value, error = _selected_match(
                record=record,
                operand=operand,
                selected=raw_match,
                tables_by_uid=tables_by_uid,
            )
            if source is None or value is None:
                continue
            if _metric_label_core(source.get("row_label")) not in {
                _metric_label_core(value)
                for value in operand.get("metric_hints") or []
                if _metric_label_core(value)
            }:
                continue
            valid.append((raw_match, source, value))
        if not valid:
            return None, {}, f"NO_EXACT_OPERAND_MATCH_{operand_id}"
        valid_by_operand[operand_id] = valid

    def identity(source: Mapping[str, Any]) -> tuple[str, str, str, str, int | None]:
        return (
            str(source.get("document_id") or ""),
            str(source.get("ticker") or ""),
            str(source.get("scope") or ""),
            str(source.get("source_unit") or ""),
            source.get("report_year") if isinstance(source.get("report_year"), int) else None,
        )

    grouped: dict[str, dict[tuple[str, str, str, str, int | None], list[tuple[Mapping[str, Any], dict[str, Any], Decimal]]]] = {}
    for operand_id, matches in valid_by_operand.items():
        by_identity: dict[tuple[str, str, str, str, int | None], list[tuple[Mapping[str, Any], dict[str, Any], Decimal]]] = {}
        for match, source, value in matches:
            by_identity.setdefault(identity(source), []).append((match, source, value))
        grouped[operand_id] = by_identity

    operand_ids = [_text(operand.get("operand_id")) for operand in operands]
    common_identities = set(grouped[operand_ids[0]])
    for operand_id in operand_ids[1:]:
        common_identities &= set(grouped[operand_id])
    if not common_identities:
        return None, {}, "NO_COHERENT_OPERAND_IDENTITY"
    if not explicit_scope and len({key[2] for key in common_identities}) > 1:
        return None, {}, "AMBIGUOUS_SCOPE_AFTER_EXACT_LABEL"

    ast = _formula_ast(formula_id, operand_ids)
    if ast is None or validate_operation_ast(ast):
        return None, {}, "FORMULA_RESELECTION_AST_UNSUPPORTED"

    choices: list[tuple[tuple[int, int, int], tuple[Decimal, ...], tuple[Mapping[str, Any], ...], tuple[str, str, str, str, int | None]]] = []
    for coherent_identity in sorted(common_identities):
        per_operand = [grouped[operand_id][coherent_identity] for operand_id in operand_ids]
        for combo in product(*per_operand):
            sources = tuple(item[1] for item in combo)
            values = tuple(item[2] for item in combo)
            # A shared table is a strong structural signal for two operands
            # from the same statement.  It is a preference only: some valid
            # formulas span a balance sheet and an income/cash-flow statement.
            table_uids = {str(source.get("internal_table_uid") or "") for source in sources}
            shared_table = int(len(table_uids) == 1 and "" not in table_uids)
            score = (
                shared_table,
                -len(table_uids),
                -sum(_candidate_rank(item[0]) for item in combo),
            )
            choices.append((score, values, tuple(item[0] for item in combo), coherent_identity))
    if not choices:
        return None, {}, "NO_COHERENT_OPERAND_COMBINATION"

    best_score = max(choice[0] for choice in choices)
    best = [choice for choice in choices if choice[0] == best_score]
    value_signatures = {tuple(format(value, "f") for value in choice[1]) for choice in best}
    if len(value_signatures) > 1:
        return None, {}, "AMBIGUOUS_EXACT_OPERAND_VALUES"

    selected_choice = best[0]
    selected = {
        operand_id: selected_choice[2][index]
        for index, operand_id in enumerate(operand_ids)
    }
    return selected, {
        "mode": "operand_match_reselection_v1",
        "explicit_scope": explicit_scope,
        "coherent_identity": {
            "document_id": selected_choice[3][0],
            "ticker": selected_choice[3][1],
            "scope": selected_choice[3][2],
            "source_unit": selected_choice[3][3],
            "report_year": selected_choice[3][4],
        },
        "shared_table_preference": bool(best_score[0]),
        "table_count": -best_score[1],
        "candidate_rank_sum": -best_score[2],
        "exact_metric_labels": True,
        "stored_sidecar_values_used": False,
    }, None


def _candidate_for_record(
    record: Mapping[str, Any],
    *,
    current_question: Mapping[str, Any] | None,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    question_id = record.get("id")
    if isinstance(question_id, bool) or not isinstance(question_id, int):
        return None, "INVALID_QUESTION_ID"
    if current_question is not None and _normalized(current_question.get("question")) != _normalized(record.get("question")):
        return None, "QUESTION_TEXT_MISMATCH"
    formula = record.get("formula")
    if not isinstance(formula, Mapping):
        return None, "FORMULA_MISSING"
    formula_id = _text(formula.get("formula_id"))
    if formula_id not in SUPPORTED_FORMULA_IDS:
        return None, "UNSUPPORTED_FORMULA"
    if _text(formula.get("definition_status")).casefold() != "defined":
        return None, "FORMULA_NOT_DEFINED"
    if _text(record.get("operand_coverage_status")).casefold() != "complete":
        return None, "OPERAND_COVERAGE_INCOMPLETE"
    operands = [value for value in formula.get("operands") or [] if isinstance(value, Mapping)]
    if not operands or len(operands) != len(formula.get("operands") or []):
        return None, "OPERANDS_INVALID"
    operand_ids = [_text(value.get("operand_id")) for value in operands]
    if not all(operand_ids) or len(set(operand_ids)) != len(operand_ids):
        return None, "OPERAND_IDS_INVALID"
    ast = _formula_ast(formula_id, operand_ids)
    if ast is None or validate_operation_ast(ast):
        return None, "FORMULA_AST_UNSUPPORTED"
    selected = record.get("selected_operand_matches")
    resolution: dict[str, Any] = {
        "mode": "sidecar_selected_operand_matches_v1",
        "stored_sidecar_values_used": False,
    }
    if not isinstance(selected, Mapping) or set(selected) != set(operand_ids):
        if _text(record.get("evidence_completeness")).casefold() == "complete":
            return None, "SELECTED_BINDINGS_INCOMPLETE"
        selected, resolution, resolution_error = _reselect_operand_matches(
            record,
            operands=operands,
            tables_by_uid=tables_by_uid,
        )
        if selected is None:
            return None, resolution_error or "EVIDENCE_INCOMPLETE"
    elif _text(record.get("evidence_completeness")).casefold() != "complete":
        # Recheck every selected coordinate below, but retain the sidecar's
        # partial status in the candidate provenance instead of upgrading it.
        resolution["sidecar_evidence_completeness"] = _text(
            record.get("evidence_completeness")
        )

    sources: list[dict[str, Any]] = []
    raw_values: dict[str, Decimal] = {}
    identities: set[tuple[str, str, str, int]] = set()
    for operand in operands:
        operand_id = _text(operand.get("operand_id"))
        match = selected.get(operand_id)
        if not isinstance(match, Mapping):
            return None, "SELECTED_BINDING_INVALID"
        source, value, error = _selected_match(
            record=record,
            operand=operand,
            selected=match,
            tables_by_uid=tables_by_uid,
        )
        if source is None or value is None:
            return None, error or "SELECTED_BINDING_UNRESOLVED"
        sources.append(source)
        raw_values[operand_id] = value
        identities.add(
            (
                str(source.get("ticker") or ""),
                str(source.get("scope") or ""),
                str(source.get("source_unit") or ""),
                int(source.get("report_year")),
            )
        )
    scopes = {identity[1] for identity in identities}
    tickers = {identity[0] for identity in identities}
    source_units = {identity[2] for identity in identities}
    if len(scopes) != 1:
        return None, "MIXED_SCOPE"
    if len(tickers) != 1:
        return None, "MIXED_TICKER"
    if len(source_units) != 1:
        return None, "MIXED_SOURCE_UNIT"

    output_unit = _text(formula.get("output_unit"))
    source_unit = next(iter(source_units))
    divisor = _requested_divisor(_text(record.get("question")))
    if formula_id in {"percentage_change", "operating_cash_flow_argmax_period"}:
        divisor = Decimal(1)
    try:
        if formula_id == "operating_cash_flow_argmax_period":
            grounded = {
                operand_id: {
                    "period": int(sources[index]["report_year"]),
                    "value": raw_values[operand_id],
                }
                for index, operand_id in enumerate(operand_ids)
            }
            raw_answer = execute_ast(ast, grounded)
            target_unit = source_unit
            answer = Decimal(raw_answer)
        else:
            raw_answer = execute_ast(ast, raw_values)
            target_unit = "percent" if formula_id == "percentage_change" else (
                output_unit or source_unit
            )
            answer = Decimal(raw_answer)
            if formula_id != "percentage_change":
                answer = answer * vnd_scale(source_unit) / divisor
        if not answer.is_finite():
            return None, "NONFINITE_FORMULA_RESULT"
    except (ArithmeticError, InvalidOperation, KeyError, TypeError, ValueError):
        return None, "FORMULA_REPLAY_FAILED"

    # The candidate selector replays the values exposed in ``value``.  Keep
    # amount operands in the requested output unit so subtract replay equals
    # the emitted answer, while exact source-unit provenance remains attached
    # to every evidence row.
    if formula_id in _NET_FORMULA_IDS:
        for source in sources:
            source["value"] = raw_values[str(source["formula_operand_id"])] * vnd_scale(source_unit) / divisor
    for source in sources:
        source["requested_output_unit"] = target_unit
        source["requested_output_divisor"] = format(divisor, "f")

    if formula_id in _NET_FORMULA_IDS:
        operand_unit = output_unit or source_unit
    else:
        operand_unit = source_unit
    plan = {
        "family": "formula_evidence_replay",
        "formula_id": formula_id,
        "formula_output_unit": output_unit or target_unit,
        "operation_ast": ast,
        "operands": [
            {
                "operand_id": operand_id,
                "period": int(sources[index]["report_year"]),
                "entity": str(sources[index]["ticker"]),
                "ticker": str(sources[index]["ticker"]),
                "scope": str(sources[index]["scope"]),
                "unit": operand_unit,
            }
            for index, operand_id in enumerate(operand_ids)
        ],
        "entities": [next(iter(tickers))],
        "scope": next(iter(scopes)),
        "requested_unit": operand_unit,
        "years": [int(source["report_year"]) for source in sources],
        "candidate_only": True,
        "formula_bridge_protocol": FORMULA_EVIDENCE_BRIDGE_PROTOCOL,
    }
    first = sources[0]
    selection = {
        "value": answer,
        "score": float(formula.get("confidence") or 0.0),
        "candidate_source": FORMULA_EVIDENCE_BRIDGE_PROTOCOL,
        "formula_id": formula_id,
        "internal_table_uid": first.get("internal_table_uid"),
        "row_index": first.get("row_index"),
        "column_index": first.get("column_index"),
        "row_label": first.get("row_label"),
        "ticker": first.get("ticker"),
        "scope": first.get("scope"),
        "report_year": first.get("report_year"),
    }
    return (
        {
            "answer": answer,
            "sources": sources,
            "query": "float(df1.loc[0, 'value'])",
            "tier": FORMULA_EVIDENCE_BRIDGE_PROTOCOL,
            "selection": selection,
            "question_plan": plan,
            "formula_id": formula_id,
            "formula_expression": formula.get("expression"),
            "formula_confidence": formula.get("confidence"),
            "formula_source_question_id": question_id,
            "formula_bridge_resolution": resolution,
        },
        None,
    )


def build_formula_evidence_candidates(
    path: Path,
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    questions_by_id: Mapping[int, Mapping[str, Any]] | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Build independently replayed formula candidates from one sidecar."""

    stats: Counter[str] = Counter()
    candidates: dict[int, dict[str, Any]] = {}
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"formula evidence sidecar does not exist: {path}")
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        stats["rows_read"] += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            stats["rejected_invalid_json"] += 1
            continue
        if not isinstance(record, Mapping):
            stats["rejected_non_mapping"] += 1
            continue
        candidate, error = _candidate_for_record(
            record,
            current_question=(questions_by_id or {}).get(record.get("id")),
            tables_by_uid=tables_by_uid,
        )
        if candidate is None:
            stats[f"rejected_{error or 'unknown'}"] += 1
            continue
        question_id = int(record["id"])
        if question_id in candidates:
            stats["rejected_duplicate_question_id"] += 1
            candidates.pop(question_id, None)
            continue
        candidates[question_id] = candidate
        stats["candidates_built"] += 1
        stats[f"built_{candidate['formula_id']}"] += 1
        if (
            (candidate.get("formula_bridge_resolution") or {}).get("mode")
            == "operand_match_reselection_v1"
        ):
            stats["reselected_candidates"] += 1
    stats["candidate_question_count"] = len(candidates)
    return candidates, {
        "protocol": FORMULA_EVIDENCE_BRIDGE_PROTOCOL,
        "path": str(path),
        "enabled": True,
        "table_count": len(tables_by_uid),
        "stats": dict(sorted(stats.items())),
        "candidate_question_ids": sorted(candidates),
        "candidate_only": True,
        "answer_authority": "current_structured_table_coordinate_and_decimal_replay",
        "stored_sidecar_answers_used": False,
    }


__all__ = [
    "FORMULA_EVIDENCE_BRIDGE_PROTOCOL",
    "SUPPORTED_FORMULA_IDS",
    "build_formula_evidence_candidates",
]
