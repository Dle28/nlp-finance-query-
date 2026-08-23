from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


spec = importlib.util.spec_from_file_location(
    "ccl_phase5_final_review_ui",
    Path(__file__).parents[1] / "local" / "ccl_phase5_final_review_ui.py",
)
ui = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = ui
spec.loader.exec_module(ui)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _package(tmp_path: Path) -> tuple[Path, Path, Path]:
    assignment_path = tmp_path / ui.ASSIGNMENT_NAME
    template_path = tmp_path / ui.TEMPLATE_NAME
    manifest_path = tmp_path / ui.MANIFEST_NAME
    assignment = {
        "schema_version": 1,
        "protocol": ui.CALIBRATION_PROTOCOL,
        "calibration_item_id": "item-1",
        "model_decisions_blinded": True,
        "training_eligible": False,
        "certification_allowed": False,
        "review_stratum": "EXACT_CLOSED_WORLD_AGREEMENT",
        "review_packet": {
            "components": [
                {"component_id": "context-1", "role": "report_scope_or_selected_relation_context", "literal": "Context"},
                {"component_id": "header-1", "role": "column_header", "literal": "Header"},
                {"component_id": "row-1", "role": "row_label", "literal": "Row"},
            ]
        },
    }
    _jsonl(assignment_path, [assignment])
    template = {
        "schema_version": 1,
        "protocol": ui.RESPONSE_PROTOCOL,
        "calibration_item_id": "item-1",
        "immutable_assignment_sha256": ui.canonical_sha(assignment),
        "reviewer_id": None,
        "reviewed_at_utc": None,
        "review_decision": None,
        "primary_component_id": None,
        "supporting_component_ids": [],
        "unresolved_conditions": [],
        "source_coordinates_checked": None,
        "training_eligible": False,
        "certification_allowed": False,
    }
    _jsonl(template_path, [template])
    manifest_path.write_text(
        json.dumps(
            {
                "protocol": ui.CALIBRATION_PROTOCOL,
                "run_status": "final_review_calibration_assignment_ready_not_scored",
                "model_decisions_blinded": True,
                "human_review_required": True,
                "training_eligible": False,
                "certification_allowed": False,
                "outputs": {
                    ui.ASSIGNMENT_NAME: {"sha256": _sha(assignment_path)},
                    ui.TEMPLATE_NAME: {"sha256": _sha(template_path)},
                },
            }
        ),
        encoding="utf-8",
    )
    return assignment_path, template_path, manifest_path


def test_ui_builds_hash_bound_component_selection_response(tmp_path: Path) -> None:
    package = ui.load_review_package(*_package(tmp_path))

    rows = ui.build_response_rows(
        package,
        {
            "reviewer_id": "human-reviewer",
            "items": [
                {
                    "calibration_item_id": "item-1",
                    "review_decision": "SELECT_COMPONENTS",
                    "primary_component_id": "context-1",
                    "supporting_component_ids": ["header-1", "row-1"],
                    "unresolved_conditions": [],
                    "source_coordinates_checked": True,
                }
            ],
        },
        reviewed_at_utc="2026-08-18T10:00:00Z",
    )

    assert rows[0]["immutable_assignment_sha256"] == ui.canonical_sha(package.assignments[0])
    assert rows[0]["training_eligible"] is False
    assert rows[0]["reviewed_at_utc"] == "2026-08-18T10:00:00Z"


def test_ui_requires_an_abstention_reason_and_refuses_overwrite(tmp_path: Path) -> None:
    package = ui.load_review_package(*_package(tmp_path))
    with pytest.raises(ValueError, match="abstention needs a reason"):
        ui.build_response_rows(
            package,
            {
                "reviewer_id": "human-reviewer",
                "items": [
                    {
                        "calibration_item_id": "item-1",
                        "review_decision": "ABSTAIN_UNRESOLVED",
                        "primary_component_id": None,
                        "supporting_component_ids": [],
                        "unresolved_conditions": [],
                        "source_coordinates_checked": True,
                    }
                ],
            },
        )
    output = tmp_path / "responses.jsonl"
    ui.write_response_rows(output, [{"calibration_item_id": "item-1"}])
    with pytest.raises(FileExistsError):
        ui.write_response_rows(output, [{"calibration_item_id": "item-1"}])


def test_ui_explains_the_three_step_review_flow() -> None:
    page = ui.page_html()

    assert "Bạn chỉ cần làm ba việc cho mỗi item" in page
    assert "Chưa thể kết luận từ nguồn hiện có" in page
    assert "Hoàn tất và lưu phản hồi" in page
    assert "Ngữ cảnh đã liên kết từ báo cáo" in page
    assert "Không có tiêu đề trong nguồn" in page
    assert "Dạng nguồn: mục lục / chỉ mục trang" in page


def test_ui_marks_a_literal_contents_page_without_relabeling_it_as_financial_data() -> None:
    context = ui._source_context(
        {
            "document": {},
            "outside_table_context": {},
            "quality": {},
            "canonical_grid": {
                "columns": [],
                "rows": [
                    ["NỘI DUNG", "TRANG"],
                    ["BÁO CÁO KIỂM TOÁN ĐỘC LẬP", "3 - 4"],
                    ["BẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT", "5 - 8"],
                ],
            },
        }
    )

    assert context["source_shape_hint"] == {
        "kind": "table_of_contents",
        "evidence": "Nội dung | Trang; 2 dòng chỉ số trang",
    }


def test_ui_loads_a_read_only_structured_table_preview(tmp_path: Path) -> None:
    assignment, template, manifest = _package(tmp_path)
    structured = tmp_path / "tables_structured_v2.jsonl"
    normalized = tmp_path / "normalized_tables_v2.jsonl"
    assignment_row = json.loads(assignment.read_text(encoding="utf-8"))
    assignment_row["review_packet"]["internal_table_uid"] = "table-1"
    _jsonl(assignment, [assignment_row])
    template_row = json.loads(template.read_text(encoding="utf-8"))
    template_row["immutable_assignment_sha256"] = ui.canonical_sha(assignment_row)
    _jsonl(template, [template_row])
    manifest_row = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_row["outputs"][ui.ASSIGNMENT_NAME]["sha256"] = _sha(assignment)
    manifest_row["outputs"][ui.TEMPLATE_NAME]["sha256"] = _sha(template)
    manifest.write_text(json.dumps(manifest_row), encoding="utf-8")
    _jsonl(
        structured,
        [{"internal_table_uid": "table-1", "document_id": "report-1", "column_labels": ["Header"], "rows": [["Value"]]}],
    )
    _jsonl(
        normalized,
        [
            {
                "internal_table_uid": "table-1",
                "document": {"document_id": "report-1", "ticker": "ABC", "report_year": 2024, "scope": "separate"},
                "outside_table_context": {"source_heading": "Nguồn ghi chú", "reader_heading": "Nguồn ghi chú"},
                "quality": {"status": "needs_review", "reason_codes": ["generic_column_header"]},
                "canonical_grid": {
                    "columns": [
                        {"column_index": 0, "canonical_label": "Nhãn dòng", "source_label": "", "role": "row_label"}
                    ]
                },
            }
        ],
    )

    package = ui.load_review_package(assignment, template, manifest, structured, normalized)

    assert package.table_previews["item-1"]["rows"] == [["Value"]]
    assert package.table_previews["item-1"]["source_context"]["source_heading"] == "Nguồn ghi chú"
    assert package.table_previews["item-1"]["source_context"]["columns"][0]["source_label"] == ""
    assert "Bảng nguồn đang được nói tới" in ui.page_html()
