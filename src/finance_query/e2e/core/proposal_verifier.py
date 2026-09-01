"""Deterministic verification for answer proposals.

The primary submission builder may *propose* an answer, a route, and a set of
source coordinates.  This module never generates another answer.  It checks
those exact claims against the supplied structured tables and returns a
structured result that the response policy can use for reranking.

``VERIFIED`` is deliberately reserved for a matching complete certificate
emitted by the canonical E2E verifier.  Local coordinate checks can establish
only ``PARTIAL`` at most: a hydrated row is not proof of entity role, period,
scope, unit, or semantic metric meaning.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


VERIFICATION_CLASSES = ("VERIFIED", "PARTIAL", "UNRESOLVED", "REJECTED")
CHECK_STATUSES = ("PASS", "UNRESOLVED", "FAIL", "NOT_APPLICABLE")

_CLASS_RANK = {
    "VERIFIED": 3,
    "PARTIAL": 2,
    "UNRESOLVED": 1,
    "REJECTED": 0,
}

_RECOVERY = {
    "DOCUMENT_NOT_FOUND": ("source_binding", True, "retry_document_retrieval"),
    "TABLE_NOT_FOUND": ("source_binding", True, "retry_table_retrieval"),
    "ROW_AMBIGUOUS": ("semantic_binding", True, "retry_row_binding"),
    "COLUMN_AMBIGUOUS": ("temporal_binding", True, "retry_column_binding"),
    "ENTITY_MISMATCH": ("scope_entity_validation", True, "retry_entity_retrieval"),
    "ENTITY_ROLE_UNRESOLVED": ("scope_entity_validation", True, "request_entity_role_evidence"),
    "SCOPE_MISMATCH": ("scope_entity_validation", True, "retry_scope_binding"),
    "PERIOD_MISMATCH": ("temporal_binding", True, "retry_column_binding"),
    "METRIC_MISMATCH": ("semantic_binding", True, "retry_row_binding"),
    "UNIT_UNRESOLVED": ("unit_validation", True, "inspect_header_or_context"),
    "UNIT_MISMATCH": ("unit_validation", True, "retry_unit_binding"),
    "OPERAND_MISSING": ("operand_compatibility", True, "retrieve_missing_operand"),
    "FORMULA_INVALID": ("formula_contract", True, "replan_operation_ast"),
    "OPERAND_INCOMPATIBLE": ("operand_compatibility", True, "retry_evidence_set"),
    "EXECUTION_FAILED": ("deterministic_execution", True, "retry_declared_execution"),
    "RESULT_INCONSISTENT": ("deterministic_execution", False, "reject_candidate"),
    "PROVENANCE_BROKEN": ("provenance_integrity", False, "reject_candidate"),
    "SOURCE_COORDINATE_MISSING": ("source_binding", True, "materialize_source_coordinate"),
    "NO_EVIDENCE": ("source_binding", True, "retry_candidate_discovery"),
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalized_document(value: object) -> str:
    return _text(value).removesuffix(".txt")


def _normalized_label(value: object) -> str:
    value = _text(value).lower()
    value = re.sub(r"\s+", " ", value)
    return value


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    text = _text(value)
    if not text:
        return None
    text = text.replace("\u00a0", " ").replace(" ", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = text.replace("%", "")
    text = re.sub(r"[^0-9,\.\-+]", "", text)
    if not text or text in {"-", "+"}:
        return None
    comma_count = text.count(",")
    dot_count = text.count(".")
    if comma_count and dot_count:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif comma_count > 1 or dot_count > 1:
        text = text.replace(",", "").replace(".", "")
    elif comma_count == 1 or dot_count == 1:
        separator = "," if comma_count else "."
        before, after = text.split(separator)
        # Vietnamese financial tables overwhelmingly use a three-digit group
        # as a thousands separator.  Other one-separator values are decimals.
        text = before + after if len(after) == 3 else before + "." + after
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    if not result.is_finite():
        return None
    return -result if negative else result


_FINANCIAL_LITERAL_PATTERN = re.compile(
    r"\(\s*[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?\s*\)"
    r"|[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?"
)


def _decimal_financial_literal(value: object, index: int) -> Decimal | None:
    """Replay one explicitly selected financial literal from an OCR cell."""

    if isinstance(index, bool) or index < 0:
        return None
    matches = list(_FINANCIAL_LITERAL_PATTERN.finditer(_text(value)))
    if index >= len(matches):
        return None
    return _decimal(matches[index].group(0))


def _failure(*, candidate_id: str, reason: str) -> dict[str, Any]:
    stage, recoverable, next_action = _RECOVERY[reason]
    return {
        "candidate_id": candidate_id,
        "stage": stage,
        "reason": reason,
        "recoverable": recoverable,
        "next_action": next_action,
    }


def _cell_from_evidence(
    evidence: Mapping[str, Any], tables_by_uid: Mapping[str, Mapping[str, Any]]
) -> tuple[Mapping[str, Any] | None, object | None, str | None]:
    uid = _text(evidence.get("internal_table_uid"))
    if not uid:
        return None, None, "SOURCE_COORDINATE_MISSING"
    table = tables_by_uid.get(uid)
    if not isinstance(table, Mapping):
        return None, None, "TABLE_NOT_FOUND"
    claimed_document = _normalized_document(evidence.get("document_id"))
    table_document = _normalized_document(table.get("document_id"))
    if claimed_document and table_document and claimed_document != table_document:
        return table, None, "PROVENANCE_BROKEN"
    row_index = evidence.get("row_index")
    column_index = evidence.get("column_index")
    if not isinstance(row_index, int) or not isinstance(column_index, int):
        return table, None, "SOURCE_COORDINATE_MISSING"
    rows = table.get("rows")
    if not isinstance(rows, list) or row_index < 0 or row_index >= len(rows):
        return table, None, "ROW_AMBIGUOUS"
    row = rows[row_index]
    if not isinstance(row, list) or column_index < 0 or column_index >= len(row):
        return table, None, "COLUMN_AMBIGUOUS"
    return table, row[column_index], None


def _strict_certificate_verifies(
    certificate: Mapping[str, Any] | None, *, prediction: Decimal
) -> bool:
    if not isinstance(certificate, Mapping):
        return False
    status = _text(certificate.get("status")).upper()
    if status not in {"VERIFIED", "ANSWER_CERTIFICATE_COMPLETE_CAMPAIGN_ONLY"}:
        return False
    certified = _decimal(certificate.get("answer"))
    return certified is not None and certified == prediction


def verify_proposed_answer(
    proposal: Mapping[str, Any],
    *,
    tables_by_uid: Mapping[str, Mapping[str, Any]],
    strict_certificate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Independently check one predicted answer without generating a replacement.

    A proposal's own entity, metric, scope, or period labels are only claims.
    This verifier refuses to turn them into semantic passes unless the canonical
    E2E certificate independently proves the same answer.
    """

    candidate_id = _text(proposal.get("proposal_id") or proposal.get("candidate_id"))
    if not candidate_id:
        candidate_id = "anonymous-proposal"
    prediction = _decimal(proposal.get("answer_decimal", proposal.get("answer")))
    checks: dict[str, str] = {
        "source_binding": "UNRESOLVED",
        "semantic_binding": "UNRESOLVED",
        "temporal_binding": "UNRESOLVED",
        "scope_entity_validation": "UNRESOLVED",
        "unit_validation": "UNRESOLVED",
        "formula_contract": "UNRESOLVED",
        "operand_compatibility": "UNRESOLVED",
        "deterministic_execution": "UNRESOLVED",
        "provenance_integrity": "UNRESOLVED",
    }
    failures: list[dict[str, Any]] = []
    evidence = proposal.get("evidence")
    evidence_rows = [row for row in evidence or [] if isinstance(row, Mapping)]

    if prediction is None:
        failures.append(_failure(candidate_id=candidate_id, reason="RESULT_INCONSISTENT"))
    elif not evidence_rows:
        if proposal.get("policy_fallback"):
            failures.append(_failure(candidate_id=candidate_id, reason="NO_EVIDENCE"))
        else:
            failures.append(_failure(candidate_id=candidate_id, reason="NO_EVIDENCE"))
    else:
        source_ok = True
        provenance_ok = True
        for source in evidence_rows:
            table, raw_cell, reason = _cell_from_evidence(source, tables_by_uid)
            if reason is not None:
                source_ok = False
                provenance_ok = reason != "PROVENANCE_BROKEN"
                failures.append(_failure(candidate_id=candidate_id, reason=reason))
                continue
            claimed_value = source.get("raw_value", source.get("source_value"))
            if claimed_value is not None:
                if source.get("raw_value_selector") == "first_financial_literal":
                    literal_index = source.get("raw_value_literal_index")
                    actual_decimal = (
                        _decimal_financial_literal(raw_cell, literal_index)
                        if isinstance(literal_index, int)
                        and not isinstance(literal_index, bool)
                        else None
                    )
                else:
                    actual_decimal = _decimal(raw_cell)
                claimed_decimal = _decimal(claimed_value)
                if (
                    actual_decimal is None
                    or claimed_decimal is None
                    or actual_decimal != claimed_decimal
                ):
                    source_ok = False
                    failures.append(_failure(candidate_id=candidate_id, reason="RESULT_INCONSISTENT"))
            if table is None:
                source_ok = False
        if source_ok:
            checks["source_binding"] = "PASS"
        if provenance_ok and source_ok:
            checks["provenance_integrity"] = "PASS"

    if _strict_certificate_verifies(strict_certificate, prediction=prediction or Decimal(0)):
        for check_name in checks:
            checks[check_name] = "PASS"
        verification_class = "VERIFIED"
        certificate_status = "MATCHED_COMPLETE_E2E_CERTIFICATE"
    else:
        certificate_status = _text((strict_certificate or {}).get("status")) or None
        hard_failure = any(failure["reason"] in {
            "DOCUMENT_NOT_FOUND",
            "TABLE_NOT_FOUND",
            "PROVENANCE_BROKEN",
            "RESULT_INCONSISTENT",
        } for failure in failures)
        if hard_failure:
            verification_class = "REJECTED"
        elif checks["source_binding"] == "PASS":
            verification_class = "PARTIAL"
        else:
            verification_class = "UNRESOLVED"

    return {
        "protocol": "vifinqa_proposal_verification_v1",
        "candidate_id": candidate_id,
        "verification_class": verification_class,
        "checks": checks,
        "failures": failures,
        "strict_certificate_status": certificate_status,
        "strict_certificate_matched": verification_class == "VERIFIED",
        "authority": (
            "complete_canonical_e2e_certificate"
            if verification_class == "VERIFIED"
            else "none"
        ),
    }


