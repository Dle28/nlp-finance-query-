from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.campaign_chatgpt_audits import (
    CampaignChatGPTAuditError,
    build_campaign_chatgpt_audit,
)


ROOT = Path(__file__).parents[1]
HANDOFF = ROOT / "artifacts/research/grounded_campaign_review_v7_document_role_20260823/grounded_campaign_review_handoff_v1.manifest.json"
CONFIG = ROOT / "configs/campaign_chatgpt_audit_v7.json"
PINNED = ROOT / "artifacts/research/grounded_campaign_chatgpt_audit_v7_20260823"
V8_HANDOFF = ROOT / "artifacts/research/grounded_campaign_review_v8_semantic_correction_20260823_lock3/grounded_campaign_review_handoff_v1.manifest.json"
V8_CONFIG = ROOT / "configs/campaign_chatgpt_audit_v8.json"
V8_PINNED = ROOT / "artifacts/research/grounded_campaign_chatgpt_audit_v8_20260823_lock3"
V10_HANDOFF = ROOT / "artifacts/research/grounded_campaign_review_v10_cross_entity_20260823_lock1/grounded_campaign_review_handoff_v1.manifest.json"
V10_CONFIG = ROOT / "configs/campaign_chatgpt_audit_v10.json"
V10_PINNED = ROOT / "artifacts/research/grounded_campaign_chatgpt_audit_v10_cross_entity_20260823_lock2"
V12_HANDOFF = ROOT / "artifacts/research/grounded_campaign_review_v12_relational_entity_role_20260823_lock1/grounded_campaign_review_handoff_v1.manifest.json"
V12_CONFIG = ROOT / "configs/campaign_chatgpt_audit_v12.json"
V12_PINNED = ROOT / "artifacts/research/grounded_campaign_chatgpt_audit_v12_relational_entity_role_20260823_lock1"


def test_builds_full_numeric_literal_free_v7_campaign_audit(tmp_path: Path) -> None:
    result = build_campaign_chatgpt_audit(
        handoff_manifest=HANDOFF,
        config_path=CONFIG,
        output_dir=tmp_path / "audit",
    )
    assert result["status"] == "campaign_revision_required"
    assert result["candidate_count"] == 12
    assert result["candidate_review_outcome_counts"] == {
        "approved_candidate": 11,
        "semantic_mismatch": 1,
    }
    assert result["blocking_issue_count"] == 1
    assert result["numeric_value_exposure_count"] == 0
    assert result["outputs"]["decision"]["sha256"] == json.loads(
        (PINNED / "grounded_campaign_chatgpt_audit_v1.manifest.json").read_text(encoding="utf-8")
    )["outputs"]["decision"]["sha256"]

    decision_text = (tmp_path / "audit/grounded_campaign_chatgpt_decision_v1.jsonl").read_text(encoding="utf-8")
    assert "24.274.993.826" not in decision_text
    assert '"answer_decimal"' not in decision_text
    decision = json.loads(decision_text)
    q702 = next(review for review in decision["candidate_reviews"] if review["question_id"] == 702)
    assert q702["review_outcome"] == "semantic_mismatch"
    assert q702["reason_code"] == "NET_OTHER_INCOME_NOT_PROVEN"
    assert q702["alternative_semantic_row"]["row_text_cells"][0].startswith("13. Lợi nhuận khác")


def test_rejects_incomplete_chatgpt_audit_config(tmp_path: Path) -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["reviews"] = config["reviews"][:-1]
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(CampaignChatGPTAuditError, match="cover every campaign candidate"):
        build_campaign_chatgpt_audit(
            handoff_manifest=HANDOFF,
            config_path=config_path,
            output_dir=tmp_path / "audit",
        )


def test_builds_v8_campaign_audit_with_q702_resolved_and_release_still_closed(
    tmp_path: Path,
) -> None:
    result = build_campaign_chatgpt_audit(
        handoff_manifest=V8_HANDOFF,
        config_path=V8_CONFIG,
        output_dir=tmp_path / "v8-audit",
    )
    assert result["status"] == "campaign_approved_not_released"
    assert result["candidate_review_outcome_counts"] == {"approved_candidate": 12}
    assert result["blocking_issue_count"] == 0
    assert result["numeric_value_exposure_count"] == 0
    assert result["outputs"]["decision"]["sha256"] == json.loads(
        (V8_PINNED / "grounded_campaign_chatgpt_audit_v1.manifest.json").read_text(
            encoding="utf-8"
        )
    )["outputs"]["decision"]["sha256"]
    decision = json.loads(
        (tmp_path / "v8-audit/grounded_campaign_chatgpt_decision_v1.jsonl").read_text(
            encoding="utf-8"
        )
    )
    q702 = next(review for review in decision["candidate_reviews"] if review["question_id"] == 702)
    assert q702["review_outcome"] == "approved_candidate"
    assert q702["reason_code"] == "NET_OTHER_INCOME_EXACT_REPORTED_ROW_PROVEN"
    assert q702["source_reopen"]["row_text_cells"] == ["13. Lợi nhuận khác (40 = 31 - 32)"]
    assert decision["release_authorized"] is False
    assert decision["promotion_allowed"] is False


