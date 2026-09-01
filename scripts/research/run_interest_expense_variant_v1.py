#!/usr/bin/env python3
"""Run a strict source-cell ablation for two interest-expense patterns.

The generic direct resolver currently misses a small but high-confidence
family.  One question asks for a closing ``lãi vay phải trả`` balance and one
asks for ``chi phí lãi vay`` paid to a named related bank.  Both values are
present in related-party schedules with an explicit report-period header, but
the ordinary semantic route can either miss the row or fall back to zero.

This adapter is intentionally narrower than the words used in the questions:
it does not take over generic interest-expense questions.  It requires a
direct-lookup plan, one ticker, one report year, a source-declared unit, a
related-party table, and either an exact payable row or a counterparty row in
the same local section.  Conflicting source values are rejected.  A
successful run is an authorized best-effort candidate lane, not a strict
VERIFIED release or an official score.
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


VARIANT_PROTOCOL = "vifinqa_interest_expense_strict_v1"
INTEREST_TIER = "program_interest_expense_v1"
HELPER_NAME = "_vifinqa_named_compensation_helper_for_interest_expense"


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

_SAFE_TABLE_KIND = "related_party_schedule"
_PAYABLE_LABEL = "lai vay phai tra"
_INTEREST_EXPENSE_LABEL = "chi phi lai vay"
_COUNTERPARTY_STOP_WORDS = {
    "cua",
    "cong",
    "ty",
    "me",
    "nam",
    "la",
    "bao",
    "nhieu",
    "ty",
}


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


def _counterparty_from_question(question_norm: str) -> str:
    """Extract the counterparty between the metric and ``của PLX``.

    Requiring text before the scope-owning ``của`` deliberately excludes a
    generic question such as Q66.  It also keeps this route from guessing a
    counterparty from a retrieved table.
    """

    marker = "chi phi lai vay"
    if marker not in question_norm:
        return ""
    suffix = question_norm.split(marker, 1)[1].strip()
    if suffix.startswith("cua "):
        return ""
    before_owner = re.split(r"\s+cua\b", suffix, maxsplit=1)[0].strip()
    if not before_owner:
        return ""
    tokens = [token for token in before_owner.split() if token]
    if len(tokens) < 2 or all(token in _COUNTERPARTY_STOP_WORDS for token in tokens):
        return ""
    return re.sub(r"\s+", " ", before_owner)


def _family_spec(item: Mapping[str, Any]) -> dict[str, Any] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "direct_lookup":
        return None
    question = str(item.get("question") or "")
    question_norm = _normalize(question)
    ticker = _single_ticker(plan)
    year = _single_year(plan)
    if not ticker or year is None:
        return None

    raw_scope = str(plan.get("scope") or plan.get("reporting_scope") or "")
    scope = raw_scope.strip().lower()
    if scope not in {"", "separate", "consolidated"}:
        return None

    if "lai vay phai tra" in question_norm and any(
        cue in question_norm
        for cue in ("den ngay", "cuoi nam", "31/12", "so du")
    ):
        mode = "payable_balance"
        counterparty = ""
    elif "chi phi lai vay" in question_norm:
        counterparty = _counterparty_from_question(question_norm)
        if not counterparty:
            # Generic interest-expense questions may have conflicting
            # separate/consolidated values; this route must leave them alone.
            return None
        mode = "counterparty_expense"
    else:
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
        "mode": mode,
        "counterparty": counterparty,
        "metric": metric or question,
        "output_divisor": BUILDER.requested_divisor(question),
    }


def _table_kind(table: Mapping[str, Any]) -> str:
    return HELPER._table_kind(table).replace(" ", "_")


def _table_is_safe(table: Mapping[str, Any]) -> bool:
    return _table_kind(table) == _SAFE_TABLE_KIND


def _table_multiplier(table: Mapping[str, Any]) -> Decimal | None:
    return HELPER._table_multiplier(table)


def _row_has_numeric_value(row: list[Any]) -> bool:
    return any(BUILDER.parse_decimal(cell) is not None for cell in row[1:])


def _counterparty_anchor(
    table: Mapping[str, Any],
    *,
    row_index: int,
    counterparty: str,
) -> tuple[int, str] | None:
    """Find a blank counterparty header in the same nearby schedule section."""

    rows = table.get("rows") or []
    start = max(0, row_index - 8)
    for index in range(row_index - 1, start - 1, -1):
        row = rows[index]
        if not isinstance(row, list):
            continue
        label = _normalize(_row_label(row))
        if counterparty in label and not _row_has_numeric_value(row):
            return index, _row_label(row)
        # A nonnumeric unrelated heading marks a different local section.  A
        # numeric transaction row is allowed between the header and target.
        if label and not _row_has_numeric_value(row):
            return None
    return None


def _candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not _table_is_safe(table):
        return None
    label = _normalize(_row_label(row))
    anchor_index: int | None = None
    anchor_label = ""
    if spec["mode"] == "payable_balance":
        if label != _PAYABLE_LABEL:
            return None
    else:
        if label != _INTEREST_EXPENSE_LABEL:
            return None
        anchor = _counterparty_anchor(
            table,
            row_index=row_index,
            counterparty=str(spec["counterparty"]),
        )
        if anchor is None:
            return None
        anchor_index, anchor_label = anchor

    chosen = HELPER.HELPER._choose_cell(
        table,
        row_index=row_index,
        row=row,
        spec=spec,
    )
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
        "kind": spec["mode"],
        "anchor_index": anchor_index,
        "anchor_label": anchor_label,
    }


def _tables_for_spec(
    tables_by_uid: Mapping[str, Mapping[str, Any]], spec: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    return HELPER._tables_for_spec(tables_by_uid, spec)


def _candidate_rank(
    candidate: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[int, int, str]:
    table = candidate["table"]
    scope = str(BUILDER.table_reporting_scope(table) or "").lower()
    return (
        int(bool(spec.get("scope")) and scope == str(spec.get("scope") or "")),
        int(candidate.get("kind") == "payable_balance"),
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
        "role": "interest_expense_source_cell",
        "document_id": document_id,
        "internal_table_uid": uid,
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "row_label": str(candidate.get("row_label") or ""),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "requested_output_divisor": str(spec["output_divisor"]),
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    selection = {
        "score": 70.6,
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
        "program_family": "interest_expense",
        "program_kind": candidate.get("kind"),
        "counterparty": spec.get("counterparty") or None,
        "question_output_divisor": str(spec["output_divisor"]),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "promotion_allowed": False,
    }
    return [source], selection


def _resolve_interest_expense(
    item: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    if not tables_by_uid:
        return None
    tables = _tables_for_spec(tables_by_uid, spec)
    _STATS["candidate_table_count_total"] += len(tables)
    candidates: list[dict[str, Any]] = []
    for table in tables:
        if not _table_is_safe(table):
            _STATS["table_rejected_non_related_party_schedule"] += 1
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
                "mode": spec["mode"],
                "counterparty": spec.get("counterparty") or None,
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
                "mode": spec["mode"],
                "counterparty": spec.get("counterparty") or None,
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
            "mode": spec["mode"],
            "counterparty": spec.get("counterparty") or None,
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
            "anchor_index": selected.get("anchor_index"),
            "anchor_label": selected.get("anchor_label") or None,
            "column_context": selected.get("column_context"),
        }
    )
    return answer, sources, "float(df1.loc[0, 'value'])", INTEREST_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    spec = _family_spec(item)
    if spec is not None:
        _STATS["family_seen"] += 1
        result = _resolve_interest_expense(
            item,
            spec,
            tables_by_uid=tables_by_uid,
        )
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == INTEREST_TIER:
        return 70.6
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _write_variant_metadata(output_dir: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["interest_expense_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "route": INTEREST_TIER,
        "contract": {
            "family": "direct_lookup",
            "one_ticker_and_one_report_year": True,
            "supported_modes": ["payable_balance", "counterparty_expense"],
            "related_party_schedule_only": True,
            "exact_payable_row_required": True,
            "counterparty_header_same_local_section_required": True,
            "current_period_column_required": True,
            "declared_source_unit_required": True,
            "conflicting_duplicate_answers_rejected": True,
            "generic_unscoped_interest_expense_not_taken_over": True,
            "question_id_allowlist": False,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "interest_expense_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace_path = output_dir / "interest_expense_trace_v1.jsonl"
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
