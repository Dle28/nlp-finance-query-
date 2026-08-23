from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.entity_role_relational_reviews import (
    AUGMENTATION_PROTOCOL,
    CRITIC_ROLE,
    PROPOSER_ROLE,
    augment_semantic_decisions_with_relational_roles,
    build_relational_candidate_queue,
    build_relational_chatgpt_decisions,
    reconcile_relational_chatgpt_decisions,
)
from finance_query.semantic_approvals import SEMANTIC_DECISION_PROTOCOL, canonical_sha256, sha256_file


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _authority(role: str, reviewer_id: str) -> tuple[dict, dict]:
    provenance = {
        "reviewer_type": "chatgpt_verified",
        "reviewer_id": reviewer_id,
        "reviewer_role": role,
        "model_family": "GPT-5",
        "review_policy": "fail_closed_evidence_bound_v1",
        "verification_authority": "human_equivalent",
        "authority_grant": {
            "granted_by": "campaign_owner",
            "grant_scope": "entity_role_review_gate_equivalence",
            "grant_basis": "explicit_user_instruction",
        },
    }
    receipt = {
        "verification_authority": "human_equivalent",
        "gate_effect": "same_eligibility_weight_as_human_verified",
        "provenance_preserved_as": "chatgpt_verified",
        "scope": "entity_role_review_gate_equivalence",
        "release_authority_included": False,
        "training_authority_included": False,
        "submission_authority_included": False,
        "value_selection_authority_included": False,
        "formula_execution_authority_included": False,
    }
    return provenance, receipt


