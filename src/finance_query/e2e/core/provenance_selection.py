"""Fail-closed evidence selection from exact document provenance.

This module does not retrieve passages and cannot authorize an answer.  It
only selects an already-retrieved evidence candidate when a single-passage
plan has exactly one candidate compatible with every required provenance
field.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


PROVENANCE_SELECTION_PROTOCOL = "unique_exact_provenance_selection_v1"
DEFAULT_REQUIRED_FIELDS = ("ticker", "fiscal_year", "document_type")


def _exact_text(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text or None


def _decision(
    *,
    status: str,
    reason: str,
    compatible_candidate_count: int,
    selected_candidate_id: str | None = None,
) -> dict[str, Any]:
    return {
        "protocol": PROVENANCE_SELECTION_PROTOCOL,
        "status": status,
        "reason": reason,
        "compatible_candidate_count": compatible_candidate_count,
        "selected_candidate_id": selected_candidate_id,
        "authorization_scope": "evidence_selection_only",
        "may_materialize_answer": False,
        "submission_eligible": False,
    }


def select_unique_provenance_candidate(
    *,
    candidates: Sequence[Mapping[str, Any]],
    expected_provenance: Mapping[str, Any],
    planned_evidence_count: int,
    required_fields: Sequence[str] = DEFAULT_REQUIRED_FIELDS,
) -> dict[str, Any]:
    """Return one candidate ID only after exact, complete, unique matching.

    Candidate IDs are tracking-only: they are validated for uniqueness but do
    not influence compatibility or ordering.  Missing metadata, unsupported
    plan cardinality, duplicate IDs, zero matches, and ambiguity all abstain.
    """

    if planned_evidence_count != 1:
        return _decision(
            status="ABSTAIN",
            reason="unsupported_plan_evidence_count",
            compatible_candidate_count=0,
        )
    fields = tuple(required_fields)
    if not fields or len(fields) != len(set(fields)) or not all(isinstance(field, str) and field for field in fields):
        return _decision(
            status="ABSTAIN",
            reason="invalid_required_fields",
            compatible_candidate_count=0,
        )
    expected = {field: _exact_text(expected_provenance.get(field)) for field in fields}
    if any(value is None for value in expected.values()):
        return _decision(
            status="ABSTAIN",
            reason="incomplete_expected_provenance",
            compatible_candidate_count=0,
        )

    identifiers: list[str] = []
    compatible: list[str] = []
    for candidate in candidates:
        candidate_id = _exact_text(candidate.get("candidate_id"))
        if candidate_id is None:
            return _decision(
                status="ABSTAIN",
                reason="invalid_candidate_id",
                compatible_candidate_count=0,
            )
        identifiers.append(candidate_id)
        metadata = candidate.get("provenance")
        if not isinstance(metadata, Mapping):
            continue
        observed = {field: _exact_text(metadata.get(field)) for field in fields}
        if all(observed[field] == expected[field] for field in fields):
            compatible.append(candidate_id)
    if len(identifiers) != len(set(identifiers)):
        return _decision(
            status="ABSTAIN",
            reason="duplicate_candidate_id",
            compatible_candidate_count=0,
        )
    if not compatible:
        return _decision(
            status="ABSTAIN",
            reason="no_exact_provenance_match",
            compatible_candidate_count=0,
        )
    if len(compatible) != 1:
        return _decision(
            status="ABSTAIN",
            reason="ambiguous_exact_provenance_match",
            compatible_candidate_count=len(compatible),
        )
    return _decision(
        status="SELECTED",
        reason="unique_exact_provenance_match",
        compatible_candidate_count=1,
        selected_candidate_id=compatible[0],
    )
