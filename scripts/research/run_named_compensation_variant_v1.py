#!/usr/bin/env python3
"""Run a strict source-cell ablation for named governance compensation.

Several direct questions ask for the remuneration of one named board member.
The generic semantic-cell route can select an unrelated ownership or salary
row, while the structured corpus contains a small, repeatable pattern:
``Hội đồng Quản trị``/``Tổng thù lao`` followed by the named person and an
explicit report-year column.  This adapter adds only that family executor and
leaves the canonical builder responsible for submission generation, source
coordinate replay, validation, and the local PARTIAL/UNRESOLVED classes.

The route is intentionally fail-closed.  It requires a direct-lookup plan,
one ticker, one report year, an explicit person name, a governance or related-
party table, local board/remuneration context, a current-period cell, and a
source-declared unit.  If an unscoped question has conflicting separate and
consolidated values, it abstains.  A successful run is an authorized
best-effort candidate lane, not a strict VERIFIED release or an official score.
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


VARIANT_PROTOCOL = "vifinqa_named_governance_compensation_strict_v1"
NAMED_COMPENSATION_TIER = "program_named_governance_compensation_v1"
HELPER_NAME = "_vifinqa_subsidiary_variant_helper_for_named_compensation"


def _load_helper() -> Any:
    """Reuse the immutable builder-loading and source-cell helpers."""

    helper_path = Path(__file__).with_name(
        "run_subsidiary_investment_variant_v1.py"
    )
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

_SAFE_TABLE_KINDS = {"governance_roster", "related_party_schedule"}
_CONTEXT_MARKERS = (
    "hoi dong quan tri",
    "tong thu lao",
    "nhan su quan ly chu chot",
    "thanh vien hdqt",
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


def _single_ticker(plan: Mapping[str, Any]) -> str:
    return HELPER._single_ticker(plan)


def _single_year(plan: Mapping[str, Any]) -> int | None:
    return HELPER._single_year(plan)


def _normalize(value: Any) -> str:
    return HELPER._normalize(value)


def _row_label(row: Any) -> str:
    return HELPER._row_label(row)


def _person_query(question: str) -> str:
    """Extract the named person from ordinary Vietnamese question wording."""

    normalized = _normalize(question)
    patterns = (
        # ``Ông Nguyễn Hạnh Phúc – Chủ tịch ...`` and
        # ``ông Lê Phước Vũ (Chủ tịch HĐQT) ...``.
        r"\b(?:ong|ba)\b\s+(.+?)(?:\s*\(|\s*[–—-]\s*|\s+cua\b|\s+tai\b|\s+trong\b|\s+nam\b|$)",
        # ``thành viên HĐQT Chu Thị Bình tại ...``.
        r"\b(?:hdqt|hoi dong quan tri)\b\s+(.+?)(?:\s+tai\b|\s+cua\b|\s+trong\b|\s+nam\b|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        value = re.sub(r"\s+", " ", match.group(1)).strip(" ,.;:?-–—")
        # The question often includes the role in the capture (for example
        # ``... Nguyễn Hạnh Phúc – Chủ tịch``).  The source row may carry the
        # role in a separate column, so match on the stable person name.
        value = re.sub(
            r"\s+(?:chu tich(?:\s+hdqt)?|hdqt|thanh vien|pho chu tich(?:\s+thuong truc)?(?:\s+hdqt)?)\s*$",
            "",
            value,
        ).strip()
        # Do not treat a role as a name.  The current dataset uses two or
        # more name tokens; requiring that shape avoids matching ``ông chủ``.
        tokens = [token for token in value.split() if token]
        if (
            len(tokens) >= 2
            and tokens[0] not in {"cua", "tai", "trong", "nam", "la", "voi"}
            and not all(
            token in {"chu", "tich", "thanh", "vien", "hdqt"}
            for token in tokens
            )
        ):
            return value
    return ""


def _family_spec(item: Mapping[str, Any]) -> dict[str, Any] | None:
    plan = item.get("question_plan") or {}
    if str(plan.get("family") or "") != "direct_lookup":
        return None
    question = str(item.get("question") or "")
    question_norm = _normalize(question)
    if "thu lao" not in question_norm:
        return None
    person = _person_query(question)
    if not person:
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
        "person": person,
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


def _row_context(table: Mapping[str, Any], row_index: int) -> str:
    rows = table.get("rows") or []
    start = max(0, int(row_index) - 5)
    labels = [_row_label(rows[index]) for index in range(start, min(len(rows), row_index + 1))]
    section = table.get("table_section") or ""
    if isinstance(section, Mapping):
        section = " ".join(str(value or "") for value in section.values())
    return _normalize(" ".join(labels) + " " + str(section))


def _is_named_compensation_row(
    table: Mapping[str, Any], row_index: int, row: list[Any], spec: Mapping[str, Any]
) -> bool:
    label = _normalize(_row_label(row))
    person = str(spec["person"])
    if not label or person not in label:
        return False
    context = _row_context(table, row_index)
    if not any(marker in context for marker in _CONTEXT_MARKERS):
        return False
    # A person can occur in several governance/related-party sections.  The
    # local board/remuneration marker must be tied to the same small table,
    # never inferred from a different document or from the question alone.
    return True


def _table_multiplier(table: Mapping[str, Any]) -> Decimal | None:
    return HELPER._explicit_source_multiplier(table)


def _source_unit_specificity(table: Mapping[str, Any]) -> int:
    """Rank source unit declarations without consulting inferred metadata."""

    header_text = _normalize(
        " ".join(
            str(value)
            for field in ("column_labels", "headers")
            for value in (table.get(field) or [])
        )
    )
    # A scale-bearing declaration is more informative than a bare ``VND``
    # header when OCR duplicated the same raw row across tables and dropped
    # the scale from one copy.  This is used only for an exact raw-value
    # duplicate; unrelated values remain fail-closed.
    if any(
        phrase in header_text
        for phrase in (
            "trieu vnd",
            "million vnd",
            "nghin vnd",
            "thousand vnd",
            "ty vnd",
            "billion vnd",
        )
    ):
        return 2
    if "vnd" in header_text or "dong" in header_text:
        return 1
    return 0


def _candidate(
    table: Mapping[str, Any],
    *,
    row_index: int,
    row: list[Any],
    spec: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not _table_is_safe(table):
        return None
    if not _is_named_compensation_row(table, row_index, row, spec):
        return None
    chosen = HELPER._choose_cell(
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
        "context": _row_context(table, row_index),
    }


def _candidate_rank(candidate: Mapping[str, Any], spec: Mapping[str, Any]) -> tuple[int, int, str]:
    table = candidate["table"]
    scope = str(BUILDER.table_reporting_scope(table) or "").lower()
    explicit_scope = bool(spec.get("scope"))
    return (
        int(explicit_scope and scope == spec.get("scope"))
        + int("tong thu lao" in str(candidate.get("context") or "")),
        int(candidate.get("kind") == "governance_roster"),
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
        "role": "named_governance_compensation_source_cell",
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
        "score": 70.5,
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
        "program_family": "named_governance_compensation",
        "program_kind": candidate.get("kind"),
        "person": spec["person"],
        "question_output_divisor": str(spec["output_divisor"]),
        "source_to_vnd_multiplier": str(candidate["source_multiplier"]),
        "promotion_allowed": False,
    }
    return [source], selection


def _tables_for_spec(
    tables_by_uid: Mapping[str, Mapping[str, Any]], spec: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    index = HELPER._table_index(tables_by_uid)
    ticker = str(spec["ticker"])
    year = int(spec["year"])
    scope = str(spec.get("scope") or "")
    if scope:
        return list(index.get((ticker, year, scope), []))
    tables: list[Mapping[str, Any]] = []
    for (indexed_ticker, indexed_year, _indexed_scope), group in index.items():
        if indexed_ticker == ticker and indexed_year == year:
            tables.extend(group)
    return tables


def _resolve_named_compensation(
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
            _STATS["table_rejected_non_compensation_kind"] += 1
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
                "person": spec["person"],
            }
        )
        return None
    answers = {candidate["answer"] for candidate in candidates}
    if len(answers) != 1:
        raw_values = {candidate["raw_value"] for candidate in candidates}
        specificities = {
            _source_unit_specificity(candidate["table"])
            for candidate in candidates
        }
        max_specificity = max(specificities)
        preferred = [
            candidate
            for candidate in candidates
            if _source_unit_specificity(candidate["table"]) == max_specificity
        ]
        preferred_answers = {candidate["answer"] for candidate in preferred}
        if len(raw_values) == 1 and max_specificity > min(specificities) and len(preferred_answers) == 1:
            # An exact duplicate row has one scale-bearing header and one OCR
            # copy with only ``VND``.  Keep the source declaration carrying
            # the more specific scale; do not apply this relaxation to
            # different raw values or two equally specific unit contracts.
            candidates = preferred
            answers = preferred_answers
            _STATS["resolved_exact_duplicate_by_specific_source_unit"] += 1
        else:
            _STATS["rejected_conflicting_duplicate_answers"] += 1
            _TRACE.append(
                {
                    "question_id": spec.get("question_id"),
                    "status": "REJECTED_CONFLICTING_DUPLICATE_ANSWERS",
                    "ticker": spec["ticker"],
                    "year": spec["year"],
                    "scope": spec.get("scope") or None,
                    "person": spec["person"],
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
            "person": spec["person"],
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
        }
    )
    return answer, sources, "float(df1.loc[0, 'value'])", NAMED_COMPENSATION_TIER


def _patched_program_aware(
    item: dict[str, Any],
    *,
    tables_by_uid: dict[str, dict[str, Any]] | None = None,
) -> tuple[Decimal, list[dict[str, Any]], str, str] | None:
    spec = _family_spec(item)
    if spec is not None:
        _STATS["family_seen"] += 1
        result = _resolve_named_compensation(
            item,
            spec,
            tables_by_uid=tables_by_uid,
        )
        if result is not None:
            return result
    return _ORIGINAL_PROGRAM_AWARE(item, tables_by_uid=tables_by_uid)


def _patched_route_priority(tier: str) -> float:
    if tier == NAMED_COMPENSATION_TIER:
        return 70.5
    return _ORIGINAL_ROUTE_PRIORITY(tier)


def _write_variant_metadata(output_dir: Path) -> None:
    report_path = output_dir / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["named_governance_compensation_variant"] = {
        "protocol": VARIANT_PROTOCOL,
        "route": NAMED_COMPENSATION_TIER,
        "contract": {
            "family": "direct_lookup",
            "named_person_required": True,
            "one_ticker_and_one_report_year": True,
            "explicit_scope_or_agreeing_scope_duplicates": True,
            "governance_or_related_party_table_only": True,
            "local_board_or_remuneration_context_required": True,
            "current_period_column_required": True,
            "declared_source_unit_required": True,
            "conflicting_duplicate_answers_rejected": True,
            "question_id_allowlist": False,
        },
        "stats": dict(sorted(_STATS.items())),
        "trace_path": str(output_dir / "named_governance_compensation_trace_v1.jsonl"),
        "answer_authority": "current_structured_table_decimal_replay",
        "research_candidate_only": True,
        "promotion_allowed": False,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace_path = output_dir / "named_governance_compensation_trace_v1.jsonl"
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