def _fixtures(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "source.txt"
    source.write_text(
        "Issuer heading\nCông ty có 3 công ty con tại ngày kết thúc kỳ kế toán.\n",
        encoding="utf-8",
    )
    source_sha = sha256_file(source)
    tables = tmp_path / "tables.jsonl"
    _jsonl(
        tables,
        [{
            "document_id": "ABC_financial_statements_2024_separate",
            "source_provenance": {"source_path": str(source), "source_sha256": source_sha},
        }],
    )
    old_queue_payload = {
        "schema_version": 1,
        "protocol": "vifinqa_entity_role_provenance_candidate_queue_v1",
        "question_id": 1,
        "claim_entity_identity": "ABC",
        "claim_entity_role": "parent",
        "document_uid": "ABC_financial_statements_2024_separate",
        "source_file_sha256": source_sha,
        "candidates": [],
    }
    old_item = {**old_queue_payload, "queue_item_sha256": canonical_sha256(old_queue_payload)}
    old_queue = tmp_path / "old-queue.jsonl"
    _jsonl(old_queue, [old_item])
    old_decision_payload = {
        "schema_version": 1,
        "protocol": "vifinqa_entity_role_provenance_chatgpt_decision_v1",
        "question_id": 1,
        "queue_item_sha256": old_item["queue_item_sha256"],
        "source_candidate_queue_sha256": sha256_file(old_queue),
        "decision": "reject_candidate_set",
    }
    old_decisions = tmp_path / "old-decisions.jsonl"
    _jsonl(old_decisions, [{**old_decision_payload, "decision_sha256": canonical_sha256(old_decision_payload)}])
    return {
        "source": source,
        "tables": tables,
        "old_queue": old_queue,
        "old_decisions": old_decisions,
    }


def _review_spec(path: Path, *, role: str, reviewer_id: str, line_number: int = 2) -> None:
    provenance, receipt = _authority(role, reviewer_id)
    path.write_text(
        json.dumps({
            "protocol": "vifinqa_entity_role_relational_chatgpt_review_spec_v1",
            "decision_provenance": provenance,
            "authority_receipt": receipt,
            "reviews": [{
                "question_id": 1,
                "decision": "approve_relational_parent_role",
                "selected_line_number": line_number,
                "reason_codes": [],
                "rationale": "Exact issuer has subsidiaries; scope is checked independently.",
                "semantic_checks": {
                    "issuer_coreference_valid": True,
                    "issuer_has_subsidiary_relation": True,
                    "parent_role_logically_proven": True,
                    "reporting_scope_checked_independently": True,
                    "same_document": True,
                    "sufficient_for_certificate": True,
                },
            }],
        }),
        encoding="utf-8",
    )


def test_dual_review_materializes_relational_role_without_relabeling_human_provenance(
    tmp_path: Path,
) -> None:
    inputs = _fixtures(tmp_path)
    candidate_result = build_relational_candidate_queue(
        prior_candidate_queue=inputs["old_queue"],
        prior_decisions=inputs["old_decisions"],
        structured_tables=inputs["tables"],
        repository_root=tmp_path,
        output_dir=tmp_path / "candidates",
    )
    assert candidate_result["counts"]["answer_numeric_value_exposure_count"] == 0
    queue = Path(candidate_result["outputs"]["queue"]["path"])
    item = json.loads(queue.read_text())
    assert item["reporting_scope"] == "separate"
    assert item["source_contract"]["separate_scope_is_parent_evidence"] is False
    assert item["candidates"][0]["assertion_type"] == "issuer_subsidiary_relation"

    proposal_spec = tmp_path / "proposal.json"
    critic_spec = tmp_path / "critic.json"
    _review_spec(proposal_spec, role=PROPOSER_ROLE, reviewer_id="proposal-1")
    _review_spec(critic_spec, role=CRITIC_ROLE, reviewer_id="critic-1")
    proposal = build_relational_chatgpt_decisions(
        candidate_queue=queue, review_spec=proposal_spec, output_dir=tmp_path / "proposal"
    )
    critic = build_relational_chatgpt_decisions(
        candidate_queue=queue, review_spec=critic_spec, output_dir=tmp_path / "critic"
    )
    adjudication = reconcile_relational_chatgpt_decisions(
        candidate_queue=queue,
        proposal_decisions=Path(proposal["outputs"]["decisions"]["path"]),
        proposal_manifest=Path(proposal["manifest_path"]),
        critic_decisions=Path(critic["outputs"]["decisions"]["path"]),
        critic_manifest=Path(critic["manifest_path"]),
        output_dir=tmp_path / "adjudication",
    )
    assert adjudication["counts"] == {"decision_count": 1, "approve_relational_parent_role": 1}

    semantic_payload = {
        "question_id": 1,
        "requested_entity": "ABC",
        "requested_entity_role": "parent",
        "document_uid": "ABC_financial_statements_2024_separate",
    }
    semantic_item = {**semantic_payload, "queue_item_sha256": canonical_sha256(semantic_payload)}
    semantic_queue = tmp_path / "semantic-queue.jsonl"
    _jsonl(semantic_queue, [semantic_item])
    prior = tmp_path / "semantic-decisions.jsonl"
    _jsonl(prior, [{
        "protocol": SEMANTIC_DECISION_PROTOCOL,
        "queue_item_sha256": semantic_item["queue_item_sha256"],
        "source_review_queue_sha256": sha256_file(semantic_queue),
        "decision": "approve",
        "decision_provenance": {"reviewer_type": "human_verified", "reviewer_id": "human-1"},
    }])
    augmentation = augment_semantic_decisions_with_relational_roles(
        semantic_queue=semantic_queue,
        prior_semantic_decisions=prior,
        relational_queue=queue,
        reconciled_decisions=Path(adjudication["outputs"]["decisions"]["path"]),
        output_dir=tmp_path / "augmentation",
    )
    assert augmentation["protocol"] == AUGMENTATION_PROTOCOL
    output = json.loads(Path(augmentation["outputs"]["decisions"]["path"]).read_text())
    assert output["decision_provenance"]["reviewer_type"] == "human_verified"
    assert output["entity_role_decision_provenance"]["reviewer_type"] == "chatgpt_verified"
    assert output["entity_role_inference_rule"] == "issuer_identity_plus_subsidiary_relation_implies_parent"


def test_reconciler_keeps_disagreement_fail_closed(tmp_path: Path) -> None:
    inputs = _fixtures(tmp_path)
    candidate_result = build_relational_candidate_queue(
        prior_candidate_queue=inputs["old_queue"],
        prior_decisions=inputs["old_decisions"],
        structured_tables=inputs["tables"],
        repository_root=tmp_path,
        output_dir=tmp_path / "candidates",
    )
    queue = Path(candidate_result["outputs"]["queue"]["path"])
    proposal_spec, critic_spec = tmp_path / "proposal.json", tmp_path / "critic.json"
    _review_spec(proposal_spec, role=PROPOSER_ROLE, reviewer_id="proposal-1")
    _review_spec(critic_spec, role=CRITIC_ROLE, reviewer_id="critic-1")
    critic_json = json.loads(critic_spec.read_text())
    critic_json["reviews"][0].update({
        "decision": "reject_relational_candidate_set",
        "selected_line_number": None,
        "reason_codes": ["ISSUER_COREFERENCE_UNPROVEN"],
    })
    critic_json["reviews"][0]["semantic_checks"]["issuer_coreference_valid"] = False
    critic_spec.write_text(json.dumps(critic_json), encoding="utf-8")
    proposal = build_relational_chatgpt_decisions(
        candidate_queue=queue, review_spec=proposal_spec, output_dir=tmp_path / "proposal"
    )
    critic = build_relational_chatgpt_decisions(
        candidate_queue=queue, review_spec=critic_spec, output_dir=tmp_path / "critic"
    )
    result = reconcile_relational_chatgpt_decisions(
        candidate_queue=queue,
        proposal_decisions=Path(proposal["outputs"]["decisions"]["path"]),
        proposal_manifest=Path(proposal["manifest_path"]),
        critic_decisions=Path(critic["outputs"]["decisions"]["path"]),
        critic_manifest=Path(critic["manifest_path"]),
        output_dir=tmp_path / "reconcile",
    )
    assert result["counts"] == {"decision_count": 1, "confirm_review_disagreement": 1}
    decision = json.loads(Path(result["outputs"]["decisions"]["path"]).read_text())
    assert decision["selected_source_anchor"] is None
    assert decision["source_contract"]["eligible_for_deterministic_materialization"] is False
