from __future__ import annotations

from finance_query.research.exact_row_metric_linking import (
    _record,
    evaluate_exact_row_full_reference,
    evaluate_exact_row_stage,
    select_exact_row,
)


def test_exact_row_linker_requires_score_and_unique_margin() -> None:
    table = [["", "2023"], ["net revenue", "10"], ["other revenue", "2"]]
    prediction, telemetry = select_exact_row("What was net revenue in 2023?", table)
    assert prediction == 1
    assert telemetry["best_score"] >= 0.2
    assert telemetry["margin"] >= 0.1
    prediction, _ = select_exact_row("What was revenue in 2023?", table)
    assert prediction is None


def test_stage_evaluation_uses_gold_row_only_for_scoring() -> None:
    item = {
        "id": "AAA/2020/page_1.pdf-1",
        "table": [["", "2020"], ["net revenue", "10"], ["other expense", "2"]],
        "qa": {
            "question": "What was net revenue in 2020?",
            "program": "subtract(10, 2)",
            "gold_inds": {"table_1": "gold"},
        },
    }
    seed = "seed"
    record = _record(item, "dev", seed)
    assignment = {
        "benchmark_stage": "development",
        "record_sha256": record["record_sha256"],
        "source_record_tracking_hash": "tracking",
        "metric_family": record["metric_family"],
        "composition_signature": record["composition_signature"],
        "gold_row_tracking_hash": __import__("hashlib").sha256(b"1").hexdigest(),
        "source_contract": {"research_only": True},
    }
    rows, report = evaluate_exact_row_stage(
        assignments=[assignment], finqa_items=[item], stage="development", split="dev", seed=seed
    )
    assert rows[0]["candidate_outcome"] == "IMPROVED"
    assert rows[0]["question_or_row_materialized"] is False
    candidate = report["arms"]["B_A_plus_normalized_unique_margin_linker"]
    assert candidate["correct_count"] == 1
    assert candidate["false_confident_count"] == 0


def test_full_reference_aggregates_splits() -> None:
    def item(company: str) -> dict:
        return {
            "id": f"{company}/2020/page.pdf-1",
            "table": [["", "2020"], ["net revenue", "10"]],
            "qa": {"question": "What was net revenue in 2020?", "program": "subtract(10, 2)", "gold_inds": {"table_1": "gold"}},
        }
    rows, report = evaluate_exact_row_full_reference(
        finqa_items_by_split={"train": [item("AAA")], "dev": [item("BBB")], "test": [item("CCC")]}, seed="seed"
    )
    assert len(rows) == 3
    assert report["source_split_counts"] == {"train": 1, "dev": 1, "test": 1}
    assert report["arms"]["B_A_plus_normalized_unique_margin_linker"]["correct_count"] == 3
