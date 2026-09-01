"""Build numeric-free ContextPackets from a submission artifact directory."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from pathlib import Path
import re
from typing import Any

from ..contracts import (
    BLOCKED_VERIFICATION_CLASSES,
    CONTEXT_PACKET_PROTOCOL,
    canonical_sha256,
)
from .prompt_profiles import DEFAULT_PROMPT_PROFILE, render_prompt


_NUMERIC_EVIDENCE_KEYS = frozenset(
    {
        "answer",
        "answer_decimal",
        "numeric_answer",
        "final_answer",
        "raw_value",
        "source_value",
        "raw_value_decimal",
        "parsed_value",
        "numeric_cells",
        "converted_output_decimal",
        "candidate_answer_decimal",
        "aggregate_value",
        "numeric_value",
    }
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path, *, required: bool = True) -> list[dict[str, Any]]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        rows.append(value)
    return rows


def _redact_numeric_evidence(value: Any, *, key: str | None = None) -> Any:
    """Remove source numbers while keeping labels, coordinates and trace."""

    normalized = (key or "").casefold()
    if normalized in _NUMERIC_EVIDENCE_KEYS:
        return None if normalized == "numeric_cells" else "<omitted_non_authoritative_value>"
    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_numeric_evidence(child, key=str(child_key))
            for child_key, child in value.items()
            if str(child_key).casefold() not in _NUMERIC_EVIDENCE_KEYS
        }
    if isinstance(value, list):
        return [_redact_numeric_evidence(child) for child in value]
    return value


def _index(rows: Iterable[Mapping[str, Any]], *, key: str, label: str) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        raw_id = row.get(key)
        try:
            question_id = int(raw_id)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} has invalid question id: {raw_id!r}") from error
        if question_id in indexed:
            raise ValueError(f"{label} has duplicate question id {question_id}")
        indexed[question_id] = dict(row)
    return indexed


def _verification_class(diagnostic: Mapping[str, Any]) -> str:
    value = diagnostic.get("verification_class")
    if not value and isinstance(diagnostic.get("verification"), Mapping):
        value = diagnostic["verification"].get("verification_class")
    return str(value or "UNRESOLVED").upper()


def _source_refs(*rows: Mapping[str, Any]) -> list[str]:
    refs: set[str] = set()
    for row in rows:
        for key in ("source_ref_sha256", "internal_table_uid", "source_packet_ref_sha256"):
            value = row.get(key)
            if isinstance(value, str) and _HASH_RE.fullmatch(value):
                refs.add(value)
        for key in ("evidence", "source", "selected_operands"):
            values = row.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, Mapping):
                    continue
                refs.update(_source_refs(item))
    return sorted(refs)


def _failure_reasons(diagnostic: Mapping[str, Any], audit: Mapping[str, Any]) -> list[str]:
    verification = diagnostic.get("verification")
    if not isinstance(verification, Mapping):
        verification = audit.get("verification") if isinstance(audit.get("verification"), Mapping) else {}
    reasons = {
        str(item.get("reason"))
        for item in verification.get("failures") or []
        if isinstance(item, Mapping) and item.get("reason")
    }
    for name, status in (verification.get("checks") or {}).items():
        if str(status) not in {"PASS", "NOT_APPLICABLE"}:
            reasons.add(f"CHECK_NOT_PASS:{name}:{status}")
    if not reasons:
        route_reasons = diagnostic.get("research_route_reason_codes") or []
        reasons.update(str(item) for item in route_reasons if item)
    return sorted(reasons)


def _candidate_context(diagnostic: Mapping[str, Any], audit: Mapping[str, Any], candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    groups = diagnostic.get("plan_groups") or []
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            for item in (group.get("top_candidates") or [])[:8]:
                if isinstance(item, Mapping):
                    values.append(
                        {
                            "group": group.get("group"),
                            "candidate": _redact_numeric_evidence(item),
                        }
                    )
    for item in (audit.get("evidence") or []):
        if isinstance(item, Mapping):
            values.append({"selected": _redact_numeric_evidence(item)})
    if candidate:
        values.append({"surviving_candidate": _redact_numeric_evidence(candidate)})
    return values[:20]


def _build_packet(
    *,
    question: Mapping[str, Any],
    diagnostic: Mapping[str, Any],
    audit: Mapping[str, Any],
    candidate: Mapping[str, Any],
    report: Mapping[str, Any],
    prompt_profile: str,
    resolved: Mapping[str, Any] | None = None,
    e2e_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    question_id = int(question["id"])
    verification = diagnostic.get("verification")
    if not isinstance(verification, Mapping):
        verification = audit.get("verification") if isinstance(audit.get("verification"), Mapping) else {}
    claims = audit.get("claims") if isinstance(audit.get("claims"), Mapping) else {}
    typed_question = {
        "claims": _redact_numeric_evidence(claims),
        "plan_shape": diagnostic.get("plan_shape"),
        "plan_status": diagnostic.get("plan_status"),
        "plan_groups": _redact_numeric_evidence(diagnostic.get("plan_groups") or []),
        "research_route_status": diagnostic.get("research_route_status"),
        "reporting_scope": claims.get("reporting_scope"),
        "source_scope": (
            resolved.get("source_scope")
            if isinstance(resolved, Mapping)
            else {"source_scope_status": "NOT_MATERIALIZED_IN_PACKET_INPUT"}
        ),
    }
    proposal = {
        "proposal_id": audit.get("proposal_id"),
        "answer_route": audit.get("answer_route"),
        "operation_ast": _redact_numeric_evidence(audit.get("operation_ast") or {}),
        "claims": _redact_numeric_evidence(audit.get("claims") or {}),
        "pandas_query": audit.get("pandas_query"),
        "policy_fallback": bool(audit.get("policy_fallback")),
        "answer_present": audit.get("answer_decimal") is not None,
    }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "protocol": CONTEXT_PACKET_PROTOCOL,
        "question_id": question_id,
        "question_raw_vi": str(question.get("question") or ""),
        "typed_question": typed_question,
        "proposal": proposal,
        "verification": _redact_numeric_evidence(verification),
        "blocked_reason_codes": _failure_reasons(diagnostic, audit),
        "candidate_context": _candidate_context(diagnostic, audit, candidate),
        "retrieval_context": {
            "top_k_candidate_count": len(_candidate_context(diagnostic, audit, candidate)),
            "candidate_context_is_navigation_only": True,
            "candidate_fields_preserved": [
                "document_id",
                "internal_table_uid",
                "row_index",
                "column_index",
                "row_label",
                "score",
                "validity_probability",
            ],
        },
        "allowed_source_refs": _source_refs(audit, candidate),
        "submission_status": {
            "prediction_tier": diagnostic.get("tier"),
            "verification_class": _verification_class(diagnostic),
            "strict_authority": verification.get("authority"),
        },
        "resolved_prediction": _redact_numeric_evidence(resolved or {}),
        "e2e_receipt": _redact_numeric_evidence(e2e_receipt or {}),
        "run_context": {
            "build_report_protocol": report.get("protocol"),
            "primary_model": report.get("primary_model"),
            "canonical_order": [
                "proposal_ast",
                "deterministic_resolver",
                "resolved_prediction",
                "e2e_verification",
                "submission_compiler",
            ],
        },
        "numeric_cells": None,
        "authority_boundary": "feedback_only_non_authorizing",
        "prompt_profile": prompt_profile,
    }
    payload["packet_id"] = canonical_sha256(payload)
    return payload


def compile_blocked_question_packets(
    submission_dir: Path,
    *,
    prompt_profile: str = DEFAULT_PROMPT_PROFILE,
    max_questions: int | None = None,
) -> list[dict[str, Any]]:
    """Compile strict-blocked questions into hash-bound, numeric-free packets."""

    submission_dir = submission_dir.resolve()
    questions = _index(_load_json(submission_dir / "submission.json"), key="id", label="submission")
    diagnostics = _index(
        _load_jsonl(submission_dir / "diagnostics.jsonl"),
        key="id",
        label="diagnostics",
    )
    audits = _index(
        _load_jsonl(submission_dir / "prediction_audit_ledger_v1.jsonl"),
        key="question_id",
        label="proposal audit",
    )
    candidates = _index(
        _load_jsonl(
            submission_dir / "best_surviving_candidates_v1.jsonl",
            required=False,
        ),
        key="question_id",
        label="candidate ledger",
    ) if (submission_dir / "best_surviving_candidates_v1.jsonl").is_file() else {}
    resolved_rows = _index(
        _load_jsonl(submission_dir / "resolved_predictions_v1.jsonl", required=False),
        key="question_id",
        label="resolved prediction ledger",
    ) if (submission_dir / "resolved_predictions_v1.jsonl").is_file() else {}
    e2e_rows = _index(
        _load_jsonl(submission_dir / "e2e_receipts_v1.jsonl", required=False),
        key="question_id",
        label="E2E receipt ledger",
    ) if (submission_dir / "e2e_receipts_v1.jsonl").is_file() else {}
    report = _load_json(submission_dir / "build_report.json") if (submission_dir / "build_report.json").is_file() else {}
    missing = sorted(set(diagnostics) - set(questions))
    if missing:
        raise ValueError(f"diagnostics reference questions missing from submission: {missing[:10]}")
    packets: list[dict[str, Any]] = []
    for question_id in sorted(diagnostics):
        diagnostic = diagnostics[question_id]
        verification_class = _verification_class(diagnostic)
        if verification_class not in BLOCKED_VERIFICATION_CLASSES:
            continue
        question = questions[question_id]
        audit = audits.get(question_id, {})
        candidate = candidates.get(question_id, {})
        packets.append(
            _build_packet(
                question=question,
                diagnostic=diagnostic,
                audit=audit,
                candidate=candidate,
                report=report,
                prompt_profile=prompt_profile,
                resolved=resolved_rows.get(question_id),
                e2e_receipt=e2e_rows.get(question_id),
            )
        )
    if max_questions is not None:
        if max_questions <= 0:
            raise ValueError("max_questions must be positive")
        packets = packets[:max_questions]
    return packets


def compile_feedback_prompt(
    packet: Mapping[str, Any],
    *,
    profile_name: str = DEFAULT_PROMPT_PROFILE,
) -> str:
    """Compile the exact model prompt for one blocked-question packet."""

    return render_prompt(packet, profile_name=profile_name)


__all__ = ["compile_blocked_question_packets", "compile_feedback_prompt"]
