"""Closed-world protocol validation for an independent grounded critic."""
from __future__ import annotations

import json
from typing import Any, Mapping


CRITIC_PROTOCOL = "grounded_critic_packets_v1"
ALLOWED_STATUS = {"accept", "reject", "abstain"}
ALLOWED_REASON_CODES = {"BINDING_CONFLICT", "MISSING_EVIDENCE", "PERIOD_OR_UNIT_CONFLICT", "FORMULA_CONFLICT", "MODEL_NOT_RUN", "UNSUPPORTED_EVIDENCE"}


def source_contract() -> dict[str, bool]:
    return {"candidate_only": True, "evidence_eligible": False, "training_eligible": False, "submission_eligible": False, "promotion_allowed": False, "may_select_final_candidate": False, "may_select_value": False, "may_execute_formula": False}


def validate_critic_response(packet: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    if set(response).difference({"question_id", "status", "stage_reviews", "binding_reviews", "execution_review", "missing_evidence", "conflicts", "feedback", "packet_evidence_ids_used", "provenance"}):
        raise ValueError("Critic response has unsupported fields")
    if response.get("question_id") != packet.get("question_id") or response.get("status") not in ALLOWED_STATUS:
        raise ValueError("Critic response question/status mismatch")
    used = list(response.get("packet_evidence_ids_used") or [])
    allowed = set(packet.get("allowed_packet_evidence_ids") or [])
    if any(str(item) not in allowed for item in used):
        raise ValueError("Critic referenced source outside bounded packet")
    if packet.get("execution_status") != "execution_replay_ready" and response.get("status") == "accept":
        raise ValueError("Critic cannot accept a non-ready execution packet")
    feedback = response.get("feedback") or {}
    reasons = list(feedback.get("reason_codes") or [])
    if any(str(reason) not in ALLOWED_REASON_CODES for reason in reasons):
        raise ValueError("Critic feedback reason code is not allowed")
    if response.get("status") in {"reject", "abstain"} and not reasons:
        raise ValueError("Critic reject/abstain lacks an allowed reason code")
    # Numeric output, formula replacement, and column selection have no place
    # in this protocol. Exact source excerpts are input-only.
    forbidden = {"value", "numeric_value", "formula", "selected", "resolved", "answer"}
    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            if forbidden.intersection(value): raise ValueError("Critic attempted selection/value/formula output")
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(response)
    return {**dict(response), "provenance": "machine_provisional", "source_contract": source_contract()}


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
