from __future__ import annotations

import json
from pathlib import Path

from finance_query.research.multicol_semantic_diagnostic import (
    SOURCE_CONTRACT,
    build_multicol_semantic_diagnostic,
    validate_multicol_semantic_diagnostic,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _source(path: Path) -> str:
    text = (
        "BÁO CÁO TÀI CHÍNH RIÊNG tại ngày 31 tháng 12 năm 2023\n"
        "16. TÀI SẢN CÓ ĐỊNH VÔ HÌNH Biến động trong năm 2023 như sau\n"
        "<table><tr><td></td><td>Quyền sử dụng đất triệu đồng</td><td>Tổng cộng triệu đồng</td></tr>"
        "<tr><td colspan=\"3\">Giá trị còn lại</td></tr>"
        "<tr><td>Tại ngày cuối năm</td><td>10</td><td>20</td></tr></table>"
    )
    path.write_text(text, encoding="utf-8")
    return text


def _fixture(tmp_path: Path, *, v3_header_conflict: bool = False) -> tuple[Path, ...]:
    source_path = tmp_path / "source.txt"
    source_text = _source(source_path)
    table_start = source_text.index("<table")
    table_end = source_text.index("</table>") + len("</table>")
    source_hash = __import__("hashlib").sha256(source_path.read_bytes()).hexdigest()
    table_sha = "a" * 64
    uid = "u-test"
    table = {
        "internal_table_uid": uid,
        "document_id": "AAA_financial_statements_2023_separate",
        "ticker": "AAA",
        "report_year": 2023,
        "local_ordinal": 1,
        "page_no": 1,
        "header_row_indices": [0],
        "rows": [
            ["", "Quyền sử dụng đất triệu đồng", "Tổng cộng triệu đồng"],
            ["Giá trị còn lại", "", ""],
            ["Tại ngày cuối năm", "10", "20"],
        ],
        "cell_provenance": [
            [
                {"source_row": 0, "source_cell": 0, "anchor_row": 0, "anchor_column": 0, "covered_by_span": False},
                {"source_row": 0, "source_cell": 1, "anchor_row": 0, "anchor_column": 1, "covered_by_span": False},
                {"source_row": 0, "source_cell": 2, "anchor_row": 0, "anchor_column": 2, "covered_by_span": False},
            ],
            [
                {"source_row": 1, "source_cell": 0, "anchor_row": 1, "anchor_column": 0, "covered_by_span": False},
                {"source_row": 1, "source_cell": 0, "anchor_row": 1, "anchor_column": 0, "covered_by_span": True},
                {"source_row": 1, "source_cell": 0, "anchor_row": 1, "anchor_column": 0, "covered_by_span": True},
            ],
            [
                {"source_row": 2, "source_cell": 0, "anchor_row": 2, "anchor_column": 0, "covered_by_span": False},
                {"source_row": 2, "source_cell": 1, "anchor_row": 2, "anchor_column": 1, "covered_by_span": False},
                {"source_row": 2, "source_cell": 2, "anchor_row": 2, "anchor_column": 2, "covered_by_span": False},
            ],
        ],
        "column_labels": ["Nhãn dòng", "Quyền sử dụng đất triệu đồng", "Tổng cộng triệu đồng"],
        "source_provenance": {
            "source_path": str(source_path),
            "source_sha256": source_hash,
            "table_sha256": table_sha,
            "char_start": 0,
        },
    }
    context_header = "Tổng cộng triệu đồng" if not v3_header_conflict else "20 · -"
    context = {
        "internal_table_uid": uid,
        "document_id": table["document_id"],
        "source_provenance": table["source_provenance"],
        "grid": {"rectangular": True, "provenance_complete": True},
        "quality": {"status": "review_ready"},
        "canonical_headers": {
            "columns": [
                {"column_index": 0, "source_label": "", "header_source_cells": []},
                {"column_index": 1, "source_label": "Quyền sử dụng đất triệu đồng", "header_source_cells": [{"row_index": 0, "column_index": 1}]},
                {"column_index": 2, "source_label": context_header, "header_source_cells": [{"row_index": 0 if not v3_header_conflict else 2, "column_index": 2}]},
            ]
        },
        "row_profiles": [
            {"row_index": 0, "role": "header", "numeric_columns": []},
            {"row_index": 1, "role": "group_or_note", "numeric_columns": []},
            {"row_index": 2, "role": "data", "numeric_columns": [1, 2]},
        ],
        "context_trace": {
            "source_title": "AAA BÁO CÁO TÀI CHÍNH RIÊNG tại ngày 31 tháng 12 năm 2023 16. TÀI SẢN CÓ ĐỊNH VÔ HÌNH Biến động trong năm 2023 như sau",
            "unit_labels": ["triệu đồng"],
        },
    }
    metadata = {
        "company": "AAA",
        "document_id": table["document_id"],
        "report_scope": "separate",
        "reporting_period_end": {"year": 2023, "month": 12, "day": 31},
    }
    config = {
        "protocol": "vifinqa_multicol_semantic_diagnostic_v1",
        "schema_version": 1,
        "targets": [
            {
                "question_id": 1,
                "question": "Giá trị còn lại của tài sản vô hình của công ty mẹ AAA đến ngày 31 tháng 12 năm 2023 là bao nhiêu triệu đồng?",
                "entity": "AAA",
                "entity_role": "parent",
                "document_id": table["document_id"],
                "internal_table_uid": uid,
                "report_year": 2023,
                "report_scope": "separate",
                "local_ordinal": 1,
                "requested_unit": "triệu đồng",
                "source_unit": "triệu đồng",
                "raw_header_row_indices": [0],
                "raw_context_fragments": ["TÀI SẢN CÓ ĐỊNH VÔ HÌNH", "Giá trị còn lại", "Tại ngày cuối năm"],
                "period": {"end": "2023-12-31", "header_role": "table_instant_context"},
                "period_context_fragments": ["31 tháng 12 năm 2023", "trong năm 2023"],
                "target": {"row_index": 2, "row_label": "Tại ngày cuối năm", "row_role": "closing_net_book_value", "column_index": 2, "column_role": "total"},
                "columns": [
                    {"column_index": 0, "role": "row_label", "semantic_label": "Nhãn dòng", "header_source_cells": [{"row_index": 0, "column_index": 0}], "required_fragments": []},
                    {"column_index": 1, "role": "asset_class", "semantic_label": "Quyền sử dụng đất", "header_source_cells": [{"row_index": 0, "column_index": 1}], "required_fragments": ["Quyền sử dụng đất", "triệu đồng"]},
                    {"column_index": 2, "role": "total", "semantic_label": "Tổng cộng", "header_source_cells": [{"row_index": 0, "column_index": 2}], "required_fragments": ["Tổng cộng", "triệu đồng"]}
                ],
                "hypotheses": [
                    {"hypothesis_id": "H1", "claim": "closing total", "expected_status": "SUPPORTED", "table_uid": uid, "row_index": 2, "row_label": "Tại ngày cuối năm", "column_index": 2, "header_source_cells": [{"row_index": 0, "column_index": 2}], "column_fragments": ["Tổng cộng"], "evidence_fragments": ["Giá trị còn lại", "TÀI SẢN CÓ ĐỊNH VÔ HÌNH"]},
                    {"hypothesis_id": "H2", "claim": "asset component", "expected_status": "REJECTED", "table_uid": uid, "row_index": 2, "row_label": "Tại ngày cuối năm", "column_index": 1, "header_source_cells": [{"row_index": 0, "column_index": 1}], "column_fragments": ["Quyền sử dụng đất"], "evidence_fragments": ["Giá trị còn lại"]}
                ]
            }
        ]
    }
    questions = [{"id": 1, "question": config["targets"][0]["question"]}]
    paths = [
        tmp_path / "config.json",
        tmp_path / "questions.jsonl",
        tmp_path / "tables.jsonl",
        tmp_path / "contexts.jsonl",
        tmp_path / "metadata.jsonl",
    ]
    paths[0].write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    _write_jsonl(paths[1], questions)
    _write_jsonl(paths[2], [table])
    _write_jsonl(paths[3], [context])
    _write_jsonl(paths[4], [metadata])
    assert table_start < table_end
    return tuple(paths)


def test_multicol_diagnostic_redacts_values_and_validates(tmp_path: Path) -> None:
    config, questions, tables, contexts, metadata = _fixture(tmp_path)
    output = tmp_path / "artifact"
    summary = build_multicol_semantic_diagnostic(
        config_path=config,
        questions_path=questions,
        structured_tables_path=tables,
        evidence_context_path=contexts,
        document_metadata_path=metadata,
        output_dir=output,
    )
    assert summary["candidate_packet_count"] == 1
    assert validate_multicol_semantic_diagnostic(output, expected_question_ids=[1])["status"] == "PASS"
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in output.iterdir() if path.is_file())
    assert '"raw_source_cell"' not in rendered
    assert '"rows"' not in rendered
    assert '"cell_value"' not in rendered
    assert '"human_verified"' not in rendered
    candidate = json.loads(
        (output / "candidate_packets_v1.jsonl").read_text(encoding="utf-8")
    )

    def contains_scalar(value: object, target: object) -> bool:
        if isinstance(value, dict):
            return any(contains_scalar(child, target) for child in value.values())
        if isinstance(value, list):
            return any(contains_scalar(child, target) for child in value)
        return value == target

    assert not contains_scalar(candidate, "20")
    assert SOURCE_CONTRACT["evidence_eligible"] is False


def test_multicol_diagnostic_quarantines_v3_header_conflict(tmp_path: Path) -> None:
    config, questions, tables, contexts, metadata = _fixture(tmp_path, v3_header_conflict=True)
    output = tmp_path / "artifact"
    build_multicol_semantic_diagnostic(
        config_path=config,
        questions_path=questions,
        structured_tables_path=tables,
        evidence_context_path=contexts,
        document_metadata_path=metadata,
        output_dir=output,
    )
    validation = validate_multicol_semantic_diagnostic(output, expected_question_ids=[1])
    assert validation["candidate_packet_count"] == 0
    assert validation["quarantine_question_ids"] == [1]
    diagnostic = json.loads((output / "multicol_semantic_diagnostic_v1.jsonl").read_text(encoding="utf-8"))
    assert diagnostic["quarantine_reason"] == "HEADER_PROVENANCE_NOT_RECONCILED"
