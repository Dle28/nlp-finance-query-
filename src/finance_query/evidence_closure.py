"""Fail-closed receipt-intake workbench for the V13 proof backlog.

The workbench turns existing, non-promotable diagnostic artifacts into
hash-bound *review packets*.  It never creates a PASS, a numeric answer, an
independent-audit decision, or a production execution record.  Those actions
require separate human/independent evidence and are intentionally outside this
module.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Sequence


PROTOCOL = "vifinqa_v13_evidence_closure_workbench_v1"
FORMULA_INTAKE_PROTOCOL = "vifinqa_formula_definition_receipt_intake_v1"
OPERAND_INTAKE_PROTOCOL = "vifinqa_operand_compatibility_receipt_intake_v1"
ROUTE_INTAKE_PROTOCOL = "vifinqa_route_binding_receipt_intake_v1"
ROUTE_OPERATOR_INTAKE_PROTOCOL = "vifinqa_route_operator_receipt_intake_v1"
ROUTE_CAUSE_INTAKE_PROTOCOL = "vifinqa_route_cause_investigation_intake_v1"
TEMPORAL_INTAKE_PROTOCOL = "vifinqa_temporal_receipt_intake_v1"
RECERTIFICATION_INTAKE_PROTOCOL = "vifinqa_v12_candidate_recertification_intake_v1"
INDEPENDENT_UNIVERSE_PROTOCOL = "vifinqa_independent_requirement_review_packets_v1"
LEDGER_INTAKE_PROTOCOL = "vifinqa_production_execution_ledger_intake_v1"


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        rows.append(row)
    return rows


def index_rows(rows: Iterable[Mapping[str, Any]], *, key: str, label: str) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        raw = row.get(key)
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise ValueError(f"{label} requires integer {key}")
        if raw in indexed:
            raise ValueError(f"{label} has duplicate Q{raw}")
        indexed[raw] = dict(row)
    return indexed


def source_contract() -> dict[str, bool]:
    return {
        "research_only": True,
        "evidence_eligible": False,
        "may_materialize_answer": False,
        "may_execute_formula": False,
        "promotion_allowed": False,
        "training_eligible": False,
        "submission_eligible": False,
        "release_authorized": False,
    }


def _manifest_output_path(manifest_path: Path, manifest: Mapping[str, Any], name: str, filename: str) -> Path:
    expected = ((manifest.get("outputs") or {}).get(name) or {}).get("sha256")
    if not isinstance(expected, str):
        raise ValueError(f"V13 manifest has no SHA for output {name}")
    result = manifest_path.parent / filename
    if not result.is_file() or sha256_file(result) != expected:
        raise ValueError(f"V13 output hash mismatch for {filename}")
    return result


def _required_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def _input_from_config(config_path: Path, config: Mapping[str, Any], name: str) -> Path:
    value = (config.get("input_paths") or {}).get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"closure config missing input path {name}")
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (config_path.parent.parent / candidate)


def _assert_config(config_path: Path, config: Mapping[str, Any]) -> dict[str, Path]:
    if config.get("protocol") != PROTOCOL or config.get("mode") != "additive_receipt_intake":
        raise ValueError("invalid closure workbench config")
    names = (
        "v13_manifest",
        "typed_plans",
        "typed_plans_manifest",
        "formula_evidence",
        "formula_evidence_manifest",
    )
    expected = config.get("locked_input_sha256")
    if not isinstance(expected, Mapping) or set(expected) != set(names):
        raise ValueError("closure config must pin every exact input hash")
    paths = {name: _input_from_config(config_path, config, name) for name in names}
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"missing closure input {name}: {path}")
        _required_hash(path, expected.get(name), name)
    return paths


def _assert_sidecar_manifest(manifest_path: Path, sidecar_path: Path, *, protocol: str | None = None) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    if protocol is not None and manifest.get("protocol") != protocol:
        raise ValueError(f"unexpected protocol in {manifest_path}")
    _required_hash(sidecar_path, manifest.get("sidecar_sha256"), sidecar_path.name)
    return manifest


def _id(protocol: str, question_id: int, payload: Mapping[str, Any]) -> str:
    return canonical_sha256({"protocol": protocol, "question_id": question_id, **payload})


def _requirement(requirement_row: Mapping[str, Any], dimension: str) -> dict[str, Any]:
    candidates = [dict(item) for item in requirement_row.get("requirements") or [] if item.get("dimension") == dimension]
    if len(candidates) != 1:
        raise ValueError(f"Q{requirement_row.get('question_id')} missing unique {dimension} requirement")
    return candidates[0]


def _operation_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    operands = [item for item in plan.get("operands") or [] if isinstance(item, Mapping)]
    ast = plan.get("operation_ast") if isinstance(plan.get("operation_ast"), Mapping) else None
    return {
        "decomposition_status": str(plan.get("decomposition_status") or "unknown"),
        "effective_family": str(plan.get("effective_family") or "unknown"),
        "formula_id": plan.get("formula_id"),
        "plan_fingerprint": plan.get("plan_fingerprint"),
        "operation_ast_sha256": canonical_sha256(ast) if ast is not None else None,
        "operand_count": len(operands),
        "operand_descriptors": [
            {
                "operand_id": item.get("operand_id"),
                "role": item.get("role"),
                "entity": item.get("entity"),
                "scope": item.get("scope"),
                "years": list(item.get("years") or []),
            }
            for item in operands
        ],
    }


def _formula_candidate(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {"status": "MISSING"}
    formula = row.get("formula") if isinstance(row.get("formula"), Mapping) else {}
    return {
        "status": "AVAILABLE_NON_AUTHORIZING",
        "evidence_row_sha256": canonical_sha256(row),
        "formula_id": formula.get("formula_id"),
        "definition_status": formula.get("definition_status"),
        "operand_coverage_status": row.get("operand_coverage_status"),
        "evidence_completeness": row.get("evidence_completeness"),
        "execution_status": row.get("execution_status"),
    }


def _pending_record(protocol: str, question_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "protocol": protocol,
        "question_id": question_id,
        "receipt_status": "PENDING_INDEPENDENT_REVIEW",
        "submission_eligible": False,
        "source_contract": source_contract(),
        **payload,
    }
    return {**base, "packet_id": _id(protocol, question_id, base)}


def _blocked_ledger_record(question_id: int, certificate: Mapping[str, Any], next_queue: str) -> dict[str, Any]:
    unresolved = sorted({str(item) for item in certificate.get("required_unresolved_dimensions") or []})
    unchecked = sorted({str(item) for item in certificate.get("unchecked_dimensions") or []})
    payload = {
        "schema_version": 1,
        "protocol": LEDGER_INTAKE_PROTOCOL,
        "question_id": question_id,
        "entry_status": "BLOCKED",
        "next_queue": next_queue,
        "semantic_certificate_id": certificate.get("semantic_coverage_certificate_id"),
        "v12_certificate_status": certificate.get("v12_certificate_status"),
        "required_unresolved_dimensions": unresolved,
        "unchecked_dimensions": unchecked,
        "required_production_gates": {
            "independent_requirement_universe": "PENDING",
            "independent_audit": "PENDING",
            "exact_execution": "BLOCKED",
            "production_eligibility": "BLOCKED",
        },
        "contains_answer": False,
        "submission_eligible": False,
        "source_contract": source_contract(),
    }
    return {**payload, "ledger_intake_id": _id(LEDGER_INTAKE_PROTOCOL, question_id, payload)}


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def build_workbench(*, config_path: Path, output_dir: Path) -> dict[str, Any]:
    """Build all six review queues atomically from pinned, non-promotable inputs."""
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite closure workbench: {output_dir}")
    config = load_json(config_path)
    inputs = _assert_config(config_path, config)
    v13_manifest = load_json(inputs["v13_manifest"])
    if v13_manifest.get("protocol") != "vifinqa_claim_requirement_coverage_v13":
        raise ValueError("closure workbench requires a V13 coverage manifest")
    if (v13_manifest.get("release_decision") or {}).get("status") != "blocked":
        raise ValueError("closure workbench is only valid while V13 release remains blocked")
    typed_manifest = _assert_sidecar_manifest(
        inputs["typed_plans_manifest"],
        inputs["typed_plans"],
        protocol="typed_operand_decomposition_fail_closed_v1",
    )
    formula_manifest = _assert_sidecar_manifest(inputs["formula_evidence_manifest"], inputs["formula_evidence"])
    requirement_path = _manifest_output_path(inputs["v13_manifest"], v13_manifest, "claim_requirements", "claim_requirement_sets_v1.jsonl")
    certificate_path = _manifest_output_path(inputs["v13_manifest"], v13_manifest, "semantic_coverage", "semantic_coverage_certificates_v2.jsonl")
    composed_path = _manifest_output_path(inputs["v13_manifest"], v13_manifest, "composed_taxonomy", "composed_blocker_taxonomy_v1.jsonl")
    route_path = _manifest_output_path(inputs["v13_manifest"], v13_manifest, "route_taxonomy", "route_blocker_taxonomy_v2.jsonl")
    temporal_path = _manifest_output_path(inputs["v13_manifest"], v13_manifest, "temporal_semantics", "temporal_semantics_v1.jsonl")
    requirements = index_rows(load_jsonl(requirement_path), key="question_id", label="V13 requirements")
    certificates = index_rows(load_jsonl(certificate_path), key="question_id", label="V13 certificates")
    plans = index_rows(load_jsonl(inputs["typed_plans"]), key="question_id", label="typed plans")
    formula_rows = index_rows(load_jsonl(inputs["formula_evidence"]), key="id", label="formula evidence")
    question_ids = set(requirements)
    if len(question_ids) != 1012 or set(certificates) != question_ids or set(plans) != question_ids:
        raise ValueError("closure inputs must cover the same 1,012 question IDs")
    if not set(formula_rows).issubset(question_ids):
        raise ValueError("formula evidence references a non-V13 question")
    composed = sorted(load_jsonl(composed_path), key=lambda row: int(row["question_id"]))
    routes = sorted(load_jsonl(route_path), key=lambda row: int(row["question_id"]))
    temporal = sorted(load_jsonl(temporal_path), key=lambda row: int(row["question_id"]))
    formula_intake = []
    operand_intake = []
    next_queue: dict[int, str] = {}
    for row in composed:
        qid = int(row["question_id"])
        requirement = requirements[qid]
        plan = plans[qid]
        if row.get("primary_blocker") == "FORMULA_DEFINITION_INCOMPLETE":
            formula_requirement = _requirement(requirement, "formula.definition")
            formula_intake.append(_pending_record(FORMULA_INTAKE_PROTOCOL, qid, {
                "claim_requirement_set_id": requirement.get("claim_requirement_set_id"),
                "obligation_id": formula_requirement.get("obligation_id"),
                "required_operations": (formula_requirement.get("expected") or {}).get("required_operations") or [],
                "operation_plan": _operation_summary(plan),
                "formula_evidence_candidate": _formula_candidate(formula_rows.get(qid)),
                "required_receipt_fields": ["formula_ast", "operation_semantics", "typed_slots", "arity", "rounding_policy", "source_anchors", "independent_approval_id"],
                "decision_policy": "Formula candidates are diagnostic only until independently approved.",
            }))
            next_queue[qid] = "formula_definition_receipt"
        elif row.get("primary_blocker") == "OPERAND_SET_INCOMPLETE":
            formula_requirement = _requirement(requirement, "formula.definition")
            operand_requirement = _requirement(requirement, "operand.set")
            compatibility_requirement = _requirement(requirement, "operand.compatibility")
            operation_plan = _operation_summary(plan)
            operand_intake.append(_pending_record(OPERAND_INTAKE_PROTOCOL, qid, {
                "claim_requirement_set_id": requirement.get("claim_requirement_set_id"),
                "formula_obligation_id": formula_requirement.get("obligation_id"),
                "operand_set_obligation_id": operand_requirement.get("obligation_id"),
                "compatibility_obligation_id": compatibility_requirement.get("obligation_id"),
                "operation_plan": operation_plan,
                "formula_evidence_candidate": _formula_candidate(formula_rows.get(qid)),
                "operand_set_required_fields": ["operand_id", "exact_cell_anchor", "metric", "entity", "scope", "period", "unit_scale"],
                "compatibility_required_fields": ["comparison_matrix", "unit_compatibility", "scope_compatibility", "period_compatibility", "dependency_order", "independent_approval_id"],
                "compatibility_required": operation_plan["operand_count"] > 1,
            }))
            next_queue[qid] = "operand_set_and_compatibility_receipt"
        else:
            raise ValueError(f"unsupported composed blocker for Q{qid}")
    route_intake = []
    route_operator_intake = []
    route_cause_intake = []
    for row in routes:
        qid = int(row["question_id"])
        requirement = requirements[qid]
        metric_requirement = _requirement(requirement, "variable.metric")
        common = {
            "claim_requirement_set_id": requirement.get("claim_requirement_set_id"),
            "metric_obligation_id": metric_requirement.get("obligation_id"),
            "primary_blocker": row.get("primary_blocker"),
            "classification_basis": row.get("classification_basis"),
            "missing_operations": row.get("missing_operations") or [],
            "covered_operations": row.get("covered_operations") or [],
            "route_reason_codes": row.get("route_reason_codes") or [],
        }
        primary = row.get("primary_blocker")
        if primary == "TABLE_OR_METRIC_BINDING_UNRESOLVED":
            route_intake.append(_pending_record(ROUTE_INTAKE_PROTOCOL, qid, {
                **common,
                "required_receipt_fields": ["table_identity", "table_role", "metric_row_anchor", "header_path", "period_column", "unit_scale", "independent_approval_id"],
            }))
            next_queue[qid] = "route_table_metric_binding"
        elif primary == "FORMULA_OR_OPERATOR_DEFINITION_UNRESOLVED":
            route_operator_intake.append(_pending_record(ROUTE_OPERATOR_INTAKE_PROTOCOL, qid, {
                **common,
                "operation_plan": _operation_summary(plans[qid]),
                "required_receipt_fields": ["operator_definition", "operand_roles", "operation_ast", "source_anchors", "independent_approval_id"],
            }))
            next_queue[qid] = "route_formula_operator_definition"
        elif primary == "ROUTE_CAUSE_UNESTABLISHED":
            route_cause_intake.append(_pending_record(ROUTE_CAUSE_INTAKE_PROTOCOL, qid, {
                **common,
                "required_investigation": ["source_search_audit", "candidate_table_inventory", "retrieval_failure_reason", "independent_review_id"],
            }))
            next_queue[qid] = "route_cause_investigation"
        else:
            raise ValueError(f"unsupported route blocker for Q{qid}: {primary}")
    temporal_intake = []
    for row in temporal:
        qid = int(row["question_id"])
        requirement = _requirement(requirements[qid], "temporal.period")
        temporal_intake.append(_pending_record(TEMPORAL_INTAKE_PROTOCOL, qid, {
            "claim_requirement_set_id": requirements[qid].get("claim_requirement_set_id"),
            "temporal_obligation_id": requirement.get("obligation_id"),
            "claim_temporal_requirement": row.get("claim_requirement"),
            "source_temporal_observation": row.get("source_observation"),
            "prior_proof_status": row.get("proof_status"),
            "required_receipt_fields": ["period_kind", "period_start", "period_end", "period_role", "source_expression_anchor", "independent_approval_id"],
        }))
        next_queue[qid] = "temporal_receipt"
    recertification_intake = []
    for qid in sorted(question_ids):
        certificate = certificates[qid]
        if certificate.get("v12_certificate_status") != "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY":
            continue
        requirement = requirements[qid]
        pending = [
            {"dimension": item.get("dimension"), "status": item.get("status"), "reason_codes": item.get("reason_codes") or []}
            for item in certificate.get("proof_obligations") or []
            if item.get("status") != "PASS"
        ]
        recertification_intake.append(_pending_record(RECERTIFICATION_INTAKE_PROTOCOL, qid, {
            "claim_requirement_set_id": requirement.get("claim_requirement_set_id"),
            "v12_answer_certificate_id": certificate.get("v12_answer_certificate_id"),
            "v12_certificate_status": certificate.get("v12_certificate_status"),
            "pending_v13_obligations": pending,
            "recertification_policy": "V12 campaign completeness cannot authorize a V13 answer.",
        }))
        next_queue[qid] = "v12_candidate_recertification"
    independent_packets = []
    ledger_intake = []
    for qid in sorted(question_ids):
        requirement = requirements[qid]
        plan = plans[qid]
        claim = str(requirement.get("claim") or "")
        independent_packets.append(_pending_record(INDEPENDENT_UNIVERSE_PROTOCOL, qid, {
            "raw_claim": claim,
            "raw_claim_sha256": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
            "candidate_operation_plan": _operation_summary(plan),
            "required_reviewer_actions": ["derive_claim_requirements_without_using_v13_output", "record_proposition_spans", "approve_or_reject_each_requirement"],
            "independence_status": "PENDING_EXTERNAL_REVIEW",
            "authoritative_requirement_universe": False,
            "decision_policy": "This packet is a prompt for an independent reviewer, not an independent decision.",
        }))
        ledger_intake.append(_blocked_ledger_record(qid, certificates[qid], next_queue.get(qid, "semantic_recertification")))
    expected_counts = {
        "formula_definition_receipt_intake": 501,
        "operand_compatibility_receipt_intake": 94,
        "route_binding_receipt_intake": 250,
        "route_operator_receipt_intake": 37,
        "route_cause_investigation_intake": 66,
        "temporal_receipt_intake": 38,
        "v12_candidate_recertification_intake": 26,
        "independent_requirement_review_packets": 1012,
        "production_execution_ledger_intake": 1012,
    }
    actual_counts = {
        "formula_definition_receipt_intake": len(formula_intake),
        "operand_compatibility_receipt_intake": len(operand_intake),
        "route_binding_receipt_intake": len(route_intake),
        "route_operator_receipt_intake": len(route_operator_intake),
        "route_cause_investigation_intake": len(route_cause_intake),
        "temporal_receipt_intake": len(temporal_intake),
        "v12_candidate_recertification_intake": len(recertification_intake),
        "independent_requirement_review_packets": len(independent_packets),
        "production_execution_ledger_intake": len(ledger_intake),
    }
    if actual_counts != expected_counts:
        raise ValueError(f"closure queue counts changed unexpectedly: {actual_counts}")
    formula_candidate_counts = Counter(item["formula_evidence_candidate"]["status"] for item in formula_intake)
    operand_candidate_counts = Counter(item["formula_evidence_candidate"]["status"] for item in operand_intake)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        output_rows = {
            "formula_definition_receipt_intake": ("formula_definition_receipt_intake_v1.jsonl", formula_intake),
            "operand_compatibility_receipt_intake": ("operand_compatibility_receipt_intake_v1.jsonl", operand_intake),
            "route_binding_receipt_intake": ("route_binding_receipt_intake_v1.jsonl", route_intake),
            "route_operator_receipt_intake": ("route_operator_receipt_intake_v1.jsonl", route_operator_intake),
            "route_cause_investigation_intake": ("route_cause_investigation_intake_v1.jsonl", route_cause_intake),
            "temporal_receipt_intake": ("temporal_receipt_intake_v1.jsonl", temporal_intake),
            "v12_candidate_recertification_intake": ("v12_candidate_recertification_intake_v1.jsonl", recertification_intake),
            "independent_requirement_review_packets": ("independent_requirement_review_packets_v1.jsonl", independent_packets),
            "production_execution_ledger_intake": ("production_execution_ledger_intake_v1.jsonl", ledger_intake),
        }
        output_paths: dict[str, Path] = {}
        for name, (filename, rows) in output_rows.items():
            output_paths[name] = staging / filename
            _write_jsonl(output_paths[name], rows)
        definition_bundle = {
            "implementation_path": str(Path(__file__).resolve()),
            "implementation_sha256": sha256_file(Path(__file__)),
            "config_sha256": sha256_file(config_path),
            "v13_manifest_sha256": sha256_file(inputs["v13_manifest"]),
        }
        summary = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "status": "closure_intakes_materialized_pending_independent_review",
            "definition_bundle": definition_bundle,
            "counts": actual_counts,
            "candidate_evidence_counts": {
                "formula_definition": dict(sorted(formula_candidate_counts.items())),
                "operand_compatibility": dict(sorted(operand_candidate_counts.items())),
            },
            "release_decision": {
                "status": "blocked",
                "reason_codes": ["PENDING_INDEPENDENT_RECEIPT_REVIEW", "INDEPENDENT_REQUIREMENT_UNIVERSE_PENDING", "PRODUCTION_EXECUTION_LEDGER_PENDING"],
            },
            "source_contract": source_contract(),
        }
        summary_path = staging / "v13_evidence_closure_workbench_summary.json"
        _write_json(summary_path, summary)
        inputs_manifest = {
            name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for name, path in {"config": config_path, **inputs}.items()
        }
        manifest = {
            **summary,
            "inputs": inputs_manifest,
            "outputs": {
                name: {"path": str(output_dir / path.name), "sha256": sha256_file(path)}
                for name, path in output_paths.items()
            } | {"summary": {"path": str(output_dir / summary_path.name), "sha256": sha256_file(summary_path)}},
        }
        manifest_path = staging / "v13_evidence_closure_workbench.manifest.json"
        _write_json(manifest_path, manifest)
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {**manifest, "manifest_path": str(output_dir / "v13_evidence_closure_workbench.manifest.json")}
