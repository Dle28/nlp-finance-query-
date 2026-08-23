from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "artifacts" / "research" / "production_coverage_iteration_v5"
ROUTE = ROOT / "artifacts" / "research" / "route_coverage_adjudication_v1"
R14 = V5 / "kaggle_run_20260812_source_bound_r14" / "vifinqa_grounded_critic_v2"

spec = importlib.util.spec_from_file_location(
    "production_coverage_v5_readiness", ROOT / "scripts" / "build_production_coverage_v5_readiness.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _kwargs(tmp_path: Path) -> dict[str, Path]:
    return {
        "critic_packets": V5 / "grounded_critic_packets_v2.jsonl",
        "critic_packets_manifest": V5 / "grounded_critic_packets_v2.manifest.json",
        "critic_results": R14 / "qwen14_grounded_critic_results_v2.jsonl",
        "critic_results_manifest": R14 / "qwen14_grounded_critic_results_v2.manifest.json",
        "gpu_run_audit": R14 / "grounded_critic_gpu_run_audit_v2.json",
        "calibration_manifest": V5 / "calibration_source_bound_r14_audited_v2" / "grounded_critic_calibration_v1.manifest.json",
        "calibration_score_manifest": V5 / "calibration_source_bound_r14_audited_v2" / "scored" / "grounded_critic_calibration_v1.manifest.json",
        "critic_assignments_manifest": V5 / "independent_review_assignments_source_bound_r14_audited_v2" / "grounded_critic_independent_review_assignments_v1.manifest.json",
        "route_queue": ROUTE / "route_coverage_adjudication_queue_v1.jsonl",
        "route_queue_manifest": ROUTE / "route_coverage_adjudication_v1.manifest.json",
        "route_assignments_manifest": ROUTE / "independent_review_assignments_v1" / "route_coverage_independent_review_assignments_v1.manifest.json",
        "output": tmp_path / "readiness.json",
    }


def test_v5_readiness_is_hash_bound_and_explicitly_blocked(tmp_path: Path) -> None:
    result = mod.build(**_kwargs(tmp_path))
    assert result["readiness_status"] == "blocked"
    assert result["production_eligible"] is False
    assert result["counts"] == {
        "audited_critic_packet_count": 14,
        "critic_independent_label_count": 0,
        "critic_needs_human_count": 14,
        "critic_unresolved_count": 14,
        "route_abstain_count": 902,
        "route_assignment_count_per_reviewer": 902,
        "route_reconciliation_count": None,
        "route_consensus_candidate_count": None,
    }
    assert [blocker["code"] for blocker in result["blockers"]] == [
        "CRITIC_INDEPENDENT_REVIEW_PENDING",
        "CRITIC_CALIBRATION_POLICY_PENDING",
        "ROUTE_COVERAGE_REVIEW_PENDING",
        "ROUTE_MATERIALIZATION_FORBIDDEN",
    ]


def test_v5_readiness_rejects_audit_that_is_not_the_calibration_input(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    bad_audit = tmp_path / "bad-audit.json"
    payload = json.loads(kwargs["gpu_run_audit"].read_text())
    payload["runtime"]["gpu"] = "tampered"
    bad_audit.write_text(json.dumps(payload), encoding="utf-8")
    kwargs["gpu_run_audit"] = bad_audit
    with pytest.raises(ValueError, match="Critic calibration gpu_run_audit hash"):
        mod.build(**kwargs)


def test_v5_readiness_rejects_incomplete_route_reconciliation_pair(tmp_path: Path) -> None:
    kwargs = _kwargs(tmp_path)
    kwargs["route_reconciliation"] = tmp_path / "missing.jsonl"
    with pytest.raises(ValueError, match="supplied together"):
        mod.build(**kwargs)


def test_v5_readiness_rejects_route_consensus_without_agreed_source_coordinates(tmp_path: Path) -> None:
    reconciliation = tmp_path / "reconciliation.jsonl"
    reconciliation.write_text("", encoding="utf-8")
    reconciliation_manifest = tmp_path / "reconciliation.manifest.json"
    reconciliation_manifest.write_text(
        json.dumps(
            {
                "protocol": mod.ROUTE_RECONCILIATION_PROTOCOL,
                "reconciliation_complete": True,
                "materialization_allowed": False,
                "source_contract": {
                    "evidence_eligible": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                    "promotion_allowed": False,
                },
                "outputs": {"reconciled": {"sha256": mod.sha256_file(reconciliation)}},
                "counts": {"review_count": 902},
            }
        ),
        encoding="utf-8",
    )
    kwargs = _kwargs(tmp_path)
    kwargs["route_reconciliation"] = reconciliation
    kwargs["route_reconciliation_manifest"] = reconciliation_manifest
    with pytest.raises(ValueError, match="complete abstained queue"):
        mod.build(**kwargs)


def test_v5_consensus_validator_rejects_missing_agreed_source_coordinates(tmp_path: Path) -> None:
    handoff = tmp_path / "handoff.jsonl"
    proposal = {"proposed_question_plan": {"scope": "consolidated"}}
    proposal_sha = mod.canonical_sha256(proposal)
    handoff.write_text(
        json.dumps(
            {
                "protocol": mod.ROUTE_CONSENSUS_PROTOCOL,
                "question_id": 2,
                "handoff_state": "requires_source_bound_validation",
                "route_status": "abstain",
                "materialization_allowed": False,
                "consensus_proposal": proposal,
                "consensus_proposal_sha256": proposal_sha,
                "reviewer_proposal_sha256": {"reviewer_a": proposal_sha, "reviewer_b": proposal_sha},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    handoff_manifest = tmp_path / "handoff.manifest.json"
    reconciliation = {"sha256": "a" * 64, "manifest_sha256": "b" * 64}
    handoff_manifest.write_text(
        json.dumps(
            {
                "protocol": mod.ROUTE_CONSENSUS_PROTOCOL,
                "materialization_allowed": False,
                "source_contract": {
                    "evidence_eligible": False,
                    "training_eligible": False,
                    "submission_eligible": False,
                    "promotion_allowed": False,
                },
                "inputs": {
                    "reconciliation": {"sha256": reconciliation["sha256"]},
                    "reconciliation_manifest": {"sha256": reconciliation["manifest_sha256"]},
                },
                "outputs": {"handoff": {"sha256": mod.sha256_file(handoff)}},
                "counts": {"consensus_candidate_count": 1},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="lacks source coordinates"):
        mod.require_route_consensus_handoff(
            handoff=handoff,
            handoff_manifest=handoff_manifest,
            reconciliation=reconciliation,
        )
