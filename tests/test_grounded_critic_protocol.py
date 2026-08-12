from __future__ import annotations

import pytest

from finance_query.grounded_critic_protocol import validate_critic_response


def _packet() -> dict:
    return {"question_id": 1, "execution_status": "execution_replay_ready", "allowed_packet_evidence_ids": ["u:1:2"]}


def _response() -> dict:
    return {"question_id": 1, "status": "abstain", "stage_reviews": [], "binding_reviews": [], "execution_review": {}, "missing_evidence": [], "conflicts": [], "feedback": {"missing_concepts": [], "suggested_table_types": [], "suggested_retrieval_stage": None, "reason_codes": ["MODEL_NOT_RUN"]}, "packet_evidence_ids_used": []}


def test_valid_closed_world_response_is_machine_provisional() -> None:
    result = validate_critic_response(_packet(), _response())
    assert result["provenance"] == "machine_provisional"
    assert result["source_contract"]["evidence_eligible"] is False


def test_external_source_and_candidate_selection_are_rejected() -> None:
    external = _response(); external["packet_evidence_ids_used"] = ["other:0:0"]
    with pytest.raises(ValueError, match="outside bounded"):
        validate_critic_response(_packet(), external)
    selected = _response(); selected["execution_review"] = {"selected": "u:1:2"}
    with pytest.raises(ValueError, match="selection"):
        validate_critic_response(_packet(), selected)
