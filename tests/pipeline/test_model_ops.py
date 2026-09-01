from __future__ import annotations

import pytest

from finance_query.pipeline.model_ops import (
    PROMOTION_APPROVE,
    PROMOTION_INELIGIBLE,
    PROMOTION_REJECT,
    TRAINING_CANDIDATE_PROTOCOL,
    admit_feedback_records,
    build_model_bundle_manifest,
    decide_promotion,
    validate_held_out_metrics,
)


SHA_CODE = "a" * 64
SHA_MODEL = "b" * 64
SHA_INDEX = "c" * 64
SHA_PROMPT = "d" * 64
SHA_INPUT = "e" * 64
SOURCE_SHA = "f" * 64


def _feedback_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 1,
        "protocol": "vifinqa_blocked_question_feedback_v1",
        "feedback_id": "feedback-1",
        "question_id": 7,
        "packet_id": "packet-7",
        "model_id": "test/critic",
        "model_status": "VALID_FEEDBACK",
        "feedback": {
            "decision": "FEEDBACK_ONLY",
            "failure_class": "CONTEXT_INCOMPLETE",
            "reason_codes": ["BLOCKED_BY_SEMANTIC_BINDING"],
            "observations": ["The period is not independently bound."],
            "missing_context_fields": ["period"],
            "recommended_actions": [
                {
                    "action": "HYDRATE_CONTEXT_FIELD",
                    "target": "context.period",
                    "rationale": "Bind the exact period before the next proposal.",
                }
            ],
            "confidence": "MEDIUM",
            "source_ref_sha256": [SOURCE_SHA],
        },
    }
    record.update(overrides)
    return record


def _source_receipt() -> dict[str, object]:
    return {
        "verified": True,
        "verification_id": "receipt-7",
        "source_ref_sha256": [SOURCE_SHA],
        "verified_at": "2026-08-30T12:00:00Z",
    }


def _human_receipt() -> dict[str, object]:
    return {
        "verified": True,
        "reviewer_id": "reviewer-1",
        "reviewed_at": "2026-08-30T12:00:00Z",
        "decision": "ACCEPT",
    }


def _metrics(**overrides: object) -> dict[str, object]:
    report: dict[str, object] = {
        "model_kind": "reranker",
        "full_run": True,
        "baseline": {"Recall@5": 0.70, "MRR@5": 0.40},
        "candidate": {"Recall@5": 0.71, "MRR@5": 0.41},
        "hashes": {
            "code": SHA_CODE,
            "model": SHA_MODEL,
            "index": SHA_INDEX,
            "prompt": SHA_PROMPT,
            "input": SHA_INPUT,
        },
    }
    report.update(overrides)
    return report


def _admitted_candidates() -> object:
    return admit_feedback_records(
        [_feedback_record(source_verification=_source_receipt())]
    )


def test_model_feedback_alone_is_not_admitted() -> None:
    result = admit_feedback_records([_feedback_record()])

    assert result.accepted_count == 0
    assert result.rejected_count == 1
    assert "SOURCE_OR_HUMAN_VERIFICATION_REQUIRED" in result.rejected[0]["reason_codes"]


def test_source_verified_feedback_becomes_non_authorizing_training_candidate() -> None:
    result = admit_feedback_records(
        [_feedback_record(source_verification=_source_receipt())]
    )

    assert result.accepted_count == 1
    candidate = result.candidates[0]
    assert candidate["protocol"] == TRAINING_CANDIDATE_PROTOCOL
    assert candidate["training_eligible"] is True
    assert candidate["source_verified"] is True
    assert candidate["human_verified"] is False
    assert candidate["answer_authorized"] is False
    assert candidate["evidence_authorized"] is False
    assert candidate["promotion_allowed"] is False
    assert "answer_decimal" not in candidate


def test_human_verified_feedback_can_be_admitted_without_model_authority() -> None:
    record = _feedback_record(human_verification=_human_receipt())
    result = admit_feedback_records([record])

    assert result.accepted_count == 1
    candidate = result.candidates[0]
    assert candidate["verification_basis"] == "HUMAN_VERIFIED"
    assert candidate["human_verified"] is True
    assert candidate["source_verified"] is False


def test_model_feedback_with_answer_field_is_rejected_even_with_receipt() -> None:
    record = _feedback_record(
        feedback={
            **_feedback_record()["feedback"],  # type: ignore[arg-type]
            "answer_decimal": "12",
        },
        source_verification=_source_receipt(),
    )

    result = admit_feedback_records([record])

    assert result.accepted_count == 0
    assert "MODEL_OUTPUT_AUTHORITY_FIELD" in result.rejected[0]["reason_codes"]


def test_held_out_metrics_accepts_non_decrease_and_complete_hashes() -> None:
    result = validate_held_out_metrics(_metrics())

    assert result.valid is True
    assert result.passed is True
    assert result.eligible is True
    assert bool(result) is True
    assert result.reason_codes == ()


def test_held_out_metric_regression_is_rejected_not_approved() -> None:
    report = _metrics(
        candidate={"Recall@5": 0.69, "MRR@5": 0.41},
    )
    result = validate_held_out_metrics(report)

    assert result.valid is True
    assert result.passed is False
    assert "METRIC_REGRESSION_RECALL_AT_5" in result.reason_codes
    decision = decide_promotion(result, training_candidates=_admitted_candidates())
    assert decision.decision == PROMOTION_REJECT
    assert decision.approved is False


