"""Prompt profiles for multilingual, structured model roles."""

from __future__ import annotations

import json
from typing import Any, Mapping


DEFAULT_PROMPT_PROFILE = "en_system_vi_context_compact_v2"
COMPACT_PROMPT_PROFILE = "en_system_vi_context_compact_v2"
ONE_JSON_OBJECT_DIRECTIVE = (
    "Return exactly one JSON object and nothing else (chỉ một JSON object; "
    "no markdown, no prose, and no second object)."
)

PROMPT_PROFILES: dict[str, dict[str, Any]] = {
    # Keep the original full-packet profile for compatibility and controlled
    # ablations. The compact profile is the operational default because it
    # leaves enough output room for the complete contract on long packets.
    "en_system_vi_context_v1": {
        "system_language": "en",
        "context_language": "vi_preserved_plus_canonical_aliases",
        "top_k_policy": "candidate_labels_and_coordinates_only",
        "output_language": "json_enums",
        "role": "blocked_question_feedback_critic",
        "numeric_cells_in_context": False,
        "authority": "feedback_only_non_authorizing",
    },
    COMPACT_PROMPT_PROFILE: {
        "system_language": "en",
        "context_language": "vi_preserved_plus_canonical_aliases",
        "top_k_policy": "candidate_labels_and_coordinates_only",
        "output_language": "json_enums",
        "role": "blocked_question_feedback_critic",
        "numeric_cells_in_context": False,
        "context_shape": "compact_failure_diagnosis_v2",
        "authority": "feedback_only_non_authorizing",
    },
    "vi_system_vi_context_v1": {
        "system_language": "vi",
        "context_language": "vi_preserved_plus_canonical_aliases",
        "top_k_policy": "candidate_labels_and_coordinates_only",
        "output_language": "json_enums",
        "role": "blocked_question_feedback_critic",
        "numeric_cells_in_context": False,
        "authority": "feedback_only_non_authorizing",
    },
}


def system_instruction(profile_name: str = DEFAULT_PROMPT_PROFILE) -> str:
    """Return the stable critic instruction for the selected profile."""

    if profile_name not in PROMPT_PROFILES:
        raise ValueError(f"unknown prompt profile: {profile_name}")
    if profile_name == "vi_system_vi_context_v1":
        return """Bạn là bộ phân tích feedback độc lập cho pipeline hỏi đáp tài chính.

Bạn chỉ đánh giá vì sao một câu hỏi bị chặn và đề xuất thay đổi cho prompt,
retrieval, reranker, context hoặc AST. Không sửa câu trả lời, không sinh số,
không xác nhận evidence, không cấp certificate và không quyết định promote.

Chỉ dùng thông tin trong ContextPacket. ContextPacket là song ngữ có chủ ý:
giữ nguyên question, nhãn bảng và thuật ngữ tiếng Việt; các key/enum canonical
có thể là tiếng Anh. Phân biệt source_scope/source_universe (phạm vi nguồn
được phép tìm) với reporting_scope (phạm vi báo cáo mà câu hỏi yêu cầu).
    Top-k chỉ là candidate navigation, không phải bằng chứng và không được lấy
    số từ candidate. Giữ nguyên ticker, period, coordinate và hash. Nếu context
    chưa đủ, trả ABSTAIN với reason code phù hợp. Chỉ xuất một JSON object
    (chỉ một JSON object), không markdown, không prose và không object thứ hai,
    theo schema đã nêu."""
    if profile_name == COMPACT_PROMPT_PROFILE:
        return """You are an independent feedback critic for a Vietnamese financial QA pipeline.

The ContextPacket is a deliberately compact, numeric-free diagnostic view.
Evaluate only why the question is blocked and what the next experiment should
change in retrieval, reranking, context compilation, prompt policy, source
binding, or AST planning. Do not repair or replace the answer. Do not generate
any numeric answer or source value, and do not issue evidence, certificate,
training, promotion, submission, or release authority.

Vietnamese text, table labels, tickers, periods, coordinates, and hashes are
semantic evidence and must be preserved exactly. Missing fields mean that the
packet does not prove that fact; do not infer it. Distinguish source_scope or
source_universe (where retrieval may search) from reporting_scope (what the
question requests). Top-k candidates are navigation metadata only, never
evidence. Use only this packet. If it is insufficient, return ABSTAIN. Return
    exactly one JSON object (chỉ một JSON object) using the enum values in
    output_schema. Emit no markdown, prose, or second object. All eight keys
    are mandatory, even when their arrays are empty."""
    return """You are an independent feedback critic for a Vietnamese financial QA pipeline.

Evaluate only why the supplied question was blocked and what the next
experiment should change in retrieval, reranking, context compilation, prompt
policy, source binding, or AST planning. Do not repair or replace the answer.
Do not generate a numeric answer, source value, evidence certificate, training
label, promotion decision, or release decision.

The raw question and source labels may be Vietnamese. Preserve them exactly;
do not translate identifiers, tickers, periods, coordinates, or hashes. The
packet is intentionally bilingual: Vietnamese is the semantic evidence
surface, while canonical keys/enums may remain English. Distinguish
source_scope/source_universe (where retrieval may search) from
reporting_scope (what the question requests). Top-k entries are navigation
metadata only; never copy a number from a candidate. Use only the supplied
ContextPacket. If the packet is insufficient, return ABSTAIN.
    Return exactly one JSON object (chỉ một JSON object) using the requested
    enum values, with no markdown, prose, or second object."""


