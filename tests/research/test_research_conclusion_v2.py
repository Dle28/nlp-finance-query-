from __future__ import annotations

import pytest

from finance_query.research.research_conclusion_v2 import validate_research_conclusion_v2
from finance_query.research.research_conclusion import EXPERIMENT_SECTIONS


def _valid_report() -> dict:
    sections = {key: {} for key in EXPERIMENT_SECTIONS}
    experiments = []
    for index in range(6):
        current = dict(sections)
        current["VERDICT"] = {"retained": index < 3, "decision": "KEEP" if index < 3 else "REJECT"}
        experiments.append({"experiment_id": f"e{index}", "sections": current})
    return {
        "protocol": "vifinqa_antileakage_research_conclusion_v2",
        "experiment_reports": experiments,
        "final_research_questions": [
            {"number": index, "question": "q", "answer": "a"} for index in range(1, 18)
        ],
        "retained_rules": [
            {
                "unseen_improved": 1,
                "unseen_regressed": 0,
                "unseen_false_confident": 0,
                "full_reference_false_confident": 0,
                "selector_uses_question_id": False,
                "manual_per_question_labels": False,
            }
            for _ in range(3)
        ],
        "general_rule_set": [
            {"dimension": dimension, "retained_rule_ids": []}
            for dimension in (
                "question_understanding",
                "financial_meaning",
                "time",
                "operation",
                "evidence_selection",
                "compatibility",
                "abstention",
            )
        ],
        "submission_readiness": {"submission_zip_created": False},
    }


def test_v2_contract_accepts_six_reports_and_three_safe_rules() -> None:
    validate_research_conclusion_v2(_valid_report())


def test_v2_contract_rejects_false_confident_retained_rule() -> None:
    report = _valid_report()
    report["retained_rules"][0]["unseen_false_confident"] = 1
    with pytest.raises(ValueError, match="false confidence"):
        validate_research_conclusion_v2(report)
