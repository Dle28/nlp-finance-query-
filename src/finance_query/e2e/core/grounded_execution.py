"""Deterministic Decimal executor for fully bound research packets only."""
from __future__ import annotations

from decimal import Decimal, DivisionByZero, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from ..decimal_sandbox import execute_decimal_ast


GROUNDED_EXECUTION_PROTOCOL = "grounded_execution_replay_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contract() -> dict[str, bool]:
    return {"research_only": True, "evidence_eligible": False, "training_eligible": False, "submission_eligible": False, "promotion_allowed": False, "may_select_value": False, "may_execute_formula_outside_protocol": False}


def execute_formula(ast: Mapping[str, Any], operands: Mapping[str, Decimal]) -> Decimal:
    """Evaluate only the resource-bounded declarative Decimal AST."""

    return execute_decimal_ast(ast, operands).value


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError("Expected JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _require(path: Path, expected: object, label: str) -> None:
    if not isinstance(expected, str) or sha256_file(path) != expected: raise ValueError(f"SHA-256 mismatch for {label}")


def _registry(path: Path) -> dict[str, Mapping[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {str(metric["metric_id"]): metric for metric in (payload.get("metrics") or [])}


def run_grounded_execution(*, bindings_path: Path, bindings_manifest_path: Path, metric_registry_path: Path, output_dir: Path) -> dict[str, Any]:
    manifest = _read_json(bindings_manifest_path)
    _require(bindings_path, ((manifest.get("outputs") or {}).get("bindings") or {}).get("sha256"), "binding candidates")
    rows = _read_jsonl(bindings_path)
    if len(rows) != 1012 or {int(row.get("question_id") or 0) for row in rows} != set(range(1, 1013)): raise ValueError("Binding question coverage mismatch")
    registry = _registry(metric_registry_path)
    results: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda value: int(value["question_id"])):
        stage_traces: list[dict[str, Any]] = []
        status = "execution_replay_ready"
        for stage in row.get("stages") or []:
            metric_id = stage.get("metric_id")
            if metric_id not in registry:
                status = "formula_unsupported"; stage_traces.append({"stage_id": stage.get("stage_id"), "status": status, "reason_codes": ["MISSING_REGISTRY_FORMULA"]}); continue
            bound: dict[str, Decimal] = {}; sources: list[dict[str, Any]] = []; fail: list[str] = []
            units: set[str] = set()
            for operand in stage.get("required_operands") or []:
                candidates = operand.get("binding_candidates") or []
                if operand.get("binding_status") != "binding_candidate_ready" or len(candidates) != 1:
                    fail.append("MISSING_OR_CONFLICTED_BINDING"); continue
                candidate = candidates[0]
                if candidate.get("period_type") not in {"instant", "duration"}: fail.append("UNSUPPORTED_PERIOD_TYPE"); continue
                if len(candidate.get("scale_candidates") or []) != 1: fail.append("MIXED_OR_MISSING_UNIT"); continue
                try: bound[str(operand.get("role"))] = Decimal(str(candidate.get("numeric_parse_candidate")))
                except (InvalidOperation, ValueError): fail.append("NUMERIC_PARSE_FAILURE"); continue
                units.add(str((candidate.get("scale_candidates") or [{}])[0].get("unit_label") or ""))
                sources.append({key: candidate.get(key) for key in ("role", "document_id", "internal_table_uid", "row_index", "column_index", "cell_provenance", "period_labels", "unit_labels")})
            if fail:
                status = "binding_conflict"; stage_traces.append({"stage_id": stage.get("stage_id"), "status": "binding_conflict", "reason_codes": sorted(set(fail)), "operand_sources": sources}); continue
            if len(units) > 1:
                status = "binding_conflict"; stage_traces.append({"stage_id": stage.get("stage_id"), "status": "binding_conflict", "reason_codes": ["MIXED_SOURCE_UNITS"], "operand_sources": sources}); continue
            try:
                value = execute_formula(registry[str(metric_id)]["formula_ast"], bound)
            except (KeyError, ValueError):
                status = "formula_unsupported"; stage_traces.append({"stage_id": stage.get("stage_id"), "status": "formula_unsupported", "reason_codes": ["FORMULA_UNSUPPORTED"], "operand_sources": sources}); continue
            except DivisionByZero:
                status = "execution_abstained"; stage_traces.append({"stage_id": stage.get("stage_id"), "status": "execution_abstained", "reason_codes": ["DIVISION_BY_ZERO"], "operand_sources": sources}); continue
            stage_traces.append({"stage_id": stage.get("stage_id"), "status": "execution_replay_ready", "execution_value_decimal": format(value, "f"), "unit_conversion": {"source_unit": next(iter(units), None), "policy": "EXACT_SINGLE_UNIT_CANDIDATE"}, "operand_sources": sources})
        if row.get("binding_packet_status") == "binding_blocked": status = "dependency_blocked"
        results.append({"schema_version": 1, "protocol": GROUNDED_EXECUTION_PROTOCOL, "question_id": row["question_id"], "execution_status": status, "question_context": row.get("question_context") or {}, "stage_traces": stage_traces, "source_contract": _contract()})
    output_dir.mkdir(parents=True, exist_ok=True)
    output, manifest_out = output_dir / "grounded_execution_replay_v1.jsonl", output_dir / "grounded_execution_v1.manifest.json"
    _write_jsonl(output, results)
    value = {"schema_version": 1, "protocol": GROUNDED_EXECUTION_PROTOCOL, "inputs": {"bindings": {"path": str(bindings_path), "sha256": sha256_file(bindings_path)}, "bindings_manifest": {"path": str(bindings_manifest_path), "sha256": sha256_file(bindings_manifest_path)}, "metric_registry": {"path": str(metric_registry_path), "sha256": sha256_file(metric_registry_path)}}, "outputs": {"execution": {"path": str(output), "sha256": sha256_file(output)}}, "counts": {"question_count": len(results), "execution_status_counts": dict(sorted(__import__("collections").Counter(row["execution_status"] for row in results).items()))}, "source_contract": _contract()}
    _write_json(manifest_out, value)
    return {**value, "manifest_path": str(manifest_out)}
