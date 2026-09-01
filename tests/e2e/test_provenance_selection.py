from __future__ import annotations

from finance_query.e2e.core.provenance_selection import select_unique_provenance_candidate


EXPECTED = {"ticker": "VNM", "fiscal_year": 2023, "document_type": "BCTC"}


def candidate(candidate_id: str, *, ticker: str = "VNM", year: int = 2023, form: str = "BCTC") -> dict:
    return {
        "candidate_id": candidate_id,
        "provenance": {"ticker": ticker, "fiscal_year": year, "document_type": form},
    }


def test_selects_only_unique_exact_match() -> None:
    result = select_unique_provenance_candidate(
        candidates=[candidate("gold"), candidate("other", ticker="FPT")],
        expected_provenance=EXPECTED,
        planned_evidence_count=1,
    )
    assert result["status"] == "SELECTED"
    assert result["selected_candidate_id"] == "gold"
    assert result["may_materialize_answer"] is False


def test_abstains_on_ambiguity_instead_of_tie_breaking() -> None:
    result = select_unique_provenance_candidate(
        candidates=[candidate("first"), candidate("second")],
        expected_provenance=EXPECTED,
        planned_evidence_count=1,
    )
    assert result["status"] == "ABSTAIN"
    assert result["reason"] == "ambiguous_exact_provenance_match"
    assert result["compatible_candidate_count"] == 2


def test_abstains_on_missing_metadata_or_unsupported_plan() -> None:
    missing = select_unique_provenance_candidate(
        candidates=[candidate("candidate")],
        expected_provenance={"ticker": "VNM", "fiscal_year": 2023},
        planned_evidence_count=1,
    )
    multi = select_unique_provenance_candidate(
        candidates=[candidate("candidate")],
        expected_provenance=EXPECTED,
        planned_evidence_count=2,
    )
    assert missing["reason"] == "incomplete_expected_provenance"
    assert multi["reason"] == "unsupported_plan_evidence_count"


def test_candidate_id_is_tracking_only_and_duplicates_abstain() -> None:
    result = select_unique_provenance_candidate(
        candidates=[candidate("same"), candidate("same", ticker="FPT")],
        expected_provenance=EXPECTED,
        planned_evidence_count=1,
    )
    assert result["status"] == "ABSTAIN"
    assert result["reason"] == "duplicate_candidate_id"
