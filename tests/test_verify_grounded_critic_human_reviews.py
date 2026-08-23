from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "verify_grounded_critic_human_reviews", ROOT / "scripts" / "verify_grounded_critic_human_reviews.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path) -> dict[str, Path | str]:
    contract = {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_candidate": False,
        "may_select_value": False,
        "may_execute_formula": False,
    }
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"protocol": "grounded_critic_gpu_run_audit_v2", "audit_passed": True, "source_contract": contract}))
    packet = {"question_id": 6, "question_context": {"scope": "separate"}}
    immutable_packet = hashlib.sha256(
        json.dumps(packet, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assignment = tmp_path / "assignment.jsonl"
    _write_jsonl(
        assignment,
        [{
            "schema_version": 1,
            "protocol": "grounded_critic_independent_review_assignment_v1",
            "assignment_id": "critic-v2-reviewer_a-q6",
            "reviewer_slot": "reviewer_a",
            "question_id": 6,
            "immutable_packet_sha256": immutable_packet,
            "packet": packet,
            "source_contract": contract,
        }],
    )
    assignment_manifest = tmp_path / "assignment.manifest.json"
    assignment_manifest.write_text(
        json.dumps(
            {
                "protocol": "grounded_critic_independent_review_assignment_v1",
                "blind_to_qwen_decision": True,
                "labels_prepopulated": False,
                "assignment_count_per_reviewer": 1,
                "source_contract": contract,
                "inputs": {"gpu_run_audit": {"path": str(audit), "sha256": _sha(audit)}},
                "outputs": {"reviewer_a": {"assignment_sha256": _sha(assignment)}},
            }
        ),
        encoding="utf-8",
    )
    labels = tmp_path / "labels.jsonl"
    _write_jsonl(
        labels,
        [{
            "schema_version": 1,
            "protocol": "grounded_critic_independent_label_v1",
            "assignment_id": "critic-v2-reviewer_a-q6",
            "question_id": 6,
            "immutable_packet_sha256": immutable_packet,
            "status": "accept",
            "provenance": "human_verified",
            "reviewer_id": "human-a",
            "reviewed_at": "2026-08-13T00:00:00Z",
            "source_coordinates_checked": True,
            "source_coordinate_agree": True,
            "unit_period_agree": True,
            "deterministic_replay_agree": True,
            "unsupported_evidence": False,
            "notes": "Reopened raw source coordinate and replayed the bounded trace.",
            "is_blank_template": False,
            "source_contract": contract,
        }],
    )
    return {
        "assignment": assignment,
        "assignment_manifest": assignment_manifest,
        "completed_labels": labels,
        "reviewer_slot": "reviewer_a",
        "reviewer_id": "human-a",
    }


def test_human_review_verification_is_hash_bound_and_non_promotable(tmp_path: Path) -> None:
    kwargs = _fixture(tmp_path)
    result = mod.verify(**kwargs, output=tmp_path / "review.manifest.json")
    assert result["protocol"] == "grounded_critic_independent_human_review_v1"
    assert result["counts"] == {"label_count": 1, "status_counts": {"accept": 1}}
    assert result["blind_to_qwen_decision"] is True
    assert result["source_contract"]["promotion_allowed"] is False


def test_human_review_verification_rejects_blank_or_unreviewed_label(tmp_path: Path) -> None:
    kwargs = _fixture(tmp_path)
    labels = [json.loads(line) for line in Path(kwargs["completed_labels"]).read_text().splitlines()]
    labels[0]["is_blank_template"] = True
    _write_jsonl(Path(kwargs["completed_labels"]), labels)
    with pytest.raises(ValueError, match="does not bind"):
        mod.verify(**kwargs, output=tmp_path / "review.manifest.json")
