from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.semantic_binding_corrections import (
    SemanticBindingCorrectionError,
    apply_semantic_binding_corrections,
    build_semantic_binding_correction_review,
    canonical_sha256,
    load_semantic_binding_corrections,
    sha256_file,
)


ROOT = Path(__file__).parents[1]
AUDIT_DIR = ROOT / "artifacts/research/grounded_campaign_chatgpt_audit_v7_20260823"
BASE_RUN = ROOT / "artifacts/runs/vifinqa-grounded-e2e-document-entity-role-v7_20260823-lock1"
TABLES = ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_structured_v2.jsonl"
SEMANTIC_QUEUE = ROOT / "artifacts/research/semantic_binding_review_v5_entity_role_20260823/semantic_binding_review_queue_v1.jsonl"
HUMAN_DECISIONS = ROOT / "artifacts/research/semantic_binding_review_v7_document_role_chatgpt_20260823/semantic_binding_human_decisions_with_document_role_v1.jsonl"
CONFIG = ROOT / "configs/semantic_binding_chatgpt_correction_q702_v1.json"
PINNED = ROOT / "artifacts/research/semantic_binding_chatgpt_correction_q702_20260823_v2"


def _build(tmp_path: Path) -> dict[str, object]:
    return build_semantic_binding_correction_review(
        audit_decision=AUDIT_DIR / "grounded_campaign_chatgpt_decision_v1.jsonl",
        audit_manifest=AUDIT_DIR / "grounded_campaign_chatgpt_audit_v1.manifest.json",
        base_bindings=BASE_RUN / "exact_cell_unit_binding_candidates_v3.jsonl",
        base_bindings_manifest=BASE_RUN / "exact_cell_unit_binding_candidates_v3.manifest.json",
        structured_tables=TABLES,
        semantic_review_queue=SEMANTIC_QUEUE,
        semantic_human_decisions=HUMAN_DECISIONS,
        config_path=CONFIG,
        output_dir=tmp_path / "review",
    )


def test_builds_numeric_value_free_q702_correction_without_rewriting_human_decision(
    tmp_path: Path,
) -> None:
    human_sha_before = sha256_file(HUMAN_DECISIONS)
    result = _build(tmp_path)
    assert result["counts"] == {
        "correction_count": 1,
        "numeric_literal_exposure_count": 0,
        "superseded_human_decision_count": 1,
    }
    assert result["outputs"]["packets"]["sha256"] == json.loads(
        (PINNED / "semantic_binding_correction_review_v1.manifest.json").read_text(
            encoding="utf-8"
        )
    )["outputs"]["packets"]["sha256"]
    packet_text = (tmp_path / "review/semantic_binding_correction_packets_v1.jsonl").read_text(
        encoding="utf-8"
    )
    assert "24.274.993.826" not in packet_text
    assert "(5.014.399.788)" not in packet_text
    assert "(113.877.140)" not in packet_text
    assert '"raw_decimal_candidate"' not in packet_text
    assert sha256_file(HUMAN_DECISIONS) == human_sha_before


def test_materializer_changes_only_q702_row_semantics_and_reads_value_deterministically(
    tmp_path: Path,
) -> None:
    _build(tmp_path)
    review_dir = tmp_path / "review"
    output = tmp_path / "exact_cell_unit_binding_candidates_v4.jsonl"
    result = apply_semantic_binding_corrections(
        base_bindings=BASE_RUN / "exact_cell_unit_binding_candidates_v3.jsonl",
        base_bindings_manifest=BASE_RUN / "exact_cell_unit_binding_candidates_v3.manifest.json",
        structured_tables=TABLES,
        packets=review_dir / "semantic_binding_correction_packets_v1.jsonl",
        decisions=review_dir / "semantic_binding_chatgpt_corrections_v1.jsonl",
        correction_manifest=review_dir / "semantic_binding_correction_review_v1.manifest.json",
        output=output,
    )
    assert result["counts"]["semantic_correction_count"] == 1
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    q702 = next(row for row in rows if row["question_id"] == 702)
    operand = q702["stages"][0]["required_operands"][0]
    assert operand["concept_id"] == "other_profit"
    assert operand["row_index"] == 14
    assert operand["semantic_correction"]["numeric_value_selected_by_reviewer"] is False
    assert operand["semantic_correction"]["value_materialization_method"] == "deterministic_exact_table_reopen_v1"
    base_rows = [
        json.loads(line)
        for line in (BASE_RUN / "exact_cell_unit_binding_candidates_v3.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    for base, corrected in zip(base_rows, rows, strict=True):
        if base["question_id"] == 702:
            continue
        corrected = dict(corrected)
        corrected["schema_version"] = base["schema_version"]
        corrected["protocol"] = base["protocol"]
        assert corrected == base


def test_rejects_tampered_chatgpt_selected_row(tmp_path: Path) -> None:
    _build(tmp_path)
    review_dir = tmp_path / "review"
    decisions_path = review_dir / "semantic_binding_chatgpt_corrections_v1.jsonl"
    decision = json.loads(decisions_path.read_text(encoding="utf-8"))
    decision["selected_semantic_row"]["row_index"] = 13
    payload = {key: value for key, value in decision.items() if key != "correction_decision_sha256"}
    decision["correction_decision_sha256"] = canonical_sha256(payload)
    decisions_path.write_text(json.dumps(decision, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest_path = review_dir / "semantic_binding_correction_review_v1.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"]["decisions"]["sha256"] = sha256_file(decisions_path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SemanticBindingCorrectionError, match="selected row is stale"):
        load_semantic_binding_corrections(
            packets=review_dir / "semantic_binding_correction_packets_v1.jsonl",
            decisions=decisions_path,
            manifest_path=manifest_path,
            base_bindings=BASE_RUN / "exact_cell_unit_binding_candidates_v3.jsonl",
            structured_tables=TABLES,
        )
