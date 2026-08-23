from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
HANDOFF_DIR = ROOT / "artifacts/research/grounded_campaign_review_v5_entity_role_v3_20260823"
HANDOFF = HANDOFF_DIR / "grounded_campaign_review_handoff_v1.manifest.json"
TEMPLATE = HANDOFF_DIR / "grounded_campaign_human_response_template_v1.json"
V7_HANDOFF = ROOT / "artifacts/research/grounded_campaign_review_v7_document_role_20260823/grounded_campaign_review_handoff_v1.manifest.json"
V7_DECISION = ROOT / "artifacts/research/grounded_campaign_chatgpt_audit_v7_20260823/grounded_campaign_chatgpt_decision_v1.jsonl"
V10_HANDOFF = ROOT / "artifacts/research/grounded_campaign_review_v10_cross_entity_20260823_lock1/grounded_campaign_review_handoff_v1.manifest.json"
V10_DECISION = ROOT / "artifacts/research/grounded_campaign_chatgpt_audit_v10_cross_entity_20260823_lock2/grounded_campaign_chatgpt_decision_v1.jsonl"
SPEC = importlib.util.spec_from_file_location(
    "validate_grounded_campaign_human_decision",
    ROOT / "scripts/validate_grounded_campaign_human_decision.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def decision(
    tmp_path: Path,
    *,
    reviewer_id: str = "independent-auditor-02",
    verdict: str = "approve_campaign",
    reviewer_type: str = "human_verified",
) -> Path:
    value = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    value.update(
        {
            "decision": verdict,
            "decision_provenance": {
                "reviewer_type": reviewer_type,
                "reviewer_role": "independent_campaign_reviewer",
                "reviewer_id": reviewer_id,
            },
            "reviewed_at": "2026-08-23T02:00:00Z",
            "review_evidence": {name: True for name in MODULE.EVIDENCE_FIELDS},
        }
    )
    if reviewer_type == "chatgpt_verified":
        value["protocol"] = MODULE.CHATGPT_DECISION_PROTOCOL
        value["reviewer_role"] = "authorized_ai_campaign_reviewer"
        value["decision_provenance"].update(
            {
                "reviewer_role": "authorized_ai_campaign_reviewer",
                "model_family": "GPT-5",
                "review_policy": "fail_closed_evidence_bound_v1",
                "authority_grant": {
                    "granted_by": "campaign_owner",
                    "grant_scope": "campaign_review_gate_equivalence",
                    "grant_basis": "explicit_user_instruction",
                },
            }
        )
    issues = [json.loads(line) for line in (HANDOFF_DIR / "entity_role_issue_briefs_v1.jsonl").read_text(encoding="utf-8").splitlines()]
    if verdict != "approve_campaign":
        value["blocking_issue_count"] = len(issues)
        value["semantic_issue_reviews"] = [
            {
                "question_id": issue["question_id"],
                "issue_brief_sha256": issue["issue_brief_sha256"],
                "answers": {prompt["id"]: ("no" if "role" in prompt["id"] else "yes") for prompt in issue["review_prompts"]},
                "outcome": "confirmed_gap",
                "notes": "Entity role remains unproven in the legacy certificate.",
            }
            for issue in issues
        ]
        value["notes"] = "Entity-role schema revision required."
    path = tmp_path / "decision.jsonl"
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_accepts_independent_campaign_revision_without_releasing(tmp_path: Path) -> None:
    result = MODULE.validate(handoff_manifest=HANDOFF, decision_path=decision(tmp_path, verdict="needs_revision"))
    assert result["status"] == "campaign_revision_required"
    assert result["candidate_count"] == 23
    assert result["release_authorized"] is False
    assert result["promotion_allowed"] is False


def test_accepts_authorized_chatgpt_revision_with_distinct_provenance(tmp_path: Path) -> None:
    result = MODULE.validate(
        handoff_manifest=HANDOFF,
        decision_path=decision(
            tmp_path,
            reviewer_id="chatgpt-gpt5-campaign-reviewer-v1",
            verdict="needs_revision",
            reviewer_type="chatgpt_verified",
        ),
    )
    assert result["status"] == "campaign_revision_required"
    assert result["reviewer_type"] == "chatgpt_verified"
    assert result["release_authorized"] is False


