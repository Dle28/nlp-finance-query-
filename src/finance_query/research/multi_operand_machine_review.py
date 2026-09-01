"""Machine-only research review for a reviewed multi-operand source selection.

The module audits selected source coordinates and may propose a narrower cell
set when an exact row label is uniquely stronger than a selected ``khác``
variant.  It never emits source values, executes a financial result, or grants
evidence, answer, training, promotion, release, or submission authority.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from finance_query.e2e.core.financial_taxonomy import normalize_label
from finance_query.research.exact_cell_research import _period_unit_diagnostic


PROTOCOL = "vifinqa_multi_operand_machine_review_v1"
_NUMERIC_TOKEN = re.compile(r"\d")
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_TOKENS = frozenset({"cac", "cua", "cho", "cong", "ty", "nam", "o", "muc", "gia", "tri", "chi", "phi", "va"})
_FORBIDDEN_KEYS = frozenset({"answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query", "rows"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _tokens(value: object) -> set[str]:
    return {token for token in _TOKEN.findall(normalize_label(str(value))) if token not in _STOP_TOKENS}


def _asset_locator(asset: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_path": str(asset["source_path"]),
        "source_sha256": str(asset["source_sha256"]),
        "table_sha256": str(asset["table_sha256"]),
        "local_ordinal": int(asset["local_ordinal"]),
        "char_start": int(asset["char_start"]),
        "page_no": asset.get("page_no"),
    }


def _load_assets(path: Path, selected_uids: set[str]) -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            asset = json.loads(line)
            uid = str(asset.get("internal_table_uid") or "")
            if uid in selected_uids:
                if uid in assets:
                    raise ValueError(f"duplicate selected table UID in full-table assets: {uid}")
                assets[uid] = asset
    missing = selected_uids - set(assets)
    if missing:
        raise ValueError(f"selected table UID missing from full-table assets: {sorted(missing)[:3]}")
    return assets


def _plans(path: Path) -> dict[int, dict[str, Any]]:
    result = {int(row["question_id"]): row for row in _load_jsonl(path)}
    if not result:
        raise ValueError("typed operand plans are empty")
    return result


def _validate_intake_lineage(*, intake_dir: Path, decision_path: Path) -> dict[str, Any]:
    manifest_path = intake_dir / "manifest.json"
    receipt_path = intake_dir / "review_intake_receipt_v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != "vifinqa_multi_operand_review_intake_v1" or receipt.get("status") != "ACCEPTED_NON_PROMOTING":
        raise ValueError("machine review requires an accepted non-promoting multi-operand intake")
    if (manifest.get("inputs") or {}).get("decisions", {}).get("sha256") != sha256_file(decision_path):
        raise ValueError("machine review decisions do not match the accepted intake")
    if receipt.get("authorization", {}).get("may_authorize_answer") is not False:
        raise ValueError("machine review cannot use an authorizing intake")
    return receipt


def _selected_cells(value: Mapping[str, Any], *, label: str) -> list[dict[str, int]]:
    cells = value.get("selected_cells")
    if not isinstance(cells, list) or not cells:
        raise ValueError(f"{label} has no selected cells")
    result: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for cell in cells:
        if not isinstance(cell, Mapping):
            raise ValueError(f"{label} selected cell is not an object")
        row_index = cell.get("row_index")
        column_index = cell.get("column_index")
        if not isinstance(row_index, int) or not isinstance(column_index, int):
            raise ValueError(f"{label} selected cell is not a coordinate")
        if (row_index, column_index) in seen:
            raise ValueError(f"{label} repeats a selected cell")
        seen.add((row_index, column_index))
        result.append({"row_index": row_index, "column_index": column_index})
    return result


def _cell_label_features(asset: Mapping[str, Any], cell: Mapping[str, int], query_tokens: set[str]) -> dict[str, Any]:
    rows = asset.get("rows") or []
    row_index, column_index = cell["row_index"], cell["column_index"]
    if not 0 <= row_index < len(rows) or not isinstance(rows[row_index], list) or not 0 <= column_index < len(rows[row_index]):
        raise ValueError("selected cell is outside the full-table asset")
    raw_cell = str(rows[row_index][column_index])
    if not _NUMERIC_TOKEN.search(raw_cell):
        raise ValueError("selected cell is not numeric in the full-table asset")
    label = str(rows[row_index][0] if rows[row_index] else "")
    label_tokens = _tokens(label)
    coverage = len(query_tokens & label_tokens) / max(1, len(query_tokens))
    return {
        "row_index": row_index,
        "column_index": column_index,
        "row_label_sha256": hashlib.sha256(label.encode("utf-8")).hexdigest(),
        "metric_token_coverage": round(coverage, 6),
        "extra_metric_token_count": len(label_tokens - query_tokens),
        "row_is_ocr_header": row_index in {int(value) for value in asset.get("header_row_indices") or []},
    }


def _unique_exact_reduction(features: list[dict[str, Any]]) -> list[dict[str, int]] | None:
    if len(features) < 2:
        return None
    ordered = sorted(
        features,
        key=lambda value: (-float(value["metric_token_coverage"]), int(value["extra_metric_token_count"]), int(value["row_index"])),
    )
    best, runner_up = ordered[0], ordered[1]
    if (
        best["metric_token_coverage"] == 1.0
        and (
            best["metric_token_coverage"] > runner_up["metric_token_coverage"]
            or best["extra_metric_token_count"] < runner_up["extra_metric_token_count"]
        )
    ):
        return [{"row_index": int(best["row_index"]), "column_index": int(best["column_index"])}]
    return None


def _composition_hypothesis(
    *, asset: Mapping[str, Any], selected: list[dict[str, int]], question: str
) -> dict[str, Any] | None:
    """Flag, but never apply, a common-plus-specific provision hypothesis."""
    if len(selected) != 1 or "du phong rui ro tin dung" not in normalize_label(question):
        return None
    selected_row = str((asset.get("rows") or [])[selected[0]["row_index"]][0])
    selected_label = normalize_label(selected_row)
    if "du phong cu the" not in selected_label or "cho vay khach hang" not in selected_label:
        return None
    column_index = selected[0]["column_index"]
    for row_index, row in enumerate(asset.get("rows") or []):
        if not isinstance(row, list) or row_index == selected[0]["row_index"] or column_index >= len(row):
            continue
        label = normalize_label(str(row[0] if row else ""))
        if "du phong chung" in label and "cho vay khach hang" in label and _NUMERIC_TOKEN.search(str(row[column_index])):
            return {
                "status": "NEEDS_COMPOSITION_HYPOTHESIS",
                "operator_candidate": "sum",
                "sibling_row_index": row_index,
                "sibling_column_index": column_index,
                "reason": "BROAD_CREDIT_RISK_METRIC_HAS_COMMON_AND_SPECIFIC_LOAN_COMPONENTS",
            }
    return None


def build_multi_operand_machine_review(
    *,
    intake_dir: Path,
    decision_path: Path,
    plans_path: Path,
    assets_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Write non-authorizing machine review and cell-reduction proposals."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    _validate_intake_lineage(intake_dir=intake_dir, decision_path=decision_path)
    decisions = _load_jsonl(decision_path)
    plans = _plans(plans_path)
    approved = [decision for decision in decisions if decision.get("overall_decision") == "APPROVE"]
    if not approved:
        raise ValueError("machine review requires at least one approved source-selection record")
    selected_uids = {
        str(operand.get("selected_internal_table_uid"))
        for decision in approved
        for operand in decision.get("operand_decisions") or []
        if isinstance(operand, Mapping)
    }
    assets = _load_assets(assets_path, selected_uids)
    reviews: list[dict[str, Any]] = []
    question_actions: dict[int, list[str]] = {}
    for decision in approved:
        question_id = decision.get("question_id")
        if not isinstance(question_id, int) or question_id not in plans:
            raise ValueError("approved decision is absent from typed operand plans")
        plan = plans[question_id]
        plan_operands = {str(operand.get("operand_id")): operand for operand in plan.get("operands") or []}
        for selected in decision.get("operand_decisions") or []:
            if not isinstance(selected, Mapping):
                raise ValueError(f"Q{question_id} has an invalid selected operand")
            operand_id = str(selected.get("operand_id") or "")
            planned = plan_operands.get(operand_id)
            if not isinstance(planned, Mapping):
                raise ValueError(f"Q{question_id} {operand_id} is absent from its typed plan")
            table_uid = str(selected.get("selected_internal_table_uid") or "")
            asset = assets.get(table_uid)
            if asset is None:
                raise ValueError(f"Q{question_id} {operand_id} selected asset is missing")
            if (
                selected.get("selected_source_sha256") != asset.get("source_sha256")
                or selected.get("selected_table_sha256") != asset.get("table_sha256")
                or selected.get("selected_exact_table_locator_sha256") != _canonical_sha(_asset_locator(asset))
            ):
                raise ValueError(f"Q{question_id} {operand_id} source identity mismatch")
            selected_cells = _selected_cells(selected, label=f"Q{question_id} {operand_id}")
            metric_hints = planned.get("metric_hints") or []
            metric_query = str(metric_hints[0] if metric_hints else plan.get("question") or "")
            features = [_cell_label_features(asset, cell, _tokens(metric_query)) for cell in selected_cells]
            reduction = _unique_exact_reduction(features)
            diagnostic = _period_unit_diagnostic(
                asset,
                requested_year=int((planned.get("years") or [0])[0]),
                requested_scope=planned.get("scope"),
            )
            composition = _composition_hypothesis(
                asset=asset, selected=selected_cells, question=str(plan.get("question") or "")
            )
            if composition is not None:
                status = "NEEDS_COMPOSITION_HYPOTHESIS"
                recommended_cells = selected_cells
                action = "KEEP_CURRENT_CELL_PENDING_COMPONENT_FORMULA"
            elif reduction is not None:
                status = "AUTO_REVISED_EXACT_LABEL"
                recommended_cells = reduction
                action = "REDUCE_TO_UNIQUE_EXACT_LABEL"
            else:
                status = "MACHINE_PROVISIONAL"
                recommended_cells = selected_cells
                action = "KEEP_SELECTED_CELLS"
            question_actions.setdefault(question_id, []).append(status)
            reviews.append(
                {
                    "question_id": question_id,
                    "operand_id": operand_id,
                    "route_id": str(selected.get("route_id") or ""),
                    "internal_table_uid": table_uid,
                    "selected_cells": selected_cells,
                    "selected_cell_features": features,
                    "machine_action": action,
                    "recommended_cells": recommended_cells,
                    "machine_review_status": status,
                    "period_diagnostic": (
                        "EXPLICIT_YEAR_HEADER"
                        if {cell["column_index"] for cell in recommended_cells}.issubset(set(diagnostic["matching_year_column_indices"]))
                        else "FALLBACK_OR_AMBIGUOUS_PERIOD"
                    ),
                    "unit_diagnostic": str(diagnostic["unit_status"]),
                    "ocr_header_recovery_used": any(feature["row_is_ocr_header"] for feature in features),
                    "composition_hypothesis": composition,
                    "numeric_parse_or_execution": "NOT_RUN_BY_MACHINE_RESEARCH_REVIEW",
                }
            )
    status_counts: dict[str, int] = {}
    for review in reviews:
        status = str(review["machine_review_status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    question_summary = []
    for question_id, statuses in sorted(question_actions.items()):
        question_summary.append(
            {
                "question_id": question_id,
                "machine_research_status": (
                    "NEEDS_COMPOSITION_HYPOTHESIS"
                    if "NEEDS_COMPOSITION_HYPOTHESIS" in statuses
                    else "AUTO_ADJUSTMENT_PROPOSED"
                    if "AUTO_REVISED_EXACT_LABEL" in statuses
                    else "MACHINE_PROVISIONAL"
                ),
            }
        )
    report = {
        "protocol": PROTOCOL,
        "status": "MACHINE_RESEARCH_REVIEW_COMPLETE_NON_PROMOTING",
        "review_count": len(reviews),
        "status_counts": dict(sorted(status_counts.items())),
        "question_summary": question_summary,
        "operand_reviews": reviews,
        "authorization": {
            "reviewer_type": "machine_research",
            "human_verified": False,
            "may_authorize_evidence": False,
            "may_authorize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
            "reason": "machine research may propose source corrections but cannot authorize answers or submissions",
        },
    }
    if _contains_forbidden_key(report):
        raise AssertionError("machine research review would expose a forbidden financial-value field")
    proposals = [
        {
            "protocol": "vifinqa_multi_operand_machine_adjustment_proposal_v1",
            "question_id": review["question_id"],
            "operand_id": review["operand_id"],
            "route_id": review["route_id"],
            "internal_table_uid": review["internal_table_uid"],
            "prior_selected_cells": review["selected_cells"],
            "proposed_selected_cells": review["recommended_cells"],
            "machine_action": review["machine_action"],
            "machine_review_status": review["machine_review_status"],
            "composition_hypothesis": review["composition_hypothesis"],
            "machine_research_only": True,
            "may_authorize_evidence": False,
            "may_authorize_answer": False,
            "training_eligible": False,
            "submission_eligible": False,
        }
        for review in reviews
    ]
    if _contains_forbidden_key(proposals):
        raise AssertionError("machine adjustment proposal would expose a forbidden financial-value field")
    output_dir.mkdir(parents=True)
    report_path = output_dir / "machine_research_review_v1.json"
    proposal_path = output_dir / "machine_adjustment_proposals_v1.jsonl"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_jsonl(proposal_path, proposals)
    manifest = {
        "protocol": PROTOCOL,
        "schema_version": 1,
        "inputs": {
            "review_intake_manifest": {"path": str(intake_dir / "manifest.json"), "sha256": sha256_file(intake_dir / "manifest.json")},
            "review_intake_receipt": {"path": str(intake_dir / "review_intake_receipt_v1.json"), "sha256": sha256_file(intake_dir / "review_intake_receipt_v1.json")},
            "decisions": {"path": str(decision_path), "sha256": sha256_file(decision_path)},
            "typed_operand_plans": {"path": str(plans_path), "sha256": sha256_file(plans_path)},
            "full_table_assets": {"path": str(assets_path), "sha256": sha256_file(assets_path)},
        },
        "outputs": {
            "machine_research_review_v1.json": {"sha256": sha256_file(report_path), "size_bytes": report_path.stat().st_size},
            "machine_adjustment_proposals_v1.jsonl": {"sha256": sha256_file(proposal_path), "size_bytes": proposal_path.stat().st_size},
        },
        "authorization": report["authorization"],
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
