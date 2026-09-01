"""Audit narrowly recognised staged plans without creating an answer path.

This module measures whether an explicit multi-stage question can now be
decomposed into typed source leaves.  It neither retrieves a table nor binds a
cell, calculates a value, produces evidence, or alters E2E eligibility.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.question_compiler import build_typed_operand_plan
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_staged_formula_materialization_audit_v1"
ALLOWED_FORMULAS = frozenset(
    {
        "cfo_positive_multiyear_max_net_margin",
        "debt_to_equity_argmax_interest_coverage",
        "positive_operating_profit_argmin_cfo_ratio_net_margin",
    }
)
CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset(
    {
        "answer",
        "answer_decimal",
        "raw_value",
        "raw_values",
        "cell_value",
        "pandas_query",
        "rows",
        "raw_source_row",
        "raw_source_cell",
        "source_label",
        "human_verified",
    }
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"{path} must contain JSON objects")
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _planner_paths() -> dict[str, Path]:
    root = Path(__file__).resolve().parents[3]
    return {
        "question_compiler": root / "src/finance_query/e2e/question_compiler.py",
        "financial_metrics": root / "src/finance_query/e2e/core/financial_metrics.py",
    }


def _require_base_plan_manifest(path: Path, plans: Path) -> None:
    manifest = _read_json(path)
    expected = ((manifest.get("outputs") or {}).get("sidecar") or {}).get("sha256") or manifest.get("sidecar_sha256")
    if not expected or sha256_file(plans) != expected:
        raise ValueError("base typed plans do not match their manifest")


def _record(
    *,
    question_id: int,
    former: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    formula_id = str(current.get("formula_id") or "")
    matched = formula_id in ALLOWED_FORMULAS
    former_status = str(former.get("decomposition_status") or "")
    current_status = str(current.get("decomposition_status") or "")
    if matched and former_status == "abstain" and current_status == "typed_non_executable":
        status = "NEW_STAGED_PLAN_MATERIALIZED"
    elif matched and former_status == "typed_non_executable" and current_status == "typed_non_executable":
        status = "PREEXISTING_STAGED_PLAN"
    elif matched:
        status = "FORMULA_MATCH_NOT_MATERIALIZED"
    else:
        status = "NO_TARGET_FORMULA_MATCH"
    record = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_id": question_id,
        "audit_status": status,
        "former_decomposition_status": former_status,
        "current_decomposition_status": current_status,
        "formula_id": formula_id or None,
        "operand_count": len(current.get("operands") or []) if matched else 0,
        "stage_count": len((current.get("operation_ast") or {}).get("stages") or []) if matched else 0,
        "source_contract": dict(CONTRACT),
    }
    if _contains_forbidden(record):
        raise ValueError("staged formula audit leaked a forbidden field")
    return record


def build_staged_formula_materialization_audit(
    *,
    base_plans_path: Path,
    base_plans_manifest_path: Path,
    review_items_path: Path,
    entity_aliases_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Generate a value-blind audit of narrowly newly typed staged formulas."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    _require_base_plan_manifest(base_plans_manifest_path, base_plans_path)
    base = {int(row["question_id"]): row for row in _read_jsonl(base_plans_path)}
    items = {int(row["id"]): row for row in _read_jsonl(review_items_path)}
    aliases = _read_jsonl(entity_aliases_path)
    expected_ids = set(range(1, expected_question_count + 1))
    if set(base) != expected_ids or set(items) != expected_ids:
        raise ValueError("staged formula audit inputs need all questions")

    audit: list[dict[str, Any]] = []
    revised_plans: list[dict[str, Any]] = []
    for question_id in range(1, expected_question_count + 1):
        current = build_typed_operand_plan(items[question_id], report_entity_aliases=aliases)
        record = _record(question_id=question_id, former=base[question_id], current=current)
        audit.append(record)
        # Keep the immutable baseline for every non-target.  The sidecar is a
        # narrow research overlay, not a wholesale recompilation of the corpus.
        revised_plans.append(
            current if record["audit_status"] == "NEW_STAGED_PLAN_MATERIALIZED" else base[question_id]
        )

    targets = [record for record in audit if record["audit_status"] == "NEW_STAGED_PLAN_MATERIALIZED"]
    matching = [record for record in audit if record["formula_id"] in ALLOWED_FORMULAS]
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_count": expected_question_count,
        "formula_match_count": len(matching),
        "new_staged_plan_count": len(targets),
        "preexisting_staged_plan_count": sum(
            record["audit_status"] == "PREEXISTING_STAGED_PLAN" for record in audit
        ),
        "new_formula_counts": dict(
            sorted(Counter(str(record["formula_id"]) for record in targets).items())
        ),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "staged_formula_materialization_audit_v1.jsonl"
        targets_path = temporary / "staged_formula_materialization_targets_v1.jsonl"
        plans_path = temporary / "typed_operand_plans.jsonl"
        summary_path = temporary / "staged_formula_materialization_summary_v1.json"
        _write_jsonl(audit_path, audit)
        _write_jsonl(targets_path, targets)
        _write_jsonl(plans_path, revised_plans)
        _write_json(summary_path, summary)
        inputs = {
            "base_plans": base_plans_path,
            "base_plans_manifest": base_plans_manifest_path,
            "review_items": review_items_path,
            "entity_aliases": entity_aliases_path,
            **_planner_paths(),
        }
        manifest = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "inputs": {
                name: {"path": str(path), "sha256": sha256_file(path)}
                for name, path in inputs.items()
            },
            "outputs": {
                "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                "targets": {"path": targets_path.name, "sha256": sha256_file(targets_path)},
                "plans": {"path": plans_path.name, "sha256": sha256_file(plans_path)},
                "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
            },
            "source_contract": dict(CONTRACT),
        }
        _write_json(temporary / "manifest.json", manifest)
        _write_json(
            temporary / "typed_operand_plans.manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": manifest["inputs"],
                "outputs": {"sidecar": {"path": plans_path.name, "sha256": sha256_file(plans_path)}},
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_staged_formula_materialization_audit(
    artifact_dir: Path,
    *,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    """Validate hashes, non-authorizing contract, and deterministic audit logic."""
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected staged formula audit contract")
    for descriptor in (manifest.get("inputs") or {}).values():
        path = Path(str(descriptor.get("path") or ""))
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("staged formula audit input hash mismatch")
    for descriptor in (manifest.get("outputs") or {}).values():
        path = artifact_dir / str(descriptor.get("path") or "")
        if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
            raise ValueError("staged formula audit output hash mismatch")

    audit = _read_jsonl(artifact_dir / "staged_formula_materialization_audit_v1.jsonl")
    targets = _read_jsonl(artifact_dir / "staged_formula_materialization_targets_v1.jsonl")
    plans = _read_jsonl(artifact_dir / "typed_operand_plans.jsonl")
    expected_ids = set(range(1, expected_question_count + 1))
    if {int(record["question_id"]) for record in audit} != expected_ids or len(audit) != expected_question_count:
        raise ValueError("staged formula audit coverage mismatch")
    if any(_contains_forbidden(record) or record.get("source_contract") != CONTRACT for record in [*audit, *targets]):
        raise ValueError("staged formula audit lost its value-blind boundary")
    expected_targets = [record for record in audit if record["audit_status"] == "NEW_STAGED_PLAN_MATERIALIZED"]
    if targets != expected_targets:
        raise ValueError("staged formula targets do not match audit")
    if any(str(record.get("formula_id") or "") not in ALLOWED_FORMULAS for record in targets):
        raise ValueError("unexpected staged formula target")
    if any(
        record["former_decomposition_status"] != "abstain"
        or record["current_decomposition_status"] != "typed_non_executable"
        for record in targets
    ):
        raise ValueError("staged formula target status is unsafe")
    if {int(record["question_id"]) for record in plans} != expected_ids or len(plans) != expected_question_count:
        raise ValueError("staged formula plan overlay coverage mismatch")
    base_path = Path(str(((manifest.get("inputs") or {}).get("base_plans") or {}).get("path") or ""))
    items_path = Path(str(((manifest.get("inputs") or {}).get("review_items") or {}).get("path") or ""))
    aliases_path = Path(str(((manifest.get("inputs") or {}).get("entity_aliases") or {}).get("path") or ""))
    base = {int(record["question_id"]): record for record in _read_jsonl(base_path)}
    items = {int(record["id"]): record for record in _read_jsonl(items_path)}
    aliases = _read_jsonl(aliases_path)
    status_by_id = {int(record["question_id"]): str(record["audit_status"]) for record in audit}
    expected_plans = []
    for question_id in range(1, expected_question_count + 1):
        current = build_typed_operand_plan(items[question_id], report_entity_aliases=aliases)
        expected_plans.append(
            current if status_by_id[question_id] == "NEW_STAGED_PLAN_MATERIALIZED" else base[question_id]
        )
    if plans != expected_plans:
        raise ValueError("staged formula plan overlay differs from narrow deterministic revision")
    sidecar_manifest = _read_json(artifact_dir / "typed_operand_plans.manifest.json")
    sidecar = ((sidecar_manifest.get("outputs") or {}).get("sidecar") or {})
    if sidecar_manifest.get("protocol") != PROTOCOL or sha256_file(artifact_dir / str(sidecar.get("path") or "")) != sidecar.get("sha256"):
        raise ValueError("staged formula sidecar manifest mismatch")
    summary = _read_json(artifact_dir / "staged_formula_materialization_summary_v1.json")
    if summary.get("new_staged_plan_count") != len(targets):
        raise ValueError("staged formula summary target count mismatch")
    return {
        "status": "PASS",
        "question_count": expected_question_count,
        "new_staged_plan_count": len(targets),
        "answer_eligible": False,
        "submission_eligible": False,
    }
