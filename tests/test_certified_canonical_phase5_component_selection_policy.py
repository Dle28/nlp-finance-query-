"""Tests for the explicit human policy gate before Phase 5 Kaggle packaging."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.certified_canonical.phase5_component_selection import PHASE5_COMPONENT_SELECTION_PROTOCOL
from finance_query.certified_canonical.phase5_component_selection_policy import (
    COMPARISON_NAME,
    POLICY_DECISION_NAME,
    POLICY_MANIFEST_NAME,
    POLICY_REVIEW_NAME,
    POLICY_RESPONSE_PROTOCOL,
    POLICY_TEMPLATE_NAME,
    SCORE_MANIFEST_NAME,
    SCORE_NAME,
    SCORE_PROTOCOL,
    _AUTHORIZE_SMOKE,
    _KEEP_BLOCKED,
    build_phase5_component_selection_policy_review,
    resolve_phase5_component_selection_policy_review,
)
from finance_query.certified_canonical.phase5_component_selection_smoke import PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL
from finance_query.certified_canonical.pipeline import CertifiedCanonicalError


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _comparison(item_id: str, stratum: str, *, abstain: bool) -> dict[str, object]:
    match = not abstain
    return {
        "protocol": SCORE_PROTOCOL,
        "calibration_item_id": item_id,
        "review_stratum": stratum,
        "human_review": {"review_decision": "ABSTAIN_UNRESOLVED" if abstain else "SELECT_COMPONENTS"},
        "qwen_comparison": {
            "primary_match": match,
            "support_exact_match": match,
            "support_jaccard": 1.0 if match else 0.0,
        },
        "mistral_comparison": {
            "primary_match": match,
            "support_exact_match": match,
            "support_jaccard": 1.0 if match else 0.0,
        },
        "training_eligible": False,
        "certification_allowed": False,
    }


def _metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    count = len(rows)
    output: dict[str, object] = {
        "reviewed_item_count": count,
        "human_abstention_count": sum(row["human_review"]["review_decision"] == "ABSTAIN_UNRESOLVED" for row in rows),
    }
    for model in ("qwen", "mistral"):
        comparisons = [row[f"{model}_comparison"] for row in rows]
        output[f"{model}_primary_match_count"] = sum(item["primary_match"] for item in comparisons)
        output[f"{model}_support_exact_match_count"] = sum(item["support_exact_match"] for item in comparisons)
        output[f"{model}_mean_support_jaccard"] = sum(item["support_jaccard"] for item in comparisons) / count
    return output


def _fixture(root: Path) -> dict[str, Path]:
    comparison = root / COMPARISON_NAME
    rows = [
        _comparison("item-1", "EXACT_CLOSED_WORLD_AGREEMENT", abstain=False),
        _comparison("item-2", "PRIMARY_CONTEXT_AGREEMENT_SUPPORT_DIFFERENCE", abstain=True),
    ]
    comparison.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    score = root / SCORE_NAME
    _json(
        score,
        {
            "protocol": SCORE_PROTOCOL,
            "run_status": "final_review_calibration_scored_not_promoted",
            "global_metrics": _metrics(rows),
            "stratum_metrics": {
                stratum: _metrics([row for row in rows if row["review_stratum"] == stratum])
                for stratum in ("EXACT_CLOSED_WORLD_AGREEMENT", "PRIMARY_CONTEXT_AGREEMENT_SUPPORT_DIFFERENCE")
            },
            "training_eligible_output_count": 0,
            "certification_allowed": False,
            "next_gate": "explicit_policy_review_of_calibration_error_and_sample_size_before_any_bounded_pilot",
        },
    )
    score_manifest = root / SCORE_MANIFEST_NAME
    _json(
        score_manifest,
        {
            "protocol": SCORE_PROTOCOL,
            "run_status": "final_review_calibration_scored_not_promoted",
            "outputs": {SCORE_NAME: {"sha256": _sha(score)}, COMPARISON_NAME: {"sha256": _sha(comparison)}},
            "training_eligible": False,
            "certification_allowed": False,
        },
    )
    source_manifest = root / "source-packets-manifest.json"
    _json(
        source_manifest,
        {
            "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
            "navigation_overlay_required": True,
            "inputs": {
                "report_navigation_overlay_v1.jsonl": {"sha256": "a" * 64},
                "report_navigation_overlay_manifest.json": {"sha256": "b" * 64},
            },
            "training_eligible": False,
            "certification_allowed": False,
        },
    )
    packets = root / "component_selection_smoke_packets_v1.jsonl"
    packets.write_text('{"component_selection_packet_id":"packet-1"}\n', encoding="utf-8")
    selected_manifest = root / "component_selection_smoke_packet_manifest.json"
    _json(
        selected_manifest,
        {
            "protocol": PHASE5_COMPONENT_SELECTION_PROTOCOL,
            "run_status": "phase_5_component_selection_packets_complete_not_dispatched",
            "navigation_overlay_required": True,
            "outputs": {packets.name: {"sha256": _sha(packets)}},
            "training_eligible": False,
            "certification_allowed": False,
        },
    )
    requests = root / "component_selection_smoke_requests_v1.jsonl"
    requests.write_text("".join(json.dumps({"component_selection_request_id": f"request-{index}"}) + "\n" for index in range(5)), encoding="utf-8")
    job_manifest = root / "component_selection_smoke_job_manifest.json"
    _json(
        job_manifest,
        {
            "protocol": PHASE5_COMPONENT_SELECTION_SMOKE_PROTOCOL,
            "run_status": "prepared_component_selection_smoke_not_executed",
            "model_execution_allowed": True,
            "navigation_overlay_required": True,
            "inputs": {"packet_manifest": {"path": str(source_manifest), "sha256": _sha(source_manifest)}},
            "outputs": {
                selected_manifest.name: {"sha256": _sha(selected_manifest)},
                requests.name: {"sha256": _sha(requests)},
            },
            "training_eligible": False,
            "certification_allowed": False,
        },
    )
    return {
        "score": score,
        "score_manifest": score_manifest,
        "comparison": comparison,
        "candidate_job_manifest": job_manifest,
    }


def _response(package_dir: Path, *, decision: str, candidate_sha: str | None) -> Path:
    review = json.loads((package_dir / POLICY_REVIEW_NAME).read_text(encoding="utf-8"))
    response = {
        "schema_version": 1,
        "protocol": POLICY_RESPONSE_PROTOCOL,
        "immutable_policy_review_sha256": hashlib.sha256(
            json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "reviewer_id": "human-policy-reviewer",
        "reviewed_at_utc": "2026-08-18T08:00:00Z",
        "policy_decision": decision,
        "acknowledge_calibration_evidence": True,
        "acknowledge_candidate_lineage": True,
        "approved_candidate_job_manifest_sha256": candidate_sha,
        "rationale": "The calibration result and the immutable candidate lineage were reviewed deliberately.",
        "training_eligible": False,
        "certification_allowed": False,
    }
    path = package_dir.parent / f"{decision}.json"
    _json(path, response)
    return path


def test_builds_pending_policy_package_and_keeps_dispatch_blocked(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    package = tmp_path / "policy-package"
    result = build_phase5_component_selection_policy_review(**paths, output_dir=package)

    assert result["run_status"] == "policy_decision_pending"
    assert result["kaggle_dataset_packaging_allowed"] is False
    assert result["recommendation"]["policy_decision"] == _KEEP_BLOCKED
    assert "CALIBRATION_SAMPLE_BELOW_RECOMMENDED_MINIMUM" in result["recommendation"]["reason_codes"]
    response = _response(package, decision=_KEEP_BLOCKED, candidate_sha=None)
    decision = resolve_phase5_component_selection_policy_review(
        policy_review=package / POLICY_REVIEW_NAME,
        policy_template=package / POLICY_TEMPLATE_NAME,
        policy_manifest=package / POLICY_MANIFEST_NAME,
        response=response,
        output_dir=tmp_path / "blocked-decision",
    )
    assert decision["run_status"] == "dispatch_remains_blocked"
    assert decision["kaggle_dataset_packaging_allowed"] is False
    assert (tmp_path / "blocked-decision" / POLICY_DECISION_NAME).is_file()


def test_requires_exact_five_request_smoke_for_human_exception(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    package = tmp_path / "policy-package"
    review = build_phase5_component_selection_policy_review(**paths, output_dir=package)
    response = _response(
        package,
        decision=_AUTHORIZE_SMOKE,
        candidate_sha=str(review["candidate_job"]["job_manifest_sha256"]),
    )
    decision = resolve_phase5_component_selection_policy_review(
        policy_review=package / POLICY_REVIEW_NAME,
        policy_template=package / POLICY_TEMPLATE_NAME,
        policy_manifest=package / POLICY_MANIFEST_NAME,
        response=response,
        output_dir=tmp_path / "authorized-decision",
    )
    assert decision["run_status"] == "bounded_smoke_explicitly_authorized_not_dispatched"
    assert decision["kaggle_dataset_packaging_allowed"] is True


def test_rejects_candidate_without_navigation_provenance(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    source_manifest = tmp_path / "source-packets-manifest.json"
    payload = json.loads(source_manifest.read_text(encoding="utf-8"))
    payload.pop("navigation_overlay_required")
    _json(source_manifest, payload)
    job_manifest = paths["candidate_job_manifest"]
    job = json.loads(job_manifest.read_text(encoding="utf-8"))
    job["inputs"]["packet_manifest"]["sha256"] = _sha(source_manifest)
    _json(job_manifest, job)

    with pytest.raises(CertifiedCanonicalError, match="navigation provenance"):
        build_phase5_component_selection_policy_review(**paths, output_dir=tmp_path / "policy-package")