def select_best_proposal(proposals: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Choose the strongest non-rejected proposal, then use generator score.

    This is deliberately lexicographic: a verified result outranks a partial
    one even if retrieval liked the latter more.  A rejected proposal cannot be
    selected.  This makes verification a real reranking signal.
    """

    eligible = [
        dict(proposal)
        for proposal in proposals
        if isinstance(proposal.get("verification"), Mapping)
        and proposal["verification"].get("verification_class") != "REJECTED"
    ]
    if not eligible:
        return None

    def key(proposal: Mapping[str, Any]) -> tuple[int, float, float]:
        verification = proposal.get("verification") or {}
        class_rank = _CLASS_RANK.get(_text(verification.get("verification_class")), -1)
        route_priority = float(proposal.get("route_priority") or 0.0)
        retrieval_score = float(proposal.get("retrieval_score") or 0.0)
        return class_rank, route_priority, retrieval_score

    return max(eligible, key=key)


def load_certificate_index(path: Path | None) -> dict[int, dict[str, Any]]:
    """Read canonical E2E certificate rows without treating ABSTAIN as proof."""

    if path is None:
        return {}
    values: dict[int, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, Mapping):
            raise ValueError(f"{path}:{line_number} must be a JSON object")
        try:
            question_id = int(row.get("question_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}:{line_number} needs an integer question_id") from exc
        if question_id in values:
            raise ValueError(f"{path}:{line_number} duplicates question_id {question_id}")
        certificate = row.get("answer_certificate", row)
        if not isinstance(certificate, Mapping):
            raise ValueError(f"{path}:{line_number} has no answer_certificate object")
        values[question_id] = dict(certificate)
    return values