def test_builds_human_equivalent_v10_audit_with_exact_cross_entity_composition(
    tmp_path: Path,
) -> None:
    result = build_campaign_chatgpt_audit(
        handoff_manifest=V10_HANDOFF,
        config_path=V10_CONFIG,
        output_dir=tmp_path / "v10-audit",
    )
    assert result["status"] == "campaign_approved_not_released"
    assert result["candidate_review_outcome_counts"] == {"approved_candidate": 14}
    assert result["numeric_value_exposure_count"] == 0
    assert result["review_authority"] == {
        "verification_authority": "human_equivalent",
        "gate_effect": "same_eligibility_weight_as_human_verified",
        "provenance_preserved_as": "chatgpt_verified",
        "scope": "campaign_review_gate_equivalence",
        "release_authority_included": False,
    }
    assert result["outputs"]["decision"]["sha256"] == json.loads(
        (V10_PINNED / "grounded_campaign_chatgpt_audit_v1.manifest.json").read_text(
            encoding="utf-8"
        )
    )["outputs"]["decision"]["sha256"]

    decision_text = (
        tmp_path / "v10-audit/grounded_campaign_chatgpt_decision_v1.jsonl"
    ).read_text(encoding="utf-8")
    assert '"answer_decimal"' not in decision_text
    decision = json.loads(decision_text)
    assert decision["decision_provenance"]["reviewer_type"] == "chatgpt_verified"
    assert decision["decision_provenance"]["verification_authority"] == "human_equivalent"
    assert decision["release_authorized"] is False
    q750 = next(review for review in decision["candidate_reviews"] if review["question_id"] == 750)
    assert q750["review_outcome"] == "approved_candidate"
    assert q750["reason_code"] == "EXACT_CROSS_ENTITY_COMPOSITION_PROVEN"
    assert [row["document_uid"] for row in q750["source_reopens"]] == [
        "SAB_financial_statements_2024_separate",
        "DBC_financial_statements_2024_separate",
    ]
    assert all(row["verified"] is True for row in q750["source_reopens"])
    assert all(role["status"] == "PASS" for role in q750["entity_role_reopens"])
    assert q750["execution_replay"]["operation"] == "subtract"
    assert q750["execution_replay"]["stage_order"] == [
        "stage_1_net_income",
        "stage_2_net_income",
    ]
    assert q750["execution_replay"]["numeric_literal_exposed"] is False


def test_rejects_chatgpt_authority_that_is_not_human_equivalent(tmp_path: Path) -> None:
    config = json.loads(V10_CONFIG.read_text(encoding="utf-8"))
    config["decision_provenance"]["verification_authority"] = "machine_provisional"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(CampaignChatGPTAuditError, match="explicit ChatGPT authority grant"):
        build_campaign_chatgpt_audit(
            handoff_manifest=V10_HANDOFF,
            config_path=config_path,
            output_dir=tmp_path / "audit",
        )


def test_builds_v12_audit_with_relational_parent_roles_and_no_numeric_exposure(
    tmp_path: Path,
) -> None:
    result = build_campaign_chatgpt_audit(
        handoff_manifest=V12_HANDOFF,
        config_path=V12_CONFIG,
        output_dir=tmp_path / "v12-audit",
    )
    assert result["status"] == "campaign_approved_not_released"
    assert result["candidate_review_outcome_counts"] == {"approved_candidate": 26}
    assert result["blocking_issue_count"] == 0
    assert result["numeric_value_exposure_count"] == 0
    assert result["outputs"]["decision"]["sha256"] == json.loads(
        (V12_PINNED / "grounded_campaign_chatgpt_audit_v1.manifest.json").read_text(
            encoding="utf-8"
        )
    )["outputs"]["decision"]["sha256"]
    decision = json.loads(
        (tmp_path / "v12-audit/grounded_campaign_chatgpt_decision_v1.jsonl").read_text(
            encoding="utf-8"
        )
    )
    q181 = next(review for review in decision["candidate_reviews"] if review["question_id"] == 181)
    assert q181["reason_code"] == "EXACT_RELATIONAL_PARENT_ROLE_PROVEN"
    assert q181["entity_role_reopen"]["status"] == "PASS"
    assert q181["entity_role_reopen"]["verified_source_anchors"][0]["line_number"] == 760
    assert decision["release_authorized"] is False
