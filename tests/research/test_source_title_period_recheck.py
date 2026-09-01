from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.source_title_period_recheck import (
    CONTRACT,
    build_source_title_period_recheck,
    validate_source_title_period_recheck,
)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_source_title_period_recheck_revalidates_navigation_without_copying_answer(tmp_path: Path) -> None:
    triage = tmp_path / "triage.jsonl"
    periods = tmp_path / "periods.jsonl"
    routes = tmp_path / "routes.jsonl"
    discoveries = tmp_path / "discoveries.jsonl"
    tables = tmp_path / "tables.jsonl"
    context = tmp_path / "context.jsonl"
    _write_jsonl(triage, [{"question_id": 1, "primary_blocker": "PERIOD_HEADER_NOT_EXTRACTED"}])
    _write_jsonl(
        periods,
        [
            {
                "question_id": 1,
                "packet_status": "no_period_column",
                "question_context": {"years": [2023]},
                "stages": [
                    {
                        "stage_id": "s1",
                        "required_operands": [
                            {"role": "r", "expected_table_types": ["income_statement"]}
                        ],
                    }
                ],
            }
        ],
    )
    _write_jsonl(routes, [{"question_id": 1, "route_status": "route_complete"}])
    _write_jsonl(
        discoveries,
        [
            {
                "question_id": 1,
                "stage_id": "s1",
                "answer_decimal": "999999",
                "source_value_cells": [
                    {
                        "document_uid": "AAA_2023",
                        "internal_table_uid": "u1",
                        "row_index": 1,
                        "column_index": 1,
                        "raw_text_sha256": "cell",
                    }
                ],
                "source_contract": {
                    "research_only": True,
                    "evidence_eligible": False,
                    "may_materialize_answer": False,
                    "submission_eligible": False,
                },
            }
        ],
    )
    source = {"source_sha256": "a" * 64, "table_sha256": "b" * 64}
    _write_jsonl(
        tables,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2023",
                "source_provenance": source,
                "rows": [["Chỉ tiêu", "Năm nay"], ["Doanh thu", "123456"]],
                "cell_provenance": [[{}, {"source_row": 0}], [{}, {"source_row": 1}]],
            }
        ],
    )
    _write_jsonl(
        context,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2023",
                "source_provenance": source,
                "grid": {"rectangular": True, "provenance_complete": True},
                "quality": {"status": "review_ready"},
                "table_function": {"kind": "income_statement"},
                "context_trace": {"source_title": "Báo cáo cho năm tài chính kết thúc ngày 31 tháng 12 năm 2023 Đơn vị: VND"},
                "canonical_headers": {
                    "columns": [
                        {
                            "column_index": 1,
                            "source_label": "Năm nay",
                            "header_source_cells": [{"row_index": 0, "column_index": 1}],
                            "period_labels": ["Năm nay"],
                            "unit_labels": [],
                        }
                    ]
                },
                "row_profiles": [{"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}],
            }
        ],
    )
    period_manifest = tmp_path / "period.manifest.json"
    route_manifest = tmp_path / "route.manifest.json"
    context_manifest = tmp_path / "context.manifest.json"
    _write_json(
        period_manifest,
        {
            "outputs": {"period_packets": {"sha256": _sha(periods)}},
            "inputs": {"structured_tables_v2": {"sha256": _sha(tables)}},
        },
    )
    _write_json(route_manifest, {"outputs": {"overlay": {"sha256": _sha(routes)}}})
    _write_json(context_manifest, {"sidecar_sha256": _sha(context)})

    output = tmp_path / "output"
    summary = build_source_title_period_recheck(
        triage_path=triage,
        base_period_packets_path=periods,
        base_period_manifest_path=period_manifest,
        route_overlay_path=routes,
        route_overlay_manifest_path=route_manifest,
        discovery_candidates_path=discoveries,
        structured_tables_path=tables,
        evidence_context_path=context,
        evidence_context_manifest_path=context_manifest,
        output_dir=output,
        expected_question_count=1,
    )

    packet = json.loads((output / "period_column_candidate_packets_v1.jsonl").read_text(encoding="utf-8"))
    audit_text = (output / "source_title_period_recheck_audit_v1.jsonl").read_text(encoding="utf-8")
    candidate = packet["stages"][0]["required_operands"][0]["period_column_candidates"][0]
    assert summary["materialized_question_count"] == 1
    assert packet["packet_status"] == "unique_period_column_candidate"
    assert candidate["period_resolution_method"] == "v2_exact_source_title_current_header_v1"
    assert "999999" not in audit_text
    assert "123456" not in audit_text
    assert "answer_decimal" not in audit_text
    assert json.loads(audit_text)["source_contract"] == CONTRACT
    assert validate_source_title_period_recheck(output, expected_question_count=1)["status"] == "PASS"
