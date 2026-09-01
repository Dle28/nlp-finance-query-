from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from finance_query.research.dataset_map import (
    build_dataset_research_map,
    classify_question,
    validate_dataset_research_map,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_classification_is_question_intrinsic_and_multidimensional() -> None:
    question = "Từ năm 2023 sang 2024, có bao nhiêu doanh nghiệp đồng thời tăng doanh thu và giảm biên lợi nhuận?"
    result = classify_question(question)
    assert result["taxonomy_basis"] == "question_text_only"
    assert result["question_type"] == "filter_then_count"
    assert {"count", "filter", "period_transition"} <= set(result["operation_families"] + result["temporal_families"])
    assert result["value_cardinality"] == "value_set"
    assert result["actual_source_topology"] == "UNRESOLVED_WITHOUT_GOLD_BINDING"


def test_reported_ratio_is_not_mistaken_for_percentage_change() -> None:
    result = classify_question("Tỷ lệ sở hữu của công ty mẹ cuối năm 2022 là bao nhiêu phần trăm?")
    assert result["question_type"] == "reported_value_lookup"
    assert result["operation_families"] == ["lookup"]
    assert result["output_type"] == "percentage"


@pytest.mark.parametrize(
    ("question", "expected_type", "expected_operation"),
    [
        ("Tính tổng chi phí của A, B và C trong năm 2024 theo đơn vị tỷ đồng.", "aggregate", "sum"),
        ("Giá trị trung bình tổng nợ cuối năm 2022, 2023 và 2024 là mấy tỷ đồng?", "aggregate", "mean"),
        ("Tổng nợ vay gấp tiền mặt bao nhiêu lần?", "ratio_or_margin", "ratio"),
        ("Kết quả thuần từ hoạt động tài chính năm 2024 là mấy tỷ đồng?", "net_value_composition", "net"),
        ("Số dư cuối năm 2022 của A hơn B mấy triệu đồng?", "difference", "difference"),
        ("Trong các năm đã nêu có bao nhiêu năm doanh thu lớn hơn 1 tỷ đồng?", "filter_then_count", "count"),
    ],
)
def test_candidate_discovery_promotes_reusable_patterns(
    question: str,
    expected_type: str,
    expected_operation: str,
) -> None:
    result = classify_question(question)
    assert result["question_type"] == expected_type
    assert expected_operation in result["operation_families"]
    assert result["candidate_category"] is False


def test_source_preposition_is_not_a_threshold_comparison() -> None:
    result = classify_question("Số dư trên báo cáo hợp nhất cuối năm 2024 là bao nhiêu tỷ đồng?")
    assert result["question_type"] == "reported_value_lookup"
    assert "threshold_filter" not in result["operation_families"]


def test_filtered_multi_entity_total_is_filter_then_aggregate() -> None:
    result = classify_question(
        "Tổng doanh thu của các công ty có lợi nhuận dương trong nhóm A, B và C là bao nhiêu?"
    )
    assert result["question_type"] == "filter_then_aggregate"
    assert {"filter", "sum"} <= set(result["operation_families"])


def test_maturity_bucket_is_not_a_threshold_filter() -> None:
    result = classify_question("Số dư nợ gốc kỳ hạn dưới 1 năm cuối năm 2024 là bao nhiêu?")
    assert result["question_type"] == "reported_value_lookup"
    assert "threshold_filter" not in result["operation_families"]


def test_count_wording_without_co_bao_nhieu_is_recognized() -> None:
    result = classify_question(
        "Trong số A, B và C, số công ty có tiền mặt trên 100 tỷ đồng là bao nhiêu?"
    )
    assert result["question_type"] == "filter_then_count"
    assert "count" in result["operation_families"]


def test_company_name_dai_duong_is_not_a_positive_sign_predicate() -> None:
    result = classify_question(
        "Vay và nợ của CTCP Tập đoàn Đại Dương cuối năm 2020 là bao nhiêu tỷ đồng?"
    )
    assert result["question_type"] == "reported_value_lookup"
    assert "threshold_filter" not in result["operation_families"]


def test_single_company_ghi_nhan_wording_remains_lookup() -> None:
    result = classify_question(
        "Doanh nghiệp ABC ghi nhận lãi tiền gửi bao nhiêu tỷ đồng trong năm 2017?"
    )
    assert result["question_type"] == "reported_value_lookup"
    assert result["operation_families"] == ["lookup"]


def test_metric_taxonomy_is_multilabel_and_question_intrinsic() -> None:
    result = classify_question(
        "Tỷ trọng hàng tồn kho trên tổng tài sản cuối năm 2024 là bao nhiêu phần trăm?"
    )
    assert {"inventory", "assets", "financial_ratio"} <= set(result["metric_families"])
    assert result["taxonomy_basis"] == "question_text_only"


def test_question_id_cannot_influence_classifier() -> None:
    question = "Doanh thu năm 2022 của công ty là bao nhiêu triệu đồng?"
    assert classify_question(question) == classify_question(question)
    with pytest.raises(TypeError):
        classify_question(question, {}, 123)  # type: ignore[call-arg]


def test_build_separates_taxonomy_from_baseline_and_validates(tmp_path: Path) -> None:
    questions = tmp_path / "questions.jsonl"
    certificates = tmp_path / "certificates.jsonl"
    output = tmp_path / "output"
    _write_jsonl(questions, [
        {"id": 99, "question": "Doanh thu năm 2022 là bao nhiêu triệu đồng?"},
        {"id": 7, "question": "Từ năm 2022 sang 2023, doanh thu tăng bao nhiêu phần trăm?"},
    ])
    _write_jsonl(certificates, [
        {"question_id": 7, "authorization_status": "ABSTAIN", "answer_certificate": {"status": "ABSTAIN", "abstain_reason_codes": ["NO_BINDING"]}},
        {"question_id": 99, "authorization_status": "ABSTAIN", "answer_certificate": {"status": "ABSTAIN", "abstain_reason_codes": ["NO_ROUTE"]}},
    ])
    summary = build_dataset_research_map(
        questions=questions,
        baseline_certificates=certificates,
        output_dir=output,
        expected_question_count=2,
    )
    assert summary["baseline_overall"]["status_counts"] == {"ABSTAIN": 2}
    assert summary["baseline_overall"]["pass_count"] is None
    assert summary["baseline_overall"]["model_outcome_counts"] == {
        "PASS": None,
        "FAIL": None,
        "ABSTAIN": 2,
    }
    assert summary["baseline_overall"]["prediction_count"] == 0
    assert summary["baseline_overall"]["coverage_rate"] == 0.0
    assert summary["baseline_overall"]["abstention_rate"] == 1.0
    assert summary["baseline_overall"]["false_confident_prediction_count"] == 0
    assert summary["baseline_overall"]["false_confidence_rate"] == "NOT_ESTIMABLE_NO_PREDICTIONS"
    assert summary["baseline_overall"]["calibration_status"] == "NOT_ESTIMABLE_NO_PREDICTIONS"
    assert all(
        not any(re.search(r"\bq\d+\b", code, re.IGNORECASE) for code in row["common_abstain_reason_counts"])
        for row in summary["baseline_by_dimension"]
    )
    taxonomy = [json.loads(line) for line in (output / "dataset_taxonomy_v1.jsonl").read_text().splitlines()]
    assert all("baseline_status" not in row for row in taxonomy)
    report = (output / "dataset_map_research_report_v1.md").read_text(encoding="utf-8")
    assert "## WHAT THIS EXPERIMENT DOES NOT PROVE" in report
    assert "`INVESTIGATE FURTHER`" in report
    assert validate_dataset_research_map(output, expected_question_count=2)["status"] == "VALIDATION_PASSED"


def test_builder_rejects_population_or_id_mismatch(tmp_path: Path) -> None:
    questions = tmp_path / "questions.jsonl"
    certificates = tmp_path / "certificates.jsonl"
    _write_jsonl(questions, [{"id": 1, "question": "Chỉ tiêu là bao nhiêu?"}])
    _write_jsonl(certificates, [{"question_id": 2, "authorization_status": "ABSTAIN"}])
    with pytest.raises(ValueError, match="identical IDs"):
        build_dataset_research_map(
            questions=questions,
            baseline_certificates=certificates,
            output_dir=tmp_path / "output",
            expected_question_count=1,
        )
