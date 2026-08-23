#!/usr/bin/env python3
"""Validate one independent, hash-bound human or authorized-AI campaign decision."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


HANDOFF_PROTOCOL = "vifinqa_grounded_campaign_review_handoff_v1"
HUMAN_DECISION_PROTOCOL = "vifinqa_grounded_campaign_human_decision_v1"
CHATGPT_DECISION_PROTOCOL = "vifinqa_grounded_campaign_chatgpt_decision_v1"
DECISION_PROTOCOLS = frozenset({HUMAN_DECISION_PROTOCOL, CHATGPT_DECISION_PROTOCOL})
ALLOWED_DECISIONS = frozenset({"approve_campaign", "reject_campaign", "needs_revision"})
EVIDENCE_FIELDS = (
    "stratified_source_reopen_complete",
    "high_severity_review_complete",
    "mutation_suite_review_complete",
    "rule_and_code_hash_review_complete",
    "candidate_manifest_diff_review_complete",
    "entity_role_issue_review_complete",
)
MULTI_BINDING_EVIDENCE_FIELD = "multi_binding_composition_review_complete"
ISSUE_ANSWER_OPTIONS = frozenset({"yes", "no", "uncertain"})
ISSUE_OUTCOMES = frozenset({"confirmed_gap", "role_provenance_present", "needs_investigation"})
CANDIDATE_REVIEW_PROTOCOL = "vifinqa_grounded_campaign_candidate_chatgpt_review_v1"
CANDIDATE_OUTCOMES = frozenset({"approved_candidate", "semantic_mismatch", "needs_investigation"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Campaign decision requires mapping: {label}")
    return value


def require_record(record: Mapping[str, Any], label: str) -> Path:
    path = Path(str(record.get("path") or ""))
    expected = record.get("sha256")
    if not path.is_file() or not isinstance(expected, str) or sha256_file(path) != expected:
        raise ValueError(f"Immutable artifact mismatch: {label}")
    return path


def validate(*, handoff_manifest: Path, decision_path: Path) -> dict[str, Any]:
    handoff = load_json(handoff_manifest)
    if (
        handoff.get("protocol") != HANDOFF_PROTOCOL
        or handoff.get("campaign_status") not in {"awaiting_independent_campaign_review", "semantic_revision_required"}
        or handoff.get("human_campaign_decision_count") != 0
    ):
        raise ValueError("Campaign handoff is not a blank independent-review packet")
    contract = mapping(handoff.get("source_contract"), "handoff.source_contract")
    if contract.get("promotion_allowed") is not False or contract.get("may_materialize_answer") is not False:
        raise ValueError("Campaign handoff source contract is unsafe")

    outputs = mapping(handoff.get("outputs"), "handoff.outputs")
    candidates_path = require_record(mapping(outputs.get("candidates"), "outputs.candidates"), "campaign candidates")
    require_record(mapping(outputs.get("review_packet"), "outputs.review_packet"), "campaign review packet")
    require_record(mapping(outputs.get("blank_human_response"), "outputs.blank_human_response"), "blank campaign response")
    candidates = load_jsonl(candidates_path)
    if len(candidates) != handoff.get("candidate_count") or not candidates:
        raise ValueError("Campaign candidate coverage is invalid")
    role_issue_record = outputs.get("entity_role_issue_briefs")
    role_issues: list[dict[str, Any]] = []
    if isinstance(role_issue_record, Mapping):
        role_issues = load_jsonl(require_record(role_issue_record, "entity role issue briefs"))

    decisions = load_jsonl(decision_path)
    if len(decisions) != 1:
        raise ValueError("Campaign decision file must contain exactly one decision")
    decision = decisions[0]
    if (
        decision.get("protocol") not in DECISION_PROTOCOLS
        or decision.get("campaign_id") != handoff.get("campaign_id")
        or decision.get("campaign_candidate_manifest_sha256") != sha256_file(candidates_path)
    ):
        raise ValueError("Campaign decision lineage is invalid")
    verdict = str(decision.get("decision") or "")
    if verdict not in ALLOWED_DECISIONS:
        raise ValueError("Campaign decision verdict is invalid")
    provenance = mapping(decision.get("decision_provenance"), "decision_provenance")
    reviewer_id = str(provenance.get("reviewer_id") or "").strip()
    reviewer_type = str(provenance.get("reviewer_type") or "")
    if not reviewer_id:
        raise ValueError("Campaign decision requires a reviewer identity")
    if reviewer_type == "human_verified":
        if (
            decision.get("protocol") != HUMAN_DECISION_PROTOCOL
            or provenance.get("reviewer_role") != "independent_campaign_reviewer"
        ):
            raise ValueError("Human campaign decision requires independent human provenance")
    elif reviewer_type == "chatgpt_verified":
        authority = mapping(provenance.get("authority_grant"), "decision_provenance.authority_grant")
        if (
            decision.get("protocol") != CHATGPT_DECISION_PROTOCOL
            or provenance.get("reviewer_role") != "authorized_ai_campaign_reviewer"
            or provenance.get("review_policy") != "fail_closed_evidence_bound_v1"
            or provenance.get("verification_authority") not in {None, "human_equivalent"}
            or not str(provenance.get("model_family") or "").strip()
            or authority.get("granted_by") != "campaign_owner"
            or authority.get("grant_scope") != "campaign_review_gate_equivalence"
            or authority.get("grant_basis") != "explicit_user_instruction"
        ):
            raise ValueError("ChatGPT campaign decision requires an explicit, fail-closed authority grant")
    else:
        raise ValueError("Campaign decision reviewer type is not authorized")
    verification_authority = str(provenance.get("verification_authority") or "")
    if verification_authority:
        authority_receipt = mapping(decision.get("authority_receipt"), "authority_receipt")
        if (
            verification_authority != "human_equivalent"
            or authority_receipt.get("verification_authority") != "human_equivalent"
            or authority_receipt.get("gate_effect") != "same_eligibility_weight_as_human_verified"
            or authority_receipt.get("provenance_preserved_as") != "chatgpt_verified"
            or authority_receipt.get("scope") != "campaign_review_gate_equivalence"
            or authority_receipt.get("release_authority_included") is not False
        ):
            raise ValueError("ChatGPT human-equivalent authority receipt is invalid")

    run_path = require_record(
        mapping(mapping(handoff.get("inputs"), "handoff.inputs").get("grounded_run"), "inputs.grounded_run"),
        "grounded run",
    )
    run = load_json(run_path)
    authorization = mapping(mapping(run.get("outputs"), "run.outputs").get("authorization"), "run.authorization")
    certificate_manifest_path = Path(str(authorization.get("manifest_path") or ""))
    if not certificate_manifest_path.is_file() or sha256_file(certificate_manifest_path) != authorization.get("manifest_sha256"):
        raise ValueError("Immutable artifact mismatch: authorization manifest")
    certificate_manifest = load_json(certificate_manifest_path)
    semantic_record = mapping(mapping(certificate_manifest.get("inputs"), "authorization.inputs").get("semantic_human_decisions"), "semantic_human_decisions")
    semantic_path = require_record(semantic_record, "semantic human decisions")
    semantic_reviewers = {
        str((row.get("decision_provenance") or {}).get("reviewer_id") or "").strip()
        for row in load_jsonl(semantic_path)
    }
    if reviewer_id in semantic_reviewers:
        raise ValueError("Campaign reviewer must be independent from semantic reviewers")

    evidence = mapping(decision.get("review_evidence"), "review_evidence")
    has_multi_binding_candidate = any(len(candidate.get("binding_ids") or []) > 1 for candidate in candidates)
    expected_evidence_fields = set(EVIDENCE_FIELDS)
    if has_multi_binding_candidate:
        expected_evidence_fields.add(MULTI_BINDING_EVIDENCE_FIELD)
    if set(evidence) != expected_evidence_fields or any(
        type(evidence[name]) is not bool for name in expected_evidence_fields
    ):
        raise ValueError("Campaign decision requires explicit boolean review evidence")
    if verdict == "approve_campaign" and not all(
        evidence[name] is True for name in expected_evidence_fields
    ):
        raise ValueError("Campaign approval requires every review evidence gate")
    candidate_reviews = decision.get("candidate_reviews")
    candidate_blocker_count = 0
    if handoff.get("campaign_status") == "awaiting_independent_campaign_review":
        if not isinstance(candidate_reviews, list) or len(candidate_reviews) != len(candidates):
            raise ValueError("Independent V7 campaign review must reopen every candidate")
        expected_candidates = {
            str(candidate.get("campaign_candidate_sha256") or ""): candidate for candidate in candidates
        }
        seen_candidate_reviews: set[str] = set()
        for review in candidate_reviews:
            if not isinstance(review, Mapping) or review.get("protocol") != CANDIDATE_REVIEW_PROTOCOL:
                raise ValueError("Campaign candidate review protocol is invalid")
            candidate_sha = str(review.get("campaign_candidate_sha256") or "")
            candidate = expected_candidates.get(candidate_sha)
            if candidate is None or candidate_sha in seen_candidate_reviews:
                raise ValueError("Campaign candidate review lineage is invalid")
            seen_candidate_reviews.add(candidate_sha)
            if (
                review.get("question_id") != candidate.get("question_id")
                or review.get("stage_id") != candidate.get("stage_id")
                or review.get("review_outcome") not in CANDIDATE_OUTCOMES
                or not str(review.get("reason_code") or "").strip()
                or not str(review.get("rationale") or "").strip()
            ):
                raise ValueError("Campaign candidate review semantics are incomplete")
            claimed_review_sha = str(review.get("candidate_review_sha256") or "")
            review_payload = dict(review)
            review_payload.pop("candidate_review_sha256", None)
            if claimed_review_sha != canonical_sha256(review_payload):
                raise ValueError("Campaign candidate review hash is invalid")
            execution_replay = mapping(review.get("execution_replay"), "candidate_review.execution_replay")
            review_contract = mapping(review.get("source_contract"), "candidate_review.source_contract")
            candidate_binding_count = len(candidate.get("binding_ids") or [])
            if candidate_binding_count < 1:
                raise ValueError("Campaign candidate has no binding receipt")
            if candidate_binding_count == 1:
                source_reopens = [mapping(review.get("source_reopen"), "candidate_review.source_reopen")]
                coordinate_verified = execution_replay.get("operand_coordinate_verified") is True
                if "source_reopens" in review or "operand_coordinates_verified" in execution_replay:
                    raise ValueError("Single-binding review uses an ambiguous multi-binding shape")
            else:
                source_reopens_raw = review.get("source_reopens")
                if not isinstance(source_reopens_raw, list) or len(source_reopens_raw) != candidate_binding_count:
                    raise ValueError("Multi-binding review must reopen every exact source")
                source_reopens = [
                    mapping(value, f"candidate_review.source_reopens[{index}]")
                    for index, value in enumerate(source_reopens_raw)
                ]
                coordinate_verified = execution_replay.get("operand_coordinates_verified") is True
                if "source_reopen" in review or "operand_coordinate_verified" in execution_replay:
                    raise ValueError("Multi-binding review uses an ambiguous single-binding shape")
                if (
                    execution_replay.get("execution_kind") != "controlled_cross_stage_composition"
                    or execution_replay.get("operation") not in {"subtract"}
                    or not isinstance(execution_replay.get("stage_order"), list)
                    or len(execution_replay.get("stage_order") or []) != candidate_binding_count
                    or len(str(execution_replay.get("operation_ast_sha256") or "")) != 64
                ):
                    raise ValueError("Multi-binding review lacks a controlled composition receipt")
                status_records = review.get("field_status_records")
                role_records = review.get("entity_role_reopens")
                if (
                    not isinstance(status_records, list)
                    or len(status_records) != candidate_binding_count
                    or not isinstance(role_records, list)
                    or len(role_records) != candidate_binding_count
                ):
                    raise ValueError("Multi-binding review lacks per-operand semantic receipts")
                for status_record, role_record in zip(status_records, role_records, strict=True):
                    statuses = mapping(status_record, "candidate_review.field_status_record")
                    role = mapping(role_record, "candidate_review.entity_role_reopen")
                    if (
                        any(
                            statuses.get(f"{field}_status") != "PASS"
                            for field in ("entity", "period", "scope", "source_integrity", "unit", "variable")
                        )
                        or statuses.get("revision_status") not in {"PASS", "NOT_APPLICABLE"}
                        or statuses.get("entity_role_status") != "PASS"
                        or role.get("status") != "PASS"
                        or not role.get("verified_source_anchors")
                    ):
                        raise ValueError("Multi-binding review contains a non-PASS semantic operand")
            expected_source_cells = candidate.get("source_value_cells") or []
            if len(expected_source_cells) != candidate_binding_count:
                raise ValueError("Campaign candidate source-cell count is inconsistent")
            if [source.get("document_uid") for source in source_reopens] != [
                source.get("document_uid") for source in expected_source_cells
            ]:
                raise ValueError("Campaign source reopen order does not match binding order")
            if (
                any(
                    source_reopen.get("verified") is not True
                    or not isinstance(source_reopen.get("row_text_cells"), list)
                    or not source_reopen.get("row_text_cells")
                    or len(str(source_reopen.get("source_value_raw_sha256") or "")) != 64
                    or "raw_value" in source_reopen
                    for source_reopen in source_reopens
                )
                or execution_replay.get("status") != "execution_replay_ready"
                or not coordinate_verified
                or execution_replay.get("numeric_literal_exposed") is not False
                or "answer_decimal" in execution_replay
                or review_contract.get("numeric_literals_exposed_to_reviewer") is not False
                or review_contract.get("may_materialize_answer") is not False
                or review_contract.get("release_authorized") is not False
            ):
                raise ValueError("Campaign candidate review violates the evidence-only boundary")
            if review.get("review_outcome") != "approved_candidate":
                candidate_blocker_count += 1
        if seen_candidate_reviews != set(expected_candidates):
            raise ValueError("Campaign candidate review coverage is incomplete")
        if verdict == "approve_campaign" and candidate_blocker_count:
            raise ValueError("Campaign approval is blocked by candidate semantic mismatches")
    issue_reviews = decision.get("semantic_issue_reviews")
    if role_issues:
        if verdict == "approve_campaign":
            raise ValueError("Campaign approval is blocked by unresolved entity-role issues")
        if not isinstance(issue_reviews, list) or len(issue_reviews) != len(role_issues):
            raise ValueError("Decision must review every entity-role issue brief")
        expected = {str(issue["issue_brief_sha256"]): issue for issue in role_issues}
        seen: set[str] = set()
        for review in issue_reviews:
            if not isinstance(review, Mapping):
                raise ValueError("Entity-role issue review must be an object")
            issue_sha = str(review.get("issue_brief_sha256") or "")
            if issue_sha not in expected or issue_sha in seen:
                raise ValueError("Entity-role issue review lineage is invalid")
            seen.add(issue_sha)
            prompt_ids = {str(prompt["id"]) for prompt in expected[issue_sha].get("review_prompts") or []}
            answers = review.get("answers")
            if not isinstance(answers, Mapping) or set(answers) != prompt_ids or any(value not in ISSUE_ANSWER_OPTIONS for value in answers.values()):
                raise ValueError("Entity-role issue review requires every structured answer")
            if review.get("outcome") not in ISSUE_OUTCOMES or not isinstance(review.get("notes"), str):
                raise ValueError("Entity-role issue review outcome is invalid")
        if evidence["entity_role_issue_review_complete"] is not True:
            raise ValueError("Entity-role issue evidence gate must be complete")
    expected_blocker_count = len(role_issues) + candidate_blocker_count
    if decision.get("blocking_issue_count") != expected_blocker_count:
        raise ValueError("Campaign blocking issue count is invalid")
    notes = decision.get("notes")
    if not isinstance(notes, str) or (verdict != "approve_campaign" and not notes.strip()):
        raise ValueError("Rejected or revision-required campaigns need reviewer notes")
    if (
        not str(decision.get("reviewed_at") or "").strip()
        or decision.get("release_authorized") is not False
        or decision.get("release_policy_gate_required") is not True
        or decision.get("promotion_allowed") is not False
    ):
        raise ValueError("Campaign decision cannot bypass the release policy gate")

    status = {
        "approve_campaign": "campaign_approved_not_released",
        "reject_campaign": "campaign_rejected",
        "needs_revision": "campaign_revision_required",
    }[verdict]
    return {
        "status": status,
        "campaign_id": handoff["campaign_id"],
        "candidate_count": len(candidates),
        "reviewer_id": reviewer_id,
        "reviewer_type": reviewer_type,
        "verification_authority": verification_authority or None,
        "release_authorized": False,
        "promotion_allowed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-manifest", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    args = parser.parse_args()
    result = validate(handoff_manifest=args.handoff_manifest.resolve(), decision_path=args.decision.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
