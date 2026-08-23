from finance_query.independent_critic import critique_direct_evidence


def _source() -> tuple[dict, dict]:
    table = {
        "internal_table_uid": "u1",
        "ticker": "AAA",
        "report_year": 2022,
        "scope": "consolidated",
        "unit_hint": "million_vnd",
        "rows": [["Doanh thu", "1.200"]],
        "cell_provenance": [[{"source_row": 0, "source_cell": 0}, {"source_row": 0, "source_cell": 1}]],
    }
    context = {
        "canonical_headers": {"columns": [{"column_index": 1, "source_label": "2022"}]},
        "row_profiles": [{"row_index": 0, "role": "data", "numeric_columns": [1]}],
    }
    return table, context


def _evidence(value: str = "1.200") -> dict:
    return {
        "id": 1,
        "family": "direct_lookup",
        "candidates": [{
            "internal_table_uid": "u1",
            "source_discovery": {
                "policy": "exact_raw_v2_metric_token_sequence_v1",
                "row_index": 0,
                "raw_row_label": "Doanh thu",
                "metric_match": {"matched_metric": "Doanh thu"},
                "value_binding": {
                    "status": "cell_bound",
                    "row_index": 0,
                    "column_index": 1,
                    "column_label": "2022",
                    "value": value,
                    "source_cell": {"source_row": 0, "source_cell": 1},
                },
            },
        }],
    }


def test_independent_critic_revalidates_source_without_reviewer_input() -> None:
    table, context = _source()
    row = critique_direct_evidence(
        _evidence(),
        {"family": "direct_lookup", "tickers": ["AAA"], "years": [2022], "scope": "consolidated"},
        {"u1": table},
        {"u1": context},
    )
    assert row["status"] == "independent_ready"
    assert row["critic_value"] == "1200"
    assert row["reviewer_inputs_used"] == []


def test_independent_critic_blocks_stale_value() -> None:
    table, context = _source()
    row = critique_direct_evidence(
        _evidence("9.999"),
        {"family": "direct_lookup", "tickers": ["AAA"], "years": [2022]},
        {"u1": table},
        {"u1": context},
    )
    assert row["status"] == "independent_blocked"
    assert row["valid_candidates"] == []
    assert row["rejection_counts"]["stored_value_differs_from_v2"] == 1


def test_independent_critic_revalidates_a_row_with_a_standalone_structural_code() -> None:
    table, context = _source()
    table["rows"] = [["06", "Doanh thu", "1.200"]]
    table["cell_provenance"] = [
        [
            {"source_row": 0, "source_cell": 0},
            {"source_row": 0, "source_cell": 1},
            {"source_row": 0, "source_cell": 2},
        ]
    ]
    context["canonical_headers"]["columns"] = [{"column_index": 2, "source_label": "2022"}]
    context["row_profiles"] = [{"row_index": 0, "role": "data", "numeric_columns": [2]}]
    evidence = _evidence()
    evidence["candidates"][0]["source_discovery"]["raw_row_label"] = "Doanh thu"
    evidence["candidates"][0]["source_discovery"]["value_binding"].update(
        {
            "column_index": 2,
            "value": "1.200",
            "source_cell": {"source_row": 0, "source_cell": 2},
        }
    )
    row = critique_direct_evidence(
        evidence,
        {"family": "direct_lookup", "tickers": ["AAA"], "years": [2022], "scope": "consolidated"},
        {"u1": table},
        {"u1": context},
    )
    assert row["status"] == "independent_ready"


def test_independent_critic_allows_only_inline_structural_source_codes() -> None:
    table, context = _source()
    table["rows"] = [["9. Doanh thu", "VI.06", "1.200"]]
    table["cell_provenance"] = [
        [
            {"source_row": 0, "source_cell": 0},
            {"source_row": 0, "source_cell": 1},
            {"source_row": 0, "source_cell": 2},
        ]
    ]
    context["canonical_headers"]["columns"] = [{"column_index": 2, "source_label": "2022"}]
    context["row_profiles"] = [{"row_index": 0, "role": "data", "numeric_columns": [2]}]
    evidence = _evidence()
    evidence["candidates"][0]["source_discovery"]["raw_row_label"] = "9. Doanh thu > VI.06"
    evidence["candidates"][0]["source_discovery"]["value_binding"].update(
        {
            "column_index": 2,
            "value": "1.200",
            "source_cell": {"source_row": 0, "source_cell": 2},
        }
    )
    row = critique_direct_evidence(
        evidence,
        {"family": "direct_lookup", "tickers": ["AAA"], "years": [2022], "scope": "consolidated"},
        {"u1": table},
        {"u1": context},
    )
    assert row["status"] == "independent_ready"
