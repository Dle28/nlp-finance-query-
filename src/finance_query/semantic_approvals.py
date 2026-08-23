"""Evidence-bound semantic approval with truthful reviewer provenance.

The grounded replay may nominate exact source cells, but neither a taxonomy
alias nor a ticker embedded in artifact metadata is financial evidence.  This
module freezes review packets for exact-cell candidates.  A decision can come
from a human or from ChatGPT under an explicit gate-scoped human-equivalent
grant.  ChatGPT provenance remains ``chatgpt_verified`` and gains no value,
formula, release, training, or submission authority. Queue generation never
approves its own proposals.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from finance_query.review_authority import (
    SEMANTIC_BINDING_SCOPE,
    ReviewAuthorityError,
    validate_gate_reviewer,
)


SEMANTIC_REVIEW_PROTOCOL = "vifinqa_semantic_binding_review_queue_v1"
SEMANTIC_DECISION_PROTOCOL = "vifinqa_semantic_binding_human_decision_v1"
SEMANTIC_CHATGPT_DECISION_PROTOCOL = "vifinqa_semantic_binding_chatgpt_decision_v1"
SEMANTIC_DECISION_PROTOCOLS = frozenset(
    {SEMANTIC_DECISION_PROTOCOL, SEMANTIC_CHATGPT_DECISION_PROTOCOL}
)
ALLOWED_DECISIONS = frozenset({"approve", "reject", "uncertain"})
ALLOWED_SCOPES = frozenset({"separate", "consolidated"})
ALLOWED_ENTITY_ROLES = frozenset({"parent", "subsidiary"})
ALLOWED_ENTITY_ROLE_REVIEWER_TYPES = frozenset({"human_verified", "chatgpt_verified"})


class SemanticApprovalError(ValueError):
    """Raised when a queue or human decision loses immutable lineage."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_financial_numeric_literal(value: object) -> bool:
    normalized = re.sub(
        r"\b(?:vnd|vnđ|đồng|dong)\b",
        "",
        str(value or "").strip(),
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"[\s()+\-−.,/%₫]", "", normalized)
    return bool(normalized) and normalized.isdigit()


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise SemanticApprovalError(f"{path}:{line_number} must be a JSON object")
        values.append(value)
    return values


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise SemanticApprovalError(f"{path} must contain a JSON object")
    return value


