from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.multi_operand_context_recheck import (
    _context_recheck,
    build_multi_operand_context_recheck,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _asset(path: Path, uid: str, rows: list[list[str]], *, start: int) -> dict[str, object]:
    return {
        "internal_table_uid": uid,
        "source_path": str(path),
        "source_sha256": _sha(path),
        "table_sha256": f"table-{uid}",
        "local_ordinal": 1,
        "char_start": start,
        "char_end": start + 10,
        "page_no": 1,
        "report_year": 2024,
        "scope": "separate",
        "headers": rows[0],
        "header_row_indices": [0],
        "rows": rows,
    }


def test_context_recheck_confirms_page_and_document_context_and_catalogues_components(tmp_path: Path) -> None:
    page_source = tmp_path / "page-source.txt"
    page_text = "===== PAGE 1 =====\nNăm tài chính kết thúc ngày 31 tháng 12 năm 2024\nĐơn vị tính: Triệu đồng Việt Nam\n<table>"
    page_source.write_text(page_text, encoding="utf-8")
    document_source = tmp_path / "document-source.txt"
    document_text = "Báo cáo tài chính riêng cho năm tài chính kết thúc ngày 31 tháng 12 năm 2024\n===== PAGE 8 =====\n<table>"
    document_source.write_text(document_text, encoding="utf-8")
    component_source = tmp_path / "component-source.txt"
    component_text = "===== PAGE 3 =====\nNăm tài chính kết thúc ngày 31 tháng 12 năm 2024\n<table>"
    component_source.write_text(component_text, encoding="utf-8")
    assets = [
        _asset(
            page_source,
            "a",
            [["Chỉ tiêu", "Cột nguồn"], ["Trích lập dự phòng cho vay khách hàng", "10"]],
            start=page_text.index("<table>"),
        ),
        _asset(
            document_source,
            "b",
            [["Chỉ tiêu", "Năm nay Triệu VND"], ["Trích lập dự phòng cho vay khách hàng", "20"]],
            start=document_text.index("<table>"),
        ),
        _asset(
            component_source,
            "c",
            [
                ["Chỉ tiêu", "Năm 2024 Triệu VND"],
                ["Trích lập dự phòng chung cho vay khách hàng", "2"],
                ["Trích lập dự phòng cụ thể cho vay khách hàng", "6"],
            ],
            start=component_text.index("<table>"),
        ),
    ]
    # Deliberately mirror an OCR failure where every row of a narrow component
    # table is classified as a header; semantic row labels must still win.
    assets[2]["header_row_indices"] = [0, 1, 2]
    assets_path = tmp_path / "assets.jsonl"
    _write_jsonl(assets_path, assets)
    plans_path = tmp_path / "plans.jsonl"
    _write_jsonl(
        plans_path,
        [
            {
                "question_id": 1,
                "operands": [
                    {
                        "operand_id": name,
                        "years": [2024],
                        "scope": "separate",
                        "unit_contract": {"requested_unit": "million_vnd"},
                    }
                    for name in ("x0", "x1", "x2")
                ],
            }
        ],
    )
    diagnostic_dir = tmp_path / "diagnostic"
    diagnostic_dir.mkdir()
    diagnostic_path = diagnostic_dir / "machine_diagnostic_candidates_v1.json"
    diagnostic_path.write_text(
        json.dumps(
            {
                "protocol": "vifinqa_multi_operand_machine_diagnostic_v1",
                "authorization": {"may_authorize_answer": False, "submission_eligible": False},
                "candidates": [
                    {
                        "candidate_id": "candidate-1",
                        "question_id": 1,
                        "variant": "machine_adjusted_selection",
                        "operands": [
                            {"operand_id": "x0", "internal_table_uid": "a", "selected_cells": [{"row_index": 1, "column_index": 1}]},
                            {"operand_id": "x1", "internal_table_uid": "b", "selected_cells": [{"row_index": 1, "column_index": 1}]},
                            {"operand_id": "x2", "internal_table_uid": "c", "selected_cells": [{"row_index": 2, "column_index": 1}]},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (diagnostic_dir / "manifest.json").write_text(
        json.dumps({"outputs": {diagnostic_path.name: {"sha256": _sha(diagnostic_path)}}}), encoding="utf-8"
    )

    output = tmp_path / "output"
    report = build_multi_operand_context_recheck(
        diagnostic_path=diagnostic_path,
        plans_path=plans_path,
        assets_path=assets_path,
        output_dir=output,
    )

    assert report["recheck_status_counts"] == {"CONTEXT_RECHECK_CONFIRMED": 3}
    assert report["component_pattern_table_count"] == 1
    assert report["composition_alignments"][0]["primary_variant"] == "composition_hypothesis_common_plus_specific"
    assert report["candidate_condition_reconciliation"][0]["research_readiness"] == "CONTEXT_CONFIRMED_RESEARCH_CANDIDATE"
    rechecks = [json.loads(line) for line in (output / "context_rechecks_v1.jsonl").read_text(encoding="utf-8").splitlines()]
    by_operand = {row["operand_id"]: row for row in rechecks}
    assert by_operand["x0"]["period_recheck_status"] == "RECHECK_CONFIRMED_PAGE_CONTEXT_YEAR"
    assert by_operand["x0"]["unit_recheck_status"] == "RECHECK_CONFIRMED_PAGE_CONTEXT_UNIT"
    assert by_operand["x1"]["period_recheck_status"] == "RECHECK_CONFIRMED_DOCUMENT_YEAR_CURRENT_COLUMN"
    conversion_probe = _context_recheck(
        assets[0],
        {
            "years": [2024],
            "scope": "separate",
            "unit_contract": {"requested_unit": "billion_vnd", "conversion_allowed": False},
        },
    )
    assert conversion_probe["source_unit"] == "million_vnd"
    assert conversion_probe["unit_recheck_status"] == "RECHECK_CONFIRMED_SOURCE_UNIT_REQUIRES_CONVERSION"
    assert conversion_probe["unit_conversion_status"] == "CONVERSION_REQUIRED_BUT_PLAN_DISALLOWS"
    rendered = (output / "context_recheck_report_v1.json").read_text(encoding="utf-8")
    assert "Trích lập dự phòng" not in rendered and "raw_value" not in rendered
    assert report["authorization"]["submission_eligible"] is False