def render_prompt(
    context_packet: Mapping[str, Any],
    *,
    profile_name: str = DEFAULT_PROMPT_PROFILE,
) -> str:
    """Render one deterministic prompt from a hash-bound context packet."""

    profile = PROMPT_PROFILES.get(profile_name)
    if profile is None:
        raise ValueError(f"unknown prompt profile: {profile_name}")
    output_schema = {
        "decision": "FEEDBACK_ONLY | ABSTAIN",
        "failure_class": (
            "RETRIEVAL_MISS | RERANK_MISS | CONTEXT_INCOMPLETE | "
            "SEMANTIC_AMBIGUITY | TEMPORAL_OR_SCOPE_AMBIGUITY | UNIT_AMBIGUITY | "
            "FORMULA_OR_OPERAND | PROVENANCE_OR_SOURCE_CONFLICT | AST_OR_FORMAT | "
            "DETERMINISTIC_REPLAY | INSUFFICIENT_EVIDENCE | ABSTAIN | OTHER"
        ),
        "reason_codes": "array of allowed reason codes",
        "observations": "array of short evidence-bound strings",
        "missing_context_fields": "array of allowed context field names",
        "recommended_actions": (
            "array of {action, target, rationale}; max 4 actions"
        ),
        "confidence": "LOW | MEDIUM | HIGH",
        "source_ref_sha256": "array containing only hashes from allowed_source_refs",
    }
    packet = _prompt_context(context_packet, profile_name=profile_name)
    packet["prompt_profile"] = profile_name
    packet["output_schema"] = output_schema
    output_hint = ""
    if profile_name == COMPACT_PROMPT_PROFILE:
        output_hint = """

MINIMAL_VALID_OUTPUT_SHAPE:
{"decision":"ABSTAIN","failure_class":"ABSTAIN","confidence":"LOW","reason_codes":["INSUFFICIENT_PACKET_EVIDENCE"],"source_ref_sha256":[],"observations":["The packet does not prove the blocked claim."],"missing_context_fields":[],"recommended_actions":[]}

Use this compact shape. Replace values only with contract enums and packet-grounded text. Do not add keys. An empty recommended_actions array is valid."""
    return (
        system_instruction(profile_name)
        + "\n\nCONTEXT_PACKET_JSON:\n"
        + json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n\n"
        + ONE_JSON_OBJECT_DIRECTIVE
        + output_hint
    )


def _pick(mapping: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: mapping[key] for key in keys if key in mapping}


