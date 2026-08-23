"""Tests for the local-only Phase 5 policy review UI helpers."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "local" / "ccl_phase5_policy_review_ui.py"
SPEC = importlib.util.spec_from_file_location("ccl_phase5_policy_review_ui", SCRIPT)
assert SPEC and SPEC.loader
ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ui)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def test_loads_hash_bound_package_and_builds_blocked_response(tmp_path: Path) -> None:
    review = {
        "protocol": ui.REVIEW_PROTOCOL,
        "run_status": "policy_decision_pending",
        "calibration_score": {"global_metrics": {"reviewed_item_count": 5, "human_abstention_count": 3}},
        "candidate_job": {"request_count": 5, "job_manifest_sha256": "c" * 64},
        "recommendation": {"reason_codes": ["CALIBRATION_SAMPLE_BELOW_RECOMMENDED_MINIMUM"]},
        "kaggle_dataset_packaging_allowed": False,
        "model_execution_allowed": False,
        "training_eligible": False,
        "certification_allowed": False,
    }
    review_path = tmp_path / ui.REVIEW_NAME
    _write(review_path, review)
    template = {
        "schema_version": 1,
        "protocol": ui.RESPONSE_PROTOCOL,
        "immutable_policy_review_sha256": ui.canonical_sha(review),
        "reviewer_id": None,
        "reviewed_at_utc": None,
        "policy_decision": None,
        "acknowledge_calibration_evidence": None,
        "acknowledge_candidate_lineage": None,
        "approved_candidate_job_manifest_sha256": None,
        "rationale": None,
        "training_eligible": False,
        "certification_allowed": False,
    }
    template_path = tmp_path / ui.TEMPLATE_NAME
    _write(template_path, template)
    manifest_path = tmp_path / ui.MANIFEST_NAME
    _write(
        manifest_path,
        {
            "protocol": ui.REVIEW_PROTOCOL,
            "run_status": "policy_decision_pending",
            "outputs": {ui.REVIEW_NAME: {"sha256": _sha(review_path)}, ui.TEMPLATE_NAME: {"sha256": _sha(template_path)}},
            "training_eligible": False,
            "certification_allowed": False,
        },
    )

    loaded_review, loaded_template = ui.load_policy_package(review_path, template_path, manifest_path)
    response = ui.build_response(
        loaded_review,
        loaded_template,
        {
            "reviewer_id": "reviewer-1",
            "policy_decision": ui.KEEP_BLOCKED,
            "rationale": "The small review sample needs additional independent calibration evidence.",
            "acknowledge_calibration_evidence": True,
            "acknowledge_candidate_lineage": True,
        },
    )
    assert response["approved_candidate_job_manifest_sha256"] is None
    assert response["training_eligible"] is False


def test_authorize_smoke_response_binds_exact_candidate_job(tmp_path: Path) -> None:
    review = {"candidate_job": {"job_manifest_sha256": "d" * 64}}
    template = {
        "schema_version": 1,
        "protocol": ui.RESPONSE_PROTOCOL,
        "immutable_policy_review_sha256": ui.canonical_sha(review),
        "reviewer_id": None,
        "reviewed_at_utc": None,
        "policy_decision": None,
        "acknowledge_calibration_evidence": None,
        "acknowledge_candidate_lineage": None,
        "approved_candidate_job_manifest_sha256": None,
        "rationale": None,
        "training_eligible": False,
        "certification_allowed": False,
    }
    response = ui.build_response(
        review,
        template,
        {
            "reviewer_id": "reviewer-1",
            "policy_decision": ui.AUTHORIZE_SMOKE,
            "rationale": "This is an explicit limited exception for the reviewed five-request smoke only.",
            "acknowledge_calibration_evidence": True,
            "acknowledge_candidate_lineage": True,
        },
    )
    assert response["approved_candidate_job_manifest_sha256"] == "d" * 64


def test_policy_page_explains_the_operational_decision() -> None:
    page = ui._render_page(
        {
            "calibration_score": {"global_metrics": {"reviewed_item_count": 5, "human_abstention_count": 3}},
            "candidate_job": {"request_count": 5, "job_manifest_sha256": "e" * 64},
            "recommendation": {"reason_codes": ["CALIBRATION_SAMPLE_BELOW_RECOMMENDED_MINIMUM"]},
        }
    )
    assert "Ban dang review gi?" in page
    assert "khong phai man hinh review bang du lieu" in page
    assert "Giu chan va mo rong calibration" in page
