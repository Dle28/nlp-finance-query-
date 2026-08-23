from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "artifacts" / "research" / "computational_semantics_v2" / "review_campaign_v1"
SPEC = importlib.util.spec_from_file_location(
    "computational_semantic_review_campaign_status",
    ROOT / "scripts" / "build_computational_semantic_review_campaign_status_v1.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
VERIFY_SPEC = importlib.util.spec_from_file_location(
    "verify_computational_semantic_human_reviews",
    ROOT / "scripts" / "verify_computational_semantic_human_reviews_v1.py",
)
assert VERIFY_SPEC is not None and VERIFY_SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(VERIFY)


def kwargs(tmp_path: Path) -> dict[str, Path]:
    return {
        "campaign": CAMPAIGN / "computational_semantic_single_reviewer_campaign_v1.jsonl",
        "campaign_manifest": CAMPAIGN / "computational_semantic_single_reviewer_campaign_v1.manifest.json",
        "output": tmp_path / "status.json",
    }


def test_campaign_status_records_pending_human_receipts_without_materializing(tmp_path: Path) -> None:
    result = MODULE.build(**kwargs(tmp_path))
    persisted = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert persisted == result
    assert result["campaign_status"] == "blocked_awaiting_human_response_receipts"
    assert result["counts"] == {
        "campaign_work_item_count": 49,
        "awaiting_human_response_count": 49,
        "phase_receipt_count": 0,
        "accepted_count": 0,
    }
    assert all(phase["status"] == "awaiting_human_response_receipt" for phase in result["phases"])
    assert result["materialization_allowed"] is False
    assert result["submission_eligible"] is False


def test_campaign_status_rejects_tampered_campaign_and_overwrite(tmp_path: Path) -> None:
    args = kwargs(tmp_path)
    tampered = tmp_path / "campaign.jsonl"
    tampered.write_text(args["campaign"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    args["campaign"] = tampered
    with pytest.raises(ValueError, match="Campaign manifest is not"):
        MODULE.build(**args)
    args = kwargs(tmp_path / "overwrite")
    args["output"].parent.mkdir(parents=True)
    args["output"].write_text("exists", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        MODULE.build(**args)


def _abstain_response(packet: dict, review_kind: str) -> dict:
    response = {
        "schema_version": 1,
        "protocol": VERIFY.RESPONSE_PROTOCOL,
        "review_kind": review_kind,
        "question_id": packet["question_id"],
        "immutable_review_context_sha256": packet["immutable_review_context_sha256"],
        "decision": "abstain",
        "decision_provenance": "human_verified",
        "reviewer_id": "human-1",
        "reviewed_at": "2026-08-15T00:00:00Z",
        "source_coordinates_checked": [],
        "notes": "Could not establish the required source contract.",
        "is_blank_template": False,
        "materialization_allowed": False,
        "source_contract": packet["source_contract"],
    }
    if review_kind == "source_coordinate":
        return {**response, "period_unit_dimension_checked": None}
    if review_kind == "scope":
        return {
            **response,
            "proposed_scope": None,
            "scope_evidence_kind": None,
            "period_unit_dimension_checked": None,
        }
    return {**response, "proposed_dimension_contract": None, "dimension_evidence_kind": None}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def test_campaign_status_accepts_only_verified_phase_receipts_and_stays_non_promotable(tmp_path: Path) -> None:
    campaign_manifest = json.loads((CAMPAIGN / "computational_semantic_single_reviewer_campaign_v1.manifest.json").read_text())
    phase_specs = [
        ("p1_direct_source_coordinates", "source_coordinate", "source_receipt"),
        ("p2_scope_resolution", "scope", "scope_receipt"),
        ("p3_component_source_coordinates", "source_coordinate", "component_receipt"),
        ("p4_dimension_contracts", "dimension", "dimension_receipt"),
    ]
    receipt_args: dict[str, Path] = {}
    for phase, review_kind, receipt_arg in phase_specs:
        metadata = campaign_manifest["inputs"][phase]
        queue = Path(metadata["queue"]["path"])
        queue_manifest = Path(metadata["queue_manifest"]["path"])
        packets = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines()]
        responses = tmp_path / f"{phase}.responses.jsonl"
        _write_jsonl(responses, [_abstain_response(packet, review_kind) for packet in packets])
        receipt = tmp_path / f"{phase}.receipt.json"
        VERIFY.verify(
            queue=queue,
            queue_manifest=queue_manifest,
            completed_responses=responses,
            review_kind=review_kind,
            reviewer_id="human-1",
            output=receipt,
        )
        receipt_args[receipt_arg] = receipt
    result = MODULE.build(**kwargs(tmp_path), **receipt_args)
    assert result["campaign_status"] == "all_phase_receipts_verified_non_materializable"
    assert result["counts"]["awaiting_human_response_count"] == 0
    assert result["counts"]["phase_receipt_count"] == 4
    assert result["counts"]["accepted_count"] == 0
    assert all(phase["status"] == "receipt_verified_non_materializable" for phase in result["phases"])
    assert result["production_eligible"] is False
    assert result["promotion_allowed"] is False
