from __future__ import annotations

from finance_query.research.temporal_header_eval import evaluate_temporal_header_stage
from finance_query.research.temporal_header_experiment import _record


def test_temporal_header_stage_reports_improvement_and_ablation() -> None:
    item = {
        "id": "AAA/2020/page_1.pdf-1",
        "table": [["Years Ended December 31,"], ["2020", "2019"]],
        "qa": {"question": "What was revenue in 2020?", "program": "subtract(2, 1)"},
    }
    seed = "seed"
    record = _record(item, "dev", seed)
    assignment = {
        "benchmark_stage": "development",
        "record_sha256": record["record_sha256"],
        "source_record_tracking_hash": "tracking",
        "metric_family": record["metric_family"],
        "composition_signature": record["composition_signature"],
        "gold_temporal_type": "duration",
        "source_contract": {"research_only": True},
    }
    rows, report = evaluate_temporal_header_stage(
        assignments=[assignment],
        finqa_items=[item],
        stage="development",
        split="dev",
        seed=seed,
    )
    assert rows[0]["candidate_outcome"] == "IMPROVED"
    assert report["arms"]["A_question_only_temporal_parser"]["predicted_count"] == 0
    assert report["arms"]["B_A_plus_source_header_backoff"]["correct_count"] == 1
    assert report["arms"]["C_B_without_duration_header_anchor"]["abstain_count"] == 1
    assert report["arms"]["D_B_without_instant_header_anchor"]["correct_count"] == 1
    assert report["question_or_header_materialized"] is False