@pytest.mark.parametrize(
    "change, expected_reason",
    [
        ({"full_run": False}, "FULL_RUN_REQUIRED"),
        ({"candidate": {"Recall@5": 0.71}}, "CANDIDATE_MRR_AT_5_MISSING_OR_INVALID"),
        ({"hashes": {"code": SHA_CODE}}, "MODEL_HASH_MISSING_OR_INVALID"),
    ],
)
def test_missing_full_run_metric_or_hash_is_ineligible(
    change: dict[str, object], expected_reason: str
) -> None:
    result = validate_held_out_metrics(_metrics(**change))

    assert result.passed is False
    assert expected_reason in result.reason_codes
    decision = decide_promotion(result, training_candidates=_admitted_candidates())
    assert decision.decision == PROMOTION_INELIGIBLE


def test_promotion_requires_an_admitted_training_candidate() -> None:
    decision = decide_promotion(validate_held_out_metrics(_metrics()), training_candidates=[])

    assert decision.decision == PROMOTION_INELIGIBLE
    assert "NO_ELIGIBLE_TRAINING_CANDIDATES" in decision.reason_codes


def test_promotion_approves_only_after_both_gates_pass() -> None:
    decision = decide_promotion(
        validate_held_out_metrics(_metrics()),
        training_candidates=_admitted_candidates(),
    )

    assert decision.decision == PROMOTION_APPROVE
    assert decision.metrics_passed is True
    assert decision.training_candidate_count == 1
    payload = decision.to_dict()
    assert payload["promotion_allowed"] is True
    assert payload["answer_authorized"] is False
    assert payload["evidence_authorized"] is False
    assert payload["submission_eligible"] is False
    assert payload["release_authorized"] is False


def test_model_bundle_requires_all_hashes_and_stays_candidate_without_approval() -> None:
    with pytest.raises(ValueError, match="hashes are incomplete"):
        build_model_bundle_manifest(
            model_name="test-reranker",
            model_version="candidate-1",
            model_kind="reranker",
            hashes={"code": SHA_CODE, "model": SHA_MODEL},
        )

    manifest = build_model_bundle_manifest(
        model_name="test-reranker",
        model_version="candidate-1",
        model_kind="reranker",
        hashes={
            "code": SHA_CODE,
            "model": SHA_MODEL,
            "index": SHA_INDEX,
            "prompt": SHA_PROMPT,
            "input": SHA_INPUT,
        },
    )
    assert manifest["lifecycle_status"] == "CANDIDATE"
    assert manifest["candidate_until_gate"] is True
    assert manifest["authority_flags"]["promotion_allowed"] is False
    assert manifest["authority_flags"]["answer_authorized"] is False
    assert manifest["authority_flags"]["release_authorized"] is False
    assert manifest["hashes"]["code_sha256"] == SHA_CODE


def test_approved_bundle_is_navigation_only() -> None:
    metrics = validate_held_out_metrics(_metrics())
    decision = decide_promotion(metrics, training_candidates=_admitted_candidates())
    manifest = build_model_bundle_manifest(
        model_name="test-reranker",
        model_version="candidate-1",
        model_kind="reranker",
        hashes={
            "code": SHA_CODE,
            "model": SHA_MODEL,
            "index": SHA_INDEX,
            "prompt": SHA_PROMPT,
            "input": SHA_INPUT,
        },
        metrics=metrics,
        promotion_decision=decision,
    )

    assert manifest["lifecycle_status"] == "PROMOTED_NAVIGATION_ONLY"
    assert manifest["candidate_until_gate"] is False
    assert manifest["authority_flags"]["promotion_allowed"] is True
    assert manifest["authority_flags"]["answer_authorized"] is False
    assert manifest["authority_flags"]["evidence_authorized"] is False
    assert manifest["authority_flags"]["submission_eligible"] is False
    assert manifest["authority_flags"]["release_authorized"] is False
    assert len(manifest["manifest_sha256"]) == 64


def test_approved_bundle_requires_metrics_and_matching_hashes() -> None:
    decision = decide_promotion(
        validate_held_out_metrics(_metrics()),
        training_candidates=_admitted_candidates(),
    )
    hashes = {
        "code": SHA_CODE,
        "model": SHA_MODEL,
        "index": SHA_INDEX,
        "prompt": SHA_PROMPT,
        "input": SHA_INPUT,
    }
    with pytest.raises(ValueError, match="requires a passed held-out metrics gate"):
        build_model_bundle_manifest(
            model_name="test-reranker",
            model_version="candidate-1",
            model_kind="reranker",
            hashes=hashes,
            promotion_decision=decision,
        )
    with pytest.raises(ValueError, match="do not match"):
        build_model_bundle_manifest(
            model_name="test-reranker",
            model_version="candidate-1",
            model_kind="reranker",
            hashes={**hashes, "model": SHA_CODE},
            metrics=validate_held_out_metrics(_metrics()),
            promotion_decision=decision,
        )
