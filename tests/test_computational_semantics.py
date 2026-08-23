from copy import deepcopy
from pathlib import Path

from finance_query.computational_semantics import (
    build_computational_semantic_plan,
    validate_semantic_dag,
)
from finance_query.financial_taxonomy import FinancialTaxonomy
from finance_query.metric_registry import FinancialMetricRegistry


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = ROOT / "configs" / "vietnamese_financial_taxonomy_v1.yaml"
REGISTRY_PATH = ROOT / "configs" / "financial_metric_registry_v1.yaml"
TAXONOMY = FinancialTaxonomy.load(TAXONOMY_PATH)
REGISTRY = FinancialMetricRegistry.load(REGISTRY_PATH, taxonomy=TAXONOMY)


def item(
    question: str,
    *,
    operation: str = "plan_required",
    family: str = "direct_lookup",
    tickers=None,
    years=None,
    scope="consolidated",
) -> dict:
    return {
        "id": 1,
        "question": question,
        "question_plan": {
            "family": family,
            "tickers": tickers if tickers is not None else ["HPG"],
            "years": years if years is not None else [2022],
            "scope": scope,
            "operation_ast": {"op": operation, "args": []},
        },
    }


def build(record: dict, catalog=None) -> dict:
    return build_computational_semantic_plan(
        record,
        taxonomy=TAXONOMY,
        registry=REGISTRY,
        table_catalog_rows=catalog,
    )


def test_total_assets_is_a_reported_lookup_not_count() -> None:
    plan = build(item("Tổng cộng tài sản của SSH cuối năm 2025 là bao nhiêu nghìn tỷ đồng?", operation="count", tickers=["SSH"], years=[2025]))
    assert plan["semantic_intent"] == "reported_value_lookup"
    assert [node["op"] for node in plan["nodes"]] == ["source_lookup", "lookup"]
    assert plan["nodes"][0]["variable_id"] == "total_assets"
    assert plan["semantic_axes"]["aggregation_role"] == ["total"]
    assert plan["question_plan_conflicts"][0]["old_operation"] == "count"


def test_company_word_does_not_become_total_aggregation_axis() -> None:
    plan = build(item("Lưu chuyển tiền thuần từ hoạt động kinh doanh của công ty mẹ VSC năm 2022 là bao nhiêu?", tickers=["VSC"]))
    assert "aggregation_role" not in plan["semantic_axes"]


def test_other_line_item_does_not_become_aggregation_axis() -> None:
    plan = build(item("Chi phí lương và các khoản khác theo lương của công ty mẹ HPG năm 2022 là bao nhiêu?"))
    assert "aggregation_role" not in plan["semantic_axes"]


def test_total_column_and_carrying_amount_do_not_trigger_sum() -> None:
    plan = build(item("Giá trị còn lại của lợi thế thương mại (tổng cộng) của VRE là bao nhiêu?", operation="sum", tickers=["VRE"], years=[2016]))
    assert plan["semantic_intent"] == "reported_value_lookup"
    assert plan["nodes"][0]["op"] == "unresolved_source_lookup"
    assert plan["semantic_axes"]["measurement_basis"] == ["carrying_amount"]
    assert "UNRESOLVED_REPORTED_LINE_ITEM" in plan["reason_codes"]
    assert plan["semantic_status"] == "partial"


def test_reported_voting_percentage_is_lookup_not_derived_ratio() -> None:
    plan = build(item("Tỷ lệ quyền biểu quyết của công ty mẹ năm 2023 là bao nhiêu phần trăm?", operation="divide", years=[2023]))
    assert plan["semantic_intent"] == "reported_value_lookup"
    assert plan["nodes"][-1]["op"] == "lookup"
    assert plan["question_plan_conflicts"]


def test_embedded_bank_entity_blocks_company_level_interest_expense_lookup() -> None:
    plan = build(
        item(
            "Chi phí lãi vay Ngân hàng TMCP Xăng dầu Petrolimex của PLX năm 2022 là bao nhiêu tỷ đồng?",
            operation="lookup",
            tickers=["PLX"],
            years=[2022],
            scope=None,
        )
    )
    assert plan["semantic_intent"] == "reported_value_lookup"
    assert plan["semantic_status"] == "partial"
    assert "UNMODELED_DETAIL_DIMENSION" in plan["reason_codes"]
    assert plan["unmodelled_detail_dimensions"] == ["embedded_reported_entity"]


