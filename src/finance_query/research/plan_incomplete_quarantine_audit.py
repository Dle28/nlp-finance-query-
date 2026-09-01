"""Give every missing-operand plan a bounded, reproducible quarantine reason."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping

from finance_query.e2e.question_compiler import COMPLEX_STAGE_RE
from finance_query.research.machine_exact_cell_proposals import sha256_file


PROTOCOL = "vifinqa_plan_incomplete_quarantine_audit_v1"
TARGET_BLOCKER = "QUESTION_TO_OPERAND_PLAN_INCOMPLETE"
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


def _require_output(manifest_path: Path, output_name: str, path: Path) -> None:
    manifest = _read_json(manifest_path)
    expected = ((manifest.get("outputs") or {}).get(output_name) or {}).get("sha256")
    if not expected or sha256_file(path) != expected:
        raise ValueError(f"{path} does not match {manifest_path} output {output_name}")


def _reason(plan: Mapping[str, Any], probe: Mapping[str, Any] | None) -> tuple[str, str]:
    if probe is not None:
        status = str(probe.get("quarantine_status") or "")
        if status == "ALL_OPERANDS_STRICT_SOURCE_READY_BUT_ROUTE_NOT_MATERIALIZED":
            return "READY_FOR_CONTROLLED_ROUTE_IMPLEMENTATION", "TWO_OPERAND_SOURCE_CELLS_UNIQUE"
        return "STRICT_SOURCE_GATES_INCOMPLETE", "TWO_PERIOD_OPERAND_SOURCE_NOT_UNIQUE_OR_MISSING"
    question = str(plan.get("question") or "")
    entities, years = list(plan.get("entities") or []), list(plan.get("years") or [])
    family = str(plan.get("effective_family") or "")
    if COMPLEX_STAGE_RE.search(question):
        return "CONTROLLED_MULTI_STAGE_GRAPH_REQUIRED", "FILTER_SELECTOR_OR_SECOND_TARGET_METRIC_NOT_EXPLICITLY_COMPILED"
    if not entities:
        return "ENTITY_NOT_RESOLVED_FOR_OPERAND", "NO_SINGLE_ISSUER_BOUND_TO_LEAF_OPERANDS"
    if len(entities) >= 2 and len(years) >= 2:
        return "ENTITY_PERIOD_ASSOCIATION_AMBIGUOUS", "QUESTION_DOES_NOT_PERMIT_A_CARTESIAN_OPERAND_GRID"
    if family == "ratio_or_derived":
        return "CONTROLLED_FINANCIAL_DEFINITION_REQUIRED", "NUMERATOR_DENOMINATOR_OR_REPORTED_ROW_SEMANTICS_NOT_EXPLICIT"
    if len(entities) >= 2:
        return "MULTI_ENTITY_OPERAND_BINDING_REQUIRED", "ENTITY_LIST_OR_COMPARISON_DIRECTION_NEEDS_EXPLICIT_LEAVES"
    if len(years) >= 2:
        return "TEMPORAL_OPERATION_OR_METRIC_BOUNDARY_REQUIRED", "QUESTION_HAS_MULTIPLE_PERIODS_BUT_NOT_A_NARROW_SAFE_OPERATOR_PATTERN"
    return "OPERAND_CONCEPT_NOT_EXPLICIT", "NO_SINGLE_REPORT_ROW_OR_CONTROLLED_FORMULA_CAN_BE_BOUND"


def build_plan_incomplete_quarantine_audit(
    *,
    triage_path: Path,
    plans_path: Path,
    temporal_probe_dir: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    _require_output(temporal_probe_dir / "manifest.json", "audit", temporal_probe_dir / "simple_temporal_operator_source_probe_v1.jsonl")
    triage = _read_jsonl(triage_path)
    plans = {int(row["question_id"]): row for row in _read_jsonl(plans_path)}
    if {int(row["question_id"]) for row in triage} != set(range(1, expected_question_count + 1)) or set(plans) != set(range(1, expected_question_count + 1)):
        raise ValueError("triage and plans must cover all questions")
    targets = [int(row["question_id"]) for row in triage if row.get("primary_blocker") == TARGET_BLOCKER]
    probes = {int(row["question_id"]): row for row in _read_jsonl(temporal_probe_dir / "simple_temporal_operator_source_probe_v1.jsonl")}
    if not set(probes) <= set(targets):
        raise ValueError("temporal source probe contains a non-target question")
    audit: list[dict[str, Any]] = []
    for question_id in sorted(targets):
        plan = plans[question_id]
        kind, reason = _reason(plan, probes.get(question_id))
        record = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "quarantine_kind": kind,
            "reason_code": reason,
            "source_probe_status": (
                None if question_id not in probes else probes[question_id].get("quarantine_status")
            ),
            "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(record):
            raise ValueError("plan-incomplete audit leaked an unsafe field")
        audit.append(record)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "target_question_count": len(audit),
        "quarantine_kind_counts": dict(sorted(Counter(row["quarantine_kind"] for row in audit).items())),
        "source_probe_backed_question_count": len(probes),
        "source_contract": dict(CONTRACT),
    }
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        audit_path = temporary / "plan_incomplete_quarantine_audit_v1.jsonl"
        summary_path = temporary / "plan_incomplete_quarantine_summary_v1.json"
        _write_jsonl(audit_path, audit)
        _write_json(summary_path, summary)
        inputs = {"triage": triage_path, "plans": plans_path, "temporal_probe_manifest": temporal_probe_dir / "manifest.json"}
        _write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "protocol": PROTOCOL,
                "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
                "outputs": {
                    "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)},
                    "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)},
                },
                "source_contract": dict(CONTRACT),
            },
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_plan_incomplete_quarantine_audit(artifact_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest = _read_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected plan-incomplete quarantine protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("plan-incomplete quarantine hash mismatch")
    audit = _read_jsonl(artifact_dir / "plan_incomplete_quarantine_audit_v1.jsonl")
    if not audit or len({int(row["question_id"]) for row in audit}) != len(audit) or any(
        _contains_forbidden(row)
        or row.get("raw_numeric_values_included") is not False
        or row.get("source_contract") != CONTRACT
        for row in audit
    ):
        raise ValueError("plan-incomplete quarantine audit lost its boundary")
    summary = _read_json(artifact_dir / "plan_incomplete_quarantine_summary_v1.json")
    if summary.get("target_question_count") != len(audit):
        raise ValueError("plan-incomplete quarantine summary mismatch")
    return {"status": "PASS", "question_count": expected_question_count, "target_question_count": len(audit), "answer_eligible": False, "submission_eligible": False}
