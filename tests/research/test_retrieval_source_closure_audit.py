from __future__ import annotations

from finance_query.research.retrieval_source_closure_audit import (
    _contains_forbidden,
    _question_rows,
    _rank_metrics,
)


def test_rank_metrics_distinguishes_any_and_full_operand_closure() -> None:
    targets = {"1": {"u1", "u2"}, "2": {"u3"}, "3": set()}
    index = {"1": {"u1": 1, "u2": 11}, "2": {"u3": 20}}

    metrics = _rank_metrics(["1", "2", "3"], targets, index)

    assert metrics["question_count"] == 3
    assert metrics["questions_with_source_uid"] == 2
    assert metrics["target_uid_count"] == 3
    assert metrics["found_uid_count_at_10"] == 1
    assert metrics["questions_any_uid_at_10"] == 1
    assert metrics["questions_all_uids_at_10"] == 0
    assert metrics["found_uid_count_at_20"] == 3
    assert metrics["questions_any_uid_at_20"] == 2
    assert metrics["questions_all_uids_at_20"] == 2


def test_question_rows_reports_tail_gain_without_emitting_values() -> None:
    rows = _question_rows(
        question_ids=["1"],
        tiers={"1": "semantic_cell_heuristic"},
        target_uids={"1": {"u1", "u2"}},
        evidence_row_counts={"1": 2},
        evidence_uid_row_counts={"1": 2},
        baseline={"1": {"u1": 1}},
        candidate={"1": {"u1": 1, "u2": 11}},
    )

    assert rows[0]["candidate_gained_uid_count_at_20"] == 1
    assert rows[0]["candidate_all_uids_at_20"] is True
    assert rows[0]["candidate_contract"]["numeric_values_emitted"] is False
    assert not _contains_forbidden(rows[0])
    assert _contains_forbidden({"answer": "must reject"})
