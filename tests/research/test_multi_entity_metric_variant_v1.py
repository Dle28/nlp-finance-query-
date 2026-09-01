from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VARIANT_PATH = ROOT / "scripts/research/run_multi_entity_metric_variant_v1.py"


def _load_variant():
    spec = importlib.util.spec_from_file_location("multi_entity_metric_variant_v1", VARIANT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clean_metric_removes_entity_and_output_shells() -> None:
    variant = _load_variant()

    cleaned = variant._clean_metric(
        "Tính giá trị trung bình của chỉ tiêu thu nhập bình quân năm của nhân viên "
        "năm của các công ty mẹ NAB, ABB, ACB và STB ra đơn vị triệu đồng",
        tickers={"NAB", "ABB", "ACB", "STB"},
    )

    assert cleaned == "thu nhap binh quan nam cua nhan vien"


def test_typed_contract_is_narrow_and_fail_closed() -> None:
    variant = _load_variant()
    complete = {
        "decomposition_status": "complete",
        "route": "simple_aggregation",
        "entities": ["AAA", "BBB"],
        "operands": [{"entity": "AAA"}, {"entity": "BBB"}],
        "operation_ast": {"op": "mean"},
    }
    abstained = {**complete, "decomposition_status": "abstain"}
    mismatched = {**complete, "operands": [{"entity": "AAA"}]}

    assert variant._is_contract_candidate(complete) is True
    assert variant._is_contract_candidate(abstained) is False
    assert variant._is_contract_candidate(mismatched) is False


def test_derived_ratio_is_not_treated_as_a_single_reported_metric() -> None:
    variant = _load_variant()

    assert variant._is_derived_metric("ty trong du phong chung trong tong du phong") is True
    assert variant._is_derived_metric("loi nhuan tren doanh thu") is True
    assert variant._is_derived_metric("ty le no xau") is True


def test_row_anchor_gate_rejects_semantic_substitutions() -> None:
    variant = _load_variant()

    assert variant._row_anchor_satisfied(
        "tong chi phi xay dung co ban do dang", "gia von hop dong xay dung"
    ) is False
    assert variant._row_anchor_satisfied(
        "trung binh lai thuan tu hoat dong kinh doanh ngoai hoi",
        "lo thuan tu hoat dong kinh doanh ngoai hoi",
    ) is False
    assert variant._row_anchor_satisfied(
        "so du cho vay khach hang", "trich lap du phong chung cho vay khach hang"
    ) is True


def test_selection_quality_rejects_generic_row_for_income_average() -> None:
    variant = _load_variant()
    metric = "thu nhap binh quan nam cua nhan vien"

    wrong = variant._selection_quality(
        {"row_label": "Thu nhập từ hoạt động dịch vụ nhận được", "score": 3.62}, metric
    )
    right = variant._selection_quality(
        {"row_label": "Thu nhập bình quân/người/năm", "score": 4.61}, metric
    )

    assert wrong[1] == 0
    assert right[1] >= 2


def test_annual_income_does_not_use_a_monthly_row() -> None:
    variant = _load_variant()
    metric = "thu nhap binh quan nam cua nhan vien"

    monthly = variant._selection_quality(
        {"row_label": "Thu nhập bình quân tháng", "score": 5.0}, metric
    )
    annual = variant._selection_quality(
        {"row_label": "Thu nhập bình quân/người/năm", "score": 5.0}, metric
    )

    assert monthly[1] == 0
    assert annual[1] >= 2
