"""Fail-closed ChatGPT critique of hash-bound navigation evidence packets."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

from .binding_conflict_workbench import canonical_sha256, sha256_file
from .navigation_review_evidence import PROTOCOL as EVIDENCE_PROTOCOL


PROTOCOL = "vifinqa_navigation_chatgpt_decision_v1"


class NavigationChatGPTReviewError(ValueError):
    """Raised when a navigation review is stale, incomplete, or unsafe."""


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise NavigationChatGPTReviewError(f"{path}:{line_number} must be an object")
        values.append(value)
    return values


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise NavigationChatGPTReviewError(f"{path} must be an object")
    return value


def _valid_packet(row: Mapping[str, Any]) -> bool:
    payload = {
        key: value
        for key, value in row.items()
        if key not in {"schema_version", "protocol", "packet_sha256", "source_contract"}
    }
    return row.get("protocol") == EVIDENCE_PROTOCOL and row.get("packet_sha256") == canonical_sha256(payload)


def _reviewer_provenance() -> dict[str, Any]:
    return {
        "reviewer_type": "chatgpt_verified",
        "reviewer_id": "chatgpt-gpt5-navigation-reviewer-v1",
        "model_family": "GPT-5",
        "review_policy": "fail_closed_exact_row_navigation_semantics_v1",
        "authority_grant": {
            "granted_by": "campaign_owner",
            "grant_scope": "navigation_candidate_review_gate_equivalence",
            "grant_basis": "explicit_user_instruction",
        },
    }


def _failed_gate_reasons(candidate: Mapping[str, Any]) -> list[str]:
    gate_vector = candidate.get("gate_vector") or {}
    reasons = [
        f"UPSTREAM_{str(field).upper()}_MISMATCH"
        for field in ("entity", "scope", "year", "table_type")
        if not bool((gate_vector.get(field) or {}).get("pass"))
    ]
    return reasons or ["EXACT_CONTEXT_SEMANTIC_REVIEW_REQUIRED"]


def build_decisions(
    *, evidence_packets: Path, evidence_manifest: Path, review_spec: Path, output_dir: Path
) -> dict[str, Any]:
    """Review all 620 nearby candidates without selecting a value or changing a route."""

    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite navigation decisions: {output_dir}")
    manifest = _json(evidence_manifest)
    if manifest.get("protocol") != EVIDENCE_PROTOCOL:
        raise NavigationChatGPTReviewError("unexpected navigation evidence protocol")
    packets_sha = sha256_file(evidence_packets)
    if (((manifest.get("outputs") or {}).get("packets") or {}).get("sha256")) != packets_sha:
        raise NavigationChatGPTReviewError("navigation evidence SHA-256 mismatch")
    packets = _rows(evidence_packets)
    if any(not _valid_packet(packet) for packet in packets):
        raise NavigationChatGPTReviewError("navigation evidence packet SHA-256 mismatch")
    if len(packets) != 38 or len({int(packet["question_id"]) for packet in packets}) != 38:
        raise NavigationChatGPTReviewError("navigation evidence coverage mismatch")

    spec = _json(review_spec)
    if spec.get("no_exact_context_policy") != "confirm_upstream_blocker_fail_closed":
        raise NavigationChatGPTReviewError("no-exact-context policy is unsafe")
    reviews = spec.get("exact_context_reviews") or []
    if not isinstance(reviews, list):
        raise NavigationChatGPTReviewError("exact_context_reviews must be a list")
    review_by_question: dict[int, Mapping[str, Any]] = {}
    for review in reviews:
        if not isinstance(review, Mapping):
            raise NavigationChatGPTReviewError("exact-context review must be an object")
        question_id = int(review.get("question_id", -1))
        if question_id in review_by_question:
            raise NavigationChatGPTReviewError("duplicate exact-context review")
        decision = review.get("decision")
        if decision not in {"approve_navigation_candidate_semantics", "reject_candidate_set"}:
            raise NavigationChatGPTReviewError(f"Q{question_id} decision is invalid")
        if not str(review.get("rationale") or "").strip():
            raise NavigationChatGPTReviewError(f"Q{question_id} rationale is required")
        if decision == "reject_candidate_set" and not review.get("reason_codes"):
            raise NavigationChatGPTReviewError(f"Q{question_id} rejection needs reason codes")
        if decision == "approve_navigation_candidate_semantics" and not review.get("selected_candidate_evidence_sha256"):
            raise NavigationChatGPTReviewError(f"Q{question_id} approval needs a selected candidate")
        review_by_question[question_id] = review

    exact_questions = {
        int(packet["question_id"])
        for packet in packets
        if any(candidate.get("exact_identity_scope_year_table") for candidate in packet.get("candidate_evidence") or [])
    }
    if set(review_by_question) != exact_questions:
        raise NavigationChatGPTReviewError("exact-context review coverage mismatch")

    decisions: list[dict[str, Any]] = []
    decision_counts: Counter[str] = Counter()
    candidate_counts: Counter[str] = Counter()
    for packet in sorted(packets, key=lambda row: int(row["question_id"])):
        question_id = int(packet["question_id"])
        candidates = packet.get("candidate_evidence") or []
        exact_candidates = [candidate for candidate in candidates if candidate.get("exact_identity_scope_year_table")]
        review = review_by_question.get(question_id)
        selected_sha = str((review or {}).get("selected_candidate_evidence_sha256") or "")
        candidate_reviews: list[dict[str, Any]] = []
        selected_candidate: Mapping[str, Any] | None = None
        for candidate in candidates:
            candidate_sha = str(candidate.get("candidate_evidence_sha256") or "")
            if not candidate.get("exact_identity_scope_year_table"):
                candidate_decision = "reject_navigation_candidate"
                reason_codes = _failed_gate_reasons(candidate)
                rationale = "Candidate vi phạm ít nhất một gate identity/scope/year/table-type nên không thể đại diện câu hỏi."
            elif candidate_sha == selected_sha:
                candidate_decision = "approve_navigation_candidate_semantics"
                reason_codes = []
                rationale = str(review.get("rationale") or "") if review else ""
                selected_candidate = candidate
            else:
                candidate_decision = "reject_navigation_candidate"
                reason_codes = [str(value) for value in (review or {}).get("reason_codes") or []]
                rationale = str((review or {}).get("rationale") or "")
                if not reason_codes:
                    reason_codes = ["EXACT_CONTEXT_CANDIDATE_NOT_SELECTED"]
            candidate_counts[candidate_decision] += 1
            candidate_reviews.append({
                "candidate_evidence_sha256": candidate_sha,
                "decision": candidate_decision,
                "reason_codes": reason_codes,
                "rationale": rationale,
            })

        if not exact_candidates:
            decision = "confirmed_upstream_blocker"
            reason_codes = ["NO_CANDIDATE_PASSES_IDENTITY_SCOPE_YEAR_TABLE"]
            rationale = "Không candidate nào qua đồng thời entity, scope, year và table-type; giữ blocker hiện tại."
        elif review is None:
            raise NavigationChatGPTReviewError(f"Q{question_id} exact candidate lacks review")
        else:
            decision = str(review["decision"])
            reason_codes = [str(value) for value in review.get("reason_codes") or []]
            rationale = str(review["rationale"])
            if decision == "approve_navigation_candidate_semantics":
                if selected_candidate is None:
                    raise NavigationChatGPTReviewError(f"Q{question_id} selected candidate is stale")
                if sum(row["decision"] == decision for row in candidate_reviews) != 1:
                    raise NavigationChatGPTReviewError(f"Q{question_id} must select exactly one candidate")
            elif selected_sha:
                raise NavigationChatGPTReviewError(f"Q{question_id} rejection cannot select a candidate")
        decision_counts[decision] += 1
        payload = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "question": packet.get("question"),
            "packet_sha256": packet.get("packet_sha256"),
            "source_evidence_packets_sha256": packets_sha,
            "decision": decision,
            "reason_codes": reason_codes,
            "rationale": rationale,
            "candidate_reviews": candidate_reviews,
            "selected_candidate": dict(selected_candidate) if selected_candidate else None,
            "decision_provenance": _reviewer_provenance(),
            "source_contract": {
                "navigation_review_gate_authorized": decision == "approve_navigation_candidate_semantics",
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

    output_dir.mkdir(parents=True, exist_ok=False)
    decisions_path = output_dir / "navigation_chatgpt_decisions_v1.jsonl"
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
            "evidence_packets": {"path": str(evidence_packets), "sha256": packets_sha},
            "evidence_manifest": {"path": str(evidence_manifest), "sha256": sha256_file(evidence_manifest)},
            "review_spec": {"path": str(review_spec), "sha256": sha256_file(review_spec)},
        },
        "outputs": {"decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)}},
        "counts": {
            "decision_count": len(decisions),
            **dict(sorted(decision_counts.items())),
            "candidate_decision_counts": dict(sorted(candidate_counts.items())),
        },
        "source_contract": {
            "chatgpt_authority_grant_present": True,
            "eligible_for_materialization": False,
            "may_change_question_plan": False,
            "may_change_route": False,
            "may_select_value": False,
            "may_execute_formula": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "navigation_chatgpt_decisions_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
