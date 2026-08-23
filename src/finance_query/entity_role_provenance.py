"""Discover exact-document candidates that may prove an issuer's entity role."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .semantic_approvals import canonical_sha256, sha256_file


PROTOCOL = "vifinqa_entity_role_provenance_candidate_queue_v1"
ROLE_LITERAL = re.compile(r"\bcông ty m(?:ẹ|ệ)\b", re.IGNORECASE)


class EntityRoleProvenanceError(ValueError):
    """Raised when discovery loses exact-document provenance."""


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise EntityRoleProvenanceError(f"{path}:{line_number} must be an object")
        values.append(value)
    return values


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise EntityRoleProvenanceError(f"{path} must be an object")
    return value


def _repo_relative(path: Path, repository_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repository_root.resolve()))
    except ValueError as error:
        raise EntityRoleProvenanceError("source path escapes the repository") from error


def build_candidate_queue(
    *,
    issue_briefs: Path,
    campaign_bundle: Path,
    structured_tables: Path,
    repository_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a review-only queue from role literals in exact extracted documents."""

    bundle = _json(campaign_bundle)
    items = bundle.get("items") or []
    item_by_question = {
        int(item["question_id"]): item
        for item in items
        if isinstance(item, Mapping) and item.get("question_id") is not None
    }
    source_by_document: dict[str, tuple[Path, str]] = {}
    for row in _rows(structured_tables):
        document_uid = str(row.get("document_id") or "")
        provenance = row.get("source_provenance") or {}
        source_path = Path(str(provenance.get("source_path") or ""))
        source_sha = str(provenance.get("source_sha256") or "")
        if not document_uid or not source_path.is_file() or len(source_sha) != 64:
            continue
        previous = source_by_document.get(document_uid)
        current = (source_path.resolve(), source_sha)
        if previous is not None and previous != current:
            raise EntityRoleProvenanceError("document maps to conflicting source files")
        source_by_document[document_uid] = current

    queue: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for issue in _rows(issue_briefs):
        if issue.get("strict_status") != "UNRESOLVED":
            continue
        # A source-title literal is handled by the existing title-bound role
        # reviewer.  This queue is only for the residual claims that need a
        # distinct exact-document provenance anchor.
        if issue.get("source_role_literal_present") is True:
            counts["already_bound_in_source_title"] += 1
            continue
        question_id = int(issue["question_id"])
        item = item_by_question.get(question_id)
        document_uid = str((item or {}).get("documentUid") or "")
        source_record = source_by_document.get(document_uid)
        if source_record is None:
            raise EntityRoleProvenanceError(f"Q{question_id} lacks exact extracted source")
        source_path, expected_source_sha = source_record
        if sha256_file(source_path) != expected_source_sha:
            raise EntityRoleProvenanceError(f"Q{question_id} source SHA-256 mismatch")
        lines = source_path.read_text(encoding="utf-8").splitlines()
        candidates: list[dict[str, Any]] = []
        for index, raw_text in enumerate(lines):
            if not ROLE_LITERAL.search(raw_text):
                continue
            payload = {
                "document_uid": document_uid,
                "source_file_sha256": expected_source_sha,
                "source_path": _repo_relative(source_path, repository_root),
                "line_number": index + 1,
                "raw_text": raw_text,
                "raw_text_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                "context_before": lines[index - 1] if index > 0 else "",
                "context_after": lines[index + 1] if index + 1 < len(lines) else "",
            }
            candidates.append({**payload, "candidate_sha256": canonical_sha256(payload)})
        counts["documents_with_role_literal" if candidates else "documents_without_role_literal"] += 1
        payload = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            "question_id": question_id,
            "claim_entity_identity": issue.get("claim_entity_identity"),
            "claim_entity_role": issue.get("claim_entity_role"),
            "document_uid": document_uid,
            "source_file_sha256": expected_source_sha,
            "issue_brief_sha256": issue.get("issue_brief_sha256"),
            "candidates": candidates,
            "review_prompts": [
                {"id": "refers_to_issuer", "question": "Candidate có nói về chính issuer trong claim, không phải công ty mẹ khác?"},
                {"id": "asserts_parent_role", "question": "Candidate có khẳng định issuer giữ vai trò công ty mẹ?"},
                {"id": "same_document", "question": "Candidate thuộc đúng extracted document đã hash-bind với claim?"},
                {"id": "sufficient_for_certificate", "question": "Dòng này có đủ cụ thể để đưa vào entity_role source anchors?"},
            ],
            "decision_options": ["approve_role_provenance", "reject_candidate_set", "needs_investigation"],
            "source_contract": {
                "research_only": True,
                "evidence_eligible": False,
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        queue.append({**payload, "queue_item_sha256": canonical_sha256(payload)})

    queue.sort(key=lambda row: int(row["question_id"]))
    output_dir.mkdir(parents=True, exist_ok=False)
    queue_path = output_dir / "entity_role_provenance_candidates_v1.jsonl"
    queue_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in queue),
        encoding="utf-8",
    )
    result = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "inputs": {
            "issue_briefs": {"path": str(issue_briefs), "sha256": sha256_file(issue_briefs)},
            "campaign_bundle": {"path": str(campaign_bundle), "sha256": sha256_file(campaign_bundle)},
            "structured_tables": {"path": str(structured_tables), "sha256": sha256_file(structured_tables)},
        },
        "outputs": {"queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)}},
        "counts": {
            "queue_item_count": len(queue),
            "candidate_count": sum(len(row["candidates"]) for row in queue),
            **dict(sorted(counts.items())),
        },
        "source_contract": {
            "research_only": True,
            "evidence_eligible": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "entity_role_provenance_candidates_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
