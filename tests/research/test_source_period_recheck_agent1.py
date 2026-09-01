from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from finance_query.research.source_period_recheck_agent1 import (
    CONTRACT,
    _aligned,
    _contains_forbidden_key,
    continuation_header_link,
    document_header_period_marker,
)


def _table(source_path: Path, text: str, table_start: int) -> dict:
    source_path.write_text(text, encoding="utf-8")
    source_bytes = source_path.read_bytes()
    table_end = text.find("</table>", table_start) + len("</table>")
    return {
        "document_id": "fixture",
        "internal_table_uid": "fixture-table",
        "source_provenance": {
            "source_path": str(source_path),
            "source_sha256": sha256(source_bytes).hexdigest(),
            "char_start": table_start,
            "table_sha256": sha256(text[table_start:table_end].encode("utf-8")).hexdigest(),
        },
    }


def test_document_header_marker_deduplicates_repeated_period_prints(tmp_path: Path) -> None:
    prefix = "Tại ngày 31 tháng 12 năm 2019\n" * 3
    text = prefix + "<table><tr><td>fixture</td></tr></table>"
    table = _table(tmp_path / "source.txt", text, len(prefix))
    marker = document_header_period_marker(table=table, requested_year=2019)
    assert marker is not None
    assert marker["source_date"] == "2019-12-31"
    assert marker["unique_period_dates"] == ["2019-12-31"]
    assert marker["matching_occurrence_count"] == 3


def test_document_header_marker_fails_closed_on_two_dates_same_year(tmp_path: Path) -> None:
    prefix = "Tại ngày 31 tháng 12 năm 2019\nTại ngày 30 tháng 11 năm 2019\n"
    text = prefix + "<table><tr><td>fixture</td></tr></table>"
    table = _table(tmp_path / "source.txt", text, len(prefix))
    assert document_header_period_marker(table=table, requested_year=2019) is None


def test_continuation_header_inherits_only_from_adjacent_same_document() -> None:
    source_text = "Bảng cân đối kế toán riêng Tại ngày 31 tháng 12 năm 2017<table>previous</table>footer Bảng cân đối kế toán riêng (Tiếp theo) Tại ngày 31 tháng 12 năm 2017<table>continuation</table>"
    previous_table_start = source_text.index("<table>")
    previous_table_end = source_text.index("</table>") + len("</table>")
    continuation_preamble_start = previous_table_end
    continuation_table_start = source_text.rindex("<table>")
    previous = {
        "internal_table_uid": "previous",
        "document_id": "doc",
        "local_ordinal": 4,
        "source_provenance": {"source_sha256": "source", "table_sha256": "previous-table"},
    }
    continuation = {
        "internal_table_uid": "continuation",
        "document_id": "doc",
        "local_ordinal": 5,
        "source_provenance": {"source_sha256": "source", "table_sha256": "continuation-table"},
        "grid": {"rectangular": True, "provenance_complete": True, "width": 5},
        "quality": {"status": "needs_processing"},
    }
    previous_context = {
        "internal_table_uid": "previous",
        "document_id": "doc",
        "source_provenance": {"source_sha256": "source", "table_sha256": "previous-table"},
        "grid": {"rectangular": True, "provenance_complete": True, "width": 5},
        "quality": {"status": "review_ready"},
        "canonical_headers": {"columns": [{"column_index": index} for index in range(3)] + [{"column_index": 3, "period_labels": ["Số cuối năm"]}, {"column_index": 4, "period_labels": ["Số đầu năm"]}]},
    }
    continuation_context = {
        "internal_table_uid": "continuation",
        "document_id": "doc",
        "source_provenance": {"source_sha256": "source", "table_sha256": "continuation-table"},
        "grid": {"rectangular": True, "provenance_complete": True, "width": 5},
        "quality": {"status": "needs_processing"},
        "canonical_headers": {"columns": [{"column_index": index} for index in range(5)]},
    }
    assert _aligned(previous, previous_context)
    assert _aligned(continuation, continuation_context, allow_needs_processing=True)
    link = continuation_header_link(
        preceding_table=previous,
        continuation_table=continuation,
        preceding_context=previous_context,
        continuation_context=continuation_context,
        source_text=source_text,
        preceding_table_end=previous_table_end,
        preceding_preamble_start=0,
        preceding_preamble_end=previous_table_start,
        continuation_preamble_start=continuation_preamble_start,
        continuation_preamble_end=continuation_table_start,
        continuation_table_start=continuation_table_start,
        report_date="2017-12-31",
    )
    assert link == {"preceding_table_uid": "previous", "preceding_local_ordinal": 4, "current_local_ordinal": 5, "same_document_id": True, "same_source_sha256": True, "statement_date": "2017-12-31"}
    broken = dict(continuation)
    broken["document_id"] = "different-doc"
    assert continuation_header_link(
        preceding_table=previous,
        continuation_table=broken,
        preceding_context=previous_context,
        continuation_context=continuation_context,
        source_text=source_text,
        preceding_table_end=previous_table_end,
        preceding_preamble_start=0,
        preceding_preamble_end=previous_table_start,
        continuation_preamble_start=continuation_preamble_start,
        continuation_preamble_end=continuation_table_start,
        continuation_table_start=continuation_table_start,
        report_date="2017-12-31",
    ) is None


def test_candidate_contract_has_no_authorization_and_forbidden_payload_keys() -> None:
    assert CONTRACT["candidate_only"] is True
    assert CONTRACT["evidence_eligible"] is False
    assert CONTRACT["submission_eligible"] is False
    assert _contains_forbidden_key({"candidate_navigation": {"row_index": 20, "column_index": 3}}) is False
    assert _contains_forbidden_key({"candidate_navigation": {"raw_value": "123"}}) is True
