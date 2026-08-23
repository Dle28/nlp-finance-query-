"""Review issuer-to-subsidiary relations as exact parent-role evidence.

This lane is intentionally separate from the older literal ``công ty mẹ``
review.  A standalone-report scope never proves a parent role.  The only new
inference allowed here is:

    exact issuer identity + issuer has/controls a subsidiary -> issuer is parent

Two independently identified ChatGPT reviewers must agree on every semantic
check before the deterministic reconciler can authorize the role gate.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .review_authority import validate_gate_reviewer
from .semantic_approvals import SEMANTIC_DECISION_PROTOCOL, canonical_sha256, sha256_file


QUEUE_PROTOCOL = "vifinqa_entity_role_relational_candidate_queue_v1"
DECISION_PROTOCOL = "vifinqa_entity_role_relational_chatgpt_decision_v1"
ADJUDICATION_PROTOCOL = "vifinqa_entity_role_relational_dual_chatgpt_adjudication_v1"
AUGMENTATION_PROTOCOL = "vifinqa_semantic_entity_role_relational_augmentation_v1"
LEGACY_QUEUE_PROTOCOL = "vifinqa_entity_role_provenance_candidate_queue_v1"
LEGACY_DECISION_PROTOCOL = "vifinqa_entity_role_provenance_chatgpt_decision_v1"
AUTHORITY_SCOPE = "entity_role_review_gate_equivalence"
PROPOSER_ROLE = "authorized_ai_entity_role_evidence_proposer"
CRITIC_ROLE = "authorized_ai_entity_role_evidence_critic"
RECONCILER_ROLE = "deterministic_entity_role_dual_review_reconciler"

SEMANTIC_CHECKS = frozenset(
    {
        "issuer_coreference_valid",
        "issuer_has_subsidiary_relation",
        "parent_role_logically_proven",
        "reporting_scope_checked_independently",
        "same_document",
        "sufficient_for_certificate",
    }
)

# Conservative discovery only.  Reviewers still have to select and approve one
# exact candidate; matching this expression grants no authority by itself.
RELATIONAL_PATTERNS = (
    re.compile(r"\bcông ty có\s+(?:\d+|các)\s+công ty con\b", re.IGNORECASE),
    re.compile(r"\b(?:công ty con|các công ty con) của công ty\b", re.IGNORECASE),
    re.compile(r"\bcông ty đã thành lập\b.*\bcông ty con\b", re.IGNORECASE),
    re.compile(r"\bcông ty\b.*\bđầu tư vào các công ty con\b", re.IGNORECASE),
    re.compile(r"\btập đoàn(?:\s+dic)? có\b.*\bcông ty con\b", re.IGNORECASE),
)


class EntityRoleRelationalReviewError(ValueError):
    """Raised when relational role evidence loses lineage or consensus."""


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise EntityRoleRelationalReviewError(f"{path}:{line_number} must be an object")
        values.append(value)
    return values


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise EntityRoleRelationalReviewError(f"{path} must be an object")
    return value


def _valid_hash(row: Mapping[str, Any], field: str) -> bool:
    expected = str(row.get(field) or "")
    payload = {key: value for key, value in row.items() if key != field}
    return len(expected) == 64 and expected == canonical_sha256(payload)


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _repo_relative(path: Path, repository_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repository_root.resolve()))
    except ValueError as error:
        raise EntityRoleRelationalReviewError("source path escapes repository root") from error


def _source_index(structured_tables: Path) -> dict[str, tuple[Path, str]]:
    sources: dict[str, tuple[Path, str]] = {}
    for row in _rows(structured_tables):
        document_uid = str(row.get("document_id") or "")
        provenance = row.get("source_provenance") or {}
        if not isinstance(provenance, Mapping):
            continue
        path = Path(str(provenance.get("source_path") or ""))
        source_sha = str(provenance.get("source_sha256") or "")
        if not document_uid or not path.is_file() or len(source_sha) != 64:
            continue
        current = (path.resolve(), source_sha)
        if document_uid in sources and sources[document_uid] != current:
            raise EntityRoleRelationalReviewError("document maps to conflicting source files")
        sources[document_uid] = current
    return sources


def build_relational_candidate_queue(
    *,
    prior_candidate_queue: Path,
    prior_decisions: Path,
    structured_tables: Path,
    repository_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a residual queue only from prior literal-review rejections."""

    prior_queue_sha = sha256_file(prior_candidate_queue)
    old_queue: dict[int, Mapping[str, Any]] = {}
    for item in _rows(prior_candidate_queue):
        if item.get("protocol") != LEGACY_QUEUE_PROTOCOL or not _valid_hash(item, "queue_item_sha256"):
            raise EntityRoleRelationalReviewError("prior candidate queue is invalid")
        question_id = int(item["question_id"])
        if question_id in old_queue:
            raise EntityRoleRelationalReviewError("prior candidate queue has duplicate question IDs")
        old_queue[question_id] = item

    rejected: dict[int, Mapping[str, Any]] = {}
    for decision in _rows(prior_decisions):
        if (
            decision.get("protocol") != LEGACY_DECISION_PROTOCOL
            or not _valid_hash(decision, "decision_sha256")
            or decision.get("source_candidate_queue_sha256") != prior_queue_sha
        ):
            raise EntityRoleRelationalReviewError("prior role decision is invalid or stale")
        question_id = int(decision["question_id"])
        item = old_queue.get(question_id)
        if item is None or decision.get("queue_item_sha256") != item.get("queue_item_sha256"):
            raise EntityRoleRelationalReviewError("prior role decision references a missing queue item")
        if decision.get("decision") == "reject_candidate_set":
            rejected[question_id] = decision

    sources = _source_index(structured_tables)
    queue: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for question_id in sorted(rejected):
        old_item = old_queue[question_id]
        document_uid = str(old_item.get("document_uid") or "")
        source = sources.get(document_uid)
        if source is None:
            raise EntityRoleRelationalReviewError(f"Q{question_id} lacks an exact source document")
        source_path, expected_sha = source
        if sha256_file(source_path) != expected_sha or expected_sha != old_item.get("source_file_sha256"):
            raise EntityRoleRelationalReviewError(f"Q{question_id} source SHA-256 mismatch")
        lines = source_path.read_text(encoding="utf-8").splitlines()
        candidates: list[dict[str, Any]] = []
        for index, raw_text in enumerate(lines):
            if not any(pattern.search(raw_text) for pattern in RELATIONAL_PATTERNS):
                continue
            payload = {
                "assertion_type": "issuer_subsidiary_relation",
                "document_uid": document_uid,
                "source_file_sha256": expected_sha,
                "source_path": _repo_relative(source_path, repository_root),
                "line_number": index + 1,
                "raw_text": raw_text,
                "raw_text_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                "context_before": lines[index - 1] if index > 0 else "",
                "context_after": lines[index + 1] if index + 1 < len(lines) else "",
            }
            candidates.append({**payload, "candidate_sha256": canonical_sha256(payload)})
        counts["documents_with_relational_candidate" if candidates else "documents_without_relational_candidate"] += 1
        payload = {
            "schema_version": 1,
            "protocol": QUEUE_PROTOCOL,
            "question_id": question_id,
            "claim_entity_identity": old_item.get("claim_entity_identity"),
            "claim_entity_role": old_item.get("claim_entity_role"),
            "document_uid": document_uid,
            "reporting_scope": "separate" if document_uid.endswith("_separate") else None,
            "source_file_sha256": expected_sha,
            "prior_queue_item_sha256": old_item.get("queue_item_sha256"),
            "prior_rejection_decision_sha256": rejected[question_id].get("decision_sha256"),
            "candidates": candidates,
            "review_prompts": [
                {"id": key, "question": question}
                for key, question in (
                    ("issuer_coreference_valid", "'Công ty' hoặc tên riêng trong dòng có đồng tham chiếu đúng issuer không?"),
                    ("issuer_has_subsidiary_relation", "Dòng có khẳng định issuer có, kiểm soát hoặc đầu tư vào công ty con không?"),
                    ("parent_role_logically_proven", "Quan hệ issuer-công ty con có đủ để suy ra role=parent không?"),
                    ("reporting_scope_checked_independently", "Scope separate có được kiểm tra riêng và không bị dùng làm bằng chứng role không?"),
                    ("same_document", "Dòng có thuộc đúng document đã hash-bind không?"),
                    ("sufficient_for_certificate", "Dòng có đủ cụ thể để đưa vào certificate không?"),
                )
            ],
            "decision_options": ["approve_relational_parent_role", "reject_relational_candidate_set"],
            "source_contract": {
                "answer_numeric_values_exposed_to_reviewer": False,
                "separate_scope_is_parent_evidence": False,
                "eligible_for_materialization": False,
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        queue.append({**payload, "queue_item_sha256": canonical_sha256(payload)})

    output_dir.mkdir(parents=True, exist_ok=False)
    queue_path = output_dir / "entity_role_relational_candidates_v1.jsonl"
    _write_jsonl(queue_path, queue)
    result = {
        "schema_version": 1,
        "protocol": QUEUE_PROTOCOL,
        "status": "residual_relational_candidates_not_reviewed",
        "inputs": {
            "prior_candidate_queue": {"path": str(prior_candidate_queue), "sha256": prior_queue_sha},
            "prior_decisions": {"path": str(prior_decisions), "sha256": sha256_file(prior_decisions)},
            "structured_tables": {"path": str(structured_tables), "sha256": sha256_file(structured_tables)},
        },
        "outputs": {"queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)}},
        "counts": {
            "queue_item_count": len(queue),
            "candidate_count": sum(len(item["candidates"]) for item in queue),
            "answer_numeric_value_exposure_count": 0,
            **dict(sorted(counts.items())),
        },
        "source_contract": {
            "answer_numeric_values_exposed_to_reviewer": False,
            "separate_scope_is_parent_evidence": False,
            "eligible_for_materialization": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "entity_role_relational_candidates_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}


def build_relational_chatgpt_decisions(
    *, candidate_queue: Path, review_spec: Path, output_dir: Path
) -> dict[str, Any]:
    """Bind one complete proposer or critic review to every residual item."""

    queue_rows = _rows(candidate_queue)
    if not queue_rows:
        raise EntityRoleRelationalReviewError("relational candidate queue must be non-empty")
    if any(row.get("protocol") != QUEUE_PROTOCOL or not _valid_hash(row, "queue_item_sha256") for row in queue_rows):
        raise EntityRoleRelationalReviewError("relational candidate queue is invalid")
    queue_by_question = {int(row["question_id"]): row for row in queue_rows}
    if len(queue_by_question) != len(queue_rows):
        raise EntityRoleRelationalReviewError("relational queue has duplicate question IDs")

    spec = _json(review_spec)
    if spec.get("protocol") != "vifinqa_entity_role_relational_chatgpt_review_spec_v1":
        raise EntityRoleRelationalReviewError("unexpected relational review spec protocol")
    provenance = spec.get("decision_provenance")
    receipt = spec.get("authority_receipt")
    if not isinstance(provenance, Mapping):
        raise EntityRoleRelationalReviewError("reviewer provenance must be a mapping")
    reviewer_role = str(provenance.get("reviewer_role") or "")
    if reviewer_role not in {PROPOSER_ROLE, CRITIC_ROLE}:
        raise EntityRoleRelationalReviewError("reviewer role must be proposer or critic")
    try:
        validate_gate_reviewer(
            provenance,
            grant_scope=AUTHORITY_SCOPE,
            authority_receipt=receipt,
            chatgpt_role=reviewer_role,
        )
    except ValueError as error:
        raise EntityRoleRelationalReviewError(str(error)) from error

    reviews = spec.get("reviews") or []
    if not isinstance(reviews, list):
        raise EntityRoleRelationalReviewError("reviews must be a list")
    by_question = {
        int(review["question_id"]): review
        for review in reviews
        if isinstance(review, Mapping) and review.get("question_id") is not None
    }
    if len(by_question) != len(reviews) or set(by_question) != set(queue_by_question):
        raise EntityRoleRelationalReviewError("relational review coverage mismatch")

    queue_sha = sha256_file(candidate_queue)
    decisions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for question_id in sorted(queue_by_question):
        item = queue_by_question[question_id]
        review = by_question[question_id]
        decision = str(review.get("decision") or "")
        if decision not in {"approve_relational_parent_role", "reject_relational_candidate_set"}:
            raise EntityRoleRelationalReviewError(f"Q{question_id} has an invalid decision")
        checks = review.get("semantic_checks")
        if not isinstance(checks, Mapping) or set(checks) != SEMANTIC_CHECKS:
            raise EntityRoleRelationalReviewError(f"Q{question_id} semantic checks are incomplete")
        rationale = str(review.get("rationale") or "").strip()
        reason_codes = [str(code) for code in review.get("reason_codes") or []]
        if not rationale:
            raise EntityRoleRelationalReviewError(f"Q{question_id} lacks a rationale")
        candidates = item.get("candidates") or []
        if any(not isinstance(candidate, Mapping) or not _valid_hash(candidate, "candidate_sha256") for candidate in candidates):
            raise EntityRoleRelationalReviewError(f"Q{question_id} candidate SHA-256 mismatch")
        selected: Mapping[str, Any] | None = None
        selected_line = review.get("selected_line_number")
        if decision == "approve_relational_parent_role":
            if any(value is not True for value in checks.values()) or not isinstance(selected_line, int):
                raise EntityRoleRelationalReviewError(f"Q{question_id} approval is incomplete")
            matches = [candidate for candidate in candidates if candidate.get("line_number") == selected_line]
            if len(matches) != 1:
                raise EntityRoleRelationalReviewError(f"Q{question_id} selected line is not unique")
            selected = matches[0]
            if reason_codes:
                raise EntityRoleRelationalReviewError(f"Q{question_id} approval cannot contain reason codes")
        else:
            if selected_line is not None or not reason_codes:
                raise EntityRoleRelationalReviewError(f"Q{question_id} rejection is incomplete")
        payload = {
            "schema_version": 1,
            "protocol": DECISION_PROTOCOL,
            "question_id": question_id,
            "claim_entity_identity": item.get("claim_entity_identity"),
            "claim_entity_role": item.get("claim_entity_role"),
            "document_uid": item.get("document_uid"),
            "queue_item_sha256": item.get("queue_item_sha256"),
            "source_candidate_queue_sha256": queue_sha,
            "decision": decision,
            "reason_codes": reason_codes,
            "rationale": rationale,
            "semantic_checks": dict(checks),
            "selected_source_anchor": dict(selected) if selected is not None else None,
            "decision_provenance": dict(provenance),
            "authority_receipt": dict(receipt) if isinstance(receipt, Mapping) else None,
            "source_contract": {
                "role_review_gate_authorized": decision == "approve_relational_parent_role",
                "answer_numeric_values_exposed_to_reviewer": False,
                "separate_scope_is_parent_evidence": False,
                "eligible_for_deterministic_materialization": False,
                "may_select_value": False,
                "may_execute_formula": False,
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        decisions.append({**payload, "decision_sha256": canonical_sha256(payload)})
        counts[decision] += 1

    output_dir.mkdir(parents=True, exist_ok=False)
    decisions_path = output_dir / "entity_role_relational_chatgpt_decisions_v1.jsonl"
    _write_jsonl(decisions_path, decisions)
    result = {
        "schema_version": 1,
        "protocol": DECISION_PROTOCOL,
        "status": "reviewed_not_materialized",
        "inputs": {
            "candidate_queue": {"path": str(candidate_queue), "sha256": queue_sha},
            "review_spec": {"path": str(review_spec), "sha256": sha256_file(review_spec)},
        },
        "outputs": {"decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)}},
        "counts": {"decision_count": len(decisions), **dict(sorted(counts.items()))},
        "source_contract": {
            "human_equivalent_gate_weight": True,
            "provenance_preserved_as": "chatgpt_verified",
            "answer_numeric_values_exposed_to_reviewer": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "entity_role_relational_chatgpt_decisions_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}


def reconcile_relational_chatgpt_decisions(
    *,
    candidate_queue: Path,
    proposal_decisions: Path,
    proposal_manifest: Path,
    critic_decisions: Path,
    critic_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Authorize only exact proposer/critic agreement over one source line."""

    queue_rows = {int(row["question_id"]): row for row in _rows(candidate_queue)}
    proposal_meta = _json(proposal_manifest)
    critic_meta = _json(critic_manifest)
    proposal_expected = str(((proposal_meta.get("outputs") or {}).get("decisions") or {}).get("sha256") or "")
    critic_expected = str(((critic_meta.get("outputs") or {}).get("decisions") or {}).get("sha256") or "")
    if sha256_file(proposal_decisions) != proposal_expected or sha256_file(critic_decisions) != critic_expected:
        raise EntityRoleRelationalReviewError("proposer or critic decision artifact is stale")
    proposals = {int(row["question_id"]): row for row in _rows(proposal_decisions)}
    critics = {int(row["question_id"]): row for row in _rows(critic_decisions)}
    if set(queue_rows) != set(proposals) or set(queue_rows) != set(critics):
        raise EntityRoleRelationalReviewError("dual-review coverage mismatch")

    reconciled: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for question_id in sorted(queue_rows):
        item, proposal, critic = queue_rows[question_id], proposals[question_id], critics[question_id]
        if (
            item.get("protocol") != QUEUE_PROTOCOL
            or not _valid_hash(item, "queue_item_sha256")
            or not _valid_hash(proposal, "decision_sha256")
            or not _valid_hash(critic, "decision_sha256")
        ):
            raise EntityRoleRelationalReviewError("queue or review hash is invalid")
        proposal_provenance = proposal.get("decision_provenance") or {}
        critic_provenance = critic.get("decision_provenance") or {}
        if (
            proposal.get("queue_item_sha256") != item.get("queue_item_sha256")
            or critic.get("queue_item_sha256") != item.get("queue_item_sha256")
            or proposal_provenance.get("reviewer_role") != PROPOSER_ROLE
            or critic_provenance.get("reviewer_role") != CRITIC_ROLE
            or proposal_provenance.get("reviewer_id") == critic_provenance.get("reviewer_id")
        ):
            raise EntityRoleRelationalReviewError("dual ChatGPT reviewer independence is invalid")
        agreed = (
            proposal.get("decision") == "approve_relational_parent_role"
            and critic.get("decision") == "approve_relational_parent_role"
            and proposal.get("semantic_checks") == critic.get("semantic_checks")
            and set((proposal.get("semantic_checks") or {})) == SEMANTIC_CHECKS
            and all(value is True for value in (proposal.get("semantic_checks") or {}).values())
            and proposal.get("selected_source_anchor") == critic.get("selected_source_anchor")
        )
        decision = "approve_relational_parent_role" if agreed else "confirm_review_disagreement"
        receipt = proposal.get("authority_receipt")
        if receipt != critic.get("authority_receipt"):
            raise EntityRoleRelationalReviewError("dual reviewers have different authority receipts")
        provenance = {
            "reviewer_type": "chatgpt_verified",
            "reviewer_id": "chatgpt-entity-role-dual-review-reconciler-v1",
            "reviewer_role": RECONCILER_ROLE,
            "model_family": "deterministic",
            "review_policy": "fail_closed_evidence_bound_v1",
            "reconciliation_policy": "require_exact_entity_role_proposer_critic_consensus_v1",
            "verification_authority": "human_equivalent",
            "authority_grant": {
                "granted_by": "campaign_owner",
                "grant_scope": AUTHORITY_SCOPE,
                "grant_basis": "explicit_user_instruction",
            },
        }
        try:
            validate_gate_reviewer(
                provenance,
                grant_scope=AUTHORITY_SCOPE,
                authority_receipt=receipt,
                chatgpt_role=RECONCILER_ROLE,
            )
        except ValueError as error:
            raise EntityRoleRelationalReviewError(str(error)) from error
        payload = {
            "schema_version": 1,
            "protocol": DECISION_PROTOCOL,
            "question_id": question_id,
            "claim_entity_identity": item.get("claim_entity_identity"),
            "claim_entity_role": item.get("claim_entity_role"),
            "document_uid": item.get("document_uid"),
            "queue_item_sha256": item.get("queue_item_sha256"),
            "source_candidate_queue_sha256": sha256_file(candidate_queue),
            "decision": decision,
            "reason_codes": [] if agreed else ["CHATGPT_PROPOSER_CRITIC_DISAGREEMENT"],
            "rationale": (
                "Proposer và critic độc lập đồng thuận cùng một exact relational anchor và toàn bộ semantic checks."
                if agreed
                else "Proposer và critic không đồng thuận chính xác; giữ fail-closed."
            ),
            "semantic_checks": dict(proposal.get("semantic_checks") or {}) if agreed else {},
            "selected_source_anchor": proposal.get("selected_source_anchor") if agreed else None,
            "proposal_decision_sha256": proposal.get("decision_sha256"),
            "critic_decision_sha256": critic.get("decision_sha256"),
            "decision_provenance": provenance,
            "authority_receipt": receipt,
            "source_contract": {
                "role_review_gate_authorized": agreed,
                "answer_numeric_values_exposed_to_reviewer": False,
                "separate_scope_is_parent_evidence": False,
                "eligible_for_deterministic_materialization": agreed,
                "may_select_value": False,
                "may_execute_formula": False,
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        reconciled.append({**payload, "decision_sha256": canonical_sha256(payload)})
        counts[decision] += 1

    output_dir.mkdir(parents=True, exist_ok=False)
    output_path = output_dir / "entity_role_relational_chatgpt_decisions_v1.jsonl"
    _write_jsonl(output_path, reconciled)
    result = {
        "schema_version": 1,
        "protocol": ADJUDICATION_PROTOCOL,
        "status": "dual_chatgpt_reviews_reconciled_not_materialized",
        "inputs": {
            "candidate_queue": {"path": str(candidate_queue), "sha256": sha256_file(candidate_queue)},
            "proposal_decisions": {"path": str(proposal_decisions), "sha256": sha256_file(proposal_decisions)},
            "critic_decisions": {"path": str(critic_decisions), "sha256": sha256_file(critic_decisions)},
        },
        "outputs": {"decisions": {"path": str(output_path), "sha256": sha256_file(output_path)}},
        "counts": {"decision_count": len(reconciled), **dict(sorted(counts.items()))},
        "source_contract": {
            "human_equivalent_gate_weight": True,
            "provenance_preserved_as": "chatgpt_verified",
            "answer_numeric_values_exposed_to_reviewer": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "entity_role_relational_dual_chatgpt_adjudication_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}


def augment_semantic_decisions_with_relational_roles(
    *,
    semantic_queue: Path,
    prior_semantic_decisions: Path,
    relational_queue: Path,
    reconciled_decisions: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Attach consensus role anchors while preserving row/cell provenance."""

    semantic_queue_sha = sha256_file(semantic_queue)
    semantic_items: dict[str, Mapping[str, Any]] = {}
    for item in _rows(semantic_queue):
        if not _valid_hash(item, "queue_item_sha256"):
            raise EntityRoleRelationalReviewError("semantic queue item SHA-256 mismatch")
        semantic_items[str(item["queue_item_sha256"])] = item

    relational_queue_sha = sha256_file(relational_queue)
    relational_items: dict[int, Mapping[str, Any]] = {}
    for item in _rows(relational_queue):
        if item.get("protocol") != QUEUE_PROTOCOL or not _valid_hash(item, "queue_item_sha256"):
            raise EntityRoleRelationalReviewError("relational queue item is invalid")
        relational_items[int(item["question_id"])] = item

    reviews: dict[int, Mapping[str, Any]] = {}
    for review in _rows(reconciled_decisions):
        question_id = int(review["question_id"])
        item = relational_items.get(question_id)
        if (
            review.get("protocol") != DECISION_PROTOCOL
            or not _valid_hash(review, "decision_sha256")
            or review.get("source_candidate_queue_sha256") != relational_queue_sha
            or item is None
            or review.get("queue_item_sha256") != item.get("queue_item_sha256")
        ):
            raise EntityRoleRelationalReviewError("reconciled role decision is invalid or stale")
        if question_id in reviews:
            raise EntityRoleRelationalReviewError("duplicate reconciled role decision")
        reviews[question_id] = review
    if set(reviews) != set(relational_items):
        raise EntityRoleRelationalReviewError("reconciled role coverage is incomplete")

    output: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for prior in _rows(prior_semantic_decisions):
        item_sha = str(prior.get("queue_item_sha256") or "")
        item = semantic_items.get(item_sha)
        if (
            prior.get("protocol") != SEMANTIC_DECISION_PROTOCOL
            or item is None
            or item_sha in seen
            or prior.get("source_review_queue_sha256") != semantic_queue_sha
        ):
            raise EntityRoleRelationalReviewError("prior semantic decision is invalid, duplicate, or stale")
        seen.add(item_sha)
        decision = dict(prior)
        question_id = int(item["question_id"])
        review = reviews.get(question_id)
        if review is not None and review.get("decision") == "approve_relational_parent_role":
            anchor = review.get("selected_source_anchor")
            if not isinstance(anchor, Mapping):
                raise EntityRoleRelationalReviewError("approved relational review lacks an anchor")
            if (
                review.get("claim_entity_identity") != item.get("requested_entity")
                or review.get("claim_entity_role") != item.get("requested_entity_role")
                or review.get("document_uid") != item.get("document_uid")
                or anchor.get("assertion_type") != "issuer_subsidiary_relation"
            ):
                raise EntityRoleRelationalReviewError("relational role review does not match semantic claim")
            decision.update(
                {
                    "approved_entity_role": review["claim_entity_role"],
                    "entity_role_evidence_checked": True,
                    "entity_role_source_title_sha256": None,
                    "entity_role_source_anchor": dict(anchor),
                    "entity_role_provenance_decision_sha256": review["decision_sha256"],
                    "entity_role_provenance_decisions_file_sha256": sha256_file(reconciled_decisions),
                    "entity_role_decision_provenance": dict(review["decision_provenance"]),
                    "entity_role_authority_receipt": dict(review.get("authority_receipt") or {}),
                    "entity_role_semantic_checks": dict(review.get("semantic_checks") or {}),
                    "entity_role_inference_rule": "issuer_identity_plus_subsidiary_relation_implies_parent",
                }
            )
            counts["relational_role_augmented"] += 1
        elif review is not None:
            counts["relational_review_unresolved"] += 1
        elif decision.get("approved_entity_role"):
            counts["existing_role_preserved"] += 1
        else:
            counts["role_not_applicable"] += 1
        output.append(decision)

    output_dir.mkdir(parents=True, exist_ok=False)
    output_path = output_dir / "semantic_binding_human_decisions_with_relational_role_v1.jsonl"
    _write_jsonl(output_path, output)
    result = {
        "schema_version": 1,
        "protocol": AUGMENTATION_PROTOCOL,
        "status": "relational_role_consensus_materialized_not_released",
        "inputs": {
            "semantic_queue": {"path": str(semantic_queue), "sha256": semantic_queue_sha},
            "prior_semantic_decisions": {"path": str(prior_semantic_decisions), "sha256": sha256_file(prior_semantic_decisions)},
            "relational_queue": {"path": str(relational_queue), "sha256": relational_queue_sha},
            "reconciled_decisions": {"path": str(reconciled_decisions), "sha256": sha256_file(reconciled_decisions)},
        },
        "outputs": {"decisions": {"path": str(output_path), "sha256": sha256_file(output_path)}},
        "counts": {"decision_count": len(output), **dict(sorted(counts.items()))},
        "source_contract": {
            "original_human_cell_provenance_preserved": True,
            "chatgpt_role_provenance_preserved": True,
            "separate_scope_is_parent_evidence": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "semantic_entity_role_relational_augmentation_v1.manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**result, "manifest_path": str(manifest_path)}
