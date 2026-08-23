from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.navigation_binding_promotions import (
    NavigationBindingPromotionError,
    apply_navigation_binding_promotions,
    build_navigation_binding_promotion_review,
    canonical_sha256,
    effective_approval_from_navigation_promotion,
    load_navigation_binding_promotions,
    sha256_file,
)


ROOT = Path(__file__).parents[1]
NAV_DECISIONS = ROOT / "artifacts/research/navigation_chatgpt_review_v1_20260823"
NAV_EVIDENCE = ROOT / "artifacts/research/navigation_review_evidence_v1_20260823"
BASE_RUN = ROOT / "artifacts/runs/vifinqa-grounded-e2e-semantic-correction-v8_20260823-lock3"
TABLES = ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_structured_v2.jsonl"
CONTEXT = ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_evidence_context_v3.jsonl"
CONTEXT_MANIFEST = ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/table_evidence_context_v3.manifest.json"
CONFIG = ROOT / "configs/navigation_binding_chatgpt_promotion_q167_v1.json"


def _build(tmp_path: Path) -> Path:
    output_dir = tmp_path / "review"
    build_navigation_binding_promotion_review(
        navigation_decisions=NAV_DECISIONS / "navigation_chatgpt_decisions_v1.jsonl",
        navigation_decisions_manifest=NAV_DECISIONS / "navigation_chatgpt_decisions_v1.manifest.json",
        navigation_evidence=NAV_EVIDENCE / "navigation_review_evidence_v1.jsonl",
        navigation_evidence_manifest=NAV_EVIDENCE / "navigation_review_evidence_v1.manifest.json",
        base_bindings=BASE_RUN / "exact_cell_unit_binding_candidates_v4.jsonl",
        base_bindings_manifest=BASE_RUN / "exact_cell_unit_binding_candidates_v4.manifest.json",
        structured_tables=TABLES,
        evidence_context=CONTEXT,
        evidence_context_manifest=CONTEXT_MANIFEST,
        config_path=CONFIG,
        output_dir=output_dir,
    )
    return output_dir


def test_q167_review_is_numeric_value_free_and_keeps_sector_unknown(tmp_path: Path) -> None:
    review = _build(tmp_path)
    packet = json.loads((review / "navigation_binding_promotion_packets_v1.jsonl").read_text())
    assert packet["question_id"] == 167
    assert packet["review_findings"]["sector"] == "NOT_INFERRED_EXACT_SOURCE_ROW_ONLY"
    assert packet["source_contract"]["numeric_value_exposed_to_reviewer"] is False
    reviewed_row = packet["reviewed_row"]
    assert "raw_value" not in reviewed_row
    assert "raw_source_cell" not in reviewed_row
    assert reviewed_row["value_cell_raw_sha256"]


def test_q167_materialization_changes_only_q167_and_builds_chatgpt_approval(tmp_path: Path) -> None:
    review = _build(tmp_path)
    output = tmp_path / "exact_cell_unit_binding_candidates_v5.jsonl"
    result = apply_navigation_binding_promotions(
        base_bindings=BASE_RUN / "exact_cell_unit_binding_candidates_v4.jsonl",
        base_bindings_manifest=BASE_RUN / "exact_cell_unit_binding_candidates_v4.manifest.json",
        structured_tables=TABLES,
        evidence_context=CONTEXT,
        packets=review / "navigation_binding_promotion_packets_v1.jsonl",
        decisions=review / "navigation_binding_chatgpt_promotions_v1.jsonl",
        promotion_manifest=review / "navigation_binding_promotion_review_v1.manifest.json",
        output=output,
    )
    assert result["counts"]["navigation_promotion_count"] == 1
    base_rows = [json.loads(line) for line in (BASE_RUN / "exact_cell_unit_binding_candidates_v4.jsonl").read_text().splitlines()]
    promoted_rows = [json.loads(line) for line in output.read_text().splitlines()]
    for base, promoted in zip(base_rows, promoted_rows, strict=True):
        if base["question_id"] == 167:
            operand = promoted["stages"][0]["required_operands"][0]
            assert promoted["binding_packet_status"] == "binding_ready"
            assert operand["binding_status"] == "binding_ready"
            assert operand["concept_id"] == "interest_expense"
            assert operand["navigation_promotion"]["numeric_value_selected_by_reviewer"] is False
            continue
        normalized = dict(promoted)
        normalized["schema_version"] = base["schema_version"]
        normalized["protocol"] = base["protocol"]
        assert normalized == base
    promotions = load_navigation_binding_promotions(
        packets=review / "navigation_binding_promotion_packets_v1.jsonl",
        decisions=review / "navigation_binding_chatgpt_promotions_v1.jsonl",
        manifest_path=review / "navigation_binding_promotion_review_v1.manifest.json",
        base_bindings=BASE_RUN / "exact_cell_unit_binding_candidates_v4.jsonl",
        structured_tables=TABLES,
        evidence_context=CONTEXT,
    )
    approval = effective_approval_from_navigation_promotion(next(iter(promotions.values())))
    assert approval["entity_role"] == "parent"
    assert approval["variable_decision_provenance"]["reviewer_type"] == "chatgpt_verified"
    assert approval["navigation_promotion_lineage"]["numeric_value_selected_by_reviewer"] is False


def test_rejects_value_selection_authority_escalation(tmp_path: Path) -> None:
    review = _build(tmp_path)
    decisions_path = review / "navigation_binding_chatgpt_promotions_v1.jsonl"
    decision = json.loads(decisions_path.read_text())
    decision["reviewer_authority"]["may_select_value"] = True
    payload = {key: value for key, value in decision.items() if key != "promotion_decision_sha256"}
    decision["promotion_decision_sha256"] = canonical_sha256(payload)
    decisions_path.write_text(json.dumps(decision, ensure_ascii=False) + "\n")
    manifest_path = review / "navigation_binding_promotion_review_v1.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["outputs"]["decisions"]["sha256"] = sha256_file(decisions_path)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(NavigationBindingPromotionError, match="authority boundary"):
        load_navigation_binding_promotions(
            packets=review / "navigation_binding_promotion_packets_v1.jsonl",
            decisions=decisions_path,
            manifest_path=manifest_path,
            base_bindings=BASE_RUN / "exact_cell_unit_binding_candidates_v4.jsonl",
            structured_tables=TABLES,
            evidence_context=CONTEXT,
        )
