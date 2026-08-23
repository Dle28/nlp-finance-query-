#!/usr/bin/env python3
"""Freeze grounded execution blockers into non-promotable remediation queues."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


PROTOCOL = "vifinqa_grounded_coverage_diagnostics_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    result = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must be a JSON object")
        result.append(value)
    return result


def _index(rows: list[dict[str, Any]], name: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        question_id = row.get("question_id")
        if not isinstance(question_id, int) or question_id in result:
            raise ValueError(f"{name} requires unique integer question_id")
        result[question_id] = row
    return result


def _require_output(manifest_path: Path, artifact_path: Path, output_name: str) -> None:
    manifest = _json(manifest_path)
    expected = ((manifest.get("outputs") or {}).get(output_name) or {}).get("sha256")
    if not isinstance(expected, str) or expected != sha256_file(artifact_path):
        raise ValueError(f"SHA-256 mismatch for {output_name}")


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _operand_reasons(binding: Mapping[str, Any]) -> list[str]:
    return sorted({
        str(reason)
        for stage in binding.get("stages") or []
        if isinstance(stage, Mapping)
        for operand in stage.get("required_operands") or []
        if isinstance(operand, Mapping)
        for reason in operand.get("reason_codes") or []
        if str(reason)
    })


def build_diagnostics(
    *,
    execution: Path,
    execution_manifest: Path,
    bindings: Path,
    bindings_manifest: Path,
    route_overlay: Path,
    route_overlay_manifest: Path,
    period_packets: Path,
    period_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite coverage diagnostics: {output_dir}")
    _require_output(execution_manifest, execution, "execution")
    _require_output(bindings_manifest, bindings, "bindings")
    _require_output(route_overlay_manifest, route_overlay, "overlay")
    _require_output(period_manifest, period_packets, "period_packets")
    indexes = {
        "execution": _index(_rows(execution), "execution"),
        "bindings": _index(_rows(bindings), "bindings"),
        "routes": _index(_rows(route_overlay), "routes"),
        "periods": _index(_rows(period_packets), "periods"),
    }
    question_ids = set(indexes["execution"])
    if not question_ids or any(set(index) != question_ids for index in indexes.values()):
        raise ValueError("coverage inputs do not describe the same question IDs")

    contract = {
        "candidate_only": True,
        "may_materialize_answer": False,
        "may_execute_formula": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
    }
    route_queue: list[dict[str, Any]] = []
    conflict_queue: list[dict[str, Any]] = []
    execution_counts = Counter()
    route_counts = Counter()
    binding_counts = Counter()
    period_counts = Counter()
    route_reasons = Counter()
    operand_reasons = Counter()
    for question_id in sorted(question_ids):
        execution_row = indexes["execution"][question_id]
        binding = indexes["bindings"][question_id]
        route = indexes["routes"][question_id]
        period = indexes["periods"][question_id]
        execution_status = str(execution_row.get("execution_status") or "unknown")
        route_status = str(route.get("route_status") or "unknown")
        binding_status = str(binding.get("binding_packet_status") or "unknown")
        period_status = str(period.get("packet_status") or "unknown")
        execution_counts[execution_status] += 1
        route_counts[route_status] += 1
        binding_counts[binding_status] += 1
        period_counts[period_status] += 1
        reason_codes = sorted(str(reason) for reason in route.get("reason_codes") or [] if str(reason))
        operand_reason_codes = _operand_reasons(binding)
        route_reasons.update(reason_codes)
        operand_reasons.update(operand_reason_codes)
        common = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "question": route.get("question"),
            "execution_status": execution_status,
            "route_status": route_status,
            "binding_packet_status": binding_status,
            "period_packet_status": period_status,
            "required_operations": list(route.get("required_operations") or []),
            "covered_operations": list(route.get("covered_operations") or []),
            "missing_operations": list(route.get("missing_operations") or []),
            "route_reason_codes": reason_codes,
            "operand_reason_codes": operand_reason_codes,
            "source_contract": contract,
        }
        if execution_status == "route_incomplete":
            route_queue.append({
                **common,
                "remediation_action": "author and review a typed whole-question operation graph; do not infer missing operations",
            })
        elif execution_status == "binding_conflict":
            conflict_queue.append({
                **common,
                "remediation_action": "resolve period/source/unit binding conflict against exact source coordinates, then rerun replay",
            })

    output_dir.mkdir(parents=True, exist_ok=False)
    route_path = output_dir / "route_incomplete_remediation_queue_v1.jsonl"
    conflict_path = output_dir / "binding_conflict_remediation_queue_v1.jsonl"
    _write_jsonl(route_path, route_queue)
    _write_jsonl(conflict_path, conflict_queue)
    summary = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "diagnostics_complete_no_automatic_remediation",
        "counts": {
            "question_count": len(question_ids),
            "execution_status_counts": dict(sorted(execution_counts.items())),
            "route_status_counts": dict(sorted(route_counts.items())),
            "binding_packet_status_counts": dict(sorted(binding_counts.items())),
            "period_packet_status_counts": dict(sorted(period_counts.items())),
            "route_reason_counts": dict(sorted(route_reasons.items())),
            "operand_reason_counts": dict(sorted(operand_reasons.items())),
            "route_remediation_count": len(route_queue),
            "binding_conflict_remediation_count": len(conflict_queue),
        },
        "diagnosis": {
            "primary_gap": "controlled whole-question operation coverage plus exact source binding",
            "automatic_gate_lowering_allowed": False,
            "typed_operation_graph_required": True,
            "human_source_review_required_for_conflicts": True,
        },
        "source_contract": contract,
    }
    summary_path = output_dir / "coverage_summary_v1.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    inputs = {
        "execution": execution,
        "execution_manifest": execution_manifest,
        "bindings": bindings,
        "bindings_manifest": bindings_manifest,
        "route_overlay": route_overlay,
        "route_overlay_manifest": route_overlay_manifest,
        "period_packets": period_packets,
        "period_manifest": period_manifest,
    }
    result = {
        **summary,
        "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()},
        "outputs": {
            "summary": {"path": str(summary_path), "sha256": sha256_file(summary_path)},
            "route_remediation": {"path": str(route_path), "sha256": sha256_file(route_path)},
            "binding_conflict_remediation": {"path": str(conflict_path), "sha256": sha256_file(conflict_path)},
        },
    }
    manifest_path = output_dir / "grounded_coverage_diagnostics_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {**result, "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "execution", "execution_manifest", "bindings", "bindings_manifest",
        "route_overlay", "route_overlay_manifest", "period_packets", "period_manifest",
    ):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_diagnostics(**vars(args))
    print(json.dumps({"status": result["status"], "counts": result["counts"], "manifest_path": result["manifest_path"]}, indent=2))


if __name__ == "__main__":
    main()
