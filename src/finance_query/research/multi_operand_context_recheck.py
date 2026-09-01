"""Recheck document context and expand loan-provision composition research.

This module is deliberately diagnostic-only.  It confirms whether an OCR
source provides a usable year/unit context around an already selected cell and
catalogues tables that split loan-loss provision into common and specific
components.  It does not create evidence bindings, answers, or submission
records.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from finance_query.e2e.core.financial_taxonomy import normalize_label
from finance_query.research.multi_operand_machine_review import sha256_file


PROTOCOL = "vifinqa_multi_operand_context_recheck_v1"
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_PAGE_MARKER_RE = re.compile(r"===== PAGE \d+ =====")
_RAW_FINANCIAL_CELL_RE = re.compile(r"^\s*(?:\(?[+-]?\d[\d.,\s]*\)?|-)\s*$")
_FORBIDDEN_KEYS = frozenset(
    {"answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query", "rows"}
)


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number} must contain JSON objects")
        records.append(record)
    if not records:
        raise ValueError(f"{path} contains no JSONL records")
    return records


def _write_jsonl(path: Path, records: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(key in _FORBIDDEN_KEYS or _contains_forbidden_key(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _unit_in_text(text: str) -> str | None:
    normalized = normalize_label(text)
    if "nghin ty" in normalized:
        return "trillion_vnd"
    if "ty dong" in normalized or "ty vnd" in normalized:
        return "billion_vnd"
    if "trieu dong" in normalized or "trieu vnd" in normalized:
        return "million_vnd"
    if "nghin dong" in normalized or "nghin vnd" in normalized:
        return "thousand_vnd"
    if "dong viet nam" in normalized or "vnd" in normalized:
        return "vnd"
    return None


def _table_text(asset: Mapping[str, Any]) -> str:
    values: list[str] = [str(value) for value in asset.get("headers") or []]
    for row_index in asset.get("header_row_indices") or []:
        rows = asset.get("rows") or []
        if isinstance(row_index, int) and 0 <= row_index < len(rows) and isinstance(rows[row_index], list):
            values.extend(str(value) for value in rows[row_index])
    return "\n".join(values)


def _document_context(asset: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(str(asset.get("source_path") or ""))
    if not path.is_file():
        raise FileNotFoundError(f"source document is unavailable: {path}")
    if sha256_file(path) != str(asset.get("source_sha256") or ""):
        raise ValueError("source document hash does not match its full-table asset")
    source = path.read_text(encoding="utf-8")
    char_start = int(asset.get("char_start") or 0)
    if not 0 <= char_start <= len(source):
        raise ValueError("full-table character locator is outside source document")
    page_starts = [match.start() for match in _PAGE_MARKER_RE.finditer(source) if match.start() <= char_start]
    page_start = page_starts[-1] if page_starts else 0
    return {
        "source_hash_verified": True,
        "page_context": source[page_start:char_start],
        "document": source,
    }


def _context_recheck(asset: Mapping[str, Any], planned: Mapping[str, Any]) -> dict[str, Any]:
    requested_year = int((planned.get("years") or [0])[0])
    requested_unit = str((planned.get("unit_contract") or {}).get("requested_unit") or "")
    context = _document_context(asset)
    table_text = _table_text(asset)
    table_years = {int(match.group(1)) for match in _YEAR_RE.finditer(table_text)}
    page_years = {int(match.group(1)) for match in _YEAR_RE.finditer(context["page_context"])}
    document_years = {int(match.group(1)) for match in _YEAR_RE.finditer(context["document"])}
    table_has_current = "nam nay" in normalize_label(table_text)
    table_unit = _unit_in_text(table_text)
    page_unit = _unit_in_text(context["page_context"])

    if requested_year in table_years:
        period_status = "RECHECK_CONFIRMED_EXPLICIT_TABLE_YEAR"
    elif requested_year in page_years:
        period_status = "RECHECK_CONFIRMED_PAGE_CONTEXT_YEAR"
    elif table_has_current and requested_year == int(asset.get("report_year") or 0) and requested_year in document_years:
        period_status = "RECHECK_CONFIRMED_DOCUMENT_YEAR_CURRENT_COLUMN"
    else:
        period_status = "RECHECK_UNRESOLVED_YEAR"
    source_unit = table_unit or page_unit
    conversion_allowed = bool((planned.get("unit_contract") or {}).get("conversion_allowed"))
    if table_unit == requested_unit:
        unit_status = "RECHECK_CONFIRMED_TABLE_UNIT"
        conversion_status = "NOT_REQUIRED"
    elif page_unit == requested_unit:
        unit_status = "RECHECK_CONFIRMED_PAGE_CONTEXT_UNIT"
        conversion_status = "NOT_REQUIRED"
    elif source_unit is not None:
        unit_status = "RECHECK_CONFIRMED_SOURCE_UNIT_REQUIRES_CONVERSION"
        conversion_status = (
            "CONVERSION_ALLOWED_BY_PLAN"
            if conversion_allowed
            else "CONVERSION_REQUIRED_BUT_PLAN_DISALLOWS"
        )
    else:
        unit_status = "RECHECK_UNRESOLVED_UNIT"
        conversion_status = "UNRESOLVED"
    return {
        "source_hash_verified": context["source_hash_verified"],
        "requested_year": requested_year,
        "requested_unit": requested_unit,
        "period_recheck_status": period_status,
        "unit_recheck_status": unit_status,
        "source_unit": source_unit,
        "unit_conversion_status": conversion_status,
        "observed_table_years": sorted(table_years),
        "observed_page_years": sorted(page_years),
        "table_current_period_label": table_has_current,
        "scope_match": str(asset.get("scope") or "") == str(planned.get("scope") or ""),
    }


def _row_label(asset: Mapping[str, Any], row_index: int) -> str:
    rows = asset.get("rows") or []
    if not 0 <= row_index < len(rows) or not isinstance(rows[row_index], list) or not rows[row_index]:
        raise ValueError("selected row is outside the full-table asset")
    return normalize_label(str(rows[row_index][0]))


def _has_component(label: str, kind: str) -> bool:
    return (
        f"du phong {kind}" in label
        and "cho vay" in label
        and "khach hang" in label
    )


def _component_patterns(asset: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = asset.get("rows") or []
    common_rows: list[int] = []
    specific_rows: list[int] = []
    for row_index, row in enumerate(rows):
        # OCR header detection can misclassify every row of a narrow table as
        # a header (as in the ABB provision table).  A row with a precise
        # component label and a numeric peer column is stronger evidence than
        # that structural hint, so do not exclude it here.
        if not isinstance(row, list) or not row:
            continue
        label = normalize_label(str(row[0]))
        if _has_component(label, "chung"):
            common_rows.append(row_index)
        if _has_component(label, "cu the"):
            specific_rows.append(row_index)
    patterns: list[dict[str, Any]] = []
    for common_row in common_rows:
        for specific_row in specific_rows:
            shared_columns = [
                column_index
                for column_index in range(1, min(len(rows[common_row]), len(rows[specific_row])))
                if _RAW_FINANCIAL_CELL_RE.fullmatch(str(rows[common_row][column_index]))
                and _RAW_FINANCIAL_CELL_RE.fullmatch(str(rows[specific_row][column_index]))
            ]
            if not shared_columns:
                continue
            base = {
                "internal_table_uid": str(asset["internal_table_uid"]),
                "source_sha256": str(asset["source_sha256"]),
                "table_sha256": str(asset["table_sha256"]),
                "report_year": asset.get("report_year"),
                "scope": asset.get("scope"),
                "common_row_index": common_row,
                "specific_row_index": specific_row,
                "shared_numeric_column_indices": shared_columns,
            }
            patterns.append({"pattern_id": _canonical_sha(base), **base})
    return patterns


def _load_assets(path: Path) -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    for asset in _load_jsonl(path):
        uid = str(asset.get("internal_table_uid") or "")
        if not uid or uid in assets:
            raise ValueError("full-table assets need one unique internal_table_uid per row")
        assets[uid] = asset
    return assets


def _plans(path: Path) -> dict[int, dict[str, Any]]:
    return {int(plan["question_id"]): plan for plan in _load_jsonl(path)}


def _load_diagnostic(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("protocol") != "vifinqa_multi_operand_machine_diagnostic_v1":
        raise ValueError("unexpected machine diagnostic protocol")
    authorization = report.get("authorization") or {}
    if authorization.get("submission_eligible") is not False or authorization.get("may_authorize_answer") is not False:
        raise ValueError("context recheck accepts only non-authorizing machine diagnostics")
    manifest = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
    output = (manifest.get("outputs") or {}).get(path.name) or {}
    if output.get("sha256") != sha256_file(path):
        raise ValueError("machine diagnostic report hash mismatch")
    return report


def _composition_alignment(
    *, candidate: Mapping[str, Any], patterns_by_table: Mapping[str, list[Mapping[str, Any]]], assets: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any] | None:
    aggregate_operand_ids: list[str] = []
    component_operand_id: str | None = None
    for operand in candidate.get("operands") or []:
        table_uid = str(operand.get("internal_table_uid") or "")
        selected = operand.get("selected_cells") or []
        if len(selected) != 1:
            continue
        row_index = int(selected[0]["row_index"])
        column_index = int(selected[0]["column_index"])
        label = _row_label(assets[table_uid], row_index)
        if "trich lap du phong cho vay khach hang" in label:
            aggregate_operand_ids.append(str(operand["operand_id"]))
        for pattern in patterns_by_table.get(table_uid, []):
            if row_index == int(pattern["specific_row_index"]) and column_index in pattern["shared_numeric_column_indices"]:
                component_operand_id = str(operand["operand_id"])
    if len(aggregate_operand_ids) < 2 or component_operand_id is None:
        return None
    return {
        "question_id": int(candidate["question_id"]),
        "status": "STRUCTURE_CONFIRMED_COMPOSITION_VARIANT_RESEARCH_ONLY",
        "aggregate_operand_ids": sorted(aggregate_operand_ids),
        "component_operand_id": component_operand_id,
        "primary_variant": "composition_hypothesis_common_plus_specific",
        "comparison_variant": "machine_adjusted_selection",
        "reason": "two peer operands use a total loan-provision row while one operand uses the specific component of a common-plus-specific table",
        "may_authorize_answer": False,
        "submission_eligible": False,
    }


def _candidate_condition_reconciliation(
    *, candidate: Mapping[str, Any], rechecks: list[Mapping[str, Any]]
) -> dict[str, Any]:
    """Clear only a diagnostic assumption that direct OCR context now proves."""
    question_id = int(candidate["question_id"])
    by_operand = {
        str(row["operand_id"]): row for row in rechecks if int(row["question_id"]) == question_id
    }
    original = set(str(value) for value in candidate.get("assumption_codes") or [])
    cleared: set[str] = set()
    for condition in original:
        if condition != "ASSUMED_REQUESTED_UNIT_WHEN_SOURCE_HEADER_MISSING":
            continue
        assumed_operands = [
            str(operand["operand_id"])
            for operand in candidate.get("operands") or []
            if condition in (operand.get("unit_assumptions") or [])
        ]
        if assumed_operands and all(
            by_operand.get(operand_id, {}).get("recheck_status") == "CONTEXT_RECHECK_CONFIRMED"
            for operand_id in assumed_operands
        ):
            cleared.add(condition)
    remaining = sorted(original - cleared)
    question_rechecks = list(by_operand.values())
    if not remaining and question_rechecks and all(
        row["recheck_status"] == "CONTEXT_RECHECK_CONFIRMED" for row in question_rechecks
    ):
        readiness = "CONTEXT_CONFIRMED_RESEARCH_CANDIDATE"
    elif "RESEARCH_CONVERSION_OUTSIDE_PLAN_CONTRACT" in remaining:
        readiness = "RESEARCH_PLAN_CONTRACT_FOLLOWUP_REQUIRED"
    else:
        readiness = "RESEARCH_CONTEXT_FOLLOWUP_REQUIRED"
    return {
        "question_id": question_id,
        "candidate_id": candidate["candidate_id"],
        "variant": candidate["variant"],
        "cleared_research_conditions": sorted(cleared),
        "remaining_research_conditions": remaining,
        "research_readiness": readiness,
        "may_authorize_answer": False,
        "submission_eligible": False,
    }


def build_multi_operand_context_recheck(
    *, diagnostic_path: Path, plans_path: Path, assets_path: Path, output_dir: Path
) -> dict[str, Any]:
    """Write source-context rechecks and a reusable component-table catalogue."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    diagnostic = _load_diagnostic(diagnostic_path)
    plans = _plans(plans_path)
    assets = _load_assets(assets_path)
    base_candidates = [
        candidate
        for candidate in diagnostic.get("candidates") or []
        if candidate.get("variant") == "machine_adjusted_selection"
    ]
    if not base_candidates:
        raise ValueError("machine diagnostic contains no baseline candidates")
    rechecks: list[dict[str, Any]] = []
    for candidate in base_candidates:
        question_id = int(candidate["question_id"])
        plan = plans.get(question_id)
        if plan is None:
            raise ValueError(f"diagnostic question Q{question_id} has no typed plan")
        plan_operands = {str(value["operand_id"]): value for value in plan.get("operands") or []}
        for operand in candidate.get("operands") or []:
            operand_id = str(operand["operand_id"])
            planned = plan_operands.get(operand_id)
            table_uid = str(operand["internal_table_uid"])
            if planned is None or table_uid not in assets:
                raise ValueError(f"Q{question_id} {operand_id} cannot resolve its planned source")
            detail = _context_recheck(assets[table_uid], planned)
            rechecks.append(
                {
                    "question_id": question_id,
                    "operand_id": operand_id,
                    "internal_table_uid": table_uid,
                    "selected_cells": operand.get("selected_cells") or [],
                    **detail,
                    "recheck_status": (
                        "CONTEXT_RECHECK_CONFIRMED"
                        if detail["period_recheck_status"] != "RECHECK_UNRESOLVED_YEAR"
                        and detail["unit_recheck_status"] != "RECHECK_UNRESOLVED_UNIT"
                        and detail["unit_conversion_status"]
                        not in {"CONVERSION_REQUIRED_BUT_PLAN_DISALLOWS", "UNRESOLVED"}
                        and detail["scope_match"]
                        else "CONTEXT_RECHECK_NEEDS_FOLLOWUP"
                    ),
                    "may_authorize_evidence": False,
                    "may_authorize_answer": False,
                    "submission_eligible": False,
                }
            )
    patterns = [pattern for asset in assets.values() for pattern in _component_patterns(asset)]
    patterns_by_table: dict[str, list[Mapping[str, Any]]] = {}
    for pattern in patterns:
        patterns_by_table.setdefault(str(pattern["internal_table_uid"]), []).append(pattern)
    reconciliations = [
        _candidate_condition_reconciliation(candidate=candidate, rechecks=rechecks)
        for candidate in base_candidates
    ]
    alignments = [
        alignment
        for candidate in base_candidates
        if (alignment := _composition_alignment(candidate=candidate, patterns_by_table=patterns_by_table, assets=assets))
        is not None
    ]
    if (
        _contains_forbidden_key(rechecks)
        or _contains_forbidden_key(patterns)
        or _contains_forbidden_key(alignments)
        or _contains_forbidden_key(reconciliations)
    ):
        raise AssertionError("context recheck would expose forbidden raw source content")
    summary = {
        "protocol": PROTOCOL,
        "status": "CONTEXT_RECHECK_AND_COMPOSITION_EXPANSION_COMPLETE_NON_PROMOTING",
        "recheck_count": len(rechecks),
        "recheck_status_counts": dict(sorted(Counter(row["recheck_status"] for row in rechecks).items())),
        "component_pattern_table_count": len({row["internal_table_uid"] for row in patterns}),
        "component_pattern_count": len(patterns),
        "composition_alignments": alignments,
        "candidate_condition_reconciliation": reconciliations,
        "authorization": {
            "reviewer_type": "machine_research",
            "human_verified": False,
            "may_authorize_evidence": False,
            "may_authorize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
            "reason": "context and topology checks are research findings, not semantic authorization",
        },
    }
    output_dir.mkdir(parents=True)
    recheck_path = output_dir / "context_rechecks_v1.jsonl"
    pattern_path = output_dir / "loan_provision_component_catalog_v1.jsonl"
    _write_jsonl(recheck_path, rechecks)
    _write_jsonl(pattern_path, patterns)
    summary_path = output_dir / "context_recheck_report_v1.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "inputs": {
            "machine_diagnostic": {"path": str(diagnostic_path), "sha256": sha256_file(diagnostic_path)},
            "typed_operand_plans": {"path": str(plans_path), "sha256": sha256_file(plans_path)},
            "full_table_assets": {"path": str(assets_path), "sha256": sha256_file(assets_path)},
        },
        "outputs": {
            name: {"sha256": sha256_file(output_dir / name), "size_bytes": (output_dir / name).stat().st_size}
            for name in (recheck_path.name, pattern_path.name, summary_path.name)
        },
        "authorization": summary["authorization"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary
