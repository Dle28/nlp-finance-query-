from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.report_navigation_overlay import (
    ReportNavigationOverlayError,
    build_report_navigation_overlay,
    load_report_navigation_overlay,
    require_financial_table_semantic_dispatch,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _contents_table() -> dict:
    return {
        "internal_table_uid": "contents-table",
        "document_id": "VGC_2025_consolidated",
        "local_ordinal": 2,
        "table_sha256": "a" * 64,
        "rows": [
            ["NỘI DUNG", "TRANG"],
            ["BÁO CÁO KIỂM TOÁN ĐỘC LẬP", "3 - 4"],
            ["BẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT", "5 - 8"],
        ],
    }


def _financial_table() -> dict:
    return {
        "internal_table_uid": "financial-table",
        "document_id": "VGC_2025_consolidated",
        "local_ordinal": 3,
        "table_sha256": "b" * 64,
        "rows": [["Chỉ tiêu", "2025"], ["Tiền và tương đương tiền", "1.000"]],
    }


def test_contents_page_is_blocked_by_literal_source_shape(tmp_path: Path) -> None:
    raw_tables = tmp_path / "tables.jsonl"
    _write_jsonl(raw_tables, [_contents_table(), _financial_table()])
    result = build_report_navigation_overlay(raw_tables=raw_tables, output_dir=tmp_path / "overlay")

    assert (result.table_count, result.contents_page_count) == (2, 1)
    overlay = load_report_navigation_overlay(tmp_path / "overlay")
    contents = overlay["contents-table"]
    assert contents["status"] == "TABLE_OF_CONTENTS_DETECTED"
    assert contents["source_shape"]["source_cells"][0]["literal"] == "NỘI DUNG"
    with pytest.raises(ReportNavigationOverlayError, match="semantic dispatch blocked"):
        require_financial_table_semantic_dispatch(overlay, "contents-table")
    assert require_financial_table_semantic_dispatch(overlay, "financial-table")["status"] == "NO_NAVIGATION_PATTERN_DETECTED"


def test_overlay_rejects_tampering_and_an_existing_output_path(tmp_path: Path) -> None:
    raw_tables = tmp_path / "tables.jsonl"
    _write_jsonl(raw_tables, [_contents_table()])
    output = tmp_path / "overlay"
    build_report_navigation_overlay(raw_tables=raw_tables, output_dir=output)

    with pytest.raises(FileExistsError, match="output-dir"):
        build_report_navigation_overlay(raw_tables=raw_tables, output_dir=output)
    (output / "report_navigation_overlay_v1.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ReportNavigationOverlayError, match="manifest is malformed"):
        load_report_navigation_overlay(output)


def test_overlay_refuses_a_changed_immutable_raw_input(tmp_path: Path) -> None:
    raw_tables = tmp_path / "tables.jsonl"
    _write_jsonl(raw_tables, [_contents_table()])
    output = tmp_path / "overlay"
    build_report_navigation_overlay(raw_tables=raw_tables, output_dir=output)

    _write_jsonl(raw_tables, [_financial_table()])

    with pytest.raises(ReportNavigationOverlayError, match="input hash"):
        load_report_navigation_overlay(output)
