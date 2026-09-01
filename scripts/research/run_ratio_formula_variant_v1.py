#!/usr/bin/env python3
"""Run a guarded exact-cell ratio formula candidate lane.

The typed-plan compiler already describes a number of two-operand ``divide``
questions, but the canonical competition path often falls back to a single
semantic cell or to a broad ratio heuristic.  This adapter materializes only
the narrow shape that is already explicit in the frozen typed plan:

* one ticker, one report year and two distinct operands;
* a complete ``divide`` AST and a ratio-like question/unit;
* independently ranked source rows for numerator and denominator;
* the same exact report document/scope, with a same-table preference;
* nonzero denominator and exact current-table Decimal replay.

It is a candidate lane.  The canonical builder still owns proposal
verification, evidence writing, submission validation and authority policy.
No retrieval score or typed-plan metadata is used as a numeric answer.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


VARIANT_PROTOCOL = "vifinqa_exact_ratio_formula_v1"
RATIO_TIER = "program_exact_ratio_formula_v1"
ARG_MODULE_NAME = "_vifinqa_arg_extreme_helpers_for_ratio"


def _load_arg_helpers() -> Any:
    runner_path = (
        Path(__file__).resolve().parent / "run_arg_extreme_period_variant_v1.py"
    )
    spec = importlib.util.spec_from_file_location(ARG_MODULE_NAME, runner_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load shared full-corpus helpers: {runner_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ARG = _load_arg_helpers()
BUILDER = ARG.BUILDER
_ORIGINAL_PROGRAM_AWARE = BUILDER.program_aware_answer
_ORIGINAL_ROUTE_PRIORITY = BUILDER._route_priority

_TYPED_PLANS: dict[int, dict[str, Any]] = {}
_TRACE: list[dict[str, Any]] = []
_STATS: Counter[str] = Counter()

_RATIO_CUES = (
    "ty trong",
    "ty le",
    "ty so",
    "ty suat",
    "he so",
    "phan tram",
    "bao nhieu %",
    "bao nhieu lan",
)
_PERCENT_CUES = ("phan tram", "bao nhieu %", "%")
_REJECT_CUES = (
    "tang truong",
    "toc do tang",
    "tang %",
    "tang bao nhieu",
    "giam bao nhieu",
    "chuyen doi",
)
_UNIT_ALLOWED = {None, "percent", "times"}


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


def _typed_plan(item: Mapping[str, Any]) -> dict[str, Any] | None:
    question_id = _question_id(item)
    return _TYPED_PLANS.get(question_id) if question_id is not None else None


def _normal(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_metric(value: Any, *, ticker: str) -> list[str]:
    """Return bounded source-row hints from one typed operand."""

    text = _normal(BUILDER.normalize(value))
    if not text:
        return []
    text = re.sub(r"(?:19|20)\d{2}", " ", text)
    text = re.sub(r"\s+[—–-]\s+", " ", text)
    text = re.sub(
        r"\b(?:cuoi|dau)\s+(?:nam|ky)\b|\b(?:tai|den)\s+ngay\b",
        " ",
        text,
    )
    variants: list[str] = []
    pieces = re.split(r"\s*/\s*|\s+hoac\s+", text)
    for piece in pieces:
        piece = _normal(ARG._clean_with_existing_helper(piece, ticker=ticker))
        if not piece:
            continue
        for candidate in (piece, _normal(re.sub(r"\b(?:tong|so du)\s+", " ", piece))):
            candidate = _normal(candidate)
            if len(BUILDER.content_tokens(candidate)) >= 2 and candidate not in variants:
                variants.append(candidate)
    return variants[:8]


def _operand_metric_variants(
    operand: Mapping[str, Any], *, ticker: str
) -> list[str]:
    variants: list[str] = []
    for hint in operand.get("metric_hints") or []:
        for value in _clean_metric(hint, ticker=ticker):
            if value not in variants:
                variants.append(value)
    return variants[:8]


def _plan_operands(typed: Mapping[str, Any]) -> dict[str, Mapping[str, Any]] | None:
    operands = [value for value in typed.get("operands") or [] if isinstance(value, Mapping)]
    ast = typed.get("operation_ast") or {}
    args = ast.get("args")
    if (
        typed.get("decomposition_status") != "complete"
        or typed.get("effective_family") != "ratio_or_derived"
        or ast.get("op") != "divide"
        or not isinstance(args, list)
        or len(args) != 2
        or set(args) != {"numerator", "denominator"}
        or len(operands) != 2
    ):
        return None
    by_id = {str(value.get("operand_id") or ""): value for value in operands}
    if set(by_id) != {"numerator", "denominator"}:
        return None
    return by_id


def _ratio_contract(
    item: Mapping[str, Any], typed: Mapping[str, Any]
) -> tuple[dict[str, Mapping[str, Any]], str] | None:
    by_id = _plan_operands(typed)
    if by_id is None:
        return None
    unit = typed.get("requested_unit")
    if unit not in _UNIT_ALLOWED:
        return None
    question = BUILDER.normalize(item.get("question") or "")
    if not any(cue in question for cue in _RATIO_CUES):
        return None
    if any(cue in question for cue in _REJECT_CUES):
        return None
    ticker = str(typed.get("entities", [""])[0] if typed.get("entities") else "").strip().upper()
    if not ticker:
        ticker = str(by_id["numerator"].get("ticker") or "").strip().upper()
    if not ticker:
        return None
    entities = {
        str(operand.get("ticker") or operand.get("entity") or ticker).strip().upper()
        for operand in by_id.values()
    }
    if entities != {ticker}:
        return None
    years = {
        tuple(int(value) for value in operand.get("years") or [])
        for operand in by_id.values()
    }
    if len(years) != 1 or len(next(iter(years))) != 1:
        return None
    for operand in by_id.values():
        if not _operand_metric_variants(operand, ticker=ticker):
            return None
        contract = operand.get("grounding_contract")
        if not isinstance(contract, Mapping) or any(
            contract.get(key) is not True
            for key in (
                "exact_internal_table_uid",
                "exact_row_index",
                "exact_column_index",
                "exact_raw_cell",
                "canonical_header_required",
            )
        ):
            return None
    return by_id, ticker


def _scope_candidates(
    item: Mapping[str, Any], typed: Mapping[str, Any], ticker: str, year: int,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    index = ARG._table_index(tables_by_uid)
    explicit: list[str] = []
    for value in (
        typed.get("scope"),
        (item.get("question_plan") or {}).get("scope"),
        (item.get("question_plan") or {}).get("reporting_scope"),
    ):
        scope = str(value or "").strip().lower()
        if scope in {"separate", "consolidated", "aggregated", "unknown"}:
            explicit.append(scope)
    question_scope = ARG._explicit_question_scope(str(item.get("question") or ""))
    if question_scope:
        explicit.append(question_scope)
    preferred = explicit[0] if explicit else ARG._review_scope_preference(item)
    ordered = [preferred, *[value for value in ("consolidated", "separate", "aggregated", "unknown") if value != preferred]]
    return [scope for scope in ordered if index.get((ticker, year, scope))]


def _same_document(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return str(left.get("document_id") or "").removesuffix(".txt") == str(
        right.get("document_id") or ""
    ).removesuffix(".txt")


def _pair_quality(
    numerator: Mapping[str, Any], denominator: Mapping[str, Any]
) -> tuple[float, float, float, float, float, str, int, int]:
    same_table = float(
        str(numerator.get("internal_table_uid") or "")
        == str(denominator.get("internal_table_uid") or "")
    )
    numerator_score = float(numerator.get("score") or 0.0)
    denominator_score = float(denominator.get("score") or 0.0)
    return (
        same_table,
        min(numerator_score, denominator_score),
        numerator_score + denominator_score,
        float(numerator.get("candidate_rank") or 999) * -1.0,
        float(denominator.get("candidate_rank") or 999) * -1.0,
        str(numerator.get("internal_table_uid") or ""),
        int(numerator.get("row_index") or -1),
        int(denominator.get("row_index") or -1),
    )


def _formula_query(*, percent: bool) -> str:
    expression = (
        "df1.loc[df1.operand_role=='numerator','operand_value'].iloc[0] / "
        "df1.loc[df1.operand_role=='denominator','operand_value'].iloc[0]"
    )
    if percent:
        expression = f"({expression}) * 100"
    return f"float({expression})"


def _resolve_ratio(
    item: Mapping[str, Any],
    typed: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    contract = _ratio_contract(item, typed)
    if contract is None:
        _STATS["ineligible_contract"] += 1
        return None
    by_id, ticker = contract
    if tables_by_uid is None:
        _STATS["tables_missing"] += 1
        return None
    year = int(next(iter(tuple(int(value) for value in by_id["numerator"].get("years") or []))))
    scope_candidates = _scope_candidates(item, typed, ticker, year, tables_by_uid)
    if not scope_candidates:
        _STATS["scope_coverage_missing"] += 1
        _TRACE.append(
            {
                "question_id": _question_id(item),
                "status": "REJECTED_SCOPE_COVERAGE_MISSING",
                "ticker": ticker,
                "year": year,
            }
        )
        return None

    best: tuple[tuple[Any, ...], dict[str, Any], dict[str, Any], str, str, str] | None = None
    for scope_index, scope in enumerate(scope_candidates):
        table_index = ARG._table_index(tables_by_uid)
        year_tables = table_index.get((ticker, year, scope), [])
        numerator_rows: list[dict[str, Any]] = []
        denominator_rows: list[dict[str, Any]] = []
        numerator_metric = ""
        denominator_metric = ""
        for metric in _operand_metric_variants(by_id["numerator"], ticker=ticker):
            rows = ARG._select_year_rows(
                item=item,
                ticker=ticker,
                year=year,
                scope=scope,
                metric=metric,
                tables_by_uid=tables_by_uid,
                tables=year_tables,
            )
            if rows and not numerator_rows:
                numerator_rows = rows
                numerator_metric = metric
        for metric in _operand_metric_variants(by_id["denominator"], ticker=ticker):
            rows = ARG._select_year_rows(
                item=item,
                ticker=ticker,
                year=year,
                scope=scope,
                metric=metric,
                tables_by_uid=tables_by_uid,
                tables=year_tables,
            )
            if rows and not denominator_rows:
                denominator_rows = rows
                denominator_metric = metric
        if not numerator_rows or not denominator_rows:
            _STATS["operand_row_window_empty"] += 1
            continue
        for numerator in numerator_rows[:30]:
            for denominator in denominator_rows[:30]:
                if not _same_document(numerator, denominator):
                    continue
                if (
                    str(numerator.get("internal_table_uid") or "")
                    == str(denominator.get("internal_table_uid") or "")
                    and int(numerator.get("row_index") or -1)
                    == int(denominator.get("row_index") or -1)
                ):
                    continue
                denominator_value = denominator.get("value")
                if denominator_value is None or denominator_value == 0:
                    continue
                quality = _pair_quality(numerator, denominator)
                # Prefer same-table, stronger independent row scores, and the
                # preferred scope.  The final scope term is below all source
                # quality terms and is only a stable tie-breaker.
                ranked = (*quality, -scope_index)
                if best is None or ranked > best[0]:
                    best = (
                        ranked,
                        dict(numerator),
                        dict(denominator),
                        scope,
                        numerator_metric,
                        denominator_metric,
                    )
    if best is None:
        _STATS["pair_rejected_no_same_document"] += 1
        _TRACE.append(
            {
                "question_id": _question_id(item),
                "status": "REJECTED_NO_SAME_DOCUMENT_PAIR",
                "ticker": ticker,
                "year": year,
                "scope_candidates": scope_candidates,
            }
        )
        return None

    _, numerator, denominator, scope, numerator_metric, denominator_metric = best
    numerator_value = numerator["value"]
    denominator_value = denominator["value"]
    percent = (
        typed.get("requested_unit") == "percent"
        or any(cue in BUILDER.normalize(item.get("question") or "") for cue in _PERCENT_CUES)
    )
    multiplier = Decimal(100) if percent else Decimal(1)
    answer = numerator_value / denominator_value * multiplier
    numerator["role"] = "numerator"
    denominator["role"] = "denominator"
    for selection, metric in (
        (numerator, numerator_metric),
        (denominator, denominator_metric),
    ):
        selection["ratio_formula_protocol"] = VARIANT_PROTOCOL
        selection["ratio_formula_scope"] = scope
        selection["ratio_formula_year"] = year
        selection["ratio_formula_metric"] = metric
    query = _formula_query(percent=percent)
    _STATS["accepted"] += 1
    _TRACE.append(
        {
            "question_id": _question_id(item),
            "status": "ACCEPTED",
            "ticker": ticker,
            "year": year,
            "scope": scope,
            "percent": percent,
            "numerator_metric": numerator_metric,
            "denominator_metric": denominator_metric,
            "numerator_value": str(numerator_value),
            "denominator_value": str(denominator_value),
            "answer": str(answer),
            "same_table": numerator.get("internal_table_uid") == denominator.get("internal_table_uid"),
            "sources": [
                {
                    "role": selection.get("role"),
                    "document_id": selection.get("document_id"),
                    "internal_table_uid": selection.get("internal_table_uid"),
                    "row_index": selection.get("row_index"),
                    "column_index": selection.get("column_index"),
                    "row_label": selection.get("row_label"),
                    "value": str(selection.get("value")),
                }
                for selection in (numerator, denominator)
            ],
        }
    )
    return answer, [numerator, denominator], query, RATIO_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    typed = _typed_plan(item)
    if typed is not None:
        ast = typed.get("operation_ast") or {}
        if ast.get("op") == "divide":
            _STATS["typed_divide_seen"] += 1
            result = _resolve_ratio(item, typed, tables_by_uid=tables_by_uid)
            if result is not None:
                return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == RATIO_TIER:
        return 70.5
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _load_typed_plans(path: Path) -> dict[int, dict[str, Any]]:
    plans: dict[int, dict[str, Any]] = {}
    for record in BUILDER.read_jsonl(path):
        plans[int(record["question_id"])] = record
    if len(plans) != 1012:
        raise ValueError(f"typed-plan count={len(plans)} expected 1012")
    return plans


def _write_variant_metadata(output_dir: Path, *, typed_plans_path: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["exact_ratio_formula_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "typed_plans_path": str(typed_plans_path),
        "typed_plan_count": len(_TYPED_PLANS),
        "contract": {
            "decomposition_status": "complete",
            "effective_family": "ratio_or_derived",
            "operation": "divide",
            "one_ticker_one_report_year": True,
            "same_document_required": True,
            "same_table_preferred": True,
            "nonzero_denominator_required": True,
            "exact_current_table_decimal_replay": True,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "exact_ratio_formula_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "exact_ratio_formula_trace_v1.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in _TRACE
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = BUILDER.configure_parser(argparse.ArgumentParser())
    parser.add_argument(
        "--typed-plans",
        type=Path,
        required=True,
        help="Frozen typed_operand_plans_v1.jsonl used by the exact ratio gate",
    )
    args = parser.parse_args()
    global _TYPED_PLANS
    _TYPED_PLANS = _load_typed_plans(args.typed_plans)
    BUILDER.program_aware_answer = _patched_program_aware
    BUILDER._route_priority = _patched_route_priority
    BUILDER.build(args)
    _write_variant_metadata(args.output, typed_plans_path=args.typed_plans)
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
