from __future__ import annotations

import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "evaluate_hierarchy_retrieval_ablation",
    Path(__file__).parents[1] / "scripts" / "evaluate_hierarchy_retrieval_ablation.py",
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def test_hierarchy_ablation_reranks_only_existing_pool_and_improves_metric() -> None:
    assets = {
        "wrong": {
            "ticker": "AAA",
            "report_year": 2024,
            "scope": "separate",
            "document_id": "aaa",
            "headers": ["Nhân viên"],
            "rows": [["Số lượng nhân viên", "10"]],
        },
        "right": {
            "ticker": "AAA",
            "report_year": 2024,
            "scope": "separate",
            "document_id": "aaa",
            "headers": ["Tài sản", "2024"],
            "rows": [["Tài sản ngắn hạn", "100"]],
        },
    }
    question = "Tài sản ngắn hạn năm 2024 là bao nhiêu?"
    reranked, _scores = module.rerank_pool(
        question,
        ["wrong", "right"],
        assets,
        rrf_k=60,
    )
    assert set(reranked) == {"wrong", "right"}
    assert reranked[0] == "right"
    rows = [{"curriculum_id": "q1", "positive_table_uids": ["right"]}]
    baseline = module.summarise(rows, {"q1": ["wrong", "right"]}, assets, ks=[1, 2])
    hierarchy = module.summarise(rows, {"q1": reranked}, assets, ks=[1, 2])
    assert hierarchy["recall_at_k"]["1"] > baseline["recall_at_k"]["1"]
    assert hierarchy["ndcg_at_k"]["2"] > baseline["ndcg_at_k"]["2"]


def test_explicit_context_pool_removes_wrong_year_without_backfill() -> None:
    assets = {
        "wrong-year": {"ticker": "AAA", "report_year": 2023, "scope": "separate"},
        "right": {"ticker": "AAA", "report_year": 2024, "scope": "separate"},
        "wrong-scope": {"ticker": "AAA", "report_year": 2024, "scope": "consolidated"},
    }
    row = {"planner_target": {"ticker": "AAA", "report_year": 2024, "scope": "separate"}}
    assert module.explicit_context_pool(
        row,
        ["wrong-year", "right", "wrong-scope"],
        assets,
    ) == ["right"]


def test_summarise_treats_empty_filtered_pool_as_no_candidate() -> None:
    rows = [{"curriculum_id": "q1", "positive_table_uids": ["positive"]}]
    assets = {
        "positive": {
            "ticker": "AAA",
            "report_year": 2024,
            "scope": "consolidated",
            "document_id": "doc-1",
        }
    }

    result = module.summarise(rows, {"q1": []}, assets, ks=(1, 10))

    assert result["mrr"] == 0.0
    assert result["recall_at_k"] == {"1": 0.0, "10": 0.0}
    assert result["top1_error_counts"] == {"no_candidate": 1}
