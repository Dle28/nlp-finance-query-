from finance_query.operation_graph_candidates import _typed_candidate
from finance_query.operation_graphs import canonical_sha256


def _item(operator: str, stage_count: int, required: list[str]) -> dict:
    route = {
        "question_id": 1,
        "question": "Giá trị là bao nhiêu?",
        "required_operations": required,
        "stages": [{"stage_id": f"s{i}"} for i in range(stage_count)],
    }
    return {
        "question_id": 1,
        "required_operations": required,
        "final_operator_candidates": [operator],
        "stage_nodes": [
            {"node_id": f"stage:s{i}", "op": "stage_ref", "stage_id": f"s{i}", "inputs": []}
            for i in range(stage_count)
        ],
        "route_contract": route,
        "source_route_sha256": canonical_sha256(route),
    }


def test_two_stage_subtract_graph_is_typed_but_not_authorized() -> None:
    graph, reasons = _typed_candidate(_item(
        "subtract", 2,
        ["reported_value", "stage_output_dependency", "subtract_or_difference", "multi_company_population"],
    ))
    assert reasons == []
    assert graph is not None
    assert graph["population_axes"] == ["entity"]
    assert graph["validation"]["status"] == "validated_not_authorized"


def test_operator_ordering_and_arity_remain_blocked() -> None:
    item = _item("subtract", 1, ["reported_value", "stage_output_dependency", "subtract_or_difference"])
    assert _typed_candidate(item)[1] == ["BINARY_OPERATOR_STAGE_ARITY_MISMATCH"]
    item["final_operator_candidates"] = ["subtract", "round"]
    assert _typed_candidate(item)[1] == ["OPERATOR_SEQUENCE_REQUIRES_HUMAN_ORDERING"]


def test_filter_then_count_requires_typed_sequence() -> None:
    item = _item(
        "filter_positive", 1,
        ["reported_value", "stage_output_dependency", "positive_negative_filter", "multi_company_population"],
    )
    item["question"] = "Có bao nhiêu công ty có dòng tiền dương?"
    assert _typed_candidate(item)[1] == ["FILTER_THEN_COUNT_REQUIRES_TYPED_SEQUENCE"]


def test_ratio_then_ranking_requires_typed_sequence() -> None:
    item = _item(
        "select_argmax", 2,
        ["reported_value", "stage_output_dependency", "min_max_ranking", "multi_year_range"],
    )
    item["question"] = "Năm nào tỷ trọng tài sản trên tổng tài sản cao nhất?"
    item["stage_nodes"][0]["stage_id"] = "stage_1_assets"
    item["stage_nodes"][1]["stage_id"] = "stage_2_total_assets"
    assert _typed_candidate(item)[1] == ["RATIO_THEN_RANKING_REQUIRES_TYPED_SEQUENCE"]


def test_population_aggregation_requires_homogeneous_stage_semantics() -> None:
    item = _item(
        "mean", 2,
        ["reported_value", "stage_output_dependency", "average_or_median", "multi_company_population"],
    )
    item["question"] = "Trung bình của các công ty là bao nhiêu?"
    item["stage_nodes"][0]["stage_id"] = "stage_1_credit_loss_provision"
    item["stage_nodes"][1]["stage_id"] = "stage_2_loans_to_customers"
    assert _typed_candidate(item)[1] == ["POPULATION_STAGE_SEMANTICS_NOT_HOMOGENEOUS"]
