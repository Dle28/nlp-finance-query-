from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_query.independent_adjudication_reviews import (
    _materialize_review,
    canonical_sha256,
    review_source_contract,
)


def _item() -> dict:
    return {
        "queue_kind": "sector_metadata",
        "diagnosis": "CANDIDATE_SECTOR_UNKNOWN",
        "immutable_source_identity": {
            "question_id": 10,
            "stage_id": "stage_1",
            "role": "financial_expense",
        },
    }


def _plan(source: Path) -> dict:
    return {
        "decision": "accept_repair",
        "decision_provenance": {
            "reviewer_type": "independent_ai_source_review",
            "reviewer_id": "test-reviewer",
        },
        "reviewed_at": "2026-08-12T00:00:00+07:00",
        "source_coordinates_checked": True,
        "authoritative_sources": [
            {
                "source_kind": "issuer_financial_statement",
                "path": source.name,
                "sha256": __import__("hashlib").sha256(source.read_bytes()).hexdigest(),
                "line_start": 1,
                "line_end": 1,
                "expected_excerpt": "issuer",
            }
        ],
        "reason_codes": ["SOURCE_CHECKED"],
        "proposed_patch": {"scope": "metadata", "field": "sector", "proposed_value": "industrial"},
    }


def test_accepted_review_is_still_non_materializable(tmp_path: Path) -> None:
    source = tmp_path / "issuer.txt"
    source.write_text("issuer source\n", encoding="utf-8")
    item = _item()
    review = _materialize_review(
        queue_name="metadata",
        queue_sha="a" * 64,
        item=item,
        plan=_plan(source),
        repository_root=tmp_path,
        structured_tables_path=tmp_path / "unused-v2.jsonl",
        evidence_context_path=tmp_path / "unused-v3.jsonl",
        tables={},
        contexts={},
    )
    assert review["decision"] == "accept_repair"
    assert review["source_item_sha256"] == canonical_sha256(item)
    assert review["eligible_for_materialization"] is False
    assert review["source_contract"] == review_source_contract()
    assert review["source_contract"]["promotion_allowed"] is False


@pytest.mark.parametrize("decision", ["reject_repair", "uncertain"])
def test_nonaccept_decision_cannot_carry_patch(tmp_path: Path, decision: str) -> None:
    source = tmp_path / "issuer.txt"
    source.write_text("issuer source\n", encoding="utf-8")
    plan = _plan(source)
    plan["decision"] = decision
    with pytest.raises(ValueError, match="must not carry proposed_patch"):
        _materialize_review(
            queue_name="metadata",
            queue_sha="a" * 64,
            item=_item(),
            plan=plan,
            repository_root=tmp_path,
            structured_tables_path=tmp_path / "unused-v2.jsonl",
            evidence_context_path=tmp_path / "unused-v3.jsonl",
            tables={},
            contexts={},
        )


def test_file_source_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "issuer.txt"
    source.write_text("issuer source\n", encoding="utf-8")
    plan = _plan(source)
    plan["authoritative_sources"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _materialize_review(
            queue_name="metadata",
            queue_sha="a" * 64,
            item=_item(),
            plan=plan,
            repository_root=tmp_path,
            structured_tables_path=tmp_path / "unused-v2.jsonl",
            evidence_context_path=tmp_path / "unused-v3.jsonl",
            tables={},
            contexts={},
        )