def test_net_measurement_blocks_broad_inventory_lookup() -> None:
    plan = build(
        item(
            "Giá trị thuần của hàng tồn kho của HSG cuối năm 2017 là bao nhiêu?",
            operation="lookup",
            tickers=["HSG"],
            years=[2017],
        )
    )
    assert plan["semantic_status"] == "partial"
    assert plan["unmodelled_detail_dimensions"] == ["net_measurement"]


def test_exact_other_income_concept_is_not_treated_as_missing_dimension() -> None:
    plan = build(
        item(
            "Thu nhập khác của HPG năm 2022 là bao nhiêu?",
            operation="lookup",
            tickers=["HPG"],
            years=[2022],
        )
    )
    assert plan["semantic_status"] == "typed_candidate"
    assert plan["unmodelled_detail_dimensions"] == []


def test_inventory_median_share_has_explicit_population_denominator() -> None:
    plan = build(item(
        "Xét nhóm CEO, HPX và KBC năm 2022: Tỷ trọng tổng nợ ngắn hạn của các mã có hệ số hàng tồn kho/nợ ngắn hạn trên mức trung vị là bao nhiêu phần trăm?",
        tickers=["CEO", "HPX", "KBC"],
    ))
    assert plan["semantic_intent"] == "median_filter_then_population_share"
    by_id = {node["node_id"]: node for node in plan["nodes"]}
    assert by_id["eligible_entities"]["op"] == "filter_entities_gt"
    assert by_id["eligible_liabilities_sum"]["inputs"] == ["eligible_liabilities"]
    assert by_id["group_liabilities_sum"]["inputs"] == ["source_current_liabilities"]
    assert plan["difficulty_candidate"] == "hard"


def test_conjunctive_working_capital_filter_is_not_a_flat_count() -> None:
    plan = build(item(
        "Năm 2024, có bao nhiêu doanh nghiệp trong nhóm HPG và VIC đồng thời ghi nhận vốn lưu động ròng âm và lưu chuyển tiền thuần từ hoạt động kinh doanh dương?",
        tickers=["HPG", "VIC"],
        years=[2024],
    ))
    by_id = {node["node_id"]: node for node in plan["nodes"]}
    assert by_id["working_capital"]["op"] == "elementwise_subtract"
    assert by_id["eligible_entities"]["op"] == "intersect_entities"
    assert by_id["answer"] == {"node_id": "answer", "op": "count", "inputs": ["eligible_entities"], "output_type": "count"}


def test_ambiguous_deepest_decline_keeps_dag_but_blocks_semantics() -> None:
    plan = build(item(
        "Trong giai đoạn 2022 đến 2025, vào năm doanh thu thuần bị sụt giảm sâu nhất so với năm trước đó, tỷ suất dòng tiền thuần từ hoạt động kinh doanh trên doanh thu thuần là bao nhiêu phần trăm?",
        years=[2022, 2025],
    ))
    assert plan["semantic_intent"] == "temporal_selector_then_ratio"
    assert "DECLINE_MEASURE_AMBIGUOUS" in plan["reason_codes"]
    assert plan["semantic_status"] == "partial"
    assert any(node["op"] == "select_period" for node in plan["nodes"])


def test_known_metric_component_does_not_complete_cross_entity_difference() -> None:
    plan = build(item(
        "Tính chênh lệch lưu chuyển tiền thuần từ hoạt động kinh doanh giữa HPG và HSG năm 2022.",
        operation="subtract",
        tickers=["HPG", "HSG"],
    ))
    assert plan["semantic_intent"] == "known_metric_component_only"
    assert plan["semantic_status"] == "partial"
    assert plan["nodes"][-1]["op"] == "unresolved_composition"
    assert "UNSUPPORTED_COMPOSITION_TEMPLATE" in plan["reason_codes"]


