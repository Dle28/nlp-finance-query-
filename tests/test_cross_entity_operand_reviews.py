from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.cross_entity_operand_reviews import (
    CrossEntityOperandReviewError,
    build_chatgpt_decisions,
    canonical_sha256,
    materialize_and_execute,
    reconcile_chatgpt_decisions,
)


ROOT = Path(__file__).resolve().parents[1]
PACKET_DIR = ROOT / "artifacts/research/cross_entity_operand_review_q746_q750_v1_20260823"
DECISION_DIR = ROOT / "artifacts/research/cross_entity_operand_chatgpt_review_q746_q750_v1_20260823"
MATERIAL_DIR = ROOT / "artifacts/research/cross_entity_subtract_materialization_q750_v1_20260823"
NORMALIZED = ROOT / "artifacts/research/preprocessing_v2_run_003/normalized_tables_v2.jsonl"
PREPROCESSING_MANIFEST = ROOT / "artifacts/research/preprocessing_v2_run_003/preprocessing_manifest_v2.json"
REVIEW_SPEC = ROOT / "configs/cross_entity_operand_chatgpt_review_q746_q750_v1.json"
Q746_PACKET_DIR = ROOT / "artifacts/research/cross_entity_operand_review_q746_v2_20260823"
Q746_PROPOSAL_DIR = ROOT / "artifacts/research/cross_entity_operand_chatgpt_proposal_q746_v2_20260823"
Q746_CRITIC_DIR = ROOT / "artifacts/research/cross_entity_operand_chatgpt_critic_q746_v2_20260823"
Q746_ADJUDICATION_DIR = ROOT / "artifacts/research/cross_entity_operand_dual_chatgpt_adjudication_q746_v2_20260823"
Q746_MATERIAL_DIR = ROOT / "artifacts/research/cross_entity_subtract_materialization_q746_v2_20260823"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rehash_record(record: dict, field: str) -> None:
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"schema_version", "protocol", field}
    }
    record[field] = canonical_sha256(payload)


def _materialize_kwargs(tmp_path: Path) -> dict:
    return {
        "packets": PACKET_DIR / "cross_entity_operand_review_packets_v1.jsonl",
        "packet_manifest": PACKET_DIR / "cross_entity_operand_review_packets_v1.manifest.json",
        "decisions": DECISION_DIR / "cross_entity_operand_chatgpt_decisions_v1.jsonl",
        "decision_manifest": DECISION_DIR / "cross_entity_operand_chatgpt_decisions_v1.manifest.json",
        "normalized_tables": NORMALIZED,
        "preprocessing_manifest": PREPROCESSING_MANIFEST,
        "output_dir": tmp_path / "materialized",
    }


def test_packets_and_public_receipts_never_expose_financial_values() -> None:
    packet_text = (PACKET_DIR / "cross_entity_operand_review_packets_v1.jsonl").read_text(encoding="utf-8")
    decision_text = (DECISION_DIR / "cross_entity_operand_chatgpt_decisions_v1.jsonl").read_text(encoding="utf-8")
    public_text = (
        (MATERIAL_DIR / "cross_entity_public_tokens_v1.jsonl").read_text(encoding="utf-8")
        + (MATERIAL_DIR / "cross_entity_public_execution_receipts_v1.jsonl").read_text(encoding="utf-8")
    )
    private_rows = _rows(MATERIAL_DIR / "cross_entity_executor_registry_v1.jsonl")
    assert private_rows
    for row in private_rows:
        raw_value = row["raw_decimal_candidate"]
        assert raw_value not in packet_text
        assert raw_value not in decision_text
        assert raw_value not in public_text
    assert '"numeric_values_exposed_to_reviewer":false' in packet_text


def test_chatgpt_gate_is_equivalent_but_provenance_is_not_human() -> None:
    packets = {row["question_id"]: row for row in _rows(PACKET_DIR / "cross_entity_operand_review_packets_v1.jsonl")}
    decisions = {row["question_id"]: row for row in _rows(DECISION_DIR / "cross_entity_operand_chatgpt_decisions_v1.jsonl")}
    assert packets[746]["packet_status"] == "blocked_missing_entity_role"
    assert packets[750]["packet_status"] == "reviewable_exact_operand_set"
    assert decisions[746]["decision"] == "confirm_source_blocker"
    assert decisions[750]["decision"] == "approve_exact_operand_set"
    provenance = decisions[750]["decision_provenance"]
    assert provenance["reviewer_type"] == "chatgpt_verified"
    assert provenance["reviewer_type"] != "human_verified"
    assert provenance["authority_grant"]["grant_scope"] == "exact_source_operand_review_gate_equivalence"
    assert decisions[750]["source_contract"]["may_select_value"] is False
    assert decisions[750]["source_contract"]["may_execute_formula"] is False


