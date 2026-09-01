from __future__ import annotations

from finance_query.pipeline.submission_flow import _model_lifecycle_snapshot


def test_model_lifecycle_snapshot_keeps_feedback_out_of_training_and_promotion() -> None:
    snapshot = _model_lifecycle_snapshot(
        {
            "model_id": "test/critic",
            "model_evaluated_count": 4,
            "valid_feedback_count": 2,
        }
    )

    assert snapshot["status"] == "FEEDBACK_COLLECTED_NOT_ADMITTED"
    assert snapshot["training_admission"]["accepted_count"] == 0
    assert snapshot["training_admission"]["rejected_count"] == 0
    assert snapshot["held_out_metrics"]["status"] == "REQUIRED_NOT_RUN"
    assert snapshot["promotion"]["decision"] == "INELIGIBLE"
    assert snapshot["promotion"]["promotion_allowed"] is False
    assert snapshot["authority"]["feedback_authorizes_training"] is False


def test_prepared_feedback_is_explicitly_not_run() -> None:
    snapshot = _model_lifecycle_snapshot({"model_id": None})

    assert snapshot["status"] == "PACKETS_PREPARED_NOT_RUN"
    assert snapshot["promotion"]["release_authorized"] is False
