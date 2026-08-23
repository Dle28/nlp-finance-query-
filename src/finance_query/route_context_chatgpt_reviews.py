"""Hash-bound ChatGPT review of literal route-context repair candidates.

The reviewer may accept or reject the semantics of an already extracted
question span.  It may not add a span, change a question plan or route, select
an operand, or authorize execution.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

from .route_context_repairs import PROTOCOL as QUEUE_PROTOCOL
from .route_context_repairs import canonical_sha256, sha256_file


PROTOCOL = "vifinqa_route_context_chatgpt_decision_v1"
REVIEWABLE_STATUS = "machine_provisional_requires_human"
BLOCKED_STATUS = "blocked_no_literal_repair"


class RouteContextChatGPTReviewError(ValueError):
    """Raised when route-context review inputs are stale or over-authorizing."""


def _rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RouteContextChatGPTReviewError(f"{path}:{line_number} must be an object")
        rows.append(value)
    return rows


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RouteContextChatGPTReviewError(f"{path} must be an object")
    return value


def _valid_item(row: Mapping[str, Any]) -> bool:
    payload = {
        key: value
        for key, value in row.items()
        if key not in {"schema_version", "protocol", "repair_item_sha256", "source_contract"}
    }
    return (
        row.get("protocol") == QUEUE_PROTOCOL
        and row.get("repair_item_sha256") == canonical_sha256(payload)
    )


def _candidate_key(question_id: int, field: str, candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        question_id,
        field,
        int(candidate.get("char_start", -1)),
        int(candidate.get("char_end", -1)),
        str(candidate.get("value") or ""),
    )


def _reviewer_provenance() -> dict[str, Any]:
    return {
        "reviewer_type": "chatgpt_verified",
        "reviewer_id": "chatgpt-gpt5-route-context-reviewer-v1",
        "model_family": "GPT-5",
        "review_policy": "fail_closed_literal_operation_semantics_v1",
        "authority_grant": {
            "granted_by": "campaign_owner",
            "grant_scope": "route_context_literal_review_gate_equivalence",
            "grant_basis": "explicit_user_instruction",
        },
    }


def build_decisions(
    *, queue: Path, queue_manifest: Path, review_spec: Path, output_dir: Path
) -> dict[str, Any]:
    """Review every literal proposal and preserve every no-literal blocker."""

    manifest = _json(queue_manifest)
    if manifest.get("protocol") != QUEUE_PROTOCOL:
        raise RouteContextChatGPTReviewError("unexpected route-context manifest protocol")
    expected_queue_sha = (((manifest.get("outputs") or {}).get("queue") or {}).get("sha256"))
    queue_sha = sha256_file(queue)
    if expected_queue_sha != queue_sha:
        raise RouteContextChatGPTReviewError("route-context queue SHA-256 mismatch")

    queue_rows = _rows(queue)
    if any(not _valid_item(row) for row in queue_rows):
        raise RouteContextChatGPTReviewError("route-context item SHA-256 mismatch")
    question_ids = [int(row["question_id"]) for row in queue_rows]
    if len(question_ids) != len(set(question_ids)):
        raise RouteContextChatGPTReviewError("duplicate route-context question IDs")

    spec = _json(review_spec)
    if spec.get("default_candidate_policy") != "approve_literal_label_after_question_semantic_review":
        raise RouteContextChatGPTReviewError("default candidate policy is not authorized")
    if spec.get("blocked_item_policy") != "confirm_incomplete_literal_blocker_fail_closed":
        raise RouteContextChatGPTReviewError("blocked item policy is not fail-closed")
    overrides = spec.get("candidate_overrides") or []
    if not isinstance(overrides, list):
        raise RouteContextChatGPTReviewError("candidate_overrides must be a list")
    override_by_key: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for override in overrides:
        if not isinstance(override, Mapping):
            raise RouteContextChatGPTReviewError("candidate override must be an object")
        key = (
            int(override.get("question_id", -1)),
            str(override.get("field") or ""),
            int(override.get("char_start", -1)),
            int(override.get("char_end", -1)),
            str(override.get("value") or ""),
        )
        if key in override_by_key:
            raise RouteContextChatGPTReviewError("duplicate candidate override")
        if override.get("decision") not in {"approve_literal_label", "reject_literal_label"}:
            raise RouteContextChatGPTReviewError("candidate override decision is invalid")
        if not str(override.get("rationale") or "").strip():
            raise RouteContextChatGPTReviewError("candidate override rationale is required")
        if override.get("decision") == "reject_literal_label" and not override.get("reason_codes"):
            raise RouteContextChatGPTReviewError("rejected candidate requires reason codes")
        override_by_key[key] = override

    decisions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    candidate_counts: Counter[str] = Counter()
    consumed_overrides: set[tuple[Any, ...]] = set()
    for item in sorted(queue_rows, key=lambda row: int(row["question_id"])):
        question_id = int(item["question_id"])
        status = str(item.get("repair_status") or "")
        proposals = item.get("literal_repair_candidates") or {}
        if not isinstance(proposals, Mapping):
            raise RouteContextChatGPTReviewError(f"Q{question_id} proposals must be an object")

        candidate_reviews: list[dict[str, Any]] = []
        approved_repairs: dict[str, list[dict[str, Any]]] = {}
        item_reasons: list[str] = []
        for field in sorted(proposals):
            candidates = proposals[field]
            if not isinstance(candidates, list):
                raise RouteContextChatGPTReviewError(f"Q{question_id} candidate list is invalid")
            for candidate in candidates:
                if not isinstance(candidate, Mapping):
                    raise RouteContextChatGPTReviewError(f"Q{question_id} candidate is invalid")
                question = str(item.get("question") or "")
                start = int(candidate.get("char_start", -1))
                end = int(candidate.get("char_end", -1))
                literal = str(candidate.get("literal_text") or "")
                if start < 0 or end <= start or question[start:end] != literal:
                    raise RouteContextChatGPTReviewError(f"Q{question_id} literal span drift")
                key = _candidate_key(question_id, str(field), candidate)
                override = override_by_key.get(key)
                if override is None:
                    candidate_decision = "approve_literal_label"
                    rationale = "Nhãn phép toán khớp nghĩa trực tiếp của literal span trong câu hỏi."
                    reason_codes: list[str] = []
                else:
                    consumed_overrides.add(key)
                    candidate_decision = str(override["decision"])
                    rationale = str(override["rationale"])
                    reason_codes = [str(value) for value in override.get("reason_codes") or []]
                candidate_counts[candidate_decision] += 1
                if candidate_decision == "approve_literal_label":
                    approved_repairs.setdefault(str(field), []).append(dict(candidate))
                else:
                    item_reasons.extend(reason_codes)
                candidate_reviews.append({
                    "field": str(field),
                    "candidate_sha256": canonical_sha256({"field": str(field), "candidate": candidate}),
                    "candidate": dict(candidate),
                    "decision": candidate_decision,
                    "reason_codes": reason_codes,
                    "rationale": rationale,
                })

        if status == BLOCKED_STATUS:
            decision = "confirmed_incomplete_literal_blocker"
            rationale = "Ít nhất một trường còn thiếu không có literal repair; giữ nguyên blocker fail-closed."
        elif status == REVIEWABLE_STATUS:
            if not candidate_reviews:
                raise RouteContextChatGPTReviewError(f"Q{question_id} reviewable item lacks candidates")
            approved_count = sum(
                review["decision"] == "approve_literal_label" for review in candidate_reviews
            )
            if approved_count == len(candidate_reviews):
                decision = "approve_all_literal_candidates"
                rationale = "Mọi literal span đề xuất đều mang đúng tín hiệu phép toán trong câu hỏi."
            elif approved_count:
                decision = "approve_partial_literal_candidates"
                rationale = "Chỉ giữ các literal label đúng nghĩa; loại các regex collision khỏi repair set."
            else:
                decision = "reject_all_literal_candidates"
                rationale = "Không literal label nào chứng minh đúng operation semantics của câu hỏi."
        else:
            raise RouteContextChatGPTReviewError(f"Q{question_id} repair status is invalid")
        counts[decision] += 1

        payload = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "question": item.get("question"),
            "repair_item_sha256": item.get("repair_item_sha256"),
            "source_queue_sha256": queue_sha,
            "repair_status": status,
            "decision": decision,
            "reason_codes": sorted(set(item_reasons)),
            "rationale": rationale,
            "candidate_reviews": candidate_reviews,
            "approved_context_repairs": approved_repairs,
            "decision_provenance": _reviewer_provenance(),
            "source_contract": {
                "literal_candidate_review_completed": True,
                "eligible_for_materialization": False,
                "may_change_question_plan": False,
                "may_change_route": False,
                "may_select_value": False,
                "may_execute_formula": False,
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        decisions.append({**payload, "decision_sha256": canonical_sha256(payload)})

    if consumed_overrides != set(override_by_key):
        raise RouteContextChatGPTReviewError("candidate override does not match the frozen queue")

    output_dir.mkdir(parents=True, exist_ok=False)
    decisions_path = output_dir / "route_context_chatgpt_decisions_v1.jsonl"
    decisions_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in decisions
        ),
        encoding="utf-8",
    )
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "status": "reviewed_not_materialized",
        "inputs": {
            "queue": {"path": str(queue), "sha256": queue_sha},
            "queue_manifest": {"path": str(queue_manifest), "sha256": sha256_file(queue_manifest)},
            "review_spec": {"path": str(review_spec), "sha256": sha256_file(review_spec)},
        },
        "outputs": {"decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)}},
        "counts": {
            "decision_count": len(decisions),
            **dict(sorted(counts.items())),
            "candidate_decision_counts": dict(sorted(candidate_counts.items())),
        },
        "source_contract": {
            "chatgpt_authority_grant_present": True,
            "eligible_for_materialization": False,
            "may_change_question_plan": False,
            "may_change_route": False,
            "may_execute_formula": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "route_context_chatgpt_decisions_v1.manifest.json"
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**result, "manifest_path": str(manifest_path)}
