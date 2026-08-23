from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from finance_query.metadata_human_approval import (
    canonical_sha256,
    materialize_metadata_human_approvals,
    sha256_file,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _source_review(question_id: int) -> dict:
    return {
        "question_id": question_id,
        "stage_id": "stage_1_metric",
        "role": "metric",
        "decision": "accept_repair",
        "source_coordinates_checked": True,
        "authoritative_sources": [{"source_kind": "issuer_financial_statement", "path": "issuer.txt"}],
        "proposed_patch": {
            "scope": "metadata",
            "field": "sector",
            "operations": [{"document_id": f"DOC-{question_id}", "old_value": "unknown", "proposed_value": "industrial"}],
            "reason": "issuer disclosure",
        },
    }


def _fixture(tmp_path: Path, *, count: int = 2) -> dict[str, Path]:
    reviews = tmp_path / "reviews.jsonl"
    review_rows = [_source_review(question_id) for question_id in range(1, count + 1)]
    _write_jsonl(reviews, review_rows)
    review_manifest = tmp_path / "reviews.manifest.json"
    review_manifest.write_text(
        json.dumps({"outputs": {"metadata_reviews": {"sha256": sha256_file(reviews)}}}), encoding="utf-8"
    )
    queue = tmp_path / "queue.jsonl"
    queue_rows = [
        {
            "source_review_sha256": canonical_sha256(review),
            "source_review_file_sha256": sha256_file(reviews),
            "question_id": review["question_id"],
            "stage_id": review["stage_id"],
            "role": review["role"],
            "proposed_patch": review["proposed_patch"],
            "human_verified": False,
            "reviewer_decision": None,
            "eligible_for_materialization": False,
        }
        for review in review_rows
    ]
    _write_jsonl(queue, queue_rows)
    queue_manifest = tmp_path / "queue.manifest.json"
    queue_manifest.write_text(
        json.dumps(
            {
                "inputs": {"metadata_reviews": {"sha256": sha256_file(reviews)}},
                "outputs": {"queue": {"sha256": sha256_file(queue)}},
            }
        ),
        encoding="utf-8",
    )
    return {"reviews": reviews, "review_manifest": review_manifest, "queue": queue, "queue_manifest": queue_manifest}


def _decision(source_sha: str, queue: Path, decision: str = "approve") -> dict:
    return {
        "source_review_sha256": source_sha,
        "source_approval_queue_sha256": sha256_file(queue),
        "decision": decision,
        "decision_provenance": {"reviewer_type": "human_verified", "reviewer_id": "reviewer-1"},
        "reviewed_at": "2026-08-12T15:00:00+07:00",
        "source_coordinates_checked": decision == "approve",
        "notes": "Reviewed against the cited issuer source.",
    }


def _run(tmp_path: Path, fixture: dict[str, Path], decisions: list[dict]) -> dict:
    decision_path = tmp_path / "human-decisions.jsonl"
    _write_jsonl(decision_path, decisions)
    return materialize_metadata_human_approvals(
        review_manifest_path=fixture["review_manifest"],
        metadata_reviews_path=fixture["reviews"],
        approval_queue_manifest_path=fixture["queue_manifest"],
        approval_queue_path=fixture["queue"],
        decisions_path=decision_path,
        output_dir=tmp_path / "out",
    )


def test_human_verified_approval_creates_only_metadata_overlay_candidate(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    review_rows = [json.loads(line) for line in fixture["reviews"].read_text().splitlines()]
    result = _run(tmp_path, fixture, [_decision(canonical_sha256(row), fixture["queue"]) for row in review_rows])
    assert result["counts"] == {
        "proposal_count": 2,
        "decision_count": 2,
        "approved_count": 2,
        "rejected_or_unresolved_count": 0,
        "decision_counts": {"approve": 2},
    }
    rows = [json.loads(line) for line in Path(result["outputs"]["approved"]["path"]).read_text().splitlines()]
    assert all(row["eligible_for_metadata_overlay"] is True for row in rows)
    assert all(row["source_contract"]["evidence_eligible"] is False for row in rows)
    assert all(row["source_contract"]["promotion_allowed"] is False for row in rows)


def test_reject_and_uncertain_remain_non_materializable(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    review_rows = [json.loads(line) for line in fixture["reviews"].read_text().splitlines()]
    result = _run(
        tmp_path,
        fixture,
        [
            _decision(canonical_sha256(review_rows[0]), fixture["queue"], "reject"),
            _decision(canonical_sha256(review_rows[1]), fixture["queue"], "uncertain"),
        ],
    )
    assert result["counts"]["approved_count"] == 0
    unresolved = [json.loads(line) for line in Path(result["outputs"]["unresolved"]["path"]).read_text().splitlines()]
    assert {row["reason_code"] for row in unresolved} == {"HUMAN_REJECTED", "HUMAN_UNCERTAIN"}
    assert all(row["eligible_for_metadata_overlay"] is False for row in unresolved)


def test_ai_provenance_fails_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    review_rows = [json.loads(line) for line in fixture["reviews"].read_text().splitlines()]
    decision = _decision(canonical_sha256(review_rows[0]), fixture["queue"])
    decision["decision_provenance"]["reviewer_type"] = "independent_ai_source_review"
    with pytest.raises(ValueError, match="human_verified"):
        _run(tmp_path, fixture, [decision])


def test_decision_sidecar_must_cover_every_proposal_once(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    review_rows = [json.loads(line) for line in fixture["reviews"].read_text().splitlines()]
    with pytest.raises(ValueError, match="cover every frozen approval proposal"):
        _run(tmp_path, fixture, [_decision(canonical_sha256(review_rows[0]), fixture["queue"])])
