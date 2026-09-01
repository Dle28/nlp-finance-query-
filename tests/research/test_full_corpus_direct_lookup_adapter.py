from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.full_corpus_direct_lookup_adapter import (
    CONTRACT,
    _candidate,
    build_full_corpus_direct_lookup_adapter,
    validate_full_corpus_direct_lookup_adapter,
)


def test_candidate_rejects_a_table_outside_the_operand_statement_type() -> None:
    source = {"source_sha256": "a" * 64, "table_sha256": "b" * 64}
    table = {
        "internal_table_uid": "u1",
        "document_id": "AAA_2024",
        "rows": [["Chỉ tiêu", "2024 VND"], ["Lợi nhuận sau thuế", "100"]],
        "cell_provenance": [[{}, {}], [{}, {}]],
        "source_provenance": source,
    }
    context = {
        "internal_table_uid": "u1",
        "document_id": "AAA_2024",
        "source_provenance": source,
        "grid": {"rectangular": True, "provenance_complete": True},
        "quality": {"status": "review_ready"},
        "table_function": {"kind": "financial_data_schedule"},
        "canonical_headers": {"columns": [{"column_index": 1, "period_labels": ["2024"], "source_label": "2024 VND", "header_source_cells": [{"row_index": 0, "column_index": 1}]}]},
        "row_profiles": [{"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}],
    }
    plan = {
        "years": [2024],
        "operands": [{"ticker": "AAA", "allowed_table_functions": ["income_statement"]}],
    }
    row = {"ticker": "AAA", "report_year": 2024, "row_index": 1, "row_token_jaccard": 1.0}
    candidate, checks = _candidate(row=row, plan=plan, table=table, context=context, minimum_row_jaccard=0.9)
    assert candidate is None
    assert checks["table_function_matches_operand"] is False


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_corpus_adapter_requires_strict_source_bound_row_candidate(tmp_path: Path) -> None:
    triage = tmp_path / "triage.jsonl"
    plans = tmp_path / "plans.jsonl"
    routes = tmp_path / "routes.jsonl"
    reviews = tmp_path / "reviews.jsonl"
    tables = tmp_path / "tables.jsonl"
    context = tmp_path / "context.jsonl"
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
        reviews,
        [
            {
                "question_id": 1,
                "operand_id": "x0",
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
                "rows": [["Chỉ tiêu", "2023 triệu đồng"], ["Doanh thu thuần", "123456"]],
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
                "row_profiles": [{"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}],
            }
        ],
    )
    full_manifest = tmp_path / "full.manifest.json"
    context_manifest = tmp_path / "context.manifest.json"
    _write_json(full_manifest, {"protocol": "vifinqa_full_corpus_candidate_retrieval_v1", "outputs": {reviews.name: {"sha256": _sha(reviews)}}})
    _write_json(context_manifest, {"sidecar_sha256": _sha(context)})

    output = tmp_path / "output"
    summary = build_full_corpus_direct_lookup_adapter(
        triage_path=triage,
        plans_path=plans,
        base_route_overlay_path=routes,
        row_review_queue_path=reviews,
        full_corpus_manifest_path=full_manifest,
        structured_tables_path=tables,
        evidence_context_path=context,
        evidence_context_manifest_path=context_manifest,
        output_dir=output,
        expected_question_count=1,
    )

    diagnostic_text = (output / "table_diagnostics_v1.jsonl").read_text(encoding="utf-8")
    audit_text = (output / "full_corpus_direct_lookup_adapter_audit_v1.jsonl").read_text(encoding="utf-8")
    assert summary["diagnostic_count"] == 1
    assert "123456" not in diagnostic_text
    assert "123456" not in audit_text
    assert "human_verified" not in audit_text
    assert json.loads(audit_text)["source_contract"] == CONTRACT
    assert validate_full_corpus_direct_lookup_adapter(output, expected_question_count=1)["status"] == "PASS"
