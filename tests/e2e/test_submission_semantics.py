from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pandas as pd


def _builder_module():
    path = Path(__file__).resolve().parents[2] / "scripts/e2e/build_competition_submission_v1.py"
    spec = importlib.util.spec_from_file_location("competition_submission_builder_semantics", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_requested_divisor_handles_compound_vietnamese_units() -> None:
    builder = _builder_module()
    assert builder.requested_divisor("bao nhiêu tỷ đồng") == builder.Decimal("1000000000")
    assert builder.requested_divisor("bao nhiêu trăm tỷ đồng") == builder.Decimal("100000000000")
    assert builder.requested_divisor("bao nhiêu nghìn tỷ đồng") == builder.Decimal("1000000000000")


def test_source_multiplier_reads_header_outside_candidate_window() -> None:
    builder = _builder_module()
    table = {
        "column_labels": ["Nội dung", "Năm 2023", "Năm 2022"],
        "rows": [["", "Năm 2023", "Năm 2022"], ["", "( Triệu đồng )", "( Triệu đồng )"]],
    }
    assert builder.source_multiplier([], table) == builder.Decimal("1000000")


def test_source_multiplier_prefers_explicit_vnd_header_over_noisy_hint() -> None:
    builder = _builder_module()
    table = {
        "headers": ["Nhãn dòng", "Số cuối năm", "Đơn vị tính: VND Số đầu năm"],
        "unit_hint": "billion_vnd",
        "context_trace": {"unit_labels": ["tỷ đồng", "VND"]},
        "rows": [["Chi phí lãi vay phải trả", "249.470.628.101"]],
    }
    assert builder.source_multiplier([], table) == builder.Decimal("1")
    assert builder._table_declared_source_multiplier(table) == builder.Decimal("1")


def test_source_multiplier_accepts_compact_vnd_header_without_spaces() -> None:
    builder = _builder_module()
    table = {
        "headers": [
            "Nhãn dòng",
            "Số cuối nămVND · Năm nayVND",
            "Số đầu nămVND · Năm trướcVND",
        ],
        "unit_hint": "billion_vnd",
        "context_trace": {"unit_labels": ["tỷ đồng", "%", "VND"]},
        "rows": [["Tổng tài sản", "920.340.407.799", "803.994.098.970"]],
    }
    assert builder.source_multiplier([], table) == builder.Decimal("1")


def test_large_csv_operand_stays_numeric_for_pandas_replay(tmp_path: Path) -> None:
    builder = _builder_module()
    path = tmp_path / "evidence.csv"
    builder.write_evidence_csv(
        path,
        [{"value": builder.Decimal("249470627625794773392")}],
        builder.Decimal("249470627625794773392"),
        "program_multi_entity_plan",
    )
    frame = pd.read_csv(path)
    assert frame["operand_value"].dtype != object
    assert math.isclose(
        float(frame.loc[0, "operand_value"]),
        float(builder.Decimal("249470627625794773392")),
        rel_tol=1e-12,
    )


def test_zero_decimal_is_not_treated_as_missing() -> None:
    builder = _builder_module()
    assert builder.parse_decimal(0) == builder.Decimal("0")
    assert builder.parse_decimal_literal(0) == builder.Decimal("0")
    assert builder._csv_operand_value(builder.Decimal("0")) == "0"


def test_metric_core_prefers_exact_financial_line_over_entity_name() -> None:
    builder = _builder_module()
    score_exact = builder.semantic_row_score(
        "Nợ trung hạn Ngân hàng TMCP Quốc tế Việt Nam",
        "Số dư Nợ trung hạn của Ngân hàng TMCP Quốc tế Việt Nam",
        "Nợ trung hạn",
    )
    score_entity = builder.semantic_row_score(
        "Nợ trung hạn Ngân hàng TMCP Quốc tế Việt Nam",
        "Số dư Nợ trung hạn của Ngân hàng TMCP Quốc tế Việt Nam",
        "Công ty TNHH MTV Quản lý Nợ và Khai thác Tài sản NH TMCP Quốc Tế Việt Nam",
    )
    assert score_exact > score_entity