def test_rejects_chatgpt_decision_without_explicit_authority_grant(tmp_path: Path) -> None:
    path = decision(tmp_path, verdict="needs_revision", reviewer_type="chatgpt_verified")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["decision_provenance"].pop("authority_grant")
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="authority_grant"):
        MODULE.validate(handoff_manifest=HANDOFF, decision_path=path)


def test_blocks_campaign_approval_while_entity_roles_are_unresolved(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="blocked by unresolved entity-role issues"):
        MODULE.validate(handoff_manifest=HANDOFF, decision_path=decision(tmp_path))


def test_rejects_same_reviewer_and_incomplete_approval_evidence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="independent from semantic reviewers"):
        MODULE.validate(handoff_manifest=HANDOFF, decision_path=decision(tmp_path, reviewer_id="dungle01", verdict="needs_revision"))

    path = decision(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["review_evidence"]["mutation_suite_review_complete"] = False
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="every review evidence gate"):
        MODULE.validate(handoff_manifest=HANDOFF, decision_path=path)


def test_rejection_requires_notes(tmp_path: Path) -> None:
    path = decision(tmp_path, verdict="reject_campaign")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["notes"] = ""
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="need reviewer notes"):
        MODULE.validate(handoff_manifest=HANDOFF, decision_path=path)


def test_accepts_full_v7_chatgpt_audit_but_keeps_campaign_blocked() -> None:
    result = MODULE.validate(handoff_manifest=V7_HANDOFF, decision_path=V7_DECISION)
    assert result["status"] == "campaign_revision_required"
    assert result["candidate_count"] == 12
    assert result["reviewer_type"] == "chatgpt_verified"
    assert result["release_authorized"] is False


def test_rejects_v7_candidate_review_that_exposes_numeric_literal(tmp_path: Path) -> None:
    value = json.loads(V7_DECISION.read_text(encoding="utf-8"))
    review = value["candidate_reviews"][0]
    review["execution_replay"]["answer_decimal"] = "5"
    review_payload = dict(review)
    review_payload.pop("candidate_review_sha256")
    review["candidate_review_sha256"] = MODULE.canonical_sha256(review_payload)
    path = tmp_path / "decision.jsonl"
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="evidence-only boundary"):
        MODULE.validate(handoff_manifest=V7_HANDOFF, decision_path=path)


def test_accepts_v10_human_equivalent_multi_binding_audit_without_release() -> None:
    result = MODULE.validate(handoff_manifest=V10_HANDOFF, decision_path=V10_DECISION)
    assert result == {
        "status": "campaign_approved_not_released",
        "campaign_id": "8e278a2ca826e6fc5b99dbde1469c5a56f1a425716d5c76e3606034c0ec65652",
        "candidate_count": 14,
        "reviewer_id": "chatgpt-gpt5-campaign-auditor-v10",
        "reviewer_type": "chatgpt_verified",
        "verification_authority": "human_equivalent",
        "release_authorized": False,
        "promotion_allowed": False,
    }


def test_rejects_v10_missing_second_source_reopen(tmp_path: Path) -> None:
    value = json.loads(V10_DECISION.read_text(encoding="utf-8"))
    q750 = next(review for review in value["candidate_reviews"] if review["question_id"] == 750)
    q750["source_reopens"] = q750["source_reopens"][:1]
    review_payload = dict(q750)
    review_payload.pop("candidate_review_sha256")
    q750["candidate_review_sha256"] = MODULE.canonical_sha256(review_payload)
    path = tmp_path / "decision.jsonl"
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="reopen every exact source"):
        MODULE.validate(handoff_manifest=V10_HANDOFF, decision_path=path)


def test_rejects_v10_authority_receipt_that_relabels_chatgpt_as_human(tmp_path: Path) -> None:
    value = json.loads(V10_DECISION.read_text(encoding="utf-8"))
    value["authority_receipt"]["provenance_preserved_as"] = "human_verified"
    path = tmp_path / "decision.jsonl"
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="authority receipt is invalid"):
        MODULE.validate(handoff_manifest=V10_HANDOFF, decision_path=path)