def test_multiple_literal_components_are_preserved_but_do_not_complete_a_temporal_selector() -> None:
    plan = build(item(
        "Trong giai đoạn 2020-2022 của HPG, tại năm có biên lợi nhuận gộp cao nhất, hệ số thanh toán lãi vay là bao nhiêu lần?",
        operation="max",
        tickers=["HPG"],
        years=[2020, 2021, 2022],
    ))
    assert plan["semantic_intent"] == "known_multiple_components_only"
    assert plan["semantic_status"] == "partial"
    assert "UNSUPPORTED_COMPOSITION_TEMPLATE" in plan["reason_codes"]
    assert plan["nodes"][-1]["op"] == "unresolved_composition"
    assert plan["nodes"][-1]["literal_stage_count"] == 2
    assert {
        node["variable_id"]
        for node in plan["nodes"]
        if node["op"] == "source_lookup"
    } == {"gross_profit", "net_revenue", "profit_before_tax", "interest_expense"}


def test_known_concept_does_not_complete_ratio_question() -> None:
    plan = build(item(
        "Tỷ trọng tài sản ngắn hạn trong tổng nguồn vốn của HPG năm 2022 là bao nhiêu phần trăm?",
        operation="divide",
    ))
    assert plan["semantic_intent"] == "known_reported_component_only"
    assert plan["semantic_status"] == "partial"
    assert plan["nodes"][-1]["op"] == "unresolved_composition"


def test_direct_table_routing_requires_exact_variable_type_context() -> None:
    catalog = [
        {
            "internal_table_uid": "correct-balance-sheet",
            "company": "SSH",
            "report_year": 2025,
            "available_period_years": [2025],
            "report_scope": "consolidated",
            "table_type": "balance_sheet",
            "routing_eligible": True,
            "canonical_variables": [{"variable_id": "total_assets"}],
        },
        {
            "internal_table_uid": "wrong-notes-table",
            "company": "SSH",
            "report_year": 2025,
            "available_period_years": [2025],
            "report_scope": "consolidated",
            "table_type": "notes",
            "routing_eligible": True,
            "canonical_variables": [{"variable_id": "total_assets"}],
        },
    ]
    plan = build(item("Tổng cộng tài sản của SSH cuối năm 2025 là bao nhiêu?", tickers=["SSH"], years=[2025]), catalog)
    assert plan["table_routes"][0]["status"] == "candidate_tables_found"
    assert plan["table_routes"][0]["candidate_table_uids"] == ["correct-balance-sheet"]


def test_missing_scope_blocks_table_candidates() -> None:
    plan = build(item("Tổng tài sản của SSH năm 2025 là bao nhiêu?", operation="lookup", tickers=["SSH"], years=[2025], scope=None), [])
    assert plan["semantic_status"] == "context_blocked"
    assert plan["table_routes"][0]["status"] == "context_blocked"


def test_missing_scope_preserves_candidates_by_scope_without_selecting_one() -> None:
    catalog = [
        {
            "internal_table_uid": f"assets-{scope}",
            "company": "SSH",
            "report_year": 2025,
            "available_period_years": [2025],
            "report_scope": scope,
            "table_type": "balance_sheet",
            "routing_eligible": True,
            "canonical_variables": [{"variable_id": "total_assets"}],
        }
        for scope in ("consolidated", "separate")
    ]
    plan = build(item("Tổng tài sản của SSH năm 2025 là bao nhiêu?", tickers=["SSH"], years=[2025], scope=None), catalog)
    route = plan["table_routes"][0]
    assert route["status"] == "scope_ambiguous_candidates"
    assert set(route["candidate_table_uids_by_scope"]) == {"consolidated", "separate"}
    assert route["reason_code"] == "SCOPE_REQUIRED_BEFORE_TABLE_SELECTION"


def test_validator_rejects_unknown_operator_and_cycle() -> None:
    plan = build(item("Tổng tài sản của SSH năm 2025 là bao nhiêu?", tickers=["SSH"], years=[2025]))
    bad = deepcopy(plan)
    bad["nodes"][-1]["op"] = "invented_operator"
    bad["nodes"][0]["inputs"] = ["answer"]
    errors = validate_semantic_dag(bad, TAXONOMY)
    assert "UNKNOWN_OPERATOR:invented_operator" in errors
    assert "DAG_CYCLE" in errors
