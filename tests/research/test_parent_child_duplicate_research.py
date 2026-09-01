from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.parent_child_duplicate_research import (
    CONTRACT,
    _contains_forbidden,
    _contains_metric_phrase,
    compare_ocr_sources,
    infer_row_hierarchy,
)


def test_parent_child_row_hierarchy_uses_prefix_and_code() -> None:
    rows = [
        ["A.", "TÀI SẢN NGẮN HẠN", "100", "", "", ""],
        ["IV.", "Hàng tồn kho", "140", "V.8", "", ""],
        ["1.", "Hàng tồn kho", "141", "", "", ""],
        ["2.", "Dự phòng giảm giá hàng tồn kho", "149", "", "", ""],
    ]
    hierarchy = infer_row_hierarchy(rows, code_columns=[2])

    assert hierarchy[1]["row_code"] == "140"
    assert hierarchy[1]["prefix_level"] == 1
    assert hierarchy[1]["child_row_indices"] == [2, 3]
    assert hierarchy[2]["row_code"] == "141"
    assert hierarchy[2]["parent_row_index"] == 1
    assert hierarchy[2]["prefix_level"] == 2


def test_duplicate_document_comparison_keeps_ocr_content_blind(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("B03 RIÊNG\nTARGET\nOCR-A\n", encoding="utf-8")
    second.write_text("B03 RIÊNG\nTARGET\nOCR-B\n", encoding="utf-8")
    result = compare_ocr_sources(
        first_path=first,
        second_path=second,
        first_expected_sha256=hashlib.sha256(first.read_bytes()).hexdigest(),
        second_expected_sha256=hashlib.sha256(second.read_bytes()).hexdigest(),
        target_table_sha256=["a" * 64, "a" * 64],
        target_row_fingerprints=["b" * 64, "b" * 64],
    )

    assert result["full_ocr_content_equal"] is False
    assert result["target_table_sha256_equal"] is True
    assert result["target_row_fingerprints_equal"] is True
    assert result["decision"] == "BLOCKED_DUPLICATE_PROVENANCE"
    assert "OCR-A" not in json.dumps(result, ensure_ascii=False)
    assert "OCR-B" not in json.dumps(result, ensure_ascii=False)


def test_candidate_contract_rejects_authorizing_or_value_bearing_fields() -> None:
    safe = {
        "candidate_only": True,
        "raw_numeric_values_included": False,
        "source_contract": CONTRACT,
        "row_code": "140",
        "numeric_cell_sha256": ["a" * 64],
    }
    unsafe = {"answer": "123", "raw_values": ["123"]}

    assert _contains_forbidden(safe) is False
    assert _contains_forbidden(unsafe) is True


def test_candidate_contract_never_authorizes_submission() -> None:
    assert CONTRACT["evidence_eligible"] is False
    assert CONTRACT["may_materialize_answer"] is False
    assert CONTRACT["submission_eligible"] is False
    assert CONTRACT["training_eligible"] is False


def test_metric_census_includes_explanatory_note_qualifier() -> None:
    assert _contains_metric_phrase("Lợi nhuận thuần trước thuế", "lợi nhuận trước thuế") is True
