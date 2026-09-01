from __future__ import annotations

import hashlib
import json
from pathlib import Path

import finance_query.research.composition_operand_source_gap_audit as composition_audit
from finance_query.research.composition_operand_source_gap_audit import (
    build_composition_operand_source_gap_audit,
    validate_composition_operand_source_gap_audit,
)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_composition_audit_requires_one_strict_source_per_operand_without_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(composition_audit, "validate_candidate_artifact", lambda *_args, **_kwargs: {"status": "PASS"})
    triage, plans, review = (tmp_path / name for name in ("triage.jsonl", "plans.jsonl", "review.jsonl"))
    tables, context, context_manifest = (tmp_path / name for name in ("tables.jsonl", "context.jsonl", "context.manifest.json"))
    _write_jsonl(triage, [{"question_id": 1, "primary_blocker": "COMPOSITION_GRAPH_NOT_MATERIALIZED"}])
    _write_jsonl(
        plans,
        [
            {
                "question_id": 1,
                "effective_family": "temporal_change",
                "operation_ast": {"op": "subtract", "args": ["x1", "x0"]},
                "operands": [
                    {
                        "operand_id": "x0",
                        "required": True,
                        "ticker": "AAA",
                        "entity": "AAA",
                        "scope": "separate",
                        "years": [2023],
                    }
                ],
            }
        ],
    )
    _write_jsonl(
        review,
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
                "rows": [["Chỉ tiêu", "2023 triệu đồng"], ["Doanh thu thuần", "999999"]],
                "cell_provenance": [[{}, {}], [{}, {}]],
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
    _write_json(context_manifest, {"sidecar_sha256": _sha(context)})
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    _write_json(corpus_dir / "manifest.json", {"outputs": {"row_review_queue_v1.jsonl": {"sha256": _sha(review)}}})

    output = tmp_path / "output"
    summary = build_composition_operand_source_gap_audit(
        triage_path=triage,
        plans_path=plans,
        row_review_queue_path=review,
        full_corpus_artifact_dir=corpus_dir,
        structured_tables_path=tables,
        evidence_context_path=context,
        evidence_context_manifest_path=context_manifest,
        output_dir=output,
        expected_question_count=1,
    )

    operands = (output / "operand_source_gap_v1.jsonl").read_text(encoding="utf-8")
    questions = (output / "question_composition_source_gap_v1.jsonl").read_text(encoding="utf-8")
    assert summary["all_operands_unique_count"] == 1
    assert "UNIQUE_STRICT_SOURCE_ROW_RESOLVED" in operands
    assert "ALL_OPERANDS_HAVE_UNIQUE_STRICT_SOURCE_ROWS" in questions
    assert "999999" not in operands
    assert "human_verified" not in operands
    assert "navigation_candidate_sha256" in operands
    assert "document_identity_sha256" in operands
    assert "row_index" not in operands
    assert validate_composition_operand_source_gap_audit(output, expected_question_count=1)["status"] == "PASS"
