from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts/research/run_subsidiary_investment_variant_v1.py"
)
SPEC = importlib.util.spec_from_file_location("subsidiary_investment_variant_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VARIANT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VARIANT)


def _item(question: str, ticker: str, year: int, *, original_cost: bool = False) -> dict:
    metric = "Nguyên giá đầu tư vào công ty con" if original_cost else "Giá trị đầu tư vào công ty con"
    return {
        "id": 9000,
        "question": question,
        "effective_metric": metric,
        "question_plan": {
            "family": "direct_lookup",
            "tickers": [ticker],
            "years": [year],
            "scope": "separate",
            "operands": [{"metric": metric}],
        },
    }


def _table(
    uid: str,
    document_id: str,
    headers: list[str],
    rows: list[list[str]],
    *,
    source_title: str,
    unit_hint: str | None = None,
) -> dict:
    table = {
        "internal_table_uid": uid,
        "document_id": document_id,
        "scope": "separate",
        "report_year": int(document_id.split("_")[3]),
        "headers": headers,
        "column_labels": headers,
        "rows": rows,
        "context_trace": {"source_title": source_title},
    }
    if unit_hint is not None:
        table["unit_hint"] = unit_hint
    return table


def test_original_cost_uses_independent_vnd_unit_anchor() -> None:
    question = (
        "Nguyên giá đầu tư vào công ty con của công ty mẹ AAA đến ngày "
        "31/12/2023 là bao nhiêu trăm tỷ đồng?"
    )
    item = _item(question, "AAA", 2023, original_cost=True)
    note = _table(
        "note-aaa",
        "AAA_financial_statements_2023_separate",
        [
            "Đầu tư vào công ty con",
            "Số cuối năm Giá gốc",
            "Dự phòng",
            "Giá trị hợp lý",
            "Số đầu năm Giá gốc",
        ],
        [["Đầu tư vào công ty con", "2.807.566.671.231", "-", "", "2.407.746.671.231"]],
        source_title="AAA THUYẾT MINH BÁO CÁO TÀI CHÍNH RIÊNG",
        unit_hint="billion_vnd",
    )
    balance_sheet = _table(
        "balance-aaa",
        "AAA_financial_statements_2023_separate",
        ["Mã số", "TÀI SẢN", "Thuyết minh", "Số cuối năm VND", "Số đầu năm VND"],
        [["251", "1. Đầu tư vào công ty con", "14.1", "2.807.566.671.231", "2.407.746.671.231"]],
        source_title="AAA BẢNG CÂN ĐỐI KẾ TOÁN RIÊNG Đơn vị tính: VND",
    )

    result = VARIANT._resolve_subsidiary_investment(
        item,
        VARIANT._family_spec(item),
        tables_by_uid={"note-aaa": note, "balance-aaa": balance_sheet},
    )

    assert result is not None
    assert result[0] == Decimal("28.07566671231")
    assert len(result[1]) == 2
    assert result[1][0]["source_to_vnd_multiplier"] == "1"
    assert result[1][1]["role"] == "subsidiary_investment_unit_anchor"


def test_section_total_requires_child_checksum() -> None:
    question = (
        "Tổng giá trị đầu tư vào công ty con của công ty mẹ VRE đến ngày "
        "31/12/2024 là bao nhiêu triệu đồng?"
    )
    item = _item(question, "VRE", 2024)
    note = _table(
        "note-vre",
        "VRE_financial_statements_2024_separate",
        [
            "Nhãn dòng",
            "Số cuối năm Triệu VND Giá gốc",
            "Dự phòng",
            "Giá trị hợp lý",
            "Số đầu năm Triệu VND Giá gốc",
        ],
        [
            ["b. Đầu tư vào công ty con", "", "", "", ""],
            ["Công ty A", "12.168.956", "-", "", "12.168.956"],
            ["Công ty B", "571.609", "-", "", "562.009"],
            ["Công ty C", "1.228.153", "-", "", "1.228.153"],
            ["Công ty D", "7.638", "-", "", ""],
            ["", "13.976.356", "-", "", "13.959.118"],
        ],
        source_title="VRE THUYẾT MINH BÁO CÁO TÀI CHÍNH RIÊNG",
    )

    result = VARIANT._resolve_subsidiary_investment(
        item,
        VARIANT._family_spec(item),
        tables_by_uid={"note-vre": note},
    )

    assert result is not None
    assert result[0] == Decimal("13976356")
    assert result[1][0]["row_index"] == 5


def test_selector_adapter_reconstructs_output_unit_from_raw_source_cell() -> None:
    question = (
        "Giá trị đầu tư vào công ty con của công ty mẹ AAA đến ngày "
        "31/12/2023 là bao nhiêu triệu đồng?"
    )
    question_plan = {
        "tickers": ["AAA"],
        "years": [2023],
        "scope": "separate",
        "requested_unit": "million_vnd",
        "operands": [
            {
                "operand_id": "x0",
                "entity": "AAA",
                "ticker": "AAA",
                "period": 2023,
                "scope": "separate",
                "unit": "million_vnd",
            }
        ],
        "operation_ast": {"op": "lookup", "args": ["x0"]},
    }
    proposal = {
        "proposal_id": "q9001:proposal:exact_execution_r9:1",
        "question_id": 9001,
        "answer_decimal": "2.5",
        "answer_route": "exact_execution_r9",
        "operation_ast": question_plan["operation_ast"],
        "claims": {"operands": question_plan["operands"], "reporting_scope": "separate"},
        "verification": {"verification_class": "PARTIAL"},
    }
    table = _table(
        "raw-unit-table",
        "AAA_financial_statements_2023_separate",
        ["Nhãn dòng", "Số cuối năm VND"],
        [["Đầu tư vào công ty con", "2.500.000"]],
        source_title="AAA BẢNG CÂN ĐỐI KẾ TOÁN RIÊNG Đơn vị tính: VND",
    )

    selector_plan = VARIANT.BUILDER._proposal_to_selector_plan(
        proposal,
        question_plan=question_plan,
        question_text=question,
        evidence_rows=[
            {
                "internal_table_uid": "raw-unit-table",
                "row_index": 0,
                "column_index": 1,
                "raw_value": "2500000",
                "source_to_vnd_multiplier": "1",
                "source_cell_sha256": "source-hash",
            }
        ],
        tables_by_uid={"raw-unit-table": table},
    )

    assert selector_plan["operands"][0]["raw_value"] == Decimal("2.5")
    assert selector_plan["replay_status"] == "PASS"
    assert selector_plan["replayed_answer_decimal"] == "2.5"


def test_selector_adapter_does_not_promote_heuristic_route_to_complete() -> None:
    question = "Giá trị đầu tư vào công ty con của công ty mẹ AAA năm 2023 là bao nhiêu triệu đồng?"
    question_plan = {
        "operands": [{"operand_id": "x0", "period": 2023, "entity": "AAA", "scope": "separate", "unit": "million_vnd"}],
        "operation_ast": {"op": "lookup", "args": ["x0"]},
    }
    proposal = {
        "proposal_id": "q9002:proposal:semantic_cell_heuristic:1",
        "question_id": 9002,
        "answer_decimal": "2.5",
        "answer_route": "semantic_cell_heuristic",
        "operation_ast": question_plan["operation_ast"],
        "claims": {"operands": question_plan["operands"], "reporting_scope": "separate"},
        "verification": {"verification_class": "PARTIAL"},
    }

    selector_plan = VARIANT.BUILDER._proposal_to_selector_plan(
        proposal,
        question_plan=question_plan,
        question_text=question,
        evidence_rows=[
            {
                "internal_table_uid": "heuristic-table",
                "row_index": 0,
                "column_index": 1,
                "value": "2.5",
                "source_cell_sha256": "heuristic-hash",
            }
        ],
        tables_by_uid={},
    )

    assert selector_plan["replay_status"] == "PASS"
    assert selector_plan["semantic_completeness"] == "PARTIAL"
    assert selector_plan["operand_completeness"] == "INCOMPLETE"
