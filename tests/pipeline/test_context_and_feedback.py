from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.pipeline.context.compiler import (
    compile_blocked_question_packets,
    compile_feedback_prompt,
)
from finance_query.pipeline.context.prompt_profiles import COMPACT_PROMPT_PROFILE
from finance_query.pipeline.feedback.blocked import (
    FEEDBACK_STATUS_INVALID,
    FEEDBACK_STATUS_VALID,
    run_blocked_feedback,
    validate_feedback_response,
)


TABLE_UID = "a" * 64


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _submission_fixture(root: Path) -> Path:
    root.mkdir(parents=True)
    _write_json(
        root / "submission.json",
        [
            {"id": 1, "question": "Doanh thu của VNM năm 2022 là bao nhiêu?", "answer": 10},
            {"id": 2, "question": "Lợi nhuận của VNM năm 2022 là bao nhiêu?", "answer": 20},
        ],
    )
    verification = {
        "protocol": "vifinqa_proposal_verification_v1",
        "verification_class": "PARTIAL",
        "checks": {
            "source_binding": "PASS",
            "semantic_binding": "UNRESOLVED",
            "temporal_binding": "UNRESOLVED",
        },
        "failures": [],
        "authority": "none",
    }
    _write_jsonl(
        root / "diagnostics.jsonl",
        [
            {
                "id": 1,
                "tier": "semantic_cell_heuristic",
                "answer_status": "PREDICTED",
                "answer_decimal": "10",
                "plan_shape": "single_cell_lookup",
                "plan_status": "CANDIDATE_PLAN_COVERED",
                "plan_groups": [{"group": "operand:x0", "top_candidates": [{"rank": 1}]}],
                "research_route_status": "route_incomplete",
                "research_route_reason_codes": ["WHOLE_QUESTION_OPERATION_UNCOVERED"],
                "verification_class": "PARTIAL",
                "verification": verification,
            },
            {
                "id": 2,
                "tier": "exact_execution_r9",
                "answer_status": "PREDICTED",
                "answer_decimal": "20",
                "verification_class": "VERIFIED",
                "verification": {
                    **verification,
                    "verification_class": "VERIFIED",
                    "authority": "complete_canonical_e2e_certificate",
                },
            },
        ],
    )
    _write_jsonl(
        root / "prediction_audit_ledger_v1.jsonl",
        [
            {
                "protocol": "vifinqa_proposed_answer_plan_v1",
                "question_id": 1,
                "answer_decimal": "10",
                "answer_route": "semantic_cell_heuristic",
                "operation_ast": {"op": "lookup", "args": ["x0"]},
                "claims": {"entities": ["VNM"], "reporting_scope": "consolidated"},
                "pandas_query": "float(df1.loc[0, 'value'])",
                "evidence": [
                    {
                        "internal_table_uid": TABLE_UID,
                        "document_id": "VNM_2022.txt",
                        "row_index": 1,
                        "column_index": 2,
                        "row_label": "Doanh thu thuần",
                        "raw_value": "1000000",
                    }
                ],
                "verification": verification,
            },
            {
                "protocol": "vifinqa_proposed_answer_plan_v1",
                "question_id": 2,
                "answer_decimal": "20",
                "answer_route": "exact_execution_r9",
                "operation_ast": {"op": "lookup", "args": ["x0"]},
                "claims": {"entities": ["VNM"]},
                "evidence": [],
                "verification": {"verification_class": "VERIFIED"},
            },
        ],
    )
    _write_jsonl(
        root / "best_surviving_candidates_v1.jsonl",
        [
            {
                "question_id": 1,
                "candidate_id": "q1:candidate",
                "answer_decimal": "10",
                "filter_status": "SURVIVED_FILTER",
                "source": [{"internal_table_uid": TABLE_UID, "row_label": "Doanh thu thuần"}],
            }
        ],
    )
    _write_json(root / "build_report.json", {"protocol": "build_report_v1", "primary_model": "test"})
    return root


def _valid_model_response() -> dict[str, object]:
    return {
        "decision": "FEEDBACK_ONLY",
        "failure_class": "CONTEXT_INCOMPLETE",
        "reason_codes": ["BLOCKED_BY_SEMANTIC_BINDING"],
        "observations": ["Header or period semantics are not independently bound."],
        "missing_context_fields": ["table_header", "period"],
        "recommended_actions": [
            {
                "action": "HYDRATE_CONTEXT_FIELD",
                "target": "context.hydrate",
                "rationale": "Add the exact header and period labels before proposal inference.",
            }
        ],
        "confidence": "MEDIUM",
        "source_ref_sha256": [TABLE_UID],
    }


