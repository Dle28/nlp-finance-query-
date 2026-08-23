from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.cross_entity_binding_promotions import (
    CrossEntityBindingPromotionError,
    canonical_sha256,
    load_cross_entity_binding_promotions,
    merge_cross_entity_binding_promotion_reviews,
)


ROOT = Path(__file__).resolve().parents[1]
PROMOTION = ROOT / "artifacts/research/cross_entity_binding_chatgpt_promotion_q750_20260823_v1"
V9 = ROOT / "artifacts/runs/vifinqa-grounded-e2e-navigation-promotion-v9_20260823-candidate2"
V10 = ROOT / "artifacts/runs/vifinqa-grounded-e2e-cross-entity-v10_20260823-lock1"
V11 = ROOT / "artifacts/runs/vifinqa-grounded-e2e-dual-review-cross-entity-v11_20260823-lock1"
MERGED_PROMOTION = ROOT / "artifacts/research/cross_entity_binding_chatgpt_promotion_q746_q750_v2_20260823"
STRUCTURED = ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_structured_v2.jsonl"
CONTEXT = ROOT / "artifacts/kaggle_runs/notebook5554bd790d_v10_20260811/vifinqa_review_bundle/tables_evidence_context_v3.jsonl"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _question_index(path: Path) -> dict[int, dict]:
    return {int(row["question_id"]): row for row in _rows(path)}


def test_promotion_review_is_numeric_value_free_and_chatgpt_provenance_is_distinct() -> None:
    packet_path = PROMOTION / "cross_entity_binding_promotion_packets_v1.jsonl"
    decision_path = PROMOTION / "cross_entity_binding_chatgpt_promotions_v1.jsonl"
    packet_text = packet_path.read_text(encoding="utf-8")
    decision_text = decision_path.read_text(encoding="utf-8")
    packet, decision = _rows(packet_path)[0], _rows(decision_path)[0]
    q750 = _question_index(V10 / "exact_cell_unit_binding_candidates_v6.jsonl")[750]
    private_values = [
        operand["raw_decimal_candidate"]
        for stage in q750["stages"]
        for operand in stage["required_operands"]
    ]
    for value in private_values:
        assert value not in packet_text
        assert value not in decision_text
    assert packet["question_id"] == 750
    assert packet["operation_graph"]["operation_ast"] == {
        "op": "subtract",
        "args": ["stage_1_net_income", "stage_2_net_income"],
    }
    assert decision["decision"] == "approve_cross_entity_binding_promotion"
    provenance = decision["decision_provenance"]
    assert provenance["reviewer_type"] == "chatgpt_verified"
    assert provenance["reviewer_type"] != "human_verified"
    assert provenance["authority_grant"]["grant_scope"] == "cross_entity_binding_review_gate_equivalence"
    assert decision["reviewer_authority"]["may_select_value"] is False
    assert decision["reviewer_authority"]["may_execute_formula"] is False


def test_v10_diff_is_isolated_to_q750() -> None:
    old_bindings = _question_index(V9 / "exact_cell_unit_binding_candidates_v5.jsonl")
    new_bindings = _question_index(V10 / "exact_cell_unit_binding_candidates_v6.jsonl")
    binding_drift: list[int] = []
    for question_id in sorted(old_bindings):
        old, new = dict(old_bindings[question_id]), dict(new_bindings[question_id])
        new["schema_version"] = old.get("schema_version")
        new["protocol"] = old.get("protocol")
        if old != new:
            binding_drift.append(question_id)
    assert binding_drift == [750]

    for name in ("grounded_execution_replay_v2.jsonl", "answer_certificates_v1.jsonl"):
        old, new = _question_index(V9 / name), _question_index(V10 / name)
        assert [question_id for question_id in sorted(old) if old[question_id] != new[question_id]] == [750]

    def evidence_index(path: Path) -> dict[tuple[int, str, str], dict]:
        return {
            (int(row["question_id"]), str(row["stage_id"]), str(row["role"])): row
            for row in _rows(path)
        }

    old_evidence = evidence_index(V9 / "evidence_bindings_v1.jsonl")
    new_evidence = evidence_index(V10 / "evidence_bindings_v1.jsonl")
    assert sorted({key[0] for key in old_evidence if old_evidence[key] != new_evidence[key]}) == [750]


def test_q750_certificate_proves_two_distinct_entities_and_q746_stays_blocked() -> None:
    certificates = _question_index(V10 / "answer_certificates_v1.jsonl")
    execution = _question_index(V10 / "grounded_execution_replay_v2.jsonl")
    q750 = certificates[750]["answer_certificate"]
    assert q750["status"] == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"
    assert q750["abstain_reason_codes"] == []
    assert len(q750["binding_receipts"]) == 2
    assert {receipt["entity_scope"]["entity"] for receipt in q750["binding_receipts"]} == {"SAB", "DBC"}
    assert {receipt["entity_scope"]["scope"] for receipt in q750["binding_receipts"]} == {"separate"}
    assert {receipt["entity_role"]["role"] for receipt in q750["binding_receipts"]} == {"parent"}
    assert q750["operation_ast_sha256"] == execution[750]["composition_trace"]["operation_ast_sha256"]
    assert q750["execution_receipt"]["answer_decimal"] == execution[750]["composition_trace"]["converted_output_decimal"]
    assert certificates[746]["answer_certificate"]["status"] == "ABSTAIN"
    assert certificates[746]["answer_certificate"]["abstain_reason_codes"] == [
        "MULTI_STAGE_EXECUTION_GRAPH_NOT_MATERIALIZED"
    ]


