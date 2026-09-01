#!/usr/bin/env python3
"""Run a strict source-cell ablation for named supplier payables.

The generic direct resolver can be distracted by a nearby ``phải trả khác``
table when a question asks for a supplier payable to one related company.  A
small, repeatable pattern is available in the structured corpus: the exact
counterparty named in the question appears as a row immediately below a
``Phải trả nhà cung cấp``/``Phải trả người bán`` section heading, with an
explicit report-period and source-unit declaration.

This adapter is deliberately family-based rather than question-ID based.  It
requires a direct-lookup plan, one ticker, one report year, an explicitly
named counterparty, a closing-period question, a safe financial-note or
related-party table, a local supplier-payable section marker, and an explicit
source unit.  Unscoped separate/consolidated duplicates are accepted only when
their replayed answers agree.  A successful run remains an authorized
best-effort candidate lane; it is not a strict VERIFIED release or an
official score.
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


VARIANT_PROTOCOL = "vifinqa_supplier_payable_strict_v1"
SUPPLIER_PAYABLE_TIER = "program_supplier_payable_v1"
HELPER_NAME = "_vifinqa_named_compensation_helper_for_supplier_payable"


def _load_helper() -> Any:
    """Reuse the immutable builder-loading and source-cell helpers."""

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

_SAFE_TABLE_KINDS = {"related_party_schedule", "financial_note"}
_SUPPLIER_SECTION_MARKERS = (
    "phai tra nha cung cap",
    "phai tra nguoi ban",
)
_SECTION_STOP_MARKERS = (
    "tra truoc nha cung cap",
    "tra truoc nguoi ban",
    "phai tra khac",
    "phai thu khac",
    "phai thu",
    "phai tra nguoi lao dong",
)
_COUNTERPARTY_SHAPE_WORDS = {
    "cong",
    "ty",
    "tong",
    "tap",
    "hang",
    "ngan",
    "cp",
    "ctcp",
    "co",
    "phan",
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


def _counterparty_key(value: Any) -> str:
    """Normalize common Vietnamese company abbreviations deterministically."""

    normalized = _normalize(value)
    normalized = re.sub(r"\bctcp\b", "cong ty co phan", normalized)
    normalized = re.sub(r"\bcp\b", "co phan", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _counterparty_from_question(question_norm: str) -> str:
    """Extract the named supplier between ``nhà cung cấp`` and ``của``.

    Requiring the owner cue prevents this route from guessing a counterparty
    from a retrieved source table or from a generic supplier-payable question.
    """

    marker = "nha cung cap"
    if marker not in question_norm:
        return ""
    suffix = question_norm.split(marker, 1)[1].strip()
    owner_match = re.search(r"\s+cua\b", suffix)
    if owner_match is None:
        return ""
    candidate = suffix[: owner_match.start()].strip(" ,.;:?-–—")
    if not candidate:
        return ""
    tokens = [token for token in candidate.split() if token]
    if len(tokens) < 2:
        return ""
    # A named company/entity should have a company-form or legal-name token.
    # This keeps phrases such as ``nhà cung cấp khác`` outside the route.
    if not any(token in _COUNTERPARTY_SHAPE_WORDS for token in tokens):
        return ""
    return _counterparty_key(candidate)


def _family_spec(item: Mapping[str, Any]) -> dict[str, Any] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "direct_lookup":
        return None
    question = str(item.get("question") or "")
    question_norm = _normalize(question)
    if "phai tra" not in question_norm or "nha cung cap" not in question_norm:
        return None
    if not any(
        cue in question_norm
        for cue in ("cuoi nam", "cuoi ky", "den ngay", "tai ngay", "ket thuc", "31/12")
    ):
        return None
    counterparty = _counterparty_from_question(question_norm)
    if not counterparty:
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
        "counterparty": counterparty,
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


def _row_has_numeric_value(row: list[Any]) -> bool:
    return any(BUILDER.parse_decimal(cell) is not None for cell in row[1:])


def _is_supplier_section(label: str) -> bool:
    normalized = _normalize(label)
    return any(
        normalized == marker or normalized.startswith(marker + " ")
        for marker in _SUPPLIER_SECTION_MARKERS
    )


def _is_section_stop(label: str) -> bool:
    normalized = _normalize(label)
    return any(
        normalized == marker or normalized.startswith(marker + " ")
        for marker in _SECTION_STOP_MARKERS
    )


def _supplier_section_anchor(
    table: Mapping[str, Any], *, row_index: int
) -> tuple[int, str] | None:
    """Find a nearby supplier-payable heading without crossing another section."""

    rows = table.get("rows") or []
    start = max(0, int(row_index) - 12)
    for index in range(int(row_index) - 1, start - 1, -1):
        row = rows[index]
        if not isinstance(row, list):
            continue
        label = _row_label(row)
        normalized = _normalize(label)
        if not normalized:
            continue
        if _is_supplier_section(label):
            return index, label
        if _is_section_stop(label):
            return None
        # Numeric detail rows can be crossed while walking to the heading.
        # Another nonnumeric heading is a boundary; do not borrow a marker
        # from a different table subsection.
        if not _row_has_numeric_value(row):
            return None
    return None


def _same_counterparty(source_label: str, question_key: str) -> bool:
    source_key = _counterparty_key(source_label)
    if source_key == question_key:
        return True
    # Allow only a short source footnote after an otherwise exact legal name.
    if not source_key.startswith(question_key + " "):
        return False
    suffix = source_key[len(question_key) :].strip()
    return bool(re.fullmatch(r"(?:\(?[ivx]+\)|\(?\d+\)|[*†‡])", suffix))


def _candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not _table_is_safe(table):
        return None
    if not _same_counterparty(_row_label(row), str(spec["counterparty"])):
        return None
    anchor = _supplier_section_anchor(table, row_index=row_index)
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
        "kind": _table_kind(table).replace(" ", "_"),
        "anchor_index": int(anchor_index),
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
        int(candidate.get("kind") == "related_party_schedule"),
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
        "role": "supplier_payable_source_cell",
        "document_id": document_id,
        "internal_table_uid": uid,
        "row_index": int(candidate["row_index"]),
        "column_index": int(candidate["column_index"]),
        "row_label": str(candidate.get("row_label") or ""),
        "section_label": str(candidate.get("anchor_label") or ""),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "requested_output_divisor": str(spec["output_divisor"]),
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
        "section_label": str(candidate.get("anchor_label") or ""),
        "column_context": candidate.get("column_context"),
        "document_id": document_id,
        "internal_table_uid": uid,
        "candidate_rank": 0,
        "candidate_source": VARIANT_PROTOCOL,
        "research_candidate_only": True,
        "program_family": "supplier_payable",
        "program_kind": candidate.get("kind"),
        "counterparty": spec["counterparty"],
        "question_output_divisor": str(spec["output_divisor"]),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "promotion_allowed": False,
    }
    return [source], selection


def _resolve_supplier_payable(
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
                "counterparty": spec["counterparty"],
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
                "counterparty": spec["counterparty"],
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
            "counterparty": spec["counterparty"],
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
            "section_index": selected["anchor_index"],
            "section_label": selected["anchor_label"],
            "column_context": selected.get("column_context"),
        }
    )
    return answer, sources, "float(df1.loc[0, 'value'])", SUPPLIER_PAYABLE_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    spec = _family_spec(item)
    if spec is not None:
        _STATS["family_seen"] += 1
        result = _resolve_supplier_payable(
            item,
            spec,
            tables_by_uid=tables_by_uid,
        )
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == SUPPLIER_PAYABLE_TIER:
        return 70.7
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _write_variant_metadata(output_dir: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["supplier_payable_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "route": SUPPLIER_PAYABLE_TIER,
        "contract": {
            "family": "direct_lookup",
            "named_counterparty_required": True,
            "one_ticker_and_one_report_year": True,
            "explicit_scope_or_agreeing_scope_duplicates": True,
            "financial_note_or_related_party_table_only": True,
            "local_supplier_payable_section_required": True,
            "exact_counterparty_row_required": True,
            "current_period_column_required": True,
            "declared_source_unit_required": True,
            "conflicting_duplicate_answers_rejected": True,
            "question_id_allowlist": False,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "supplier_payable_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace_path = output_dir / "supplier_payable_trace_v1.jsonl"
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
