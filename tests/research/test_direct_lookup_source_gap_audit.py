from __future__ import annotations

import hashlib
import json
from pathlib import Path

import finance_query.research.direct_lookup_source_gap_audit as source_gap
from finance_query.research.direct_lookup_source_gap_audit import (
    build_direct_lookup_source_gap_audit,
    validate_direct_lookup_source_gap_audit,
)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _output_descriptor(path: Path) -> dict[str, str]:
    return {"path": path.name, "sha256": _sha(path)}


def test_source_gap_audit_quarantines_ambiguous_row_without_copying_a_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(source_gap, "validate_full_corpus_direct_lookup_adapter", lambda *_args, **_kwargs: {"status": "PASS"})
    monkeypatch.setattr(source_gap, "validate_machine_direct_lookup_route_materialization", lambda *_args, **_kwargs: {"status": "PASS"})
    triage, plans, routes = (tmp_path / name for name in ("triage.jsonl", "plans.jsonl", "routes.jsonl"))
    review, tables, context = (tmp_path / name for name in ("review.jsonl", "tables.jsonl", "context.jsonl"))
    _write_jsonl(triage, [{"question_id": 1, "primary_blocker": "RETRIEVED_ROUTE_NOT_MATERIALIZED_IN_E2E"}])
    _write_jsonl(
        plans,
        [
            {
                "question_id": 1,
                "decomposition_status": "complete",
                "effective_family": "direct_lookup",
                "operation_ast": {"op": "lookup"},
                "operands": [{"operand_id": "x0", "ticker": "AAA", "scope": "separate"}],
                "entities": ["AAA"],
                "years": [2023],
            }
        ],
    )
    _write_jsonl(
        routes,
        [
            {
                "question_id": 1,
                "route_status": "route_incomplete",
                "required_operations": ["reported_value"],
                "missing_operations": ["reported_value"],
            }
        ],
    )
    _write_jsonl(
        review,
        [
            {
                "question_id": 1,
                "ticker": "AAA",
                "report_year": 2023,
                "observed_scope": "separate",
                "internal_table_uid": "u1",
                "row_index": 1,
                "row_rank": 1,
                "row_label": "Doanh thu thuần",
                "row_token_jaccard": 1.0,
                "numeric_cell_indices": [1],
                "review_packet_id": "packet",
                "raw_value": "999999",
            }
        ],
    )
    source = {"source_path": "/source/AAA.txt", "source_sha256": "a" * 64, "table_sha256": "b" * 64, "char_start": 123}
    _write_jsonl(
        tables,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2023",
                "local_ordinal": 3,
                "source_provenance": source,
                "rows": [["Chỉ tiêu", "2023 triệu đồng"], ["Doanh thu thuần", "999999"], ["Doanh thu khác", "888888"]],
                "cell_provenance": [[{}, {}], [{}, {}], [{}, {}]],
            }
        ],
    )
    _write_jsonl(
        context,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2023",
                "source_provenance": {key: source[key] for key in ("source_sha256", "table_sha256")},
                "grid": {"rectangular": True, "provenance_complete": True},
                "quality": {"status": "review_ready"},
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
                    {"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []},
                    {"row_index": 2, "numeric_columns": [1], "unreliable_numeric_columns": []},
                ],
            }
        ],
    )
    adapter_dir, materialization_dir = tmp_path / "adapter", tmp_path / "materialization"
    adapter_dir.mkdir()
    materialization_dir.mkdir()
    adapter_audit = adapter_dir / "full_corpus_direct_lookup_adapter_audit_v1.jsonl"
    diagnostics = adapter_dir / "table_diagnostics_v1.jsonl"
    candidate_rows = adapter_dir / "row_candidates_v1.jsonl"
    header_sha = hashlib.sha256("2023 triệu đồng".encode()).hexdigest()
    locator = {**source, "local_ordinal": 3, "page_no": None}
    locator_sha = hashlib.sha256(json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    _write_jsonl(adapter_audit, [{"question_id": 1, "accepted_candidate_count": 1}])
    _write_jsonl(
        diagnostics,
        [
            {
                "diagnostic_id": "d1",
                "question_id": 1,
                "document_id": "AAA_2023",
                "internal_table_uid": "u1",
                "requested_year": 2023,
                "exact_table_locator": locator,
                "exact_table_locator_sha256": locator_sha,
                "period_status": "UNIQUE_YEAR_HEADER_CANDIDATE",
                "unit_status": "UNIQUE_HEADER_UNIT_CANDIDATE",
                "scope_status": "SCOPE_MATCH",
                "source_unit_candidate": "million_vnd",
                "matching_year_column_indices": [1],
                "column_headers": [{"column_index": 1, "header_sha256": header_sha}],
            }
        ],
    )
    _write_jsonl(
        candidate_rows,
        [
            {"diagnostic_id": "d1", "row_index": 1, "row_rank": 1, "row_label": "Doanh thu thuần", "row_label_sha256": "x", "row_label_token_jaccard": 1.0, "numeric_column_indices": [1]},
            {"diagnostic_id": "d1", "row_index": 2, "row_rank": 2, "row_label": "Doanh thu khác", "row_label_sha256": "y", "row_label_token_jaccard": 0.85, "numeric_column_indices": [1]},
        ],
    )
    _write_json(adapter_dir / "full_corpus_direct_lookup_adapter_summary_v1.json", {"minimum_row_jaccard": 0.9})
    _write_json(
        adapter_dir / "manifest.json",
        {
            "outputs": {
                "audit": _output_descriptor(adapter_audit),
                "table_diagnostics": _output_descriptor(diagnostics),
                "row_candidates": _output_descriptor(candidate_rows),
            }
        },
    )
    materialization_audit = materialization_dir / "machine_direct_lookup_route_audit_v1.jsonl"
    _write_jsonl(materialization_audit, [{"question_id": 1, "materialization_status": "QUARANTINED_SOURCE_CANDIDATE_NOT_UNIQUE_OR_INSUFFICIENT"}])
    _write_json(
        materialization_dir / "machine_direct_lookup_route_summary_v1.json",
        {"thresholds": {"minimum_row_margin": 0.2, "allow_v3_header_recovery": False}},
    )
    _write_json(
        materialization_dir / "manifest.json",
        {
            "inputs": {
                "table_diagnostics": {"sha256": _sha(diagnostics)},
                "row_candidates": {"sha256": _sha(candidate_rows)},
            },
            "outputs": {"audit": _output_descriptor(materialization_audit)},
        },
    )

    output = tmp_path / "output"
    summary = build_direct_lookup_source_gap_audit(
        triage_path=triage,
        plans_path=plans,
        base_route_overlay_path=routes,
        row_review_queue_path=review,
        structured_tables_path=tables,
        evidence_context_path=context,
        full_adapter_artifact_dir=adapter_dir,
        materialization_artifact_dir=materialization_dir,
        output_dir=output,
        expected_question_count=1,
    )

    text = (output / "question_source_gap_v1.jsonl").read_text(encoding="utf-8")
    assert summary["status_counts"] == {"STRICT_SOURCE_ROW_MARGIN_AMBIGUITY": 1}
    assert "row_label_margin_threshold" in text
    assert "999999" not in text
    assert "888888" not in text
    assert "human_verified" not in text
    assert validate_direct_lookup_source_gap_audit(output, expected_question_count=1)["status"] == "PASS"
