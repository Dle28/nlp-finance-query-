from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "research"
    / "run_named_compensation_variant_v1.py"
)
SPEC = importlib.util.spec_from_file_location("named_compensation_variant_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_person_extraction_handles_board_and_parent_company_wording() -> None:
    assert MODULE._person_query(
        "Thù lao của thành viên HĐQT Chu Thị Bình tại công ty mẹ MPC năm 2021"
    ) == "chu thi binh"
    assert MODULE._person_query(
        "Tổng thù lao Ông Nguyễn Hạnh Phúc – Chủ tịch của VNM trong năm 2023"
    ) == "nguyen hanh phuc"
    assert MODULE._person_query(
        "Tiền thù lao của ông Lê Phước Vũ (Chủ tịch HĐQT) của công ty mẹ HSG"
    ) == "le phuoc vu"


def test_family_spec_is_direct_named_compensation_only() -> None:
    item = {
        "id": 311,
        "question": (
            "Tiền thù lao của ông Lê Phước Vũ (Chủ tịch HĐQT) "
            "của công ty mẹ HSG trong năm tài chính kết thúc ngày 30/09/2024"
        ),
        "question_plan": {
            "family": "direct_lookup",
            "tickers": ["HSG"],
            "years": [2024],
            "scope": "separate",
        },
    }
    spec = MODULE._family_spec(item)
    assert spec is not None
    assert spec["person"] == "le phuoc vu"
    assert spec["ticker"] == "HSG"
    assert spec["year"] == 2024
    assert spec["scope"] == "separate"

    non_named = dict(item)
    non_named["question"] = "Tổng thù lao Hội đồng Quản trị của HSG năm 2024"
    assert MODULE._family_spec(non_named) is None


def test_specific_source_scale_beats_bare_vnd_only_for_duplicate_raw_value() -> None:
    assert MODULE._source_unit_specificity({"headers": ["2023Triệu VND"]}) == 2
    assert MODULE._source_unit_specificity({"headers": ["2023VND"]}) == 1
    assert MODULE._source_unit_specificity({"headers": ["2023"]}) == 0
