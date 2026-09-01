#!/usr/bin/env python3
"""Run a strict source-cell ablation for operating-lease receipt totals.

Several direct questions ask for the total minimum rent/operating-lease
commitment of an issuer acting as lessor.  The review ranker can confuse that
table with a nearby lessee schedule (future rent payable), especially when
both tables share the same ``TỔNG CỘNG`` row shape.  This adapter binds the
answer to the source context: the table must explicitly describe the issuer
as ``cho thuê`` and the requested total must be a current-period total row.

The route is family-based and fail-closed.  It requires one ticker, one year,
an end-period direct-lookup plan, a financial-note or financial-data schedule,
an explicit source unit, a lessor-context marker, and an exact total row.  It
does not infer a missing unit from the question or from another statement.
Unscoped duplicates are accepted only when their Decimal answers agree.  The
output remains an authorized best-effort candidate lane, not a strict
VERIFIED release or an official score.
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


VARIANT_PROTOCOL = "vifinqa_operating_lease_total_strict_v1"
OPERATING_LEASE_TOTAL_TIER = "program_operating_lease_total_v1"
HELPER_NAME = "_vifinqa_named_compensation_helper_for_operating_lease_total"


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

_SAFE_TABLE_KINDS = {"financial_note", "financial_data_schedule"}
_TOTAL_LABELS = {"tong cong", "cong", "tong"}
_LESSOR_MARKERS = (
    "cam ket cho thue hoat dong",
    "tai san cho thue hoat dong",
)
_LESSEE_MARKERS = (
    "cam ket thue hoat dong",
    "thue dat",
    "tien thue phai tra",
    "khoan tien thue phai tra",
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
    if "thue hoat dong" not in question_norm:
        return None
    if not any(
        cue in question_norm
        for cue in (
            "cam ket",
            "tien thue toi thieu",
            "khoan tien thue toi thieu",
        )
    ):
        return None
    if "phai tra" in question_norm:
        # This route is for the lessor/receipt direction only.
        return None
    if not any(
        cue in question_norm
        for cue in ("tong", "toan bo", "tong so tien")
    ):
        return None
    if not any(
        cue in question_norm
        for cue in ("cuoi nam", "cuoi ky", "den ngay", "tai ngay", "ket thuc", "31/12")
    ):
        return None

    ticker = _single_ticker(plan)
    year = _single_year(plan)
    if not ticker or year is None:
        return None
    raw_scope = str(plan.get("scope") or plan.get("reporting_scope") or "")
    scope = raw_scope.strip().lower()
    if scope not in {"", "separate", "consolidated", "aggregated"}:
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
    trace = table.get("context_trace") or {}
    values: list[str] = []
    if isinstance(trace, Mapping):
        values.extend(str(trace.get(key) or "") for key in ("source_title", "summary"))
        unit_labels = trace.get("unit_labels") or []
        if isinstance(unit_labels, list):
            values.extend(str(value or "") for value in unit_labels)
    values.extend(str(value or "") for value in table.get("headers") or [])
    values.extend(str(value or "") for value in table.get("row_paths") or [])
    return _normalize(" ".join(values))


def _is_lessor_lease_table(table: Mapping[str, Any]) -> bool:
    context = _table_context(table)
    if not any(marker in context for marker in _LESSOR_MARKERS):
        return False
    # The exact lessor marker must not be overridden by a source context that
    # explicitly says the amounts are rent payable/land lease commitments.
    if any(marker in context for marker in _LESSEE_MARKERS):
        return False
    return True


def _candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not _table_is_safe(table) or not _is_lessor_lease_table(table):
        return None
    if _normalize(_row_label(row)) not in _TOTAL_LABELS:
        return None
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
        "kind": _table_kind(table).replace(" ", "_"),
        "context": _table_context(table),
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
        "role": "operating_lease_total_source_cell",
        "document_id": document_id,
        "internal_table_uid": uid,
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "row_label": str(candidate.get("row_label") or ""),
        "source_context": str(candidate.get("context") or ""),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "requested_output_divisor": str(spec["output_divisor"]),
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    selection = {
        "score": 70.8,
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
        "program_family": "operating_lease_total",
        "program_kind": candidate.get("kind"),
        "question_output_divisor": str(spec["output_divisor"]),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "promotion_allowed": False,
    }
    return [source], selection


def _resolve_operating_lease_total(
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
            "context": selected["context"],
            "column_context": selected.get("column_context"),
        }
    )
    return answer, sources, "float(df1.loc[0, 'value'])", OPERATING_LEASE_TOTAL_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    spec = _family_spec(item)
    if spec is not None:
        _STATS["family_seen"] += 1
        result = _resolve_operating_lease_total(
            item,
            spec,
            tables_by_uid=tables_by_uid,
        )
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == OPERATING_LEASE_TOTAL_TIER:
        return 70.8
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _write_variant_metadata(output_dir: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["operating_lease_total_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "route": OPERATING_LEASE_TOTAL_TIER,
        "contract": {
            "family": "direct_lookup",
            "one_ticker_and_one_report_year": True,
            "explicit_scope_or_agreeing_scope_duplicates": True,
            "financial_note_or_financial_data_schedule_only": True,
            "lessor_context_marker_required": True,
            "lessee_payable_context_rejected": True,
            "exact_total_row_required": True,
            "current_period_column_required": True,
            "declared_source_unit_required": True,
            "conflicting_duplicate_answers_rejected": True,
            "missing_source_unit_not_inferred": True,
            "question_id_allowlist": False,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "operating_lease_total_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace_path = output_dir / "operating_lease_total_trace_v1.jsonl"
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
