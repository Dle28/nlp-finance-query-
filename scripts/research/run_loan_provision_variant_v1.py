#!/usr/bin/env python3
"""Run a strict source-cell ablation for loan-provision balances.

Direct questions about provisions for customer loans are unusually easy to
bind to the wrong nearby row.  A report can contain the balance-sheet
contra-asset row, a movement schedule with an ending balance, a current-year
expense row, and a separate provision for another asset class.  This adapter
adds a small family executor for the two balance patterns that can be bound
without learned ranking:

* an exact ``Dự phòng rủi ro cho vay khách hàng`` balance row;
* an explicitly titled loan-provision movement/short-loan schedule whose
  ending row is selected by the period-column contract.

The short-loan aggregate is accepted only after a Decimal checksum over the
detail rows.  The route is family-based and fail-closed: it requires one
ticker/year, an explicit ending-period question, a safe source table, an
explicit source unit, and either an exact row/context match or a checked
aggregate.  Unscoped separate and consolidated values are accepted only when
their replayed Decimal answers agree.  Results remain in the authorized
best-effort candidate lane, not a strict VERIFIED release or an official
score.
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


VARIANT_PROTOCOL = "vifinqa_loan_provision_balance_strict_v1"
LOAN_PROVISION_TIER = "program_loan_provision_balance_v1"
HELPER_NAME = "_vifinqa_named_compensation_helper_for_loan_provision"


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
    "debt_schedule",
    "financial_data_schedule",
    "financial_note",
    "financial_note_detail",
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
_FLOW_CUES = (
    "chi phi",
    "trich lap",
    "hoan nhap",
    "su dung du phong",
    "phat sinh trong nam",
)
_BALANCE_ROW_LABELS = {
    "so du cuoi ky",
    "so du cuoi nam",
    "so cuoi nam",
}
_TOTAL_ROW_LABELS = {"cong", "tong", "tong cong"}
_DIRECT_CUSTOMER_ROW = "du phong rui ro cho vay khach hang"
_CUSTOMER_CONTEXT = "du phong rui ro cho vay khach hang"
_COMMON_CONTEXT = "du phong chung cho cac khoan cho vay khach hang"
_SHORT_CONTEXT = "chi tiet du phong cac khoan cho vay ngan han"


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
    label = re.sub(r"^(?:\d+|[ivxlcdm]+)[.)]?\s+", "", label)
    label = re.sub(r"\s*\(?[*†‡]+\)?\s*$", "", label)
    return re.sub(r"\s+", " ", label).strip()


def _family_spec(item: Mapping[str, Any]) -> dict[str, Any] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "direct_lookup":
        return None
    question = str(item.get("question") or "")
    question_norm = _normalize(question)
    if "du phong" not in question_norm:
        return None
    if not (
        _CUSTOMER_CONTEXT in question_norm
        or "du phong chung cho cac khoan cho vay khach hang" in question_norm
        or "du phong cac khoan cho vay ngan han" in question_norm
    ):
        return None
    if any(cue in question_norm for cue in _FLOW_CUES):
        return None
    if not any(cue in question_norm for cue in _ENDING_CUES):
        return None

    ticker = _single_ticker(plan)
    year = _single_year(plan)
    if not ticker or year is None:
        return None
    raw_scope = str(plan.get("scope") or plan.get("reporting_scope") or "")
    scope = raw_scope.strip().lower()
    if scope not in {"", "separate", "consolidated"}:
        return None

    if "du phong cac khoan cho vay ngan han" in question_norm:
        metric_kind = "short_loan"
    elif "du phong chung" in question_norm:
        metric_kind = "common"
    else:
        metric_kind = "customer_total"

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
        "metric_kind": metric_kind,
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
    for field in ("headers", "column_labels"):
        raw = table.get(field) or []
        if isinstance(raw, Mapping):
            values.extend(str(value or "") for value in raw.values())
        elif isinstance(raw, (list, tuple)):
            values.extend(str(value or "") for value in raw)
        elif raw:
            values.append(str(raw))
    section = table.get("table_section") or ""
    if isinstance(section, Mapping):
        values.extend(str(value or "") for value in section.values())
    elif section:
        values.append(str(section))
    # A movement title is normally in context_trace, while a short-loan
    # section title is frequently only the first structured row/header.
    for row in (table.get("rows") or [])[:16]:
        if isinstance(row, list):
            values.append(_row_label(row))
    return _normalize(" ".join(values))


def _row_context(table: Mapping[str, Any], row_index: int) -> str:
    rows = table.get("rows") or []
    start = max(0, int(row_index) - 4)
    labels = [
        _row_label(rows[index])
        for index in range(start, min(len(rows), int(row_index) + 1))
        if isinstance(rows[index], list)
    ]
    return _normalize(" ".join(labels) + " " + _table_context(table))


def _is_ending_balance_row(label: Any) -> bool:
    normalized = _clean_label(label)
    if normalized in _BALANCE_ROW_LABELS:
        return True
    if normalized.startswith("so du tai ngay"):
        # Do not let an opening-date row masquerade as the requested closing
        # balance.  The date-bearing closing rows in this corpus use day 28+
        # or the explicit year-end wording.
        return bool(re.search(r"\b(?:28|29|30|31)\s+thang\s+", normalized))
    return False


def _is_direct_customer_row(table: Mapping[str, Any], row: list[Any]) -> bool:
    label = _clean_label(_row_label(row))
    if label != _DIRECT_CUSTOMER_ROW:
        return False
    context = _table_context(table)
    return _CUSTOMER_CONTEXT in context and "chi phi" not in context


def _is_common_movement_row(
    table: Mapping[str, Any], row_index: int, row: list[Any]
) -> bool:
    if not _is_ending_balance_row(_row_label(row)):
        return False
    context = _row_context(table, row_index)
    if _COMMON_CONTEXT not in context:
        return False
    if "cu the" in context and "du phong chung" not in context:
        return False
    return "tong cong" in context or "so du cuoi" in _clean_label(_row_label(row))


def _is_customer_movement_row(
    table: Mapping[str, Any], row_index: int, row: list[Any]
) -> bool:
    if not _is_ending_balance_row(_row_label(row)):
        return False
    context = _row_context(table, row_index)
    if _CUSTOMER_CONTEXT not in context:
        return False
    if any(marker in context for marker in ("chi phi du phong", "trich lap du phong")):
        return False
    # Require movement/aggregate context so a generic ``Số cuối năm`` row in
    # an unrelated schedule cannot become a loan-provision answer.
    return any(
        marker in context
        for marker in ("thay doi du phong", "bien dong du phong", "tong cong")
    )


def _choose_cell(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> tuple[int, Decimal, list[dict[str, Any]], str] | None:
    return HELPER.HELPER._choose_cell(
        table,
        row_index=row_index,
        row=row,
        spec=spec,
    )


def _candidate_from_row(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
    program_kind: str,
) -> dict[str, Any] | None:
    chosen = _choose_cell(table, row_index=row_index, row=row, spec=spec)
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
        "program_kind": program_kind,
        "context": _row_context(table, row_index),
    }


def _short_loan_total_candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> dict[str, Any] | None:
    if _SHORT_CONTEXT not in _table_context(table):
        return None
    label = _clean_label(_row_label(row))
    if label and label not in _TOTAL_ROW_LABELS:
        return None
    rows = table.get("rows") or []
    section_indices = [
        index
        for index, candidate_row in enumerate(rows[: row_index + 1])
        if isinstance(candidate_row, list)
        and _SHORT_CONTEXT in _clean_label(_row_label(candidate_row))
    ]
    if not section_indices:
        return None
    section_index = section_indices[-1]
    if row_index <= section_index:
        return None
    # The aggregate must terminate this small detail section.  This prevents
    # a blank subtotal in the middle of a longer note from being selected.
    if any(
        isinstance(later, list)
        and any(BUILDER.parse_decimal(cell) is not None for cell in later[1:])
        for later in rows[row_index + 1 :]
    ):
        return None

    chosen_total = _choose_cell(table, row_index=row_index, row=row, spec=spec)
    if chosen_total is None:
        return None
    total_column, total_raw, evidence, column_context = chosen_total
    child_values: list[Decimal] = []
    child_count = 0
    for child_index in range(section_index + 1, row_index):
        child = rows[child_index]
        if not isinstance(child, list):
            continue
        child_label = _clean_label(_row_label(child))
        if not child_label:
            continue
        if not any(BUILDER.parse_decimal(cell) is not None for cell in child[1:]):
            continue
        chosen_child = _choose_cell(
            table,
            row_index=child_index,
            row=child,
            spec=spec,
        )
        if chosen_child is None or chosen_child[0] != total_column:
            return None
        child_values.append(chosen_child[1])
        child_count += 1
    if child_count == 0 or sum(child_values, Decimal(0)) != total_raw:
        _STATS["short_loan_checksum_rejected"] += 1
        return None

    multiplier = _table_multiplier(table)
    if multiplier is None:
        return None
    answer = total_raw * multiplier / spec["output_divisor"]
    return {
        "answer": answer,
        "raw_value": total_raw,
        "source_multiplier": multiplier,
        "row_index": int(row_index),
        "column_index": int(total_column),
        "row_label": _row_label(row),
        "column_context": column_context,
        "evidence": evidence,
        "table": table,
        "kind": _table_kind(table).replace(" ", "_"),
        "program_kind": "short_loan_total",
        "context": _row_context(table, row_index),
        "checksum_child_count": child_count,
        "checksum_child_sum": str(sum(child_values, Decimal(0))),
    }


def _candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not _table_is_safe(table):
        return None
    metric_kind = spec["metric_kind"]
    if metric_kind == "short_loan":
        return _short_loan_total_candidate(
            table, row_index=row_index, row=row, spec=spec
        )
    if metric_kind == "common":
        if _is_common_movement_row(table, row_index, row):
            return _candidate_from_row(
                table,
                row_index=row_index,
                row=row,
                spec=spec,
                program_kind="common_movement_balance",
            )
        return None

    if _is_direct_customer_row(table, row):
        return _candidate_from_row(
            table,
            row_index=row_index,
            row=row,
            spec=spec,
            program_kind="customer_balance_row",
        )
    if _is_customer_movement_row(table, row_index, row):
        return _candidate_from_row(
            table,
            row_index=row_index,
            row=row,
            spec=spec,
            program_kind="customer_movement_balance",
        )
    return None


def _candidate_rank(
    candidate: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[int, int, int, str]:
    table = candidate["table"]
    scope = str(BUILDER.table_reporting_scope(table) or "").lower()
    kind_priority = {
        "short_loan_total": 4,
        "common_movement_balance": 3,
        "customer_balance_row": 2,
        "customer_movement_balance": 1,
    }
    return (
        int(bool(spec.get("scope")) and scope == str(spec.get("scope") or "")),
        kind_priority.get(str(candidate.get("program_kind") or ""), 0),
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
        "role": "loan_provision_balance_source_cell",
        "document_id": document_id,
        "internal_table_uid": uid,
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "row_label": str(candidate.get("row_label") or ""),
        "source_context": str(candidate.get("context") or ""),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "requested_output_divisor": str(spec["output_divisor"]),
        "metric_kind": spec["metric_kind"],
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    selection = {
        "score": 71.0,
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
        "program_family": "loan_provision_balance",
        "program_kind": candidate.get("program_kind"),
        "table_kind": candidate.get("kind"),
        "metric_kind": spec["metric_kind"],
        "question_output_divisor": str(spec["output_divisor"]),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "promotion_allowed": False,
    }
    if candidate.get("checksum_child_count") is not None:
        selection["checksum_child_count"] = candidate["checksum_child_count"]
        selection["checksum_child_sum"] = candidate["checksum_child_sum"]
    return [source], selection


def _tables_for_spec(
    tables_by_uid: Mapping[str, Mapping[str, Any]], spec: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    return HELPER._tables_for_spec(tables_by_uid, spec)


def _resolve_loan_provision(
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
                "candidates": [
                    {
                        "answer": str(candidate["answer"]),
                        "program_kind": candidate.get("program_kind"),
                        "document_id": candidate["table"].get("document_id"),
                        "internal_table_uid": candidate["table"].get(
                            "internal_table_uid"
                        ),
                    }
                    for candidate in candidates
                ],
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
    _STATS[f"accepted_{selected['program_kind']}"] += 1
    _TRACE.append(
        {
            "question_id": spec.get("question_id"),
            "status": "ACCEPTED",
            "ticker": spec["ticker"],
            "year": spec["year"],
            "scope": spec.get("scope") or None,
            "metric_kind": spec["metric_kind"],
            "candidate_count": len(candidates),
            "answer": str(answer),
            "document_id": sources[0]["document_id"],
            "internal_table_uid": sources[0]["internal_table_uid"],
            "row_index": sources[0]["row_index"],
            "column_index": sources[0]["column_index"],
            "raw_value": sources[0]["raw_value_decimal"],
            "source_to_vnd_multiplier": sources[0]["source_to_vnd_multiplier"],
            "selected_program_kind": selected["program_kind"],
            "selected_table_kind": selected["kind"],
            "row_label": selected["row_label"],
            "column_context": selected.get("column_context"),
        }
    )
    return answer, sources, "float(df1.loc[0, 'value'])", LOAN_PROVISION_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    spec = _family_spec(item)
    if spec is not None:
        _STATS["family_seen"] += 1
        result = _resolve_loan_provision(item, spec, tables_by_uid=tables_by_uid)
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == LOAN_PROVISION_TIER:
        return 71.0
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _write_variant_metadata(output_dir: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["loan_provision_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "route": LOAN_PROVISION_TIER,
        "contract": {
            "family": "direct_lookup",
            "one_ticker_and_one_report_year": True,
            "ending_period_intent_required": True,
            "safe_balance_or_financial_note_kinds_only": True,
            "exact_customer_loan_provision_row_or_titled_schedule_required": True,
            "common_provision_requires_ending_balance_row": True,
            "short_loan_total_requires_detail_checksum": True,
            "current_period_column_required": True,
            "declared_source_unit_required": True,
            "conflicting_duplicate_answers_rejected": True,
            "question_id_allowlist": False,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "loan_provision_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    trace_path = output_dir / "loan_provision_trace_v1.jsonl"
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
