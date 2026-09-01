"""Stable contracts for the submission-first pipeline.

These contracts intentionally separate three things which are easy to mix up:

* a candidate answer emitted for competition coverage;
* a model's diagnosis of why a candidate was blocked; and
* an independently authorized answer certificate.

The model feedback contract is non-authorizing by construction.  It can point
the next experiment at a prompt, retrieval or AST problem, but it cannot
upgrade a verification class or provide a numeric answer.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any


CONTEXT_PACKET_PROTOCOL = "vifinqa_context_packet_v1"
FEEDBACK_PROTOCOL = "vifinqa_blocked_question_feedback_v1"
IMPROVEMENT_RECORD_PROTOCOL = "vifinqa_improvement_feedback_record_v1"
FEEDBACK_MANIFEST_PROTOCOL = "vifinqa_blocked_feedback_run_manifest_v1"

BLOCKED_VERIFICATION_CLASSES = frozenset({"PARTIAL", "UNRESOLVED", "REJECTED", "ABSTAIN"})

FEEDBACK_DECISIONS = frozenset({"FEEDBACK_ONLY", "ABSTAIN"})
FEEDBACK_FAILURE_CLASSES = frozenset(
    {
        "RETRIEVAL_MISS",
        "RERANK_MISS",
        "CONTEXT_INCOMPLETE",
        "SEMANTIC_AMBIGUITY",
        "TEMPORAL_OR_SCOPE_AMBIGUITY",
        "UNIT_AMBIGUITY",
        "FORMULA_OR_OPERAND",
        "PROVENANCE_OR_SOURCE_CONFLICT",
        "AST_OR_FORMAT",
        "DETERMINISTIC_REPLAY",
        "INSUFFICIENT_EVIDENCE",
        "ABSTAIN",
        "OTHER",
    }
)
FEEDBACK_CONFIDENCES = frozenset({"LOW", "MEDIUM", "HIGH"})
FEEDBACK_REASON_CODES = frozenset(
    {
        "MODEL_UNCERTAIN",
        "MODEL_RUNTIME_ERROR",
        "MODEL_OUTPUT_INVALID",
        "INSUFFICIENT_PACKET_EVIDENCE",
        "BLOCKED_BY_SOURCE_BINDING",
        "BLOCKED_BY_SEMANTIC_BINDING",
        "BLOCKED_BY_TEMPORAL_BINDING",
        "BLOCKED_BY_SCOPE_OR_ENTITY",
        "BLOCKED_BY_UNIT",
        "BLOCKED_BY_FORMULA",
        "BLOCKED_BY_OPERAND",
        "BLOCKED_BY_REPLAY",
        "BLOCKED_BY_PROVENANCE",
        "SUPPORTED_BY_RECEIPT",
    }
)
FEEDBACK_CONTEXT_FIELDS = frozenset(
    {
        "question_raw_vi",
        "entity",
        "metric",
        "period",
        "scope",
        "unit",
        "source_document",
        "source_table",
        "table_header",
        "row_label",
        "column_label",
        "operand_set",
        "operation_ast",
        "source_hash",
        "retrieval_candidate",
        "rerank_score",
        "formula_definition",
        "evidence_binding",
    }
)
FEEDBACK_ACTIONS = frozenset(
    {
        "ADD_VI_QUERY_ALIAS",
        "ADD_EN_CANONICAL_ALIAS",
        "INCREASE_RETRIEVAL_K",
        "ADD_RERANK_HARD_NEGATIVE",
        "HYDRATE_CONTEXT_FIELD",
        "UPDATE_PROMPT_PROFILE",
        "ADD_AST_TRAINING_EXAMPLE",
        "REPAIR_SOURCE_BINDING",
        "REQUIRE_HUMAN_REVIEW",
        "NO_ACTION",
    }
)


def canonical_sha256(value: object) -> str:
    """Hash a JSON-compatible value with one canonical serialization."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def non_authorizing_flags() -> dict[str, bool]:
    """Return flags repeated on every model feedback/improvement record."""

    return {
        "answer_authorized": False,
        "evidence_authorized": False,
        "training_eligible": False,
        "promotion_allowed": False,
        "submission_eligible": False,
        "release_authorized": False,
    }


def assert_no_authority_upgrade(value: object) -> None:
    """Reject model output that attempts to cross the authority boundary."""

    forbidden = {
        "answer",
        "answer_decimal",
        "numeric_answer",
        "final_answer",
        "raw_value",
        "source_value",
        "parsed_value",
        "human_verified",
        "training_eligible",
        "promotion_allowed",
        "submission_eligible",
        "release_authorized",
        "answer_authorized",
        "evidence_authorized",
        "certificate",
        "answer_certificate",
    }

    def visit(item: object, path: str) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                normalized = str(key).casefold()
                if normalized in forbidden or normalized.endswith("_answer"):
                    raise ValueError(f"feedback output crosses authority boundary at {path}.{key}")
                visit(child, f"{path}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")

    visit(value, "$")


__all__ = [
    "BLOCKED_VERIFICATION_CLASSES",
    "CONTEXT_PACKET_PROTOCOL",
    "FEEDBACK_ACTIONS",
    "FEEDBACK_CONFIDENCES",
    "FEEDBACK_CONTEXT_FIELDS",
    "FEEDBACK_DECISIONS",
    "FEEDBACK_FAILURE_CLASSES",
    "FEEDBACK_MANIFEST_PROTOCOL",
    "FEEDBACK_PROTOCOL",
    "FEEDBACK_REASON_CODES",
    "IMPROVEMENT_RECORD_PROTOCOL",
    "assert_no_authority_upgrade",
    "canonical_sha256",
    "non_authorizing_flags",
]
