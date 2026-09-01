from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[2]
    / "scripts"
    / "research"
    / "run_ratio_formula_variant_v1.py"
)
SPEC = importlib.util.spec_from_file_location("ratio_formula_variant_v1", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _operand(operand_id: str, metric: str) -> dict[str, object]:
    return {
        "operand_id": operand_id,
        "entity": "FOX",
        "ticker": "FOX",
        "years": [2024],
        "scope": "separate",
        "metric_hints": [metric],
        "grounding_contract": {
            "exact_internal_table_uid": True,
            "exact_row_index": True,
            "exact_column_index": True,
            "exact_raw_cell": True,
            "canonical_header_required": True,
        },
    }


def _item_and_plan(
    *,
    question: str = "Tỷ trọng các khoản tương đương tiền trên tổng tài sản năm 2024 là bao nhiêu %?",
    requested_unit: str | None = "percent",
) -> tuple[dict[str, object], dict[str, object]]:
    item = {"id": 656, "question": question}
    plan = {
        "question_id": 656,
        "entities": ["FOX"],
        "effective_family": "ratio_or_derived",
        "decomposition_status": "complete",
        "requested_unit": requested_unit,
        "operation_ast": {"op": "divide", "args": ["numerator", "denominator"]},
        "operands": [
            _operand("numerator", "các khoản tương đương tiền"),
            _operand("denominator", "tổng tài sản"),
        ],
    }
    return item, plan


def test_ratio_contract_requires_complete_explicit_divide_plan() -> None:
    item, plan = _item_and_plan()
    contract = MODULE._ratio_contract(item, plan)
    assert contract is not None
    operands, ticker = contract
    assert ticker == "FOX"
    assert set(operands) == {"numerator", "denominator"}


def test_ratio_contract_rejects_amount_unit_and_growth_shell() -> None:
    item, plan = _item_and_plan(requested_unit="billion_vnd")
    assert MODULE._ratio_contract(item, plan) is None

    item, plan = _item_and_plan(
        question="Tỷ lệ tăng trưởng tổng tài sản từ năm 2022 đến năm 2024 là bao nhiêu %?"
    )
    assert MODULE._ratio_contract(item, plan) is None


def test_ratio_query_has_explicit_percent_scaling_only_for_percent_output() -> None:
    assert MODULE._formula_query(percent=True).startswith("float((df1.loc[")
    assert MODULE._formula_query(percent=True).endswith(") * 100)")
    assert "* 100" not in MODULE._formula_query(percent=False)


def test_same_document_pair_is_required() -> None:
    left = {"document_id": "FOX_financial_statements_2024_separate"}
    right = {"document_id": "FOX_financial_statements_2024_separate.txt"}
    other = {"document_id": "FOX_financial_statements_2023_separate"}
    assert MODULE._same_document(left, right)
    assert not MODULE._same_document(left, other)
