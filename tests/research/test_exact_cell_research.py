from __future__ import annotations

import json
from pathlib import Path

from finance_query.research.exact_cell_research import (
    build_exact_cell_research,
    validate_exact_cell_research,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _candidate(uid: str) -> dict[str, object]:
    return {
        "review_priority_rank": 1,
        "internal_table_uid": uid,
        "document_id": f"{uid}_2023_consolidated",
        "observed_scope": "consolidated",
        "retrieval_support": "lexical_and_dense",
        "lexical_rank": 1,
        "dense_rank": 1,
        "max_row_label_token_jaccard": 1.0,
        "exact_table_locator": {},
        "exact_table_locator_sha256": "x",
    }


def test_exact_cell_research_is_value_blind_and_stratified(tmp_path: Path) -> None:
    assets = tmp_path / "assets.jsonl"
    asset_rows: list[dict[str, object]] = []
    review_rows: list[dict[str, object]] = []
    for index, bucket in enumerate(
        ("agreement_high_proxy", "standard_review", "hard_review"), start=1
    ):
        uid = f"u{index}"
        asset_rows.append(
            {
                "internal_table_uid": uid,
                "document_id": f"{uid}_2023_consolidated",
                "ticker": "AAA",
                "report_year": 2023,
                "scope": "consolidated",
                "headers": ["Chỉ tiêu", "Năm 2023 (triệu đồng)"],
                "rows": [
                    ["Chỉ tiêu", "Năm 2023 (triệu đồng)"],
                    ["Doanh thu thuần", "1.000.000"],
                    ["Chi phí", "20.000"],
                ],
                "header_row_indices": [0],
                "unit_hint": None,
                "table_function": {"kind": "income_statement"},
                "table_section": {"kind": "income_statement"},
                "source_path": f"/source/{uid}.txt",
                "source_sha256": "a" * 64,
                "table_sha256": (str(index) * 64)[:64],
                "local_ordinal": index,
                "char_start": index * 10,
                "page_no": index,
            }
        )
        review_rows.append(
            {
                "review_packet_id": f"packet-{index}",
                "route_id": f"route-{index}",
                "question_id": index,
                "operand_id": "x0",
                "ticker": "AAA",
                "report_year": 2023,
                "requested_scope": "consolidated",
                "metric_core_query": "Doanh thu thuần",
                "priority_bucket": bucket,
                "candidates": [_candidate(uid)],
            }
        )
    _write_jsonl(assets, asset_rows)
    queue = tmp_path / "review.jsonl"
    _write_jsonl(queue, review_rows)
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol": "vifinqa_exact_cell_research_v1",
                "expected_route_count": 3,
                "research": {"max_tables_per_route": 3, "max_rows_per_table": 2},
                "review_sample": {
                    "per_priority_bucket": {
                        "agreement_high_proxy": 1,
                        "standard_review": 1,
                        "hard_review": 1,
                    }
                },
                "authorization": {
                    "research_only": True,
                    "navigation_metadata_only": True,
                    "may_authorize_evidence": False,
                    "may_authorize_answer": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "research"
    coverage = build_exact_cell_research(
        config_path=config,
        hybrid_review_queue_path=queue,
        assets_path=assets,
        output_dir=output,
    )
    diagnostics = list(
        map(json.loads, (output / "table_diagnostics_v1.jsonl").read_text().splitlines())
    )
    rows = list(map(json.loads, (output / "row_candidates_v1.jsonl").read_text().splitlines()))
    assert coverage["table_diagnostic_count"] == 3
    assert coverage["review_sample_count"] == 3
    assert coverage["structural_candidate_only_count"] == 3
    assert all(row["period_status"] == "UNIQUE_YEAR_HEADER_CANDIDATE" for row in diagnostics)
    assert all(row["unit_status"] == "UNIQUE_HEADER_UNIT_CANDIDATE" for row in diagnostics)
    assert all("1.000.000" not in json.dumps(row, ensure_ascii=False) for row in [*diagnostics, *rows])
    sample = list(
        map(json.loads, (output / "stratified_review_sample_v1.jsonl").read_text().splitlines())
    )
    assert all(len(row["candidate_packets"]) == 1 for row in sample)
    assert all(row["candidate_packets"][0]["row_candidates"] for row in sample)
    assert validate_exact_cell_research(
        output, expected_route_count=3, expected_review_sample_count=3
    )["status"] == "PASS"
