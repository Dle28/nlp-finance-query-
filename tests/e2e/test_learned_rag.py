from __future__ import annotations

import json

from finance_query.e2e.core.learned_rag import (
    rerank_table_candidates,
    rerank_review_items_candidates,
    resolve_finetuned_artifacts,
)


class FakeScorer:
    def predict(self, sentences, **kwargs):
        assert kwargs["show_progress_bar"] is False
        return [0.1 if "Doanh thu" in passage else 0.9 for _, passage in sentences]


def test_reranker_reorders_but_never_authorizes() -> None:
    candidates = [
        {"internal_table_uid": "a", "rank": 1, "document_id": "AAA_2024"},
        {"internal_table_uid": "b", "rank": 2, "document_id": "AAA_2024"},
    ]
    assets = {
        "a": {"headers": ["Doanh thu"], "row_paths": ["Doanh thu thuần"]},
        "b": {"headers": ["Lợi nhuận"], "row_paths": ["Lợi nhuận sau thuế"]},
    }
    result = rerank_table_candidates(
        query="Lợi nhuận sau thuế",
        candidates=candidates,
        assets_by_uid=assets,
        limit=2,
        scorer=FakeScorer(),
    )
    assert [row["internal_table_uid"] for row in result] == ["b", "a"]
    assert [row["pre_rerank_rank"] for row in result] == [2, 1]
    assert all(row["navigation_metadata_only"] is True for row in result)
    assert all(row["may_authorize_answer"] is False for row in result)


def test_reranker_accepts_structured_v2_projection_without_numeric_cells() -> None:
    asset = {
        "document_id": "VNM_financial_statements_2024_consolidated",
        "table_section": "Kết quả kinh doanh",
        "table_purpose": "So sánh giữa các kỳ",
        "column_labels": ["Nhãn dòng", "2024", "2023"],
        "rows": [["Doanh thu thuần", "123.456", "100.000"]],
    }

    class StructuredScorer:
        def predict(self, sentences, **kwargs):
            assert "Doanh thu thuần" in sentences[0][1]
            assert "123.456" not in sentences[0][1]
            return [0.7 for _ in sentences]

    result = rerank_table_candidates(
        query="Doanh thu thuần VNM 2024",
        candidates=[{"internal_table_uid": "structured", "rank": 1}],
        assets_by_uid={"structured": asset},
        limit=1,
        scorer=StructuredScorer(),
    )
    assert result[0]["navigation_metadata_only"] is True


def test_resolve_finetuned_artifacts_marks_smoke_checkpoint(tmp_path) -> None:
    root = tmp_path / "model"
    (root / "retriever_finetuned").mkdir(parents=True)
    (root / "reranker_finetuned").mkdir(parents=True)
    (root / "generator_lora").mkdir(parents=True)
    (root / "metrics.json").write_text(
        json.dumps(
            {
                "fast_dev_run": True,
                "retriever_promotion_allowed": False,
                "reranker_promotion_allowed": True,
                "generator_promotion_allowed": False,
            }
        ),
        encoding="utf-8",
    )
    manifest = resolve_finetuned_artifacts(root)
    assert manifest.fast_dev_run is True
    assert manifest.promotion_allowed is False
    assert manifest.reranker_promotion_allowed is True
    assert manifest.generator_promotion_allowed is False
    assert manifest.reranker_path == root / "reranker_finetuned"


def test_batch_reranker_preserves_item_boundaries() -> None:
    seen = {}

    class BatchScorer:
        def predict(self, sentences, **kwargs):
            seen["batch_size"] = kwargs["batch_size"]
            return [float(index) for index, _ in enumerate(sentences)]

    items = [
        {"id": 10, "question": "q10", "candidates": [{"internal_table_uid": "a"}, {"internal_table_uid": "b"}]},
        {"id": 11, "question": "q11", "candidates": [{"internal_table_uid": "c"}]},
    ]
    assets = {uid: {"column_labels": [uid], "rows": [[uid]]} for uid in ("a", "b", "c")}
    result = rerank_review_items_candidates(
        items=items,
        assets_by_uid=assets,
        scorer=BatchScorer(),
        limit=2,
        batch_size=7,
    )
    assert seen["batch_size"] == 7
    assert [row["internal_table_uid"] for row in result[10][1]] == ["b", "a"]
    assert [row["internal_table_uid"] for row in result[11][1]] == ["c"]
