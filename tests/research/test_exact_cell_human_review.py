from __future__ import annotations

import json
from pathlib import Path

from finance_query.research.exact_cell_human_review import (
    build_exact_cell_human_review_forms,
    validate_exact_cell_human_review_forms,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_human_review_forms_are_readable_and_non_approving(tmp_path: Path) -> None:
    sample = tmp_path / "sample.jsonl"
    _write_jsonl(
        sample,
        [
            {
                "sample_id": "sample-1",
                "review_packet_id": "packet-1",
                "route_id": "route-1",
                "question_id": 1,
                "operand_id": "x0",
                "ticker": "AAA",
                "report_year": 2023,
                "requested_scope": "consolidated",
                "metric_core_query": "Doanh thu thuần",
                "priority_bucket": "agreement_high_proxy",
                "candidate_packets": [
                    {
                        "review_priority_rank": 1,
                        "internal_table_uid": "u1",
                        "document_id": "AAA_2023_consolidated",
                        "observed_scope": "consolidated",
                        "scope_status": "SCOPE_MATCH",
                        "table_function": {"label": "Báo cáo kết quả kinh doanh"},
                        "exact_table_locator": {
                            "source_path": "/source/AAA.txt",
                            "page_no": 7,
                            "local_ordinal": 3,
                            "char_start": 100,
                        },
                        "period_status": "UNIQUE_YEAR_HEADER_CANDIDATE",
                        "fallback_period_status": "NOT_NEEDED_EXPLICIT_YEAR_HEADER",
                        "matching_year_column_indices": [2],
                        "unit_status": "UNIQUE_HEADER_UNIT_CANDIDATE",
                        "source_unit_candidate": "trieu_dong",
                        "column_headers": [
                            {
                                "column_index": 2,
                                "header_labels": ["Năm 2023 (triệu đồng)"],
                                "header_years": [2023],
                            }
                        ],
                        "row_candidates": [
                            {
                                "row_rank": 1,
                                "row_index": 4,
                                "row_label": "Doanh thu thuần",
                                "row_label_token_jaccard": 1.0,
                                "numeric_column_indices": [2],
                            }
                        ],
                    }
                ],
            }
        ],
    )
    plans = tmp_path / "plans.jsonl"
    _write_jsonl(
        plans,
        [{"question_id": 1, "question": "Doanh thu thuần AAA năm 2023 là bao nhiêu?"}],
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol": "vifinqa_exact_cell_human_review_forms_v1",
                "expected_review_sample_count": 1,
                "authorization": {
                    "review_forms_only": True,
                    "human_verified": False,
                    "may_authorize_evidence": False,
                    "may_authorize_answer": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "forms"
    result = build_exact_cell_human_review_forms(
        config_path=config, sample_path=sample, plans_path=plans, output_dir=output
    )
    form = next((output / "packets").glob("*.md")).read_text(encoding="utf-8")
    assert result["form_count"] == 1
    assert "Doanh thu thuần AAA năm 2023" in form
    assert "Dòng 4 — Doanh thu thuần" in form
    assert "APPROVE" in form and "UNCERTAIN" in form
    assert validate_exact_cell_human_review_forms(
        output, expected_review_sample_count=1
    )["status"] == "PASS"
