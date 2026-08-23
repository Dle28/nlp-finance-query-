"""Fail-closed materialization of human decisions for V3 metadata proposals.

The upstream source review is intentionally only a proposal.  This module
binds a separately supplied human-decision sidecar to that proposal and emits
metadata-overlay *candidates*.  It neither edits metadata nor turns a routing
decision into evidence, a label, or an answer.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


PROTOCOL = "v3_metadata_human_approval_materialization_v1"
ALLOWED_DECISIONS = frozenset({"approve", "reject", "uncertain"})


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Expected JSON object rows: {path}")
    return rows


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def source_contract(*, metadata_overlay_materialization_allowed: bool) -> dict[str, bool]:
    return {
        "candidate_only": True,
        "evidence_eligible": False,
        "training_eligible": False,
        "submission_eligible": False,
        "promotion_allowed": False,
        "may_select_final_candidate": False,
        "may_select_value": False,
        "may_execute_formula": False,
        "metadata_overlay_materialization_allowed": metadata_overlay_materialization_allowed,
    }


def _require_hash(path: Path, expected: object, label: str) -> str:
    actual = sha256_file(path)
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(f"SHA-256 mismatch for {label}")
    return actual


def _proposal_index(reviews: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    proposed = [review for review in reviews if review.get("decision") == "accept_repair"]
    indexed = {canonical_sha256(review): dict(review) for review in proposed}
    if len(indexed) != len(proposed):
        raise ValueError("Accepted source reviews have duplicate canonical identities")
    for source_sha, review in indexed.items():
        patch = review.get("proposed_patch")
        if not isinstance(patch, Mapping) or patch.get("scope") != "metadata":
            raise ValueError(f"Accepted source review {source_sha} is not a metadata proposal")
        if not review.get("authoritative_sources"):
            raise ValueError(f"Accepted source review {source_sha} lacks authoritative sources")
        if not bool(review.get("source_coordinates_checked")):
            raise ValueError(f"Accepted source review {source_sha} lacks coordinate review")
    return indexed


def _validate_approval_queue(
    *,
    queue: Sequence[Mapping[str, Any]],
    proposals: Mapping[str, Mapping[str, Any]],
    reviews_sha: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in queue:
        source_sha = str(item.get("source_review_sha256") or "")
        if not source_sha or source_sha in indexed:
            raise ValueError("Human approval queue has missing or duplicate source_review_sha256")
        if source_sha not in proposals:
            raise ValueError("Human approval queue references an unknown source proposal")
        if item.get("source_review_file_sha256") != reviews_sha:
            raise ValueError("Human approval queue is bound to a different source-review file")
        if item.get("proposed_patch") != proposals[source_sha].get("proposed_patch"):
            raise ValueError("Human approval queue proposal patch mismatch")
        if bool(item.get("human_verified")) or item.get("reviewer_decision") is not None:
            raise ValueError("Frozen human approval queue must not contain a decision")
        if bool(item.get("eligible_for_materialization")):
            raise ValueError("Frozen human approval queue must remain non-materializable")
        indexed[source_sha] = dict(item)
    if set(indexed) != set(proposals):
        raise ValueError("Human approval queue coverage differs from accepted source proposals")
    return indexed


def _validate_decisions(
    decisions: Sequence[Mapping[str, Any]], *, queue: Mapping[str, Mapping[str, Any]], queue_sha: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        source_sha = str(decision.get("source_review_sha256") or "")
        if not source_sha or source_sha in indexed:
            raise ValueError("Human decision sidecar has missing or duplicate source_review_sha256")
        if source_sha not in queue:
            raise ValueError("Human decision references a proposal outside the frozen approval queue")
        if decision.get("source_approval_queue_sha256") != queue_sha:
            raise ValueError("Human decision is bound to a different approval queue")
        if decision.get("decision") not in ALLOWED_DECISIONS:
            raise ValueError("Human decision is not an allowed value")
        reviewer = decision.get("decision_provenance")
        if not isinstance(reviewer, Mapping) or reviewer.get("reviewer_type") != "human_verified":
            raise ValueError("Human decision requires truthful human_verified provenance")
        if not str(reviewer.get("reviewer_id") or "").strip():
            raise ValueError("Human decision requires reviewer_id")
        if not str(decision.get("reviewed_at") or "").strip():
            raise ValueError("Human decision requires reviewed_at")
        if decision.get("decision") == "approve" and not bool(decision.get("source_coordinates_checked")):
            raise ValueError("Approved metadata decision requires checked source coordinates")
        if not isinstance(decision.get("notes"), str):
            raise ValueError("Human decision requires a string notes field")
        indexed[source_sha] = dict(decision)
    if set(indexed) != set(queue):
        raise ValueError("Human decision sidecar must cover every frozen approval proposal exactly once")
    return indexed


def _validated_metadata_patch(patch: Mapping[str, Any]) -> dict[str, Any]:
    if patch.get("scope") != "metadata" or not str(patch.get("field") or "").strip():
        raise ValueError("Approved metadata patch has invalid scope or field")
    operations = list(patch.get("operations") or [])
    if not operations:
        raise ValueError("Approved metadata patch has no operations")
    document_ids: set[str] = set()
    copied: list[dict[str, Any]] = []
    for operation in operations:
        document_id = str(operation.get("document_id") or "").strip()
        if not document_id or document_id in document_ids:
            raise ValueError("Approved metadata patch has missing or duplicate document_id")
        if "old_value" not in operation or "proposed_value" not in operation:
            raise ValueError("Approved metadata patch operation has incomplete values")
        document_ids.add(document_id)
        copied.append(
            {
                "document_id": document_id,
                "old_value": operation["old_value"],
                "proposed_value": operation["proposed_value"],
            }
        )
    return {
        "scope": "metadata",
        "field": str(patch["field"]),
        "operations": copied,
        "reason": str(patch.get("reason") or ""),
    }


def materialize_metadata_human_approvals(
    *,
    review_manifest_path: Path,
    metadata_reviews_path: Path,
    approval_queue_manifest_path: Path,
    approval_queue_path: Path,
    decisions_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create non-mutating metadata-overlay candidates from human decisions.

    The decision sidecar is deliberately separate from the generated blank
    queue.  This prevents a source-review or queue generator from quietly
    approving its own proposal.
    """
    review_manifest = _read_json(review_manifest_path)
    reviews_sha = _require_hash(
        metadata_reviews_path,
        ((review_manifest.get("outputs") or {}).get("metadata_reviews") or {}).get("sha256"),
        "metadata source reviews",
    )
    proposals = _proposal_index(_read_jsonl(metadata_reviews_path))

    approval_manifest = _read_json(approval_queue_manifest_path)
    queue_sha = _require_hash(
        approval_queue_path,
        ((approval_manifest.get("outputs") or {}).get("queue") or {}).get("sha256"),
        "human approval queue",
    )
    queue_input_reviews = ((approval_manifest.get("inputs") or {}).get("metadata_reviews") or {}).get("sha256")
    if queue_input_reviews != reviews_sha:
        raise ValueError("Human approval queue manifest is not bound to the supplied source reviews")
    queue = _validate_approval_queue(queue=_read_jsonl(approval_queue_path), proposals=proposals, reviews_sha=reviews_sha)
    decisions = _validate_decisions(_read_jsonl(decisions_path), queue=queue, queue_sha=queue_sha)

    approved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for source_sha in sorted(decisions):
        decision = decisions[source_sha]
        source_review = proposals[source_sha]
        common = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "source_review_sha256": source_sha,
            "source_review_file_sha256": reviews_sha,
            "source_approval_queue_sha256": queue_sha,
            "question_id": source_review.get("question_id"),
            "stage_id": source_review.get("stage_id"),
            "role": source_review.get("role"),
            "human_decision": decision["decision"],
            "human_decision_provenance": dict(decision["decision_provenance"]),
            "human_reviewed_at": decision["reviewed_at"],
            "human_source_coordinates_checked": bool(decision.get("source_coordinates_checked")),
            "human_notes": decision["notes"],
            "authoritative_sources": list(source_review.get("authoritative_sources") or []),
        }
        if decision["decision"] == "approve":
            approved.append(
                {
                    **common,
                    "metadata_patch": _validated_metadata_patch(source_review["proposed_patch"]),
                    "eligible_for_metadata_overlay": True,
                    "source_contract": source_contract(metadata_overlay_materialization_allowed=True),
                }
            )
        else:
            unresolved.append(
                {
                    **common,
                    "metadata_patch": None,
                    "eligible_for_metadata_overlay": False,
                    "reason_code": "HUMAN_REJECTED" if decision["decision"] == "reject" else "HUMAN_UNCERTAIN",
                    "source_contract": source_contract(metadata_overlay_materialization_allowed=False),
                }
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    approved_path = output_dir / "approved_metadata_overlay_candidates_v1.jsonl"
    unresolved_path = output_dir / "rejected_or_unresolved_metadata_approvals_v1.jsonl"
    _write_jsonl(approved_path, approved)
    _write_jsonl(unresolved_path, unresolved)
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "inputs": {
            "review_manifest": {"path": str(review_manifest_path), "sha256": sha256_file(review_manifest_path)},
            "metadata_reviews": {"path": str(metadata_reviews_path), "sha256": reviews_sha},
            "approval_queue_manifest": {"path": str(approval_queue_manifest_path), "sha256": sha256_file(approval_queue_manifest_path)},
            "approval_queue": {"path": str(approval_queue_path), "sha256": queue_sha},
            "human_decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)},
        },
        "outputs": {
            "approved": {"path": str(approved_path), "sha256": sha256_file(approved_path)},
            "unresolved": {"path": str(unresolved_path), "sha256": sha256_file(unresolved_path)},
        },
        "counts": {
            "proposal_count": len(proposals),
            "decision_count": len(decisions),
            "approved_count": len(approved),
            "rejected_or_unresolved_count": len(unresolved),
            "decision_counts": dict(sorted(Counter(row["decision"] for row in decisions.values()).items())),
        },
        "repairs_applied": False,
        "source_contract": source_contract(metadata_overlay_materialization_allowed=False),
    }
    manifest_path = output_dir / "metadata_human_approval_materialization_v1.manifest.json"
    _write_json(manifest_path, result)
    return {**result, "manifest_path": str(manifest_path)}
