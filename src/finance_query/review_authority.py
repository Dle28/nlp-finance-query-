"""Fail-closed reviewer authority for evidence-bound ViFinQA gates.

Human-equivalent authority changes the eligibility weight of a review at one
explicit gate.  It never changes the truthful reviewer provenance and never
implies release, promotion, training, submission, value selection, or formula
execution authority.
"""
from __future__ import annotations

from typing import Any, Mapping


HUMAN_REVIEWER_TYPE = "human_verified"
CHATGPT_REVIEWER_TYPE = "chatgpt_verified"
HUMAN_EQUIVALENT = "human_equivalent"
GATE_EFFECT = "same_eligibility_weight_as_human_verified"
REVIEW_POLICY = "fail_closed_evidence_bound_v1"

SEMANTIC_BINDING_SCOPE = "semantic_binding_review_gate_equivalence"
CAMPAIGN_REVIEW_SCOPE = "campaign_review_gate_equivalence"


class ReviewAuthorityError(ValueError):
    """Raised when a reviewer claims authority outside an explicit grant."""


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def validate_gate_reviewer(
    provenance: object,
    *,
    grant_scope: str,
    authority_receipt: object = None,
    chatgpt_role: str,
    human_role: str | None = None,
) -> dict[str, Any]:
    """Validate native-human or explicitly granted ChatGPT gate authority."""

    record = _mapping(provenance)
    reviewer_type = _text(record.get("reviewer_type"))
    reviewer_id = _text(record.get("reviewer_id"))
    if not reviewer_id:
        raise ReviewAuthorityError("review decision requires a reviewer identity")

    if reviewer_type == HUMAN_REVIEWER_TYPE:
        if human_role and _text(record.get("reviewer_role")) != human_role:
            raise ReviewAuthorityError("human reviewer role is invalid for this gate")
        if record.get("verification_authority") not in {None, "", HUMAN_REVIEWER_TYPE}:
            raise ReviewAuthorityError("human review cannot claim delegated AI authority")
        return {
            "reviewer_type": reviewer_type,
            "reviewer_id": reviewer_id,
            "verification_authority": HUMAN_REVIEWER_TYPE,
            "gate_effect": "native_human_verified",
            "grant_scope": grant_scope,
        }

    if reviewer_type != CHATGPT_REVIEWER_TYPE:
        raise ReviewAuthorityError("reviewer type is not authorized for this gate")

    grant = _mapping(record.get("authority_grant"))
    receipt = _mapping(authority_receipt)
    if (
        _text(record.get("reviewer_role")) != chatgpt_role
        or _text(record.get("model_family")) == ""
        or record.get("review_policy") != REVIEW_POLICY
        or record.get("verification_authority") != HUMAN_EQUIVALENT
        or grant.get("granted_by") != "campaign_owner"
        or grant.get("grant_scope") != grant_scope
        or grant.get("grant_basis") != "explicit_user_instruction"
    ):
        raise ReviewAuthorityError("ChatGPT review requires an explicit fail-closed authority grant")
    if (
        receipt.get("verification_authority") != HUMAN_EQUIVALENT
        or receipt.get("gate_effect") != GATE_EFFECT
        or receipt.get("provenance_preserved_as") != CHATGPT_REVIEWER_TYPE
        or receipt.get("scope") != grant_scope
        or receipt.get("release_authority_included") is not False
        or receipt.get("training_authority_included") is not False
        or receipt.get("submission_authority_included") is not False
        or receipt.get("value_selection_authority_included") is not False
        or receipt.get("formula_execution_authority_included") is not False
    ):
        raise ReviewAuthorityError("ChatGPT human-equivalent authority receipt is invalid")
    return {
        "reviewer_type": reviewer_type,
        "reviewer_id": reviewer_id,
        "verification_authority": HUMAN_EQUIVALENT,
        "gate_effect": GATE_EFFECT,
        "grant_scope": grant_scope,
    }

