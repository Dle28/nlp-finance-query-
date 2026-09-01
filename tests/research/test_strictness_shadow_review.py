from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_query.research.strictness_shadow_review import (
    CONTRACT,
    build_strictness_shadow_review,
    validate_strictness_shadow_review,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_shadow_review_is_value_blind_and_non_submittable(tmp_path: Path) -> None:
    bindings = tmp_path / "bindings.jsonl"
    execution = tmp_path / "execution.jsonl"
    certificates = tmp_path / "certificates.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    overlay = tmp_path / "overlay.jsonl"
    queue = tmp_path / "queue.jsonl"
    _write_jsonl(
        bindings,
        [
            {
                "question_id": 1,
                "stages": [
                    {
                        "stage_id": "s1",
                        "required_operands": [
                            {
                                "binding_status": "binding_ready",
                                "role": "x0",
                                "concept_id": "interest_income",
                                "period_labels": ["2024"],
                                "source_unit": "vnd",
                                "header_source_cells": [{"row_index": 0, "column_index": 1}],
                            }
                        ],
                    }
                ],
            }
        ],
    )
    _write_jsonl(execution, [{"question_id": 1, "execution_status": "execution_replay_ready"}])
    _write_jsonl(
        certificates,
        [
            {
                "question_id": 1,
                "authorization_status": "ABSTAIN",
                "answer_certificate": {"status": "ABSTAIN", "abstain_reason_codes": ["VARIABLE_MISMATCH"]},
            }
        ],
    )
    _write_jsonl(
        evidence,
        [
            {
                "question_id": 1,
                "stage_id": "s1",
                "role": "x0",
                "evidence_binding": {"field_statuses": {"entity_status": "PASS", "variable_status": "UNRESOLVED"}},
            }
        ],
    )
    _write_jsonl(
        overlay,
        [
            {
                "question_id": 1,
                "question": "Chỉ tiêu X năm 2024 là bao nhiêu?",
                "question_context": {"entities": ["AAA"], "years": [2024]},
                "requested_output_unit": {"kind": "currency", "unit": "trieu_dong", "vnd_to_output_divisor": "1000000"},
            }
        ],
    )
    _write_jsonl(
        queue,
        [
            {
                "question_id": 1,
                "stage_id": "s1",
                "role": "x0",
                "document_uid": "AAA_2024",
                "internal_table_uid": "table-1",
                "source_title": "Báo cáo năm 2024",
                "source_provenance": {"source_path": "/source.txt", "char_start": 12, "source_sha256": "a", "table_sha256": "b"},
                "value_cell": {"row_index": 1, "column_index": 1, "raw_text": "123456", "raw_text_sha256": "value-hash"},
                "row_label_candidates": [
                    {"row_index": 1, "column_index": 0, "raw_text": "Chỉ tiêu X", "raw_text_sha256": "label-hash"},
                    {"row_index": 1, "column_index": 2, "raw_text": "789", "raw_text_sha256": "numeric-hash"},
                ],
            }
        ],
    )
    run = tmp_path / "run.json"
    _write_json(
        run,
        {
            "protocol": "vifinqa_grounded_e2e_v1",
            "source_contract": {"submission_eligible": False},
            "inputs": {"route_overlay": {"path": str(overlay), "sha256": _sha(overlay)}, "semantic_review_queue": {"path": str(queue), "sha256": _sha(queue)}},
            "outputs": {
                "bindings": {"path": str(bindings), "sha256": _sha(bindings)},
                "execution": {"path": str(execution), "sha256": _sha(execution)},
                "authorization": {
                    "answer_certificates_path": str(certificates),
                    "answer_certificates_sha256": _sha(certificates),
                    "evidence_bindings_path": str(evidence),
                    "evidence_bindings_sha256": _sha(evidence),
                },
            },
        },
    )

    output = tmp_path / "review"
    result = build_strictness_shadow_review(e2e_run_manifest_path=run, output_dir=output, expected_question_count=1)

    item = json.loads((output / "strictness_review_items_v1.jsonl").read_text(encoding="utf-8"))
    rendered = json.dumps(item, ensure_ascii=False)
    assert result["review_question_count"] == 1
    assert item["source_contract"] == CONTRACT
    assert item["operands"][0]["row_label_candidates"] == [
        {"column_index": 0, "label": "Chỉ tiêu X", "label_sha256": "label-hash", "row_index": 1}
    ]
    assert "123456" not in rendered and "789" not in rendered and "raw_text" not in rendered
    assert validate_strictness_shadow_review(output, expected_question_count=1)["status"] == "PASS"
