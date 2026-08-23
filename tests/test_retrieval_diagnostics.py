from finance_query.retrieval_diagnostics import build_dynamic_candidate_set, evaluate_retrieval_ladder


def test_candidate_set_expands_when_declared_scope_coverage_is_missing() -> None:
    candidate_set = build_dynamic_candidate_set(
        question_id=1,
        lexical=[
            {
                "internal_table_uid": "table-a",
                "score": 0.9,
                "ticker": "AAA",
                "report_year": 2022,
                "scope": "separate",
            }
        ],
        dense=[],
        metadata=[],
        coverage_requirements={"ticker": ["AAA"], "report_year": [2022], "scope": ["consolidated"]},
    )
    assert candidate_set["status"] == "EXPANSION_REQUIRED"
    assert candidate_set["coverage"]["scope"]["missing"] == ["consolidated"]
    assert candidate_set["next_action"] == "expand_retrieval_before_binding_or_abstain"
    assert candidate_set["training_eligible"] is False


def test_retrieval_ladder_requires_the_complete_operand_set_not_only_the_right_table() -> None:
    candidate_set = build_dynamic_candidate_set(
        question_id=2,
        lexical=[
            {
                "internal_table_uid": "table-a",
                "score": 0.9,
                "document_id": "AAA_2022",
                "page_no": 4,
                "anchor_ids": ["cell-a"],
            }
        ],
        dense=[],
        metadata=[],
    )
    diagnostic = evaluate_retrieval_ladder(
        candidate_set=candidate_set,
        oracle={
            "oracle_id": "oracle-2",
            "document_id": "AAA_2022",
            "page_no": 4,
            "internal_table_uid": "table-a",
            "operand_anchor_sets": [["cell-a"], ["cell-b"]],
        },
    )
    assert diagnostic["stages"]["document"]["hit"] is True
    assert diagnostic["stages"]["page"]["hit"] is True
    assert diagnostic["stages"]["table"]["hit"] is True
    assert diagnostic["stages"]["complete_operand_set"]["hit"] is False
    assert diagnostic["status"] == "RETRIEVAL_GAP"