def test_only_q750_is_replayed_and_stays_research_only() -> None:
    receipts = {row["question_id"]: row for row in _rows(MATERIAL_DIR / "cross_entity_public_execution_receipts_v1.jsonl")}
    private = _rows(MATERIAL_DIR / "cross_entity_private_execution_v1.jsonl")
    assert receipts[746]["execution_status"] == "dependency_blocked"
    assert receipts[750]["execution_status"] == "execution_replay_ready_research_only"
    assert "execution_value_decimal" not in receipts[750]
    assert [row["question_id"] for row in private] == [750]
    assert private[0]["source_contract"]["answer_materialization_allowed"] is False
    assert private[0]["source_contract"]["release_authorized"] is False


def test_q746_dual_chatgpt_consensus_proves_khg_role_without_numeric_exposure() -> None:
    packet = _rows(Q746_PACKET_DIR / "cross_entity_operand_review_packets_v1.jsonl")[0]
    khg = next(row for row in packet["operand_evidence"] if row["entity"] == "KHG")
    assert packet["packet_status"] == "reviewable_exact_operand_set"
    assert khg["entity_role"]["status"] == "candidate"
    assert khg["entity_role"]["evidence"]["line_number"] == 284
    assert "hai (02) công ty con" in khg["entity_role"]["evidence"]["line_text"]

    proposal = _rows(Q746_PROPOSAL_DIR / "cross_entity_operand_chatgpt_decisions_v1.jsonl")[0]
    critic = _rows(Q746_CRITIC_DIR / "cross_entity_operand_chatgpt_decisions_v1.jsonl")[0]
    final = _rows(Q746_ADJUDICATION_DIR / "cross_entity_operand_chatgpt_decisions_v1.jsonl")[0]
    assert proposal["decision_provenance"]["reviewer_id"] != critic["decision_provenance"]["reviewer_id"]
    assert proposal["decision_provenance"]["reviewer_role"].endswith("proposer")
    assert critic["decision_provenance"]["reviewer_role"].endswith("critic")
    assert final["decision"] == "approve_exact_operand_set"
    assert final["proposal_decision_sha256"] == proposal["decision_sha256"]
    assert final["critic_decision_sha256"] == critic["decision_sha256"]
    assert final["decision_provenance"]["reviewer_type"] == "chatgpt_verified"
    assert final["decision_provenance"]["verification_authority"] == "human_equivalent"

    public = (
        (Q746_PACKET_DIR / "cross_entity_operand_review_packets_v1.jsonl").read_text()
        + (Q746_PROPOSAL_DIR / "cross_entity_operand_chatgpt_decisions_v1.jsonl").read_text()
        + (Q746_CRITIC_DIR / "cross_entity_operand_chatgpt_decisions_v1.jsonl").read_text()
        + (Q746_MATERIAL_DIR / "cross_entity_public_execution_receipts_v1.jsonl").read_text()
    )
    private = _rows(Q746_MATERIAL_DIR / "cross_entity_executor_registry_v1.jsonl")
    assert len(private) == 2
    assert all(row["raw_decimal_candidate"] not in public for row in private)


def test_q746_dual_review_disagreement_remains_fail_closed(tmp_path: Path) -> None:
    critic_rows = _rows(Q746_CRITIC_DIR / "cross_entity_operand_chatgpt_decisions_v1.jsonl")
    critic_rows[0]["decision"] = "reject_exact_operand_set"
    critic_rows[0]["reason_codes"] = ["CRITIC_REJECTED_ROLE_COREFERENCE"]
    critic_rows[0]["semantic_checks"]["entity_role_parent_proven"] = False
    critic_rows[0]["source_contract"]["operand_review_gate_authorized"] = False
    critic_rows[0]["source_contract"]["eligible_for_deterministic_materialization"] = False
    _rehash_record(critic_rows[0], "decision_sha256")
    critic_path = tmp_path / "critic.jsonl"
    _write_rows(critic_path, critic_rows)
    critic_manifest = json.loads(
        (Q746_CRITIC_DIR / "cross_entity_operand_chatgpt_decisions_v1.manifest.json").read_text()
    )
    critic_manifest["outputs"]["decisions"]["sha256"] = _sha(critic_path)
    critic_manifest_path = tmp_path / "critic.manifest.json"
    critic_manifest_path.write_text(json.dumps(critic_manifest))
    result = reconcile_chatgpt_decisions(
        packets=Q746_PACKET_DIR / "cross_entity_operand_review_packets_v1.jsonl",
        packet_manifest=Q746_PACKET_DIR / "cross_entity_operand_review_packets_v1.manifest.json",
        proposal_decisions=Q746_PROPOSAL_DIR / "cross_entity_operand_chatgpt_decisions_v1.jsonl",
        proposal_manifest=Q746_PROPOSAL_DIR / "cross_entity_operand_chatgpt_decisions_v1.manifest.json",
        critic_decisions=critic_path,
        critic_manifest=critic_manifest_path,
        output_dir=tmp_path / "reconciled",
    )
    final = _rows(Path(result["outputs"]["decisions"]["path"]))[0]
    assert final["decision"] == "confirm_review_disagreement"
    assert final["source_contract"]["eligible_for_deterministic_materialization"] is False


