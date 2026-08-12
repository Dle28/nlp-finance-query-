"""Prompt and fail-closed fallback for the GPU Qwen grounded critic V2."""
from __future__ import annotations

import json
from typing import Any, Mapping


def critic_prompt(packet: Mapping[str, Any]) -> str:
    """Create a closed-world prompt; Qwen is a critic, never an executor."""
    evidence = [
        {
            "evidence_id": item.get("evidence_id"),
            "stage_id": item.get("stage_id"),
            "role": item.get("role"),
            "document_id": item.get("document_id"),
            "cell_provenance": item.get("cell_provenance"),
        }
        for item in packet.get("bounded_source_excerpts") or []
    ]
    payload = {
        "question_id": packet.get("question_id"),
        "question_context": packet.get("question_context"),
        "execution_status": packet.get("execution_status"),
        "allowed_evidence_ids": packet.get("allowed_packet_evidence_ids"),
        "bounded_source_excerpts": evidence,
        "deterministic_execution_trace": packet.get("deterministic_execution_trace"),
    }
    schema = {
        "question_id": packet.get("question_id"),
        "status": "accept | reject | abstain",
        "stage_reviews": [],
        "binding_reviews": [],
        "execution_review": {},
        "missing_evidence": [],
        "conflicts": [],
        "feedback": {
            "missing_concepts": [],
            "suggested_table_types": [],
            "suggested_retrieval_stage": None,
            "reason_codes": [],
        },
        "packet_evidence_ids_used": [],
    }
    return (
        "You are an independent closed-world financial QA critic. Assess only whether the "
        "given deterministic execution trace is supported by the bounded packet. You MUST NOT "
        "compute, repeat, invent, select, or output any numeric value, answer, formula, row, "
        "column, table candidate, or evidence outside allowed_evidence_ids. If unsure, abstain. "
        "Return exactly one JSON object matching this schema and use feedback.reason_codes only "
        "from BINDING_CONFLICT, MISSING_EVIDENCE, PERIOD_OR_UNIT_CONFLICT, FORMULA_CONFLICT, "
        "UNSUPPORTED_EVIDENCE.\nSCHEMA:\n"
        + json.dumps(schema, ensure_ascii=False)
        + "\nPACKET:\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def fallback_abstention(question_id: int, detail: str) -> dict[str, Any]:
    """A parse/model failure remains an abstention with no invented output."""
    return {
        "question_id": question_id,
        "status": "abstain",
        "stage_reviews": [],
        "binding_reviews": [],
        "execution_review": {},
        "missing_evidence": [],
        "conflicts": [],
        "feedback": {
            "missing_concepts": [],
            "suggested_table_types": [],
            "suggested_retrieval_stage": None,
            "reason_codes": ["UNSUPPORTED_EVIDENCE"],
            "detail": detail[:500],
        },
        "packet_evidence_ids_used": [],
    }
