from __future__ import annotations

from finance_query.e2e.core.candidate_validity import (
    FEATURE_NAMES,
    CandidateValidityModel,
    candidate_feature_map,
    candidate_limit_for_item,
    load_candidate_validity_model,
    rank_candidate_pool,
    save_candidate_validity_model,
)


def _table(uid: str, ticker: str) -> dict[str, object]:
    return {
        "internal_table_uid": uid,
        "document_id": f"{ticker}_financial_statements_2022_separate",
        "source_provenance": {"source_path": f"/{ticker}.txt", "char_start": 1},
        "rows": [["Nhãn", "Năm 2022"], ["Doanh thu", "123"]],
        "column_labels": ["Nhãn", "Năm 2022"],
    }


def _candidate(uid: str, ticker: str, rank: int) -> dict[str, object]:
    return {
        "internal_table_uid": uid,
        "document_id": f"{ticker}_financial_statements_2022_separate",
        "ticker": ticker,
        "report_year": 2022,
        "rank": rank,
        "lexical_rank": rank,
        "metadata_score": 1.0,
        "review_score": 0.4,
        "ticker_match": True,
        "scope_match": True,
        "year_match": True,
        "evidence_window": [{"index": 1, "row": ["Doanh thu", "123"]}],
        "evidence_features": {
            "row_score": 0.8,
            "metric_overlap": 0.8,
            "question_overlap": 0.4,
            "numeric": True,
        },
    }


def test_native_model_fit_is_deterministic_and_round_trips(tmp_path) -> None:
    item = {
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["AAA"],
            "years": [2022],
            "operands": [{"operand_id": "x0", "ticker": "AAA", "period": 2022}],
        }
    }
    candidate = _candidate("u1", "AAA", 1)
    features = candidate_feature_map(item, candidate, _table("u1", "AAA"))
    positive = CandidateValidityModel.fit(
        [features, {**features, "rank_reciprocal": 0.01, "row_score": 0.1}],
        [1, 0],
        feature_names=FEATURE_NAMES,
        epochs=25,
    )
    repeat = CandidateValidityModel.fit(
        [features, {**features, "rank_reciprocal": 0.01, "row_score": 0.1}],
        [1, 0],
        feature_names=FEATURE_NAMES,
        epochs=25,
    )
    assert positive.to_dict() == repeat.to_dict()
    path = tmp_path / "candidate_validity.json"
    save_candidate_validity_model(path, positive, training={"label_source": "explicit"})
    loaded = load_candidate_validity_model(path)
    assert loaded.kind == "native_logistic"
    assert loaded.predict_probability(features) == positive.predict_probability(features)


def test_direct_pool_keeps_top_ten_and_emits_vectors() -> None:
    candidates = [_candidate(f"u{i}", "AAA", i) for i in range(1, 22)]
    item = {
        "question": "Doanh thu AAA năm 2022 là bao nhiêu?",
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["AAA"],
            "years": [2022],
            "operands": [{"operand_id": "x0", "ticker": "AAA", "period": 2022}],
        },
        "candidates": candidates,
    }
    tables = {f"u{i}": _table(f"u{i}", "AAA") for i in range(1, 22)}
    updated, summary = rank_candidate_pool(item, tables_by_uid=tables)
    assert candidate_limit_for_item(item) == 10
    assert len(updated["candidates"]) == 10
    assert len(updated["candidates"][0]["validity_vector"]) == len(FEATURE_NAMES)
    assert summary["plan_shape"] == "single_cell_lookup"


def test_complex_pool_retains_top_k_for_each_entity_group() -> None:
    candidates = []
    tables = {}
    for ticker in ("AAA", "BBB"):
        for rank in range(1, 23):
            uid = f"{ticker}-{rank}"
            candidates.append(_candidate(uid, ticker, rank))
            tables[uid] = _table(uid, ticker)
    item = {
        "question": "Giá trị trung bình của AAA và BBB năm 2022?",
        "question_plan": {
            "family": "multi_entity_or_period_aggregation",
            "tickers": ["AAA", "BBB"],
            "years": [2022],
            "operands": [],
            "operation_ast": {"op": "mean", "args": ["values"]},
        },
        "candidates": candidates,
    }
    updated, summary = rank_candidate_pool(item, tables_by_uid=tables)
    assert candidate_limit_for_item(item) == 20
    assert len(updated["candidates"]) == 40
    assert {candidate["ticker"] for candidate in updated["candidates"]} == {"AAA", "BBB"}
    assert summary["plan_status"] == "CANDIDATE_GROUPS_COVERED"
    assert summary["coverage"] == 1.0


def test_plan_required_is_reported_without_claiming_resolution() -> None:
    item = {
        "question": "Một câu hỏi phức tạp",
        "question_plan": {
            "family": "conditional_analytical",
            "tickers": ["AAA"],
            "years": [2022],
            "operands": [],
            "operation_ast": {"op": "plan_required", "args": []},
        },
        "candidates": [_candidate("u1", "AAA", 1)],
    }
    updated, summary = rank_candidate_pool(item, tables_by_uid={"u1": _table("u1", "AAA")})
    assert updated["candidate_plan_summary"]["plan_status"] == "DECOMPOSITION_REQUIRED"
    assert summary["plan_shape"] == "decomposition_required"