def test_locked_v10_replay_matches_all_five_candidate_outputs() -> None:
    run = json.loads((V10 / "grounded_e2e_run_v1.json").read_text(encoding="utf-8"))
    assert run["binding_protocol"] == "v6_cross_entity_composition"
    assert run["reproducibility"] == {
        "answer_certificates_match": True,
        "authorization_readiness_match": True,
        "bindings_match": True,
        "evidence_bindings_match": True,
        "execution_match": True,
    }
    counts = run["outputs"]["authorization"]["counts"]
    assert counts["chatgpt_cross_entity_promotion_count"] == 1
    assert counts["answer_certificate_status_counts"] == {
        "ABSTAIN": 998,
        "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY": 14,
    }


def test_v11_diff_is_isolated_to_q746_and_preserves_q750() -> None:
    old_bindings = _question_index(V10 / "exact_cell_unit_binding_candidates_v6.jsonl")
    new_bindings = _question_index(V11 / "exact_cell_unit_binding_candidates_v6.jsonl")
    binding_drift: list[int] = []
    for question_id in sorted(old_bindings):
        old, new = dict(old_bindings[question_id]), dict(new_bindings[question_id])
        if old != new:
            binding_drift.append(question_id)
    assert binding_drift == [746]
    for name in ("grounded_execution_replay_v2.jsonl", "answer_certificates_v1.jsonl"):
        old, new = _question_index(V10 / name), _question_index(V11 / name)
        assert [qid for qid in sorted(old) if old[qid] != new[qid]] == [746]

    def evidence_index(path: Path) -> dict[tuple[int, str, str], dict]:
        return {
            (int(row["question_id"]), str(row["stage_id"]), str(row["role"])): row
            for row in _rows(path)
        }

    old_evidence = evidence_index(V10 / "evidence_bindings_v1.jsonl")
    new_evidence = evidence_index(V11 / "evidence_bindings_v1.jsonl")
    assert sorted({key[0] for key in old_evidence if old_evidence[key] != new_evidence[key]}) == [746]

    certificates = _question_index(V11 / "answer_certificates_v1.jsonl")
    for question_id, entities in ((746, {"DXS", "KHG"}), (750, {"SAB", "DBC"})):
        certificate = certificates[question_id]["answer_certificate"]
        assert certificate["status"] == "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"
        assert {row["entity_scope"]["entity"] for row in certificate["binding_receipts"]} == entities
        assert {row["entity_role"]["status"] for row in certificate["binding_receipts"]} == {"PASS"}


def test_locked_v11_replay_matches_all_five_candidate_outputs() -> None:
    run = json.loads((V11 / "grounded_e2e_run_v1.json").read_text(encoding="utf-8"))
    assert run["reproducibility"] == {
        "answer_certificates_match": True,
        "authorization_readiness_match": True,
        "bindings_match": True,
        "evidence_bindings_match": True,
        "execution_match": True,
    }
    counts = run["outputs"]["authorization"]["counts"]
    assert counts["chatgpt_cross_entity_promotion_count"] == 2
    assert counts["answer_certificate_status_counts"] == {
        "ABSTAIN": 997,
        "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY": 15,
    }


def test_merged_promotion_revalidates_both_questions_against_common_base(tmp_path: Path) -> None:
    result = merge_cross_entity_binding_promotion_reviews(
        source_manifests=[
            PROMOTION / "cross_entity_binding_promotion_review_v1.manifest.json",
            ROOT / "artifacts/research/cross_entity_binding_chatgpt_promotion_q746_v2_20260823/cross_entity_binding_promotion_review_v1.manifest.json",
        ],
        base_bindings=V9 / "exact_cell_unit_binding_candidates_v5.jsonl",
        structured_tables=STRUCTURED,
        evidence_context=CONTEXT,
        repository_root=ROOT,
        output_dir=tmp_path / "merged",
    )
    packets = _rows(Path(result["outputs"]["packets"]["path"]))
    assert [row["question_id"] for row in packets] == [746, 750]
    assert result["counts"] == {
        "promotion_count": 2,
        "operand_count": 4,
        "numeric_value_exposure_count": 0,
    }


def test_loader_rejects_reviewer_authority_escalation(tmp_path: Path) -> None:
    packets = PROMOTION / "cross_entity_binding_promotion_packets_v1.jsonl"
    decisions = _rows(PROMOTION / "cross_entity_binding_chatgpt_promotions_v1.jsonl")
    decisions[0]["reviewer_authority"]["may_execute_formula"] = True
    payload = {key: value for key, value in decisions[0].items() if key != "promotion_decision_sha256"}
    decisions[0]["promotion_decision_sha256"] = canonical_sha256(payload)
    decision_path = tmp_path / "decisions.jsonl"
    _write_rows(decision_path, decisions)
    manifest = json.loads((PROMOTION / "cross_entity_binding_promotion_review_v1.manifest.json").read_text(encoding="utf-8"))
    manifest["outputs"]["decisions"]["sha256"] = _sha(decision_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CrossEntityBindingPromotionError, match="authority boundary"):
        load_cross_entity_binding_promotions(
            packets=packets,
            decisions=decision_path,
            manifest_path=manifest_path,
            base_bindings=V9 / "exact_cell_unit_binding_candidates_v5.jsonl",
            structured_tables=STRUCTURED,
            evidence_context=CONTEXT,
            repository_root=ROOT,
        )
