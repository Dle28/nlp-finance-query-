from __future__ import annotations

import json

from finance_query.pipeline import ViFinQARetrievalPipeline
from finance_query.schemas import OperandSpec, QuestionPlan, RetrievedTable


def test_legacy_probe_never_returns_an_unconverted_final_answer() -> None:
    plan = QuestionPlan(
        question_id=1,
        original_question="Doanh thu",
        family="direct_lookup",
        family_confidence=1.0,
        requested_unit="billion_vnd",
        operands=[OperandSpec(operand_id="x0", metric="Doanh thu", period=2023)],
    )
    asset = {
        "document_id": "doc",
        "report_year": 2023,
        "rows_json": json.dumps([["Chỉ tiêu", "Năm 2023"], ["Doanh thu", "1000"]]),
        "unit_hint": "unsupported_unit",
    }
    pipeline = object.__new__(ViFinQARetrievalPipeline)
    pipeline.planner_mode = "test"
    pipeline.planner_warning = None
    pipeline.planner = type("Planner", (), {"plan": lambda self, question, question_id: plan})()
    pipeline.retriever = type(
        "Retriever",
        (),
        {
            "retrieve": lambda self, question, selected_plan: [
                RetrievedTable(
                    internal_table_uid="u1",
                    document_id="doc",
                    ticker="",
                    report_year=2023,
                    scope="unknown",
                )
            ]
        },
    )()
    pipeline.store = type("Store", (), {"get_assets": lambda self, uids: {"u1": asset}})()

    result = pipeline.answer_direct("Doanh thu", 1)

    assert result["status"] == "legacy_candidate_probe"
    assert result["legacy_score_threshold_met"] is True
    assert result["answer"] is None
    assert result["answer_unit"] is None
    assert result["submission_eligible"] is False
    assert "Unsupported unit conversion" in result["top_candidate"]["warnings"][0]
