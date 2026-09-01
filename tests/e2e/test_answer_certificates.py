from __future__ import annotations

from finance_query.e2e.core.answer_certificates import compile_abstention_certificate


def test_abstain_without_a_surviving_candidate_has_null_answer() -> None:
    certificate = compile_abstention_certificate(
        question_id=123,
        reason_codes=["NO_EXECUTABLE_STAGE"],
        execution={"status": "route_incomplete"},
    )

    assert certificate["status"] == "ABSTAIN"
    assert certificate["answer_status"] == "ABSTAIN"
    assert certificate["answer_available"] is False
    assert certificate["answer"] is None
    assert certificate["execution_receipt"]["answer_decimal"] is None
    assert certificate["serving_eligible"] is False
    assert certificate["abstain_reason_codes"] == ["NO_EXECUTABLE_STAGE"]
    assert certificate["next_gate"] == "repair_or_expand_candidate_set"


def test_abstain_serves_the_best_surviving_candidate_when_available() -> None:
    certificate = compile_abstention_certificate(
        question_id=123,
        reason_codes=["NO_EXECUTABLE_STAGE"],
        execution={"status": "route_incomplete"},
        best_candidate={
            "candidate_id": "q123:primary:best",
            "answer_decimal": "42.75",
            "filter_status": "SURVIVED_FILTER",
            "filter_passed": True,
            "filter_score": 0.91,
            "source": [{"internal_table_uid": "table-1", "row_index": 4, "column_index": 2}],
        },
    )

    assert certificate["status"] == "ABSTAIN"
    assert certificate["answer_status"] == "PREDICTED_CANDIDATE"
    assert certificate["prediction_status"] == "UNCERTAIN_CANDIDATE"
    assert certificate["answer_available"] is True
    assert certificate["answer_authorized"] is False
    assert certificate["answer"] == "42.75"
    assert certificate["serving_eligible"] is True
    assert certificate["candidate_prediction"]["candidate_id"] == "q123:primary:best"
