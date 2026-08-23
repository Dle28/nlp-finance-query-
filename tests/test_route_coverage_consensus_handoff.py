from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from finance_query.route_coverage_adjudication import canonical_sha256, sha256_file, source_contract
from finance_query.route_coverage_consensus_handoff import build_consensus_handoff


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    proposal = {"proposed_question_plan": {"scope": "consolidated", "source_locator": "report.pdf#page=1"}, "proposed_taxonomy_alias": None, "proposed_operation_contract": None}
    proposal_sha = canonical_sha256(proposal)
    source_coordinates = [{"source_locator": "report.pdf#page=1", "page_no": 1}]
    source_coordinates_sha = canonical_sha256({"source_coordinates_checked": source_coordinates})
    rows = [
        {
            "schema_version": 1,
            "protocol": "route_coverage_independent_review_reconciliation_v1",
            "question_id": 2,
            "immutable_queue_payload_sha256": "a" * 64,
            "review_tracks": ["question_plan_context"],
            "reviewer_proposal_sha256": {"reviewer_a": proposal_sha, "reviewer_b": proposal_sha},
            "reviewer_source_coordinates_sha256": {"reviewer_a": source_coordinates_sha, "reviewer_b": source_coordinates_sha},
            "reconciliation_state": "agreed_accept_non_materializable",
            "proposed_route_change": proposal,
            "consensus_source_coordinates_checked": source_coordinates,
            "route_status_after_reconciliation": "abstain",
            "materialization_allowed": False,
            "source_contract": source_contract(),
        },
        {
            "schema_version": 1,
            "protocol": "route_coverage_independent_review_reconciliation_v1",
            "question_id": 5,
            "immutable_queue_payload_sha256": "b" * 64,
            "review_tracks": ["literal_concept_taxonomy"],
            "reviewer_proposal_sha256": {"reviewer_a": canonical_sha256({"proposed_question_plan": None, "proposed_taxonomy_alias": None, "proposed_operation_contract": None}), "reviewer_b": canonical_sha256({"proposed_question_plan": None, "proposed_taxonomy_alias": None, "proposed_operation_contract": None})},
            "reconciliation_state": "agreed_nonaccept_route_remains_abstain",
            "proposed_route_change": None,
            "route_status_after_reconciliation": "abstain",
            "materialization_allowed": False,
            "source_contract": source_contract(),
        },
    ]
    reconciliation = tmp_path / "reconciled.jsonl"
    _write_jsonl(reconciliation, rows)
    manifest = tmp_path / "reconciled.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "protocol": "route_coverage_independent_review_reconciliation_v1",
                "reconciliation_complete": True,
                "materialization_allowed": False,
                "source_contract": source_contract(),
                "outputs": {"reconciled": {"sha256": sha256_file(reconciliation)}},
                "counts": {"review_count": 2},
            }
        ),
        encoding="utf-8",
    )
    return reconciliation, manifest


def test_consensus_handoff_preserves_only_agreed_nonmaterializable_proposals(tmp_path: Path) -> None:
    reconciliation, manifest = _fixture(tmp_path)
    result = build_consensus_handoff(
        reconciliation=reconciliation,
        reconciliation_manifest=manifest,
        output_dir=tmp_path / "out",
    )
    assert result["counts"] == {
        "reconciliation_count": 2,
        "consensus_candidate_count": 1,
        "reconciliation_state_counts": {
            "agreed_accept_non_materializable": 1,
            "agreed_nonaccept_route_remains_abstain": 1,
        },
    }
    row = json.loads((tmp_path / "out" / "route_coverage_consensus_handoff_v1.jsonl").read_text())
    assert row["question_id"] == 2
    assert row["handoff_state"] == "requires_source_bound_validation"
    assert row["consensus_source_coordinates_checked"] == [{"source_locator": "report.pdf#page=1", "page_no": 1}]
    assert row["materialization_allowed"] is False
    assert row["source_contract"] == source_contract()


def test_consensus_handoff_rejects_unsafe_or_mismatched_proposals(tmp_path: Path) -> None:
    reconciliation, manifest = _fixture(tmp_path)
    rows = [json.loads(line) for line in reconciliation.read_text().splitlines()]
    rows[0]["proposed_route_change"]["proposed_question_plan"] = {"row_index": 4}
    _write_jsonl(reconciliation, rows)
    payload = json.loads(manifest.read_text())
    payload["outputs"]["reconciled"]["sha256"] = sha256_file(reconciliation)
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden key"):
        build_consensus_handoff(
            reconciliation=reconciliation,
            reconciliation_manifest=manifest,
            output_dir=tmp_path / "unsafe",
        )


def test_consensus_handoff_rejects_unbound_source_coordinates(tmp_path: Path) -> None:
    reconciliation, manifest = _fixture(tmp_path)
    rows = [json.loads(line) for line in reconciliation.read_text().splitlines()]
    rows[0]["consensus_source_coordinates_checked"] = [{"source_locator": "other.pdf#page=1"}]
    _write_jsonl(reconciliation, rows)
    payload = json.loads(manifest.read_text())
    payload["outputs"]["reconciled"]["sha256"] = sha256_file(reconciliation)
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="source-coordinate hash differs"):
        build_consensus_handoff(
            reconciliation=reconciliation,
            reconciliation_manifest=manifest,
            output_dir=tmp_path / "unbound-source",
        )
