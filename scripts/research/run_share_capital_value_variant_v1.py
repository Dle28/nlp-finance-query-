#!/usr/bin/env python3
"""Run a strict source-cell ablation for monetary share-capital values.

The direct-lookup population contains a reusable distinction between a
monetary equity balance and a nearby transaction/count row.  This adapter
binds the former to an explicit owner-contribution/share-capital row, an
ending-period amount column, and a source-side VND unit.  A continuation page
may omit its unit; in that case the unit is accepted only from another table
in the same source document whose unit is explicitly declared.  It never
infers a unit from magnitude, filename, retrieval rank, or a question ID.

This is a research candidate lane.  It does not use question IDs as rules,
does not read an answer from retrieval metadata, and cannot authorize strict
verification or an official score.
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


VARIANT_PROTOCOL = "vifinqa_share_capital_value_strict_v1"
SHARE_CAPITAL_VALUE_TIER = "program_share_capital_value_v1"
HELPER_NAME = "_vifinqa_named_compensation_helper_for_share_capital_value"


def _load_helper() -> Any:
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
    "balance_sheet",
    "equity_change_statement",
    "financial_note",
    "financial_note_detail",
}
_DIRECT_INVESTMENT_TABLE_KINDS = {"financial_data_schedule"}
_ENDING_CUES = (
    "cuoi nam",
    "cuoi ky",
    "den ngay",
    "tai ngay",
    "ket thuc",
    "31/12",
    "31 thang 12",
)
_MONEY_UNIT_MARKERS = (
    "vnd",
    "dong",
    "ty",
    "ti",
    "trieu",
    "nghin",
    "ngan",
    "million",
    "billion",
    "thousand",
)
_COUNT_MARKERS = (
    "so luong",
    "so co phieu",
    "co phieu",
    "shares",
)
_PRIOR_MARKERS = (
    "nam truoc",
    "ky truoc",
    "previous",
    "prior",
)
_MOVEMENT_MARKERS = (
    "so tang giam trong nam",
    "so phat sinh",
    "phat sinh trong nam",
    "da nop trong nam",
    "hoan trong nam",
)
_OWNER_ROW_PATTERNS = (
    "von gop cua chu so huu",
    "von gop chu so huu",
)
_NUMBERING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*[.)]|[ivxlcdm]+[.)])\s*",
    re.IGNORECASE,
)
_TARGET_STOP_CUES = (
    " den ngay",
    " tai ngay",
    " ket thuc",
    " la bao nhieu",
    " bao nhieu",
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


def _clean_label(value: Any) -> str:
    label = _normalize(value)
    label = _NUMBERING_RE.sub("", label)
    label = re.sub(r"\s*\(?[*†‡]+\)?\s*$", "", label)
    return re.sub(r"\s+", " ", label).strip()


def _has_money_unit_word(question_norm: str) -> bool:
    # Require a monetary currency word.  ``ty`` alone is unsafe because it
    # also occurs in ``cong ty``; all accepted population examples say VND or
    # đồng explicitly.
    return bool(re.search(r"\b(?:dong|vnd)\b", question_norm))


def _target_phrase(question_norm: str) -> str:
    """Return the object after ``vào`` when a question names one.

    A balance-sheet equity row is not evidence for an investment *into a
    named fund/subsidiary*.  The target is treated as a lexical contract and
    must be found in the source table context; it is never a question-ID
    exception or an answer lookup.
    """

    marker = " vao "
    start = question_norm.find(marker)
    if start < 0:
        return ""
    target = question_norm[start + len(marker) :]
    stop_positions = [
        position
        for cue in _TARGET_STOP_CUES
        for position in [target.find(cue)]
        if position >= 0
    ]
    if stop_positions:
        target = target[: min(stop_positions)]
    return re.sub(r"\s+", " ", target).strip(" ,;:-")


def _family_spec(item: Mapping[str, Any]) -> dict[str, Any] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "direct_lookup":
        return None
    question = str(item.get("question") or "")
    question_norm = _normalize(question)
    if not any(phrase in question_norm for phrase in ("von co phan", "von gop")):
        return None
    if not _has_money_unit_word(question_norm):
        return None
    if not any(cue in question_norm for cue in _ENDING_CUES):
        return None
    # These are comparison/ratio/conditional families.  They require a
    # multi-operand contract and must not be silently collapsed into a
    # one-cell equity lookup.
    if any(
        marker in question_norm
        for marker in (
            "chenh lech",
            "so voi",
            "ty trong",
            "phan tram",
            "lon nhat",
            "nho nhat",
            "nam nao",
        )
    ):
        return None

    direct_investment = (
        "von gop truc tiep" in question_norm
        and bool(_target_phrase(question_norm))
    )
    if direct_investment:
        metric_kind = "direct_investment_contribution"
    elif "von gop" in question_norm:
        metric_kind = "owner_contribution"
    else:
        metric_kind = "share_capital"

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
    share_state = "generic"
    if metric_kind == "share_capital":
        if "da phat hanh" in question_norm:
            share_state = "issued"
        elif "dang ky" in question_norm or "duoc phep" in question_norm:
            share_state = "registered"

    return {
        "question_id": _question_id(item),
        "question": question,
        "question_norm": question_norm,
        "ticker": ticker,
        "year": year,
        "scope": scope,
        "metric": metric or question,
        "metric_kind": metric_kind,
        "target_phrase": _target_phrase(question_norm)
        if metric_kind == "direct_investment_contribution"
        else "",
        "share_state": share_state,
        "output_divisor": BUILDER.requested_divisor(question),
    }


def _table_kind(table: Mapping[str, Any]) -> str:
    classifier = getattr(BUILDER.source_first_lookup_module, "_table_kind", None)
    raw = classifier(table) if callable(classifier) else ""
    if not raw:
        function = table.get("table_function")
        raw = function.get("kind") if isinstance(function, Mapping) else function
    if isinstance(raw, Mapping):
        raw = raw.get("kind") or raw.get("label")
    return _normalize(raw).replace(" ", "_")


def _table_is_safe(
    table: Mapping[str, Any], *, spec: Mapping[str, Any] | None = None
) -> bool:
    kind = _table_kind(table)
    if kind in _SAFE_TABLE_KINDS:
        return True
    return bool(
        spec
        and spec.get("metric_kind") == "direct_investment_contribution"
        and kind in _DIRECT_INVESTMENT_TABLE_KINDS
    )


def _table_document_id(table: Mapping[str, Any]) -> str:
    return str(table.get("document_id") or "").removesuffix(".txt")


def _row_matches_metric(
    row: list[Any], *, spec: Mapping[str, Any]
) -> bool:
    label = _clean_label(_row_label(row))
    if not label:
        return False
    # ``Thặng dư vốn cổ phần`` is a different accounting component even
    # though it contains the words ``vốn cổ phần``.
    if "thang du" in label or "quy" in label or "loi nhuan" in label:
        return False
    kind = str(spec["metric_kind"])
    if kind == "direct_investment_contribution":
        # The row must describe the parent's direct investment leg.  A
        # generic ``Vốn góp của chủ sở hữu`` row belongs to the issuer's own
        # equity and must not be substituted for an investment into a named
        # fund or subsidiary.
        return "dau tu truc tiep" in label and "dau tu gian tiep" not in label
    if kind == "owner_contribution":
        if not any(pattern in label for pattern in _OWNER_ROW_PATTERNS):
            return False
        # ``Góp vốn`` is a cash-flow/transaction label, not the balance
        # ``Vốn góp của chủ sở hữu``.  The ordered phrase check above already
        # separates them; this explicit guard documents the boundary.
        if label.startswith("gop von"):
            return False
        return True

    if "von co phan" not in label:
        return False
    if "thang du" in label:
        return False
    requested_state = str(spec.get("share_state") or "generic")
    if requested_state == "issued" and "da phat hanh" not in label:
        return False
    if requested_state == "registered" and not any(
        marker in label for marker in ("dang ky", "duoc phep")
    ):
        return False
    return True


def _evidence_window(table: Mapping[str, Any], row_index: int) -> list[dict[str, Any]]:
    # The subsidiary helper uses the same canonical builder semantic-header
    # contract as the snapshot builder and preserves source coordinates.
    return HELPER.HELPER._evidence_window(table, row_index)


def _column_has_money_context(context: str) -> bool:
    return any(marker in context for marker in _MONEY_UNIT_MARKERS)


def _choose_value_column(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
    unit_multiplier: Decimal,
) -> tuple[int, Decimal, list[dict[str, Any]], str, dict[str, Any]] | None:
    evidence = _evidence_window(table, row_index)
    period_info_fn = getattr(BUILDER, "_semantic_column_period_info", None)
    context_fn = getattr(BUILDER, "semantic_column_context", None)
    if not callable(period_info_fn) or not callable(context_fn):
        return None

    if spec.get("metric_kind") == "direct_investment_contribution":
        # Some investment schedules put the as-of date in the surrounding
        # source prose rather than in the table header.  Accept that layout
        # only when the named target is present in the table context, the
        # requested year is present in an explicit as-of/ending phrase, and
        # exactly one VND amount column is available.
        table_context_parts: list[str] = []
        for field in ("context_before", "search_text"):
            value = table.get(field)
            if value:
                table_context_parts.append(str(value))
        trace = table.get("context_trace")
        if isinstance(trace, Mapping):
            for field in ("source_title", "summary"):
                value = trace.get(field)
                if value:
                    table_context_parts.append(str(value))
        table_context = _normalize(" ".join(table_context_parts))
        target = str(spec.get("target_phrase") or "").strip()
        if not target or target not in table_context:
            return None
        if str(spec["year"]) not in table_context or not any(
            cue in table_context
            for cue in ("tai thoi diem ngay", "tai ngay", "den ngay", "ket thuc")
        ):
            return None

        direct_candidates: list[
            tuple[int, Decimal, str, dict[str, Any], float]
        ] = []
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
            if "vnd" not in column_context:
                continue
            if any(
                marker in column_context
                for marker in ("phan tram", "ty le", "so luong", "co phieu")
            ):
                continue
            if "von gop" not in column_context:
                continue
            period_info = {
                "context": column_context,
                "years": {int(spec["year"])},
                "start": False,
                "end": True,
                "percentage": False,
                "source": "explicit_document_context_as_of_date",
            }
            score = 10.0
            if "vnd" in column_context:
                score += 2.0
            if "von gop" in column_context:
                score += 1.0
            direct_candidates.append(
                (int(column_index), raw_value, column_context, period_info, score)
            )
        if not direct_candidates:
            return None
        best_score = max(candidate[4] for candidate in direct_candidates)
        best = [
            candidate for candidate in direct_candidates if candidate[4] == best_score
        ]
        if len(best) != 1:
            return None
        column_index, raw_value, column_context, period_info, _ = best[0]
        return column_index, raw_value, evidence, column_context, period_info

    candidates: list[tuple[int, Decimal, str, dict[str, Any], float]] = []
    table_kind = _table_kind(table)
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
        period_info = period_info_fn(
            table,
            evidence,
            column_index=int(column_index),
            row_index=int(row_index),
        )
        # Some note layouts put the period/share-class header on the left
        # value column and put the adjacent money column under a bare ``VND``
        # header.  Propagate only an ending signal from the immediately
        # preceding column when that column has the target year and an
        # explicit ending date/label.  This cannot cross a distant period
        # block and does not copy any numeric value.
        inherited_ending = False
        if (
            column_index > 1
            and not period_info.get("end")
            and not period_info.get("start")
            and _column_has_money_context(column_context)
        ):
            previous_context = _normalize(
                context_fn(
                    table,
                    evidence,
                    column_index=int(column_index - 1),
                    row_index=int(row_index),
                )
            )
            previous_info = period_info_fn(
                table,
                evidence,
                column_index=int(column_index - 1),
                row_index=int(row_index),
            )
            previous_years = {
                int(value) for value in previous_info.get("years") or set()
            }
            if (
                previous_info.get("end")
                and int(spec["year"]) in previous_years
                and not any(marker in previous_context for marker in _PRIOR_MARKERS)
            ):
                period_info = dict(period_info)
                period_info["end"] = True
                period_info["years"] = previous_years
                inherited_ending = True
        years = {int(value) for value in period_info.get("years") or set()}
        if years and (
            int(spec["year"]) not in years
            or any(value != int(spec["year"]) for value in years)
        ):
            continue
        # A single amount column may be labelled by a merged header such as
        # ``31/12/2024 và 1/1/2024``.  It legitimately exposes both signals;
        # reject only a start-only/prior column when the question asks for an
        # ending balance.
        if (period_info.get("start") and not period_info.get("end")) or any(
            marker in column_context for marker in _PRIOR_MARKERS
        ):
            continue
        if any(marker in column_context for marker in _MOVEMENT_MARKERS):
            continue
        if any(marker in column_context for marker in _COUNT_MARKERS):
            continue
        if any(marker in column_context for marker in ("ma so", "thuyet minh")):
            continue
        if period_info.get("percentage") or any(
            marker in column_context for marker in ("phan tram", "ty le")
        ):
            continue

        is_ending = bool(period_info.get("end"))
        is_current = "nam nay" in column_context or "current" in column_context
        if not is_ending and not is_current:
            continue

        money_context = _column_has_money_context(column_context)
        # A balance/equity statement may carry VND only on the first page of
        # a continuation.  The caller has already proven the same-document
        # unit anchor; do not accept a unit-less amount in an arbitrary table.
        if not money_context and unit_multiplier is None:
            continue

        score = 0.0
        if is_ending:
            score += 6.0
        if inherited_ending:
            score += 0.5
        if is_current:
            score += 4.0
        if int(spec["year"]) in years:
            score += 3.0
        if money_context:
            score += 3.0
        if "vnd" in column_context:
            score += 1.0
        if "so cuoi nam" in column_context or "cuoi nam" in column_context:
            score += 1.0
        if table_kind == "balance_sheet":
            score += 0.25
        candidates.append(
            (int(column_index), raw_value, column_context, period_info, score)
        )

    if not candidates:
        return None
    best_score = max(candidate[4] for candidate in candidates)
    best = [candidate for candidate in candidates if candidate[4] == best_score]
    if len(best) != 1:
        return None
    column_index, raw_value, column_context, period_info, _ = best[0]
    return column_index, raw_value, evidence, column_context, period_info


def _document_unit_anchor(
    table: Mapping[str, Any], all_tables: list[Mapping[str, Any]]
) -> tuple[Decimal, str, str] | None:
    own_multiplier = HELPER.HELPER._explicit_source_multiplier(table)
    if own_multiplier is not None:
        return own_multiplier, "table", str(table.get("internal_table_uid") or "")

    document_id = _table_document_id(table)
    if not document_id:
        return None
    anchors: list[tuple[Decimal, str]] = []
    for other in all_tables:
        if _table_document_id(other) != document_id:
            continue
        multiplier = HELPER.HELPER._explicit_source_multiplier(other)
        if multiplier is not None:
            anchors.append((multiplier, str(other.get("internal_table_uid") or "")))
    multipliers = {value for value, _uid in anchors}
    if len(multipliers) != 1 or not anchors:
        return None
    multiplier = next(iter(multipliers))
    anchor_uid = sorted(uid for value, uid in anchors if value == multiplier)[0]
    return multiplier, "same_document", anchor_uid


def _candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
    all_tables: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not _table_is_safe(table, spec=spec):
        return None
    if spec.get("metric_kind") == "direct_investment_contribution":
        target = str(spec.get("target_phrase") or "").strip()
        source_context = _normalize(
            " ".join(
                str(table.get(field) or "")
                for field in ("context_before", "search_text")
            )
        )
        if not target or target not in source_context:
            _STATS["direct_investment_target_context_mismatch"] += 1
            return None
    if not _row_matches_metric(row, spec=spec):
        return None
    unit_contract = _document_unit_anchor(table, all_tables)
    if unit_contract is None:
        return None
    multiplier, unit_origin, unit_anchor_uid = unit_contract
    chosen = _choose_value_column(
        table,
        row_index=row_index,
        row=row,
        spec=spec,
        unit_multiplier=multiplier,
    )
    if chosen is None:
        return None
    column_index, raw_value, evidence, column_context, period_info = chosen
    answer = raw_value * multiplier / spec["output_divisor"]
    return {
        "answer": answer,
        "raw_value": raw_value,
        "source_multiplier": multiplier,
        "unit_origin": unit_origin,
        "unit_anchor_uid": unit_anchor_uid,
        "row_index": int(row_index),
        "column_index": int(column_index),
        "row_label": _row_label(row),
        "column_context": column_context,
        "period_info": period_info,
        "evidence": evidence,
        "table": table,
        "kind": _table_kind(table),
        "context": f"{_row_label(row)} {column_context}",
    }


def _candidate_rank(
    candidate: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[int, int, int, int, str]:
    table = candidate["table"]
    scope = str(BUILDER.table_reporting_scope(table) or "").lower()
    return (
        int(bool(spec.get("scope")) and scope == str(spec.get("scope") or "")),
        int(candidate.get("unit_origin") == "table"),
        int(candidate.get("kind") == "balance_sheet"),
        int(candidate.get("kind") == "equity_change_statement"),
        str(table.get("internal_table_uid") or ""),
    )


def _candidate_navigation_ranks(item: Mapping[str, Any]) -> dict[str, int]:
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
    item: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    all_tables = list(HELPER._tables_for_spec(tables_by_uid, spec))
    navigation_ranks = _candidate_navigation_ranks(item)
    if not navigation_ranks:
        return all_tables, all_tables
    hinted_tables = [
        table
        for table in all_tables
        if str(table.get("internal_table_uid") or "") in navigation_ranks
    ]
    if not hinted_tables:
        _STATS["candidate_navigation_hints_missing_from_asset"] += 1
        return all_tables, all_tables
    _STATS["candidate_navigation_filtered_questions"] += 1
    _STATS["candidate_navigation_tables_dropped"] += len(all_tables) - len(hinted_tables)
    # Unit anchors are allowed to come from the complete same-document cohort,
    # while answer cells remain bounded to the retrieved candidate boundary.
    return hinted_tables, all_tables


def _sources_and_selection(
    candidate: Mapping[str, Any], *, spec: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    table = candidate["table"]
    document_id = _table_document_id(table)
    uid = str(table.get("internal_table_uid") or "")
    source = {
        "raw_value_decimal": str(candidate["raw_value"]),
        "value": candidate["answer"],
        "role": "share_capital_value_source_cell",
        "document_id": document_id,
        "internal_table_uid": uid,
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "row_label": str(candidate.get("row_label") or ""),
        "column_context": candidate.get("column_context"),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "requested_output_divisor": str(spec["output_divisor"]),
        "unit_origin": candidate.get("unit_origin"),
        "unit_anchor_internal_table_uid": candidate.get("unit_anchor_uid"),
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    selection = {
        "score": 70.7,
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
        "program_family": "share_capital_value",
        "program_kind": candidate.get("kind"),
        "metric_kind": spec.get("metric_kind"),
        "share_state": spec.get("share_state"),
        "question_output_divisor": str(spec["output_divisor"]),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "unit_origin": candidate.get("unit_origin"),
        "unit_anchor_internal_table_uid": candidate.get("unit_anchor_uid"),
        "promotion_allowed": False,
    }
    if candidate.get("navigation_rank") is not None:
        source["candidate_navigation_rank"] = int(candidate["navigation_rank"])
        selection["candidate_navigation_rank"] = int(candidate["navigation_rank"])
    return [source], selection


def _resolve_share_capital_value(
    item: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    if not tables_by_uid:
        return None
    tables, all_tables = _tables_for_spec(tables_by_uid, spec, item=item)
    navigation_ranks = _candidate_navigation_ranks(item)
    def collect_candidates(
        search_tables: list[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        _STATS["candidate_table_count_total"] += len(search_tables)
        found: list[dict[str, Any]] = []
        for table in search_tables:
            if not _table_is_safe(table, spec=spec):
                _STATS["table_rejected_unsafe_kind"] += 1
                continue
            for row_index, row in enumerate(table.get("rows") or []):
                if not isinstance(row, list):
                    continue
                candidate = _candidate(
                    table,
                    row_index=row_index,
                    row=row,
                    spec=spec,
                    all_tables=all_tables,
                )
                if candidate is not None:
                    uid = str(table.get("internal_table_uid") or "")
                    if uid in navigation_ranks:
                        candidate["navigation_rank"] = navigation_ranks[uid]
                    found.append(candidate)
        return found

    candidates = collect_candidates(tables)
    if not candidates and tables is not all_tables:
        # The answer-lane candidate pool is intentionally small (usually
        # top-10), while the route asset is complete.  A family contract can
        # safely recover a source omitted by navigation only after every
        # bounded candidate fails the strict row/column/unit checks.  This is
        # still a source-cell replay, not a retrieval-rank answer.
        _STATS["candidate_navigation_fallback_to_complete_asset"] += 1
        candidates = collect_candidates(all_tables)

    if not candidates:
        _STATS["family_no_strict_candidate"] += 1
        _TRACE.append(
            {
                "question_id": spec.get("question_id"),
                "status": "REJECTED_NO_STRICT_SOURCE_CANDIDATE",
                "ticker": spec["ticker"],
                "year": spec["year"],
                "scope": spec.get("scope") or None,
                "metric_kind": spec["metric_kind"],
            }
        )
        return None

    answers = {candidate["answer"] for candidate in candidates}
    if len(answers) != 1:
        _STATS["rejected_conflicting_duplicate_answers"] += 1
        _TRACE.append(
            {
                "question_id": spec.get("question_id"),
                "status": "REJECTED_CONFLICTING_DUPLICATE_ANSWERS",
                "ticker": spec["ticker"],
                "year": spec["year"],
                "scope": spec.get("scope") or None,
                "metric_kind": spec["metric_kind"],
                "answers": sorted(str(value) for value in answers),
                "candidate_count": len(candidates),
            }
        )
        return None

    selected = sorted(
        candidates,
        key=lambda value: _candidate_rank(value, spec),
        reverse=True,
    )[0]
    sources, selection = _sources_and_selection(selected, spec=spec)
    answer = selected["answer"]
    _STATS["family_accepted"] += 1
    _STATS[f"accepted_{selected['kind']}"] += 1
    _STATS[f"accepted_unit_{selected['unit_origin']}"] += 1
    _TRACE.append(
        {
            "question_id": spec.get("question_id"),
            "status": "ACCEPTED",
            "ticker": spec["ticker"],
            "year": spec["year"],
            "scope": spec.get("scope") or None,
            "metric_kind": spec["metric_kind"],
            "share_state": spec.get("share_state"),
            "candidate_count": len(candidates),
            "answer": str(answer),
            "document_id": sources[0]["document_id"],
            "internal_table_uid": sources[0]["internal_table_uid"],
            "row_index": sources[0]["row_index"],
            "column_index": sources[0]["column_index"],
            "raw_value": sources[0]["raw_value_decimal"],
            "source_to_vnd_multiplier": sources[0]["source_to_vnd_multiplier"],
            "unit_origin": selected["unit_origin"],
            "unit_anchor_internal_table_uid": selected["unit_anchor_uid"],
            "selected_kind": selected["kind"],
            "row_label": selected["row_label"],
            "column_context": selected.get("column_context"),
            "candidate_navigation_rank": selected.get("navigation_rank"),
        }
    )
    return answer, sources, "float(df1.loc[0, 'value'])", SHARE_CAPITAL_VALUE_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    spec = _family_spec(item)
    if spec is not None:
        _STATS["family_seen"] += 1
        result = _resolve_share_capital_value(
            item,
            spec,
            tables_by_uid=tables_by_uid,
        )
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == SHARE_CAPITAL_VALUE_TIER:
        return 70.7
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _write_variant_metadata(output_dir: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["share_capital_value_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "route": SHARE_CAPITAL_VALUE_TIER,
        "contract": {
            "family": "direct_lookup",
            "one_ticker_and_one_report_year": True,
            "ending_period_required": True,
            "monetary_currency_word_required": True,
            "owner_contribution_or_share_capital_row_required": True,
            "transaction_gop_von_row_excluded": True,
            "treasury_count_percentage_and_comparison_families_excluded": True,
            "current_amount_column_required": True,
            "prior_and_movement_columns_rejected": True,
            "explicit_table_unit_or_same_document_unit_anchor_required": True,
            "complete_asset_fallback_when_navigation_boundary_has_no_contract_candidate": True,
            "conflicting_duplicate_answers_rejected": True,
            "question_id_allowlist": False,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "share_capital_value_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace_path = output_dir / "share_capital_value_trace_v1.jsonl"
    trace_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in _TRACE
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
