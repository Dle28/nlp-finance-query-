"""Compute non-authorizing numeric diagnostics from machine-reviewed sources.

Candidate values here are for research and evaluation experiments only.  They
are deliberately not evidence bindings, answers, certificates, release input,
or a competition submission.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from finance_query.e2e.decimal_executor import convert_unit, execute_ast, parse_decimal
from finance_query.research.exact_cell_research import _period_unit_diagnostic
from finance_query.research.multi_operand_machine_review import sha256_file


PROTOCOL = "vifinqa_multi_operand_machine_diagnostic_v1"
_SOURCE_TO_EXECUTION_UNIT = {
    "vnd": "vnd",
    "nghin_dong": "thousand_vnd",
    "trieu_dong": "million_vnd",
    "ty_dong": "billion_vnd",
    "nghin_ty_dong": "trillion_vnd",
}
_FORBIDDEN_KEYS = frozenset({"answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query", "rows"})


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


def _load_assets(path: Path, table_uids: set[str]) -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            asset = json.loads(line)
            uid = str(asset.get("internal_table_uid") or "")
            if uid in table_uids:
                if uid in assets:
                    raise ValueError(f"duplicate table UID in full-table assets: {uid}")
                assets[uid] = asset
    missing = table_uids - set(assets)
    if missing:
        raise ValueError(f"machine diagnostic cannot find source table: {sorted(missing)[:3]}")
    return assets


def _plans(path: Path) -> dict[int, dict[str, Any]]:
    return {int(record["question_id"]): record for record in _load_jsonl(path)}


def _load_machine_review(machine_review_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_path = machine_review_dir / "manifest.json"
    review_path = machine_review_dir / "machine_research_review_v1.json"
    proposal_path = machine_review_dir / "machine_adjustment_proposals_v1.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != "vifinqa_multi_operand_machine_review_v1" or review.get("status") != "MACHINE_RESEARCH_REVIEW_COMPLETE_NON_PROMOTING":
        raise ValueError("unexpected machine-review lineage")
    outputs = manifest.get("outputs") or {}
    if outputs.get("machine_research_review_v1.json", {}).get("sha256") != sha256_file(review_path):
        raise ValueError("machine-review report hash mismatch")
    if outputs.get("machine_adjustment_proposals_v1.jsonl", {}).get("sha256") != sha256_file(proposal_path):
        raise ValueError("machine-adjustment proposal hash mismatch")
    authorization = review.get("authorization") or {}
    if authorization.get("reviewer_type") != "machine_research" or authorization.get("submission_eligible") is not False:
        raise ValueError("machine-review lineage is not non-authorizing")
    return review, _load_jsonl(proposal_path)


def _source_unit(
    *, asset: Mapping[str, Any], planned: Mapping[str, Any], selected_cells: list[Mapping[str, int]]
) -> tuple[str, list[str], dict[str, Any]]:
    diagnostic = _period_unit_diagnostic(
        asset,
        requested_year=int((planned.get("years") or [0])[0]),
        requested_scope=planned.get("scope"),
    )
    source_candidate = _SOURCE_TO_EXECUTION_UNIT.get(str(diagnostic.get("source_unit_candidate") or ""))
    if source_candidate:
        return source_candidate, [], diagnostic
    requested = str((planned.get("unit_contract") or {}).get("requested_unit") or "")
    if requested not in {"vnd", "thousand_vnd", "million_vnd", "billion_vnd", "trillion_vnd"}:
        raise ValueError("source unit missing and requested unit cannot be used as an explicit research assumption")
    return requested, ["ASSUMED_REQUESTED_UNIT_WHEN_SOURCE_HEADER_MISSING"], diagnostic


def _operand_value(
    *, asset: Mapping[str, Any], planned: Mapping[str, Any], cells: list[Mapping[str, int]], target_unit: str
) -> tuple[Decimal, dict[str, Any]]:
    if not cells:
        raise ValueError("machine diagnostic operand has no selected cells")
    source_unit, warnings, period_unit = _source_unit(
        asset=asset, planned=planned, selected_cells=cells
    )
    conversion_assumption = None
    if source_unit != target_unit and not bool((planned.get("unit_contract") or {}).get("conversion_allowed")):
        conversion_assumption = "RESEARCH_CONVERSION_OUTSIDE_PLAN_CONTRACT"
    total = Decimal("0")
    parse_warnings: list[str] = []
    rows = asset.get("rows") or []
    for cell in cells:
        row_index, column_index = int(cell["row_index"]), int(cell["column_index"])
        if not 0 <= row_index < len(rows) or not isinstance(rows[row_index], list) or not 0 <= column_index < len(rows[row_index]):
            raise ValueError("machine diagnostic cell is outside its source table")
        parsed = parse_decimal(rows[row_index][column_index])
        if parsed.value is None:
            raise ValueError("machine diagnostic cannot parse a selected source value")
        total += Decimal(parsed.value)
        parse_warnings.extend(parsed.warnings)
    return convert_unit(total, source_unit, target_unit), {
        "selected_cell_count": len(cells),
        "source_unit": source_unit,
        "period_status": period_unit["period_status"],
        "fallback_period_status": period_unit["fallback_period_status"],
        "scope_status": period_unit["scope_status"],
        "unit_assumptions": warnings,
        "conversion_assumption": conversion_assumption,
        "numeric_parse_warnings": sorted(set(parse_warnings)),
    }


def _candidate(
    *,
    question_id: int,
    variant: str,
    plan: Mapping[str, Any],
    proposals: Mapping[str, Mapping[str, Any]],
    assets: Mapping[str, Mapping[str, Any]],
    composition_override: Mapping[str, list[dict[str, int]]] | None = None,
) -> dict[str, Any]:
    target_unit = str(plan.get("requested_unit") or "")
    if target_unit not in {"vnd", "thousand_vnd", "million_vnd", "billion_vnd", "trillion_vnd"}:
        raise ValueError(f"Q{question_id} has unsupported requested unit for machine diagnostic")
    values: dict[str, Decimal] = {}
    operand_receipts: list[dict[str, Any]] = []
    assumptions: list[str] = []
    for planned in plan.get("operands") or []:
        if planned.get("required") is not True:
            continue
        operand_id = str(planned.get("operand_id") or "")
        proposal = proposals.get(operand_id)
        if proposal is None:
            raise ValueError(f"Q{question_id} has no machine-adjustment proposal for {operand_id}")
        cells = (composition_override or {}).get(operand_id, proposal.get("proposed_selected_cells") or [])
        table_uid = str(proposal.get("internal_table_uid") or "")
        value, receipt = _operand_value(
            asset=assets[table_uid], planned=planned, cells=cells, target_unit=target_unit
        )
        values[operand_id] = value
        assumptions.extend(receipt["unit_assumptions"])
        if receipt["conversion_assumption"]:
            assumptions.append(receipt["conversion_assumption"])
        if receipt["numeric_parse_warnings"]:
            assumptions.append("NUMERIC_PARSE_WARNING")
        operand_receipts.append(
            {
                "operand_id": operand_id,
                "internal_table_uid": table_uid,
                "selected_cells": cells,
                "selected_cell_count": receipt["selected_cell_count"],
                "source_unit": receipt["source_unit"],
                "period_status": receipt["period_status"],
                "fallback_period_status": receipt["fallback_period_status"],
                "scope_status": receipt["scope_status"],
                "unit_assumptions": receipt["unit_assumptions"],
                "conversion_assumption": receipt["conversion_assumption"],
                "numeric_parse_warnings": receipt["numeric_parse_warnings"],
            }
        )
    result = execute_ast(dict(plan.get("operation_ast") or {}), values)
    if not isinstance(result, Decimal):
        result = Decimal(str(result))
    return {
        "protocol": PROTOCOL,
        "candidate_id": hashlib.sha256(
            json.dumps({"question_id": question_id, "variant": variant, "operands": operand_receipts}, sort_keys=True).encode()
        ).hexdigest(),
        "question_id": question_id,
        "variant": variant,
        "operation": str((plan.get("operation_ast") or {}).get("op") or ""),
        "diagnostic_result": format(result, "f"),
        "output_unit": target_unit,
        "operands": operand_receipts,
        "diagnostic_status": "MACHINE_RESEARCH_CANDIDATE",
        "assumption_codes": sorted(set(assumptions)),
        "may_authorize_evidence": False,
        "may_authorize_answer": False,
        "training_eligible": False,
        "submission_eligible": False,
    }


def _evaluation_recommendation(
    *, summary: Mapping[str, Any], candidates: list[Mapping[str, Any]]
) -> dict[str, Any]:
    """Select a primary experiment without claiming an answer is authorized."""
    question_id = int(summary["question_id"])
    status = str(summary.get("machine_research_status") or "")
    baseline = next(candidate for candidate in candidates if candidate["variant"] == "machine_adjusted_selection")
    if status == "NEEDS_COMPOSITION_HYPOTHESIS":
        primary = next(
            candidate
            for candidate in candidates
            if candidate["variant"] == "composition_hypothesis_common_plus_specific"
        )
        return {
            "question_id": question_id,
            "machine_research_status": status,
            "primary_candidate_id": primary["candidate_id"],
            "primary_variant": primary["variant"],
            "evaluation_priority": "COMPARE_PRIMARY_WITH_BASELINE",
            "comparison_candidate_id": baseline["candidate_id"],
            "conditions": sorted(
                {
                    condition
                    for candidate in candidates
                    for condition in candidate["assumption_codes"]
                }
            ),
            "reason": "the source table exposes a common-plus-specific component hypothesis; evaluate both variants",
        }
    conditions = sorted(
        {
            condition
            for candidate in candidates
            for condition in candidate["assumption_codes"]
        }
    )
    return {
        "question_id": question_id,
        "machine_research_status": status,
        "primary_candidate_id": baseline["candidate_id"],
        "primary_variant": baseline["variant"],
        "evaluation_priority": "CONDITIONAL_SINGLE_CANDIDATE",
        "conditions": conditions,
        "reason": "single machine-research candidate; retain its listed conditions during evaluation",
    }


def build_multi_operand_machine_diagnostic(
    *, machine_review_dir: Path, plans_path: Path, assets_path: Path, output_dir: Path
) -> dict[str, Any]:
    """Calculate evaluation candidates without turning them into answers."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    review, all_proposals = _load_machine_review(machine_review_dir)
    plans = _plans(plans_path)
    proposal_by_question: dict[int, dict[str, dict[str, Any]]] = {}
    for proposal in all_proposals:
        question_id = int(proposal["question_id"])
        proposal_by_question.setdefault(question_id, {})[str(proposal["operand_id"])] = proposal
    table_uids = {str(proposal["internal_table_uid"]) for proposal in all_proposals}
    assets = _load_assets(assets_path, table_uids)
    candidates: list[dict[str, Any]] = []
    evaluation_recommendations: list[dict[str, Any]] = []
    for summary in review.get("question_summary") or []:
        question_id = int(summary["question_id"])
        plan = plans.get(question_id)
        if plan is None:
            raise ValueError(f"machine review question Q{question_id} is absent from typed plans")
        proposals = proposal_by_question.get(question_id, {})
        question_candidates = [
            _candidate(
                question_id=question_id,
                variant="machine_adjusted_selection",
                plan=plan,
                proposals=proposals,
                assets=assets,
            )
        ]
        if summary.get("machine_research_status") == "NEEDS_COMPOSITION_HYPOTHESIS":
            component = next(
                proposal for proposal in proposals.values() if proposal.get("composition_hypothesis")
            )
            hypothesis = component["composition_hypothesis"]
            selected = component.get("proposed_selected_cells") or []
            override = {
                str(component["operand_id"]): [
                    *selected,
                    {
                        "row_index": int(hypothesis["sibling_row_index"]),
                        "column_index": int(hypothesis["sibling_column_index"]),
                    },
                ]
            }
            question_candidates.append(
                _candidate(
                    question_id=question_id,
                    variant="composition_hypothesis_common_plus_specific",
                    plan=plan,
                    proposals=proposals,
                    assets=assets,
                    composition_override=override,
                )
            )
        candidates.extend(question_candidates)
        evaluation_recommendations.append(
            _evaluation_recommendation(summary=summary, candidates=question_candidates)
        )
    if _contains_forbidden_key(candidates):
        raise AssertionError("machine diagnostic would expose forbidden raw source content")
    report = {
        "protocol": PROTOCOL,
        "status": "MACHINE_DIAGNOSTIC_CANDIDATES_COMPLETE_NON_PROMOTING",
        "candidate_count": len(candidates),
        "question_ids": sorted({candidate["question_id"] for candidate in candidates}),
        "candidates": candidates,
        "evaluation_recommendations": evaluation_recommendations,
        "authorization": {
            "reviewer_type": "machine_research",
            "human_verified": False,
            "may_authorize_evidence": False,
            "may_authorize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
            "reason": "numeric diagnostics are evaluation candidates, not authorized answers or submission output",
        },
    }
    output_dir.mkdir(parents=True)
    report_path = output_dir / "machine_diagnostic_candidates_v1.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_jsonl(output_dir / "machine_diagnostic_candidates_v1.jsonl", candidates)
    manifest = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "inputs": {
            "machine_review_manifest": {"path": str(machine_review_dir / "manifest.json"), "sha256": sha256_file(machine_review_dir / "manifest.json")},
            "machine_review_report": {"path": str(machine_review_dir / "machine_research_review_v1.json"), "sha256": sha256_file(machine_review_dir / "machine_research_review_v1.json")},
            "machine_adjustment_proposals": {"path": str(machine_review_dir / "machine_adjustment_proposals_v1.jsonl"), "sha256": sha256_file(machine_review_dir / "machine_adjustment_proposals_v1.jsonl")},
            "typed_operand_plans": {"path": str(plans_path), "sha256": sha256_file(plans_path)},
            "full_table_assets": {"path": str(assets_path), "sha256": sha256_file(assets_path)},
        },
        "outputs": {
            name: {"sha256": sha256_file(output_dir / name), "size_bytes": (output_dir / name).stat().st_size}
            for name in ("machine_diagnostic_candidates_v1.json", "machine_diagnostic_candidates_v1.jsonl")
        },
        "authorization": report["authorization"],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
