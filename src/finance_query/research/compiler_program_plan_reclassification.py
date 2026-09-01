"""Freeze narrowly repaired compiler outputs for already-defined formulas."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.question_compiler import build_typed_operand_plan
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_compiler_program_plan_reclassification_v1"
ALLOWED_FORMULAS = frozenset({"percentage_change", "net_other_income"})
CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}
FORBIDDEN = frozenset({
    "answer", "answer_decimal", "raw_value", "raw_values", "cell_value", "pandas_query",
    "rows", "raw_source_row", "raw_source_cell", "source_label", "human_verified",
})


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"{path} must contain JSON objects")
    return values


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
    )


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in FORBIDDEN or _contains_forbidden(child) for key, child in value.items())
    return any(_contains_forbidden(child) for child in value) if isinstance(value, list) else False


def _require_base_plan_manifest(path: Path, plans: Path) -> None:
    manifest = _read_json(path)
    expected = ((manifest.get("outputs") or {}).get("sidecar") or {}).get("sha256") or manifest.get("sidecar_sha256")
    if not expected or sha256_file(plans) != expected:
        raise ValueError("base typed plans do not match their manifest")


def _eligible(base: Mapping[str, Any], revised: Mapping[str, Any]) -> bool:
    formula_id = str(revised.get("formula_id") or "")
    ast = revised.get("operation_ast") or {}
    return bool(
        base.get("decomposition_status") == "typed_non_executable"
        and revised.get("decomposition_status") == "complete"
        and formula_id in ALLOWED_FORMULAS
        and isinstance(ast, Mapping)
        and str(ast.get("op") or "") in {"percentage_change", "subtract"}
        and len(revised.get("operands") or []) == 2
    )


def build_compiler_program_plan_reclassification(
    *,
    base_plans_path: Path,
    base_plans_manifest_path: Path,
    review_items_path: Path,
    entity_aliases_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    _require_base_plan_manifest(base_plans_manifest_path, base_plans_path)
    base = {int(row["question_id"]): row for row in _read_jsonl(base_plans_path)}
    items = {int(row["id"]): row for row in _read_jsonl(review_items_path)}
    aliases = _read_jsonl(entity_aliases_path)
    expected_ids = set(range(1, expected_question_count + 1))
    if set(base) != expected_ids or set(items) != expected_ids:
        raise ValueError("compiler reclassification inputs need all questions")
    plans: dict[int, dict[str, Any]] = {}
    targets: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    for question_id in range(1, expected_question_count + 1):
        recomputed = build_typed_operand_plan(items[question_id], report_entity_aliases=aliases)
        changed = _eligible(base[question_id], recomputed)
        plans[question_id] = recomputed if changed else base[question_id]
        if changed:
            targets.append(
                {
                    "schema_version": 1,
                    "protocol": PROTOCOL,
                    "question_id": question_id,
                    "formula_id": recomputed["formula_id"],
                    "source_contract": dict(CONTRACT),
                }
            )
        record = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "reclassification_status": "COMPILER_PROGRAM_NOW_EXECUTABLE" if changed else "UNCHANGED",
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(record):
            raise ValueError("compiler reclassification audit leaked an unsafe field")
        audit.append(record)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "question_count": expected_question_count,
        "reclassified_question_count": len(targets),
        "formula_counts": dict(sorted(Counter(row["formula_id"] for row in targets).items())),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        plans_path = temporary / "typed_operand_plans.jsonl"
        targets_path = temporary / "compiler_program_targets_v1.jsonl"
        audit_path = temporary / "compiler_program_reclassification_audit_v1.jsonl"
        summary_path = temporary / "compiler_program_reclassification_summary_v1.json"
        _write_jsonl(plans_path, (plans[question_id] for question_id in range(1, expected_question_count + 1)))
        _write_jsonl(targets_path, targets)
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {"base_plans": base_plans_path, "base_plans_manifest": base_plans_manifest_path, "review_items": review_items_path, "entity_aliases": entity_aliases_path}
        common = {"schema_version": 1, "protocol": PROTOCOL, "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()}, "source_contract": dict(CONTRACT)}
        _write_json(temporary / "typed_operand_plans.manifest.json", {**common, "outputs": {"sidecar": {"path": plans_path.name, "sha256": sha256_file(plans_path)}}})
        _write_json(temporary / "manifest.json", {**common, "outputs": {"plans": {"path": plans_path.name, "sha256": sha256_file(plans_path)}, "targets": {"path": targets_path.name, "sha256": sha256_file(targets_path)}, "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)}, "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)}}})
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_compiler_program_plan_reclassification(artifact_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected compiler-program reclassification protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("compiler-program reclassification hash mismatch")
    plans = {int(row["question_id"]): row for row in _read_jsonl(artifact_dir / "typed_operand_plans.jsonl")}
    targets = _read_jsonl(artifact_dir / "compiler_program_targets_v1.jsonl")
    audit = _read_jsonl(artifact_dir / "compiler_program_reclassification_audit_v1.jsonl")
    expected_ids = set(range(1, expected_question_count + 1))
    target_ids = {int(row["question_id"]) for row in targets}
    if set(plans) != expected_ids or {int(row["question_id"]) for row in audit} != expected_ids or len(target_ids) != len(targets):
        raise ValueError("compiler-program reclassification coverage mismatch")
    if any(_contains_forbidden(row) or row.get("source_contract") != CONTRACT for row in [*targets, *audit]):
        raise ValueError("compiler-program reclassification lost non-authorizing boundary")
    if any(not _eligible({"decomposition_status": "typed_non_executable"}, plans[question_id]) for question_id in target_ids):
        raise ValueError("compiler-program target is not a supported executable formula")
    summary = _read_json(artifact_dir / "compiler_program_reclassification_summary_v1.json")
    if summary.get("reclassified_question_count") != len(target_ids):
        raise ValueError("compiler-program reclassification summary mismatch")
    return {"status": "PASS", "question_count": expected_question_count, "reclassified_question_count": len(target_ids), "answer_eligible": False, "submission_eligible": False}
