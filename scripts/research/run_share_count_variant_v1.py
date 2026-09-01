#!/usr/bin/env python3
"""Run a strict source-cell ablation for share-count state questions.

The generic direct resolver can select an ownership row, a monetary share
capital column, a treasury-share row, or a prior-period count when a note
contains several share states.  This adapter binds a reusable family:
one issuer/year, an ending-period share-count question, a state-aware share
row, an explicitly count-oriented column, and Decimal replay from the current
structured asset.

This is a research candidate lane.  It does not use question IDs as rules,
does not read an answer from retrieval metadata, and cannot authorize strict
verification or an official score.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


VARIANT_PROTOCOL = "vifinqa_share_count_state_strict_v1"
SHARE_COUNT_TIER = "program_share_count_state_v1"
HELPER_NAME = "_vifinqa_named_compensation_helper_for_share_count"


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
    "financial_note",
    "financial_note_detail",
    "financial_data_schedule",
}
_ENDING_CUES = (
    "cuoi nam",
    "cuoi ky",
    "den ngay",
    "tai ngay",
    "ket thuc",
    "31/12",
    "31 thang 12",
)
_START_MARKERS = (
    "dau nam",
    "dau ky",
    "so dau nam",
    "so dau ky",
    "nam truoc",
    "ky truoc",
)
_END_MARKERS = (
    "cuoi nam",
    "cuoi ky",
    "so cuoi nam",
    "so cuoi ky",
)
_COUNT_MARKERS = (
    "so luong",
    "co phieu",
    "co phan",
    "shares",
)
_MONEY_MARKERS = (
    "vnd",
    "dong",
    "gia tri",
    "menh gia",
    "ty",
    "trieu",
    "nghin",
    "million",
    "billion",
)
_BAD_ROW_MARKERS = (
    "co phieu quy",
    "uu dai",
    "binh quan",
    "lai co ban",
    "lai suy giam",
    "loi nhuan",
    "ty le",
    "phan tram",
    "co tuc",
    "gia tri",
    "von co phan",
    "thang du von",
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


def _family_spec(item: Mapping[str, Any]) -> dict[str, Any] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "direct_lookup":
        return None
    question = str(item.get("question") or "")
    question_norm = _normalize(question)

    if not any(marker in question_norm for marker in _COUNT_MARKERS):
        return None
    if not any(marker in question_norm for marker in _ENDING_CUES):
        return None
    # Monetary share-capital, EPS, ownership, and average-count questions are
    # separate families.  Keeping them out is a semantic safety contract, not
    # a question-ID exception.
    if any(
        marker in question_norm
        for marker in (
            "vnd",
            "dong",
            "ty dong",
            "trieu dong",
            "nghin dong",
            "gia tri",
            "von co phan",
            "von gop",
            "lai co ban",
            "lai tren moi co phieu",
            "lai suy giam",
            "ty le",
            "phan tram",
            "binh quan",
            "co tuc",
        )
    ):
        return None
    if "so luong" not in question_norm and not any(
        marker in question_norm for marker in ("tong so co phan", "tong so luong co phan")
    ):
        return None

    state = "total"
    if "dang luu hanh" in question_norm:
        state = "outstanding"
    elif "da phat hanh" in question_norm or "phat hanh" in question_norm:
        state = "issued"
    elif "dang ky" in question_norm or "duoc phep phat hanh" in question_norm:
        state = "registered"

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
        "state": state,
        "metric": metric or question,
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
    return _normalize(raw).replace("_", " ")


def _table_is_safe(table: Mapping[str, Any]) -> bool:
    return _table_kind(table).replace(" ", "_") in _SAFE_TABLE_KINDS


def _table_context(table: Mapping[str, Any]) -> str:
    values: list[str] = []
    trace = table.get("context_trace") or {}
    if isinstance(trace, Mapping):
        for key in ("source_title", "summary", "topic", "period_labels", "unit_labels"):
            value = trace.get(key)
            if isinstance(value, Mapping):
                values.extend(str(part or "") for part in value.values())
            elif isinstance(value, (list, tuple)):
                values.extend(str(part or "") for part in value)
            else:
                values.append(str(value or ""))
    for field in ("headers", "column_labels", "table_section"):
        value = table.get(field) or []
        if isinstance(value, Mapping):
            values.extend(str(part or "") for part in value.values())
        elif isinstance(value, (list, tuple)):
            values.extend(str(part or "") for part in value)
        elif value:
            values.append(str(value))
    return _normalize(" ".join(values))


def _evidence_window(table: Mapping[str, Any], row_index: int) -> list[dict[str, Any]]:
    """Build value-preserving evidence and expose contiguous text headers.

    Some materialized tables do not mark their second header row in
    header_row_indices.  Adding it as an index -1 evidence row lets the
    canonical semantic column helper bind a year and share class without
    copying numeric data into the header context.
    """

    evidence = HELPER.HELPER._evidence_window(table, row_index)
    rows = table.get("rows") or []
    for index in range(min(int(row_index), 8)):
        row = rows[index]
        if not isinstance(row, list):
            continue
        numeric_values = [
            BUILDER.parse_decimal(cell)
            for cell in row
            if BUILDER.parse_decimal(cell) is not None
        ]
        # A year-only header such as "2021 | | 2020" is numeric to the
        # generic parser but is still a semantic header.  Stop at the first
        # ordinary data row while retaining that narrow calendar-year shape.
        if numeric_values and not all(
            value == value.to_integral_value()
            and Decimal("1900") <= value <= Decimal("2100")
            for value in numeric_values
        ):
            break
        evidence.append({"index": -1, "row": row})
    return evidence


def _nearest_parent_state(table: Mapping[str, Any], row_index: int) -> str | None:
    """Read only the nearest explicit state row for a nested common-share row."""

    rows = table.get("rows") or []
    for index in range(int(row_index) - 1, max(-1, int(row_index) - 3), -1):
        row = rows[index]
        if not isinstance(row, list):
            continue
        label = _normalize(_row_label(row))
        if not label:
            continue
        if "co phieu quy" in label:
            return "treasury"
        if "dang luu hanh" in label:
            return "outstanding"
        if "da phat hanh" in label:
            return "issued"
        if "dang ky" in label or "duoc phep phat hanh" in label:
            return "registered"
        # A nested common/preferred row may sit below another descriptive row.
        # Stop at an unrelated labelled section to avoid borrowing state from
        # a different share block.
        if not any(marker in label for marker in ("pho thong", "uu dai")):
            break
    return None


def _row_state(
    table: Mapping[str, Any], *, row_index: int, row: list[Any]
) -> tuple[str, bool] | None:
    label = _normalize(_row_label(row))
    if not label or any(marker in label for marker in _BAD_ROW_MARKERS):
        return None
    if not any(marker in label for marker in ("co phieu", "co phan", "shares")):
        return None
    if "co phieu quy" in label:
        return None
    if "dang luu hanh" in label:
        return "outstanding", True
    if "da phat hanh" in label and "dang ky" not in label:
        return "issued", True
    if "dang ky" in label or "duoc phep phat hanh" in label:
        return "registered", True
    parent = _nearest_parent_state(table, row_index)
    if parent in {"outstanding", "issued", "registered"}:
        return parent, False
    if "pho thong" in label:
        return "total", False
    return "total", False


def _row_matches_state(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    requested_state: str,
) -> tuple[str, bool] | None:
    observed = _row_state(table, row_index=row_index, row=row)
    if observed is None:
        return None
    state, direct = observed
    if requested_state == "total":
        return observed
    if state == requested_state:
        return observed
    return None


def _choose_share_column(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> tuple[int, Decimal, list[dict[str, Any]], str, dict[str, Any]] | None:
    evidence = _evidence_window(table, row_index)
    period_info_fn = getattr(BUILDER, "_semantic_column_period_info", None)
    context_fn = getattr(BUILDER, "semantic_column_context", None)
    if not callable(period_info_fn) or not callable(context_fn):
        return None
    row_label = _normalize(_row_label(row))
    table_context = _table_context(table)
    candidates: list[tuple[int, Decimal, str, dict[str, Any], float]] = []
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
        years = {int(value) for value in period_info.get("years") or set()}
        if years and (
            int(spec["year"]) not in years
            or any(value != int(spec["year"]) for value in years)
        ):
            continue
        if period_info.get("start") or any(
            marker in column_context for marker in _START_MARKERS
        ):
            continue
        if not (
            period_info.get("end")
            or any(marker in column_context for marker in _END_MARKERS)
            or "nam nay" in column_context
            or int(spec["year"]) in years
        ):
            continue
        if any(marker in column_context for marker in _MONEY_MARKERS):
            continue
        if any(marker in column_context for marker in ("%", "phan tram", "ty le")):
            continue
        if any(marker in column_context for marker in ("ma so", "thuyet minh")):
            continue
        if not any(
            marker in f"{row_label} {column_context} {table_context}"
            for marker in ("co phieu", "co phan", "shares")
        ):
            continue
        if "so luong" not in f"{row_label} {column_context}" and not any(
            marker in row_label for marker in ("co phieu", "co phan", "shares")
        ):
            continue
        score = 0.0
        if int(spec["year"]) in years:
            score += 5.0
        if period_info.get("end") or "nam nay" in column_context:
            score += 4.0
        if "so luong" in column_context:
            score += 3.0
        if "co phieu" in column_context or "co phan" in column_context:
            score += 2.0
        if "pho thong" in column_context:
            score += 0.5
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


def _candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not _table_is_safe(table):
        return None
    observed_state = _row_matches_state(
        table,
        row_index=row_index,
        row=row,
        requested_state=str(spec["state"]),
    )
    if observed_state is None:
        return None
    chosen = _choose_share_column(
        table,
        row_index=row_index,
        row=row,
        spec=spec,
    )
    if chosen is None:
        return None
    column_index, raw_value, evidence, column_context, period_info = chosen
    return {
        "answer": raw_value,
        "raw_value": raw_value,
        "source_multiplier": Decimal(1),
        "row_index": int(row_index),
        "column_index": int(column_index),
        "row_label": _row_label(row),
        "column_context": column_context,
        "period_info": period_info,
        "evidence": evidence,
        "table": table,
        "kind": _table_kind(table).replace(" ", "_"),
        "observed_state": observed_state[0],
        "direct_state_match": bool(observed_state[1]),
        "context": f"{_row_label(row)} {column_context}",
    }


def _candidate_rank(
    candidate: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[int, int, int, str]:
    table = candidate["table"]
    scope = str(BUILDER.table_reporting_scope(table) or "").lower()
    requested_state = str(spec["state"])
    direct = int(bool(candidate.get("direct_state_match")))
    state_match = int(candidate.get("observed_state") == requested_state)
    if requested_state == "total":
        state_match = int(candidate.get("observed_state") in {"outstanding", "issued", "registered"})
    return (
        int(bool(spec.get("scope")) and scope == str(spec.get("scope") or "")),
        direct + state_match,
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
        "role": "share_count_source_cell",
        "document_id": document_id,
        "internal_table_uid": uid,
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "row_label": str(candidate.get("row_label") or ""),
        "column_context": candidate.get("column_context"),
        "source_to_vnd_multiplier": "1",
        "requested_output_divisor": "1",
        "share_state": str(candidate.get("observed_state") or ""),
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    selection = {
        "score": 70.8,
        "value": candidate["answer"],
        "raw_value": candidate["raw_value"],
        "source_multiplier": Decimal(1),
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "row_label": str(candidate.get("row_label") or ""),
        "column_context": candidate.get("column_context"),
        "document_id": document_id,
        "internal_table_uid": uid,
        "candidate_rank": 0,
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "program_family": "share_count_state",
        "program_kind": candidate.get("kind"),
        "share_state": str(candidate.get("observed_state") or ""),
        "question_output_divisor": "1",
        "source_to_vnd_multiplier": "1",
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


def _resolve_share_count(
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
            candidate = _candidate(
                table,
                row_index=row_index,
                row=row,
                spec=spec,
            )
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
                "share_state": spec["state"],
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
                    "share_state": spec["state"],
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
    _TRACE.append(
        {
            "question_id": spec.get("question_id"),
            "status": "ACCEPTED",
            "ticker": spec["ticker"],
            "year": spec["year"],
            "scope": spec.get("scope") or None,
            "share_state": spec["state"],
            "candidate_count": len(candidates),
            "answer": str(answer),
            "document_id": sources[0]["document_id"],
            "internal_table_uid": sources[0]["internal_table_uid"],
            "row_index": sources[0]["row_index"],
            "column_index": sources[0]["column_index"],
            "raw_value": sources[0]["raw_value_decimal"],
            "selected_kind": selected["kind"],
            "row_label": selected["row_label"],
            "column_context": selected.get("column_context"),
            "candidate_navigation_rank": selected.get("navigation_rank"),
            "candidate_navigation_tiebreak": bool(
                selected.get("navigation_tiebreak")
            ),
        }
    )
    return answer, sources, "float(df1.loc[0, 'value'])", SHARE_COUNT_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    spec = _family_spec(item)
    if spec is not None:
        _STATS["family_seen"] += 1
        result = _resolve_share_count(item, spec, tables_by_uid=tables_by_uid)
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == SHARE_COUNT_TIER:
        return 70.8
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _write_variant_metadata(output_dir: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["share_count_state_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "route": SHARE_COUNT_TIER,
        "contract": {
            "family": "direct_lookup",
            "one_ticker_and_one_report_year": True,
            "ending_period_required": True,
            "share_count_wording_required": True,
            "monetary_share_capital_and_eps_excluded": True,
            "share_state_row_required": True,
            "count_column_required": True,
            "money_percentage_and_prior_period_columns_rejected": True,
            "financial_note_or_schedule_only": True,
            "conflicting_duplicate_answers_rejected": True,
            "question_id_allowlist": False,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "share_count_state_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace_path = output_dir / "share_count_state_trace_v1.jsonl"
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