def test_approval_requires_all_seven_semantic_checks(tmp_path: Path) -> None:
    spec = json.loads(REVIEW_SPEC.read_text(encoding="utf-8"))
    next(row for row in spec["reviews"] if row["question_id"] == 750)["semantic_checks"]["entity_role_parent_proven"] = False
    spec_path = tmp_path / "review.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CrossEntityOperandReviewError, match="approval contains a failed check"):
        build_chatgpt_decisions(
            packets=PACKET_DIR / "cross_entity_operand_review_packets_v1.jsonl",
            packet_manifest=PACKET_DIR / "cross_entity_operand_review_packets_v1.manifest.json",
            review_spec=spec_path,
            output_dir=tmp_path / "out",
        )


def test_blocked_packet_cannot_be_approved(tmp_path: Path) -> None:
    spec = json.loads(REVIEW_SPEC.read_text(encoding="utf-8"))
    review = next(row for row in spec["reviews"] if row["question_id"] == 746)
    review["decision"] = "approve_exact_operand_set"
    review["reason_codes"] = []
    review["semantic_checks"]["entity_role_parent_proven"] = True
    spec_path = tmp_path / "review.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CrossEntityOperandReviewError, match="blocked packet cannot be approved"):
        build_chatgpt_decisions(
            packets=PACKET_DIR / "cross_entity_operand_review_packets_v1.jsonl",
            packet_manifest=PACKET_DIR / "cross_entity_operand_review_packets_v1.manifest.json",
            review_spec=spec_path,
            output_dir=tmp_path / "out",
        )


def test_materializer_rejects_authority_escalation(tmp_path: Path) -> None:
    kwargs = _materialize_kwargs(tmp_path)
    decisions = _rows(kwargs["decisions"])
    approved = next(row for row in decisions if row["question_id"] == 750)
    approved["source_contract"]["may_execute_formula"] = True
    _rehash_record(approved, "decision_sha256")
    decision_path = tmp_path / "decisions.jsonl"
    _write_rows(decision_path, decisions)
    manifest = json.loads(kwargs["decision_manifest"].read_text(encoding="utf-8"))
    manifest["outputs"]["decisions"]["sha256"] = _sha(decision_path)
    manifest_path = tmp_path / "decision.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    kwargs.update(decisions=decision_path, decision_manifest=manifest_path)
    with pytest.raises(CrossEntityOperandReviewError, match="decision authority is invalid"):
        materialize_and_execute(**kwargs)


def test_materializer_rejects_value_cell_hash_drift(tmp_path: Path) -> None:
    kwargs = _materialize_kwargs(tmp_path)
    packets = _rows(kwargs["packets"])
    packet = next(row for row in packets if row["question_id"] == 750)
    packet["operand_evidence"][0]["lineage"]["value_cell_sha256"] = "0" * 64
    _rehash_record(packet, "packet_sha256")
    packet_path = tmp_path / "packets.jsonl"
    _write_rows(packet_path, packets)
    packet_manifest = json.loads(kwargs["packet_manifest"].read_text(encoding="utf-8"))
    packet_manifest["outputs"]["packets"]["sha256"] = _sha(packet_path)
    packet_manifest_path = tmp_path / "packet.manifest.json"
    packet_manifest_path.write_text(json.dumps(packet_manifest), encoding="utf-8")

    decisions = _rows(kwargs["decisions"])
    decision = next(row for row in decisions if row["question_id"] == 750)
    decision["packet_sha256"] = packet["packet_sha256"]
    _rehash_record(decision, "decision_sha256")
    decision_path = tmp_path / "decisions.jsonl"
    _write_rows(decision_path, decisions)
    decision_manifest = json.loads(kwargs["decision_manifest"].read_text(encoding="utf-8"))
    decision_manifest["outputs"]["decisions"]["sha256"] = _sha(decision_path)
    decision_manifest_path = tmp_path / "decision.manifest.json"
    decision_manifest_path.write_text(json.dumps(decision_manifest), encoding="utf-8")

    kwargs.update(
        packets=packet_path,
        packet_manifest=packet_manifest_path,
        decisions=decision_path,
        decision_manifest=decision_manifest_path,
    )
    with pytest.raises(CrossEntityOperandReviewError, match="value cell hash drift"):
        materialize_and_execute(**kwargs)
