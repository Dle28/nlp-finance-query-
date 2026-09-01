from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.ownership_subject_header_probe import (
    CONTRACT,
    build_ownership_subject_header_probe,
    validate_ownership_subject_header_probe,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_ownership_subject_header_probe_requires_one_source_ready_ownership_column(tmp_path: Path) -> None:
    subject_dir = tmp_path / "subject"
    subject_dir.mkdir()
    subject_rows = subject_dir / "embedded_subject_row_probe_v1.jsonl"
    _write_jsonl(
        subject_rows,
        [
            {
                "question_id": 1,
                "status": "UNIQUE_EXACT_SUBJECT_ROW_NAVIGATION",
                "subject_row_candidates": [{"internal_table_uid": "u1", "document_id": "AAA_2024", "local_ordinal": 2, "row_index": 1}],
            }
        ],
    )
    _write_json(subject_dir / "manifest.json", {"outputs": {subject_rows.name: {"sha256": _sha(subject_rows)}}})
    reclass = tmp_path / "reclass"
    reclass.mkdir()
    plans = reclass / "typed_operand_plans.jsonl"
    _write_jsonl(plans, [{"question_id": 1, "years": [2024], "operands": [{"scope": "separate"}]}])
    _write_json(reclass / "manifest.json", {"outputs": {"plans": {"sha256": _sha(plans)}}})
    source = {"source_sha256": "a" * 64, "table_sha256": "b" * 64}
    tables = tmp_path / "tables.jsonl"
    _write_jsonl(
        tables,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2024",
                "source_provenance": source,
                "rows": [["Tên", "Tỷ lệ sở hữu 31/12/2024"], ["Sao Mai", "888"]],
                "cell_provenance": [[{}, {}], [{}, {}]],
            }
        ],
    )
    context = tmp_path / "context.jsonl"
    _write_jsonl(
        context,
        [
            {
                "internal_table_uid": "u1",
                "document_id": "AAA_2024",
                "source_provenance": source,
                "grid": {"rectangular": True, "provenance_complete": True},
                "quality": {"status": "review_ready"},
                "canonical_headers": {
                    "columns": [
                        {
                            "column_index": 1,
                            "source_label": "Tỷ lệ sở hữu 31/12/2024",
                            "header_source_cells": [{"row_index": 0, "column_index": 1}],
                        }
                    ]
                },
                "row_profiles": [{"row_index": 1, "numeric_columns": [1], "unreliable_numeric_columns": []}],
            }
        ],
    )
    context_manifest = tmp_path / "context.manifest.json"
    _write_json(context_manifest, {"sidecar_sha256": _sha(context)})

    output = tmp_path / "output"
    summary = build_ownership_subject_header_probe(
        subject_probe_dir=subject_dir,
        reclassification_dir=reclass,
        structured_tables_path=tables,
        evidence_context_path=context,
        evidence_context_manifest_path=context_manifest,
        output_dir=output,
        expected_question_count=1,
    )
    rendered = (output / "ownership_subject_header_probe_v1.jsonl").read_text(encoding="utf-8")
    assert summary["status_counts"] == {"UNIQUE_OWNERSHIP_SUBJECT_HEADER_NAVIGATION": 1}
    assert "888" not in rendered and "Tỷ lệ sở hữu" not in rendered and "Sao Mai" not in rendered
    assert json.loads(rendered)["source_contract"] == CONTRACT
    assert validate_ownership_subject_header_probe(output, expected_question_count=1)["status"] == "PASS"
