#!/usr/bin/env python3
"""Run a strict source-cell ablation for corporate-income-tax payables.

The generic direct resolver often sees several nearby tax tables: current tax
expense, tax paid in cash flow, deferred tax, and the balance owed to the tax
authority.  This adapter binds only the latter balance shape.  It accepts a
direct lookup with one issuer/year, an explicitly requested beginning or
ending balance, a source-declared unit, an exact corporate-income-tax row, and
payable-oriented period columns/section context.

The route is family-based rather than question-ID based.  Unscoped separate
and consolidated duplicates are accepted only when their replayed Decimal
answers agree.  A successful run remains an authorized best-effort candidate
lane; it is not a strict VERIFIED release or an official score.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


VARIANT_PROTOCOL = "vifinqa_tax_payable_strict_v1"
TAX_PAYABLE_TIER = "program_tax_payable_v1"
HELPER_NAME = "_vifinqa_named_compensation_helper_for_tax_payable"


def _load_helper() -> Any:
    """Reuse the canonical builder-loading and source-cell helpers."""

    helper_path = Path(__file__).with_name("run_named_compensation_variant_v1.py")
    spec = importlib.util.spec_from_file_location(HELPER_NAME, helper_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load variant helper: {helper_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[HELPER_NAME] = module
    spec.loader.exec_module(module)
    return module


HELPER = _load_helper()
BUILDER = HELPER.BUILDER
_ORIGINAL_PROGRAM_AWARE = HELPER._ORIGINAL_PROGRAM_AWARE
_ORIGINAL_ROUTE_PRIORITY = HELPER._ORIGINAL_ROUTE_PRIORITY

_TRACE: list[dict[str, Any]] = []
_STATS: Counter[str] = Counter()

_SAFE_TABLE_KINDS = {
    "financial_note",
    "financial_note_detail",
    "financial_data_schedule",
}
_TAX_LABELS = {
    "thue tndn",
    "thue thu nhap doanh nghiep",
}
_PAYABLE_MARKERS = (
    "phai nop",
    "phai tra",
    "ngan sach nha nuoc",
)
_RECEIVABLE_MARKERS = (
    "phai thu",
    "tai san thue",
)
_BALANCE_MARKERS = (
    "so dau nam",
    "so du dau nam",
    "so cuoi nam",
    "so du cuoi nam",
    "so dau ky",
    "so cuoi ky",
    "31/12",
    "1/1",
)
_NORMALIZED_START_DATE_RE = re.compile(
    r"\b0?1\s+0?1(?:\s+(?:nam\s+)?(?:19|20)\d{2})?\b"
)
_NORMALIZED_END_DATE_RE = re.compile(
    r"\b0?(?:28|29|30|31)\s+(?:0?[1-9]|1[0-2])"
    r"(?:\s+(?:nam\s+)?(?:19|20)\d{2})?\b"
)
_ROW_PERIOD_MARKERS = ("dau nam", "cuoi nam", "dau ky", "cuoi ky")
_BEGINNING_CUES = ("dau nam", "dau ky", "dau ky ke toan")
_ENDING_CUES = (
    "cuoi nam",
    "cuoi ky",
    "den ngay",
    "tai ngay",
    "ket thuc",
    "31/12",
)


def _question_id(item: Mapping[str, Any]) -> int | None:
    for key in ("id", "question_id"):
        try:
            if item.get(key) is not None:
                return int(item[key])
        except (TypeError, ValueError):
            continue
    plan = item.get("question_plan")
    if isinstance(plan, Mapping):
        try:
            if plan.get("question_id") is not None:
                return int(plan["question_id"])
        except (TypeError, ValueError):
            pass
    return None


def _normalize(value: Any) -> str:
    return HELPER._normalize(value)


def _row_label(row: Any) -> str:
    return HELPER._row_label(row)


def _single_ticker(plan: Mapping[str, Any]) -> str:
    return HELPER._single_ticker(plan)


def _single_year(plan: Mapping[str, Any]) -> int | None:
    return HELPER._single_year(plan)


def _intent(question_norm: str) -> str | None:
    has_beginning = any(cue in question_norm for cue in _BEGINNING_CUES)
    has_ending = any(cue in question_norm for cue in _ENDING_CUES)
    if has_beginning == has_ending:
        return None
    return "beginning" if has_beginning else "ending"


def _family_spec(item: Mapping[str, Any]) -> dict[str, Any] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "direct_lookup":
        return None
    question = str(item.get("question") or "")
    question_norm = _normalize(question)
    if not any(
        phrase in question_norm
        for phrase in ("thue tndn", "thue thu nhap doanh nghiep")
    ):
        return None
    # Keep expense, deferred-tax, asset, and cash-paid questions out of this
    # balance route even when their source table is adjacent to a payable note.
    if any(
        phrase in question_norm
        for phrase in ("chi phi", "thu nhap hoan lai", "tai san thue", "da nop")
    ):
        return None
    if not any(
        phrase in question_norm
        for phrase in ("phai nop", "phai tra", "so du", "dau nam", "cuoi nam", "den ngay", "31/12")
    ):
        return None
    intent = _intent(question_norm)
    if intent is None:
        return None

    ticker = _single_ticker(plan)
    year = _single_year(plan)
    if not ticker or year is None:
        return None
    raw_scope = str(plan.get("scope") or plan.get("reporting_scope") or "")
    scope = raw_scope.strip().lower()
    if scope not in {"", "separate", "consolidated"}:
        return None

    metric = str(item.get("effective_metric") or "").strip()
    if not metric:
        operands = plan.get("operands") or []
        if operands and isinstance(operands[0], Mapping):
            metric = str(operands[0].get("metric") or "").strip()
    return {
        "question_id": _question_id(item),
        "question": question,
        "question_norm": question_norm,
        "ticker": ticker,
        "year": year,
        "scope": scope,
        "intent": intent,
        "metric": metric or question,
        "output_divisor": BUILDER.requested_divisor(question),
    }


def _table_kind(table: Mapping[str, Any]) -> str:
    classifier = getattr(BUILDER.source_first_lookup_module, "_table_kind", None)
    raw = classifier(table) if callable(classifier) else ""
    if not raw:
        function = table.get("table_function")
        raw = function.get("kind") if isinstance(function, Mapping) else function
    return _normalize(raw).replace("_", " ")


def _table_is_safe(table: Mapping[str, Any]) -> bool:
    return _table_kind(table).replace(" ", "_") in _SAFE_TABLE_KINDS


def _table_multiplier(table: Mapping[str, Any]) -> Decimal | None:
    return HELPER._table_multiplier(table)


def _table_context(table: Mapping[str, Any]) -> str:
    values: list[str] = []
    trace = table.get("context_trace") or {}
    if isinstance(trace, Mapping):
        for key in ("source_title", "summary", "topic"):
            value = trace.get(key)
            if isinstance(value, Mapping):
                values.extend(str(part or "") for part in value.values())
            else:
                values.append(str(value or ""))
        unit_labels = trace.get("unit_labels") or []
        if isinstance(unit_labels, list):
            values.extend(str(value or "") for value in unit_labels)
    for field in ("headers", "column_labels", "row_paths"):
        raw = table.get(field) or []
        if isinstance(raw, Mapping):
            values.extend(str(value or "") for value in raw.values())
        elif isinstance(raw, (list, tuple)):
            values.extend(str(value or "") for value in raw)
        elif raw:
            values.append(str(raw))
    # A section row such as ``Phải nộp`` may only exist in structured rows,
    # not in headers or context_trace.  Include a bounded local vocabulary.
    for row in (table.get("rows") or [])[:12]:
        if isinstance(row, list):
            values.append(_row_label(row))
    return _normalize(" ".join(values))


def _clean_tax_label(label: str) -> str:
    value = _normalize(label)
    value = re.sub(r"^(?:\d+|[ivxlcdm]+)[.)]?\s+", "", value)
    value = re.sub(r"\s*\(?[*†‡]+\)?\s*$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _is_tax_payable_row(row: list[Any]) -> bool:
    label = _clean_tax_label(_row_label(row))
    if not label or "chi phi" in label or "tai san" in label or "hoan lai" in label:
        return False
    if label in _TAX_LABELS:
        return True
    return bool(
        re.fullmatch(
            r"(?:thue tndn|thue thu nhap doanh nghiep)\s+(?:con\s+)?phai nop(?:\s+.*)?",
            label,
        )
    )


def _row_matches_period_intent(row: list[Any], *, intent: str) -> bool:
    """Reject an explicitly opposite-period payable row.

    Most tax notes use one generic tax row with opening/closing columns.  A
    few notes instead spell out both balances as separate rows (for example,
    ``Thuế TNDN phải nộp đầu năm`` and ``Thuế TNDN còn phải nộp cuối năm``).
    In that shape the column header only says ``Năm 2024`` and cannot resolve
    the period, so the row label is the authoritative local period cue.
    Unqualified rows remain eligible and still rely on the column contract.
    """

    label = _clean_tax_label(_row_label(row))
    has_beginning = any(marker in label for marker in _BEGINNING_CUES)
    has_ending = any(marker in label for marker in _ENDING_CUES)
    if has_beginning and has_ending:
        return False
    if intent == "beginning":
        return not has_ending
    if intent == "ending":
        return not has_beginning
    return False


def _local_payable_section(table: Mapping[str, Any], *, row_index: int) -> bool:
    rows = table.get("rows") or []
    start = max(0, int(row_index) - 12)
    for index in range(int(row_index) - 1, start - 1, -1):
        row = rows[index]
        if not isinstance(row, list):
            continue
        label = _normalize(_row_label(row))
        if not label:
            continue
        if any(marker in label for marker in _RECEIVABLE_MARKERS):
            return False
        if any(marker in label for marker in _PAYABLE_MARKERS):
            return True
        # Do not borrow a section marker across a distinct nonnumeric heading.
        if not any(BUILDER.parse_decimal(cell) is not None for cell in row[1:]):
            return False
    return False


def _local_receivable_section(table: Mapping[str, Any], *, row_index: int) -> bool:
    """Detect an explicitly marked ``Phải thu`` subsection for one row."""

    rows = table.get("rows") or []
    start = max(0, int(row_index) - 12)
    for index in range(int(row_index) - 1, start - 1, -1):
        row = rows[index]
        if not isinstance(row, list):
            continue
        label = _normalize(_row_label(row))
        if not label:
            continue
        has_receivable = any(marker in label for marker in _RECEIVABLE_MARKERS)
        has_payable = any(marker in label for marker in _PAYABLE_MARKERS)
        if has_receivable and has_payable:
            # A multi-row column header may contain both ``Số phải thu`` and
            # ``Số phải nộp``.  It is not a local subsection marker.
            return False
        if has_receivable:
            return True
        if has_payable:
            return False
        if not any(BUILDER.parse_decimal(cell) is not None for cell in row[1:]):
            return False
    return False


def _has_explicit_payable_header(table: Mapping[str, Any]) -> bool:
    """Return whether the table exposes a payable-specific header cell."""

    raw_headers: list[Any] = []
    for field in ("headers", "column_labels"):
        value = table.get(field) or []
        if isinstance(value, Mapping):
            raw_headers.extend(value.values())
        elif isinstance(value, (list, tuple)):
            raw_headers.extend(value)
        elif value:
            raw_headers.append(value)
    # Multi-row HTML/PDF headers are often retained only in the first few
    # structured rows.  Inspect those rows as header vocabulary; the actual
    # cell-level check below still binds the answer to one payable column.
    for row in (table.get("rows") or [])[:4]:
        if isinstance(row, list):
            raw_headers.extend(row)
    header_text = _normalize(" ".join(str(value or "") for value in raw_headers))
    return any(marker in header_text for marker in ("phai nop", "phai tra"))


def _column_is_payable(column_context: str) -> bool:
    """Reject a chosen receivable column in a mixed tax table."""

    context = _normalize(column_context)
    has_payable = any(marker in context for marker in _PAYABLE_MARKERS)
    has_receivable = any(marker in context for marker in _RECEIVABLE_MARKERS)
    if has_receivable and not has_payable:
        return False
    return True


_PAYABLE_MOVEMENT_COLUMN_MARKERS = (
    "phat sinh",
    "trong nam",
    "da nop",
    "bu tru",
    "chi tra",
    "tang",
    "giam",
)


def _choose_payable_balance_cell(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    evidence: list[dict[str, Any]],
    spec: Mapping[str, Any],
) -> tuple[int, Decimal, list[dict[str, Any]], str] | None:
    """Choose a unique period-specific payable balance column.

    Mixed tax notes can have ``Số phải thu`` and ``Số phải nộp`` columns in the
    same period block.  The generic semantic scorer may tie a payable balance
    with a payable movement column, so this recovery enumerates only explicit
    payable balance headers and binds them to the requested opening/closing
    period.  Ambiguous column sets are rejected.
    """

    period_info_fn = getattr(BUILDER, "_semantic_column_period_info", None)
    context_fn = getattr(BUILDER, "semantic_column_context", None)
    if not callable(period_info_fn) or not callable(context_fn):
        return None
    candidates: list[tuple[int, Decimal, str, int]] = []
    for column_index, cell in enumerate(row[1:], start=1):
        raw_value = BUILDER.parse_decimal(cell)
        if raw_value is None:
            continue
        column_context = _normalize(
            context_fn(
                table,
                evidence,
                column_index=int(column_index),
                row_index=int(row_index),
            )
        )
        if not _column_is_payable(column_context):
            continue
        if any(marker in column_context for marker in _PAYABLE_MOVEMENT_COLUMN_MARKERS):
            continue
        period_info = period_info_fn(
            table,
            evidence,
            column_index=int(column_index),
            row_index=int(row_index),
        )
        years = period_info.get("years") or set()
        if years and int(spec["year"]) not in years:
            continue
        intent = str(spec["intent"])
        if intent == "beginning":
            if period_info.get("end") and not period_info.get("start"):
                continue
            period_score = int(bool(period_info.get("start")))
        elif intent == "ending":
            if period_info.get("start") and not period_info.get("end"):
                continue
            period_score = int(bool(period_info.get("end")))
        else:
            continue
        header_score = int(
            any(marker in column_context for marker in ("so phai nop", "so phai tra"))
        )
        candidates.append((int(column_index), raw_value, column_context, period_score + header_score))
    if not candidates:
        return None
    best_score = max(candidate[3] for candidate in candidates)
    best = [candidate for candidate in candidates if candidate[3] == best_score]
    if len(best) != 1:
        return None
    column_index, raw_value, column_context, _ = best[0]
    return column_index, raw_value, evidence, column_context


def _is_payable_balance_table(table: Mapping[str, Any], *, row_index: int) -> bool:
    context = _table_context(table)
    if any(marker in context for marker in _RECEIVABLE_MARKERS):
        # A table containing both ``Phải nộp`` and ``Phải thu`` is still
        # eligible when the row is in the payable subsection or the table has
        # explicit payable header columns.  The selected cell is checked
        # separately so a mixed table cannot leak a ``Phải thu`` value.
        if _local_receivable_section(table, row_index=row_index):
            return False
        if not _local_payable_section(table, row_index=row_index) and not _has_explicit_payable_header(table):
            return False
    has_payable = any(marker in context for marker in _PAYABLE_MARKERS)
    has_balance_shape = any(marker in context for marker in _BALANCE_MARKERS)
    if not has_balance_shape:
        # Normalization turns ``1/1/2020`` and ``31/12/2020`` into token
        # sequences.  Use token-boundary regexes rather than substring checks:
        # a value such as ``1.160.511`` must not look like the date ``1/1``.
        has_balance_shape = bool(
            _NORMALIZED_START_DATE_RE.search(context)
            or _NORMALIZED_END_DATE_RE.search(context)
            or any(marker in context for marker in _ROW_PERIOD_MARKERS)
        )
    if not has_payable or not has_balance_shape:
        return False
    return _local_payable_section(table, row_index=row_index) or has_payable


def _candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not _table_is_safe(table) or not _is_tax_payable_row(row):
        return None
    if not _row_matches_period_intent(row, intent=str(spec["intent"])):
        return None
    if not _is_payable_balance_table(table, row_index=row_index):
        return None
    chosen = HELPER.HELPER._choose_cell(
        table,
        row_index=row_index,
        row=row,
        spec=spec,
    )
    if chosen is not None and not _column_is_payable(chosen[3]):
        # The canonical scorer can prefer the first numeric column when the
        # column header carries both a period and a metric label.  Re-run the
        # same scorer with an explicit payable metric, but only as a recovery
        # inside a table whose headers already expose payable columns.
        if _has_explicit_payable_header(table):
            chosen = _choose_payable_balance_cell(
                table,
                row_index=row_index,
                row=row,
                evidence=chosen[2],
                spec=spec,
            )
        else:
            chosen = None
    if chosen is None:
        return None
    column_index, raw_value, evidence, column_context = chosen
    multiplier = _table_multiplier(table)
    if multiplier is None:
        return None
    answer = raw_value * multiplier / spec["output_divisor"]
    return {
        "answer": answer,
        "raw_value": raw_value,
        "source_multiplier": multiplier,
        "row_index": int(row_index),
        "column_index": int(column_index),
        "row_label": _row_label(row),
        "column_context": column_context,
        "evidence": evidence,
        "table": table,
        "kind": _table_kind(table).replace(" ", "_"),
        "context": _table_context(table),
    }


def _candidate_rank(
    candidate: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[int, int, str]:
    table = candidate["table"]
    scope = str(BUILDER.table_reporting_scope(table) or "").lower()
    return (
        int(bool(spec.get("scope")) and scope == str(spec.get("scope") or "")),
        int(candidate.get("kind") == "financial_note"),
        str(table.get("internal_table_uid") or ""),
    )


def _sources_and_selection(
    candidate: Mapping[str, Any], *, spec: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    table = candidate["table"]
    document_id = str(table.get("document_id") or "").removesuffix(".txt")
    uid = str(table.get("internal_table_uid") or "")
    source = {
        "raw_value_decimal": str(candidate["raw_value"]),
        "value": candidate["answer"],
        "role": "tax_payable_source_cell",
        "document_id": document_id,
        "internal_table_uid": uid,
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "row_label": str(candidate.get("row_label") or ""),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "requested_output_divisor": str(spec["output_divisor"]),
        "period_intent": spec["intent"],
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    selection = {
        "score": 70.9,
        "value": candidate["answer"],
        "raw_value": candidate["raw_value"],
        "source_multiplier": candidate["source_multiplier"],
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "row_label": str(candidate.get("row_label") or ""),
        "column_context": candidate.get("column_context"),
        "document_id": document_id,
        "internal_table_uid": uid,
        "candidate_rank": 0,
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "program_family": "tax_payable",
        "program_kind": candidate.get("kind"),
        "period_intent": spec["intent"],
        "question_output_divisor": str(spec["output_divisor"]),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "promotion_allowed": False,
    }
    if candidate.get("navigation_rank") is not None:
        source["candidate_navigation_rank"] = int(candidate["navigation_rank"])
        source["candidate_navigation_tiebreak"] = bool(
            candidate.get("navigation_tiebreak")
        )
        selection["candidate_navigation_rank"] = int(candidate["navigation_rank"])
        selection["candidate_navigation_tiebreak"] = bool(
            candidate.get("navigation_tiebreak")
        )
    return [source], selection


def _candidate_navigation_ranks(item: Mapping[str, Any]) -> dict[str, int]:
    """Return review-bundle ranks as navigation metadata only.

    The bundle narrows the table search, but it never supplies an answer.  A
    selected cell must still pass this module's family contract and be
    replayed from the full structured asset.
    """

    ranks: dict[str, int] = {}
    for raw_candidate in item.get("candidates") or []:
        if not isinstance(raw_candidate, Mapping):
            continue
        uid = str(raw_candidate.get("internal_table_uid") or "").strip()
        if not uid:
            continue
        try:
            rank = int(
                raw_candidate.get("rank")
                if raw_candidate.get("rank") is not None
                else raw_candidate.get("original_retrieval_rank")
            )
        except (TypeError, ValueError):
            continue
        if rank >= 1:
            ranks[uid] = min(rank, ranks.get(uid, rank))
    return ranks


def _tables_for_spec(
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    spec: Mapping[str, Any],
    *,
    item: Mapping[str, Any] | None = None,
) -> list[Mapping[str, Any]]:
    tables = HELPER._tables_for_spec(tables_by_uid, spec)
    if not item:
        return tables
    navigation_ranks = _candidate_navigation_ranks(item)
    if not navigation_ranks:
        return tables
    hinted_tables = [
        table
        for table in tables
        if str(table.get("internal_table_uid") or "") in navigation_ranks
    ]
    if not hinted_tables:
        _STATS["candidate_navigation_hints_missing_from_asset"] += 1
        return tables
    _STATS["candidate_navigation_filtered_questions"] += 1
    _STATS["candidate_navigation_tables_dropped"] += len(tables) - len(hinted_tables)
    return hinted_tables


def _resolve_tax_payable(
    item: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    if not tables_by_uid:
        return None
    tables = _tables_for_spec(tables_by_uid, spec, item=item)
    _STATS["candidate_table_count_total"] += len(tables)
    candidates: list[dict[str, Any]] = []
    for table in tables:
        if not _table_is_safe(table):
            _STATS["table_rejected_unsafe_kind"] += 1
            continue
        for row_index, row in enumerate(table.get("rows") or []):
            if not isinstance(row, list):
                continue
            candidate = _candidate(table, row_index=row_index, row=row, spec=spec)
            if candidate is not None:
                candidates.append(candidate)

    if not candidates:
        _STATS["family_no_strict_candidate"] += 1
        _TRACE.append(
            {
                "question_id": spec.get("question_id"),
                "status": "REJECTED_NO_STRICT_SOURCE_CANDIDATE",
                "ticker": spec["ticker"],
                "year": spec["year"],
                "scope": spec.get("scope") or None,
                "period_intent": spec["intent"],
            }
        )
        return None

    answers = {candidate["answer"] for candidate in candidates}
    navigation_tiebreak = False
    if len(answers) != 1:
        navigation_ranks = _candidate_navigation_ranks(item)
        ranked_candidates = [
            candidate
            for candidate in candidates
            if str(candidate["table"].get("internal_table_uid") or "")
            in navigation_ranks
        ]
        if ranked_candidates:
            best_navigation_rank = min(
                navigation_ranks[
                    str(candidate["table"].get("internal_table_uid") or "")
                ]
                for candidate in ranked_candidates
            )
            best_navigation_candidates = [
                candidate
                for candidate in ranked_candidates
                if navigation_ranks[
                    str(candidate["table"].get("internal_table_uid") or "")
                ]
                == best_navigation_rank
            ]
            best_navigation_answers = {
                candidate["answer"] for candidate in best_navigation_candidates
            }
            if len(best_navigation_answers) == 1:
                candidates = best_navigation_candidates
                answers = best_navigation_answers
                navigation_tiebreak = True
                _STATS["accepted_candidate_navigation_rank_tiebreak"] += 1
                for candidate in candidates:
                    candidate["navigation_rank"] = best_navigation_rank
                    candidate["navigation_tiebreak"] = True
        if not navigation_tiebreak:
            _STATS["rejected_conflicting_duplicate_answers"] += 1
            _TRACE.append(
                {
                    "question_id": spec.get("question_id"),
                    "status": "REJECTED_CONFLICTING_DUPLICATE_ANSWERS",
                    "ticker": spec["ticker"],
                    "year": spec["year"],
                    "scope": spec.get("scope") or None,
                    "period_intent": spec["intent"],
                    "answers": sorted(str(value) for value in answers),
                    "candidate_count": len(candidates),
                }
            )
            return None

    selected = sorted(candidates, key=lambda value: _candidate_rank(value, spec), reverse=True)[0]
    sources, selection = _sources_and_selection(selected, spec=spec)
    answer = selected["answer"]
    _STATS["family_accepted"] += 1
    _STATS[f"accepted_{selected['kind']}"] += 1
    _TRACE.append(
        {
            "question_id": spec.get("question_id"),
            "status": "ACCEPTED",
            "ticker": spec["ticker"],
            "year": spec["year"],
            "scope": spec.get("scope") or None,
            "period_intent": spec["intent"],
            "candidate_count": len(candidates),
            "answer": str(answer),
            "document_id": sources[0]["document_id"],
            "internal_table_uid": sources[0]["internal_table_uid"],
            "row_index": sources[0]["row_index"],
            "column_index": sources[0]["column_index"],
            "raw_value": sources[0]["raw_value_decimal"],
            "source_to_vnd_multiplier": sources[0]["source_to_vnd_multiplier"],
            "selected_kind": selected["kind"],
            "row_label": selected["row_label"],
            "column_context": selected.get("column_context"),
            "candidate_navigation_rank": selected.get("navigation_rank"),
            "candidate_navigation_tiebreak": bool(
                selected.get("navigation_tiebreak")
            ),
        }
    )
    return answer, sources, "float(df1.loc[0, 'value'])", TAX_PAYABLE_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    spec = _family_spec(item)
    if spec is not None:
        _STATS["family_seen"] += 1
        result = _resolve_tax_payable(item, spec, tables_by_uid=tables_by_uid)
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == TAX_PAYABLE_TIER:
        return 70.9
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _write_variant_metadata(output_dir: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["tax_payable_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "route": TAX_PAYABLE_TIER,
        "contract": {
            "family": "direct_lookup",
            "one_ticker_and_one_report_year": True,
            "beginning_or_ending_intent_required": True,
            "financial_note_or_schedule_only": True,
            "exact_corporate_income_tax_row_required": True,
            "payable_balance_column_shape_required": True,
            "receivable_subsection_rejected": True,
            "current_or_beginning_period_column_required": True,
            "declared_source_unit_required": True,
            "conflicting_duplicate_answers_rejected": True,
            "question_id_allowlist": False,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "tax_payable_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    trace_path = output_dir / "tax_payable_trace_v1.jsonl"
    trace_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in _TRACE
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = BUILDER.configure_parser(argparse.ArgumentParser())
    args = parser.parse_args()
    BUILDER.program_aware_answer = _patched_program_aware
    BUILDER._route_priority = _patched_route_priority
    BUILDER.build(args)
    _write_variant_metadata(args.output)
    print(
        json.dumps(
            {
                "protocol": VARIANT_PROTOCOL,
                "output": str(args.output),
                "stats": dict(sorted(_STATS.items())),
                "trace_count": len(_TRACE),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
