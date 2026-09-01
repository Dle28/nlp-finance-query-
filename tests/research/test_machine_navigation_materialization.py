from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.machine_navigation_materialization import (
    MACHINE_CONTRACT,
    build_machine_navigation_materialization,
    validate_machine_navigation_materialization,
)
from finance_query.research.machine_exact_cell_proposals import PROTOCOL as PROPOSAL_PROTOCOL, SOURCE_CONTRACT


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_machine_navigation_materialization_only_changes_rechecked_candidate(tmp_path: Path) -> None:
    base_packets = tmp_path / "base.jsonl"
    _write_jsonl(
        base_packets,
        [
            {
                "question_id": 1,
                "packet_status": "packet_blocked",
                "input_packet_status": "no_candidate",
                "stages": [
                    {
                        "required_operands": [
                            {"expected_table_types": ["income_statement"], "period_column_candidates": []}
                        ]
                    }
                ],
            }
        ],
    )
    tables = tmp_path / "tables.jsonl"
    _write_jsonl(
        tables,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2023",
                "source_provenance": {"source_sha256": "a" * 64, "table_sha256": "b" * 64},
                "rows": [["Chỉ tiêu", "2023 triệu đồng"], ["Doanh thu", "123456"]],
            }
        ],
    )
    evidence = tmp_path / "evidence.jsonl"
    _write_jsonl(
        evidence,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2023",
                "source_provenance": {"source_sha256": "a" * 64, "table_sha256": "b" * 64},
                "grid": {"rectangular": True, "provenance_complete": True},
                "quality": {"status": "review_ready"},
                "table_function": {"kind": "income_statement"},
                "canonical_headers": {
                    "columns": [
                        {
                            "column_index": 1,
                            "header_source_cells": [{"row_index": 0, "column_index": 1}],
                            "period_labels": ["2023"],
                            "source_label": "2023 triệu đồng",
                            "unit_labels": ["triệu đồng"],
                        }
                    ]
                },
                "row_profiles": [
                    {"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}
                ],
            }
        ],
    )
    evidence_manifest = tmp_path / "evidence.manifest.json"
    _write_json(evidence_manifest, {"sidecar_sha256": _sha(evidence)})
    base_manifest = tmp_path / "base.manifest.json"
    _write_json(
        base_manifest,
        {
            "outputs": {"period_packets": {"sha256": _sha(base_packets)}},
            "inputs": {"structured_tables_v2": {"sha256": _sha(tables)}},
        },
    )
    header_hash = hashlib.sha256("2023 triệu đồng".encode("utf-8")).hexdigest()
    proposals = tmp_path / "proposals.jsonl"
    _write_jsonl(
        proposals,
        [
            {
                "proposal_id": "p1",
                "question_id": 1,
                "expected_table_types": ["income_statement"],
                "source_contract": SOURCE_CONTRACT,
                "source_navigation": {
                    "internal_table_uid": "u1",
                    "row_index": 1,
                    "column_index": 1,
                    "requested_year": 2023,
                    "period_header_sha256": header_hash,
                    "source_unit_candidate": "trieu_dong",
                    "document_id": "AAA_2023",
                    "exact_table_locator_sha256": "locator",
                    "row_label": "Doanh thu",
                    "row_label_sha256": "row",
                    "source_cell_provenance_sha256": "provenance",
                },
            }
        ],
    )
    proposal_manifest = tmp_path / "proposals.manifest.json"
    _write_json(
        proposal_manifest,
        {"protocol": PROPOSAL_PROTOCOL, "outputs": {"proposals": {"sha256": _sha(proposals)}}},
    )
    output = tmp_path / "output"

    summary = build_machine_navigation_materialization(
        base_period_packets_path=base_packets,
        base_period_manifest_path=base_manifest,
        proposals_path=proposals,
        proposals_manifest_path=proposal_manifest,
        structured_tables_path=tables,
        evidence_context_path=evidence,
        evidence_context_manifest_path=evidence_manifest,
        output_dir=output,
        expected_question_count=1,
    )

    packets = [json.loads(line) for line in (output / "period_column_candidate_packets_v1.jsonl").read_text().splitlines()]
    audit = [json.loads(line) for line in (output / "machine_navigation_recheck_audit_v1.jsonl").read_text().splitlines()]
    assert summary["materialized_question_count"] == 1
    assert packets[0]["packet_status"] == "unique_period_column_candidate"
    assert packets[0]["stages"][0]["required_operands"][0]["period_column_candidates"][0]["row_index"] == 1
    assert audit[0]["source_contract"] == MACHINE_CONTRACT
    assert "123456" not in (output / "machine_navigation_recheck_audit_v1.jsonl").read_text()
    assert validate_machine_navigation_materialization(output, expected_question_count=1)["status"] == "PASS"