def _compact_status(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    result = _pick(
        value,
        "status",
        "verification_class",
        "authority",
        "checks",
        "failures",
        "first_failure",
        "failure_reason_codes",
        "answer_status",
        "answer_authorized",
        "contract_status",
        "compatibility_status",
    )
    if isinstance(result.get("failures"), list):
        result["failures"] = result["failures"][:8]
    if isinstance(result.get("failure_reason_codes"), list):
        result["failure_reason_codes"] = result["failure_reason_codes"][:12]
    return result


def _compact_claims(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    result = _pick(value, "entities", "reporting_scope", "source_scope", "source_universe")
    operands = value.get("operands")
    if isinstance(operands, list):
        result["operand_count"] = len(operands)
        result["operands"] = [
            _pick(item, "operand_id", "ticker", "entity", "metric", "period", "scope", "qualifiers")
            for item in operands[:12]
            if isinstance(item, Mapping)
        ]
        if len(operands) > 12:
            result["operands_truncated"] = True
    return result


def _compact_candidate(item: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(item.get("candidate"), Mapping):
        return {
            "group": item.get("group"),
            "candidate": _pick(
                item["candidate"],
                "document_id",
                "internal_table_uid",
                "row_index",
                "column_index",
                "row_label",
                "column_label",
                "rank",
                "score",
                "validity_probability",
            ),
        }
    if isinstance(item.get("selected"), Mapping):
        return {
            "selected": _pick(
                item["selected"],
                "document_id",
                "internal_table_uid",
                "row_index",
                "column_index",
                "row_label",
                "column_label",
            )
        }
    if isinstance(item.get("surviving_candidate"), Mapping):
        candidate = item["surviving_candidate"]
        result = _pick(candidate, "candidate_id", "tier", "plan_status", "selection_method")
        verification = candidate.get("verification")
        if isinstance(verification, Mapping):
            result["verification"] = _compact_status(verification)
        source = candidate.get("source")
        if isinstance(source, list):
            result["source"] = [
                _pick(
                    row,
                    "document_id",
                    "internal_table_uid",
                    "row_index",
                    "column_index",
                    "row_label",
                    "column_label",
                )
                for row in source
                if isinstance(row, Mapping)
            ][:4]
        return {"surviving_candidate": result}
    return {}


def _compact_receipt(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    result = _compact_status(value)
    operands = value.get("operand_receipts")
    if isinstance(operands, list):
        compact_operands: list[dict[str, Any]] = []
        for operand in operands[:6]:
            if not isinstance(operand, Mapping):
                continue
            item = _pick(
                operand,
                "operand_id",
                "role",
                "binding_status",
                "semantic_status",
                "temporal_status",
                "scope_entity_status",
                "unit_status",
                "failure_reason_codes",
            )
            compact_operands.append(item)
        result["operand_receipts"] = compact_operands
        if len(operands) > 6:
            result["operand_receipts_truncated"] = True
    formula = value.get("formula_receipt")
    if isinstance(formula, Mapping):
        result["formula_receipt"] = _pick(
            formula,
            "contract_status",
            "compatibility_status",
        )
    execution = value.get("execution_receipt")
    if isinstance(execution, Mapping):
        result["execution_receipt"] = _pick(execution, "status", "execution_method")
    return result


def _prompt_context(context_packet: Mapping[str, Any], *, profile_name: str) -> dict[str, Any]:
    """Select a bounded diagnostic view for the compact Kaggle prompt.

    The full packet remains the hash-bound artifact. The compact profile is a
    prompt serialization policy: it keeps Vietnamese semantics, coordinates,
    hashes, and failure states while dropping repeated proposal/audit payloads
    that can push the output contract out of the model's context window.
    """

    packet = dict(context_packet)
    if profile_name != COMPACT_PROMPT_PROFILE:
        return packet
    typed = packet.get("typed_question") if isinstance(packet.get("typed_question"), Mapping) else {}
    proposal = packet.get("proposal") if isinstance(packet.get("proposal"), Mapping) else {}
    retrieval = packet.get("retrieval_context") if isinstance(packet.get("retrieval_context"), Mapping) else {}
    status = packet.get("submission_status") if isinstance(packet.get("submission_status"), Mapping) else {}
    compact: dict[str, Any] = {
        "schema_version": packet.get("schema_version"),
        "protocol": packet.get("protocol"),
        "question_id": packet.get("question_id"),
        "question_raw_vi": packet.get("question_raw_vi"),
        "typed_question": _pick(
            typed,
            "plan_shape",
            "plan_status",
            "research_route_status",
            "reporting_scope",
            "source_scope",
        ),
        "typed_question_claims": _compact_claims(typed.get("claims")),
        "proposal": {
            **_pick(proposal, "proposal_id", "answer_route", "operation_ast", "policy_fallback"),
            "claims": _compact_claims(proposal.get("claims")),
        },
        "verification": _compact_status(packet.get("verification")),
        "blocked_reason_codes": list(packet.get("blocked_reason_codes") or [])[:16],
        "candidate_context": [
            compact_candidate
            for item in (packet.get("candidate_context") or [])[:6]
            if isinstance(item, Mapping)
            for compact_candidate in [_compact_candidate(item)]
            if compact_candidate
        ],
        "retrieval_context": _pick(
            retrieval,
            "top_k_candidate_count",
            "candidate_context_is_navigation_only",
            "candidate_fields_preserved",
        ),
        "allowed_source_refs": list(packet.get("allowed_source_refs") or [])[:16],
        "submission_status": _pick(status, "prediction_tier", "verification_class", "strict_authority"),
        "resolved_prediction": _compact_receipt(packet.get("resolved_prediction")),
        "e2e_receipt": _compact_receipt(packet.get("e2e_receipt")),
        "authority_boundary": packet.get("authority_boundary"),
        "prompt_profile": profile_name,
    }
    return compact


__all__ = [
    "COMPACT_PROMPT_PROFILE",
    "DEFAULT_PROMPT_PROFILE",
    "ONE_JSON_OBJECT_DIRECTIVE",
    "PROMPT_PROFILES",
    "render_prompt",
    "system_instruction",
]
