"""Materialize reviewed operation literals into the graph-review queue only.

This promotion is deliberately narrower than a route repair.  It may clear a
missing ``controlled_operation_contract`` review gate after a hash-bound
ChatGPT decision, but it cannot edit the question plan, route stages, entity,
period, scope, operands, values, or formula execution authority.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

from .operation_graphs import QUEUE_PROTOCOL, canonical_sha256, sha256_file, source_contract
from .route_context_chatgpt_reviews import PROTOCOL as DECISION_PROTOCOL


PROTOCOL = "vifinqa_route_context_chatgpt_promotion_v1"
PROMOTABLE_DECISION = "approve_all_literal_candidates"


class RouteContextPromotionError(ValueError):
    """Raised when a promotion input is stale, incomplete, or too broad."""


def _json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RouteContextPromotionError(f"{path} must contain an object")
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RouteContextPromotionError(f"{path}:{line_number} must contain an object")
        rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _valid_queue_item(row: Mapping[str, Any]) -> bool:
    payload = {
        key: value
        for key, value in row.items()
        if key not in {"schema_version", "protocol", "queue_item_sha256", "source_contract"}
    }
    return row.get("protocol") == QUEUE_PROTOCOL and row.get("queue_item_sha256") == canonical_sha256(payload)


def _valid_decision(row: Mapping[str, Any]) -> bool:
    payload = {key: value for key, value in row.items() if key != "decision_sha256"}
    return row.get("protocol") == DECISION_PROTOCOL and row.get("decision_sha256") == canonical_sha256(payload)


def _require_output(manifest: Mapping[str, Any], name: str, path: Path) -> None:
    expected = (((manifest.get("outputs") or {}).get(name) or {}).get("sha256"))
    if expected != sha256_file(path):
        raise RouteContextPromotionError(f"{name} SHA-256 mismatch")


def _validate_config(config: Mapping[str, Any]) -> set[str]:
    if config.get("promotion_policy") != "complete_literal_repairs_to_graph_review_only":
        raise RouteContextPromotionError("promotion policy is not authorized")
    allowed = {str(value) for value in config.get("allowed_fields") or []}
    if allowed != {"controlled_operation_contract"}:
        raise RouteContextPromotionError("only controlled_operation_contract may be promoted")
    authority = config.get("authority_grant") or {}
    if (
        authority.get("granted_by") != "campaign_owner"
        or authority.get("grant_basis") != "explicit_user_instruction"
        or authority.get("grant_scope") != "route_context_literal_review_gate_equivalence"
    ):
        raise RouteContextPromotionError("explicit scope-bound authority grant is required")
    forbidden = {
        "may_change_question_plan",
        "may_change_route",
        "may_select_value",
        "may_execute_formula",
        "promotion_allowed",
        "release_authorized",
    }
    if any(config.get(field) is not False for field in forbidden):
        raise RouteContextPromotionError("promotion config attempts to expand authority")
    return allowed


def build_promotion(
    *,
    operation_queue: Path,
    operation_manifest: Path,
    decisions: Path,
    decision_manifest: Path,
    promotion_config: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Create an effective graph-review queue with only reviewed literals applied."""

    queue_manifest = _json(operation_manifest)
    if queue_manifest.get("protocol") != QUEUE_PROTOCOL:
        raise RouteContextPromotionError("unexpected operation queue protocol")
    _require_output(queue_manifest, "queue", operation_queue)
    chatgpt_manifest = _json(decision_manifest)
    if chatgpt_manifest.get("protocol") != DECISION_PROTOCOL:
        raise RouteContextPromotionError("unexpected route-context decision protocol")
    _require_output(chatgpt_manifest, "decisions", decisions)
    if (chatgpt_manifest.get("source_contract") or {}).get("chatgpt_authority_grant_present") is not True:
        raise RouteContextPromotionError("ChatGPT authority receipt is missing")
    allowed_fields = _validate_config(_json(promotion_config))

    queue_rows = _rows(operation_queue)
    decision_rows = _rows(decisions)
    if any(not _valid_queue_item(row) for row in queue_rows):
        raise RouteContextPromotionError("operation queue item SHA-256 mismatch")
    if any(not _valid_decision(row) for row in decision_rows):
        raise RouteContextPromotionError("route-context decision SHA-256 mismatch")
    decision_by_question = {int(row["question_id"]): row for row in decision_rows}
    if len(decision_by_question) != len(decision_rows):
        raise RouteContextPromotionError("duplicate route-context decision question IDs")

    effective_rows: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    promoted = 0
    for row in queue_rows:
        question_id = int(row["question_id"])
        missing = [str(value) for value in row.get("missing_context") or []]
        decision = decision_by_question.get(question_id)
        can_promote = (
            row.get("queue_status") == "blocked_missing_context"
            and bool(missing)
            and set(missing).issubset(allowed_fields)
            and decision is not None
            and decision.get("decision") == PROMOTABLE_DECISION
            and set((decision.get("approved_context_repairs") or {}).keys()) == set(missing)
        )
        if not can_promote:
            effective_rows.append(dict(row))
            statuses[str(row.get("queue_status") or "unknown")] += 1
            continue

        provenance = decision.get("decision_provenance") or {}
        decision_contract = decision.get("source_contract") or {}
        if (
            provenance.get("reviewer_type") != "chatgpt_verified"
            or ((provenance.get("authority_grant") or {}).get("grant_scope"))
            != "route_context_literal_review_gate_equivalence"
            or decision_contract.get("literal_candidate_review_completed") is not True
            or any(
                decision_contract.get(field) is not False
                for field in (
                    "may_change_question_plan",
                    "may_change_route",
                    "may_select_value",
                    "may_execute_formula",
                    "promotion_allowed",
                    "release_authorized",
                )
            )
        ):
            raise RouteContextPromotionError(f"Q{question_id} decision authority is invalid")

        base_payload = {
            key: value
            for key, value in row.items()
            if key not in {"schema_version", "protocol", "queue_item_sha256", "source_contract"}
        }
        receipt_payload = {
            "question_id": question_id,
            "base_queue_item_sha256": row.get("queue_item_sha256"),
            "decision_sha256": decision.get("decision_sha256"),
            "promoted_fields": sorted(missing),
            "approved_context_repairs_sha256": canonical_sha256(decision.get("approved_context_repairs") or {}),
            "provenance": provenance,
            "authority_boundary": {
                "graph_review_gate_only": True,
                "may_change_question_plan": False,
                "may_change_route": False,
                "may_select_value": False,
                "may_execute_formula": False,
                "promotion_allowed": False,
                "release_authorized": False,
            },
        }
        receipt = {
            "schema_version": 1,
            "protocol": PROTOCOL,
            **receipt_payload,
            "promotion_receipt_sha256": canonical_sha256(receipt_payload),
        }
        receipts.append(receipt)
        base_payload.update(
            {
                "queue_status": (
                    "review_required_operation_graph"
                    if row.get("stage_nodes")
                    else "blocked_missing_route_stages"
                ),
                "missing_context": [],
                "route_context_promotion_receipt_sha256": receipt["promotion_receipt_sha256"],
            }
        )
        effective = {
            "schema_version": 1,
            "protocol": QUEUE_PROTOCOL,
            **base_payload,
            "queue_item_sha256": canonical_sha256(base_payload),
            "source_contract": source_contract(),
        }
        effective_rows.append(effective)
        statuses[str(effective["queue_status"])] += 1
        promoted += 1

    output_dir.mkdir(parents=True, exist_ok=False)
    queue_path = output_dir / "operation_graph_review_queue_v2.jsonl"
    receipts_path = output_dir / "route_context_chatgpt_promotion_receipts_v1.jsonl"
    _write_jsonl(queue_path, effective_rows)
    _write_jsonl(receipts_path, receipts)
    result = {
        "schema_version": 1,
        "protocol": QUEUE_PROTOCOL,
        "promotion_protocol": PROTOCOL,
        "status": "context_repair_materialized_for_graph_review_only",
        "inputs": {
            "operation_queue": {"path": str(operation_queue), "sha256": sha256_file(operation_queue)},
            "operation_manifest": {"path": str(operation_manifest), "sha256": sha256_file(operation_manifest)},
            "decisions": {"path": str(decisions), "sha256": sha256_file(decisions)},
            "decision_manifest": {"path": str(decision_manifest), "sha256": sha256_file(decision_manifest)},
            "promotion_config": {"path": str(promotion_config), "sha256": sha256_file(promotion_config)},
        },
        "outputs": {
            "queue": {"path": str(queue_path), "sha256": sha256_file(queue_path)},
            "promotion_receipts": {"path": str(receipts_path), "sha256": sha256_file(receipts_path)},
        },
        "counts": {
            "queue_item_count": len(effective_rows),
            "promoted_question_count": promoted,
            "queue_status_counts": dict(sorted(statuses.items())),
        },
        "source_contract": {
            "chatgpt_gate_equivalence_applied": True,
            "allowed_fields": sorted(allowed_fields),
            "eligible_for_graph_review": True,
            "may_change_question_plan": False,
            "may_change_route": False,
            "may_select_value": False,
            "may_execute_formula": False,
            "promotion_allowed": False,
            "release_authorized": False,
        },
    }
    manifest_path = output_dir / "operation_graph_review_queue_v2.manifest.json"
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {**result, "manifest_path": str(manifest_path)}
