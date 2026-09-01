from __future__ import annotations

from pathlib import Path

from finance_query.e2e.core.proposal_verifier import (
    load_certificate_index,
    select_best_proposal,
    verify_proposed_answer,
)


def _tables() -> dict[str, dict]:
    return {
        "table-1": {
            "document_id": "VGT_2024_separate",
            "rows": [["Vốn cổ phần", "5,000,000,000,000"]],
        }
    }


def _proposal(**overrides: object) -> dict:
    proposal = {
        "proposal_id": "q123:formula:1",
        "answer_decimal": "5",
        "evidence": [
            {
                "document_id": "VGT_2024_separate",
                "internal_table_uid": "table-1",
                "row_index": 0,
                "column_index": 1,
                "raw_value": "5,000,000,000,000",
            }
        ],
    }
    proposal.update(overrides)
    return proposal


def test_local_verifier_binds_coordinates_but_does_not_self_authorize_semantics() -> None:
    result = verify_proposed_answer(_proposal(), tables_by_uid=_tables())
    assert result["verification_class"] == "PARTIAL"
    assert result["checks"]["source_binding"] == "PASS"
    assert result["checks"]["semantic_binding"] == "UNRESOLVED"
    assert result["authority"] == "none"


def test_verifier_rejects_a_claimed_value_that_disagrees_with_the_source() -> None:
    result = verify_proposed_answer(
        _proposal(evidence=[{**_proposal()["evidence"][0], "raw_value": "7"}]),
        tables_by_uid=_tables(),
    )
    assert result["verification_class"] == "REJECTED"
    assert {failure["reason"] for failure in result["failures"]} == {"RESULT_INCONSISTENT"}


def test_complete_e2e_certificate_is_the_only_local_upgrade_to_verified() -> None:
    result = verify_proposed_answer(
        _proposal(),
        tables_by_uid=_tables(),
        strict_certificate={
            "status": "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY",
            "answer": "5",
        },
    )
    assert result["verification_class"] == "VERIFIED"
    assert all(value == "PASS" for value in result["checks"].values())


def test_selector_uses_verification_before_generator_score() -> None:
    selected = select_best_proposal(
        [
            {
                "proposal_id": "high-score-unresolved",
                "retrieval_score": 0.99,
                "verification": {"verification_class": "UNRESOLVED"},
            },
            {
                "proposal_id": "lower-score-partial",
                "retrieval_score": 0.01,
                "verification": {"verification_class": "PARTIAL"},
            },
            {
                "proposal_id": "rejected",
                "retrieval_score": 2.0,
                "verification": {"verification_class": "REJECTED"},
            },
        ]
    )
    assert selected is not None
    assert selected["proposal_id"] == "lower-score-partial"


def test_certificate_loader_rejects_duplicate_question_ids(tmp_path: Path) -> None:
    path = tmp_path / "certificates.jsonl"
    path.write_text(
        '{"question_id": 1, "answer_certificate": {"status": "ABSTAIN"}}\n'
        '{"question_id": 1, "answer_certificate": {"status": "ABSTAIN"}}\n',
        encoding="utf-8",
    )
    try:
        load_certificate_index(path)
    except ValueError as exc:
        assert "duplicates question_id 1" in str(exc)
    else:
        raise AssertionError("duplicate certificate rows must be rejected")
