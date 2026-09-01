"""Materialize only a fully source-checked, same-entity period argmax route."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from finance_query.research.composition_operand_source_gap_audit import (
    _contains_forbidden as _audit_contains_forbidden,
    validate_composition_operand_source_gap_audit,
)
from finance_query.research.full_corpus_candidate_retrieval import validate_candidate_artifact
from finance_query.research.machine_direct_lookup_route_materialization import (
    PERIOD_CONTRACT,
    PERIOD_PROTOCOL,
    ROUTE_CONTRACT,
    _contains_forbidden,
    _load_json,
    _load_jsonl,
    _write_json,
    _write_jsonl,
)
from finance_query.research.machine_exact_cell_proposals import sha256_file
from finance_query.research.temporal_subtract_route_materialization import _candidate_for_operand


PROTOCOL = "vifinqa_argmax_period_route_materialization_v1"
CONTRACT = {
    "research_only": True,
    "machine_recheck_only": True,
    "evidence_eligible": False,
    "may_materialize_answer": False,
    "training_eligible": False,
    "submission_eligible": False,
    "promotion_allowed": False,
}


def _require(manifest: Mapping[str, Any], key: str, path: Path) -> None:
    if sha256_file(path) != (((manifest.get("outputs") or {}).get(key) or {}).get("sha256")):
        raise ValueError(f"SHA-256 mismatch for {key}")


def _stage_id(question_id: int, operand_id: str, plan: Mapping[str, Any]) -> str:
    return f"argmax_period_q{question_id}_{operand_id}_{str(plan.get('plan_fingerprint') or '')[:12]}"


def _eligible_shape(plan: Mapping[str, Any]) -> bool:
    operands = [item for item in plan.get("operands") or [] if item.get("required") is True]
    years = {int(value) for value in plan.get("years") or []}
    operand_years = [list(item.get("years") or []) for item in operands]
    entities = {str(item.get("entity") or item.get("ticker") or "") for item in operands}
    return bool(
        plan.get("formula_id") == "operating_cash_flow_argmax_period"
        and (plan.get("operation_ast") or {}).get("op") == "staged_program"
        and len(operands) >= 2
        and len(entities) == 1
        and "" not in entities
        and len(years) == len(operands)
        and all(len(values) == 1 and int(values[0]) in years for values in operand_years)
        and len({int(values[0]) for values in operand_years}) == len(operands)
    )


def _period_stage(question_id: int, operand: Mapping[str, Any], plan: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    operand_id = str(operand["operand_id"])
    selection = {key: candidate[key] for key in ("internal_table_uid", "row_index", "column_index", "header_source_cells", "period_labels", "source_label", "unit_labels", "requested_year")}
    selection.update({"reason_codes": ["EXACT_REQUESTED_YEAR", "ARGMAX_PERIOD_SOURCE_RECHECK_CANDIDATE_ONLY"], "source_contract": dict(PERIOD_CONTRACT)})
    return {
        "stage_id": _stage_id(question_id, operand_id, plan),
        "route_kind": "reported_concept",
        "metric_id": None,
        "concept_id": operand.get("role") or operand_id,
        "required_operands": [{
            "role": operand_id,
            "concept_id": operand.get("role") or operand_id,
            "expected_table_types": [],
            "period_type": "fiscal_year_machine_candidate",
            "column_status": "unique_period_column_candidate",
            "navigation_row_count": 1,
            "period_column_candidate_count": 1,
            "period_column_candidates": [selection],
        }],
    }


def build_argmax_period_route_materialization(
    *,
    plans_path: Path,
    source_audit_dir: Path,
    base_period_packets_path: Path,
    base_period_manifest_path: Path,
    base_route_overlay_path: Path,
    base_route_overlay_manifest_path: Path,
    full_corpus_artifact_dir: Path,
    row_review_queue_path: Path,
    structured_tables_path: Path,
    evidence_context_path: Path,
    evidence_context_manifest_path: Path,
    output_dir: Path,
    expected_question_count: int = 1012,
    minimum_row_jaccard: float = 0.9,
    minimum_row_margin: float = 0.2,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    validate_composition_operand_source_gap_audit(source_audit_dir, expected_question_count=expected_question_count)
    validate_candidate_artifact(full_corpus_artifact_dir, expected_question_count=expected_question_count)
    source_manifest = _load_json(source_audit_dir / "manifest.json")
    if ((source_manifest.get("inputs") or {}).get("plans") or {}).get("sha256") != sha256_file(plans_path):
        raise ValueError("source audit was not run against these typed plans")
    question_descriptor = (source_manifest.get("outputs") or {}).get("question_audit") or {}
    question_audit_path = source_audit_dir / str(question_descriptor.get("path") or "")
    if not question_audit_path.is_file() or sha256_file(question_audit_path) != question_descriptor.get("sha256"):
        raise ValueError("source-audit question output is unavailable or stale")
    period_manifest, route_manifest, context_manifest = _load_json(base_period_manifest_path), _load_json(base_route_overlay_manifest_path), _load_json(evidence_context_manifest_path)
    _require(period_manifest, "period_packets", base_period_packets_path)
    _require(route_manifest, "overlay", base_route_overlay_path)
    if sha256_file(structured_tables_path) != ((period_manifest.get("inputs") or {}).get("structured_tables_v2") or {}).get("sha256"):
        raise ValueError("V2 tables mismatch")
    if sha256_file(evidence_context_path) != context_manifest.get("sidecar_sha256"):
        raise ValueError("V3 context mismatch")
    corpus_manifest = _load_json(full_corpus_artifact_dir / "manifest.json")
    queue_descriptor = (corpus_manifest.get("outputs") or {}).get("row_review_queue_v1.jsonl") or {}
    if sha256_file(row_review_queue_path) != queue_descriptor.get("sha256"):
        raise ValueError("full-corpus row queue mismatch")

    expected_ids = set(range(1, expected_question_count + 1))
    plans = {int(row["question_id"]): row for row in _load_jsonl(plans_path)}
    source_ready = {int(row["question_id"]): row for row in _load_jsonl(question_audit_path)}
    periods = {int(row["question_id"]): row for row in _load_jsonl(base_period_packets_path)}
    routes = {int(row["question_id"]): row for row in _load_jsonl(base_route_overlay_path)}
    if set(plans) != expected_ids or set(periods) != expected_ids or set(routes) != expected_ids:
        raise ValueError("full E2E input coverage mismatch")
    rows_by_operand: defaultdict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in _load_jsonl(row_review_queue_path):
        rows_by_operand[(int(row["question_id"]), str(row.get("operand_id") or ""))].append(row)
    tables = {str(row.get("internal_table_uid") or ""): row for row in _load_jsonl(structured_tables_path)}
    contexts = {str(row.get("internal_table_uid") or ""): row for row in _load_jsonl(evidence_context_path)}

    revised_periods: dict[int, dict[str, Any]] = {}
    revised_routes: dict[int, dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    for question_id in sorted(source_ready):
        plan = plans[question_id]
        operands = [item for item in plan.get("operands") or [] if item.get("required") is True]
        source_complete = source_ready[question_id].get("question_status") == "ALL_OPERANDS_HAVE_UNIQUE_STRICT_SOURCE_ROWS"
        shape = _eligible_shape(plan)
        eligible = shape and source_complete
        checks: dict[str, bool] = {"eligible_argmax_period_shape": shape, "all_operands_source_ready": source_complete}
        candidates: dict[str, dict[str, Any]] = {}
        if eligible:
            for operand in operands:
                candidate, candidate_checks = _candidate_for_operand(question_id, operand, rows_by_operand[(question_id, str(operand["operand_id"]))], tables, contexts, minimum_row_jaccard, minimum_row_margin)
                checks.update({f"{operand['operand_id']}:{name}": value for name, value in candidate_checks.items()})
                if candidate is not None:
                    candidates[str(operand["operand_id"])] = candidate
        materialize = eligible and len(candidates) == len(operands) and all(checks.values())
        if materialize:
            stages = [_period_stage(question_id, operand, plan, candidates[str(operand["operand_id"])]) for operand in operands]
            stage_by_operand = {str(operand["operand_id"]): _stage_id(question_id, str(operand["operand_id"]), plan) for operand in operands}
            stage_order = [stage_by_operand[str(operand["operand_id"])] for operand in operands]
            stage_periods = {stage_by_operand[str(operand["operand_id"])]: int((operand.get("years") or [None])[0]) for operand in operands}
            graph = {
                "protocol": "vifinqa_controlled_composition_graph_v1",
                "composition_mode": "same_entity_multi_period_argmax_v1",
                "stage_order": stage_order,
                "stage_periods": stage_periods,
                "operation_ast": {"op": "arg_extreme_period", "direction": "max", "args": stage_order},
                "binding_operation_ast": {"op": "arg_extreme_period", "direction": "max", "args": [f"q{question_id}:stage:{stage_by_operand[str(operand['operand_id'])]}:role:{operand['operand_id']}" for operand in operands]},
                "final_node_id": f"argmax_period_q{question_id}",
            }
            revised_periods[question_id] = {
                "schema_version": 1, "protocol": PERIOD_PROTOCOL, "question_id": question_id,
                "input_packet_status": "argmax_period_source_recheck_candidate",
                "packet_status": "unique_period_column_candidate", "route_status": "route_complete",
                "question_context": {"entities": list(plan.get("entities") or []), "scope": plan.get("scope"), "source": "typed_plan_argmax_period_recheck", "years": list(plan.get("years") or [])},
                "stages": stages, "source_contract": dict(PERIOD_CONTRACT),
            }
            overlay = json.loads(json.dumps(routes[question_id]))
            overlay.update({
                "route_status": "route_complete",
                "requested_output_unit": {"kind": "period", "unit": "year", "vnd_to_output_divisor": None, "source": "controlled_argmax_question_semantics_v1"},
                "covered_operations": ["reported_value", "multi_year_range", "min_max_ranking", "stage_output_dependency"],
                "missing_operations": [], "reason_codes": ["ARGMAX_PERIOD_SOURCE_RECHECK_CANDIDATE_ONLY"],
                "controlled_operation_graph": graph, "source_contract": dict(ROUTE_CONTRACT),
            })
            revised_routes[question_id] = overlay
        audit_row = {
            "schema_version": 1, "protocol": PROTOCOL, "question_id": question_id,
            "materialization_status": "MATERIALIZED_ARGMAX_PERIOD_EXECUTION_CANDIDATE" if materialize else "QUARANTINED_NOT_FULLY_SOURCE_READY_ARGMAX_PERIOD",
            "checks": checks, "candidate_count": len(candidates), "raw_numeric_values_included": False,
            "source_contract": dict(CONTRACT),
        }
        if _contains_forbidden(audit_row) or _audit_contains_forbidden(audit_row) or "human_verified" in audit_row:
            raise ValueError("argmax materialization audit leaked an unsafe field")
        audit.append(audit_row)

    output_periods = [revised_periods.get(question_id, periods[question_id]) for question_id in range(1, expected_question_count + 1)]
    output_routes = [revised_routes.get(question_id, routes[question_id]) for question_id in range(1, expected_question_count + 1)]
    summary = {"schema_version": 1, "protocol": PROTOCOL, "target_question_count": len(audit), "materialized_question_count": len(revised_periods), "status_counts": dict(sorted(Counter(row["materialization_status"] for row in audit).items())), "thresholds": {"minimum_row_jaccard": minimum_row_jaccard, "minimum_row_margin": minimum_row_margin}, "source_contract": dict(CONTRACT)}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        periods_path, routes_path = temporary / "period_column_candidate_packets_v1.jsonl", temporary / "route_completeness_overlay_v3.jsonl"
        audit_path, summary_path = temporary / "argmax_period_route_audit_v1.jsonl", temporary / "argmax_period_route_summary_v1.json"
        _write_jsonl(periods_path, output_periods); _write_jsonl(routes_path, output_routes); _write_jsonl(audit_path, audit); _write_json(summary_path, summary)
        inputs = {"plans": plans_path, "source_audit_manifest": source_audit_dir / "manifest.json", "base_period_packets": base_period_packets_path, "base_period_manifest": base_period_manifest_path, "base_route_overlay": base_route_overlay_path, "base_route_overlay_manifest": base_route_overlay_manifest_path, "full_corpus_manifest": full_corpus_artifact_dir / "manifest.json", "row_review_queue": row_review_queue_path, "structured_tables_v2": structured_tables_path, "evidence_context_v3": evidence_context_path, "evidence_context_manifest_v3": evidence_context_manifest_path}
        common = {"schema_version": 1, "protocol": PROTOCOL, "inputs": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in inputs.items()}, "source_contract": dict(CONTRACT)}
        _write_json(temporary / "period_column_candidate_packets_v1.manifest.json", {**common, "outputs": {"period_packets": {"path": periods_path.name, "sha256": sha256_file(periods_path)}}})
        _write_json(temporary / "route_completeness_overlay_v3.manifest.json", {**common, "outputs": {"overlay": {"path": routes_path.name, "sha256": sha256_file(routes_path)}}})
        _write_json(temporary / "manifest.json", {**common, "outputs": {"period_packets": {"path": periods_path.name, "sha256": sha256_file(periods_path)}, "overlay": {"path": routes_path.name, "sha256": sha256_file(routes_path)}, "audit": {"path": audit_path.name, "sha256": sha256_file(audit_path)}, "summary": {"path": summary_path.name, "sha256": sha256_file(summary_path)}}})
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return summary


def validate_argmax_period_route_materialization(artifact_dir: Path, *, expected_question_count: int = 1012) -> dict[str, Any]:
    manifest = _load_json(artifact_dir / "manifest.json")
    if manifest.get("protocol") != PROTOCOL or manifest.get("source_contract") != CONTRACT:
        raise ValueError("unexpected argmax materialization protocol")
    for group, relative in (("inputs", False), ("outputs", True)):
        for descriptor in (manifest.get(group) or {}).values():
            path = artifact_dir / str(descriptor.get("path") or "") if relative else Path(str(descriptor.get("path") or ""))
            if not path.is_file() or sha256_file(path) != descriptor.get("sha256"):
                raise ValueError("argmax materialization hash mismatch")
    periods, routes, audit = _load_jsonl(artifact_dir / "period_column_candidate_packets_v1.jsonl"), _load_jsonl(artifact_dir / "route_completeness_overlay_v3.jsonl"), _load_jsonl(artifact_dir / "argmax_period_route_audit_v1.jsonl")
    expected = set(range(1, expected_question_count + 1))
    if {int(row["question_id"]) for row in periods} != expected or {int(row["question_id"]) for row in routes} != expected:
        raise ValueError("argmax materialization coverage mismatch")
    if any(_contains_forbidden(row) or _audit_contains_forbidden(row) or row.get("raw_numeric_values_included") is not False or row.get("source_contract") != CONTRACT or "human_verified" in row for row in audit):
        raise ValueError("argmax materialization audit lost its non-authorizing boundary")
    materialized = {int(row["question_id"]) for row in audit if row.get("materialization_status") == "MATERIALIZED_ARGMAX_PERIOD_EXECUTION_CANDIDATE"}
    for route in routes:
        if int(route["question_id"]) in materialized:
            graph = route.get("controlled_operation_graph") or {}
            if route.get("route_status") != "route_complete" or route.get("requested_output_unit", {}).get("kind") != "period" or graph.get("composition_mode") != "same_entity_multi_period_argmax_v1" or graph.get("operation_ast", {}).get("op") != "arg_extreme_period":
                raise ValueError("argmax graph missing")
    return {"status": "PASS", "question_count": expected_question_count, "target_question_count": len(audit), "materialized_question_count": len(materialized), "answer_eligible": False, "submission_eligible": False}
