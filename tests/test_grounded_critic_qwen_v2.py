from __future__ import annotations

from finance_query.grounded_critic_qwen_v2 import critic_prompt, fallback_abstention


def test_prompt_prohibits_answer_or_cell_selection() -> None:
    prompt = critic_prompt({"question_id": 6, "question_context": {}, "execution_status": "execution_replay_ready", "allowed_packet_evidence_ids": ["u:1:2"], "bounded_source_excerpts": [], "deterministic_execution_trace": []})
    assert "MUST NOT" in prompt
    assert "numeric value" in prompt
    assert "u:1:2" in prompt


def test_model_failure_falls_back_to_non_numeric_abstention() -> None:
    response = fallback_abstention(6, "invalid JSON")
    assert response["status"] == "abstain"
    assert response["packet_evidence_ids_used"] == []
    assert response["feedback"]["reason_codes"] == ["UNSUPPORTED_EVIDENCE"]
