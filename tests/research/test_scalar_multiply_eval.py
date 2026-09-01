from __future__ import annotations

from finance_query.research.scalar_multiply_eval import (
    evaluate_scalar_multiply_full_reference,
    evaluate_scalar_multiply_stage,
    scalar_policy_eligible,
)


def test_scalar_policy_requires_every_multiply_step_to_have_allowed_scalar() -> None:
    assert scalar_policy_eligible("divide(1, 2), multiply(#0, const_100)", frozenset({"named_constant"}))
    assert not scalar_policy_eligible("multiply(2, 3)", frozenset({"named_constant", "percent_literal"}))
    assert not scalar_policy_eligible(
        "multiply(2, const_100), multiply(#0, 3)",
        frozenset({"named_constant", "percent_literal"}),
    )
    assert scalar_policy_eligible("multiply(100, 7.5%)", frozenset({"percent_literal"}))


def test_stage_evaluation_reports_ablation_and_no_false_confidence() -> None:
    item = {
        "id": "AAA/2020/page_1.pdf-1",
        "table": [],
        "qa": {"question": "what percentage?", "program": "divide(1, 2), multiply(#0, const_100)", "exe_ans": 50},
    }
    from finance_query.research.scalar_multiply_experiment import _record

    seed = "seed"
    record = _record(item, "dev", seed)
    assignment = {
        "benchmark_stage": "development",
        "record_sha256": record["record_sha256"],
        "source_record_tracking_hash": "tracking",
        "metric_family": record["metric_family"],
        "composition_signature": record["composition_signature"],
        "scalar_kinds": record["scalar_kinds"],
        "source_contract": {"research_only": True},
    }
    rows, report = evaluate_scalar_multiply_stage(
        assignments=[assignment],
        finqa_items=[item],
        stage="development",
        split="dev",
        seed=seed,
    )
    assert rows[0]["candidate_outcome"] == "IMPROVED"
    assert report["arms"]["A_current_locked_kernel_contract"]["eligible_count"] == 0
    assert report["arms"]["B_A_plus_named_constant_or_percent_scalar"]["eligible_count"] == 1
    assert report["arms"]["B_A_plus_named_constant_or_percent_scalar"]["false_confident_count"] == 0
    assert report["question_or_program_materialized"] is False


def test_full_reference_evaluation_aggregates_all_source_splits() -> None:
    def item(company: str) -> dict:
        return {
            "id": f"{company}/2020/page_1.pdf-1",
            "table": [],
            "qa": {
                "question": "what percentage?",
                "program": "divide(1, 2), multiply(#0, const_100)",
                "exe_ans": 50,
            },
        }

    rows, report = evaluate_scalar_multiply_full_reference(
        finqa_items_by_split={"train": [item("AAA")], "dev": [item("BBB")], "test": [item("CCC")]},
        seed="seed",
    )
    assert len(rows) == 3
    assert report["source_split_counts"] == {"train": 1, "dev": 1, "test": 1}
    assert report["candidate_outcome_counts"] == {"IMPROVED": 3}
    assert report["arms"]["B_A_plus_named_constant_or_percent_scalar"]["false_confident_count"] == 0
