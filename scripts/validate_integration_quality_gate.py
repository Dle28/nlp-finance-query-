#!/usr/bin/env python3
"""Validate the fail-closed integration policy and immutable local artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from finance_query.config import ModelConfig


PROTOCOL = "vifinqa_integration_quality_gate_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"quality gate requires mapping: {label}")
    return value


def _resolve(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"quality gate requires path: {label}")
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_bound_json(root: Path, record: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = _resolve(root, record.get("path"), label)
    expected_sha = record.get("sha256")
    if not path.is_file() or not isinstance(expected_sha, str) or sha256_file(path) != expected_sha:
        raise ValueError(f"immutable artifact mismatch: {label}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"immutable artifact must be a JSON object: {label}")
    return value


def _load_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{label}:{line_number} must be a JSON object")
        rows.append(value)
    return rows


def _contains_forbidden_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(key in forbidden or _contains_forbidden_key(child, forbidden) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_key(child, forbidden) for child in value)
    return False


def validate(*, policy_path: Path, repository_root: Path, policy_only: bool = False) -> dict[str, Any]:
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    if policy.get("protocol") != PROTOCOL or policy.get("schema_version") != 1:
        raise ValueError("unexpected integration quality gate protocol")
    defaults = _mapping(policy.get("safety_defaults"), "safety_defaults")
    if defaults.get("hierarchy_rrf_enabled") is not False or ModelConfig().hierarchy_rrf_enabled:
        raise ValueError("hierarchy RRF must remain disabled by default")
    if defaults.get("evidence_eligible") is not False or defaults.get("promotion_allowed") is not False:
        raise ValueError("quality gate must remain research-only and non-promotable")
    for label in (
        "reviewer_authority_policy",
        "baseline", "hierarchy_ablations", "coverage_diagnostics", "human_handoff",
        "context_unit_v3", "route_semantics_v5", "operation_graph_review",
        "binding_conflict_workbench", "human_handoff_v3", "period_title_recovery_v2",
        "grounded_v4", "grounded_v5_human_verified", "grounded_campaign_review_v5", "binding_conflict_workbench_v4", "operation_graph_candidates_v1",
        "route_context_repairs_v1", "navigation_remediation_v1", "human_handoff_v4",
        "entity_role_provenance_candidates_v1", "entity_role_chatgpt_reviews_v1",
        "entity_role_augmentation_v7", "grounded_v7_document_role",
        "grounded_campaign_review_v7", "grounded_campaign_chatgpt_audit_v7",
        "semantic_binding_chatgpt_correction_q702_v2",
        "grounded_v8_semantic_correction", "grounded_campaign_review_v8",
        "grounded_campaign_chatgpt_audit_v8",
        "navigation_binding_chatgpt_promotion_q167_v1",
            "grounded_v9_navigation_promotion", "grounded_campaign_review_v9",
            "grounded_campaign_chatgpt_audit_v9",
            "cross_entity_binding_chatgpt_promotion_q750_v1",
            "grounded_v10_cross_entity", "grounded_campaign_review_v10",
            "grounded_campaign_chatgpt_audit_v10",
            "cross_entity_operand_packets_q746_v2",
            "cross_entity_operand_dual_chatgpt_adjudication_q746_v2",
            "cross_entity_subtract_materialization_q746_v2",
            "cross_entity_binding_chatgpt_promotion_q746_q750_v2",
            "grounded_v11_dual_review_cross_entity", "grounded_campaign_review_v11",
            "grounded_campaign_chatgpt_audit_v11",
            "entity_role_relational_candidates_v1",
            "entity_role_relational_chatgpt_proposal_v1",
            "entity_role_relational_chatgpt_critic_v1",
            "entity_role_relational_dual_chatgpt_v1",
            "semantic_binding_relational_role_augmentation_v12",
            "grounded_v12_relational_entity_role", "grounded_campaign_review_v12",
            "grounded_campaign_chatgpt_audit_v12",
        "operation_graph_chatgpt_review_v1",
        "cross_entity_operand_packets_v1",
        "cross_entity_operand_chatgpt_review_v1",
        "cross_entity_subtract_materialization_v1",
        "route_context_chatgpt_review_v1",
        "route_context_chatgpt_promotion_v1",
        "navigation_review_evidence_v1", "navigation_chatgpt_review_v1",
        "security_review",
    ):
        _mapping(policy.get(label), label)
    security_path = _resolve(repository_root, _mapping(policy["security_review"], "security_review").get("path"), "security_review")
    if not security_path.is_file():
        raise ValueError("security review is missing")
    reviewer_authority = _load_bound_json(
        repository_root,
        _mapping(policy["reviewer_authority_policy"], "reviewer_authority_policy"),
        "reviewer authority policy",
    )
    authority_grant = _mapping(reviewer_authority.get("grant"), "reviewer_authority_policy.grant")
    excluded_authorities = _mapping(
        reviewer_authority.get("excluded_authorities"),
        "reviewer_authority_policy.excluded_authorities",
    )
    required_scopes = {
        "semantic_binding_review_gate_equivalence",
        "entity_role_review_gate_equivalence",
        "campaign_review_gate_equivalence",
    }
    if (
        reviewer_authority.get("protocol") != "vifinqa_reviewer_authority_policy_v1"
        or authority_grant.get("reviewer_type") != "chatgpt_verified"
        or authority_grant.get("verification_authority") != "human_equivalent"
        or authority_grant.get("gate_effect") != "same_eligibility_weight_as_human_verified"
        or authority_grant.get("review_policy") != "fail_closed_evidence_bound_v1"
        or not required_scopes.issubset(set(reviewer_authority.get("allowed_scopes") or []))
        or any(value is not False for value in excluded_authorities.values())
    ):
        raise ValueError("reviewer authority policy widens or mislabels the ChatGPT grant")
    if policy_only:
        return {"status": "policy_only_pass", "artifact_validation": False}

    baseline_policy = _mapping(policy["baseline"], "baseline")
    baseline = _load_bound_json(repository_root, baseline_policy, "baseline")
    if baseline.get("run_status") != baseline_policy.get("run_status"):
        raise ValueError("baseline run status mismatch")
    reproducibility = _mapping(baseline.get("reproducibility"), "baseline.reproducibility")
    if not reproducibility or not all(value is True for value in reproducibility.values()):
        raise ValueError("baseline reproducibility gate failed")
    outputs = _mapping(baseline.get("outputs"), "baseline.outputs")
    token_counts = _mapping(_mapping(outputs.get("numeric_cell_tokens"), "numeric_cell_tokens").get("counts"), "token_counts")
    execution_counts = _mapping(_mapping(outputs.get("execution"), "execution").get("counts"), "execution_counts")
    authorization_counts = _mapping(_mapping(outputs.get("authorization"), "authorization").get("counts"), "authorization_counts")
    checks = {
        "question_count": execution_counts.get("question_count"),
        "token_count": token_counts.get("token_count"),
        "sandbox_execution_count": execution_counts.get("sandbox_execution_count"),
        "execution_status_counts": execution_counts.get("execution_status_counts"),
        "answer_certificate_status_counts": authorization_counts.get("answer_certificate_status_counts"),
        "human_semantic_approval_count": authorization_counts.get("human_semantic_approval_count"),
    }
    for name, actual in checks.items():
        if actual != baseline_policy.get(name):
            raise ValueError(f"baseline threshold mismatch: {name}")
    source_contract = _mapping(baseline.get("source_contract"), "baseline.source_contract")
    if source_contract.get("may_materialize_answer") is not False or source_contract.get("promotion_allowed") is not False:
        raise ValueError("baseline answer/promotion gate is unsafe")

    for label, record_value in _mapping(policy["hierarchy_ablations"], "hierarchy_ablations").items():
        record = _mapping(record_value, f"hierarchy_ablations.{label}")
        artifact = _load_bound_json(repository_root, record, f"hierarchy_ablations.{label}")
        observed_filter = bool((_mapping(artifact.get("configuration"), "configuration")).get("explicit_context_filter", False))
        if observed_filter is not record.get("explicit_context_filter"):
            raise ValueError(f"hierarchy context-filter mismatch: {label}")
        for model_label, expected_status in _mapping(record.get("expected_statuses"), "expected_statuses").items():
            assessment = _mapping(_mapping(_mapping(artifact.get("results"), "results").get(model_label), model_label).get("assessment"), "assessment")
            if assessment.get("status") != expected_status:
                raise ValueError(f"hierarchy assessment mismatch: {label}/{model_label}")

    coverage_policy = _mapping(policy["coverage_diagnostics"], "coverage_diagnostics")
    coverage = _load_bound_json(repository_root, coverage_policy, "coverage_diagnostics")
    coverage_counts = _mapping(coverage.get("counts"), "coverage.counts")
    for name in ("route_remediation_count", "binding_conflict_remediation_count"):
        if coverage_counts.get(name) != coverage_policy.get(name):
            raise ValueError(f"coverage threshold mismatch: {name}")
    handoff_policy = _mapping(policy["human_handoff"], "human_handoff")
    handoff = _load_bound_json(repository_root, handoff_policy, "human_handoff")
    handoff_counts = _mapping(handoff.get("counts"), "handoff.counts")
    for name in ("review_item_count", "human_decision_count"):
        if handoff_counts.get(name) != handoff_policy.get(name):
            raise ValueError(f"human handoff threshold mismatch: {name}")
    if _mapping(handoff.get("source_contract"), "handoff.source_contract").get("authorization_allowed") is not False:
        raise ValueError("blank human handoff must not authorize")

    v3_policy = _mapping(policy["context_unit_v3"], "context_unit_v3")
    v3 = _load_bound_json(repository_root, v3_policy, "context_unit_v3")
    if v3.get("binding_protocol") != v3_policy.get("binding_protocol"):
        raise ValueError("V3 binding protocol mismatch")
    v3_reproducibility = _mapping(v3.get("reproducibility"), "context_unit_v3.reproducibility")
    if len(v3_reproducibility) != 5 or not all(value is True for value in v3_reproducibility.values()):
        raise ValueError("V3 reproducibility gate failed")
    v3_outputs = _mapping(v3.get("outputs"), "context_unit_v3.outputs")
    v3_token_counts = _mapping(_mapping(v3_outputs.get("numeric_cell_tokens"), "v3.tokens").get("counts"), "v3.token_counts")
    v3_execution_counts = _mapping(_mapping(v3_outputs.get("execution"), "v3.execution").get("counts"), "v3.execution_counts")
    v3_authorization_counts = _mapping(_mapping(v3_outputs.get("authorization"), "v3.authorization").get("counts"), "v3.authorization_counts")
    v3_checks = {
        "token_count": v3_token_counts.get("token_count"),
        "sandbox_execution_count": v3_execution_counts.get("sandbox_execution_count"),
        "execution_status_counts": v3_execution_counts.get("execution_status_counts"),
        "answer_certificate_status_counts": v3_authorization_counts.get("answer_certificate_status_counts"),
        "human_semantic_approval_count": v3_authorization_counts.get("human_semantic_approval_count"),
    }
    for name, actual in v3_checks.items():
        if actual != v3_policy.get(name):
            raise ValueError(f"V3 threshold mismatch: {name}")

    route_policy = _mapping(policy["route_semantics_v5"], "route_semantics_v5")
    route_artifact = _load_bound_json(repository_root, route_policy, "route_semantics_v5")
    if _mapping(route_artifact.get("counts"), "route.counts").get("route_status_counts") != route_policy.get("route_status_counts"):
        raise ValueError("route semantics status counts mismatch")
    for section, count_names in (
        ("operation_graph_review", ("queue_item_count", "human_decision_count", "queue_status_counts")),
        ("binding_conflict_workbench", ("binding_conflict_count", "source_title_unit_candidate_count", "human_decision_count")),
        ("human_handoff_v3", ("review_item_count", "human_decision_count")),
    ):
        section_policy = _mapping(policy[section], section)
        artifact = _load_bound_json(repository_root, section_policy, section)
        counts = _mapping(artifact.get("counts"), f"{section}.counts")
        for name in count_names:
            if counts.get(name) != section_policy.get(name):
                raise ValueError(f"{section} threshold mismatch: {name}")

    period_policy = _mapping(policy["period_title_recovery_v2"], "period_title_recovery_v2")
    period_artifact = _load_bound_json(repository_root, period_policy, "period_title_recovery_v2")
    for name in ("recovered_question_count", "packet_status_counts", "recovery_reason_counts"):
        if period_artifact.get(name) != period_policy.get(name):
            raise ValueError(f"period title recovery threshold mismatch: {name}")

    v4_policy = _mapping(policy["grounded_v4"], "grounded_v4")
    v4 = _load_bound_json(repository_root, v4_policy, "grounded_v4")
    if v4.get("binding_protocol") != v4_policy.get("binding_protocol"):
        raise ValueError("V4 binding protocol mismatch")
    v4_reproducibility = _mapping(v4.get("reproducibility"), "grounded_v4.reproducibility")
    if len(v4_reproducibility) != 5 or not all(value is True for value in v4_reproducibility.values()):
        raise ValueError("V4 reproducibility gate failed")
    v4_outputs = _mapping(v4.get("outputs"), "grounded_v4.outputs")
    v4_tokens = _mapping(_mapping(v4_outputs.get("numeric_cell_tokens"), "v4.tokens").get("counts"), "v4.token_counts")
    v4_execution = _mapping(_mapping(v4_outputs.get("execution"), "v4.execution").get("counts"), "v4.execution_counts")
    v4_authorization = _mapping(_mapping(v4_outputs.get("authorization"), "v4.authorization").get("counts"), "v4.authorization_counts")
    v4_checks = {
        "token_count": v4_tokens.get("token_count"),
        "sandbox_execution_count": v4_execution.get("sandbox_execution_count"),
        "execution_status_counts": v4_execution.get("execution_status_counts"),
        "answer_certificate_status_counts": v4_authorization.get("answer_certificate_status_counts"),
        "human_semantic_approval_count": v4_authorization.get("human_semantic_approval_count"),
        "period_field_status_counts": _mapping(v4_authorization.get("field_status_counts"), "v4.field_status_counts").get("period"),
    }
    for name, actual in v4_checks.items():
        if actual != v4_policy.get(name):
            raise ValueError(f"V4 threshold mismatch: {name}")

    v5_policy = _mapping(policy["grounded_v5_human_verified"], "grounded_v5_human_verified")
    v5 = _load_bound_json(repository_root, v5_policy, "grounded_v5_human_verified")
    if v5.get("binding_protocol") != v5_policy.get("binding_protocol"):
        raise ValueError("V5 binding protocol mismatch")
    v5_reproducibility = _mapping(v5.get("reproducibility"), "grounded_v5_human_verified.reproducibility")
    if len(v5_reproducibility) != 5 or not all(value is True for value in v5_reproducibility.values()):
        raise ValueError("V5 reproducibility gate failed")
    v5_outputs = _mapping(v5.get("outputs"), "grounded_v5_human_verified.outputs")
    v5_tokens = _mapping(_mapping(v5_outputs.get("numeric_cell_tokens"), "v5.tokens").get("counts"), "v5.token_counts")
    v5_execution = _mapping(_mapping(v5_outputs.get("execution"), "v5.execution").get("counts"), "v5.execution_counts")
    v5_authorization_output = _mapping(v5_outputs.get("authorization"), "v5.authorization")
    v5_authorization = _mapping(v5_authorization_output.get("counts"), "v5.authorization_counts")
    v5_checks = {
        "token_count": v5_tokens.get("token_count"),
        "sandbox_execution_count": v5_execution.get("sandbox_execution_count"),
        "execution_status_counts": v5_execution.get("execution_status_counts"),
        "answer_certificate_status_counts": v5_authorization.get("answer_certificate_status_counts"),
        "evidence_binding_status_counts": v5_authorization.get("evidence_binding_status_counts"),
        "human_semantic_approval_count": v5_authorization.get("human_semantic_approval_count"),
    }
    for name, actual in v5_checks.items():
        if actual != v5_policy.get(name):
            raise ValueError(f"V5 threshold mismatch: {name}")
    readiness_path = _resolve(repository_root, v5_authorization_output.get("readiness_path"), "v5.authorization_readiness")
    expected_readiness_sha = v5_authorization_output.get("readiness_sha256")
    if not readiness_path.is_file() or not isinstance(expected_readiness_sha, str) or sha256_file(readiness_path) != expected_readiness_sha:
        raise ValueError("immutable artifact mismatch: V5 authorization readiness")
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    if (
        not isinstance(readiness, dict)
        or readiness.get("release_authorized") is not v5_policy.get("release_authorized")
        or readiness.get("authorization_status") != v5_policy.get("authorization_status")
        or readiness.get("independent_audit_required") is not True
        or readiness.get("release_gate_required") is not True
    ):
        raise ValueError("V5 release gate status mismatch")
    v5_contract = _mapping(v5.get("source_contract"), "grounded_v5_human_verified.source_contract")
    if v5_contract.get("may_materialize_answer") is not False or v5_contract.get("promotion_allowed") is not False:
        raise ValueError("V5 answer/promotion gate is unsafe")

    campaign_policy = _mapping(policy["grounded_campaign_review_v5"], "grounded_campaign_review_v5")
    campaign = _load_bound_json(repository_root, campaign_policy, "grounded_campaign_review_v5")
    for name in ("campaign_status", "candidate_count", "human_campaign_decision_count"):
        if campaign.get(name) != campaign_policy.get(name):
            raise ValueError(f"V5 campaign review threshold mismatch: {name}")
    campaign_contract = _mapping(campaign.get("source_contract"), "grounded_campaign_review_v5.source_contract")
    if campaign_contract.get("may_materialize_answer") is not False or campaign_contract.get("promotion_allowed") is not False:
        raise ValueError("V5 campaign review gate is unsafe")
    for name, record_value in _mapping(campaign.get("outputs"), "grounded_campaign_review_v5.outputs").items():
        record = _mapping(record_value, f"grounded_campaign_review_v5.outputs.{name}")
        path = _resolve(repository_root, record.get("path"), f"grounded_campaign_review_v5.outputs.{name}")
        if not path.is_file() or not isinstance(record.get("sha256"), str) or sha256_file(path) != record["sha256"]:
            raise ValueError(f"immutable artifact mismatch: V5 campaign review {name}")

    for section, count_names in (
        ("binding_conflict_workbench_v4", ("binding_conflict_count", "classification_counts", "human_decision_count")),
        ("operation_graph_candidates_v1", ("candidate_item_count", "candidate_status_counts")),
        ("route_context_repairs_v1", ("queue_item_count", "repair_status_counts", "human_decision_count")),
        ("route_context_chatgpt_promotion_v1", ("queue_item_count", "promoted_question_count", "queue_status_counts")),
        ("navigation_remediation_v1", ("queue_item_count", "primary_cause_counts", "automatic_materialization_eligible_count", "human_decision_count")),
        ("human_handoff_v4", ("review_item_count", "human_decision_count")),
    ):
        section_policy = _mapping(policy[section], section)
        artifact = _load_bound_json(repository_root, section_policy, section)
        counts = _mapping(artifact.get("counts"), f"{section}.counts")
        for name in count_names:
            if counts.get(name) != section_policy.get(name):
                raise ValueError(f"{section} threshold mismatch: {name}")
        contract = _mapping(artifact.get("source_contract"), f"{section}.source_contract")
        if contract.get("promotion_allowed") is not False:
            raise ValueError(f"{section} must remain non-promotable")
        if section == "route_context_chatgpt_promotion_v1" and (
            contract.get("chatgpt_gate_equivalence_applied") is not True
            or contract.get("allowed_fields") != ["controlled_operation_contract"]
            or contract.get("eligible_for_graph_review") is not True
            or contract.get("may_change_question_plan") is not False
            or contract.get("may_change_route") is not False
            or contract.get("may_select_value") is not False
            or contract.get("may_execute_formula") is not False
            or contract.get("release_authorized") is not False
        ):
            raise ValueError("route-context promotion authority boundary is invalid")

    for section, count_names in (
        (
            "entity_role_provenance_candidates_v1",
            (
                "queue_item_count", "candidate_count", "documents_with_role_literal",
                "documents_without_role_literal", "already_bound_in_source_title",
            ),
        ),
        (
            "entity_role_chatgpt_reviews_v1",
            ("decision_count", "approved_role_provenance", "rejected_candidate_set"),
        ),
        (
            "entity_role_augmentation_v7",
            (
                "decision_count", "document_line_role_augmented",
                "existing_title_role_preserved", "role_not_applicable",
                "role_review_rejected",
            ),
        ),
        (
            "operation_graph_chatgpt_review_v1",
            (
                "decision_count", "approve_graph_semantics",
                "confirmed_blocker",
            ),
        ),
        (
            "route_context_chatgpt_review_v1",
            (
                "decision_count", "approve_all_literal_candidates",
                "confirmed_incomplete_literal_blocker", "candidate_decision_counts",
            ),
        ),
        (
            "navigation_review_evidence_v1",
            (
                "packet_count", "candidate_count",
                "exact_identity_scope_year_table_count",
                "numeric_value_exposure_count", "primary_cause_counts",
            ),
        ),
        (
            "navigation_chatgpt_review_v1",
            (
                "decision_count", "approve_navigation_candidate_semantics",
                "reject_candidate_set", "confirmed_upstream_blocker",
                "candidate_decision_counts",
            ),
        ),
    ):
        section_policy = _mapping(policy[section], section)
        artifact = _load_bound_json(repository_root, section_policy, section)
        counts = _mapping(artifact.get("counts"), f"{section}.counts")
        for name in count_names:
            if counts.get(name) != section_policy.get(name):
                raise ValueError(f"{section} threshold mismatch: {name}")
        contract = _mapping(artifact.get("source_contract"), f"{section}.source_contract")
        if contract.get("promotion_allowed") is not False:
            raise ValueError(f"{section} must remain non-promotable")
        if section == "entity_role_chatgpt_reviews_v1" and (
            contract.get("chatgpt_authority_grant_present") is not True
            or contract.get("release_authorized") is not False
        ):
            raise ValueError("entity-role ChatGPT review authority boundary is invalid")
        if section == "entity_role_augmentation_v7" and (
            contract.get("original_human_cell_provenance_preserved") is not True
            or contract.get("chatgpt_role_review_separate") is not True
        ):
            raise ValueError("V7 role augmentation provenance boundary is invalid")
        if section == "operation_graph_chatgpt_review_v1" and (
            contract.get("chatgpt_authority_grant_present") is not True
            or contract.get("eligible_for_materialization") is not False
            or contract.get("may_execute_formula") is not False
            or contract.get("release_authorized") is not False
        ):
            raise ValueError("operation-graph review execution boundary is invalid")
        if section == "route_context_chatgpt_review_v1" and (
            contract.get("chatgpt_authority_grant_present") is not True
            or contract.get("eligible_for_materialization") is not False
            or contract.get("may_change_question_plan") is not False
            or contract.get("may_change_route") is not False
            or contract.get("may_execute_formula") is not False
            or contract.get("release_authorized") is not False
        ):
            raise ValueError("route-context ChatGPT review authority boundary is invalid")
        if section == "navigation_review_evidence_v1" and (
            contract.get("review_evidence_only") is not True
            or contract.get("numeric_values_exposed") is not False
            or contract.get("eligible_for_materialization") is not False
            or contract.get("may_change_route") is not False
            or contract.get("may_select_value") is not False
            or contract.get("may_execute_formula") is not False
        ):
            raise ValueError("navigation evidence boundary is invalid")
        if section == "navigation_chatgpt_review_v1" and (
            contract.get("chatgpt_authority_grant_present") is not True
            or contract.get("eligible_for_materialization") is not False
            or contract.get("may_change_question_plan") is not False
            or contract.get("may_change_route") is not False
            or contract.get("may_select_value") is not False
            or contract.get("may_execute_formula") is not False
            or contract.get("release_authorized") is not False
        ):
            raise ValueError("navigation ChatGPT review authority boundary is invalid")

    v7_policy = _mapping(policy["grounded_v7_document_role"], "grounded_v7_document_role")
    v7 = _load_bound_json(repository_root, v7_policy, "grounded_v7_document_role")
    if v7.get("binding_protocol") != v7_policy.get("binding_protocol"):
        raise ValueError("V7 binding protocol mismatch")
    v7_reproducibility = _mapping(v7.get("reproducibility"), "grounded_v7_document_role.reproducibility")
    if len(v7_reproducibility) != 5 or not all(value is True for value in v7_reproducibility.values()):
        raise ValueError("V7 reproducibility gate failed")
    v7_outputs = _mapping(v7.get("outputs"), "grounded_v7_document_role.outputs")
    v7_tokens = _mapping(_mapping(v7_outputs.get("numeric_cell_tokens"), "v7.tokens").get("counts"), "v7.token_counts")
    v7_execution = _mapping(_mapping(v7_outputs.get("execution"), "v7.execution").get("counts"), "v7.execution_counts")
    v7_authorization_output = _mapping(v7_outputs.get("authorization"), "v7.authorization")
    v7_authorization = _mapping(v7_authorization_output.get("counts"), "v7.authorization_counts")
    v7_checks = {
        "token_count": v7_tokens.get("token_count"),
        "sandbox_execution_count": v7_execution.get("sandbox_execution_count"),
        "execution_status_counts": v7_execution.get("execution_status_counts"),
        "answer_certificate_status_counts": v7_authorization.get("answer_certificate_status_counts"),
        "evidence_binding_status_counts": v7_authorization.get("evidence_binding_status_counts"),
        "entity_role_field_status_counts": _mapping(
            v7_authorization.get("field_status_counts"), "v7.field_status_counts"
        ).get("entity_role"),
        "human_semantic_approval_count": v7_authorization.get("human_semantic_approval_count"),
    }
    for name, actual in v7_checks.items():
        if actual != v7_policy.get(name):
            raise ValueError(f"V7 threshold mismatch: {name}")
    v7_readiness_path = _resolve(
        repository_root,
        v7_authorization_output.get("readiness_path"),
        "v7.authorization_readiness",
    )
    expected_v7_readiness_sha = v7_authorization_output.get("readiness_sha256")
    if (
        not v7_readiness_path.is_file()
        or not isinstance(expected_v7_readiness_sha, str)
        or sha256_file(v7_readiness_path) != expected_v7_readiness_sha
    ):
        raise ValueError("immutable artifact mismatch: V7 authorization readiness")
    v7_readiness = json.loads(v7_readiness_path.read_text(encoding="utf-8"))
    if (
        not isinstance(v7_readiness, dict)
        or v7_readiness.get("release_authorized") is not v7_policy.get("release_authorized")
        or v7_readiness.get("authorization_status") != v7_policy.get("authorization_status")
        or v7_readiness.get("release_gate_required") is not True
    ):
        raise ValueError("V7 release gate status mismatch")
    v7_contract = _mapping(v7.get("source_contract"), "grounded_v7_document_role.source_contract")
    if v7_contract.get("may_materialize_answer") is not False or v7_contract.get("promotion_allowed") is not False:
        raise ValueError("V7 answer/promotion gate is unsafe")

    v7_campaign_policy = _mapping(policy["grounded_campaign_review_v7"], "grounded_campaign_review_v7")
    v7_campaign = _load_bound_json(repository_root, v7_campaign_policy, "grounded_campaign_review_v7")
    for name in ("campaign_status", "candidate_count", "human_campaign_decision_count"):
        if v7_campaign.get(name) != v7_campaign_policy.get(name):
            raise ValueError(f"V7 campaign handoff threshold mismatch: {name}")
    v7_campaign_outputs = _mapping(v7_campaign.get("outputs"), "grounded_campaign_review_v7.outputs")
    for name, record_value in v7_campaign_outputs.items():
        record = _mapping(record_value, f"grounded_campaign_review_v7.outputs.{name}")
        path = _resolve(repository_root, record.get("path"), f"grounded_campaign_review_v7.outputs.{name}")
        if not path.is_file() or not isinstance(record.get("sha256"), str) or sha256_file(path) != record["sha256"]:
            raise ValueError(f"immutable artifact mismatch: V7 campaign handoff {name}")
    role_issue_record = _mapping(v7_campaign_outputs.get("entity_role_issue_briefs"), "V7 role issues")
    role_issue_path = _resolve(repository_root, role_issue_record.get("path"), "V7 role issues")
    if len(_load_jsonl(role_issue_path, "V7 role issues")) != v7_campaign_policy.get("entity_role_issue_count"):
        raise ValueError("V7 campaign entity-role issue count mismatch")
    v7_campaign_contract = _mapping(v7_campaign.get("source_contract"), "grounded_campaign_review_v7.source_contract")
    if v7_campaign_contract.get("may_materialize_answer") is not False or v7_campaign_contract.get("promotion_allowed") is not False:
        raise ValueError("V7 campaign handoff authority boundary is unsafe")

    audit_policy = _mapping(policy["grounded_campaign_chatgpt_audit_v7"], "grounded_campaign_chatgpt_audit_v7")
    audit = _load_bound_json(repository_root, audit_policy, "grounded_campaign_chatgpt_audit_v7")
    for name in (
        "status", "candidate_count", "candidate_review_outcome_counts",
        "blocking_issue_count", "numeric_value_exposure_count",
        "scope_coverage", "period_method_coverage",
    ):
        if audit.get(name) != audit_policy.get(name):
            raise ValueError(f"V7 campaign ChatGPT audit threshold mismatch: {name}")
    audit_contract = _mapping(audit.get("source_contract"), "grounded_campaign_chatgpt_audit_v7.source_contract")
    if (
        audit_contract.get("numeric_literals_exposed_to_reviewer") is not False
        or audit_contract.get("may_materialize_answer") is not False
        or audit_contract.get("promotion_allowed") is not False
        or audit_contract.get("release_authorized") is not False
    ):
        raise ValueError("V7 campaign ChatGPT audit authority boundary is unsafe")
    audit_decision_record = _mapping(_mapping(audit.get("outputs"), "audit.outputs").get("decision"), "audit.decision")
    audit_decision_path = _resolve(repository_root, audit_decision_record.get("path"), "audit.decision")
    if (
        not audit_decision_path.is_file()
        or not isinstance(audit_decision_record.get("sha256"), str)
        or sha256_file(audit_decision_path) != audit_decision_record["sha256"]
    ):
        raise ValueError("immutable artifact mismatch: V7 campaign ChatGPT decision")
    audit_decisions = _load_jsonl(audit_decision_path, "V7 campaign ChatGPT decision")
    if len(audit_decisions) != 1:
        raise ValueError("V7 campaign ChatGPT audit requires exactly one decision")
    audit_decision = audit_decisions[0]
    audit_provenance = _mapping(audit_decision.get("decision_provenance"), "audit.decision_provenance")
    audit_authority = _mapping(audit_provenance.get("authority_grant"), "audit.authority_grant")
    candidate_reviews = audit_decision.get("candidate_reviews")
    q702_reviews = [review for review in candidate_reviews or [] if review.get("question_id") == 702]
    if (
        audit_decision.get("protocol") != "vifinqa_grounded_campaign_chatgpt_decision_v1"
        or audit_decision.get("campaign_id") != v7_campaign.get("campaign_id")
        or audit_decision.get("decision") != "needs_revision"
        or audit_decision.get("blocking_issue_count") != 1
        or audit_decision.get("release_authorized") is not False
        or audit_decision.get("promotion_allowed") is not False
        or audit_provenance.get("reviewer_type") != "chatgpt_verified"
        or audit_provenance.get("review_policy") != "fail_closed_evidence_bound_v1"
        or audit_authority.get("granted_by") != "campaign_owner"
        or audit_authority.get("grant_scope") != "campaign_review_gate_equivalence"
        or audit_authority.get("grant_basis") != "explicit_user_instruction"
        or not isinstance(candidate_reviews, list)
        or len(candidate_reviews) != 12
        or len(q702_reviews) != 1
        or q702_reviews[0].get("review_outcome") != "semantic_mismatch"
        or q702_reviews[0].get("reason_code") != "NET_OTHER_INCOME_NOT_PROVEN"
        or _contains_forbidden_key(candidate_reviews, {"answer_decimal", "raw_value"})
    ):
        raise ValueError("V7 campaign ChatGPT audit decision boundary is invalid")

    correction_policy = _mapping(
        policy["semantic_binding_chatgpt_correction_q702_v2"],
        "semantic_binding_chatgpt_correction_q702_v2",
    )
    correction = _load_bound_json(
        repository_root,
        correction_policy,
        "semantic_binding_chatgpt_correction_q702_v2",
    )
    for name in (
        "status",
        "correction_count",
        "numeric_literal_exposure_count",
        "superseded_human_decision_count",
    ):
        actual = correction.get(name)
        if name != "status":
            actual = _mapping(correction.get("counts"), "correction.counts").get(name)
        if actual != correction_policy.get(name):
            raise ValueError(f"Q702 semantic correction threshold mismatch: {name}")
    correction_contract = _mapping(correction.get("source_contract"), "correction.source_contract")
    correction_outputs = _mapping(correction.get("outputs"), "correction.outputs")
    correction_artifacts: dict[str, Path] = {}
    for name in ("packets", "decisions"):
        record = _mapping(correction_outputs.get(name), f"correction.outputs.{name}")
        artifact = _resolve(repository_root, record.get("path"), f"correction.outputs.{name}")
        if not artifact.is_file() or sha256_file(artifact) != record.get("sha256"):
            raise ValueError(f"immutable artifact mismatch: Q702 semantic correction {name}")
        correction_artifacts[name] = artifact
    packets = _load_jsonl(correction_artifacts["packets"], "Q702 correction packets")
    correction_decisions = _load_jsonl(correction_artifacts["decisions"], "Q702 correction decisions")
    correction_decision = correction_decisions[0] if len(correction_decisions) == 1 else {}
    correction_provenance = _mapping(
        correction_decision.get("decision_provenance"), "correction.decision_provenance"
    )
    correction_authority = _mapping(
        correction_provenance.get("authority_grant"), "correction.authority_grant"
    )
    reviewer_authority = _mapping(
        correction_decision.get("reviewer_authority"), "correction.reviewer_authority"
    )
    if (
        len(packets) != 1
        or packets[0].get("question_id") != 702
        or correction_decision.get("decision") != "approve_semantic_row_correction"
        or correction_decision.get("corrected_variable_id") != "other_profit"
        or correction_provenance.get("reviewer_type") != "chatgpt_verified"
        or correction_provenance.get("review_policy") != "fail_closed_evidence_bound_v1"
        or correction_authority.get("grant_scope") != "semantic_binding_review_gate_equivalence"
        or correction_authority.get("grant_basis") != "explicit_user_instruction"
        or reviewer_authority.get("may_select_semantic_row") is not True
        or reviewer_authority.get("may_select_value") is not False
        or reviewer_authority.get("may_change_binding") is not False
        or reviewer_authority.get("may_execute_formula") is not False
        or correction_decision.get("deterministic_materialization_required") is not True
        or correction_contract.get("numeric_literals_exposed_to_reviewer") is not False
        or correction_contract.get("release_authorized") is not False
        or _contains_forbidden_key(packets, {"answer_decimal", "raw_decimal_candidate", "raw_source_cell", "raw_value"})
    ):
        raise ValueError("Q702 ChatGPT semantic correction boundary is invalid")

    v8_policy = _mapping(policy["grounded_v8_semantic_correction"], "grounded_v8_semantic_correction")
    v8 = _load_bound_json(repository_root, v8_policy, "grounded_v8_semantic_correction")
    v8_outputs = _mapping(v8.get("outputs"), "grounded_v8.outputs")
    v8_authorization = _mapping(v8_outputs.get("authorization"), "grounded_v8.authorization")
    v8_counts = _mapping(v8_authorization.get("counts"), "grounded_v8.authorization.counts")
    if (
        v8.get("binding_protocol") != v8_policy.get("binding_protocol")
        or not _mapping(v8.get("reproducibility"), "grounded_v8.reproducibility")
        or not all(_mapping(v8.get("reproducibility"), "grounded_v8.reproducibility").values())
        or v8_counts.get("chatgpt_semantic_correction_count") != v8_policy.get("chatgpt_semantic_correction_count")
        or v8_counts.get("human_semantic_approval_count") != v8_policy.get("human_semantic_approval_count")
        or v8_counts.get("answer_certificate_status_counts") != v8_policy.get("answer_certificate_status_counts")
    ):
        raise ValueError("V8 semantic-correction replay threshold mismatch")
    v8_readiness_path = _resolve(
        repository_root, v8_authorization.get("readiness_path"), "V8 authorization readiness"
    )
    if not v8_readiness_path.is_file() or sha256_file(v8_readiness_path) != v8_authorization.get("readiness_sha256"):
        raise ValueError("immutable artifact mismatch: V8 authorization readiness")
    v8_readiness = json.loads(v8_readiness_path.read_text(encoding="utf-8"))
    if (
        v8_readiness.get("release_authorized") is not v8_policy.get("release_authorized")
        or v8_readiness.get("authorization_status") != v8_policy.get("authorization_status")
        or v8_readiness.get("chatgpt_semantic_correction_count") != 1
    ):
        raise ValueError("V8 release gate status mismatch")

    def question_index(run: Mapping[str, Any], output_name: str) -> dict[int, dict[str, Any]]:
        record = _mapping(_mapping(run.get("outputs"), "run.outputs").get(output_name), output_name)
        raw_path = record.get("path") or record.get("evidence_bindings_path") or record.get("answer_certificates_path")
        expected = record.get("sha256") or record.get("evidence_bindings_sha256") or record.get("answer_certificates_sha256")
        artifact = _resolve(repository_root, raw_path, output_name)
        if not artifact.is_file() or sha256_file(artifact) != expected:
            raise ValueError(f"immutable artifact mismatch: {output_name}")
        return {int(row["question_id"]): row for row in _load_jsonl(artifact, output_name)}

    v7_bindings = question_index(v7, "bindings")
    v8_bindings = question_index(v8, "bindings")
    binding_drift: list[int] = []
    for question_id in sorted(v7_bindings):
        old = dict(v7_bindings[question_id])
        new = dict(v8_bindings[question_id])
        new["schema_version"] = old.get("schema_version")
        new["protocol"] = old.get("protocol")
        if old != new:
            binding_drift.append(question_id)
    if binding_drift != [702]:
        raise ValueError("V8 binding diff is not isolated to Q702")
    for output_name in ("execution", "authorization"):
        if output_name == "authorization":
            for field in ("evidence_bindings", "answer_certificates"):
                def auth_index(run: Mapping[str, Any], field_name: str) -> dict[int, dict[str, Any]]:
                    record = _mapping(_mapping(run.get("outputs"), "run.outputs").get("authorization"), "authorization")
                    artifact = _resolve(repository_root, record.get(f"{field_name}_path"), field_name)
                    expected = record.get(f"{field_name}_sha256")
                    if not artifact.is_file() or sha256_file(artifact) != expected:
                        raise ValueError(f"immutable artifact mismatch: {field_name}")
                    return {int(row["question_id"]): row for row in _load_jsonl(artifact, field_name)}
                old_rows, new_rows = auth_index(v7, field), auth_index(v8, field)
                if [qid for qid in sorted(old_rows) if old_rows[qid] != new_rows[qid]] != [702]:
                    raise ValueError(f"V8 {field} diff is not isolated to Q702")
        else:
            old_rows, new_rows = question_index(v7, output_name), question_index(v8, output_name)
            if [qid for qid in sorted(old_rows) if old_rows[qid] != new_rows[qid]] != [702]:
                raise ValueError("V8 execution diff is not isolated to Q702")

    v8_campaign_policy = _mapping(policy["grounded_campaign_review_v8"], "grounded_campaign_review_v8")
    v8_campaign = _load_bound_json(repository_root, v8_campaign_policy, "grounded_campaign_review_v8")
    for name in ("campaign_status", "candidate_count", "human_campaign_decision_count"):
        if v8_campaign.get(name) != v8_campaign_policy.get(name):
            raise ValueError(f"V8 campaign handoff threshold mismatch: {name}")
    v8_campaign_outputs = _mapping(v8_campaign.get("outputs"), "grounded_campaign_review_v8.outputs")
    role_issue_record = _mapping(v8_campaign_outputs.get("entity_role_issue_briefs"), "V8 role issues")
    role_issue_path = _resolve(repository_root, role_issue_record.get("path"), "V8 role issues")
    if len(_load_jsonl(role_issue_path, "V8 role issues")) != v8_campaign_policy.get("entity_role_issue_count"):
        raise ValueError("V8 campaign entity-role issue count mismatch")

    v8_audit_policy = _mapping(policy["grounded_campaign_chatgpt_audit_v8"], "grounded_campaign_chatgpt_audit_v8")
    v8_audit = _load_bound_json(repository_root, v8_audit_policy, "grounded_campaign_chatgpt_audit_v8")
    for name in (
        "status", "candidate_count", "candidate_review_outcome_counts",
        "blocking_issue_count", "numeric_value_exposure_count",
    ):
        if v8_audit.get(name) != v8_audit_policy.get(name):
            raise ValueError(f"V8 campaign ChatGPT audit threshold mismatch: {name}")
    v8_audit_decision_record = _mapping(_mapping(v8_audit.get("outputs"), "v8_audit.outputs").get("decision"), "v8_audit.decision")
    v8_audit_decision_path = _resolve(repository_root, v8_audit_decision_record.get("path"), "v8 audit decision")
    if not v8_audit_decision_path.is_file() or sha256_file(v8_audit_decision_path) != v8_audit_decision_record.get("sha256"):
        raise ValueError("immutable artifact mismatch: V8 campaign ChatGPT decision")
    v8_audit_decision = _load_jsonl(v8_audit_decision_path, "V8 audit decision")[0]
    v8_reviews = v8_audit_decision.get("candidate_reviews") or []
    v8_q702 = [review for review in v8_reviews if review.get("question_id") == 702]
    if (
        v8_audit_decision.get("campaign_id") != v8_campaign.get("campaign_id")
        or v8_audit_decision.get("decision") != "approve_campaign"
        or v8_audit_decision.get("blocking_issue_count") != 0
        or v8_audit_decision.get("release_authorized") is not False
        or v8_audit_decision.get("promotion_allowed") is not False
        or len(v8_reviews) != 12
        or len(v8_q702) != 1
        or v8_q702[0].get("review_outcome") != "approved_candidate"
        or v8_q702[0].get("reason_code") != "NET_OTHER_INCOME_EXACT_REPORTED_ROW_PROVEN"
        or _contains_forbidden_key(v8_reviews, {"answer_decimal", "raw_value"})
    ):
        raise ValueError("V8 campaign ChatGPT audit decision boundary is invalid")

    promotion_policy = _mapping(
        policy["navigation_binding_chatgpt_promotion_q167_v1"],
        "navigation_binding_chatgpt_promotion_q167_v1",
    )
    promotion = _load_bound_json(
        repository_root, promotion_policy, "navigation_binding_chatgpt_promotion_q167_v1"
    )
    promotion_counts = _mapping(promotion.get("counts"), "navigation promotion counts")
    if (
        promotion.get("status") != promotion_policy.get("status")
        or promotion_counts.get("promotion_count") != promotion_policy.get("promotion_count")
        or promotion_counts.get("numeric_value_exposure_count")
        != promotion_policy.get("numeric_value_exposure_count")
    ):
        raise ValueError("Q167 navigation promotion threshold mismatch")
    promotion_outputs = _mapping(promotion.get("outputs"), "navigation promotion outputs")
    promotion_packets_record = _mapping(promotion_outputs.get("packets"), "promotion packets")
    promotion_decisions_record = _mapping(promotion_outputs.get("decisions"), "promotion decisions")
    promotion_packets_path = _resolve(
        repository_root, promotion_packets_record.get("path"), "promotion packets"
    )
    promotion_decisions_path = _resolve(
        repository_root, promotion_decisions_record.get("path"), "promotion decisions"
    )
    if (
        not promotion_packets_path.is_file()
        or sha256_file(promotion_packets_path) != promotion_packets_record.get("sha256")
        or not promotion_decisions_path.is_file()
        or sha256_file(promotion_decisions_path) != promotion_decisions_record.get("sha256")
    ):
        raise ValueError("immutable artifact mismatch: Q167 navigation promotion")
    promotion_packets = _load_jsonl(promotion_packets_path, "promotion packets")
    promotion_decisions = _load_jsonl(promotion_decisions_path, "promotion decisions")
    if len(promotion_packets) != 1 or len(promotion_decisions) != 1:
        raise ValueError("Q167 navigation promotion coverage mismatch")
    promotion_packet, promotion_decision = promotion_packets[0], promotion_decisions[0]
    promotion_provenance = _mapping(
        promotion_decision.get("decision_provenance"), "promotion provenance"
    )
    promotion_authority = _mapping(
        promotion_provenance.get("authority_grant"), "promotion authority grant"
    )
    reviewer_authority = _mapping(
        promotion_decision.get("reviewer_authority"), "promotion reviewer authority"
    )
    if (
        promotion_packet.get("question_id") != 167
        or promotion_decision.get("decision") != "approve_navigation_binding_promotion"
        or promotion_decision.get("approved_variable_id") != "interest_expense"
        or promotion_decision.get("approved_entity_role") != "parent"
        or promotion_provenance.get("reviewer_type") != "chatgpt_verified"
        or promotion_authority.get("grant_scope")
        != "navigation_binding_review_gate_equivalence"
        or promotion_authority.get("grant_basis") != "explicit_user_instruction"
        or reviewer_authority.get("may_approve_exact_row") is not True
        or reviewer_authority.get("may_approve_period_header") is not True
        or reviewer_authority.get("may_approve_table_role") is not True
        or reviewer_authority.get("may_approve_entity_role") is not True
        or reviewer_authority.get("may_infer_sector") is not False
        or reviewer_authority.get("may_select_value") is not False
        or reviewer_authority.get("may_execute_formula") is not False
        or reviewer_authority.get("release_authorized") is not False
        or _mapping(promotion_packet.get("source_contract"), "promotion contract").get(
            "numeric_value_exposed_to_reviewer"
        )
        is not False
        or _contains_forbidden_key(
            promotion_packets, {"answer_decimal", "raw_decimal_candidate", "raw_source_cell", "raw_value"}
        )
    ):
        raise ValueError("Q167 navigation promotion authority boundary is invalid")

    v9_policy = _mapping(policy["grounded_v9_navigation_promotion"], "grounded_v9_navigation_promotion")
    v9 = _load_bound_json(repository_root, v9_policy, "grounded_v9_navigation_promotion")
    v9_authorization = _mapping(
        _mapping(v9.get("outputs"), "grounded_v9.outputs").get("authorization"),
        "grounded_v9.authorization",
    )
    v9_counts = _mapping(v9_authorization.get("counts"), "grounded_v9.authorization.counts")
    if (
        v9.get("binding_protocol") != v9_policy.get("binding_protocol")
        or not all(_mapping(v9.get("reproducibility"), "grounded_v9.reproducibility").values())
        or v9_counts.get("chatgpt_semantic_correction_count")
        != v9_policy.get("chatgpt_semantic_correction_count")
        or v9_counts.get("chatgpt_navigation_promotion_count")
        != v9_policy.get("chatgpt_navigation_promotion_count")
        or v9_counts.get("human_semantic_approval_count")
        != v9_policy.get("human_semantic_approval_count")
        or v9_counts.get("answer_certificate_status_counts")
        != v9_policy.get("answer_certificate_status_counts")
    ):
        raise ValueError("V9 navigation-promotion replay threshold mismatch")
    v9_readiness_path = _resolve(
        repository_root, v9_authorization.get("readiness_path"), "V9 authorization readiness"
    )
    if (
        not v9_readiness_path.is_file()
        or sha256_file(v9_readiness_path) != v9_authorization.get("readiness_sha256")
    ):
        raise ValueError("immutable artifact mismatch: V9 authorization readiness")
    v9_readiness = json.loads(v9_readiness_path.read_text(encoding="utf-8"))
    if (
        v9_readiness.get("release_authorized") is not v9_policy.get("release_authorized")
        or v9_readiness.get("authorization_status") != v9_policy.get("authorization_status")
        or v9_readiness.get("chatgpt_navigation_promotion_count") != 1
    ):
        raise ValueError("V9 release gate status mismatch")

    v9_bindings = question_index(v9, "bindings")
    v9_binding_drift: list[int] = []
    for question_id in sorted(v8_bindings):
        old = dict(v8_bindings[question_id])
        new = dict(v9_bindings[question_id])
        new["schema_version"] = old.get("schema_version")
        new["protocol"] = old.get("protocol")
        if old != new:
            v9_binding_drift.append(question_id)
    if v9_binding_drift != [167]:
        raise ValueError("V9 binding diff is not isolated to Q167")
    old_execution, new_execution = question_index(v8, "execution"), question_index(v9, "execution")
    if [qid for qid in sorted(old_execution) if old_execution[qid] != new_execution[qid]] != [167]:
        raise ValueError("V9 execution diff is not isolated to Q167")
    for field in ("evidence_bindings", "answer_certificates"):
        def v9_auth_index(run: Mapping[str, Any], field_name: str) -> dict[int, dict[str, Any]]:
            record = _mapping(
                _mapping(run.get("outputs"), "run.outputs").get("authorization"),
                "authorization",
            )
            artifact = _resolve(repository_root, record.get(f"{field_name}_path"), field_name)
            expected = record.get(f"{field_name}_sha256")
            if not artifact.is_file() or sha256_file(artifact) != expected:
                raise ValueError(f"immutable artifact mismatch: {field_name}")
            return {int(row["question_id"]): row for row in _load_jsonl(artifact, field_name)}
        old_rows, new_rows = v9_auth_index(v8, field), v9_auth_index(v9, field)
        if [qid for qid in sorted(old_rows) if old_rows[qid] != new_rows[qid]] != [167]:
            raise ValueError(f"V9 {field} diff is not isolated to Q167")

    v9_campaign_policy = _mapping(policy["grounded_campaign_review_v9"], "grounded_campaign_review_v9")
    v9_campaign = _load_bound_json(repository_root, v9_campaign_policy, "grounded_campaign_review_v9")
    for name in ("campaign_status", "candidate_count", "human_campaign_decision_count"):
        if v9_campaign.get(name) != v9_campaign_policy.get(name):
            raise ValueError(f"V9 campaign handoff threshold mismatch: {name}")
    v9_role_record = _mapping(
        _mapping(v9_campaign.get("outputs"), "grounded_campaign_review_v9.outputs").get(
            "entity_role_issue_briefs"
        ),
        "V9 role issues",
    )
    v9_role_path = _resolve(repository_root, v9_role_record.get("path"), "V9 role issues")
    if len(_load_jsonl(v9_role_path, "V9 role issues")) != v9_campaign_policy.get("entity_role_issue_count"):
        raise ValueError("V9 campaign entity-role issue count mismatch")

    v9_audit_policy = _mapping(
        policy["grounded_campaign_chatgpt_audit_v9"], "grounded_campaign_chatgpt_audit_v9"
    )
    v9_audit = _load_bound_json(repository_root, v9_audit_policy, "grounded_campaign_chatgpt_audit_v9")
    for name in (
        "status", "candidate_count", "candidate_review_outcome_counts",
        "blocking_issue_count", "numeric_value_exposure_count",
    ):
        if v9_audit.get(name) != v9_audit_policy.get(name):
            raise ValueError(f"V9 campaign ChatGPT audit threshold mismatch: {name}")
    v9_decision_record = _mapping(
        _mapping(v9_audit.get("outputs"), "v9_audit.outputs").get("decision"),
        "v9_audit.decision",
    )
    v9_decision_path = _resolve(repository_root, v9_decision_record.get("path"), "V9 audit decision")
    if not v9_decision_path.is_file() or sha256_file(v9_decision_path) != v9_decision_record.get("sha256"):
        raise ValueError("immutable artifact mismatch: V9 campaign ChatGPT decision")
    v9_decision = _load_jsonl(v9_decision_path, "V9 audit decision")[0]
    v9_reviews = v9_decision.get("candidate_reviews") or []
    v9_q167 = [review for review in v9_reviews if review.get("question_id") == 167]
    if (
        v9_decision.get("campaign_id") != v9_campaign.get("campaign_id")
        or v9_decision.get("decision") != "approve_campaign"
        or v9_decision.get("blocking_issue_count") != 0
        or v9_decision.get("release_authorized") is not False
        or v9_decision.get("promotion_allowed") is not False
        or len(v9_reviews) != 13
        or len(v9_q167) != 1
        or v9_q167[0].get("review_outcome") != "approved_candidate"
        or v9_q167[0].get("reason_code") != "EXACT_NAVIGATION_BINDING_PROVEN"
        or _contains_forbidden_key(v9_reviews, {"answer_decimal", "raw_value"})
    ):
        raise ValueError("V9 campaign ChatGPT audit decision boundary is invalid")

    cross_promotion_policy = _mapping(
        policy["cross_entity_binding_chatgpt_promotion_q750_v1"],
        "cross_entity_binding_chatgpt_promotion_q750_v1",
    )
    cross_promotion = _load_bound_json(
        repository_root,
        cross_promotion_policy,
        "cross_entity_binding_chatgpt_promotion_q750_v1",
    )
    cross_promotion_counts = _mapping(
        cross_promotion.get("counts"), "cross_entity_binding_promotion.counts"
    )
    for name in ("promotion_count", "operand_count", "numeric_value_exposure_count"):
        if cross_promotion_counts.get(name) != cross_promotion_policy.get(name):
            raise ValueError(f"cross-entity binding promotion threshold mismatch: {name}")
    cross_outputs = _mapping(cross_promotion.get("outputs"), "cross promotion outputs")
    cross_artifacts: dict[str, list[dict[str, Any]]] = {}
    for name in ("packets", "decisions"):
        record = _mapping(cross_outputs.get(name), f"cross promotion {name}")
        artifact = _resolve(repository_root, record.get("path"), f"cross promotion {name}")
        if not artifact.is_file() or sha256_file(artifact) != record.get("sha256"):
            raise ValueError(f"immutable artifact mismatch: cross promotion {name}")
        cross_artifacts[name] = _load_jsonl(artifact, f"cross promotion {name}")
    if len(cross_artifacts["packets"]) != 1 or len(cross_artifacts["decisions"]) != 1:
        raise ValueError("cross-entity binding promotion must contain one Q750 decision")
    cross_packet, cross_decision = cross_artifacts["packets"][0], cross_artifacts["decisions"][0]
    cross_provenance = _mapping(cross_decision.get("decision_provenance"), "cross promotion provenance")
    cross_grant = _mapping(cross_provenance.get("authority_grant"), "cross promotion authority grant")
    cross_reviewer_authority = _mapping(
        cross_decision.get("reviewer_authority"), "cross promotion reviewer authority"
    )
    if (
        cross_packet.get("question_id") != 750
        or len(cross_packet.get("reviewed_operands") or []) != 2
        or _mapping(_mapping(cross_packet.get("operation_graph"), "cross graph").get("operation_ast"), "cross AST").get("op") != "subtract"
        or cross_decision.get("decision") != "approve_cross_entity_binding_promotion"
        or cross_provenance.get("reviewer_type") != "chatgpt_verified"
        or cross_grant.get("grant_scope") != "cross_entity_binding_review_gate_equivalence"
        or cross_grant.get("grant_basis") != "explicit_user_instruction"
        or cross_reviewer_authority.get("may_approve_exact_operands") is not True
        or cross_reviewer_authority.get("may_approve_composition_graph") is not True
        or cross_reviewer_authority.get("may_select_value") is not False
        or cross_reviewer_authority.get("may_execute_formula") is not False
        or cross_reviewer_authority.get("release_authorized") is not False
        or _contains_forbidden_key(
            [cross_packet, cross_decision],
            {"answer_decimal", "execution_value_decimal", "raw_decimal_candidate", "raw_source_cell", "raw_value"},
        )
    ):
        raise ValueError("Q750 cross-entity promotion authority boundary is invalid")

    v10_policy = _mapping(policy["grounded_v10_cross_entity"], "grounded_v10_cross_entity")
    v10 = _load_bound_json(repository_root, v10_policy, "grounded_v10_cross_entity")
    v10_authorization = _mapping(
        _mapping(v10.get("outputs"), "grounded_v10.outputs").get("authorization"),
        "grounded_v10.authorization",
    )
    v10_counts = _mapping(v10_authorization.get("counts"), "grounded_v10.authorization.counts")
    for name in (
        "chatgpt_cross_entity_promotion_count", "human_semantic_approval_count",
        "effective_semantic_approval_count", "answer_certificate_status_counts",
        "evidence_binding_status_counts",
    ):
        if v10_counts.get(name) != v10_policy.get(name):
            raise ValueError(f"V10 cross-entity threshold mismatch: {name}")
    if (
        v10.get("binding_protocol") != v10_policy.get("binding_protocol")
        or len(_mapping(v10.get("reproducibility"), "grounded_v10.reproducibility")) != 5
        or not all(_mapping(v10.get("reproducibility"), "grounded_v10.reproducibility").values())
    ):
        raise ValueError("V10 cross-entity reproducibility or protocol mismatch")
    v10_readiness_path = _resolve(
        repository_root, v10_authorization.get("readiness_path"), "V10 authorization readiness"
    )
    if (
        not v10_readiness_path.is_file()
        or sha256_file(v10_readiness_path) != v10_authorization.get("readiness_sha256")
    ):
        raise ValueError("immutable artifact mismatch: V10 authorization readiness")
    v10_readiness = json.loads(v10_readiness_path.read_text(encoding="utf-8"))
    if (
        v10_readiness.get("release_authorized") is not v10_policy.get("release_authorized")
        or v10_readiness.get("authorization_status") != v10_policy.get("authorization_status")
        or v10_readiness.get("chatgpt_cross_entity_promotion_count") != 1
    ):
        raise ValueError("V10 release gate status mismatch")

    v10_bindings = question_index(v10, "bindings")
    v10_binding_drift: list[int] = []
    for question_id in sorted(v9_bindings):
        old = dict(v9_bindings[question_id])
        new = dict(v10_bindings[question_id])
        new["schema_version"] = old.get("schema_version")
        new["protocol"] = old.get("protocol")
        if old != new:
            v10_binding_drift.append(question_id)
    if v10_binding_drift != [750]:
        raise ValueError("V10 binding diff is not isolated to Q750")
    v10_execution = question_index(v10, "execution")
    if [qid for qid in sorted(new_execution) if new_execution[qid] != v10_execution[qid]] != [750]:
        raise ValueError("V10 execution diff is not isolated to Q750")
    v10_authorization_rows: dict[str, dict[int, dict[str, Any]]] = {}
    for field in ("evidence_bindings", "answer_certificates"):
        old_rows = v9_auth_index(v9, field)
        new_rows = v9_auth_index(v10, field)
        if [qid for qid in sorted(old_rows) if old_rows[qid] != new_rows[qid]] != [750]:
            raise ValueError(f"V10 {field} diff is not isolated to Q750")
        v10_authorization_rows[field] = new_rows
    q750_certificate = _mapping(
        v10_authorization_rows["answer_certificates"][750].get("answer_certificate"),
        "Q750 certificate",
    )
    q746_certificate = _mapping(
        v10_authorization_rows["answer_certificates"][746].get("answer_certificate"),
        "Q746 certificate",
    )
    if (
        q750_certificate.get("status") != "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"
        or len(q750_certificate.get("binding_receipts") or []) != 2
        or q746_certificate.get("status") != "ABSTAIN"
        or "MULTI_STAGE_EXECUTION_GRAPH_NOT_MATERIALIZED"
        not in (q746_certificate.get("abstain_reason_codes") or [])
    ):
        raise ValueError("V10 Q750/Q746 certificate boundary is invalid")

    v10_campaign_policy = _mapping(policy["grounded_campaign_review_v10"], "grounded_campaign_review_v10")
    v10_campaign = _load_bound_json(repository_root, v10_campaign_policy, "grounded_campaign_review_v10")
    for name in ("campaign_status", "candidate_count", "human_campaign_decision_count"):
        if v10_campaign.get(name) != v10_campaign_policy.get(name):
            raise ValueError(f"V10 campaign handoff threshold mismatch: {name}")
    v10_role_record = _mapping(
        _mapping(v10_campaign.get("outputs"), "V10 campaign outputs").get("entity_role_issue_briefs"),
        "V10 role issues",
    )
    v10_role_path = _resolve(repository_root, v10_role_record.get("path"), "V10 role issues")
    if len(_load_jsonl(v10_role_path, "V10 role issues")) != v10_campaign_policy.get("entity_role_issue_count"):
        raise ValueError("V10 campaign entity-role issue count mismatch")

    v10_audit_policy = _mapping(
        policy["grounded_campaign_chatgpt_audit_v10"], "grounded_campaign_chatgpt_audit_v10"
    )
    v10_audit = _load_bound_json(repository_root, v10_audit_policy, "grounded_campaign_chatgpt_audit_v10")
    for name in (
        "status", "candidate_count", "candidate_review_outcome_counts",
        "blocking_issue_count", "numeric_value_exposure_count",
    ):
        if v10_audit.get(name) != v10_audit_policy.get(name):
            raise ValueError(f"V10 campaign ChatGPT audit threshold mismatch: {name}")
    v10_authority = _mapping(v10_audit.get("review_authority"), "V10 review authority")
    v10_decision_record = _mapping(
        _mapping(v10_audit.get("outputs"), "V10 audit outputs").get("decision"),
        "V10 audit decision",
    )
    v10_decision_path = _resolve(repository_root, v10_decision_record.get("path"), "V10 audit decision")
    if not v10_decision_path.is_file() or sha256_file(v10_decision_path) != v10_decision_record.get("sha256"):
        raise ValueError("immutable artifact mismatch: V10 campaign ChatGPT decision")
    v10_decision = _load_jsonl(v10_decision_path, "V10 audit decision")[0]
    v10_reviews = v10_decision.get("candidate_reviews") or []
    v10_q750 = [review for review in v10_reviews if review.get("question_id") == 750]
    if (
        v10_authority.get("verification_authority") != v10_audit_policy.get("verification_authority")
        or v10_authority.get("provenance_preserved_as") != v10_audit_policy.get("provenance_preserved_as")
        or v10_authority.get("release_authority_included") is not v10_audit_policy.get("release_authority_included")
        or v10_decision.get("campaign_id") != v10_campaign.get("campaign_id")
        or v10_decision.get("decision") != "approve_campaign"
        or _mapping(v10_decision.get("decision_provenance"), "V10 decision provenance").get("reviewer_type") != "chatgpt_verified"
        or v10_decision.get("release_authorized") is not False
        or v10_decision.get("promotion_allowed") is not False
        or len(v10_reviews) != 14
        or len(v10_q750) != 1
        or v10_q750[0].get("review_outcome") != "approved_candidate"
        or v10_q750[0].get("reason_code") != "EXACT_CROSS_ENTITY_COMPOSITION_PROVEN"
        or len(v10_q750[0].get("source_reopens") or []) != 2
        or _mapping(v10_q750[0].get("execution_replay"), "Q750 audit execution").get("operation") != "subtract"
        or _contains_forbidden_key(v10_reviews, {"answer_decimal", "raw_value"})
    ):
        raise ValueError("V10 human-equivalent ChatGPT audit boundary is invalid")

    operand_packet_policy = _mapping(
        policy["cross_entity_operand_packets_v1"], "cross_entity_operand_packets_v1"
    )
    operand_packet_manifest = _load_bound_json(
        repository_root, operand_packet_policy, "cross_entity_operand_packets_v1"
    )
    operand_packet_counts = _mapping(
        operand_packet_manifest.get("counts"), "cross_entity_operand_packets_v1.counts"
    )
    for name in (
        "question_count", "operand_count", "numeric_value_exposure_count",
        "packet_status_counts",
    ):
        if operand_packet_counts.get(name) != operand_packet_policy.get(name):
            raise ValueError(f"cross-entity operand packet threshold mismatch: {name}")
    operand_packet_contract = _mapping(
        operand_packet_manifest.get("source_contract"), "cross_entity_operand_packets_v1.source_contract"
    )
    packet_record = _mapping(
        _mapping(operand_packet_manifest.get("outputs"), "operand packet outputs").get("packets"),
        "operand packets",
    )
    operand_packet_path = _resolve(repository_root, packet_record.get("path"), "operand packets")
    if not operand_packet_path.is_file() or sha256_file(operand_packet_path) != packet_record.get("sha256"):
        raise ValueError("immutable artifact mismatch: cross-entity operand packets")
    operand_packets = _load_jsonl(operand_packet_path, "cross-entity operand packets")
    if (
        operand_packet_contract.get("numeric_values_exposed_to_reviewer") is not False
        or operand_packet_contract.get("may_select_value") is not False
        or operand_packet_contract.get("may_execute_formula") is not False
        or operand_packet_contract.get("promotion_allowed") is not False
        or operand_packet_contract.get("release_authorized") is not False
        or _contains_forbidden_key(
            operand_packets,
            {"answer_decimal", "execution_value_decimal", "raw_decimal_candidate", "raw_source_cell", "raw_value"},
        )
    ):
        raise ValueError("cross-entity operand packet authority boundary is invalid")

    operand_decision_policy = _mapping(
        policy["cross_entity_operand_chatgpt_review_v1"],
        "cross_entity_operand_chatgpt_review_v1",
    )
    operand_decision_manifest = _load_bound_json(
        repository_root,
        operand_decision_policy,
        "cross_entity_operand_chatgpt_review_v1",
    )
    operand_decision_counts = _mapping(
        operand_decision_manifest.get("counts"),
        "cross_entity_operand_chatgpt_review_v1.counts",
    )
    for name in ("decision_count", "approve_exact_operand_set", "confirm_source_blocker"):
        if operand_decision_counts.get(name) != operand_decision_policy.get(name):
            raise ValueError(f"cross-entity operand decision threshold mismatch: {name}")
    operand_decision_contract = _mapping(
        operand_decision_manifest.get("source_contract"),
        "cross_entity_operand_chatgpt_review_v1.source_contract",
    )
    decision_record = _mapping(
        _mapping(operand_decision_manifest.get("outputs"), "operand decision outputs").get("decisions"),
        "operand decisions",
    )
    operand_decision_path = _resolve(repository_root, decision_record.get("path"), "operand decisions")
    if not operand_decision_path.is_file() or sha256_file(operand_decision_path) != decision_record.get("sha256"):
        raise ValueError("immutable artifact mismatch: cross-entity operand decisions")
    operand_decisions = _load_jsonl(operand_decision_path, "cross-entity operand decisions")
    decisions_by_id = {int(row["question_id"]): row for row in operand_decisions}
    approved_provenance = _mapping(
        _mapping(decisions_by_id.get(750), "Q750 operand decision").get("decision_provenance"),
        "Q750 operand decision provenance",
    )
    approved_grant = _mapping(
        approved_provenance.get("authority_grant"), "Q750 operand authority grant"
    )
    if (
        set(decisions_by_id) != {746, 750}
        or decisions_by_id[746].get("decision") != "confirm_source_blocker"
        or decisions_by_id[746].get("reason_codes") != ["KHG_PARENT_ROLE_UNPROVEN"]
        or decisions_by_id[750].get("decision") != "approve_exact_operand_set"
        or approved_provenance.get("reviewer_type") != "chatgpt_verified"
        or approved_grant.get("grant_scope") != "exact_source_operand_review_gate_equivalence"
        or approved_grant.get("grant_basis") != "explicit_user_instruction"
        or operand_decision_contract.get("chatgpt_authority_grant_present") is not True
        or operand_decision_contract.get("numeric_values_exposed_to_reviewer") is not False
        or operand_decision_contract.get("may_select_value") is not False
        or operand_decision_contract.get("may_execute_formula") is not False
        or operand_decision_contract.get("promotion_allowed") is not False
        or operand_decision_contract.get("release_authorized") is not False
        or _contains_forbidden_key(
            operand_decisions,
            {"answer_decimal", "execution_value_decimal", "raw_decimal_candidate", "raw_source_cell", "raw_value"},
        )
    ):
        raise ValueError("cross-entity ChatGPT operand authority boundary is invalid")

    materialization_policy = _mapping(
        policy["cross_entity_subtract_materialization_v1"],
        "cross_entity_subtract_materialization_v1",
    )
    materialization = _load_bound_json(
        repository_root, materialization_policy, "cross_entity_subtract_materialization_v1"
    )
    materialization_counts = _mapping(
        materialization.get("counts"), "cross_entity_subtract_materialization_v1.counts"
    )
    for name in (
        "question_count", "token_count", "private_execution_count", "execution_status_counts",
    ):
        if materialization_counts.get(name) != materialization_policy.get(name):
            raise ValueError(f"cross-entity materialization threshold mismatch: {name}")
    materialization_contract = _mapping(
        materialization.get("source_contract"),
        "cross_entity_subtract_materialization_v1.source_contract",
    )
    materialization_paths: dict[str, Path] = {}
    for name, value in _mapping(materialization.get("outputs"), "materialization outputs").items():
        record = _mapping(value, f"materialization output {name}")
        artifact = _resolve(repository_root, record.get("path"), f"materialization output {name}")
        if not artifact.is_file() or sha256_file(artifact) != record.get("sha256"):
            raise ValueError(f"immutable artifact mismatch: cross-entity materialization {name}")
        materialization_paths[name] = artifact
    public_receipts = _load_jsonl(materialization_paths["public_receipts"], "public execution receipts")
    public_tokens = _load_jsonl(materialization_paths["public_tokens"], "public operand tokens")
    receipt_by_id = {int(row["question_id"]): row for row in public_receipts}
    if (
        receipt_by_id.get(746, {}).get("execution_status") != "dependency_blocked"
        or receipt_by_id.get(750, {}).get("execution_status")
        != "execution_replay_ready_research_only"
        or materialization_contract.get("research_only") is not True
        or materialization_contract.get("reviewer_numeric_value_exposure") is not False
        or materialization_contract.get("deterministic_executor_reads_values") is not True
        or materialization_contract.get("answer_materialization_allowed") is not False
        or materialization_contract.get("promotion_allowed") is not False
        or materialization_contract.get("release_authorized") is not False
        or _contains_forbidden_key(
            [*public_receipts, *public_tokens],
            {"answer_decimal", "execution_value_decimal", "raw_decimal_candidate", "raw_source_cell", "raw_value"},
        )
    ):
        raise ValueError("cross-entity materialization public boundary is invalid")

    q746_packet_policy = _mapping(
        policy["cross_entity_operand_packets_q746_v2"],
        "cross_entity_operand_packets_q746_v2",
    )
    q746_packet_manifest = _load_bound_json(
        repository_root, q746_packet_policy, "cross_entity_operand_packets_q746_v2"
    )
    q746_packet_counts = _mapping(q746_packet_manifest.get("counts"), "Q746 packet counts")
    for name in ("question_count", "operand_count", "numeric_value_exposure_count", "packet_status_counts"):
        if q746_packet_counts.get(name) != q746_packet_policy.get(name):
            raise ValueError(f"Q746 operand packet threshold mismatch: {name}")
    q746_packet_record = _mapping(
        _mapping(q746_packet_manifest.get("outputs"), "Q746 packet outputs").get("packets"),
        "Q746 packets",
    )
    q746_packet_path = _resolve(repository_root, q746_packet_record.get("path"), "Q746 packets")
    if not q746_packet_path.is_file() or sha256_file(q746_packet_path) != q746_packet_record.get("sha256"):
        raise ValueError("immutable artifact mismatch: Q746 operand packets")
    q746_packets = _load_jsonl(q746_packet_path, "Q746 operand packets")
    if (
        len(q746_packets) != 1
        or q746_packets[0].get("question_id") != 746
        or q746_packets[0].get("packet_status") != "reviewable_exact_operand_set"
        or len(q746_packets[0].get("operand_evidence") or []) != 2
        or _contains_forbidden_key(q746_packets, {"answer_decimal", "raw_value", "raw_source_cell"})
    ):
        raise ValueError("Q746 operand packet boundary is invalid")

    dual_policy = _mapping(
        policy["cross_entity_operand_dual_chatgpt_adjudication_q746_v2"],
        "cross_entity_operand_dual_chatgpt_adjudication_q746_v2",
    )
    dual_manifest = _load_bound_json(repository_root, dual_policy, "Q746 dual ChatGPT adjudication")
    dual_counts = _mapping(dual_manifest.get("counts"), "Q746 dual review counts")
    if (
        dual_manifest.get("status") != dual_policy.get("status")
        or dual_counts.get("decision_count") != dual_policy.get("decision_count")
        or dual_counts.get("approve_exact_operand_set") != dual_policy.get("approve_exact_operand_set")
    ):
        raise ValueError("Q746 dual-review adjudication threshold mismatch")
    dual_artifacts: dict[str, list[dict[str, Any]]] = {}
    for name, record_raw in {
        **dict(_mapping(dual_manifest.get("inputs"), "Q746 dual inputs")),
        **dict(_mapping(dual_manifest.get("outputs"), "Q746 dual outputs")),
    }.items():
        record = _mapping(record_raw, f"Q746 dual {name}")
        artifact = _resolve(repository_root, record.get("path"), f"Q746 dual {name}")
        if not artifact.is_file() or sha256_file(artifact) != record.get("sha256"):
            raise ValueError(f"immutable artifact mismatch: Q746 dual {name}")
        dual_artifacts[name] = _load_jsonl(artifact, f"Q746 dual {name}")
    proposal = dual_artifacts["proposal_decisions"][0]
    critic = dual_artifacts["critic_decisions"][0]
    reconciled = dual_artifacts["decisions"][0]
    proposal_provenance = _mapping(proposal.get("decision_provenance"), "Q746 proposal provenance")
    critic_provenance = _mapping(critic.get("decision_provenance"), "Q746 critic provenance")
    reconciled_receipt = _mapping(reconciled.get("authority_receipt"), "Q746 reconciled authority")
    if (
        len(dual_artifacts["proposal_decisions"]) != 1
        or len(dual_artifacts["critic_decisions"]) != 1
        or len(dual_artifacts["decisions"]) != 1
        or {proposal.get("question_id"), critic.get("question_id"), reconciled.get("question_id")} != {746}
        or proposal.get("decision") != "approve_exact_operand_set"
        or critic.get("decision") != "approve_exact_operand_set"
        or reconciled.get("decision") != "approve_exact_operand_set"
        or proposal.get("semantic_checks") != critic.get("semantic_checks")
        or not all(_mapping(proposal.get("semantic_checks"), "Q746 semantic checks").values())
        or proposal_provenance.get("reviewer_role") != "authorized_ai_operand_evidence_proposer"
        or critic_provenance.get("reviewer_role") != "authorized_ai_operand_evidence_critic"
        or proposal_provenance.get("reviewer_id") == critic_provenance.get("reviewer_id")
        or proposal_provenance.get("verification_authority") != "human_equivalent"
        or critic_provenance.get("verification_authority") != "human_equivalent"
        or reconciled.get("proposal_decision_sha256") != proposal.get("decision_sha256")
        or reconciled.get("critic_decision_sha256") != critic.get("decision_sha256")
        or reconciled_receipt.get("provenance_preserved_as") != "chatgpt_verified"
        or reconciled_receipt.get("release_authority_included") is not False
        or _contains_forbidden_key([proposal, critic, reconciled], {"answer_decimal", "raw_value", "raw_source_cell"})
    ):
        raise ValueError("Q746 independent proposer/critic consensus boundary is invalid")

    q746_materialization_policy = _mapping(
        policy["cross_entity_subtract_materialization_q746_v2"],
        "cross_entity_subtract_materialization_q746_v2",
    )
    q746_materialization = _load_bound_json(
        repository_root, q746_materialization_policy, "Q746 subtract materialization"
    )
    q746_materialization_counts = _mapping(q746_materialization.get("counts"), "Q746 materialization counts")
    for name in ("question_count", "token_count", "private_execution_count", "execution_status_counts"):
        if q746_materialization_counts.get(name) != q746_materialization_policy.get(name):
            raise ValueError(f"Q746 materialization threshold mismatch: {name}")
    q746_public: list[dict[str, Any]] = []
    for name in ("public_receipts", "public_tokens"):
        record = _mapping(
            _mapping(q746_materialization.get("outputs"), "Q746 materialization outputs").get(name),
            f"Q746 {name}",
        )
        artifact = _resolve(repository_root, record.get("path"), f"Q746 {name}")
        if not artifact.is_file() or sha256_file(artifact) != record.get("sha256"):
            raise ValueError(f"immutable artifact mismatch: Q746 {name}")
        q746_public.extend(_load_jsonl(artifact, f"Q746 {name}"))
    q746_materialization_contract = _mapping(q746_materialization.get("source_contract"), "Q746 materialization contract")
    if (
        q746_materialization_contract.get("reviewer_numeric_value_exposure") is not False
        or q746_materialization_contract.get("answer_materialization_allowed") is not False
        or q746_materialization_contract.get("release_authorized") is not False
        or _contains_forbidden_key(q746_public, {"answer_decimal", "raw_value", "raw_source_cell"})
    ):
        raise ValueError("Q746 materialization public boundary is invalid")

    merged_policy = _mapping(
        policy["cross_entity_binding_chatgpt_promotion_q746_q750_v2"],
        "cross_entity_binding_chatgpt_promotion_q746_q750_v2",
    )
    merged = _load_bound_json(repository_root, merged_policy, "merged Q746/Q750 promotion")
    merged_counts = _mapping(merged.get("counts"), "merged promotion counts")
    for name in ("promotion_count", "operand_count", "numeric_value_exposure_count"):
        if merged_counts.get(name) != merged_policy.get(name):
            raise ValueError(f"merged cross-entity promotion threshold mismatch: {name}")
    merged_decision_record = _mapping(
        _mapping(merged.get("outputs"), "merged promotion outputs").get("decisions"),
        "merged promotion decisions",
    )
    merged_decision_path = _resolve(repository_root, merged_decision_record.get("path"), "merged promotion decisions")
    if not merged_decision_path.is_file() or sha256_file(merged_decision_path) != merged_decision_record.get("sha256"):
        raise ValueError("immutable artifact mismatch: merged promotion decisions")
    merged_decisions = _load_jsonl(merged_decision_path, "merged promotion decisions")
    if (
        {row.get("question_id") for row in merged_decisions} != {746, 750}
        or any(row.get("decision") != "approve_cross_entity_binding_promotion" for row in merged_decisions)
        or _contains_forbidden_key(merged_decisions, {"answer_decimal", "raw_value", "raw_source_cell"})
    ):
        raise ValueError("merged Q746/Q750 promotion boundary is invalid")

    v11_policy = _mapping(policy["grounded_v11_dual_review_cross_entity"], "grounded_v11_dual_review_cross_entity")
    v11 = _load_bound_json(repository_root, v11_policy, "grounded V11")
    v11_authorization = _mapping(_mapping(v11.get("outputs"), "V11 outputs").get("authorization"), "V11 authorization")
    v11_counts = _mapping(v11_authorization.get("counts"), "V11 authorization counts")
    for name in (
        "chatgpt_cross_entity_promotion_count", "human_semantic_approval_count",
        "effective_semantic_approval_count", "answer_certificate_status_counts",
        "evidence_binding_status_counts",
    ):
        if v11_counts.get(name) != v11_policy.get(name):
            raise ValueError(f"V11 threshold mismatch: {name}")
    if (
        v11.get("binding_protocol") != v11_policy.get("binding_protocol")
        or len(_mapping(v11.get("reproducibility"), "V11 reproducibility")) != 5
        or not all(_mapping(v11.get("reproducibility"), "V11 reproducibility").values())
    ):
        raise ValueError("V11 reproducibility or protocol mismatch")
    for field in ("bindings", "execution"):
        old_rows = question_index(v10, field)
        new_rows = question_index(v11, field)
        if [qid for qid in sorted(old_rows) if old_rows[qid] != new_rows[qid]] != [746]:
            raise ValueError(f"V11 {field} diff is not isolated to Q746")
    v11_authorization_rows: dict[str, dict[int, dict[str, Any]]] = {}
    for field in ("evidence_bindings", "answer_certificates"):
        old_rows = v9_auth_index(v10, field)
        new_rows = v9_auth_index(v11, field)
        if [qid for qid in sorted(old_rows) if old_rows[qid] != new_rows[qid]] != [746]:
            raise ValueError(f"V11 {field} diff is not isolated to Q746")
        v11_authorization_rows[field] = new_rows
    for question_id in (746, 750):
        certificate = _mapping(
            v11_authorization_rows["answer_certificates"][question_id].get("answer_certificate"),
            f"Q{question_id} V11 certificate",
        )
        if certificate.get("status") != "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY" or len(certificate.get("binding_receipts") or []) != 2:
            raise ValueError(f"Q{question_id} V11 certificate is incomplete")

    v11_campaign_policy = _mapping(policy["grounded_campaign_review_v11"], "grounded_campaign_review_v11")
    v11_campaign = _load_bound_json(repository_root, v11_campaign_policy, "V11 campaign handoff")
    for name in ("campaign_status", "candidate_count", "human_campaign_decision_count"):
        if v11_campaign.get(name) != v11_campaign_policy.get(name):
            raise ValueError(f"V11 campaign handoff threshold mismatch: {name}")
    v11_role_record = _mapping(
        _mapping(v11_campaign.get("outputs"), "V11 campaign outputs").get("entity_role_issue_briefs"),
        "V11 role issues",
    )
    v11_role_path = _resolve(repository_root, v11_role_record.get("path"), "V11 role issues")
    if len(_load_jsonl(v11_role_path, "V11 role issues")) != v11_campaign_policy.get("entity_role_issue_count"):
        raise ValueError("V11 campaign entity-role issue count mismatch")

    v11_audit_policy = _mapping(policy["grounded_campaign_chatgpt_audit_v11"], "grounded_campaign_chatgpt_audit_v11")
    v11_audit = _load_bound_json(repository_root, v11_audit_policy, "V11 campaign audit")
    for name in ("status", "candidate_count", "candidate_review_outcome_counts", "blocking_issue_count", "numeric_value_exposure_count"):
        if v11_audit.get(name) != v11_audit_policy.get(name):
            raise ValueError(f"V11 campaign audit threshold mismatch: {name}")
    v11_authority = _mapping(v11_audit.get("review_authority"), "V11 review authority")
    v11_decision_record = _mapping(_mapping(v11_audit.get("outputs"), "V11 audit outputs").get("decision"), "V11 audit decision")
    v11_decision_path = _resolve(repository_root, v11_decision_record.get("path"), "V11 audit decision")
    if not v11_decision_path.is_file() or sha256_file(v11_decision_path) != v11_decision_record.get("sha256"):
        raise ValueError("immutable artifact mismatch: V11 campaign decision")
    v11_decision = _load_jsonl(v11_decision_path, "V11 campaign decision")[0]
    v11_reviews = v11_decision.get("candidate_reviews") or []
    v11_q746 = [review for review in v11_reviews if review.get("question_id") == 746]
    if (
        v11_authority.get("verification_authority") != v11_audit_policy.get("verification_authority")
        or v11_authority.get("provenance_preserved_as") != v11_audit_policy.get("provenance_preserved_as")
        or v11_authority.get("release_authority_included") is not v11_audit_policy.get("release_authority_included")
        or v11_decision.get("campaign_id") != v11_campaign.get("campaign_id")
        or v11_decision.get("decision") != "approve_campaign"
        or len(v11_reviews) != 15
        or len(v11_q746) != 1
        or v11_q746[0].get("review_outcome") != "approved_candidate"
        or v11_q746[0].get("reason_code") != "EXACT_DUAL_REVIEW_CROSS_ENTITY_COMPOSITION_PROVEN"
        or len(v11_q746[0].get("source_reopens") or []) != 2
        or _mapping(v11_q746[0].get("execution_replay"), "Q746 audit execution").get("operation") != "subtract"
        or v11_decision.get("release_authorized") is not False
        or v11_decision.get("promotion_allowed") is not False
        or _contains_forbidden_key(v11_reviews, {"answer_decimal", "raw_value"})
    ):
        raise ValueError("V11 human-equivalent ChatGPT audit boundary is invalid")

    relational_question_ids = {6, 10, 27, 145, 168, 181, 184, 249, 292, 316, 325}
    relational_candidate_policy = _mapping(
        policy["entity_role_relational_candidates_v1"],
        "entity_role_relational_candidates_v1",
    )
    relational_candidate_manifest = _load_bound_json(
        repository_root, relational_candidate_policy, "entity-role relational candidates"
    )
    relational_candidate_counts = _mapping(
        relational_candidate_manifest.get("counts"), "relational candidate counts"
    )
    for name in (
        "queue_item_count", "candidate_count", "answer_numeric_value_exposure_count",
        "documents_with_relational_candidate",
    ):
        if relational_candidate_counts.get(name) != relational_candidate_policy.get(name):
            raise ValueError(f"entity-role relational candidate threshold mismatch: {name}")
    relational_queue_record = _mapping(
        _mapping(relational_candidate_manifest.get("outputs"), "relational candidate outputs").get("queue"),
        "relational candidate queue",
    )
    relational_queue_path = _resolve(
        repository_root, relational_queue_record.get("path"), "relational candidate queue"
    )
    if (
        not relational_queue_path.is_file()
        or sha256_file(relational_queue_path) != relational_queue_record.get("sha256")
    ):
        raise ValueError("immutable artifact mismatch: relational candidate queue")
    relational_queue_rows = _load_jsonl(relational_queue_path, "relational candidate queue")
    if (
        {row.get("question_id") for row in relational_queue_rows} != relational_question_ids
        or any(row.get("reporting_scope") != "separate" for row in relational_queue_rows)
        or any(
            _mapping(row.get("source_contract"), "relational candidate contract").get(
                "separate_scope_is_parent_evidence"
            )
            is not False
            for row in relational_queue_rows
        )
        or any(
            candidate.get("assertion_type") != "issuer_subsidiary_relation"
            for row in relational_queue_rows
            for candidate in row.get("candidates") or []
        )
        or _contains_forbidden_key(relational_queue_rows, {"answer_decimal", "raw_value", "raw_source_cell"})
    ):
        raise ValueError("entity-role relational candidate boundary is invalid")

    dual_reviews: dict[str, tuple[dict[str, Any], dict[int, dict[str, Any]]]] = {}
    for section, expected_role in (
        ("entity_role_relational_chatgpt_proposal_v1", "authorized_ai_entity_role_evidence_proposer"),
        ("entity_role_relational_chatgpt_critic_v1", "authorized_ai_entity_role_evidence_critic"),
    ):
        section_policy = _mapping(policy[section], section)
        manifest = _load_bound_json(repository_root, section_policy, section)
        counts = _mapping(manifest.get("counts"), f"{section}.counts")
        for name in ("decision_count", "approve_relational_parent_role"):
            if counts.get(name) != section_policy.get(name):
                raise ValueError(f"{section} threshold mismatch: {name}")
        record = _mapping(
            _mapping(manifest.get("outputs"), f"{section}.outputs").get("decisions"),
            f"{section}.decisions",
        )
        path = _resolve(repository_root, record.get("path"), f"{section}.decisions")
        if not path.is_file() or sha256_file(path) != record.get("sha256"):
            raise ValueError(f"immutable artifact mismatch: {section}.decisions")
        rows = {int(row["question_id"]): row for row in _load_jsonl(path, section)}
        if (
            set(rows) != relational_question_ids
            or any(row.get("decision") != "approve_relational_parent_role" for row in rows.values())
            or any(
                _mapping(row.get("decision_provenance"), f"{section}.provenance").get("reviewer_role")
                != expected_role
                for row in rows.values()
            )
            or any(
                _mapping(row.get("decision_provenance"), f"{section}.provenance").get(
                    "verification_authority"
                )
                != "human_equivalent"
                for row in rows.values()
            )
            or _contains_forbidden_key(list(rows.values()), {"answer_decimal", "raw_value", "raw_source_cell"})
        ):
            raise ValueError(f"{section} review boundary is invalid")
        dual_reviews[section] = (manifest, rows)

    proposal_rows = dual_reviews["entity_role_relational_chatgpt_proposal_v1"][1]
    critic_rows = dual_reviews["entity_role_relational_chatgpt_critic_v1"][1]
    if any(
        _mapping(proposal_rows[qid].get("decision_provenance"), "proposal provenance").get("reviewer_id")
        == _mapping(critic_rows[qid].get("decision_provenance"), "critic provenance").get("reviewer_id")
        for qid in relational_question_ids
    ):
        raise ValueError("relational proposer and critic identities are not independent")

    relational_adjudication_policy = _mapping(
        policy["entity_role_relational_dual_chatgpt_v1"],
        "entity_role_relational_dual_chatgpt_v1",
    )
    relational_adjudication = _load_bound_json(
        repository_root, relational_adjudication_policy, "entity-role relational adjudication"
    )
    adjudication_counts = _mapping(
        relational_adjudication.get("counts"), "relational adjudication counts"
    )
    for name in ("status",):
        if relational_adjudication.get(name) != relational_adjudication_policy.get(name):
            raise ValueError(f"relational adjudication threshold mismatch: {name}")
    for name in ("decision_count", "approve_relational_parent_role"):
        if adjudication_counts.get(name) != relational_adjudication_policy.get(name):
            raise ValueError(f"relational adjudication threshold mismatch: {name}")
    adjudication_record = _mapping(
        _mapping(relational_adjudication.get("outputs"), "relational adjudication outputs").get("decisions"),
        "relational adjudication decisions",
    )
    adjudication_path = _resolve(
        repository_root, adjudication_record.get("path"), "relational adjudication decisions"
    )
    if not adjudication_path.is_file() or sha256_file(adjudication_path) != adjudication_record.get("sha256"):
        raise ValueError("immutable artifact mismatch: relational adjudication decisions")
    adjudication_rows = {
        int(row["question_id"]): row
        for row in _load_jsonl(adjudication_path, "relational adjudication decisions")
    }
    if (
        set(adjudication_rows) != relational_question_ids
        or any(
            row.get("decision") != "approve_relational_parent_role"
            or row.get("proposal_decision_sha256") != proposal_rows[qid].get("decision_sha256")
            or row.get("critic_decision_sha256") != critic_rows[qid].get("decision_sha256")
            or row.get("selected_source_anchor") != proposal_rows[qid].get("selected_source_anchor")
            or row.get("selected_source_anchor") != critic_rows[qid].get("selected_source_anchor")
            or _mapping(row.get("decision_provenance"), "relational reconciler provenance").get(
                "reviewer_role"
            )
            != "deterministic_entity_role_dual_review_reconciler"
            or _mapping(row.get("authority_receipt"), "relational authority receipt").get(
                "provenance_preserved_as"
            )
            != "chatgpt_verified"
            or _mapping(row.get("source_contract"), "relational adjudication contract").get(
                "separate_scope_is_parent_evidence"
            )
            is not False
            for qid, row in adjudication_rows.items()
        )
    ):
        raise ValueError("relational exact-consensus boundary is invalid")

    augmentation_policy = _mapping(
        policy["semantic_binding_relational_role_augmentation_v12"],
        "semantic_binding_relational_role_augmentation_v12",
    )
    augmentation = _load_bound_json(repository_root, augmentation_policy, "V12 role augmentation")
    augmentation_counts = _mapping(augmentation.get("counts"), "V12 role augmentation counts")
    if augmentation.get("status") != augmentation_policy.get("status"):
        raise ValueError("V12 role augmentation status mismatch")
    for name in ("decision_count", "existing_role_preserved", "relational_role_augmented", "role_not_applicable"):
        if augmentation_counts.get(name) != augmentation_policy.get(name):
            raise ValueError(f"V12 role augmentation threshold mismatch: {name}")
    augmentation_record = _mapping(
        _mapping(augmentation.get("outputs"), "V12 role augmentation outputs").get("decisions"),
        "V12 semantic decisions",
    )
    augmentation_path = _resolve(repository_root, augmentation_record.get("path"), "V12 semantic decisions")
    if not augmentation_path.is_file() or sha256_file(augmentation_path) != augmentation_record.get("sha256"):
        raise ValueError("immutable artifact mismatch: V12 semantic decisions")
    augmented_rows = _load_jsonl(augmentation_path, "V12 semantic decisions")
    augmented_relational = [row for row in augmented_rows if row.get("entity_role_inference_rule")]
    if (
        len(augmented_relational) != 11
        or any(
            _mapping(row.get("decision_provenance"), "V12 original provenance").get("reviewer_type")
            != "human_verified"
            for row in augmented_rows
        )
        or any(
            row.get("entity_role_inference_rule")
            != "issuer_identity_plus_subsidiary_relation_implies_parent"
            or _mapping(row.get("entity_role_decision_provenance"), "V12 role provenance").get(
                "reviewer_type"
            )
            != "chatgpt_verified"
            for row in augmented_relational
        )
        or _mapping(augmentation.get("source_contract"), "V12 augmentation contract").get(
            "separate_scope_is_parent_evidence"
        )
        is not False
    ):
        raise ValueError("V12 role augmentation provenance boundary is invalid")

    v12_policy = _mapping(policy["grounded_v12_relational_entity_role"], "grounded_v12_relational_entity_role")
    v12 = _load_bound_json(repository_root, v12_policy, "grounded V12")
    v12_authorization = _mapping(_mapping(v12.get("outputs"), "V12 outputs").get("authorization"), "V12 authorization")
    v12_counts = _mapping(v12_authorization.get("counts"), "V12 authorization counts")
    for name in (
        "chatgpt_cross_entity_promotion_count", "human_semantic_approval_count",
        "effective_semantic_approval_count", "answer_certificate_status_counts",
        "evidence_binding_status_counts",
    ):
        if v12_counts.get(name) != v12_policy.get(name):
            raise ValueError(f"V12 threshold mismatch: {name}")
    if (
        _mapping(v12_counts.get("field_status_counts"), "V12 field status counts").get("entity_role")
        != v12_policy.get("entity_role_field_status_counts")
        or v12.get("binding_protocol") != v12_policy.get("binding_protocol")
        or len(_mapping(v12.get("reproducibility"), "V12 reproducibility")) != 5
        or not all(_mapping(v12.get("reproducibility"), "V12 reproducibility").values())
    ):
        raise ValueError("V12 role counts, protocol, or reproducibility mismatch")
    for field in ("bindings", "execution"):
        old_rows = question_index(v11, field)
        new_rows = question_index(v12, field)
        if {qid for qid in old_rows if old_rows[qid] != new_rows[qid]}:
            raise ValueError(f"V12 {field} changed despite role-only authorization remediation")
    v12_certificate_rows: dict[int, dict[str, Any]] = {}
    for field in ("evidence_bindings", "answer_certificates"):
        old_rows = v9_auth_index(v11, field)
        new_rows = v9_auth_index(v12, field)
        if {qid for qid in old_rows if old_rows[qid] != new_rows[qid]} != relational_question_ids:
            raise ValueError(f"V12 {field} diff is not isolated to relational role questions")
        if field == "answer_certificates":
            v12_certificate_rows = new_rows
    if any(
        _mapping(v12_certificate_rows[qid].get("answer_certificate"), f"Q{qid} V12 certificate").get("status")
        != "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"
        for qid in relational_question_ids
    ):
        raise ValueError("V12 relational certificates are incomplete")

    v12_campaign_policy = _mapping(policy["grounded_campaign_review_v12"], "grounded_campaign_review_v12")
    v12_campaign = _load_bound_json(repository_root, v12_campaign_policy, "V12 campaign handoff")
    for name in ("campaign_status", "candidate_count", "human_campaign_decision_count"):
        if v12_campaign.get(name) != v12_campaign_policy.get(name):
            raise ValueError(f"V12 campaign handoff threshold mismatch: {name}")
    v12_role_record = _mapping(
        _mapping(v12_campaign.get("outputs"), "V12 campaign outputs").get("entity_role_issue_briefs"),
        "V12 role issues",
    )
    v12_role_path = _resolve(repository_root, v12_role_record.get("path"), "V12 role issues")
    if len(_load_jsonl(v12_role_path, "V12 role issues")) != v12_campaign_policy.get("entity_role_issue_count"):
        raise ValueError("V12 campaign entity-role issue count mismatch")

    v12_audit_policy = _mapping(policy["grounded_campaign_chatgpt_audit_v12"], "grounded_campaign_chatgpt_audit_v12")
    v12_audit = _load_bound_json(repository_root, v12_audit_policy, "V12 campaign audit")
    for name in (
        "status", "candidate_count", "candidate_review_outcome_counts",
        "blocking_issue_count", "numeric_value_exposure_count",
    ):
        if v12_audit.get(name) != v12_audit_policy.get(name):
            raise ValueError(f"V12 campaign audit threshold mismatch: {name}")
    v12_authority = _mapping(v12_audit.get("review_authority"), "V12 review authority")
    v12_decision_record = _mapping(
        _mapping(v12_audit.get("outputs"), "V12 audit outputs").get("decision"),
        "V12 audit decision",
    )
    v12_decision_path = _resolve(repository_root, v12_decision_record.get("path"), "V12 audit decision")
    if not v12_decision_path.is_file() or sha256_file(v12_decision_path) != v12_decision_record.get("sha256"):
        raise ValueError("immutable artifact mismatch: V12 campaign decision")
    v12_decision = _load_jsonl(v12_decision_path, "V12 campaign decision")[0]
    v12_reviews = v12_decision.get("candidate_reviews") or []
    v12_relational_reviews = [review for review in v12_reviews if review.get("question_id") in relational_question_ids]
    if (
        v12_authority.get("verification_authority") != v12_audit_policy.get("verification_authority")
        or v12_authority.get("provenance_preserved_as") != v12_audit_policy.get("provenance_preserved_as")
        or v12_authority.get("release_authority_included") is not v12_audit_policy.get("release_authority_included")
        or v12_decision.get("campaign_id") != v12_campaign.get("campaign_id")
        or v12_decision.get("decision") != "approve_campaign"
        or len(v12_reviews) != 26
        or len(v12_relational_reviews) != 11
        or any(review.get("reason_code") != "EXACT_RELATIONAL_PARENT_ROLE_PROVEN" for review in v12_relational_reviews)
        or any(
            _mapping(review.get("entity_role_reopen"), "V12 role reopen").get("status") != "PASS"
            for review in v12_relational_reviews
        )
        or v12_decision.get("release_authorized") is not False
        or v12_decision.get("promotion_allowed") is not False
        or _contains_forbidden_key(v12_reviews, {"answer_decimal", "raw_value"})
    ):
        raise ValueError("V12 human-equivalent ChatGPT audit boundary is invalid")
    return {
        "status": "full_integration_gate_pass",
        "artifact_validation": True,
        "baseline_sha256": baseline_policy["sha256"],
        "hierarchy_default_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--policy-only", action="store_true")
    args = parser.parse_args()
    result = validate(
        policy_path=args.policy.resolve(),
        repository_root=args.repository_root.resolve(),
        policy_only=args.policy_only,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
