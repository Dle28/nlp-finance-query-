from __future__ import annotations

import json
from pathlib import Path

from finance_query.research.machine_exact_cell_proposals import (
    SOURCE_CONTRACT,
    build_machine_exact_cell_proposals,
    validate_machine_exact_cell_proposals,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_machine_proposals_are_value_blind_source_bound_and_non_authorizing(tmp_path: Path) -> None:
    triage = tmp_path / "triage.jsonl"
    _write_jsonl(
        triage,
        [
            {"question_id": 1, "primary_blocker": "SOURCE_NAVIGATION_PACKET_MISSING"},
            {"question_id": 2, "primary_blocker": "QUESTION_TO_OPERAND_PLAN_INCOMPLETE"},
        ],
    )
    period_packets = tmp_path / "period_packets.jsonl"
    _write_jsonl(
        period_packets,
        [
            {
                "question_id": 1,
                "stages": [
                    {"required_operands": [{"expected_table_types": ["income_statement"]}]}
                ],
            },
            {"question_id": 2, "stages": []},
        ],
    )
    locator = {
        "source_path": "/source/AAA.txt",
        "source_sha256": "a" * 64,
        "table_sha256": "b" * 64,
        "local_ordinal": 3,
        "char_start": 123,
        "page_no": 2,
    }
    diagnostics = tmp_path / "diagnostics.jsonl"
    _write_jsonl(
        diagnostics,
        [
            {
                "diagnostic_id": "d1",
                "question_id": 1,
                "route_id": "r1",
                "operand_id": "x0",
                "document_id": "AAA_2023",
                "internal_table_uid": "u1",
                "requested_year": 2023,
                "exact_table_locator": locator,
                "exact_table_locator_sha256": "locator-hash",
                "period_status": "UNIQUE_YEAR_HEADER_CANDIDATE",
                "unit_status": "UNIQUE_HEADER_UNIT_CANDIDATE",
                "scope_status": "SCOPE_MATCH",
                "source_unit_candidate": "trieu_dong",
                "table_function": {"kind": "income_statement"},
                "matching_year_column_indices": [1],
                "column_headers": [{"column_index": 1, "header_sha256": "header-hash"}],
                "review_priority_rank": 1,
            },
            {
                "diagnostic_id": "d2",
                "question_id": 1,
                "route_id": "r1",
                "operand_id": "x0",
                "document_id": "AAA_2023",
                "internal_table_uid": "u2",
                "requested_year": 2023,
                "exact_table_locator": {**locator, "table_sha256": "c" * 64, "local_ordinal": 4},
                "exact_table_locator_sha256": "locator-hash-2",
                "period_status": "UNIQUE_YEAR_HEADER_CANDIDATE",
                "unit_status": "UNIQUE_HEADER_UNIT_CANDIDATE",
                "scope_status": "SCOPE_MATCH",
                "source_unit_candidate": "trieu_dong",
                "table_function": {"kind": "income_statement"},
                "matching_year_column_indices": [1],
                "column_headers": [{"column_index": 1, "header_sha256": "header-hash-2"}],
                "review_priority_rank": 2,
            },
        ],
    )
    candidates = tmp_path / "candidates.jsonl"
    _write_jsonl(
        candidates,
        [
            {
                "row_candidate_id": "c1",
                "diagnostic_id": "d1",
                "row_index": 1,
                "row_rank": 1,
                "row_label": "Doanh thu thuần",
                "row_label_sha256": "row-hash",
                "row_label_token_jaccard": 1.0,
                "numeric_column_indices": [1],
            },
            {
                "row_candidate_id": "c2",
                "diagnostic_id": "d1",
                "row_index": 2,
                "row_rank": 2,
                "row_label": "Chi phí",
                "row_label_sha256": "other-row-hash",
                "row_label_token_jaccard": 0.2,
                "numeric_column_indices": [1],
            },
            {
                "row_candidate_id": "c3",
                "diagnostic_id": "d2",
                "row_index": 1,
                "row_rank": 1,
                "row_label": "Doanh thu thuần",
                "row_label_sha256": "row-hash-2",
                "row_label_token_jaccard": 0.9,
                "numeric_column_indices": [1],
            },
        ],
    )
    tables = tmp_path / "tables.jsonl"
    _write_jsonl(
        tables,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2023",
                "local_ordinal": 3,
                "source_provenance": {key: locator[key] for key in ("source_path", "source_sha256", "table_sha256", "char_start")},
                "rows": [["Chỉ tiêu", "2023"], ["Doanh thu thuần", "123456"], ["Chi phí", "2"]],
                "cell_provenance": [
                    [{}, {}],
                    [{"source_row": 1, "source_cell": 0}, {"source_row": 1, "source_cell": 1}],
                    [{}, {}],
                ],
            },
            {
                "internal_table_uid": "u2",
                "document_id": "AAA_2023",
                "local_ordinal": 4,
                "source_provenance": {**{key: locator[key] for key in ("source_path", "source_sha256", "char_start")}, "table_sha256": "wrong"},
                "rows": [["Chỉ tiêu", "2023"], ["Doanh thu thuần", "999"]],
                "cell_provenance": [[{}, {}], [{}, {"source_row": 1, "source_cell": 1}]],
            },
        ],
    )
    output = tmp_path / "output"

    summary = build_machine_exact_cell_proposals(
        triage_path=triage,
        period_packets_path=period_packets,
        table_diagnostics_path=diagnostics,
        row_candidates_path=candidates,
        structured_tables_path=tables,
        output_dir=output,
    )

    proposals = [
        json.loads(line)
        for line in (output / "machine_exact_cell_proposals_v1.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert summary["target_question_count"] == 1
    assert summary["proposal_count"] == 1
    assert proposals[0]["source_navigation"]["row_index"] == 1
    assert proposals[0]["source_navigation"]["column_index"] == 1
    assert proposals[0]["source_contract"] == SOURCE_CONTRACT
    assert "human_verified" not in proposals[0]
    assert "123456" not in (output / "machine_exact_cell_proposals_v1.jsonl").read_text(encoding="utf-8")
    assert validate_machine_exact_cell_proposals(output, expected_target_question_count=1)["status"] == "PASS"