def _write_jsonl(path: Path, values: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _require_manifest_output(manifest_path: Path, artifact_path: Path, output_name: str) -> None:
    manifest = _json(manifest_path)
    expected = ((_mapping(manifest.get("outputs")).get(output_name) or {}).get("sha256"))
    if not isinstance(expected, str) or sha256_file(artifact_path) != expected:
        raise SemanticApprovalError(f"SHA-256 mismatch for {output_name}")


def _table_index(path: Path) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in _rows(path):
        uid = _text(row.get("internal_table_uid"))
        if not uid or uid in result:
            raise SemanticApprovalError("structured tables require unique internal_table_uid")
        result[uid] = row
    return result


def _context_index(path: Path) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in _rows(path):
        uid = _text(row.get("internal_table_uid"))
        if not uid or uid in result:
            raise SemanticApprovalError("evidence context requires unique internal_table_uid")
        result[uid] = row
    return result


def _cell_candidates(table: Mapping[str, Any], *, row_index: int, value_column: int) -> list[dict[str, Any]]:
    rows = table.get("rows") or []
    if row_index < 0 or row_index >= len(rows) or not isinstance(rows[row_index], list):
        return []
    document_uid = _text(table.get("document_id"))
    table_uid = _text(table.get("internal_table_uid"))
    candidates: list[dict[str, Any]] = []
    for column_index, raw_value in enumerate(rows[row_index]):
        raw_text = str(raw_value)
        if column_index == value_column or not raw_text.strip():
            continue
        candidates.append(
            {
                "document_uid": document_uid,
                "internal_table_uid": table_uid,
                "row_index": row_index,
                "column_index": column_index,
                "raw_text": raw_text,
                "raw_text_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            }
        )
    return candidates


def _queue_item(
    *,
    question: Mapping[str, Any],
    stage: Mapping[str, Any],
    operand: Mapping[str, Any],
    table: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    question_context = _mapping(question.get("question_context"))
    entities = question_context.get("entities") or []
    requested_entity = _text(entities[0]) if isinstance(entities, list) and len(entities) == 1 else ""
    row_index = operand.get("row_index")
    column_index = operand.get("column_index")
    if not isinstance(row_index, int) or not isinstance(column_index, int):
        raise SemanticApprovalError("binding_ready operand lacks exact coordinates")
    payload = {
        "schema_version": 1,
        "protocol": SEMANTIC_REVIEW_PROTOCOL,
        "question_id": question.get("question_id"),
        "stage_id": stage.get("stage_id"),
        "role": operand.get("role"),
        "candidate_variable_id": operand.get("concept_id") or operand.get("role"),
        "requested_entity": requested_entity or None,
        "requested_entity_role": _text(question_context.get("entity_role")) or None,
        "requested_scope": _text(question_context.get("scope")) or None,
        "document_uid": table.get("document_id"),
        "internal_table_uid": table.get("internal_table_uid"),
        "value_cell": {
            "row_index": row_index,
            "column_index": column_index,
            "raw_text": str((table.get("rows") or [])[row_index][column_index]),
            "raw_text_sha256": hashlib.sha256(
                str((table.get("rows") or [])[row_index][column_index]).encode("utf-8")
            ).hexdigest(),
        },
        "row_label_candidates": _cell_candidates(table, row_index=row_index, value_column=column_index),
        "source_title": _text(_mapping(context.get("context_trace")).get("source_title")),
        "source_provenance": dict(_mapping(table.get("source_provenance"))),
        "reviewer_action": "approve | reject | uncertain",
        "human_verified": False,
        "eligible_for_authorization": False,
        "source_contract": {
            "candidate_only": True,
            "evidence_eligible": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    return {**payload, "queue_item_sha256": canonical_sha256(payload)}


def build_semantic_review_queue(
    *,
    bindings: Path,
    bindings_manifest: Path,
    structured_tables: Path,
    evidence_context: Path,
    evidence_context_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Freeze one review item for every current ``binding_ready`` operand."""

    _require_manifest_output(bindings_manifest, bindings, "bindings")
    context_manifest = _json(evidence_context_manifest)
    if _text(context_manifest.get("sidecar_sha256")) != sha256_file(evidence_context):
        raise SemanticApprovalError("SHA-256 mismatch for evidence context")
    if _text(context_manifest.get("input_structure_sha256")) != sha256_file(structured_tables):
        raise SemanticApprovalError("evidence context does not derive from structured tables")
    tables = _table_index(structured_tables)
    contexts = _context_index(evidence_context)
    queue: list[dict[str, Any]] = []
    operand_blockers: list[dict[str, Any]] = []
    question_blockers: list[dict[str, Any]] = []
    for question in _rows(bindings):
        stages = [stage for stage in question.get("stages") or [] if isinstance(stage, Mapping)]
        if not stages:
            question_blockers.append(
                {
                    "schema_version": 1,
                    "protocol": "vifinqa_question_stage_blocker_queue_v1",
                    "question_id": question.get("question_id"),
                    "route_status": question.get("route_status"),
                    "binding_packet_status": question.get("binding_packet_status"),
                    "reason_codes": ["NO_PLANNED_STAGE"],
                    "reviewer_action": "repair plan/route or confirm abstention",
                    "eligible_for_authorization": False,
                    "source_contract": {
                        "candidate_only": True,
                        "evidence_eligible": False,
                        "training_eligible": False,
                        "submission_eligible": False,
                        "promotion_allowed": False,
                    },
                }
            )
        for stage in stages:
            if not isinstance(stage, Mapping):
                continue
            for operand in stage.get("required_operands") or []:
                if not isinstance(operand, Mapping):
                    continue
                if _text(operand.get("binding_status")) != "binding_ready":
                    operand_blockers.append(
                        {
                            "schema_version": 1,
                            "protocol": "vifinqa_operand_binding_blocker_queue_v1",
                            "question_id": question.get("question_id"),
                            "stage_id": stage.get("stage_id"),
                            "role": operand.get("role"),
                            "concept_id": operand.get("concept_id"),
                            "binding_status": operand.get("binding_status"),
                            "reason_codes": list(operand.get("reason_codes") or []),
                            "route_status": question.get("route_status"),
                            "binding_packet_status": question.get("binding_packet_status"),
                            "reviewer_action": "repair exact source binding or confirm abstention",
                            "eligible_for_authorization": False,
                            "source_contract": {
                                "candidate_only": True,
                                "evidence_eligible": False,
                                "training_eligible": False,
                                "submission_eligible": False,
                                "promotion_allowed": False,
                            },
                        }
                    )
                    continue
                uid = _text(operand.get("internal_table_uid"))
                table, context = tables.get(uid), contexts.get(uid)
                if table is None or context is None:
                    raise SemanticApprovalError("binding_ready operand lacks V2/V3 source context")
                queue.append(
                    _queue_item(question=question, stage=stage, operand=operand, table=table, context=context)
                )
    identities = {
        (row.get("question_id"), row.get("stage_id"), row.get("role"))
        for row in queue
    }
    if len(identities) != len(queue):
        raise SemanticApprovalError("semantic review queue has duplicate operand identity")
    queue.sort(key=lambda row: (int(row["question_id"]), _text(row["stage_id"]), _text(row["role"])))
    operand_blockers.sort(
        key=lambda row: (int(row["question_id"]), _text(row["stage_id"]), _text(row["role"]))
    )
    question_blockers.sort(key=lambda row: int(row["question_id"]))
    output_dir.mkdir(parents=True, exist_ok=False)
    queue_path = output_dir / "semantic_binding_review_queue_v1.jsonl"
    decisions_path = output_dir / "semantic_binding_human_decisions_v1.jsonl"
    operand_blockers_path = output_dir / "operand_binding_blockers_v1.jsonl"
    question_blockers_path = output_dir / "question_stage_blockers_v1.jsonl"
    _write_jsonl(queue_path, queue)
    _write_jsonl(decisions_path, [])
    _write_jsonl(operand_blockers_path, operand_blockers)
    _write_jsonl(question_blockers_path, question_blockers)
    result = {
        "schema_version": 1,
        "protocol": SEMANTIC_REVIEW_PROTOCOL,
        "inputs": {
            "bindings": {"path": str(bindings), "sha256": sha256_file(bindings)},
            "bindings_manifest": {"path": str(bindings_manifest), "sha256": sha256_file(bindings_manifest)},
            "structured_tables": {"path": str(structured_tables), "sha256": sha256_file(structured_tables)},
            "evidence_context": {"path": str(evidence_context), "sha256": sha256_file(evidence_context)},
            "evidence_context_manifest": {
                "path": str(evidence_context_manifest),
                "sha256": sha256_file(evidence_context_manifest),
            },
        },
        "outputs": {
            "queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)},
            "blank_decisions": {"path": str(decisions_path), "sha256": sha256_file(decisions_path)},
            "operand_blockers": {
                "path": str(operand_blockers_path),
                "sha256": sha256_file(operand_blockers_path),
            },
            "question_blockers": {
                "path": str(question_blockers_path),
                "sha256": sha256_file(question_blockers_path),
            },
        },
        "counts": {
            "review_item_count": len(queue),
            "human_decision_count": 0,
            "operand_blocker_count": len(operand_blockers),
            "question_stage_blocker_count": len(question_blockers),
            "operand_blocker_status_counts": dict(
                sorted(Counter(_text(row.get("binding_status")) for row in operand_blockers).items())
            ),
        },
        "source_contract": {
            "evidence_eligible": False,
            "training_eligible": False,
            "submission_eligible": False,
            "promotion_allowed": False,
        },
    }
    manifest_path = output_dir / "semantic_binding_review_queue_v1.manifest.json"
    _write_json(manifest_path, result)
    return {**result, "manifest_path": str(manifest_path)}


def load_semantic_approvals(
    *,
    queue: Path,
    queue_manifest: Path,
    decisions: Path,
    bindings: Path,
    structured_tables: Path,
    evidence_context: Path,
) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Return authorized approvals tied to the current immutable inputs."""

    manifest = _json(queue_manifest)
    if manifest.get("protocol") != SEMANTIC_REVIEW_PROTOCOL:
        raise SemanticApprovalError("unexpected semantic review manifest protocol")
    records = {
        "bindings": bindings,
        "structured_tables": structured_tables,
        "evidence_context": evidence_context,
    }
    for name, path in records.items():
        expected = ((_mapping(manifest.get("inputs")).get(name) or {}).get("sha256"))
        if not isinstance(expected, str) or sha256_file(path) != expected:
            raise SemanticApprovalError(f"semantic review queue is stale for {name}")
    expected_queue_sha = ((_mapping(manifest.get("outputs")).get("queue") or {}).get("sha256"))
    queue_sha = sha256_file(queue)
    if queue_sha != expected_queue_sha:
        raise SemanticApprovalError("SHA-256 mismatch for semantic review queue")
    queue_index: dict[str, Mapping[str, Any]] = {}
    for item in _rows(queue):
        item_sha = _text(item.get("queue_item_sha256"))
        payload = {key: value for key, value in item.items() if key != "queue_item_sha256"}
        if not item_sha or item_sha != canonical_sha256(payload) or item_sha in queue_index:
            raise SemanticApprovalError("semantic review queue item identity is invalid")
        queue_index[item_sha] = item

    approved: dict[tuple[int, str, str], dict[str, Any]] = {}
    seen_decisions: set[str] = set()
    for decision in _rows(decisions):
        if decision.get("protocol") not in SEMANTIC_DECISION_PROTOCOLS:
            raise SemanticApprovalError("unexpected semantic decision protocol")
        item_sha = _text(decision.get("queue_item_sha256"))
        if not item_sha or item_sha not in queue_index or item_sha in seen_decisions:
            raise SemanticApprovalError("semantic decision references missing or duplicate queue item")
        seen_decisions.add(item_sha)
        if _text(decision.get("source_review_queue_sha256")) != queue_sha:
            raise SemanticApprovalError("semantic decision is bound to a different review queue")
        verdict = _text(decision.get("decision"))
        if verdict not in ALLOWED_DECISIONS:
            raise SemanticApprovalError("semantic decision verdict is invalid")
        provenance = _mapping(decision.get("decision_provenance"))
        reviewer_type = _text(provenance.get("reviewer_type"))
        expected_protocol = (
            SEMANTIC_CHATGPT_DECISION_PROTOCOL
            if reviewer_type == "chatgpt_verified"
            else SEMANTIC_DECISION_PROTOCOL
        )
        if decision.get("protocol") != expected_protocol:
            raise SemanticApprovalError("semantic decision protocol does not match reviewer provenance")
        try:
            gate_authority = validate_gate_reviewer(
                provenance,
                grant_scope=SEMANTIC_BINDING_SCOPE,
                authority_receipt=decision.get("authority_receipt"),
                chatgpt_role="authorized_ai_semantic_reviewer",
            )
        except ReviewAuthorityError as exc:
            raise SemanticApprovalError(str(exc)) from exc
        if not _text(decision.get("reviewed_at")) or not isinstance(decision.get("notes"), str):
            raise SemanticApprovalError("semantic decision requires reviewed_at and notes")
        if verdict != "approve":
            continue
        if decision.get("source_coordinates_checked") is not True:
            raise SemanticApprovalError("approved semantic decision requires checked coordinates")
        variable_id = _text(decision.get("approved_variable_id"))
        entity = _text(decision.get("approved_entity"))
        scope = _text(decision.get("approved_scope"))
        entity_role = _text(decision.get("approved_entity_role"))
        selected_label = _mapping(decision.get("selected_row_label"))
        if not variable_id or not entity or scope not in ALLOWED_SCOPES:
            raise SemanticApprovalError("approved semantic decision has incomplete semantics")
        candidates = queue_index[item_sha].get("row_label_candidates") or []
        matches = [
            candidate
            for candidate in candidates
            if isinstance(candidate, Mapping)
            and candidate.get("row_index") == selected_label.get("row_index")
            and candidate.get("column_index") == selected_label.get("column_index")
            and candidate.get("raw_text_sha256") == selected_label.get("raw_text_sha256")
        ]
        if len(matches) != 1:
            raise SemanticApprovalError("approved row label is not an immutable queue candidate")
        if reviewer_type == "chatgpt_verified" and _is_financial_numeric_literal(matches[0].get("raw_text")):
            raise SemanticApprovalError("ChatGPT semantic review cannot select a financial numeric literal")
        item = queue_index[item_sha]
        requested_entity_role = _text(item.get("requested_entity_role"))
        entity_role_evidence: dict[str, Any] | None = None
        if entity_role:
            if entity_role not in ALLOWED_ENTITY_ROLES or entity_role != requested_entity_role:
                raise SemanticApprovalError("approved entity role does not match the explicit claim")
            if decision.get("entity_role_evidence_checked") is not True:
                raise SemanticApprovalError("approved entity role requires checked source evidence")
            role_provenance = _mapping(decision.get("entity_role_decision_provenance"))
            role_reviewer_type = _text(role_provenance.get("reviewer_type"))
            if (
                role_reviewer_type not in ALLOWED_ENTITY_ROLE_REVIEWER_TYPES
                or not _text(role_provenance.get("reviewer_id"))
            ):
                raise SemanticApprovalError("approved entity role requires truthful review provenance")
            if role_reviewer_type == "chatgpt_verified":
                role_receipt = _mapping(decision.get("entity_role_authority_receipt"))
                reviewer_role = _text(role_provenance.get("reviewer_role"))
                if reviewer_role:
                    try:
                        validate_gate_reviewer(
                            role_provenance,
                            grant_scope="entity_role_review_gate_equivalence",
                            authority_receipt=role_receipt,
                            chatgpt_role=reviewer_role,
                        )
                    except ValueError as error:
                        raise SemanticApprovalError(str(error)) from error
                else:
                    # Backward-compatible validation for immutable V7 literal
                    # reviews created before explicit authority receipts existed.
                    role_authority_grant = _mapping(role_provenance.get("authority_grant"))
                    if (
                        role_provenance.get("review_policy") != "fail_closed_evidence_bound_v1"
                        or not _text(role_provenance.get("model_family"))
                        or role_authority_grant.get("granted_by") != "campaign_owner"
                        or role_authority_grant.get("grant_scope") != "entity_role_review_gate_equivalence"
                        or role_authority_grant.get("grant_basis") != "explicit_user_instruction"
                    ):
                        raise SemanticApprovalError("ChatGPT entity-role review requires an explicit authority grant")
            document_uid = _text(item.get("document_uid"))
            source_title = _text(item.get("source_title"))
            expected_title_sha = hashlib.sha256(source_title.encode("utf-8")).hexdigest()
            role_line_anchor = _mapping(decision.get("entity_role_source_anchor"))
            if role_line_anchor:
                if (
                    _text(role_line_anchor.get("document_uid")) != document_uid
                    or not isinstance(role_line_anchor.get("line_number"), int)
                    or int(role_line_anchor["line_number"]) < 1
                ):
                    raise SemanticApprovalError("approved entity role source-line coordinate is invalid")
                candidate_sha = _text(role_line_anchor.get("candidate_sha256"))
                candidate_payload = {
                    key: value for key, value in role_line_anchor.items() if key != "candidate_sha256"
                }
                if not candidate_sha or candidate_sha != canonical_sha256(candidate_payload):
                    raise SemanticApprovalError("approved entity role source-line candidate is stale")
                source_provenance = _mapping(item.get("source_provenance"))
                expected_source_sha = _text(source_provenance.get("source_sha256"))
                if _text(role_line_anchor.get("source_file_sha256")) != expected_source_sha:
                    raise SemanticApprovalError("approved entity role source document is stale")
                source_path = Path(_text(source_provenance.get("source_path")))
                if not source_path.is_file() or sha256_file(source_path) != expected_source_sha:
                    raise SemanticApprovalError("approved entity role source file is unavailable or stale")
                source_lines = source_path.read_text(encoding="utf-8").splitlines()
                line_number = int(role_line_anchor["line_number"])
                if line_number > len(source_lines):
                    raise SemanticApprovalError("approved entity role source line is out of range")
                source_text = source_lines[line_number - 1]
                source_text_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
                if (
                    source_text != role_line_anchor.get("raw_text")
                    or source_text_sha != _text(role_line_anchor.get("raw_text_sha256"))
                ):
                    raise SemanticApprovalError("approved entity role source line is stale")
                normalized_source = source_text.casefold()
                direct_parent_literal = "công ty mẹ" in normalized_source or "công ty mệ" in normalized_source
                relational_parent_proof = (
                    role_line_anchor.get("assertion_type") == "issuer_subsidiary_relation"
                    and decision.get("entity_role_inference_rule")
                    == "issuer_identity_plus_subsidiary_relation_implies_parent"
                    and set(_mapping(decision.get("entity_role_semantic_checks")))
                    == {
                        "issuer_coreference_valid",
                        "issuer_has_subsidiary_relation",
                        "parent_role_logically_proven",
                        "reporting_scope_checked_independently",
                        "same_document",
                        "sufficient_for_certificate",
                    }
                    and all(
                        value is True
                        for value in _mapping(decision.get("entity_role_semantic_checks")).values()
                    )
                    and role_provenance.get("reviewer_role")
                    == "deterministic_entity_role_dual_review_reconciler"
                    and role_provenance.get("reconciliation_policy")
                    == "require_exact_entity_role_proposer_critic_consensus_v1"
                )
                if entity_role == "parent" and not direct_parent_literal and not relational_parent_proof:
                    raise SemanticApprovalError("source line does not explicitly or relationally prove parent role")
                if not _text(decision.get("entity_role_provenance_decision_sha256")):
                    raise SemanticApprovalError("approved source-line role lacks review-decision lineage")
                entity_role_evidence = {
                    "source_text": source_text,
                    "source_text_sha256": source_text_sha,
                    "source_anchors": [{
                        "kind": "document_text_line",
                        "document_uid": document_uid,
                        "line_number": line_number,
                        "source_file_sha256": expected_source_sha,
                        "raw_text_sha256": source_text_sha,
                    }],
                    "role_review_decision_sha256": _text(
                        decision.get("entity_role_provenance_decision_sha256")
                    ),
                }
            else:
                if _text(decision.get("entity_role_source_title_sha256")) != expected_title_sha:
                    raise SemanticApprovalError("approved entity role evidence is stale")
                normalized_title = source_title.casefold()
                if (
                    entity_role == "parent"
                    and "công ty mẹ" not in normalized_title
                    and "công ty mệ" not in normalized_title
                ):
                    raise SemanticApprovalError("source title does not explicitly prove parent role")
                entity_role_evidence = {
                    "source_text": source_title,
                    "source_text_sha256": expected_title_sha,
                    "source_anchors": [{
                        "kind": "document_metadata",
                        "document_uid": document_uid,
                        "raw_text_sha256": _text(
                            _mapping(item.get("source_provenance")).get("source_sha256")
                        ),
                    }],
                }
        key = (int(item["question_id"]), _text(item.get("stage_id")), _text(item.get("role")))
        approved[key] = {
            "approval_id": canonical_sha256(decision),
            "queue_item_sha256": item_sha,
            "variable_id": variable_id,
            "entity": entity,
            "scope": scope,
            "entity_role": entity_role or None,
            "entity_role_evidence": entity_role_evidence,
            "entity_role_decision_provenance": dict(role_provenance) if entity_role else None,
            "row_label": dict(matches[0]),
            "decision_provenance": dict(provenance),
            "verification_authority": gate_authority["verification_authority"],
            "authority_receipt": dict(_mapping(decision.get("authority_receipt"))) or None,
            "reviewed_at": decision["reviewed_at"],
        }
    return approved


def load_human_semantic_approvals(
    *,
    queue: Path,
    queue_manifest: Path,
    decisions: Path,
    bindings: Path,
    structured_tables: Path,
    evidence_context: Path,
) -> dict[tuple[int, str, str], dict[str, Any]]:
    """Backward-compatible entry point for the now authority-aware loader."""

    return load_semantic_approvals(
        queue=queue,
        queue_manifest=queue_manifest,
        decisions=decisions,
        bindings=bindings,
        structured_tables=structured_tables,
        evidence_context=evidence_context,
    )


def decision_status_counts(decisions: Path) -> dict[str, int]:
    return dict(sorted(Counter(_text(row.get("decision")) for row in _rows(decisions)).items()))