def test_context_packet_preserves_vietnamese_and_redacts_numeric_values(tmp_path: Path) -> None:
    submission_dir = _submission_fixture(tmp_path / "submission")
    packets = compile_blocked_question_packets(submission_dir)

    assert [packet["question_id"] for packet in packets] == [1]
    packet = packets[0]
    serialized = json.dumps(packet, ensure_ascii=False)
    assert "Doanh thu của VNM" in serialized
    assert "raw_value" not in serialized
    assert "answer_decimal" not in serialized
    assert packet["numeric_cells"] is None
    assert packet["allowed_source_refs"] == [TABLE_UID]

    prompt = compile_feedback_prompt(packet)
    assert "independent feedback critic" in prompt
    assert "Doanh thu của VNM" in prompt
    assert "numeric answer" in prompt


def test_model_feedback_is_validated_and_remains_non_authorizing(tmp_path: Path) -> None:
    submission_dir = _submission_fixture(tmp_path / "submission")
    output_dir = tmp_path / "feedback"
    seen_prompts: list[str] = []

    def fake_model(prompt: str) -> dict[str, object]:
        seen_prompts.append(prompt)
        return _valid_model_response()

    result = run_blocked_feedback(
        submission_dir=submission_dir,
        output_dir=output_dir,
        model=fake_model,
        model_id="test/feedback-model",
    )

    assert result.packet_count == 1
    assert result.evaluated_count == 1
    assert result.valid_count == 1
    assert len(seen_prompts) == 1
    feedback = [
        json.loads(line)
        for line in (output_dir / "blocked_question_feedback_v1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert feedback[0]["model_status"] == FEEDBACK_STATUS_VALID
    assert feedback[0]["feedback"]["failure_class"] == "CONTEXT_INCOMPLETE"
    assert feedback[0]["answer_authorized"] is False
    assert feedback[0]["training_eligible"] is False
    assert feedback[0]["promotion_allowed"] is False

    improvement = [
        json.loads(line)
        for line in (output_dir / "improvement_feedback_records_v1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert improvement[0]["learning_mode"] == "feedback_candidate_not_weight_update"
    assert improvement[0]["requires_human_verification"] is True
    assert improvement[0]["training_eligible"] is False

    plan = json.loads((output_dir / "improvement_plan_v1.json").read_text(encoding="utf-8"))
    assert plan["experiment_count"] == 1
    assert plan["experiments"][0]["status"] == "PROPOSED_NOT_RUN"
    assert plan["automatic_weight_update"] is False

    summary = json.loads((output_dir / "feedback_summary.json").read_text(encoding="utf-8"))
    assert summary["valid_feedback_count"] == 1
    assert summary["release_authorized"] is False


def test_compact_prompt_preserves_vi_context_and_puts_contract_in_window(tmp_path: Path) -> None:
    submission_dir = _submission_fixture(tmp_path / "submission")
    packet = compile_blocked_question_packets(submission_dir)[0]
    prompt = compile_feedback_prompt(packet, profile_name=COMPACT_PROMPT_PROFILE)

    assert "Doanh thu của VNM" in prompt
    assert "source_scope" in prompt
    assert "reporting_scope" in prompt
    assert "MINIMAL_VALID_OUTPUT_SHAPE" in prompt
    assert "answer_decimal" not in prompt
    assert len(prompt) < 12_000


def test_invalid_model_answer_is_converted_to_abstain_feedback(tmp_path: Path) -> None:
    submission_dir = _submission_fixture(tmp_path / "submission")

    def forbidden_model(_: str) -> dict[str, object]:
        return {"answer_decimal": "123", "decision": "FEEDBACK_ONLY"}

    result = run_blocked_feedback(
        submission_dir=submission_dir,
        output_dir=tmp_path / "invalid-feedback",
        model=forbidden_model,
        model_id="test/forbidden-model",
    )
    assert result.valid_count == 0
    record = json.loads(
        (tmp_path / "invalid-feedback" / "blocked_question_feedback_v1.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert record["model_status"] == FEEDBACK_STATUS_INVALID
    assert record["feedback"]["decision"] == "ABSTAIN"
    assert record["feedback"]["reason_codes"] == ["MODEL_OUTPUT_INVALID"]
    assert record["answer_authorized"] is False


def test_feedback_contract_rejects_authority_fields() -> None:
    packet = {
        "question_id": 1,
        "packet_id": "packet",
        "allowed_source_refs": [TABLE_UID],
    }
    response = _valid_model_response() | {"answer_decimal": "1"}
    with pytest.raises(ValueError, match="authority boundary"):
        validate_feedback_response(response, packet=packet)
