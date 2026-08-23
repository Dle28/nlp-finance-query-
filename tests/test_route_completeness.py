import hashlib
import json

import pytest

from finance_query.route_completeness import (
    build_route_completeness_overlay,
    operation_requirements,
    requested_output_unit,
    route_coverage,
)

def test_explicit_output_units_are_parsed_without_registry_default() -> None:
 assert requested_output_unit("CFO là bao nhiêu tỷ đồng?")["vnd_to_output_divisor"]=="1000000000"
 assert requested_output_unit("CFO là bao nhiêu triệu đồng?")["unit"]=="trieu_dong"
 assert requested_output_unit("hệ số là bao nhiêu lần?")["kind"]=="times"

def test_q400_shape_cannot_be_covered_by_a_single_cfo_lookup() -> None:
 plan={"tickers":["HPG"],"years":[2020,2024],"scope":"consolidated","family":"multi_entity_or_period_aggregation"}
 req=operation_requirements("ở năm có tốc độ tăng doanh thu thuần so với năm liền trước cao nhất trong các năm tăng trưởng dương, tỷ lệ CFO trên doanh thu thuần là bao nhiêu phần trăm",plan)
 covered=route_coverage({"stages":[{"metric_id":"operating_cash_flow"}]},req)
 assert "year_over_year_growth" in req and "min_max_ranking" in req and "ratio_or_percent" in req
 assert set(req)-set(covered)


def test_equivalent_cash_label_is_not_a_positive_filter() -> None:
 plan={"tickers":["SAB"],"years":[2016],"scope":"separate","family":"direct_lookup"}
 req=operation_requirements("Tiền và các khoản tương đương tiền cuối năm 2016 là bao nhiêu tỷ đồng?",plan)
 assert req==["reported_value"]


def test_explicit_sign_predicate_remains_a_positive_filter() -> None:
 plan={"tickers":["HPG"],"years":[2024],"scope":"consolidated","family":"conditional_analytical"}
 req=operation_requirements("Trong các năm có tăng trưởng dương, doanh thu là bao nhiêu?",plan)
 assert "positive_negative_filter" in req


def test_planner_family_alone_does_not_invent_stage_dependency() -> None:
 plan={"tickers":["SSH"],"years":[2025],"scope":None,"family":"multi_entity_or_period_aggregation"}
 req=operation_requirements("Tổng cộng tài sản của SSH cuối năm 2025 là bao nhiêu nghìn tỷ đồng?",plan)
 assert req==["reported_value"]


def test_explicit_multi_year_range_requires_stage_dependency() -> None:
 plan={"tickers":["HPG"],"years":[2023,2024],"scope":"consolidated","family":"direct_lookup"}
 req=operation_requirements("Doanh thu giai đoạn từ năm 2023 đến năm 2024 là bao nhiêu?",plan)
 assert "multi_year_range" in req
 assert "stage_output_dependency" in req


def test_literal_cross_entity_comparison_requires_difference_graph() -> None:
 plan={"tickers":["MBB","EIB"],"years":[2023],"scope":"separate","family":"cross_entity_comparison"}
 req=operation_requirements("Tổng tài sản của MBB lớn hơn EIB bao nhiêu triệu đồng?",plan)
 assert "subtract_or_difference" in req
 assert "stage_output_dependency" in req


def test_overlay_accepts_hash_bound_bounded_route_packet_manifest(tmp_path) -> None:
    routes = tmp_path / "routes.jsonl"
    questions = tmp_path / "questions.jsonl"
    output = tmp_path / "overlay.jsonl"
    route_rows = []
    question_rows = []
    for question_id in range(1, 1013):
        route_rows.append(
            {"question_id": question_id, "route_status": "abstain", "question_context": {}, "reason_codes": [], "stages": []}
        )
        question_rows.append({"id": question_id, "question": "Câu hỏi không có ngữ cảnh", "question_plan": {}})
    routes.write_text("".join(json.dumps(row) + "\n" for row in route_rows), encoding="utf-8")
    questions.write_text("".join(json.dumps(row) + "\n" for row in question_rows), encoding="utf-8")
    manifest = tmp_path / "routes.manifest.json"
    manifest.write_text(
        json.dumps({"outputs": {"packets": {"sha256": hashlib.sha256(routes.read_bytes()).hexdigest()}}}),
        encoding="utf-8",
    )

    result = build_route_completeness_overlay(
        questions_path=questions,
        routes_path=routes,
        routes_manifest_path=manifest,
        output=output,
    )

    assert result["counts"]["question_count"] == 1012


def test_overlay_rejects_unbound_bounded_route_packet_manifest(tmp_path) -> None:
    routes = tmp_path / "routes.jsonl"
    questions = tmp_path / "questions.jsonl"
    routes.write_text("", encoding="utf-8")
    questions.write_text("", encoding="utf-8")
    manifest = tmp_path / "routes.manifest.json"
    manifest.write_text(json.dumps({"outputs": {"packets": {"sha256": "wrong"}}}), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        build_route_completeness_overlay(
            questions_path=questions,
            routes_path=routes,
            routes_manifest_path=manifest,
            output=tmp_path / "overlay.jsonl",
        )
